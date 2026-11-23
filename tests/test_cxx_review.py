"""Every experiment day and both milestone messages use measured owner data."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import selltest
import tg_selltest
from tests.test_cxx_selltest import choices, NOW, CHAT


class ReviewTests(unittest.TestCase):
    def test_all_fourteen_days_and_conservative_keep_change_drop_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'test.db')
            with mock.patch.object(selltest,'candidates',return_value=choices()):
                selltest.start(db,now=NOW)
            for offset in range(14):
                now=NOW+dt.timedelta(days=offset)
                for aid,views,sales in [(1,120,1),(2,0,0),(3,20,0),(4,2,0)]:
                    selltest.save_stats(aid,views,sales,db,now)
                self.assertIn(f'Sell test day {offset+1}/14',selltest.report_block(db,now,temp))
                decisions=selltest.review(db,now)
                if offset not in (6,13):
                    self.assertEqual(decisions,[])
                    continue
                by_id={p['id']:p for p in decisions}
                self.assertEqual(by_id[1]['action'],'Keep')
                self.assertEqual(by_id[2]['action'],'Change');self.assertIn('Traffic',by_id[2]['reason'])
                self.assertEqual(by_id[3]['action'],'Drop' if offset==13 else 'Change')
                self.assertIn('Listing',by_id[4]['reason'])
                self.assertEqual(by_id[5]['missing_days'],offset+1)
                self.assertEqual(by_id[5]['action'],'Change')
                reply=tg_selltest.handle('/selltest list',{'db_path':db,'now':now,'chat_id':CHAT})['text']
                self.assertIn(f'day {offset+1} review',reply)
                self.assertLessEqual(len(reply.splitlines()),15)
                self.assertIn('Next:',reply)
            self.assertEqual(selltest.report_block(db,NOW+dt.timedelta(days=14)), '')
            self.assertEqual(selltest.review(db,NOW+dt.timedelta(days=14)), [])

    def test_reports_include_milestone_and_never_count_future_numbers(self):
        import daily_report
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'test.db')
            with mock.patch.object(selltest,'candidates',return_value=choices()):
                selltest.start(db,now=NOW)
            selltest.save_stats(1,250,0,db,NOW+dt.timedelta(days=13))
            day7=NOW+dt.timedelta(days=6)
            row=selltest.review(db,day7)[0]
            self.assertEqual(row['views'],0);self.assertEqual(row['entered_days'],0)
            text=daily_report.daily_text(db,day7,status_fn=lambda:{},products_dir=temp)
            self.assertIn('day 7 review',text)
            self.assertNotIn('250 Views',text)
