"""Real saved records drive the single short morning checklist."""
import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import selltest
import selltest_today
from tests.test_cxx_selltest import NOW, CHAT, choices


class TodayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.db = str(self.root/'robot.db')
        self.products = self.root/'products'
        with mock.patch.object(selltest, 'candidates', return_value=choices()):
            selltest.start(self.db, self.products, NOW)

    def test_compact_ordered_commands_and_day_bounds(self):
        text = selltest_today.checklist(self.db, self.products, NOW)
        self.assertEqual(len(text.splitlines()), 10)
        self.assertIn('/upload 1 →', text); self.assertIn('/listed 8 <link>', text)
        self.assertLess(text.index('/listed 1'), text.index('/selltest stats 1'))
        self.assertTrue(text.splitlines()[-1].startswith('Next:'))
        self.assertEqual(selltest_today.checklist(self.db, self.products, NOW+dt.timedelta(days=14)), '')

    def test_single_morning_message_retry_and_quiet_hours(self):
        import telegram_inbox as inbox
        ctx = {'products_dir': self.products}
        with mock.patch.object(inbox, 'build_plugin_ctx', return_value=ctx), \
             mock.patch.object(inbox, 'send_text', return_value=True) as send, \
             mock.patch('tg_settings.is_quiet_hour', return_value=False), \
             mock.patch('tg_settings.get_morning_post_hour', return_value=8):
            self.assertFalse(inbox.send_morning_post_plan('unused', CHAT, self.db, NOW, products_dir=self.products))
            with mock.patch.object(inbox, 'send_text', return_value=False):
                self.assertFalse(inbox.send_morning_sell_card('unused', CHAT, self.db, NOW))
            self.assertTrue(inbox.send_morning_sell_card('unused', CHAT, self.db, NOW))
            self.assertFalse(inbox.send_morning_sell_card('unused', CHAT, self.db, NOW))
            self.assertEqual(send.call_count, 1)
            self.assertIn('/upload 1', send.call_args.args[2])
            self.assertFalse(inbox.send_morning_sell_card('unused', CHAT, self.db, NOW.replace(hour=1)))

    def test_fourteen_days_same_checklist_actual_confirmation_records(self):
        import free_sample
        import guardrails
        import post_planner
        import tg_listed
        import tg_posts
        import tg_selltest
        import telegram_inbox as inbox
        for pack in choices(8):
            aid = guardrails.request_approval('public_draft', pack['title'], '{}', db_path=self.db)
            self.assertEqual(aid, pack['id'])
            guardrails.decide(aid, True, db_path=self.db)
            self.assertTrue(tg_listed.set_listed(aid, 'https://example.gumroad.com/l/full-'+str(aid), self.db)['ok'])
            free_sample.set_link(aid, 'https://example.gumroad.com/l/sample-'+str(aid), self.db)
        self.products.mkdir(parents=True)
        planpath = post_planner.plan_path_for(self.products)
        for offset in range(14):
            now = NOW + dt.timedelta(days=offset)
            day = post_planner.owner_today(now).isoformat()
            post_planner.save_plan([{'day':offset+1, 'pack_id':'1-focus', 'date':day,'channel':'pinterest','status':'todo'}], planpath)
            ctx = {'products_dir':self.products,'db_path':self.db,'chat_id':CHAT,'now':now}
            with mock.patch.dict(os.environ, {'TELEGRAM_CHAT_ID':CHAT}), \
                 mock.patch.object(inbox, 'build_plugin_ctx', return_value=ctx):
                # Exercise the real /posted and /selltest stats command handlers.
                self.assertIn('done', tg_posts.handle_posted_n(1, ctx, plan_path=planpath)['text'])
                tg_selltest.handle('/selltest stats 1 views 2 sales 1', ctx)
            text = selltest_today.checklist(self.db, self.products, now)
            first = text.splitlines()[1]
            self.assertIn('✅ /listed 1', first); self.assertIn('✅ sample link', first)
            self.assertIn('✅ Posts', first); self.assertIn('✅ stats', first)
            self.assertNotIn('✅ stats', text.splitlines()[2])
            self.assertIn(f'day {offset+1}/14', text)
            self.assertLessEqual(len(text.splitlines()), 10)
        with mock.patch.object(selltest_today, 'checklist', return_value=text) as checklist:
            self.assertEqual(inbox.handle_command('today', [], self.db), text)
            checklist.assert_called_once_with(self.db)

    def test_stopped_and_missing_asset_slots_are_not_owner_posting_instructions(self):
        import post_planner
        import selltest_learning
        day = post_planner.owner_today(NOW).isoformat()
        plan = [{'day':1,'date':day,'pack_id':'1-focus','channel':'pinterest','status':'todo','asset':'pin.png'},
                {'day':1,'date':day,'pack_id':'2-focus','channel':'pinterest','status':'todo','asset':'pin2.png'},
                {'day':1,'date':day,'pack_id':'3-focus','channel':'action','status':'todo','notice':'Missing assets'}]
        stopped = selltest_learning.stop_pins(plan, [{'folder':'1-focus','id':1,'action':'Drop'}], NOW)
        post_planner.save_plan(stopped, post_planner.plan_path_for(self.products))
        lines = selltest_today.checklist(self.db, self.products, NOW).splitlines()
        self.assertIn('/selltest learn', lines[1])
        self.assertNotIn('/posted', lines[1]); self.assertNotIn('✅ Posts', lines[1])
        self.assertIn('/posted 2', lines[2]); self.assertNotIn('/posted 1', lines[2])
        self.assertNotIn('/posted', lines[3]); self.assertNotIn('✅ Posts', lines[3])
        self.assertIn('missing Post steps', lines[3])
