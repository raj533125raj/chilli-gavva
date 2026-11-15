"""CPC-2: exercise /preflight through the real Telegram plugin loader."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import telegram_inbox as ti
import tg_preflight

OWNER = str(int("9876" "5432"))


class TelegramPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cpc_tg_preflight_")
        self.addCleanup(self.tmp.cleanup)
        self.products = Path(self.tmp.name) / "products"
        self.products.mkdir()
        self.pack = self.products / "7-sample-pack"
        self.pack.mkdir()
        self.sent = []
        self.env = mock.patch.dict(os.environ, {
            "TELEGRAM_CHAT_ID": OWNER,
            "CHILLI_PRODUCTS_DIR": str(self.products),
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.send_patch = mock.patch.object(
            ti, "send_text",
            side_effect=lambda _token, _chat_id, text, buttons=None, keyboard=None:
            self.sent.append(str(text)))
        self.send_patch.start()
        self.addCleanup(self.send_patch.stop)

    def call_loader(self, text, chat_id=OWNER):
        return ti.call_plugins(text, "test-token", chat_id, plugins=[tg_preflight])

    def test_slash_pack_runs_preflight_and_sends_all_pass_footer(self):
        checks = [{"name": "Buyer files exist", "ok": True, "fix_te": "fix"}]
        with mock.patch("gumroad_preflight.check", return_value=checks) as check:
            handled, result = self.call_loader("/preflight 7")
        self.assertTrue(handled)
        self.assertEqual(result, {"handled": True})
        check.assert_called_once_with(self.pack.resolve())
        self.assertIn("✅ Buyer files unnayi", self.sent[-1])
        self.assertIn("Anni ✅ aithe: /sell 7", self.sent[-1])

    def test_slash_pack_with_bot_suffix(self):
        with mock.patch("gumroad_preflight.check", return_value=[]):
            handled, _ = self.call_loader("/preflight@samplebot 7")
        self.assertTrue(handled)
        self.assertIn("/sell 7", self.sent[-1])

    def test_preflight_word_with_pack_id(self):
        with mock.patch("gumroad_preflight.check", return_value=[]):
            handled, _ = self.call_loader("preflight 7")
        self.assertTrue(handled)
        self.assertIn("Pack #7", self.sent[-1])

    def test_ready_na_word_with_pack_id(self):
        with mock.patch("gumroad_preflight.check", return_value=[]):
            handled, _ = self.call_loader("ready na 7")
        self.assertTrue(handled)
        self.assertIn("Pack #7", self.sent[-1])

    def test_pack_first_tenglish_form(self):
        with mock.patch("gumroad_preflight.check", return_value=[]):
            handled, _ = self.call_loader("pack 7 ready na")
        self.assertTrue(handled)
        self.assertIn("Pack #7", self.sent[-1])

    def test_failed_checks_include_fix_and_do_not_show_sell_cta(self):
        checks = [
            {"name": "Product ZIP opens", "ok": False, "fix_te": "product.zip ni malli valid ZIP ga build cheyandi."},
            {"name": "LICENSE is present", "ok": True, "fix_te": ""},
        ]
        with mock.patch("gumroad_preflight.check", return_value=checks):
            handled, _ = self.call_loader("/preflight 7")
        self.assertTrue(handled)
        self.assertIn("❌ Product ZIP open avuthundi:", self.sent[-1])
        self.assertIn("✅ LICENSE present ga undi", self.sent[-1])
        self.assertNotIn("/sell 7", self.sent[-1])

    def test_missing_pack_replies_with_help(self):
        handled, _ = self.call_loader("/preflight 88")
        self.assertTrue(handled)
        self.assertIn("Pack #88 kanipinchaledu", self.sent[-1])
        self.assertIn("`/preflight`", self.sent[-1])

    def test_bare_slash_shows_help_and_five_latest_packs(self):
        for pack_id in range(1, 8):
            (self.products / f"{pack_id}-pack").mkdir()
        handled, _ = self.call_loader("/preflight")
        self.assertTrue(handled)
        text = self.sent[-1]
        self.assertIn("`/preflight N`", text)
        for pack_id in range(7, 2, -1):
            self.assertIn(f"Pack #{pack_id}", text)
        self.assertNotIn("Pack #2:", text)

    def test_each_bare_declared_word_replies(self):
        for phrase in ("ready na", "preflight"):
            with self.subTest(phrase=phrase):
                self.sent.clear()
                handled, _ = self.call_loader(phrase)
                self.assertTrue(handled)
                self.assertIn("recent packs", self.sent[-1])

    def test_no_packs_gives_clear_help(self):
        self.pack.rmdir()
        handled, _ = self.call_loader("preflight")
        self.assertTrue(handled)
        self.assertIn("Inka packs kanipinchaledu.", self.sent[-1])

    def test_upload_directory_takes_precedence_for_selected_pack(self):
        upload = self.products / "upload"
        upload.mkdir()
        selected = upload / "7-upload-pack"
        selected.mkdir()
        with mock.patch("gumroad_preflight.check", return_value=[]) as check:
            handled, _ = self.call_loader("/preflight 7")
        self.assertTrue(handled)
        check.assert_called_once_with(selected.resolve())

    def test_duplicate_ids_in_latest_list_are_deduplicated(self):
        (self.products / "upload").mkdir()
        (self.products / "upload" / "7-upload-pack").mkdir()
        handled, _ = self.call_loader("preflight")
        self.assertTrue(handled)
        self.assertEqual(self.sent[-1].count("Pack #7:"), 1)

    def test_non_owner_is_ignored(self):
        handled, result = self.call_loader("/preflight 7", chat_id=str(int("1111" "2222")))
        self.assertFalse(handled)
        self.assertIsNone(result)
        self.assertEqual(self.sent, [])

    def test_missing_owner_configuration_is_ignored(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": ""}):
            handled, result = self.call_loader("/preflight 7")
        self.assertFalse(handled)
        self.assertIsNone(result)

    def test_unrelated_text_is_not_claimed(self):
        handled, result = self.call_loader("is the new product ready?")
        self.assertFalse(handled)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
