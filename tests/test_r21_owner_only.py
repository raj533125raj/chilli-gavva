import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guardrails  # noqa: E402
import telegram_inbox as inbox  # noqa: E402


class OwnerOnlyTests(unittest.TestCase):
    def test_stranger_start_approve_and_callback_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            db = os.path.join(temp, "inbox.db")
            approval_id = guardrails.request_approval(
                "public_draft", "Sample title", "Sample body", db_path=db
            )
            stranger = 999888777
            updates = [
                {"update_id": 1, "message": {"chat": {"id": stranger},
                                                  "text": "/start ap_MQ"}},
                {"update_id": 2, "message": {"chat": {"id": stranger},
                                                  "text": f"/approve {approval_id}"}},
                {"update_id": 3, "callback_query": {
                    "id": "fake-callback", "data": f"a:{approval_id}",
                    "message": {"chat": {"id": stranger}}}},
            ]
            sent = []
            with mock.patch.object(inbox, "fetch_updates", return_value=updates), \
                    mock.patch.object(inbox, "send_text", side_effect=lambda *a, **k: sent.append(a) or True), \
                    mock.patch.object(inbox, "_call", return_value={"ok": True}) as api_call:
                handled = inbox.process_updates("fake-token", "111222333", db)

            self.assertEqual(handled, 0)
            self.assertEqual(sent, [])
            api_call.assert_not_called()
            self.assertEqual(guardrails.get_approval(approval_id, db)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
