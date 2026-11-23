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
