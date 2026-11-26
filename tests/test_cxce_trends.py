import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import trend

FEED=[
 {'title':'Monthly budget spreadsheet savings goals','snippet':'Planned and actual spending records'},
 {'title':'ADHD daily planner for executive function','snippet':'One task at a time'},
 {'title':'Resume ATS and cover letter templates','snippet':'Job application records'},
 {'title':'Small business inventory order tracker','snippet':'Stock counts and orders'},
 {'title':'Goodnotes digital planner navigation','snippet':'Daily plans and linked pages'},
 {'title':'New AI model beats benchmark','source':'huggingface'},
 {'title':'Agent framework launch','source':'github'},
 {'title':'LLM product announcement'},
]

class SellingTrends(unittest.TestCase):
    def test_scores_and_reasons_match_our_niches(self):
        scored=trend.score_items(FEED)
        self.assertEqual(len({row['score'] for row in scored[:5]}),5)
        self.assertEqual(len({row['why'] for row in scored[:5]}),5)
        self.assertTrue(all(row['score']>80 for row in scored[:5]))
        self.assertTrue(all(row['score']<=25 for row in scored[5:]))
        self.assertFalse(any(row['ai_news'] for row in trend.digest_items(FEED)))
    def test_at_most_one_news_even_when_no_product_feed(self):
        with patch('notify._telegram_configured',return_value=(True,'unused','owner')), patch('telegram_inbox.send_text',return_value=True) as send:
            trend.send_telegram_digest([dict(row,id=i+1) for i,row in enumerate(FEED[5:])])
        text=send.call_args.args[2]
        self.assertEqual(text.count('Score:'),1)
        self.assertTrue(text.splitlines()[-1].startswith('Next:'))
    def test_full_fake_feed_cycle_persists_scores_and_digest(self):
        with tempfile.TemporaryDirectory() as temp, patch('trend.fetch_all_raw_trends',return_value=[dict(row,url=f'https://example.com/item-{i}') for i,row in enumerate(FEED)]), \
             patch('notify._telegram_configured',return_value=(True,'unused','owner')), patch('telegram_inbox.send_text',return_value=True) as send:
            rows=trend.run_trend(db_path=str(Path(temp)/'robot.db'),llm=lambda *a,**k: self.fail('No model should inflate trend scores'))
            self.assertEqual(len(rows),8)
            text=send.call_args.args[2]
            self.assertEqual(text.count('Score:'),5)
            self.assertNotIn('benchmark',text)
            self.assertLessEqual(len(text.splitlines()),15)

    def test_substrings_do_not_invent_resume_relevance(self):
        item=trend.rule_based_score({'title':'Cats printable template'})
        self.assertLess(item['score'],80)
        self.assertNotIn('Resume Pack',item['why'])
