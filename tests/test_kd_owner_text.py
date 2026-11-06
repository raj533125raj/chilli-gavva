"""Tests for Binny-KD Robot: talk to the robot in Tenglish / owner text parser."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guardrails
import owner_commands
import telegram_inbox as ti

OWNER = "111222333"


def _upd(uid, text, chat=OWNER):
    return {"update_id": uid, "message": {"chat": {"id": int(chat)}, "text": text}}


def _cb(uid, data, msg_id=100, chat=OWNER, sender=OWNER):
    return {
        "update_id": uid,
        "callback_query": {
            "id": f"q{uid}",
            "data": data,
            "from": {"id": int(sender)},
            "message": {"message_id": msg_id, "chat": {"id": int(chat)}},
        },
    }


class KdOwnerTextTests(unittest.TestCase):
    def setUp(self):
        # Clear all GITHUB_* environment variables in setUp using mock.patch.dict
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
        env_patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "kd_test.db")

        self.sent = []
        self.edited = []
        self.calls = []

        def fake_send(tok, chat, text, buttons=None):
            self.sent.append((str(chat), text, buttons))
            return True

        def fake_edit(tok, chat, msg_id, text, buttons=None):
            self.edited.append((str(chat), msg_id, text, buttons))
            return True

        def fake_call(tok, method, params, *a, **kw):
            self.calls.append((method, params))
            return {"ok": True}

        p_send = mock.patch.object(ti, "send_text", side_effect=fake_send)
        p_edit = mock.patch.object(ti, "edit_text", side_effect=fake_edit)
        p_call = mock.patch.object(ti, "_call", side_effect=fake_call)

        p_send.start()
        p_edit.start()
        p_call.start()

        self.addCleanup(p_send.stop)
        self.addCleanup(p_edit.stop)
        self.addCleanup(p_call.stop)

    def add(self, title="Draft: [Budget Sheets Agent] test", review="score 80/100"):
        return guardrails.request_approval("public_draft", title, "BODY " + title, review=review, db_path=self.db)

    def run_updates(self, updates):
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            return ti.process_updates("tok", OWNER, self.db)

    # 1. "59 approve chey" -> confirm prompt, Yes -> approved, No -> nothing
    def test_approve_chey_flow(self):
        draft_id = self.add(title="Planner 59")
        # Prompt: "59 approve chey"
        self.run_updates([_upd(1, f"{draft_id} approve chey")])
        self.assertEqual(len(self.sent), 1)
        chat, text, buttons = self.sent[0]
        self.assertEqual(chat, OWNER)
        self.assertIn("Confirm: approve draft #" + str(draft_id) + "? Yes/No", text)
        self.assertIsNotNone(buttons)
        # Verify inline Yes/No buttons
        btn_row = buttons[0]
        self.assertEqual(btn_row[0][0], "Yes")
        self.assertEqual(btn_row[1][0], "No")
        cb_yes = btn_row[0][1]
        cb_no = btn_row[1][1]
        self.assertTrue(cb_yes.startswith("kd:y:"))
        self.assertTrue(cb_no.startswith("kd:n:"))

        # Before clicking Yes, draft is still pending
        self.assertEqual(guardrails.get_approval(draft_id, self.db)["status"], "pending")

        # Click Yes button
        self.run_updates([_cb(2, cb_yes)])
        self.assertEqual(guardrails.get_approval(draft_id, self.db)["status"], "approved")
        self.assertIn(f"Approved ✓ #{draft_id}", self.sent[-1][1])

        # Second press on used confirmation token says expired / not found
        self.run_updates([_cb(3, cb_yes)])
        self.assertIn("expired", self.sent[-1][1].lower())

    def test_approve_chey_cancelled_with_no(self):
        draft_id = self.add(title="Planner 59")
        self.run_updates([_upd(1, f"{draft_id} approve chey")])
        btn_row = self.sent[0][2][0]
        cb_no = btn_row[1][1]

        # Click No button
        self.run_updates([_cb(2, cb_no)])
        self.assertEqual(guardrails.get_approval(draft_id, self.db)["status"], "pending")
        self.assertIn("Cancelled action 'approve_draft'", self.sent[-1][1])

    # 2. Reject flow with reason
    def test_reject_chey_flow(self):
        draft_id = self.add(title="Planner 61")
        self.run_updates([_upd(1, f"draft {draft_id} reject chey, too generic")])
        self.assertEqual(len(self.sent), 1)
        chat, text, buttons = self.sent[0]
        self.assertIn(f"Confirm: reject draft #{draft_id} (too generic)? Yes/No", text)
        cb_yes = buttons[0][0][1]

        # Confirm Yes
        self.run_updates([_cb(2, cb_yes)])
        appr = guardrails.get_approval(draft_id, self.db)
        self.assertEqual(appr["status"], "rejected")
        conn = guardrails.connect(self.db)
        saved_reason = conn.execute("SELECT reason FROM approvals WHERE id = ?", (draft_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(saved_reason, "Too generic")
        self.assertIn("Rejected: Too generic", self.sent[-1][1])

    # 3. Confirmation expiry (> 10 minutes)
    def test_confirmation_expired_after_10_minutes(self):
        draft_id = self.add(title="Planner 77")
        self.run_updates([_upd(1, f"{draft_id} approve chey")])
        cb_yes = self.sent[0][2][0][0][1]

        # Simulate 11 minutes later
        with mock.patch("time.time", return_value=time.time() + 11 * 60):
            self.run_updates([_cb(2, cb_yes)])
        self.assertEqual(guardrails.get_approval(draft_id, self.db)["status"], "pending")
        self.assertIn("expired", self.sent[-1][1].lower())

    # 4. "ee roju em jarigindi" -> daily report text
    def test_what_happened_today(self):
        self.run_updates([_upd(1, "ee roju em jarigindi")])
        self.assertEqual(len(self.sent), 1)
        chat, text, buttons = self.sent[0]
        self.assertEqual(chat, OWNER)
        self.assertIn("Chilli Gavva daily report", text)
        self.assertIsNone(buttons)

    # 5. "Pinterest room ni pedda chey" -> queued office request
    def test_pinterest_room_resize_queued(self):
        self.run_updates([_upd(1, "Pinterest room ni pedda chey")])
        self.assertEqual(len(self.sent), 1)
        chat, text, buttons = self.sent[0]
        self.assertEqual(chat, OWNER)
        self.assertIn("Office lo marchutha:", text)
        self.assertIn("Pinterest room bigger", text)
        self.assertIn("Binny Mills app lo apply avutundi", text)

        # Check database table office_requests
        conn = guardrails.connect(self.db)
        row = conn.execute("SELECT intent, args FROM office_requests").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "resize_room")
        args = json.loads(row[1])
        self.assertEqual(args.get("name"), "Pinterest")
        self.assertEqual(args.get("size"), "bigger")

    # 6. Safe intents run at once: list drafts, status, daily report, packs
    def test_safe_intents_execute_immediately(self):
        d1 = self.add(title="Draft Safe 1")
        # "list drafts" / "drafts chupinchu"
        self.run_updates([_upd(1, "drafts chupinchu")])
        # Summary text + 1 draft card
        self.assertTrue(any("1 draft(s) waiting" in t for _, t, _ in self.sent))

        # "show status"
        self.sent.clear()
        self.run_updates([_upd(2, "status chupinchu")])
        self.assertTrue(any("Chilli Gavva Status" in t for _, t, _ in self.sent))

        # "daily report"
        self.sent.clear()
        self.run_updates([_upd(3, "roju report chupinchu")])
        self.assertTrue(any("Chilli Gavva daily report" in t for _, t, _ in self.sent))

        # "list packs"
        self.sent.clear()
        self.run_updates([_upd(4, "packs chupinchu")])
        self.assertTrue(any("upload pack" in t.lower() or "ready pack" in t.lower() for _, t, _ in self.sent))

        # "help"
        self.sent.clear()
        self.run_updates([_upd(5, "help")])
        self.assertTrue(any("Commands:" in t or "approvals" in t for _, t, _ in self.sent))

    # 7. Unknown plain text replies with parser's Tenglish help hint
    def test_unknown_command_returns_tenglish_hint(self):
        self.run_updates([_upd(1, "random unknown chatter xyz123")])
        self.assertEqual(len(self.sent), 1)
        chat, text, buttons = self.sent[0]
        self.assertEqual(chat, OWNER)
        self.assertIn("Ardam kaaledu", text)
        self.assertIn(owner_commands.TENGLISH_UNKNOWN_HINT, text)

    # 8. Non-owner message is ignored
    def test_non_owner_ignored(self):
        d = self.add(title="Draft 99")
        self.run_updates([_upd(1, f"{d} approve chey", chat="9999999")])
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(guardrails.get_approval(d, self.db)["status"], "pending")

        # Non-owner callback button press is ignored
        self.run_updates([_upd(2, f"{d} approve chey", chat=OWNER)])
        cb_yes = self.sent[0][2][0][0][1]
        self.sent.clear()
        self.run_updates([_cb(3, cb_yes, chat="9999999", sender="9999999")])
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(guardrails.get_approval(d, self.db)["status"], "pending")

    # 9. Old slash commands unchanged
    def test_old_slash_commands_unchanged(self):
        d = self.add(title="Draft 101")
        self.run_updates([_upd(1, f"/approve {d}")])
        self.assertEqual(guardrails.get_approval(d, self.db)["status"], "approved")
        self.run_updates([_upd(2, "/status")])
        self.assertTrue(any("Chilli Gavva Status" in t for _, t, _ in self.sent))

    # 10. Pause all and resume all flow with confirm
    def test_pause_and_resume_all_confirm(self):
        # "anni aapu" -> pause_all
        self.run_updates([_upd(1, "anni aapu")])
        self.assertIn("Confirm: pause all? Yes/No", self.sent[-1][1])
        cb_yes = self.sent[-1][2][0][0][1]

        self.assertFalse(guardrails.is_paused(self.db))
        self.run_updates([_cb(2, cb_yes)])
        self.assertTrue(guardrails.is_paused(self.db))
        self.assertIn("Paused", self.sent[-1][1])

        # "malli start chey" -> resume_all
        self.run_updates([_upd(3, "malli start chey")])
        self.assertIn("Confirm: resume all? Yes/No", self.sent[-1][1])
        cb_resume_yes = self.sent[-1][2][0][0][1]

        self.run_updates([_cb(4, cb_resume_yes)])
        self.assertFalse(guardrails.is_paused(self.db))
        self.assertIn("Resumed", self.sent[-1][1])

    # 11. Run now confirm
    def test_run_now_confirm(self):
        self.run_updates([_upd(1, "ippude run chey")])
        self.assertIn("Confirm: run now? Yes/No", self.sent[-1][1])
        cb_yes = self.sent[-1][2][0][0][1]
        self.run_updates([_cb(2, cb_yes)])
        self.assertIn("Engine run triggered", self.sent[-1][1])


if __name__ == "__main__":
    unittest.main()
