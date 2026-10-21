import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import collect as c

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
TG = {'id':'test', 'url':'https://t.me/s/test'}

def post(number, date, text='Текст &amp; цифры 25%'):
    return f'<div class="tgme_widget_message" data-post="test/{number}"><div class="tgme_widget_message_text">{text}<br>Вторая строка</div><time datetime="{date}"></time></div>'

class CollectorTests(unittest.TestCase):
    def test_pagination_and_date_filter(self):
        pages = [c.Document(post(9,'2026-10-20T10:00:00Z') + '<a data-before="9"></a>').root,
                 c.Document(post(8,'2026-10-19T14:00:00Z') + post(7,'2026-10-18T10:00:00Z')).root]
        with patch.object(c,'fetch',side_effect=pages) as fetch, patch.object(c.time,'sleep'):
            rows, report = c.collect_telegram(TG, NOW, 10)
        self.assertEqual(len(rows),2)
        self.assertIn('before=9',fetch.call_args_list[1].args[0])
        self.assertEqual(report['status'],'ok')
        self.assertIn('Текст & цифры',rows[0]['text'])

    def test_limit_and_failure_not_empty_success(self):
        doc=c.Document(post(9,'2026-10-20T10:00:00Z')+'<a data-before="9"></a>').root
        with patch.object(c,'fetch',return_value=doc), patch.object(c.time,'sleep'):
            _,report=c.collect_telegram(TG,NOW,1)
        self.assertEqual(report['status'],'partial')
        with patch.object(c,'fetch',return_value=c.Document('<html>Login</html>').root):
            _,report=c.collect_telegram(TG,NOW,1)
        self.assertEqual(report['status'],'unavailable')

    def test_article_date_body_and_navigation(self):
        html='<div data-gs-uitk-component="card-body"><p>' + ('junk '*60) + '</p></div><nav><p>'+('junk '*60)+'</p></nav><main><h1>Research</h1><p>'+('Actual evidence. '*30)+'</p></main>'
        row=c.article(c.Document(html).root,{'id':'gs'},'https://example.com/a',NOW)
        self.assertFalse(row['eligible_by_date'])
        self.assertNotIn('junk',row['text'])
        dated=html+'<script type="application/ld+json">{"@graph":[{"@type":"Article","datePublished":"2026-10-19"}]}</script>'
        row=c.article(c.Document(dated).root,{'id':'gs'},'https://example.com/a',NOW)
        self.assertTrue(row['eligible_by_date'])
        self.assertFalse(c.material({'id':'a'},'u','','body','2027-01-01',NOW,7)['eligible_by_date'])

    def test_links_are_same_host_articles(self):
        doc=c.Document('<a href="/insights/articles/a?x=1">A</a><a href="https://evil.example/insights/articles/b">B</a><a href="/insights/reports">Hub</a>').root
        self.assertEqual(c.article_links(doc,{'url':'https://www.goldmansachs.com/insights/reports'}),['https://www.goldmansachs.com/insights/articles/a'])

    def test_goldman_feed_filters_topic_and_uses_publication_date(self):
        feed = [
            {'slug':'/insights/articles/report', 'cmsPageProps':{'publishDate':'2026-10-19', 'contentType':[{'id':'gscom:content-type/report'}]}},
            {'slug':'/insights/articles/old', 'lastModifiedDate':9999999999999, 'cmsPageProps':{'publishDate':'2020-01-01', 'contentType':[{'id':'gscom:content-type/report'}]}},
            {'slug':'/insights/articles/ai', 'cmsPageProps':{'publishDate':'2026-10-19', 'primaryTopic':[{'id':'gscom:topic/intelligence-topics/artificial-intelligence'}]}}
        ]
        self.assertEqual(c.goldman_candidates(feed,{'id':'gs-reports'},NOW),[('https://www.goldmansachs.com/insights/articles/report','2026-10-19')])
        self.assertEqual(len(c.goldman_candidates(feed,{'id':'gs-ai'},NOW)),1)

    def test_database_idempotent_and_updated(self):
        import sqlite3,json
        row=c.material({'id':'a'},'u','','one','2026-10-20',NOW,1)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db'
            c.save(path,[row,row])
            row['text']='edited'
            c.save(path,[row])
            with sqlite3.connect(path) as db:
                result=db.execute('SELECT payload FROM materials').fetchall()
            self.assertEqual(len(result),1)
            self.assertEqual(json.loads(result[0][0])['text'],'edited')

if __name__=='__main__':
    unittest.main()
