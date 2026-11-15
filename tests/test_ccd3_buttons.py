"""CCD-3: Approve buttons doctor.

Every real way a button press could be lost or look like "nothing happened" has a test here, and /buttontest
reports on the next run exactly what happened (press received or not, webhook, allowed_updates).
The Telegram HTTP layer (_call) is faked; send_text / process_updates / _run are the real code. No network.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guardrails
import telegram_inbox as ti

OWNER = int("1234" "5678")
STRANGER = int("8765" "4321")


def media_brain_patch():
    """process_updates imports media_brain (-> llm_router, Python 3.10+ syntax). Text and buttons never use it,
    so on an older local Python an empty stand-in is used; CI (3.11) loads the real module."""
    try:
        import media_brain  # noqa: F401
        return []
    except Exception:
        return [mock.patch.dict(sys.modules, {"media_brain": types.ModuleType("media_brain")})]


class FakeTelegram:
    """Answers _call(token, method, params) like the Bot API and records every call."""

    def __init__(self):
        self.updates = []
        self.calls = []
        self.webhook = {"url": "", "allowed_updates": ["message", "callback_query"]}
        self.bot_id = 777

    def __call__(self, token, method, params, *a, **k):
        self.calls.append((method, params))
        if method == "getMe":
            return {"ok": True, "result": {"id": self.bot_id, "is_bot": True}}
        if method == "getWebhookInfo":
            return {"ok": True, "result": dict(self.webhook)}
        if method == "getUpdates":
            if params.get("limit") == 1:             # acknowledgement call
                return {"ok": True, "result": []}
            offset = params.get("offset") or 0
            return {"ok": True, "result": [u for u in self.updates if u["update_id"] >= offset]}
        return {"ok": True, "result": True}

    def sent(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]

    def sent_with_buttons(self):
        return [p for m, p in self.calls if m == "sendMessage" and "reply_markup" in p]

    def answered(self):
        return [p for m, p in self.calls if m == "answerCallbackQuery"]


def press(uid, data, chat=OWNER, sender=OWNER, with_message=True):
    cb = {"id": f"q{uid}", "data": data, "from": {"id": sender}}
    if with_message:
        cb["message"] = {"message_id": 5, "chat": {"id": chat}}
    return {"update_id": uid, "callback_query": cb}


def text(uid, words, chat=OWNER):
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": chat}, "text": words}}


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = str(Path(temp.name) / "robot.db")
        self.tg = FakeTelegram()
        patches = [mock.patch.object(ti, "_call", side_effect=self.tg),
                   mock.patch("notify._telegram_configured", return_value=(True, "123:abc", str(OWNER))),
                   mock.patch.dict(os.environ, {"CF_KV_NAMESPACE_ID": "", "CF_ACCOUNT_ID": "", "CF_API_TOKEN": "",
                                                "ALWAYS_ON_URL": "", "ALWAYS_ON_SECRET": ""})]
        for patch in patches + media_brain_patch():
            patch.start()
            self.addCleanup(patch.stop)

    def draft(self):
        return guardrails.request_approval("public_draft", "Draft: Habit tracker", "PRODUCT NAME: Habit\nBODY",
                                           None, self.db)

    def poll(self, updates):
        self.tg.updates = list(updates)
        stats = ti.new_stats()
        with contextlib.redirect_stderr(io.StringIO()):
            ti.process_updates("123:abc", OWNER, self.db, stats)
        return stats

    def run_inbox(self, updates):
        """One real recovery run (_run: getMe, getChat, getWebhookInfo, updates, button test report)."""
        self.tg.updates = list(updates)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            ti._run(self.db, recovery=True)
        return out.getvalue()


class LostPressTests(Base):
    def test_press_without_message_uses_presser_id(self):
        """Reason: very old / inaccessible messages come without callback_query.message, so the chat was ''
        and the owner's press was dropped as 'not owner chat'."""
        a = self.draft()
        stats = self.poll([press(1, f"a:{a}", with_message=False)])
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "approved")
        self.assertEqual(stats["skipped"]["not owner chat"], 0)

    def test_press_without_message_from_stranger_is_ignored(self):
        a = self.draft()
        stats = self.poll([press(1, f"a:{a}", sender=STRANGER, with_message=False)])
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "pending")
        self.assertEqual(stats["skipped"]["not owner chat"], 1)
        self.assertEqual(self.tg.answered(), [])

    def test_chat_id_text_and_number_match(self):
        a = self.draft()
        self.tg.updates = [press(1, f"a:{a}", chat=str(OWNER))]
        with contextlib.redirect_stderr(io.StringIO()):
            ti.process_updates("123:abc", " " + str(OWNER) + " ", self.db, ti.new_stats())
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "approved")

    def test_crash_in_button_handler_is_told_and_next_update_still_runs(self):
        """Reason: the update was confirmed before handling; an exception stopped the whole inbox and the press
        (plus every later update in the batch) was lost silently."""
        a, b = self.draft(), self.draft()
        real = ti.handle_callback

        def flaky(token, chat, data, db_path=None, message_id=None):
            if data == f"a:{a}":
                raise RuntimeError("database is locked")
            return real(token, chat, data, db_path, message_id=message_id)

        with mock.patch.object(ti, "handle_callback", side_effect=flaky):
            self.poll([press(1, f"a:{a}"), press(2, f"a:{b}")])
        self.assertIn(f"Button pani cheyaledu, robot fix chestundi. Ippudu /approve {a} ani type cheyandi.",
                      self.tg.sent())
        self.assertEqual(guardrails.get_approval(b, self.db)["status"], "approved")
        self.assertEqual(len(self.tg.answered()), 2)
        self.assertNotIn("locked", " ".join(self.tg.sent()))

    def test_approve_works_when_ai_module_cannot_import(self):
        """Reason: handle_callback imported chat_brain (AI) before looking at a:/r:/v: buttons, so an AI import
        problem broke every Approve button."""
        a = self.draft()
        with mock.patch.dict(sys.modules, {"chat_brain": None, "autofix": None, "improver_loop": None}):
            self.poll([press(1, f"a:{a}")])
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "approved")

    def test_every_owner_press_is_answered_with_a_toast(self):
        """Reason: an unanswered press keeps the spinning clock on the button ('nothing happens')."""
        a = self.draft()
        self.poll([press(1, f"v:{a}"), press(2, "zz:unknown")])
        answered = self.tg.answered()
        self.assertEqual([p["callback_query_id"] for p in answered], ["q1", "q2"])
        self.assertTrue(all(p.get("text") for p in answered))

    def test_second_press_on_decided_draft_gets_a_visible_reply(self):
        """Reason: pressing Approve on an already approved draft returned silently."""
        a = self.draft()
        self.poll([press(1, f"a:{a}")])
        self.poll([press(2, f"a:{a}")])
        self.assertIn(f"Approved ✓ #{a} (mundee approve ayyindi)", self.tg.sent())
        b = self.draft()
        self.poll([press(3, f"r:{b}")])
        self.poll([press(4, f"r:{b}")])
        self.assertTrue(any("mundee reject ayyindi" in t for t in self.tg.sent()))
        self.poll([press(5, f"y:{b}:duplicate")])
        self.poll([press(6, f"y:{b}:other")])
        self.assertTrue(any("reason mundee save ayyindi" in t for t in self.tg.sent()))

    def test_unknown_button_gets_a_reply(self):
        stats = self.poll([press(1, "old:format:9")])
        self.assertEqual(stats["skipped"]["unknown button"], 1)
        self.assertIn(ti.UNKNOWN_BUTTON_TEXT, self.tg.sent())

    def test_every_poll_asks_for_button_presses(self):
        """Reason: Telegram only keeps the update types the bot last asked for."""
        a = self.draft()
        self.poll([press(1, f"a:{a}")])
        asks = [p.get("allowed_updates") for m, p in self.tg.calls if m == "getUpdates"]
        self.assertTrue(asks)
        self.assertTrue(all("callback_query" in x for x in asks))

    def test_typed_fallback_still_works(self):
        a = self.draft()
        self.poll([text(1, f"/approve {a}")])
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "approved")


