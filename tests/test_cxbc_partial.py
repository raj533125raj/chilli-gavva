"""Partial starts, later READY enrollment and independent pack milestones."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import selltest
import selltest_learning
import selltest_posts
import tg_selltest
from tests.test_cxx_selltest import choices, NOW, CHAT


class PartialTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.db = str(self.root / 'robot.db')
        self.packs = choices(3)
        self.enterContext(patch.object(selltest, 'candidates', side_effect=lambda *a: self.packs))

    def test_one_to_eight_can_start_and_three_ready_use_real_loader(self):
        import telegram_inbox
        ctx = {'db_path':self.db,'products_dir':str(self.root/'products'),'now':NOW,'chat_id':CHAT}
        with patch.object(telegram_inbox,'build_plugin_ctx',return_value=ctx), \
                patch.object(telegram_inbox,'send_text',return_value=True):
            handled, reply = telegram_inbox.call_plugins('/selltest start','unused',CHAT,db_path=self.db,now=NOW)
        self.assertTrue(handled)
        self.assertIn('Ready 3/8 — kotha packs ready ayyaka auto ga add avutayi',reply['text'])
        self.assertEqual(len(selltest.active(self.db)['packs']),3)
        for size in range(1,9):
            self.packs = choices(size)
            db = str(self.root / f'robot-{size}.db')
            self.assertEqual(len(selltest.start(db,now=NOW)['packs']),size)

    def test_new_pack_day_seven_and_fourteen_use_its_own_clock(self):
        selltest.start(self.db,now=NOW)
        self.packs = choices(12)
        joined = NOW+dt.timedelta(days=5)
        run = selltest.sync_ready(self.db,now=joined)
        self.assertEqual(len(run['packs']),8)
        self.assertEqual(selltest.day_number(run,joined,run['packs'][3]),1)
        for _ in range(2): self.assertEqual(len(selltest.sync_ready(self.db,now=joined)['packs']),8)
        # Initial packs reach day 7 while new packs are only on day 2.
        initial7 = NOW+dt.timedelta(days=6)
        self.assertEqual({p['id'] for p in selltest.review(self.db,initial7)},{1,2,3})
        self.assertEqual({p['id'] for p in selltest_learning.rules(self.db,self.root,initial7)},{1,2,3})
        new7 = joined+dt.timedelta(days=6)
        self.assertEqual({p['id'] for p in selltest.review(self.db,new7)},set(range(4,9)))
        new14 = joined+dt.timedelta(days=13)
        self.assertTrue(selltest.running(run,new14))
        selltest.save_stats(4,200,0,self.db,new14)
        self.assertEqual({p['id'] for p in selltest.review(self.db,new14)},set(range(4,9)))
        self.assertEqual(next(p for p in selltest_learning.rules(self.db,self.root,new14) if p['id']==4)['checkpoint'],14)
        with self.assertRaises(ValueError): selltest.save_stats(1,2,0,self.db,new14)
        with self.assertRaises(ValueError): selltest.save_stats(4,2,0,self.db,new14+dt.timedelta(days=1))

    def test_later_pack_gets_posts_only_during_its_own_fourteen_days(self):
        selltest.start(self.db,now=NOW)
        self.packs = choices(4)
        joined = NOW+dt.timedelta(days=5)
        run = selltest.sync_ready(self.db,now=joined)
        with patch.object(selltest_posts,'_assets'), patch('post_planner.ready_items',return_value=[]):
            plan = selltest_posts.build(self.root,run,[],self.db,joined)
        new = [p for p in plan if p['pack_id']=='4-focus']
        self.assertTrue(new)
        self.assertTrue(all('2026-11-28'<=p['date']<='2026-12-11' for p in new))
        self.assertEqual(max(p['day'] for p in new),14)

    def test_legacy_membership_keeps_original_start_without_migration_write(self):
        import sqlite3
        with selltest._connect(self.db) as conn:
            conn.execute('INSERT INTO sell_test VALUES(1,?)',('2026-11-23',))
            conn.execute('INSERT INTO sell_test_packs VALUES(1,56,?,?,?,100)',('Resume','56-resume','resume'))
            conn.execute('DROP TABLE sell_test_pack_starts')
        self.assertEqual(selltest.active(self.db)['packs'][0]['start_day'],'2026-11-23')
        with sqlite3.connect(self.db) as conn:
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='sell_test_pack_starts'").fetchone())

    def test_mixed_pack_report_never_labels_a_later_pack_as_day_nineteen_of_fourteen(self):
        import selltest_today
        selltest.start(self.db,now=NOW)
        self.packs = choices(4)
        joined = NOW+dt.timedelta(days=5)
        selltest.sync_ready(self.db,now=joined)
        last = joined+dt.timedelta(days=13)
        for text in (selltest.report_block(self.db,last,self.root),selltest_today.checklist(self.db,self.root,last)):
            self.assertNotIn('19/14',text)
            self.assertIn('day 14/14',text)
        import promise_check
        stats = next(text for key,text in promise_check.check(self.db,self.root,last) if key=='stats')
        self.assertIn('1 Packs',stats)
        self.assertIn('/selltest stats 4',stats)
        self.packs = choices(5)
        swap = selltest.swap(4,5,self.db,now=last)
        pack = next(p for p in swap['packs'] if p['id']==5)
        self.assertEqual(selltest.day_number(swap,last,pack),1)
