"""Read-only complete and broken sell-test diagnostics through the real loader."""
import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
try:
    from PIL import Image
except ImportError:
    Image = None
import guardrails
import selltest
import selltest_doctor as doctor
import selltest_site
from tests.test_cxx_selltest import CHAT, NOW, choices


class MissingTests(unittest.TestCase):
    def test_all_eight_missing_packs_fit_one_line_each_with_exact_repair_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp)/'owner.db'); products = Path(temp)/'products'
            self.assertIn('Next: /selltest', doctor.report(db, products, NOW))
            with mock.patch.object(selltest, 'candidates', return_value=choices()): selltest.start(db, products, NOW)
            text = doctor.report(db, products, NOW)
            self.assertEqual(len(text.splitlines()), 10); self.assertLess(len(text), 4000)
            self.assertTrue(text.splitlines()[-1].startswith('Next:'))
            for aid, line in enumerate(text.splitlines()[1:-1], 1):
                for command in (f'/upload {aid}', f'/upload {aid} sample', f'/listed {aid} <link>',
                    f'/selltest sample {aid} <link>', '/post today', f'/site prepare {aid}', f'/selltest stats {aid} views <views> sales <sales>'):
                    self.assertIn(command, line)
                self.assertEqual(line.count('❌'), 7)
            self.assertFalse(products.exists())
            import telegram_inbox, tg_selltest_doctor
            ctx = {'db_path':db, 'products_dir':products, 'chat_id':CHAT, 'now':NOW}
            with mock.patch.dict(os.environ, {'TELEGRAM_CHAT_ID':CHAT}), \
                 mock.patch.object(telegram_inbox, 'build_plugin_ctx', return_value=ctx):
                handled, reply = telegram_inbox.call_plugins('/doctor selltest', 'unused', CHAT, db_path=db)
                self.assertTrue(handled); self.assertEqual(reply['text'], text)
                self.assertIsNone(tg_selltest_doctor.handle('/doctor selltest', dict(ctx, chat_id='other')))
                self.assertIn('Next: /doctor selltest', tg_selltest_doctor.handle('/doctor', ctx)['text'])
                self.assertIsNone(tg_selltest_doctor.handle('/doctor errors', ctx))


@unittest.skipUnless(Image is not None, 'Pillow required')
class CompleteTests(unittest.TestCase):
    def setUp(self):
        from tests.test_cxai_site import LandingTests
        self.fixture = LandingTests('test_real_files_gated_copy_images_faq_and_tracked_buttons')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db; self.products = self.fixture.products
        import selltest_listings, post_planner
        selltest_listings.ensure_all(self.products, self.db)
        post_planner.refresh_ready_plan(self.products, now=NOW, db_path=self.db)
        self.ids = self.fixture.drafts()
        selltest.save_stats(27, 10, 1, self.db, NOW)

    def test_all_real_parts_green_pending_approved_and_no_files_written(self):
        before = {str(p):p.read_bytes() for p in self.products.rglob('*') if p.is_file()}
        text = doctor.report(self.db, self.products, NOW)
        self.assertNotIn('❌', text); self.assertEqual(text.count('✅'), 7)
        self.assertIn('Site✅ Draft', text)
        self.assertIn('/approve '+str(self.ids[0]), text)
        self.assertEqual(before, {str(p):p.read_bytes() for p in self.products.rglob('*') if p.is_file()})
        for aid in self.ids: guardrails.decide(aid, True, self.db)
        self.assertIn('Site✅ Approved', doctor.report(self.db, self.products, NOW))

    def test_missing_corrupt_and_stale_parts_have_their_own_fixes(self):
        import pin_ready, tg_listed
        pack = selltest.active(self.db)['packs'][0]
        handoff = self.products/'upload'/pack['folder']/'gumroad-upload'
        (handoff/'preview-1.png').unlink()
        text = doctor.report(self.db, self.products, NOW)
        self.assertIn('Kit❌ /upload 27', text)
        self.assertIn('Stats❌ /selltest stats 27', doctor.report(self.db, self.products, NOW+dt.timedelta(days=1)))
        tg_listed.set_listed(27, 'https://example.gumroad.com/l/new-full', self.db, NOW)
        text = doctor.report(self.db, self.products, NOW)
        self.assertIn('Site❌ /site prepare 27', text)
        pins = self.products/'marketing'/pack['folder']/'pinterest'/pin_ready.META_FILE
        pins.write_text('{}')
        self.assertIn('Pins❌ /post today', doctor.report(self.db, self.products, NOW))

    def test_rejected_page_can_be_reprepared_for_real_owner_approval(self):
        guardrails.decide(self.ids[0], False, self.db)
        self.assertIn('Site❌ /site prepare 27', doctor.report(self.db, self.products, NOW))
        replacement = selltest_site.draft(27, False, self.products, self.db)
        self.assertNotEqual(replacement, self.ids[0])
        self.assertEqual(guardrails.get_approval(replacement, self.db)['status'], 'pending')
        self.assertIn('Site✅ Draft', doctor.report(self.db, self.products, NOW))
