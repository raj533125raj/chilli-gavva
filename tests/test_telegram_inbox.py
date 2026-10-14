"""Tests for telegram_inbox.py: all offline (Telegram API is stubbed)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guardrails  # noqa: E402
import telegram_inbox as ti  # noqa: E402

OWNER = "111222333"


def _upd(uid, text, chat=OWNER):
    return {"update_id": uid, "message": {"chat": {"id": int(chat)}, "text": text}}


class InboxBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        self.sent = []
        p1 = mock.patch.object(ti, "send_text", side_effect=lambda tok, chat, text: self.sent.append((chat, text)) or True)
        p1.start()
        self.addCleanup(p1.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, title="Draft: [Budget Sheets Agent] test", review="score 80/100"):
        return guardrails.request_approval("public_draft", title, "BODY " + title, review=review, db_path=self.db)


class ParseTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(ti.parse_command("/approve 12"), ("approve", [12]))
        self.assertEqual(ti.parse_command("/approve 12 13, 14"), ("approve", [12, 13, 14]))
        self.assertEqual(ti.parse_command("reject 7"), ("reject", [7]))
        self.assertEqual(ti.parse_command("/LIST"), ("list", []))
        self.assertEqual(ti.parse_command("/view@chilligavvabot 3"), ("view", [3]))
        self.assertEqual(ti.parse_command("/start"), ("help", []))

    def test_non_commands(self):
        self.assertEqual(ti.parse_command("hello there"), (None, []))
        self.assertEqual(ti.parse_command(""), (None, []))
        self.assertEqual(ti.parse_command(None), (None, []))
        self.assertEqual(ti.parse_command("/deleteall 5"), (None, []))


class CommandTests(InboxBase):
    def test_approve_and_reject(self):
        a, b = self.add(), self.add()
        out = ti.handle_command("approve", [a], self.db)
        self.assertIn(f"Approved: #{a}", out)
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "approved")
        out = ti.handle_command("reject", [b], self.db)
        self.assertIn(f"Rejected: #{b}", out)
        self.assertEqual(guardrails.get_approval(b, self.db)["status"], "rejected")

    def test_already_decided_or_missing(self):
        a = self.add()
        ti.handle_command("approve", [a], self.db)
        out = ti.handle_command("reject", [a, 999], self.db)
        self.assertIn("Not changed", out)
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "approved")

    def test_missing_id(self):
        self.assertIn("draft number", ti.handle_command("approve", [], self.db))
        self.assertIn("draft number", ti.handle_command("view", [], self.db))

    def test_list_and_view(self):
        a = self.add()
        self.assertIn(f"#{a}", ti.handle_command("list", [], self.db))
        self.assertIn("[80/100]", ti.handle_command("list", [], self.db))
        self.assertIn("BODY", ti.handle_command("view", [a], self.db))
        self.assertIn("not found", ti.handle_command("view", [12345], self.db))

    def test_empty_list(self):
        self.assertIn("No drafts waiting", ti.handle_command("list", [], self.db))

    def test_help(self):
        self.assertIn("/approve", ti.handle_command("help", [], self.db))


class UpdateTests(InboxBase):
    def test_only_owner_can_approve(self):
        a = self.add()
        ups = [_upd(10, f"/approve {a}", chat="999"), _upd(11, "/list")]
        with mock.patch.object(ti, "fetch_updates", return_value=ups):
            n = ti.process_updates("tok", OWNER, self.db)
        self.assertEqual(n, 1)
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "pending")
        self.assertTrue(all(chat == OWNER for chat, _ in self.sent))

    def test_offset_saved_and_used(self):
        a = self.add()
        with mock.patch.object(ti, "fetch_updates", return_value=[_upd(41, f"/approve {a}")]) as f:
            ti.process_updates("tok", OWNER, self.db)
        f.assert_called_once_with("tok", None)
        self.assertEqual(ti.get_state("update_offset", db_path=self.db), "42")
        with mock.patch.object(ti, "fetch_updates", return_value=[]) as f:
            ti.process_updates("tok", OWNER, self.db)
        f.assert_called_once_with("tok", "42")

    def test_non_command_text_ignored(self):
        with mock.patch.object(ti, "fetch_updates", return_value=[_upd(5, "thanks!")]):
            self.assertEqual(ti.process_updates("tok", OWNER, self.db), 0)
        self.assertEqual(self.sent, [])


class AlertTests(InboxBase):
    def test_first_run_sends_one_summary(self):
        self.add(); self.add(); self.add()
        n = ti.alert_new_drafts("tok", OWNER, self.db)
        self.assertEqual(n, 3)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(ti.alert_new_drafts("tok", OWNER, self.db), 0)
        self.assertEqual(len(self.sent), 1)

    def test_only_new_drafts_announced_once(self):
        self.add()
        ti.alert_new_drafts("tok", OWNER, self.db)
        b = self.add()
        self.assertEqual(ti.alert_new_drafts("tok", OWNER, self.db), 1)
        self.assertIn(f"#{b}", self.sent[-1][1])
        self.assertEqual(ti.alert_new_drafts("tok", OWNER, self.db), 0)

    def test_no_drafts_no_message(self):
        self.assertEqual(ti.alert_new_drafts("tok", OWNER, self.db), 0)
        self.assertEqual(self.sent, [])


class SendTests(unittest.TestCase):
    def test_long_text_is_cut_and_plain(self):
        captured = {}

        def fake_call(token, method, params):
            captured.update(params)
            return {"ok": True}
        with mock.patch.object(ti, "_call", side_effect=fake_call):
            self.assertTrue(ti.send_text("tok", OWNER, "x" * 10000))
        self.assertLessEqual(len(captured["text"]), 4096)
        self.assertNotIn("parse_mode", captured)

    def test_network_error_is_safe(self):
        with mock.patch.object(ti, "_call", side_effect=OSError("down")):
            self.assertFalse(ti.send_text("tok", OWNER, "hi"))
            self.assertEqual(ti.fetch_updates("tok", None), [])


class RunTests(unittest.TestCase):
    def test_not_configured_skips(self):
        with mock.patch.object(ti.notify, "_telegram_configured", return_value=(False, "", "")):
            self.assertEqual(ti.run()["configured"], False)


if __name__ == "__main__":
    unittest.main()
