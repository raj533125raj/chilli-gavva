"""Late callback receipts never consume the actual owner decision."""
import io
import json
import tempfile
import unittest
import urllib.error
from unittest import mock
from pathlib import Path
import guardrails
import improver_loop
import telegram_inbox as inbox

OWNER = int("1234" "5678")

class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "state.db")
        guardrails.connect(self.db).close()
        self.messages = []
        self.edits = []

    def press(self, data, old_receipt=False, broken_edit=False, owner=OWNER, message_date=None):
        update = {"update_id": 1, "callback_query": {"id": "test-query", "data": data,
                  "message": {"message_id": 7, "chat": {"id": owner}}}}
        if message_date is not None:
            update["callback_query"]["message"]["date"] = message_date
        def open_request(request, timeout=None):
            method = request.full_url.rsplit("/",1)[-1]
            params = json.loads(request.data)
            if method == "answerCallbackQuery" and old_receipt:
                raise urllib.error.HTTPError(request.full_url,400,"old query",{},io.BytesIO(json.dumps({"ok":False,"description":"Bad Request: query is too old and response timeout expired or query ID is invalid"}).encode()))
            if method == "editMessageText":
                self.edits.append(params)
                if broken_edit:
                    raise urllib.error.HTTPError(request.full_url,400,"missing message",{},io.BytesIO(b"{}"))
            if method == "sendMessage": self.messages.append(params)
            return io.BytesIO(json.dumps({"ok":True}).encode())
        with mock.patch.object(inbox, "fetch_updates", return_value=[update]), mock.patch.object(inbox, "acknowledge_update"), mock.patch("urllib.request.urlopen", side_effect=open_request):
            stats = inbox.new_stats()
            handled = inbox.process_updates("test-bot", OWNER, self.db, stats)
        return handled, stats

    def test_old_acknowledgement_still_approves_and_sends_result(self):
        aid = guardrails.request_approval("public_draft", "Printable routine", "Daily planning pages", None, self.db)
        handled, _ = self.press(f"a:{aid}", old_receipt=True)
        self.assertEqual(handled,1)
        self.assertEqual(guardrails.get_approval(aid,self.db)["status"],"approved")
        self.assertEqual(len(self.messages),1)
        self.assertIn("Approved",self.messages[0]["text"])
        self.assertEqual(self.messages[0]["chat_id"],OWNER)

    def test_missing_old_message_still_gets_a_new_result(self):
        aid = guardrails.request_approval("public_draft", "Printable routine", "Daily planning pages", None, self.db)
        self.press(f"a:{aid}",old_receipt=True,broken_edit=True)
        self.assertEqual(len(self.messages),1)
        self.assertIn("Approved",self.messages[0]["text"])
        self.assertEqual(guardrails.get_approval(aid,self.db)["status"],"approved")

    def test_deleted_draft_and_unknown_button_point_to_new_drafts(self):
        for data in ("a:999", "v:999", "r:999", "unrecognized-old-button"):
            with self.subTest(data=data):
                self.messages.clear()
                with mock.patch.object(inbox,"get_state",return_value=None): self.press(data)
                self.assertEqual([m["text"] for m in self.messages],["Ee button paatadi. Next: /drafts"])

    def test_expired_and_deleted_improvement_buttons_are_stale(self):
        conn = improver_loop.connect(self.db)
        conn.execute("INSERT INTO learned_rules (rule_text,status) VALUES ('Improve descriptions','rejected')")
        conn.commit(); conn.close()
        for data in ("imp:a:1","imp:r:999"):
            with self.subTest(data=data):
                self.messages.clear()
                with mock.patch.object(inbox,"get_state",return_value=None): self.press(data)
                self.assertEqual([m["text"] for m in self.messages],["Ee button paatadi. Next: /drafts"])

    def test_overdue_proposal_cannot_be_approved_before_timeout_sweep(self):
        conn = improver_loop.connect(self.db)
        conn.execute("INSERT INTO learned_rules (rule_text,status,created_ts) VALUES ('Improve descriptions','proposed','2026-01-01T00:00:00+00:00')")
        conn.commit(); conn.close()
        self.press("imp:a:1")
        self.assertEqual([m["text"] for m in self.messages],["Ee button paatadi. Next: /drafts"])
        conn = improver_loop.connect(self.db)
        self.assertEqual(conn.execute("SELECT status FROM learned_rules WHERE id=1").fetchone()[0],"proposed")
        conn.close()

    def test_expired_receipt_and_first_button_age_survive_safe_run_log(self):
        import telegram_diagnostics as diagnostics
        import contextlib
        aid = guardrails.request_approval("public_draft", "Printable routine", "Daily planning pages", None, self.db)
        stamp = 1800000000
        with mock.patch.object(inbox.time, "time", return_value=stamp), diagnostics.session() as state:
            _, stats = self.press(f"a:{aid}", old_receipt=True, message_date=stamp-17*60)
            self.assertEqual(stats["buttons_too_old"],1)
            self.assertEqual(stats["first_button_age_min"],17)
            self.assertEqual(state["send_failed"],0)
            output = io.StringIO()
            with contextlib.redirect_stdout(output): diagnostics.print_summary(state,stats)
        line = next(line for line in output.getvalue().splitlines() if line.startswith("Telegram diagnostic: updates="))
        self.assertTrue(diagnostics.safe_line(line))
        self.assertIn("buttons_too_old=1 first_button_age_min=17",line)
        self.assertIn("buttons_too_old=1 first_button_age_min=17",inbox.stats_line(stats))
        self.assertFalse(diagnostics.safe_line(line+" PRIVATE"))

    def test_future_button_date_is_clamped_and_current_receipt_is_not_old(self):
        with mock.patch.object(inbox.time,"time",return_value=1000):
            _,stats = self.press("unrecognized-old-button",message_date=2000)
        self.assertEqual(stats["first_button_age_min"],0)
        self.assertEqual(stats["buttons_too_old"],0)

    def test_other_chat_cannot_use_old_or_live_buttons(self):
        handled, stats = self.press("a:999",owner=OWNER+1)
        self.assertEqual(handled,0)
        self.assertEqual(self.messages,[])
        self.assertEqual(stats["skipped"]["not owner chat"],1)
