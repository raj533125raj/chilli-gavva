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
        self.buttons = []

        def fake_send(tok, chat, text, buttons=None):
            self.sent.append((chat, text))
            self.buttons.append(buttons)
            return True
        p1 = mock.patch.object(ti, "send_text", side_effect=fake_send)
        p1.start()
        self.addCleanup(p1.stop)
        transport = mock.patch.object(ti, "_call", return_value={"ok": True})
        transport.start()
        self.addCleanup(transport.stop)

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

    def test_missing_id_shows_real_waiting_drafts(self):
        self.assertEqual(ti.handle_command("approve", [], self.db), "No drafts are waiting right now.")
        a, b, c = self.add(), self.add(), self.add()
        out = ti.handle_command("approve", [], self.db)
        self.assertEqual(out, f"Waiting: #{a}, #{b}, #{c}. Send /approve {a}")
        self.assertIn(f"Send /view {a}", ti.handle_command("view", [], self.db))
        self.assertIn(f"Send /reject {a}", ti.handle_command("reject", [], self.db))
        for reply in (out, ti.handle_command("view", [], self.db)):
            self.assertNotIn("12", reply)                     # no made-up example number
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "pending")   # nothing decided

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
            self.assertEqual(ti.process_updates("tok", OWNER, self.db), 1)
        self.assertTrue(len(self.sent) >= 1)


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


def _cb(uid, data, chat=OWNER, sender=OWNER):
    return {"update_id": uid, "callback_query": {"id": f"q{uid}", "data": data, "from": {"id": int(sender)},
                                                 "message": {"chat": {"id": int(chat)}}}}


class ButtonTests(InboxBase):
    def run_updates(self, updates):
        calls = []
        with mock.patch.object(ti, "fetch_updates", return_value=updates), \
                mock.patch.object(ti, "_call", side_effect=lambda *a: calls.append(a[1]) or {"ok": True}):
            n = ti.process_updates("tok", OWNER, self.db)
        return n, calls

    def test_new_draft_alert_has_three_buttons(self):
        self.add()
        ti.alert_new_drafts("tok", OWNER, self.db)
        b = self.add()
        ti.alert_new_drafts("tok", OWNER, self.db)
        self.assertEqual(self.buttons[-1], [[("✅ Approve", f"a:{b}"), ("❌ Reject", f"r:{b}"),
                                             ("\U0001f441 View", f"v:{b}")]])

    def test_approve_button(self):
        a = self.add()
        n, calls = self.run_updates([_cb(1, f"a:{a}")])
        self.assertEqual(n, 1)
        self.assertIn("answerCallbackQuery", calls)
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "approved")

    def test_reject_then_reason_is_saved(self):
        a = self.add()
        self.run_updates([_cb(1, f"r:{a}")])
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "rejected")
        self.assertIn("Why?", self.sent[-1][1])
        labels = [label for row in self.buttons[-1] for label, _ in row]
        self.assertEqual(labels, ["Price", "Too generic", "Bad niche", "Poor quality", "Other"])
        self.run_updates([_cb(2, f"y:{a}:too generic")])
        conn = guardrails.connect(self.db)
        self.assertEqual(conn.execute("SELECT reason FROM approvals WHERE id = ?", (a,)).fetchone()[0], "too generic")
        conn.close()

    def test_unknown_reason_or_pending_draft_not_saved(self):
        a = self.add()
        self.assertFalse(guardrails.set_reason(a, "price", self.db))        # still pending
        guardrails.decide(a, False, self.db)
        self.assertFalse(guardrails.set_reason(a, "DROP TABLE", self.db))
        self.assertTrue(guardrails.set_reason(a, "price", self.db))

    def test_view_button_shows_text_with_buttons(self):
        a = self.add()
        self.run_updates([_cb(1, f"v:{a}")])
        self.assertIn("BODY", self.sent[-1][1])
        self.assertIsNotNone(self.buttons[-1])

    def test_buttons_from_other_chats_are_ignored(self):
        a = self.add()
        stats = ti.new_stats()
        with mock.patch.object(ti, "fetch_updates", return_value=[_cb(1, f"a:{a}", chat="999"), _cb(3, "a:abc"),
                                                                  _cb(4, "x:1")]), \
                mock.patch.object(ti, "_call", return_value={"ok": True}):
            n = ti.process_updates("tok", OWNER, self.db, stats)
        self.assertEqual(n, 0)
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "pending")
        self.assertEqual(stats["skipped"], {"not owner chat": 1, "not a command": 0, "unknown button": 2})

    def test_button_in_owner_chat_works_even_when_presser_id_differs(self):
        """The real bug: buttons were also checked against the presser's own id, which only equals the chat
        id in a private chat. Typed commands never had that rule, so they worked while buttons were dropped."""
        a = self.add()
        n, _ = self.run_updates([_cb(1, f"v:{a}", sender="555")])
        self.assertEqual(n, 1)
        self.assertIn("BODY", self.sent[-1][1])

    def test_old_typed_commands_still_work(self):
        a = self.add()
        n, _ = self.run_updates([_upd(1, f"/approve {a}")])
        self.assertEqual(n, 1)
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "approved")

    def test_help_mentions_buttons_and_delay(self):
        self.assertIn("30 minutes", ti.HELP)
        self.assertIn("/pause", ti.HELP)