class OffsetTests(Base):
    def test_offset_of_an_old_bot_is_cleared(self):
        """Reason: after a new bot was made, the old bot's high update offset made getUpdates confirm (delete)
        every new message and button press."""
        ti.set_state("bot_id", "111", self.db)
        ti.set_state("update_offset", "900000", self.db)
        a = self.draft()
        out = self.run_inbox([press(5, f"a:{a}")])
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "approved")
        self.assertEqual(ti.get_state("bot_id", None, self.db), "777")
        self.assertIn("old update offset was cleared", out)
        self.assertNotIn("123:abc", out)

    def test_same_bot_keeps_its_offset(self):
        ti.set_state("bot_id", "777", self.db)
        ti.set_state("update_offset", "50", self.db)
        a = self.draft()
        self.run_inbox([press(49, f"a:{a}")])           # already confirmed earlier: not handled twice
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "pending")
        self.run_inbox([press(50, f"a:{a}")])
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "approved")

    def test_first_run_only_remembers_the_bot(self):
        ti.set_state("update_offset", "60", self.db)
        self.assertFalse(ti.check_bot_identity({"id": 777}, self.db))
        self.assertEqual(ti.get_state("update_offset", None, self.db), "60")
        self.assertFalse(ti.check_bot_identity({"id": "bad"}, self.db))
        self.assertFalse(ti.check_bot_identity(None, self.db))


