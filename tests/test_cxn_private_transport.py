"""Only explicitly marked private text can retain amounts on Telegram."""
import unittest
from unittest import mock
import telegram_inbox
import public_export
from owner_private import OwnerPrivateText

class PrivateTransportTests(unittest.TestCase):
    def test_private_amount_and_secrets_have_independent_policies(self):
        amount = "USD " + str(12)
        probe_value = "private-" + "test-token"
        with mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": probe_value}), mock.patch.object(telegram_inbox, "_call", return_value={"ok": True}) as api:
            self.assertTrue(telegram_inbox.send_text("token", "owner", OwnerPrivateText(amount + " " + probe_value)))
        delivered = api.call_args.args[2]["text"]
        self.assertIn(amount, delivered)
        self.assertNotIn(probe_value, delivered)
        self.assertNotIn(amount, public_export.scrub_text(OwnerPrivateText(amount)))

    def test_plain_reply_cannot_opt_in_by_its_words(self):
        amount = "USD " + str(12)
        self.assertNotIn(amount, telegram_inbox._scrub_outgoing("Private sales report: " + amount))

    def test_private_scope_survives_burst_note(self):
        value = OwnerPrivateText("USD " + str(12)) + "\nOne more update"
        self.assertIsInstance(value, OwnerPrivateText)
        self.assertIn("USD " + str(12), telegram_inbox._scrub_outgoing(value))
