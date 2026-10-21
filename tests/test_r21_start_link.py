import base64
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guardrails  # noqa: E402
import telegram_inbox as inbox  # noqa: E402


class StartLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.temp.name, "test.db")
        self.owner = "111222333"
        self.approval_id = guardrails.request_approval(
            "public_draft", "Sample draft", "Sample body", db_path=self.db
        )
        self.sent = []

    def tearDown(self):
        self.temp.cleanup()

    def _payload(self, approval_id):
        encoded = base64.urlsafe_b64encode(str(approval_id).encode()).decode().rstrip("=")
        return "ap_" + encoded

    def test_start_payload_opens_the_same_draft_view_with_buttons(self):
        payload = self._payload(self.approval_id)
        self.assertEqual(inbox.parse_start_payload("/start " + payload), str(self.approval_id))
        update = {"update_id": 1, "message": {"chat": {"id": int(self.owner)},
                                                   "text": "/start " + payload}}

        def fake_send(token, chat, text, buttons=None):
            self.sent.append((chat, text, buttons))
            return True

        with mock.patch.object(inbox, "fetch_updates", return_value=[update]), \
                mock.patch.object(inbox, "send_text", side_effect=fake_send):
            handled = inbox.process_updates("fake-token", self.owner, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(self.sent, [(self.owner, inbox.view_text(self.approval_id, self.db),
                                      inbox.draft_buttons(str(self.approval_id)))])

    def test_opening_start_link_does_not_auto_approve(self):
        update = {"update_id": 2, "message": {"chat": {"id": int(self.owner)},
                                                   "text": "/start " + self._payload(self.approval_id)}}
        with mock.patch.object(inbox, "fetch_updates", return_value=[update]), \
                mock.patch.object(inbox, "send_text", return_value=True):
            inbox.process_updates("fake-token", self.owner, self.db)
        self.assertEqual(guardrails.get_approval(self.approval_id, self.db)["status"], "pending")

    def test_unknown_start_id_has_not_found_reply(self):
        update = {"update_id": 1, "message": {"chat": {"id": int(self.owner)},
                                                   "text": "/start " + self._payload("987654")}}
        with mock.patch.object(inbox, "fetch_updates", return_value=[update]), \
                mock.patch.object(inbox, "send_text", side_effect=lambda *args, **kwargs:
                                  self.sent.append((args, kwargs)) or True):
            self.assertEqual(inbox.process_updates("fake-token", self.owner, self.db), 1)
        self.assertIn("Draft #987654 not found.", self.sent[0][0][2])

    def test_plain_start_keeps_normal_help(self):
        update = {"update_id": 1, "message": {"chat": {"id": int(self.owner)}, "text": "/start"}}
        with mock.patch.object(inbox, "fetch_updates", return_value=[update]), \
                mock.patch.object(inbox, "send_text", side_effect=lambda *args, **kwargs:
                                  self.sent.append(args[2]) or True):
            inbox.process_updates("fake-token", self.owner, self.db)
        self.assertEqual(self.sent, [inbox.HELP])


if __name__ == "__main__":
    unittest.main()