class ButtonTestReportTests(Base):
    def report(self):
        return [t for t in self.tg.sent() if t.startswith("\U0001f50e Button test report")]

    def test_buttontest_sends_one_test_button_and_no_report_in_same_run(self):
        self.run_inbox([text(1, "/buttontest")])
        buttons = self.tg.sent_with_buttons()
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "t:ping")
        self.assertEqual(self.report(), [])

    def test_next_run_reports_press_received(self):
        self.run_inbox([text(1, "/buttontest")])
        self.run_inbox([press(2, "t:ping")])
        report = self.report()
        self.assertEqual(len(report), 1)
        self.assertIn("Button press: VACHINDI ✅", report[0])
        self.assertIn("Webhook: ledu ✅", report[0])
        self.assertIn("allowed_updates: message, callback_query ✅", report[0])
        self.assertIn("buttons=1", report[0])
        self.assertIn("/approve N", report[0])
        self.assertIn(ti.BUTTON_TEST_OK, self.tg.sent())
        self.run_inbox([])                                # reported once only
        self.assertEqual(len(self.report()), 1)

    def test_next_run_reports_press_not_received(self):
        self.run_inbox([text(1, "/buttontest")])
        self.run_inbox([])
        report = self.report()
        self.assertEqual(len(report), 1)
        self.assertIn("Button press: RAALEDU ❌", report[0])
        self.assertIn("/buttontest malli", report[0])

    def test_report_shows_allowed_updates_without_buttons(self):
        self.run_inbox([text(1, "/buttontest")])
        self.tg.webhook = {"url": "", "allowed_updates": ["message"]}
        self.run_inbox([])
        report = self.report()[0]
        self.assertIn("allowed_updates: message ❌ callback_query ledu, buttons raavu", report)
        self.assertIn("getUpdates", report)

    def test_report_shows_default_allowed_updates(self):
        self.run_inbox([text(1, "/buttontest")])
        self.tg.webhook = {"url": ""}
        self.run_inbox([press(2, "t:ping")])
        self.assertIn("allowed_updates: default (anni types, buttons kuda) ✅", self.report()[0])

    def test_webhook_set_is_reported_then_run_stops(self):
        self.run_inbox([text(1, "/buttontest")])
        self.tg.webhook = {"url": "https://hook.example.com/x", "allowed_updates": ["message", "callback_query"]}
        with self.assertRaises(RuntimeError):
            self.run_inbox([])
        report = self.report()[0]
        self.assertIn("Webhook: SET ayyi undi ❌", report)
        self.assertIn("deleteWebhook", report)
        self.assertNotIn("hook.example.com", report)

    def test_report_never_contains_token_or_chat_id(self):
        self.run_inbox([text(1, "/buttontest")])
        self.run_inbox([press(2, "t:ping")])
        report = self.report()[0]
        self.assertNotIn("123:abc", report)
        self.assertNotIn(str(OWNER), report)


if __name__ == "__main__":
    unittest.main()
