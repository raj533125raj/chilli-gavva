"""Unit tests for R66 Telegram clean behavior:
1. Burst of 3 /help -> 1 reply with '(answered 3 messages)'
2. Unknown text -> hint not menu
3. /status shape with missing data -> 'unknown' not crash
4. Daily report twice same day -> sent once
5. Outgoing message scrubbing and <= 3500 chars limit
"""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guardrails
import notify
import telegram_inbox as ti

OWNER = "111222333"


def _upd(uid, text, chat=OWNER):
    return {"update_id": uid, "message": {"chat": {"id": int(chat)}, "text": text}}


class TestR66TelegramClean(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "clean_test.db")
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

    def test_burst_of_help_deduplicated_to_one_reply(self):
        """Burst of 3 /help -> 1 reply with '(answered 3 messages)'."""
        updates = [
            _upd(1, "/help"),
            _upd(2, "/help"),
            _upd(3, "/help"),
        ]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 3)
        self.assertEqual(len(self.sent), 1)
        chat, text = self.sent[0]
        self.assertEqual(chat, OWNER)
        self.assertIn("Chilli Gavva approvals", text)
        self.assertIn("(answered 3 messages)", text)

    def test_burst_of_start_deduplicated(self):
        """Burst of /start (mapped to help) is also deduplicated with burst note."""
        updates = [
            _upd(1, "/start"),
            _upd(2, "/help"),
        ]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 2)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("(answered 2 messages)", self.sent[0][1])

    def test_unknown_text_gets_short_hint_not_full_menu(self):
        """Unknown text that is not a command gets ONE short hint line, never full help menu."""
        updates = [_upd(10, "completely random gibberish 12345")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        chat, text = self.sent[0]
        self.assertEqual(text, ti.UNKNOWN_TEXT_HINT)
        self.assertNotIn("Chilli Gavva approvals", text)
        self.assertNotIn("/approve ID", text)

    def test_unknown_text_burst_deduplicated(self):
        """Burst of unknown text messages answered once with count."""
        updates = [
            _upd(11, "some random text"),
            _upd(12, "another random message"),
        ]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 2)
        self.assertEqual(len(self.sent), 1)
        self.assertIn(ti.UNKNOWN_TEXT_HINT, self.sent[0][1])
        self.assertIn("(answered 2 messages)", self.sent[0][1])

    def test_status_shape_with_missing_data(self):
        """Status with empty or missing data shows 'unknown' without crashing."""
        missing_db = os.path.join(self.tmp.name, "nonexistent.db")
        text = ti.status_text(missing_db)

        self.assertIn("🤖 Chilli Gavva Status", text)
        self.assertIn("AI providers: unknown", text)
        self.assertIn("GitHub minutes: unknown", text)
        self.assertIn("Drafts today:", text)
        self.assertIn("Products built today:", text)
        self.assertIn("Product quality: 0 READY, 0 NEEDS FIX", text)
        self.assertIn("Pipeline:", text)
        self.assertIn("Last run: unknown", text)

    def test_status_shape_with_populated_data(self):
        """Status displays provider states, minutes, pipeline and IST timestamp."""
        conn = guardrails.connect(self.db)
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        conn.execute("CREATE TABLE IF NOT EXISTS provider_log (day TEXT, provider TEXT, ok INTEGER, fail INTEGER, PRIMARY KEY(day, provider))")
        conn.execute("INSERT INTO provider_log (day, provider, ok, fail) VALUES (?, 'Groq', 5, 0)", (today,))
        conn.execute("INSERT INTO provider_log (day, provider, ok, fail) VALUES (?, 'Gemini', 0, 2)", (today,))

        conn.execute("CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO control (key, value) VALUES ('month_minutes', '450')")
        conn.execute("INSERT INTO control (key, value) VALUES ('month_limit', '2000')")

        conn.execute("CREATE TABLE IF NOT EXISTS approvals (id INTEGER PRIMARY KEY, title TEXT, payload TEXT, status TEXT, kind TEXT, ts TEXT, decided_ts TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS truth_checks (approval_id INTEGER PRIMARY KEY, verdict TEXT, score INTEGER)")
        conn.execute("INSERT INTO approvals (id, title, payload, status, kind) VALUES (1, 'Prod 1', '{}', 'approved', 'public_draft')")
        conn.execute("INSERT INTO truth_checks (approval_id, verdict, score) VALUES (1, 'READY', 90)")
        conn.commit()
        conn.close()

        ti.set_state("last_cloud_run_ts", "2026-10-31T05:00:00Z", self.db)

        text = ti.status_text(self.db)
        self.assertIn("ok: Groq", text)
        self.assertIn("failing: Gemini", text)
        self.assertIn("GitHub minutes: 450/2000", text)
        self.assertIn("1 READY, 0 NEEDS FIX", text)
        self.assertIn("2026-10-31 10:30:00 IST", text)

    def test_status_command_via_updates(self):
        """Owner sending /status gets the compact status message."""
        updates = [_upd(20, "/status")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("🤖 Chilli Gavva Status", self.sent[0][1])

    def test_daily_report_twice_same_ist_day_sent_once(self):
        """Daily report must never be sent twice in one IST day, even across retries."""
        with mock.patch.object(ti.notify, "_telegram_configured", return_value=(True, "tok", OWNER)), \
             mock.patch.object(ti, "process_updates", return_value=0), \
             mock.patch.object(ti, "alert_new_drafts", return_value=0), \
             mock.patch.object(ti, "remind_waiting_drafts", return_value=False):

            # Simulate first run of the day
            import daily_report
            with mock.patch.object(daily_report, "maybe_send", return_value=True) as mock_daily:
                res1 = ti.run(self.db)
                self.assertTrue(res1["daily_report"])
                mock_daily.assert_called_once()

            # Simulate second run / retry on the same IST day
            with mock.patch.object(daily_report, "maybe_send", return_value=True) as mock_daily2:
                res2 = ti.run(self.db)
                self.assertFalse(res2["daily_report"])
                mock_daily2.assert_not_called()

    def test_send_text_scrubbing_and_length_limit(self):
        """Outgoing messages are scrubbed of secrets, tokens, emails, money and capped <= 3500 chars."""
        scrubbed = ti._scrub_outgoing("Hello key=secret123 token=abc Bearer sensitive_token test@example.com $50 USD")
        self.assertNotIn("secret123", scrubbed)
        self.assertNotIn("sensitive_token", scrubbed)
        self.assertNotIn("test@example.com", scrubbed)
        self.assertNotIn("$50", scrubbed)

        long_scrubbed = ti._scrub_outgoing("A" * 6000)
        self.assertLessEqual(len(long_scrubbed), 3500)
        self.assertIn("message length limit", long_scrubbed)

    def test_notify_send_scrubbing_and_length_limit(self):
        """notify.py send() is also scrubbed and capped <= 3500 chars."""
        captured = {}

        def fake_urlopen(req, timeout=10):
            import json
            from unittest.mock import MagicMock
            captured["data"] = json.loads(req.data.decode("utf-8"))
            resp = MagicMock()
            resp.read.return_value = json.dumps({"ok": True}).encode("utf-8")
            return resp

        with mock.patch.object(notify, "_telegram_configured", return_value=(True, "tok", OWNER)), \
             mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            notify.send("Notice: Bearer token123 key=abc user@domain.com " + "Z" * 5000)

        sent_text = captured["data"]["text"]
        self.assertNotIn("token123", sent_text)
        self.assertNotIn("user@domain.com", sent_text)
        self.assertLessEqual(len(sent_text), 3500)


if __name__ == "__main__":
    unittest.main()
