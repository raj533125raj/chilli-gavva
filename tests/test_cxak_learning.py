"""Measured milestone rules reach Scout and stop new Pins without rewriting history."""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import guardrails
import post_planner
import selltest
import selltest_learning as learning
from tests.test_cxx_selltest import choices, NOW, CHAT


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.products = Path(self.temp.name)/'products'; self.db = str(Path(self.temp.name)/'owner.db')
        with mock.patch.object(selltest, 'candidates', return_value=choices()):
            selltest.start(self.db, self.products, NOW)
        for pack in choices(8):
            aid = guardrails.request_approval('public_draft', pack['title'],
                  'Niche: niche_adhd_planners\nDesign theme: Calm Sage', db_path=self.db)
            self.assertEqual(aid, pack['id']); guardrails.decide(aid, True, self.db)
        for day in range(14):
            now = NOW+dt.timedelta(days=day)
            for aid, views, sales in ((1, 100, 2), (2, 0, 0), (3, 20, 0), (4, 2, 0)):
                selltest.save_stats(aid, views, sales, self.db, now)

    def learned(self, day=14):
        return learning.rules(self.db, self.products, NOW+dt.timedelta(days=day-1))

    def test_day7_and_day14_rules_missing_data_and_checkpoint_cutoffs(self):
        self.assertEqual(self.learned(6), [])
        for day in (7, 8, 13, 14, 15, 30):
            rows = {r['id']:r for r in self.learned(day)}
            checkpoint = 7 if day < 14 else 14
            self.assertEqual(rows[1]['action'], 'winner')
            self.assertEqual(rows[1]['sales'], checkpoint*2)
            self.assertEqual(rows[2]['action'], 'traffic fix')
            self.assertIn('kotha keyword hooks', rows[2]['task'])
            self.assertEqual(rows[3]['action'], 'Drop' if day >= 14 else 'listing fix')
            self.assertEqual(rows[4]['action'], 'listing fix')
            self.assertIn('Cover, first Preview', rows[4]['task'])
            self.assertEqual(rows[5]['action'], 'unknown')
            self.assertEqual(rows[5]['missing_days'], checkpoint)
            self.assertEqual(rows[1]['style'], 'Calm Sage')
        with selltest._connect(self.db) as conn:
            conn.execute('DELETE FROM sell_test_stats WHERE approval_id=3 AND day<>?', ((NOW+dt.timedelta(days=13)).date().isoformat(),))
        self.assertEqual(self.learned()[2]['action'], 'listing fix')

    def test_learning_tasks_are_idempotent_correctable_and_reachable_in_real_loader(self):
        import telegram_inbox, tg_selltest
        now = NOW+dt.timedelta(days=13)
        ctx = {'db_path':self.db, 'products_dir':self.products, 'chat_id':CHAT, 'now':now}
        with mock.patch.dict(os.environ, {'TELEGRAM_CHAT_ID':CHAT}), \
             mock.patch.object(telegram_inbox, 'build_plugin_ctx', return_value=ctx):
            for _ in range(2):
                handled, reply = telegram_inbox.call_plugins('/selltest learn', 'unused', CHAT, db_path=self.db, plugins=[tg_selltest])
                self.assertTrue(handled); self.assertIn('#3 Drop', reply['text'])
                self.assertLessEqual(len(reply['text'].splitlines()), 10)
            handled, reply = telegram_inbox.call_plugins('/selltest numbers 3 views 20 sales 1', 'unused', CHAT, db_path=self.db, plugins=[tg_selltest])
            self.assertTrue(handled); self.assertIn('save chesanu', reply['text'])
            self.assertIsNone(tg_selltest.handle('/selltest learn', dict(ctx, chat_id='other')))
        self.assertEqual(self.learned()[2]['action'], 'winner')
        future = dict(ctx, now=NOW+dt.timedelta(days=14))
        self.assertIn('day 14 learning', tg_selltest.handle('/selltest numbers', future)['text'])
        with learning.closing(guardrails.connect(self.db)) as conn:
            rows = conn.execute('SELECT payload FROM sell_test_learning').fetchall()
        self.assertEqual(len(rows), 8)
        self.assertEqual(json.loads(rows[2][0])['action'], 'winner')

    def test_scout_prefers_winning_combination_and_carries_style_with_caps_intact(self):
        import scout
        winner = {'idea':'Daily Focus Planner for Remote Workers', 'niche':'niche_adhd_planners', 'format':'planner',
                  'category':'Planners', 'audience':'remote workers', 'demand_score':20,'competition_score':20,'fit_score':20,'effort_score':20}
        unrelated = dict(winner, idea='Resume Interview Workbook for Applicants', niche='niche_resume_bundle', format='resume', audience='applicants')
        rules = self.learned()
        tuned = learning.tune(winner, rules)
        self.assertEqual(tuned['selltest_boost'], 16); self.assertEqual(tuned['style'], 'Calm Sage')
        self.assertEqual(learning.tune(unrelated, rules)['selltest_boost'], 0)
        self.assertIn('Calm Sage', learning.prompt(rules))
        with mock.patch.object(learning, 'rules', return_value=rules), \
             mock.patch.object(scout, 'get_candidate_ideas', return_value=[winner, unrelated]), \
             mock.patch.object(scout, 'get_recent_approved_or_rejected_ideas', return_value=[]), \
             mock.patch.object(scout, 'get_recent_drafts', return_value=[]), \
             mock.patch.object(scout, 'get_audience_usage_history', return_value={}), \
             mock.patch.object(scout, 'get_available_audiences', return_value=[]), \
             mock.patch.object(scout, 'get_audience_weekly_counts', return_value={}), \
             mock.patch.object(scout.guardrails, 'can_call_llm', return_value=False):
            stored = scout.run_scout(self.db)
        self.assertEqual(stored[0]['format'], 'planner')
        self.assertEqual(stored[0]['style'], 'Calm Sage')
        payload = guardrails.get_approval(stored[0]['approval_id'], self.db)['payload']
        self.assertIn('Design theme: Calm Sage', payload)
        self.assertIn('Sell test evidence:', payload)
        self.assertTrue(all(0 <= item['total_score'] <= 100 for item in stored))

    def test_stop_loser_pins_in_test_and_next_rotation_preserves_numbered_history(self):
        today = NOW+dt.timedelta(days=13)
        day = post_planner.owner_today(today).isoformat()
        history = {'pack_id':'3-focus','post_id':'done','date':day,'day':14,'channel':'pinterest','status':'done','asset':'actual-history.png'}
        pending = dict(history, post_id='pending', status='todo')
        kept = learning.stop_pins([history, pending], self.learned(), today)
        self.assertEqual(kept[0], history)
        self.assertEqual(kept[1]['channel'], 'action'); self.assertFalse(kept[1]['ready'])
        self.assertFalse(post_planner.mark_posted(kept, day, 2, today))
        import selltest_posts
        with mock.patch.object(selltest_posts, '_assets'), mock.patch.object(post_planner, 'ready_items', return_value=[]):
            plan = selltest_posts.build(self.products, selltest.active(self.db), [history], self.db, today)
        self.assertIn(history, plan)
        self.assertFalse(any(row['pack_id']=='3-focus' and row.get('status')!='done' and row['date']>=day and row['channel']=='pinterest' for row in plan))
        item = dict(pending, ready=True)
        with mock.patch.object(post_planner, 'ready_items', return_value=[item]):
            post_planner.refresh_ready_plan(self.products, now=today+dt.timedelta(days=1), db_path=self.db)
        plan = post_planner.load_plan(post_planner.plan_path_for(self.products))
        self.assertFalse(any(row.get('channel')=='pinterest' and row.get('status')!='done' for row in plan))

    def test_real_ready_pin_assets_stop_for_loser_during_and_after_experiment(self):
        from tests.test_cxy_selltest_posts import SellPlanTests, HAS_PIL
        if not HAS_PIL: self.skipTest('Pillow required')
        fixture = SellPlanTests('test_every_day_has_three_pins_and_one_carousel_for_only_selected_packs')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        initial = fixture.refresh()
        folder = fixture.packs[0]['folder']; aid = fixture.packs[0]['id']
        self.assertTrue(any(row['pack_id']==folder and row['channel']=='pinterest' for row in initial))
        for offset in range(14):
            selltest.save_stats(aid, 20, 0, fixture.db, NOW+dt.timedelta(days=offset))
        day14 = NOW+dt.timedelta(days=13)
        plan = fixture.refresh(day14)
        self.assertFalse(any(row['pack_id']==folder and row['date']>=post_planner.owner_today(day14).isoformat() and row['channel']=='pinterest' for row in plan))
        plan = fixture.refresh(NOW+dt.timedelta(days=14))
        self.assertFalse(any(row['pack_id']==folder and row.get('status')!='done' and row['date']>=post_planner.owner_today(NOW+dt.timedelta(days=14)).isoformat() and row['channel']=='pinterest' for row in plan))
