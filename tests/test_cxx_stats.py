"""Private daily numbers are replaceable, dated in India and reflected in reports."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import selltest
import tg_selltest
import post_planner
from tests.test_cxx_selltest import choices, CHAT, NOW


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=str(self.root/'test.db')
        self.products=self.root/'products';self.products.mkdir()
        with mock.patch.object(selltest,'candidates',return_value=choices()):
            selltest.start(self.db,self.products,NOW)
        self.ctx={'db_path':self.db,'products_dir':str(self.products),'chat_id':CHAT,'now':NOW}

    def test_resubmission_replaces_and_new_india_day_gets_separate_row(self):
        tg_selltest.handle('/selltest stats 1 views 120 sales 1',self.ctx)
        tg_selltest.handle('/selltest stats 1 views 200 sales 2',self.ctx)
        self.assertEqual(len(selltest.stats(self.db)),1)
        self.assertEqual(selltest.stats(self.db)[0]['views'],200)
        night=NOW.replace(hour=19,minute=45)
        selltest.save_stats(1,20,0,self.db,night)
        self.assertEqual([r['day'] for r in selltest.stats(self.db)],['2026-11-23','2026-11-24'])

    def test_daily_report_counts_actual_posted_pins_and_conversion(self):
        import daily_report
        selltest.save_stats(1,120,1,self.db,NOW)
        rows=[{'day':1,'date':'2026-11-22','pack_id':'1-focus','channel':'pinterest','status':'done','posted_at':'2026-11-22T19:45:00','post_id':'pin1'},
              {'day':1,'date':'2026-11-23','pack_id':'1-focus','channel':'pinterest','status':'todo','post_id':'pin2'}]
        post_planner.save_plan(rows,post_planner.plan_path_for(self.products))
        text=daily_report.daily_text(self.db,NOW,status_fn=lambda:{},products_dir=self.products)
        self.assertIn('Sell test day 1/14',text)
        self.assertIn('Views 120 | Sales 1 | Conversion 0.8% | Pins posted 1',text)
        self.assertIn('numbers inka ivvaledu',text)
        self.assertFalse(list(self.products.glob('*stats*')))

    def test_bad_totals_unknown_pack_and_expired_run_cannot_write(self):
        for aid,views,sales,now in [(1,-1,0,NOW),(1,0,1,NOW),(90,10,0,NOW),(1,10,0,NOW+dt.timedelta(days=14))]:
            with self.subTest(aid=aid,views=views),self.assertRaises(ValueError):
                selltest.save_stats(aid,views,sales,self.db,now)
        self.assertEqual(selltest.stats(self.db),[])

    def test_loader_stats_is_reachable_and_stats_stays_unknown(self):
        import telegram_inbox
        with mock.patch.dict('os.environ',{'TELEGRAM_CHAT_ID':CHAT}),mock.patch.object(telegram_inbox,'send_text',return_value=True):
            handled,reply=telegram_inbox.call_plugins('/selltest stats 1 views 120 sales 1','unused',CHAT,db_path=self.db,now=NOW)
        self.assertTrue(handled);self.assertIn('totals save',reply['text'])
        self.assertEqual(selltest.stats(self.db)[0]['sales'],1)
        import sqlite3
        with sqlite3.connect(self.db) as conn:
            exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='sales'").fetchone()
            if exists:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0],0)
        self.assertIsNone(tg_selltest.handle('/stats',self.ctx))
