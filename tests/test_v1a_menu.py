"""Unit tests for tg_menu.py: greetings, menu, help, typo tolerance, suggestions, and integration.

Tests cover:
  1. Greetings ('hi', 'hello', 'namaste', 'hii', 'em chestunnav') -> Tenglish greeting + status + menu.
  2. /menu and 'menu' command -> Grouped menu with Drafts, Packs, Posts, Reports, Settings.
  3. /help with no args -> Full grouped menu.
  4. /help <command> / /help <word> -> Detailed command help (Telugu & English).
  5. Typo tolerance with edit distance 1-2 for command words (e.g. /pakcs, /stats, /todey).
  6. Suggestions fallback: 3 closest suggestions for unknown commands starting with slash or unknown text.
  7. Integration via telegram_inbox.process_updates verifying end-to-end delivery to owner.
  8. Zero money amounts, pure Tenglish house style.
"""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guardrails
import telegram_inbox as ti
import tg_menu

OWNER = "111222333"


def _upd(uid, text, chat=OWNER):
    return {"update_id": uid, "message": {"chat": {"id": int(chat)}, "text": text}}


class TgMenuUnitTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
            "CHILLI_TEST": "1"
        })
        self.env.start()
        self.addCleanup(self.env.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "menu_test.db")
        self.ctx = ti.build_plugin_ctx("tok", OWNER, db_path=self.db)

    def test_commands_structure(self):
        self.assertTrue(isinstance(tg_menu.COMMANDS, list))
        for cmd in tg_menu.COMMANDS:
            self.assertIn("cmd", cmd)
            self.assertIn("words", cmd)
            self.assertIn("help_en", cmd)
            self.assertIn("help_te", cmd)

    def test_greeting_hi(self):
        res = tg_menu.handle("hi", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Namaste andi", res["text"])
        self.assertIn("Status:", res["text"])
        self.assertIn("Chilli Gavva Menu", res["text"])

    def test_greeting_hello(self):
        res = tg_menu.handle("hello", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Namaste andi", res["text"])

    def test_greeting_namaste(self):
        res = tg_menu.handle("namaste", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Namaste andi", res["text"])

    def test_greeting_hii(self):
        res = tg_menu.handle("hii", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Namaste andi", res["text"])

    def test_greeting_em_chestunnav(self):
        res = tg_menu.handle("em chestunnav?", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Namaste andi", res["text"])
        self.assertIn("Status:", res["text"])

    def test_menu_slash(self):
        res = tg_menu.handle("/menu", self.ctx)
        self.assertIsNotNone(res)
        text = res["text"]
        self.assertIn("📋 *Chilli Gavva Menu*", text)
        self.assertIn("*Drafts*:", text)
        self.assertIn("*Packs*:", text)
        self.assertIn("*Posts*:", text)
        self.assertIn("*Reports*:", text)
        self.assertIn("*Settings*:", text)

    def test_menu_plain_words(self):
        for w in ("menu", "options", "commands"):
            with self.subTest(word=w):
                res = tg_menu.handle(w, self.ctx)
                self.assertIsNotNone(res)
                self.assertIn("📋 *Chilli Gavva Menu*", res["text"])

    def test_help_no_args_shows_menu(self):
        res = tg_menu.handle("/help", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("📋 *Chilli Gavva Menu*", res["text"])

    def test_help_specific_command(self):
        res = tg_menu.handle("/help list", self.ctx)
        self.assertIsNotNone(res)
        text = res["text"]
        self.assertIn("Command Help: /list", text)
        self.assertIn("Drafts", text)
        self.assertIn("Waiting drafts", text)

    def test_help_specific_command_with_slash(self):
        res = tg_menu.handle("/help /packs", self.ctx)
        self.assertIsNotNone(res)
        text = res["text"]
        self.assertIn("Command Help: /packs", text)
        self.assertIn("Packs", text)

    def test_help_telugu_word(self):
        res = tg_menu.handle("/help aamodinchu", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Command Help: /approve", res["text"])

    def test_help_unknown_word_offers_suggestions(self):
        res = tg_menu.handle("/help nonexistentxyz", self.ctx)
        self.assertIsNotNone(res)
        text = res["text"]
        self.assertIn("command dorakaledu. Ivi try cheyandi:", text)

    def test_levenshtein_distance(self):
        self.assertEqual(tg_menu.levenshtein_distance("cat", "cat"), 0)
        self.assertEqual(tg_menu.levenshtein_distance("cat", "cats"), 1)
        self.assertEqual(tg_menu.levenshtein_distance("cat", "cut"), 1)
        self.assertEqual(tg_menu.levenshtein_distance("kitten", "sitting"), 3)

    def test_typo_pakcs_suggests_packs(self):
        res = tg_menu.handle("/pakcs", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Meeru '/packs' anukunnara?", res["text"])

    def test_typo_stats_suggests_status(self):
        res = tg_menu.handle("/stats", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Meeru '/status' anukunnara?", res["text"])

    def test_typo_todey_suggests_today(self):
        res = tg_menu.handle("todey", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Meeru '/today' anukunnara?", res["text"])

    def test_unknown_slash_command_gives_3_suggestions(self):
        res = tg_menu.handle("/whatisthiscommand", self.ctx)
        self.assertIsNotNone(res)
        text = res["text"]
        self.assertIn("Meeru cheppindi ardam kaaledu:", text)
        self.assertIn("Ivi try cheyandi:", text)
        self.assertEqual(text.count("• /"), 3)

    def test_format_unknown_suggestions_helper(self):
        text = tg_menu.format_unknown_suggestions("xyz123")
        self.assertIn("Meeru cheppindi ardam kaaledu", text)
        self.assertIn("Ivi try cheyandi:", text)
        self.assertEqual(text.count("• /"), 3)

    def test_no_currency_in_tg_menu_texts(self):
        sample_texts = [
            tg_menu.format_grouped_menu(),
            tg_menu.handle("hi", self.ctx)["text"],
            tg_menu.handle("/help status", self.ctx)["text"],
            tg_menu.format_unknown_suggestions("sample")
        ]
        currency_markers = ["$", "€", "£", "₹"]
        for txt in sample_texts:
            for cur in currency_markers:
                self.assertNotIn(cur, txt)
            lower = txt.lower()
            for word in ("rs", "inr", "usd", "cents", "dollar", "rupees"):
                self.assertNotIn(f" {word} ", f" {lower} ")


class TgMenuInboxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
            "CHILLI_TEST": "1"
        })
        self.env.start()
        self.addCleanup(self.env.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "integration.db")
        self.sent = []

        def fake_send(tok, chat, text, buttons=None, keyboard=None):
            self.sent.append((chat, text))
            return True

        p1 = mock.patch.object(ti, "send_text", side_effect=fake_send)
        p1.start()
        self.addCleanup(p1.stop)

        p2 = mock.patch.object(ti, "_call", return_value={"ok": True})
        p2.start()
        self.addCleanup(p2.stop)

    def test_inbox_greeting_replies_with_menu(self):
        updates = [_upd(1, "namaste")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        chat, text = self.sent[0]
        self.assertEqual(chat, OWNER)
        self.assertIn("Namaste andi", text)
        self.assertIn("Chilli Gavva Menu", text)

    def test_inbox_slash_menu_command(self):
        updates = [_upd(2, "/menu")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("📋 *Chilli Gavva Menu*", self.sent[0][1])

    def test_inbox_plain_menu_command(self):
        updates = [_upd(3, "menu")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("📋 *Chilli Gavva Menu*", self.sent[0][1])

    def test_inbox_help_word(self):
        updates = [_upd(4, "/help today")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Command Help: /today", self.sent[0][1])

    def test_inbox_typo_command(self):
        updates = [_upd(5, "/pakcs")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Meeru '/packs' anukunnara?", self.sent[0][1])

    def test_inbox_unknown_slash_command_gives_suggestions(self):
        updates = [_upd(6, "/somethingunknown")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Ivi try cheyandi:", self.sent[0][1])

    def test_inbox_greeting_burst_deduplicated(self):
        updates = [_upd(7, "hi"), _upd(8, "hi")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 2)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("(answered 2 messages)", self.sent[0][1])

    def test_inbox_status_in_greeting_shows_pending_drafts(self):
        guardrails.request_approval("public_draft", "Test Draft #1", "payload", db_path=self.db)
        updates = [_upd(9, "hello")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            ti.process_updates("tok", OWNER, self.db)

        self.assertIn("1 drafts waiting", self.sent[0][1])

    def test_inbox_options_word_triggers_menu(self):
        updates = [_upd(10, "options")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            ti.process_updates("tok", OWNER, self.db)

        self.assertIn("📋 *Chilli Gavva Menu*", self.sent[0][1])

    def test_inbox_commands_slash_menu_triggers_menu(self):
        updates = [_upd(11, "/menu")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            ti.process_updates("tok", OWNER, self.db)

        self.assertIn("📋 *Chilli Gavva Menu*", self.sent[0][1])

    def test_inbox_help_pipeline_command(self):
        updates = [_upd(12, "/help pipeline")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            ti.process_updates("tok", OWNER, self.db)

        self.assertIn("Command Help: /pipeline", self.sent[0][1])
        self.assertIn("Reports", self.sent[0][1])

    def test_inbox_help_doctor_command(self):
        updates = [_upd(13, "/help doctor")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            ti.process_updates("tok", OWNER, self.db)

        self.assertIn("Command Help: /doctor", self.sent[0][1])

    def test_inbox_help_menu_command(self):
        updates = [_upd(14, "/help menu")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            ti.process_updates("tok", OWNER, self.db)

        self.assertIn("Command Help: /menu", self.sent[0][1])


if __name__ == "__main__":
    unittest.main()
