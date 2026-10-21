import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from codex_api import CodexAPI

class CodexTests(unittest.TestCase):
    def test_structured_result_and_no_api_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            api=CodexAPI(None,'gpt-6.1-sol',Path(directory))
            def launch(command,**kwargs):
                self.assertNotIn('OPENAI_API_KEY',kwargs['env'])
                self.assertNotIn('TELEGRAM_BOT_TOKEN',kwargs['env'])
                self.assertIn('--output-schema',command)
                Path(command[command.index('-o')+1]).write_text('{"selected_ids":[1]}')
                return Mock(returncode=0,communicate=Mock(return_value=('','')))
            schema={'type':'object','properties':{'selected_ids':{'type':'array','items':{'type':'integer'}}}}
            with patch.dict(os.environ,{'OPENAI_API_KEY':'secret','TELEGRAM_BOT_TOKEN':'secret'}),patch('codex_api.subprocess.Popen',side_effect=launch):
                self.assertEqual(api.ask('select','Select',{},schema,100),{'selected_ids':[1]})
    def test_failure_no_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            api=CodexAPI(None,'gpt-6.1-sol',Path(directory))
            with patch('codex_api.subprocess.Popen',return_value=Mock(returncode=1,communicate=Mock(return_value=('','secret error')))):
                with self.assertRaisesRegex(RuntimeError,'Codex failed at select'):
                    api.ask('select','Select',{}, {'type':'string'},100)