class PauseTests(InboxBase):
    def test_pause_and_resume(self):
        self.assertEqual(ti.parse_command("/pause"), ("pause", []))
        self.assertIn("Paused", ti.handle_command("pause", [], self.db))
        self.assertTrue(guardrails.is_paused(self.db))
        self.assertIn("Resumed", ti.handle_command("resume", [], self.db))
        self.assertFalse(guardrails.is_paused(self.db))

    def test_pause_from_a_stranger_is_ignored(self):
        with mock.patch.object(ti, "fetch_updates", return_value=[_upd(1, "/pause", chat="999")]):
            ti.process_updates("tok", OWNER, self.db)
        self.assertFalse(guardrails.is_paused(self.db))

    def test_paused_flag_stops_ai_and_drafts(self):
        import llm_router
        import scheduler
        import workforce
        guardrails.set_paused(True, self.db)
        with mock.patch.object(guardrails, "_TESTING", False), mock.patch.object(guardrails, "DB_PATH", self.db):
            self.assertTrue(guardrails.is_killed())
            self.assertFalse(guardrails.can_call_llm(self.db))
            self.assertEqual(workforce.run_once(db_path=self.db)["status"], "killed")
            self.assertTrue(scheduler.run_now(db_path=self.db).get("skipped"))
            self.assertIsNone(llm_router.chat("hello", db_path=self.db))
        guardrails.set_paused(False, self.db)
        with mock.patch.object(guardrails, "_TESTING", False), mock.patch.object(guardrails, "DB_PATH", self.db):
            self.assertFalse(guardrails.is_killed())

    def test_tests_ignore_the_real_pause_flag(self):
        with mock.patch.object(guardrails, "_TESTING", True), \
                mock.patch.object(guardrails, "is_paused", side_effect=AssertionError("must not read")):
            self.assertFalse(guardrails.is_killed())


class FreshButtonTests(InboxBase):
    def run_updates(self, updates):
        with mock.patch.object(ti, "fetch_updates", return_value=updates), \
                mock.patch.object(ti, "_call", return_value={"ok": True}):
            return ti.process_updates("tok", OWNER, self.db)

    def test_list_sends_fresh_buttons_for_each_waiting_draft(self):
        a, b = self.add(), self.add()
        self.run_updates([_upd(1, "/list")])
        self.assertIn("2 draft(s) waiting", self.sent[0][1])
        self.assertIsNone(self.buttons[0])
        self.assertEqual([row[0][1] for row in (self.buttons[1][0], self.buttons[2][0])], [f"a:{a}", f"a:{b}"])
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "pending")

    def test_button_test_round_trip_changes_nothing(self):
        a = self.add()
        self.assertEqual(ti.parse_command("/buttontest"), ("buttontest", []))
        self.run_updates([_upd(1, "/buttontest")])
        self.assertEqual(self.buttons[-1], [[("Test button", "t:ping")]])
        stats = ti.new_stats()
        with mock.patch.object(ti, "fetch_updates", return_value=[_cb(2, "t:ping")]), \
                mock.patch.object(ti, "_call", return_value={"ok": True}):
            self.assertEqual(ti.process_updates("tok", OWNER, self.db, stats), 1)
        self.assertEqual(self.sent[-1][1], ti.BUTTON_TEST_OK)
        self.assertEqual((stats["button"], stats["handled"]), (1, 1))
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "pending")
        self.assertIn("/buttontest", ti.HELP)


