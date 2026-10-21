import sys
from pathlib import Path
import unittest
from datetime import datetime,timezone,timedelta
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import draft as d

NOW=datetime(2026,10,20,12,tzinfo=timezone.utc)
SOURCE={'id':'test','type':'telegram_web','enabled':True}

def row(**extra):
    value={'id':1,'source_id':'test','url':'https://t.me/test/1','text':'Компания увеличила выручку на 20% за 2026 год.', 'title':'',
           'published_at':NOW.isoformat(),'collected_at':NOW.isoformat(),'eligible_by_date':True}
    value.update(extra)
    return value

def item(**extra):
    value={'title':'Рост выручки','summary':'По данным канала, выручка выросла на 20% за 2026 год.',
           'category':'business_finance','source_ids':[1], 'evidence':[{'source_id':1,'quote':'увеличила выручку на 20% за 2026 год.'}]}
    value.update(extra)
    return value

class DraftTests(unittest.TestCase):
    def test_rechecks_dates_instead_of_stale_eligibility_flag(self):
        rows=[row(),row(published_at=(NOW-timedelta(days=2)).isoformat(),text='old'),row(published_at=None,text='unknown'),row(collected_at=(NOW-timedelta(hours=7)).isoformat(),text='stale')]
        self.assertEqual(len(d.candidates(rows,[SOURCE],NOW)),1)

    def test_disabled_and_duplicate_text_excluded(self):
        self.assertEqual(len(d.candidates([row(),row(url='https://t.me/test/2')],[SOURCE],NOW)),1)
        self.assertEqual(d.candidates([row()],[dict(SOURCE,enabled=False)],NOW),[])

    def test_unknown_source_and_fabricated_evidence_rejected(self):
        with self.assertRaises(ValueError):
            d.validate({'items':[item(source_ids=[999])]}, {1:row()})
        with self.assertRaises(ValueError):
            d.validate({'items':[item(evidence=[{'source_id':1,'quote':'Рост составил 55 процентов'}])]}, {1:row()})

    def test_valid_evidence_and_html_escaping(self):
        result=d.validate({'items':[item(title='A < B & C')]},{1:row()})
        self.assertIn('A &lt; B &amp; C',result['html'])
        self.assertIn('https://t.me/test/1',result['markdown'])

    def test_different_news_cannot_reuse_same_source(self):
        with self.assertRaises(ValueError):
            d.validate({'items':[item(),item()]},{1:row()})

    def test_quoted_environment_and_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'.env'
            path.write_text('# note\nOPENAI_API_KEY="test"\n')
            self.assertEqual(d.read_env(path)['OPENAI_API_KEY'],'test')
            path.write_text('OPENAI_API_KEY=one\nOPENAI_API_KEY=two\n')
            with self.assertRaises(ValueError):d.read_env(path)

if __name__=='__main__':unittest.main()
