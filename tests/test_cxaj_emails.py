"""Real samples carry three honest owner-sent drafts and the current full-pack link."""
from pathlib import Path
import unittest
from unittest import mock
import zipfile
try:
    from PIL import Image
except ImportError:
    Image = None
import selltest_emails
from tests.test_cxx_selltest import CHAT, NOW


@unittest.skipUnless(Image is not None, 'Pillow required')
class EmailTests(unittest.TestCase):
    def setUp(self):
        from tests.test_cxai_site import LandingTests
        self.fixture = LandingTests('test_real_files_gated_copy_images_faq_and_tracked_buttons')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.products = self.fixture.products; self.db = self.fixture.db

    def test_three_short_drafts_real_instructions_and_paid_link_not_in_buyer_zip(self):
        import free_sample
        folder = free_sample.build(27, self.products, self.db)
        text = (folder/'emails.txt').read_text()
        self.assertEqual(text.count('Subject:'), 3)
        for day in (0, 3, 7): self.assertIn(f'Day {day} DRAFT', text)
        for block in text.split('Body:\n')[1:]: self.assertLess(len(block.split()), 160)
        self.assertEqual(text.count('https://example.gumroad.com/l/full-planner'), 2)
        self.assertIn('sample.pdf', text); self.assertIn('README.txt', text)
        self.assertIn('OWNER SENDS', text)
        __import__('selltest_site').public_check(text)
        with zipfile.ZipFile(folder/'product.zip') as archive:
            self.assertNotIn('emails.txt', archive.namelist())
        self.assertEqual(free_sample.build(27, self.products, self.db), folder)
        self.assertEqual((folder/'emails.txt').read_text(), text)

    def test_link_change_and_removal_refresh_cached_drafts_without_stale_links(self):
        import free_sample, tg_listed
        folder = free_sample.build(27, self.products, self.db)
        tg_listed.set_listed(27, 'https://example.gumroad.com/l/new-full', self.db, NOW)
        free_sample.build(27, self.products, self.db)
        self.assertIn('/l/new-full', (folder/'emails.txt').read_text())
        self.assertNotIn('/l/full-planner', (folder/'emails.txt').read_text())
        tg_listed.remove_listed(27, self.db)
        free_sample.build(27, self.products, self.db)
        self.assertFalse((folder/'emails.txt').exists())
        self.assertIsNone(selltest_emails.write(27, folder, self.db))

    def test_real_upload_plugin_delivers_drafts_only_to_owner(self):
        import telegram_inbox, tg_upload
        files = []; texts = []
        ctx = {'db_path':self.db, 'products_dir':self.products, 'chat_id':CHAT, 'now':NOW,
               'send_document':lambda p, caption='': files.append(Path(p).name) or True,
               'send_text':lambda text, **k: texts.append(text) or True}
        with mock.patch.dict('os.environ', {'TELEGRAM_CHAT_ID':CHAT}), \
             mock.patch.object(telegram_inbox, 'build_plugin_ctx', return_value=ctx):
            handled, reply = telegram_inbox.call_plugins('/upload 27 sample', 'unused', CHAT, db_path=self.db, plugins=[tg_upload])
            self.assertTrue(handled); self.assertTrue(reply['handled'])
            self.assertEqual(files[-1], 'emails.txt')
            files.clear()
            self.assertIsNone(tg_upload.handle('/upload 27 sample', dict(ctx, chat_id='other')))
            self.assertEqual(files, [])
