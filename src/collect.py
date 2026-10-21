#!/usr/bin/env python3
"""Collect source material only. Never reads bot credentials or publishes posts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit, urlencode
from urllib.request import Request, urlopen

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def walk(self, tag=None):
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.walk(tag)

    def text(self):
        if self.tag in {'script', 'style', 'nav', 'footer', 'header', 'noscript'}:
            return ''
        return ' '.join(' '.join(c.text() if isinstance(c, Node) else c for c in self.children).split())

    def has(self, cls):
        return cls in self.attrs.get('class', '').split()


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.current = self.root
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, text):
        self.current.children.append(text)


def parse_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC)
    except ValueError:
        return None


def fetch(url, json_mode=False):
    request = Request(url, headers={'User-Agent': 'NewsDigestCollector/0.1', 'Accept': 'application/json' if json_mode else 'text/html'})
    last = None
    for attempt in range(2):
        try:
            with urlopen(request, timeout=12) as response:
                if urlsplit(response.url).hostname != urlsplit(url).hostname:
                    raise ValueError('unexpected_cross_host_redirect')
                if ('json' if json_mode else 'html') not in response.headers.get('Content-Type', '').lower():
                    raise ValueError('unsupported_content_type')
                limit = 25_000_000 if json_mode else 6_000_000
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise ValueError('response_too_large')
                text = body.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
                return json.loads(text) if json_mode else Document(text).root
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504}:
                raise
            last = exc
        except (URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt == 0:
            time.sleep(1)
    raise last


def material(source, url, title, body, published, now, days):
    date = parse_date(published)
    return {'source_id': source['id'], 'url': url, 'title': title, 'text': body,
            'published_at': date.isoformat() if date else None,
            'collected_at': now.isoformat(),
            'eligible_by_date': bool(date and now - timedelta(days=days) <= date <= now),
            'content_hash': hashlib.sha256(body.encode()).hexdigest()}


def telegram_page(doc, source, now):
    rows = []
    dated = []
    ids = []
    for node in doc.walk():
        if not node.has('tgme_widget_message'):
            continue
        post = node.attrs.get('data-post', '')
        if '/' not in post:
            continue
        channel, number = post.rsplit('/', 1)
        if channel.lower() != source['id'].lower() or not number.isdigit():
            continue
        ids.append(int(number))
        stamps = [x.attrs.get('datetime') for x in node.walk('time')]
        published = stamps[0] if stamps else None
        date = parse_date(published)
        if date:
            dated.append(date)
        texts = [x.text() for x in node.walk() if x.has('tgme_widget_message_text')]
        body = '\n'.join(texts).strip()
        if body:
            rows.append(material(source, 'https://t.me/' + post, '', body, published, now, 1))
    return rows, ids, dated


def collect_telegram(source, now, max_pages):
    rows = {}
    cursor = None
    cutoff = now - timedelta(days=1)
    for page in range(max_pages):
        url = source['url'] + ('?' + urlencode({'before': cursor}) if cursor else '')
        try:
            doc = fetch(url)
        except Exception as exc:
            return list(rows.values()), {'status': 'partial' if rows else 'unavailable', 'error': type(exc).__name__ + ': ' + str(exc), 'pages': page}
        batch, ids, dates = telegram_page(doc, source, now)
        if not ids:
            return list(rows.values()), {'status': 'partial' if rows else 'unavailable', 'error': 'no_public_messages', 'pages': page + 1}
        for row in batch:
            if row['eligible_by_date'] or row['published_at'] is None:
                rows[row['url']] = row
        older = [a for a in doc.walk('a') if a.attrs.get('data-before')]
        if dates and min(dates) <= cutoff:
            return list(rows.values()), {'status': 'ok', 'pages': page + 1, 'coverage': 'reached_24h_boundary'}
        if not older:
            return list(rows.values()), {'status': 'ok', 'pages': page + 1, 'coverage': 'end_of_public_feed'}
        next_cursor = min(ids)
        if cursor is not None and next_cursor >= cursor:
            return list(rows.values()), {'status': 'partial', 'error': 'pagination_stalled', 'pages': page + 1}
        cursor = next_cursor
        time.sleep(0.25)
    return list(rows.values()), {'status': 'partial', 'error': 'page_limit_before_24h_boundary', 'pages': max_pages}


def json_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from json_objects(child)


def content_paragraph(node):
    parent = node
    while parent is not None:
        component = parent.attrs.get('data-gs-uitk-component', '')
        marker = parent.attrs.get('class', '') + ' ' + component
        if parent.tag in {'nav', 'header', 'footer', 'aside', 'form'}:
            return False
        if component in {'card-body', 'card-text'} or 'subscribe' in marker or 'subscription' in marker:
            return False
        parent = parent.parent
    return True


def article(doc, source, url, now):
    meta = {}
    for node in doc.walk('meta'):
        key = node.attrs.get('property') or node.attrs.get('name')
        if key:
            meta[key.lower()] = node.attrs.get('content', '')
    title = meta.get('og:title', '')
    published = meta.get('article:published_time') or meta.get('datepublished') or meta.get('date')
    body = ''
    for script in doc.walk('script'):
        if script.attrs.get('type') != 'application/ld+json':
            continue
        try:
            data = json.loads(''.join(c for c in script.children if isinstance(c, str)))
        except (ValueError, TypeError):
            continue
        for obj in json_objects(data):
            kind = obj.get('@type', [])
            kind = [kind] if isinstance(kind, str) else kind
            if not set(kind or []) & {'Article', 'NewsArticle', 'Report', 'BlogPosting', 'ScholarlyArticle', 'AnalysisNewsArticle'}:
                continue
            published = published or obj.get('datePublished')
            title = title or obj.get('headline', '')
            if isinstance(obj.get('articleBody'), str):
                body = obj['articleBody']
    if not title:
        title = next((x.text() for x in doc.walk('h1')), '')
    if not published:
        published = next((x.attrs.get('datetime') for x in doc.walk('time') if x.attrs.get('datetime')), None)
    if not body:
        scope = next(doc.walk('article'), None)
        if scope is None:
            scope = next(doc.walk('main'), doc)
        paragraphs = [p.text() for p in scope.walk('p') if content_paragraph(p) and len(p.text()) >= 60]
        body = '\n\n'.join(dict.fromkeys(paragraphs))
    if len(body) < 200 or not title:
        return None
    result = material(source, url, title, body, published, now, 7)
    result['content_scope'] = 'webpage_text_only'
    result['pdf_links_not_read'] = list(dict.fromkeys(urljoin(url, a.attrs.get('href', '')) for a in doc.walk('a') if urlsplit(a.attrs.get('href', '')).path.lower().endswith('.pdf')))
    result['date_quality'] = 'published' if parse_date(published) else 'unknown_excluded'
    return result


def article_links(doc, source):
    base = source['url']
    host = urlsplit(base).hostname
    result = []
    for a in doc.walk('a'):
        u = urlsplit(urljoin(base, a.attrs.get('href', '')))
        if u.scheme != 'https' or u.hostname != host:
            continue
        path = u.path.rstrip('/')
        if host == 'www.goldmansachs.com':
            valid = '/insights/articles/' in path or '/insights/pages/' in path or path.startswith('/insights/reports/')
        elif host == 'www.mckinsey.com':
            valid = '/our-insights/' in path
        else:
            valid = False
        clean = urlunsplit((u.scheme, u.netloc, path, '', ''))
        if valid and clean != base.rstrip('/') and not path.lower().endswith(('.pdf', '.jpg', '.png')) and clean not in result:
            result.append(clean)
    return result


def goldman_candidates(feed, source, now):
    if not isinstance(feed, list):
        raise ValueError('unexpected_goldman_feed_schema')
    selected = []
    for entry in feed:
        props = entry.get('cmsPageProps', {})
        tags = [obj.get('id', '') for obj in json_objects(props)]
        if source['id'] == 'gs-reports':
            match = 'gscom:content-type/report' in tags
        else:
            match = any(tag.endswith('/artificial-intelligence') for tag in tags)
        if not match:
            continue
        date = parse_date(props.get('publishDate'))
        if date and not now - timedelta(days=7) <= date <= now:
            continue
        path = entry.get('slug', '')
        url = urljoin('https://www.goldmansachs.com', path)
        if urlsplit(url).hostname != 'www.goldmansachs.com' or not urlsplit(url).path.startswith('/insights/'):
            continue
        selected.append((url, props.get('publishDate')))
    return sorted(set(selected), key=lambda pair: pair[1] or '', reverse=True)


def collect_goldman(source, now, max_articles):
    # Public JSON feed used by the site's own article grid (not a private API).
    feed = fetch('https://www.goldmansachs.com/feeds/insights.json', json_mode=True)
    candidates = goldman_candidates(feed, source, now)
    rows, errors = [], []
    for url, published in candidates[:max_articles]:
        try:
            row = article(fetch(url), source, url, now)
            if row is None:
                errors.append({'url': url, 'error': 'article_body_not_extracted'})
                continue
            if row['published_at'] is None and parse_date(published):
                row['published_at'] = parse_date(published).isoformat()
                row['eligible_by_date'] = now - timedelta(days=7) <= parse_date(published) <= now
                row['date_quality'] = 'official_feed_publish_date'
            rows.append(row)
        except Exception as exc:
            errors.append({'url': url, 'error': type(exc).__name__ + ': ' + str(exc)})
        time.sleep(0.25)
    unknown = sum(r['published_at'] is None for r in rows)
    return rows, {'status': 'partial' if errors or unknown or len(candidates) > max_articles else 'ok',
                  'feed_entries': len(feed), 'recent_candidates': len(candidates),
                  'links_checked': min(len(candidates), max_articles), 'unknown_dates': unknown,
                  'coverage': 'official_public_feed', 'errors': errors}


def collect_website(source, now, max_articles):
    if source['id'] in {'gs-reports', 'gs-ai'}:
        return collect_goldman(source, now, max_articles)
    links = article_links(fetch(source['url']), source)
    if not links:
        return [], {'status': 'unavailable', 'error': 'no_article_links_found'}
    rows, errors = [], []
    for url in links[:max_articles]:
        try:
            row = article(fetch(url), source, url, now)
            if row is None:
                errors.append({'url': url, 'error': 'article_body_not_extracted'})
            elif row['eligible_by_date'] or row['published_at'] is None:
                rows.append(row)
        except Exception as exc:
            errors.append({'url': url, 'error': type(exc).__name__ + ': ' + str(exc)})
        time.sleep(0.25)
    return rows, {'status': 'partial' if errors or len(links) > max_articles else 'ok',
                  'links_discovered': len(links), 'links_checked': min(len(links), max_articles),
                  'coverage': 'linked_articles_only', 'errors': errors,
                  'unknown_dates': sum(r['published_at'] is None for r in rows)}


def save(db_path, rows):
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS materials (source_id TEXT, url TEXT, payload TEXT NOT NULL, PRIMARY KEY(source_id,url))')
        db.executemany('INSERT INTO materials VALUES (?,?,?) ON CONFLICT(source_id,url) DO UPDATE SET payload=excluded.payload',
                       [(r['source_id'], r['url'], json.dumps(r, ensure_ascii=False)) for r in rows])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, default=ROOT / 'config/sources.yaml')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--source', action='append', help='Restrict to a source ID; may be repeated')
    parser.add_argument('--max-pages', type=int, default=20)
    parser.add_argument('--max-articles', type=int, default=20)
    args = parser.parse_args()
    if args.max_pages < 1 or args.max_articles < 1:
        parser.error('limits must be positive')
    sources = json.loads(args.sources.read_text())['sources']
    if args.source and set(args.source) - {s['id'] for s in sources}:
        parser.error('unknown source ID')
    sources = [s for s in sources if s.get('enabled') and (not args.source or s['id'] in args.source)]
    now = datetime.now(UTC)
    def collect(source):
        try:
            if source['type'] == 'telegram_web':
                rows, report = collect_telegram(source, now, args.max_pages)
            elif source['type'] == 'website':
                rows, report = collect_website(source, now, args.max_articles)
            else:
                raise ValueError('unsupported_source_type')
        except Exception as exc:
            rows, report = [], {'status': 'unavailable', 'error': type(exc).__name__ + ': ' + str(exc)}
        report.update(source_id=source['id'], materials=len(rows), eligible=sum(r['eligible_by_date'] for r in rows))
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return rows, report
    args.data_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        collected = list(pool.map(collect, sources))
    rows = [row for group, _ in collected for row in group]
    save(args.data_dir / 'materials.sqlite3', rows)
    report = {'collected_at': now.isoformat(), 'sources': [r for _, r in collected], 'materials': len(rows), 'unique_urls': len({r['url'] for r in rows}), 'publication_performed': False}
    name = 'collection-' + now.strftime('%Y%m%dT%H%M%S%fZ') + '.json'
    (args.data_dir / name).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (args.data_dir / name.replace('.json', '.jsonl')).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    print(json.dumps({'report': name, 'materials': len(rows), 'unique_urls': report['unique_urls']}))
    return 0 if sources and all(r['status'] == 'ok' for _, r in collected) else 2


if __name__ == '__main__':
    raise SystemExit(main())
