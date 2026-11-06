"""Tests for BINNY-JE2 one-tap draft approval and edit flow."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guardrails
import telegram_inbox as ti

OWNER = "111222333"


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


def _upd(uid, text, chat=OWNER):
    return {"update_id": uid, "message": {"chat": {"id": int(chat)}, "text": text}}


class ApproveFlowTests(unittest.TestCase):
    def setUp(self):
        # Clear CI environment variables
        self.env_patcher = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
        }, clear=False)
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "flow.db")

        self.sent = []
        self.edited = []
        self.calls = []

        def fake_send(tok, chat, text, buttons=None):
            self.sent.append((chat, text, buttons))
            return True

        def fake_edit(tok, chat, msg_id, text, buttons=None):
            self.edited.append((chat, msg_id, text, buttons))
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

    def add(self, title="Planner Draft", review="score 85/100 | top: Clean format; Great market"):
        return guardrails.request_approval("public_draft", title, "BODY " + title, review=review, db_path=self.db)

    def run_updates(self, updates):
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            return ti.process_updates("tok", OWNER, self.db)

    def test_list_shows_waiting_drafts_with_buttons(self):
        d1 = self.add(title="Draft 1", review="score 85/100 | top: Good format; Clear focus")
        d2 = self.add(title="Draft 2", review="score 72/100 | top: Solid value; Niche fit")

        self.run_updates([_upd(1, "/list")])
        # Summary message plus one message per draft
        draft_messages = [(c, t, b) for c, t, b in self.sent if b is not None]
        self.assertEqual(len(draft_messages), 2)
        # Check first draft message
        chat, text, btns = draft_messages[0]
        self.assertIn("#" + str(d1), text)
        self.assertIn("Checker: 85/100", text)
        self.assertIn("- Good format", text)
        self.assertIn("- Clear focus", text)
        self.assertEqual(btns, ti.draft_buttons(d1))

    def test_one_tap_approve_edits_message_in_place(self):
        d = self.add()
        # Tap Approve
        self.run_updates([_cb(1, f"a:{d}", msg_id=42)])
        self.assertEqual(guardrails.get_approval(d, self.db)["status"], "approved")
        self.assertEqual(len(self.edited), 1)
        chat, msg_id, text, btns = self.edited[0]
        self.assertEqual(chat, OWNER)
        self.assertEqual(msg_id, 42)
        self.assertEqual(text, "Approved ✓")
        self.assertIsNone(btns)

        # Second tap does nothing
        self.run_updates([_cb(2, f"a:{d}", msg_id=42)])
        self.assertEqual(len(self.edited), 1)
        self.assertEqual(guardrails.get_approval(d, self.db)["status"], "approved")

    def test_reject_flow_asks_reasons_and_edits_on_selection(self):
        d = self.add()
        # Press Reject
        self.run_updates([_cb(1, f"r:{d}", msg_id=50)])
        self.assertEqual(guardrails.get_approval(d, self.db)["status"], "rejected")
        self.assertEqual(len(self.edited), 1)
        chat, msg_id, text, btns = self.edited[0]
        self.assertIn(f"Rejected: #{d}. Why?", text)
        labels = [label for row in btns for label, _ in row]
        self.assertEqual(labels, ["Too generic", "Duplicate", "Weak niche", "Promises too much", "Other"])

        # Pick reason "Weak niche"
        self.run_updates([_cb(2, f"y:{d}:weak niche", msg_id=50)])
        conn = guardrails.connect(self.db)
        saved_reason = conn.execute("SELECT reason FROM approvals WHERE id = ?", (d,)).fetchone()[0]
        conn.close()
        self.assertEqual(saved_reason, "Weak niche")

        # Second edit reflects the chosen reason
        self.assertEqual(len(self.edited), 2)
        _, _, text2, btns2 = self.edited[1]
        self.assertEqual(text2, "Rejected: Weak niche")
        self.assertIsNone(btns2)

        # Subsequent press does nothing
        self.run_updates([_cb(3, f"y:{d}:weak niche", msg_id=50)])
        self.assertEqual(len(self.edited), 2)

    def test_owner_chat_only_and_text_scrubbed(self):
        d = self.add()
        # Non-owner press is ignored
        self.run_updates([_cb(1, f"a:{d}", msg_id=99, chat="999999")])
        self.assertEqual(guardrails.get_approval(d, self.db)["status"], "pending")
        self.assertEqual(len(self.edited), 0)

        # Scrub outgoing check
        long_text = "sample " * 700
        scrubbed = ti._scrub_outgoing(long_text)
        self.assertLessEqual(len(scrubbed), 3500)
        self.assertTrue(scrubbed.endswith("...(cut, message length limit)"))


if __name__ == "__main__":
    unittest.main()
