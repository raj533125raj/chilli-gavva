"""R22 public-status privacy checks (all data is local and temporary)."""
import json
import os
import tempfile
import unittest
from unittest import mock

import export_status
import guardrails
import telegram_inbox as inbox


class PublicDraftTitleTests(unittest.TestCase):
    def test_pending_draft_title_is_anonymous_in_written_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "robot.db")
            path = os.path.join(tmp, "status.json")
            title = "Draft: [ADHD Planners Agent] Travel itinerary planner for busy families"
            approval_id = guardrails.request_approval("public_draft", title, "private draft", db_path=db)

            export_status.write_status(path, db)
            with open(path, encoding="utf-8") as status_file:
                status = json.load(status_file)
            blob = json.dumps(status)

            self.assertNotIn(title, blob)
            self.assertEqual(status["pending_approvals"], [{
                "id": approval_id, "ts": status["pending_approvals"][0]["ts"],
                "kind": "public_draft", "title": f"Draft #{approval_id}",
            }])

    def test_full_public_file_excludes_every_realistic_draft_detail(self):
        """No public field may accidentally reintroduce words from a queued idea."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "robot.db")
            path = os.path.join(tmp, "status.json")
            title = "Draft: [ADHD Planners Agent] Travel itinerary planner"
            product_name = "Calm Trip Companion"
            prompt = "Write a private planner prompt for overwhelmed travellers"
            price = "$17.50"
            payload = f"PRODUCT NAME: {product_name}\nPROMPT: {prompt}\nPRICE: {price}"
            guardrails.request_approval("public_draft", title, payload, db_path=db)

            export_status.write_status(path, db)
            with open(path, encoding="utf-8") as status_file:
                public_json = status_file.read()

            for private_text in (title, product_name, prompt, price, "17.50"):
                self.assertNotIn(private_text, public_json)


class WaitingDraftReminderTests(unittest.TestCase):
    def test_old_drafts_remind_once_per_twelve_hours_without_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "robot.db")
            old = "2026-10-20T00:00:00"
            for number in range(3):
                conn = guardrails.connect(db)
                conn.execute("INSERT INTO approvals (ts, kind, title, payload, status) VALUES (?, ?, ?, ?, 'pending')",
                             (old, "public_draft", f"Draft {number}", "body"))
                conn.commit()
                conn.close()
            now = inbox._approval_time("2026-10-20T12:01:00")
            sent = []
            with mock.patch.object(inbox, "send_text", side_effect=lambda *args: sent.append(args[2]) or True):
                self.assertTrue(inbox.remind_waiting_drafts("fake", "owner", db, now))
                self.assertFalse(inbox.remind_waiting_drafts("fake", "owner", db, now + 60))
                self.assertTrue(inbox.remind_waiting_drafts("fake", "owner", db, now + 12 * 60 * 60))
            self.assertEqual(sent, ["3 drafts waiting - /list", "3 drafts waiting - /list"])
            self.assertEqual([item["status"] for item in guardrails.pending(db)], ["pending"] * 3)

    def test_no_reminder_before_twelve_hours_or_when_send_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "robot.db")
            guardrails.request_approval("public_draft", "Draft", "body", db_path=db)
            now = inbox._approval_time("2026-10-22T12:00:00")
            conn = guardrails.connect(db)
            conn.execute("UPDATE approvals SET ts = '2026-10-22T00:00:01'")
            conn.commit()
            conn.close()
            with mock.patch.object(inbox, "send_text", return_value=False):
                self.assertFalse(inbox.remind_waiting_drafts("fake", "owner", db, now))
            self.assertIsNone(inbox.get_state(inbox.WAITING_DRAFT_REMINDER_STATE, db_path=db))


if __name__ == "__main__":
    unittest.main()
