import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import schedule

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.data=Path(self.temp.name)
        self.now=datetime(2026,10,20,9,0,tzinfo=schedule.MOSCOW)
        self.box={'day':'2026-10-20','prepared_at':'2026-10-20T08:40:00+03:00','text':'Test','history':[]}
        self.save()
        self.env=patch.object(schedule,'read_env',return_value={'TELEGRAM_CHAT_ID':schedule.TARGET,'TELEGRAM_BOT_TOKEN':'dummy'})
        self.env.start()
        self.clock_config=patch.object(schedule,'publication_time',return_value='09:00')
        self.clock_config.start()
    def tearDown(self):
        self.env.stop()
        self.clock_config.stop()
        self.temp.cleanup()
    def save(self):
        (self.data/'outbox-2026-10-20.json').write_text(json.dumps(self.box))
    def test_at_most_once(self):
        sender=Mock(return_value=123)
        schedule.publish(self.data,self.now,sender)
        schedule.publish(self.data,self.now,sender)
        self.assertEqual(sender.call_count,1)
    def test_uncertain_not_retried(self):
        sender=Mock(side_effect=TimeoutError())
        with self.assertRaises(RuntimeError): schedule.publish(self.data,self.now,sender)
        schedule.publish(self.data,self.now,sender)
        self.assertEqual(sender.call_count,1)
    def test_outside_window(self):
        sender=Mock()
        with self.assertRaises(RuntimeError): schedule.publish(self.data,self.now.replace(hour=10),sender)
        sender.assert_not_called()
    def test_stale(self):
        self.box['prepared_at']='2026-10-19T08:40:00+03:00'
        self.save()
        sender=Mock(return_value=124)
        schedule.publish(self.data,self.now,sender)
        self.assertEqual(sender.call_args.args[0]['text'],schedule.ERROR_TEXT)
    def test_wrong_target(self):
        with patch.object(schedule,'read_env',return_value={'TELEGRAM_CHAT_ID':'other'}):
            with self.assertRaises(RuntimeError): schedule.publish(self.data,self.now,Mock())

    def test_missing_outbox_error_once(self):
        (self.data/'outbox-2026-10-20.json').unlink()
        sender=Mock(return_value=125)
        schedule.publish(self.data,self.now,sender)
        schedule.publish(self.data,self.now,sender)
        self.assertEqual(sender.call_count,1)
        self.assertEqual(sender.call_args.args[0]['text'],'ошибка в проверке и сборе поста')
    def test_corrupt_outbox(self):
        (self.data/'outbox-2026-10-20.json').write_text('{')
        sender=Mock(return_value=126)
        schedule.publish(self.data,self.now,sender)
        self.assertEqual(sender.call_args.args[0]['text'],schedule.ERROR_TEXT)
    def test_empty_issue_not_error(self):
        self.box['skip']=True
        self.save()
        sender=Mock()
        schedule.publish(self.data,self.now,sender)
        sender.assert_not_called()
    def test_valid_issue_unchanged(self):
        sender=Mock(return_value=127)
        schedule.publish(self.data,self.now,sender)
        self.assertEqual(sender.call_args.args[0]['text'],'Test')

    def test_evening_after_morning_attempt(self):
        sender=Mock(return_value=128)
        schedule.publish(self.data,self.now,sender)
        self.box['prepared_at']='2026-10-20T23:25:00+03:00'
        self.save()
        with patch.object(schedule,'publication_time',return_value='23:30'):
            now=self.now.replace(hour=23,minute=30)
            schedule.publish(self.data,now,sender)
            schedule.publish(self.data,now,sender)
        self.assertEqual(sender.call_count,2)
    def test_evening_window_rejects_morning(self):
        with patch.object(schedule,'publication_time',return_value='23:30'):
            with self.assertRaises(RuntimeError):
                schedule.publish(self.data,self.now,Mock())
