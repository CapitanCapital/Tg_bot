#!/usr/bin/env python3
"""Daily preparation and at-most-once Telegram delivery. No retries after ambiguous sends."""
import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from collect import ROOT
from draft import read_env, validate

MOSCOW = ZoneInfo('Europe/Moscow')
TARGET = '-1004452185250'
DATA = ROOT/'data'
ERROR_TEXT = 'ошибка в проверке и сборе поста'

def now_moscow():
    return datetime.now(MOSCOW)

def delivery_db(data):
    db = sqlite3.connect(data/'deliveries.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS deliveries (day TEXT PRIMARY KEY, state TEXT, message_id INTEGER)')
    db.commit()
    return db

def prepare(data):
    start = datetime.now(timezone.utc)
    outbox = data/('outbox-'+now_moscow().date().isoformat()+'.json')
    outbox.unlink(missing_ok=True)
    collected = subprocess.run([sys.executable,str(ROOT/'src/collect.py'),'--data-dir',str(data)],timeout=600)
    if collected.returncode not in (0,2):
        raise RuntimeError('Collection failed')
    drafted = subprocess.run([sys.executable,str(ROOT/'src/draft.py'),'--provider','codex','--data-dir',str(data)],timeout=1000)
    if drafted.returncode:
        raise RuntimeError('No approved draft; publication skipped')
    directories = sorted(data.glob('draft-*'))
    directory = directories[-1]
    result = json.loads((directory/'result.json').read_text())
    if result['status'] != 'reviewed_draft' or datetime.fromisoformat(result['created_at']) < start:
        raise RuntimeError('Draft is stale or unapproved')
    draft = json.loads((directory/'final.json').read_text())
    if not draft['items']:
        outbox.write_text(json.dumps({'day':now_moscow().date().isoformat(),'prepared_at':datetime.now(timezone.utc).isoformat(),'skip':True}))
        print('No qualifying news; skipping')
        return
    candidates = {r['id']:r for r in json.loads((directory/'candidates.json').read_text())}
    rendered = validate(draft,candidates)
    history = [{'title':i['title'],'summary':i['summary'],'urls':[candidates[n]['url'] for n in i['source_ids']], 'date':now_moscow().date().isoformat()} for i in draft['items']]
    payload = {'day':now_moscow().date().isoformat(),'prepared_at':datetime.now(timezone.utc).isoformat(),'text':rendered['html'],'history':history}
    temporary = outbox.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload,ensure_ascii=False))
    temporary.replace(outbox)
    print('Approved daily outbox ready', flush=True)

def send(payload, token):
    req=Request('https://api.telegram.org/bot'+token+'/sendMessage',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=40) as response:
        answer=json.load(response)
    if not answer.get('ok'):
        raise RuntimeError('Telegram rejected publication')
    result=answer['result']
    if str(result['chat']['id']) != TARGET or not isinstance(result['message_id'],int):
        raise RuntimeError('Unexpected Telegram response')
    return result['message_id']

def publication_time():
    return json.loads((ROOT/'deploy/server.json').read_text())['publish_at']

def publish(data, now=None, sender=send):
    now=now or now_moscow()
    now=now.astimezone(MOSCOW)
    at = publication_time()
    hour, minute = map(int, at.split(':'))
    planned = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if not timedelta(0) <= now-planned < timedelta(minutes=15):
        raise RuntimeError('Outside configured Moscow delivery window')
    day=now.date().isoformat()
    delivery_key = day if at == '09:00' else day+'@'+at
    try:
        box=json.loads((data/('outbox-'+day+'.json')).read_text())
        age=now-datetime.fromisoformat(box['prepared_at'])
        if box['day'] != day or not timedelta(0) <= age <= timedelta(hours=2):
            raise ValueError('Outbox is stale')
        if box.get('skip') is True:
            print('No qualifying news; skipping')
            return
        if not isinstance(box['text'],str) or not box['text'].strip() or not isinstance(box['history'],list):
            raise ValueError('Invalid outbox')
    except (OSError, ValueError, KeyError, TypeError):
        # Missing output also covers crashes/timeouts before preparation could record failure.
        box={'text':ERROR_TEXT,'history':[]}
    env=read_env(ROOT/'.env')
    if env.get('TELEGRAM_CHAT_ID') != TARGET:
        raise RuntimeError('Target differs from authorized test channel')
    db=delivery_db(data)
    try:
        db.execute('INSERT INTO deliveries(day,state) VALUES (?,?)',(delivery_key,'sending'))
        db.commit()
    except sqlite3.IntegrityError:
        print('Delivery already attempted; no resend')
        return
    try:
        mid=sender({'chat_id':TARGET,'text':box['text'],'parse_mode':'HTML','link_preview_options':{'is_disabled':True}},env['TELEGRAM_BOT_TOKEN'])
    except Exception:
        db.execute('UPDATE deliveries SET state=? WHERE day=?',('uncertain',delivery_key))
        db.commit()
        raise RuntimeError('Send failed or outcome uncertain; automatic retry disabled') from None
    db.execute('UPDATE deliveries SET state=?, message_id=? WHERE day=?',('sent',mid,delivery_key))
    db.commit()
    history_path=data/'published.json'
    history=json.loads(history_path.read_text()) if history_path.exists() else []
    history.extend(box['history'])
    tmp=history_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(history[-2000:],ensure_ascii=False))
    tmp.replace(history_path)
    print('Published successfully; message_id='+str(mid),flush=True)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','publish'])
    args=parser.parse_args()
    DATA.mkdir(mode=0o700,exist_ok=True)
    with (DATA/'schedule.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            (prepare if args.action=='prepare' else publish)(DATA)
        except Exception as exc:
            print('Schedule failed: '+type(exc).__name__+(' '+str(exc) if isinstance(exc,RuntimeError) else ''),flush=True)
            sys.exit(1)