class StrictCommandTests(unittest.TestCase):
    def test_real_commands_still_work(self):
        self.assertEqual(ti.parse_command("/approve 4"), ("approve", [4]))
        self.assertEqual(ti.parse_command("approve 4"), ("approve", [4]))
        self.assertEqual(ti.parse_command("Approve 4, 5"), ("approve", [4, 5]))
        self.assertEqual(ti.parse_command("approve #4"), ("approve", [4]))
        self.assertEqual(ti.parse_command("/approve"), ("approve", []))
        self.assertEqual(ti.parse_command("/approve@chilligavvabot 6"), ("approve", [6]))
        self.assertEqual(ti.parse_command("list"), ("list", []))
        self.assertEqual(ti.parse_command("/view 5"), ("view", [5]))
        self.assertEqual(ti.parse_command("/pause"), ("pause", []))

    def test_normal_sentences_are_not_commands(self):
        for text in ("Approve button edhuku work avadam ledhu?", "approve this one please",
                     "view chesthe em kanipinchaledu", "List lo enni unnayi?", "reject 4 because price",
                     "I will approve 4 later", "approved"):
            with self.subTest(text=text):
                self.assertEqual(ti.parse_command(text), (None, []))

    def test_sentence_handled_by_chat_brain(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sent = []
        with mock.patch.object(ti, "fetch_updates", return_value=[_upd(1, "Approve button edhuku work avadam ledhu?")]), \
                mock.patch.object(ti, "_call", return_value={"ok": True}), \
                mock.patch.object(ti, "send_text", side_effect=lambda *a, **k: sent.append(a) or True):
            stats = ti.new_stats()
            self.assertEqual(ti.process_updates("tok", OWNER, os.path.join(tmp.name, "t.db"), stats), 1)
        self.assertTrue(len(sent) >= 1)


class LogLineTests(InboxBase):
    def test_counts_by_kind_and_reasons_without_private_data(self):
        a = self.add()
        updates = [_upd(1, f"/view {a}"), _upd(2, "hello there friend"), _upd(3, "/list", chat="777"),
                   _cb(4, f"v:{a}"), {"update_id": 5, "message": {"chat": {"id": int(OWNER)}, "sticker": {}}},
                   {"update_id": 6, "edited_message": {}}]
        stats = ti.new_stats()
        with mock.patch.object(ti, "fetch_updates", return_value=updates), \
                mock.patch.object(ti, "_call", return_value={"ok": True}):
            ti.process_updates("SECRET-TOKEN", OWNER, self.db, stats)
        line = ti.stats_line(stats, "webhook set: no")
        self.assertEqual(line, "Telegram updates: 3 text, 1 button, 2 other; handled 3; "
                               "skipped: 1 not owner chat; webhook set: no.")
        for private in ("SECRET", OWNER, "777", "hello", "/view"):
            self.assertNotIn(private, line)

    def test_read_failure_is_named(self):
        import urllib.error
        import io
        err = urllib.error.HTTPError("https://api.telegram.org/botSECRET/getUpdates", 409, "Conflict", {},
                                     io.BytesIO(b"{}"))
        with mock.patch.object(ti, "_call", side_effect=err):
            self.assertEqual(ti.fetch_updates("SECRET", None), [])
        line = ti.stats_line(ti.new_stats())
        self.assertIn("READ FAILED: 409 (another program or a webhook is reading this bot)", line)
        self.assertNotIn("SECRET", line)
        with mock.patch.object(ti, "_call", return_value={"ok": True, "result": []}):
            ti.fetch_updates("SECRET", None)
        self.assertNotIn("READ FAILED", ti.stats_line(ti.new_stats()))

    def test_always_asks_for_button_presses(self):
        seen = {}
        with mock.patch.object(ti, "_call", side_effect=lambda t, m, p: seen.update(p) or {"ok": True, "result": []}):
            ti.fetch_updates("tok", 5)
        self.assertEqual(seen["allowed_updates"], ["message", "callback_query"])

    def test_subscription_note_warns(self):
        cases = [({"url": "", "allowed_updates": ["message"]}, "WARNING button presses are not delivered"),
                 ({"url": "https://x.invalid/hook"}, "webhook set: YES"),
                 ({"url": "", "allowed_updates": ["message", "callback_query"]}, "bot asks for: message, callback_query")]
        for info, expected in cases:
            with mock.patch.object(ti, "_call", return_value={"ok": True, "result": info}):
                note = ti.subscription_note("tok")
            self.assertIn(expected, note)
            self.assertNotIn("x.invalid", note)


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
