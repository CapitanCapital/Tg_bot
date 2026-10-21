import sys,unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from openrouter_api import OpenRouterAPI,check_schema,DEFAULT_MODEL
from draft import SELECTION

class RouterTests(unittest.TestCase):
    def test_paid_model_rejected(self):
        with self.assertRaises(ValueError):OpenRouterAPI('secret','paid/model',Path('/tmp'))
    def test_zero_price_without_paid_fallback(self):
        body=OpenRouterAPI('secret',DEFAULT_MODEL,Path('/tmp')).build_request('select','instructions',{},SELECTION,100)
        self.assertEqual(body['provider']['max_price'],{'prompt':0,'completion':0,'request':0})
        self.assertNotIn('models',body)
        self.assertTrue(body['provider']['require_parameters'])
    def test_json_schema_rejects_bool_as_id_and_extra_fields(self):
        for value in [{'selected_ids':[True]},{'selected_ids':[1],'x':1},{'selected_ids':list(range(25))}]:
            with self.assertRaises(ValueError):check_schema(value,SELECTION)
        check_schema({'selected_ids':[1,2]},SELECTION)
    def test_success_and_incomplete_response(self):
        import io
        data={'choices':[{'finish_reason':'stop','message':{'content':'{"selected_ids":[1]}'}}],'usage':{'cost':0}}
        with tempfile.TemporaryDirectory() as tmp:
            api=OpenRouterAPI('secret',DEFAULT_MODEL,Path(tmp))
            with patch('openrouter_api.urlopen',return_value=io.BytesIO(json.dumps(data).encode())):
                self.assertEqual(api.ask('select','s',{},SELECTION,100),{'selected_ids':[1]})
            data['choices'][0]['finish_reason']='length'
            with patch('openrouter_api.urlopen',return_value=io.BytesIO(json.dumps(data).encode())):
                with self.assertRaises(RuntimeError):api.ask('select','s',{},SELECTION,100)
if __name__=='__main__':unittest.main()
