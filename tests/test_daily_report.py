"""Tests for daily_report.py and the provider tally in llm_router (offline, temp databases)."""
import datetime
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daily_report as dr  # noqa: E402
import guardrails  # noqa: E402
import llm_router  # noqa: E402
import product_builder  # noqa: E402
import record_earning  # noqa: E402
import telegram_inbox as ti  # noqa: E402
import workforce  # noqa: E402

MONDAY = datetime.datetime(2026, 10, 12, 4, 0)    # after 03:30 UTC
SUNDAY = datetime.datetime(2026, 10, 18, 4, 0)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "r.db")
        self.addCleanup(self.tmp.cleanup)

    def approval(self, status="pending", reason=None, ts="2026-10-12T01:00:00", decided="2026-10-12T02:00:00"):
        conn = guardrails.connect(self.db)
        conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts, reason) "
                     "VALUES (?, 'public_draft', 't', 'p', ?, ?, ?)",
                     (ts, status, decided if status != "pending" else None, reason))
        conn.commit()
        conn.close()


class DailyTests(Base):
    def test_daily_numbers(self):
        self.approval()
        self.approval("approved")
        self.approval("pending", ts="2026-10-01T00:00:00")          # older than a day: waiting, not "made"
        product_builder._record(self.db, 2, "built", folder="2-x")
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE product_builds SET ts = '2026-10-12T03:00:00'")
        conn.commit()
        conn.close()
        workforce.enqueue("research", {"topic": "x"}, db_path=self.db)
        workforce.fail(1, 1, "research: no answer from any AI model", self.db)
        llm_router.log_provider("Gemini", True, self.db)
        llm_router.log_provider("Groq", False, self.db)
        today = datetime.datetime.combine(datetime.datetime.now(datetime.timezone.utc).date(), datetime.time(4, 0))
        text = dr.daily_text(self.db, now=today, status_fn=lambda: {"month_minutes": 412, "month_runs": 80})
        self.assertIn("Gemini OK (1 ok, 0 failed)", text)
        self.assertIn("Groq FAIL (0 ok, 1 failed)", text)
        self.assertIn("GitHub minutes this month: 412 of 2000", text)
        self.assertIn("1 task(s) waiting to retry", text)
        text = dr.daily_text(self.db, now=MONDAY, status_fn=lambda: None)
        self.assertIn("Drafts: 2 made, 2 waiting, 1 approved", text)
        self.assertIn("Products built: 1", text)
        self.assertIn("unknown (alarm clock not reachable)", text)
        self.assertLess(len(text), 800)

    def test_empty_database(self):
        text = dr.report_text(self.db, now=MONDAY, status_fn=lambda: {})
        self.assertIn("AI: not used in the last day", text)
        self.assertIn("Errors: none", text)
        self.assertNotIn("Sunday", text)

    def test_paused_is_shown(self):
        guardrails.set_paused(True, self.db)
        self.assertIn("PAUSED", dr.daily_text(self.db, now=MONDAY, status_fn=lambda: {}))


class WeeklyTests(Base):
    def test_sunday_letter(self):
        self.approval("rejected", reason="price", decided="2026-10-16T10:00:00")
        self.approval("rejected", reason="price", decided="2026-10-16T11:00:00")
        self.approval("rejected", reason="bad niche", decided="2026-10-17T11:00:00")
        self.approval("approved", decided="2026-10-17T12:00:00")
        record_earning.add(4.5, "PAYOUT-7", "sale", db_path=self.db)
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE campaigns SET timestamp = '2026-10-17T09:00:00'")
        conn.execute("INSERT INTO campaigns (timestamp, niche, content_title, clicks, earnings, status, source) "
                     "VALUES ('2026-10-17', 'n', 'fake', 1, 99, 'COMPLETED', 'simulated')")
        conn.commit()
        conn.close()
        text = dr.report_text(self.db, now=SUNDAY, status_fn=lambda: {})
        self.assertIn("Sunday letter", text)
        self.assertIn("Approved: 1. Rejected: 3 (top reasons: price (2), bad niche (1))", text)
        self.assertIn("Real money: $4.50 this week, $4.50 in total", text)   # simulated rows never count

    def test_zero_money_is_fine(self):
        self.assertIn("Real money: $0.00 this week, $0.00 in total", dr.weekly_text(self.db, now=SUNDAY))


class DoorBellAndButtonLineTests(Base):
    def test_door_bell_line(self):
        self.assertEqual(dr.door_bell_line({"door_bell": True, "telegram_token_ok": "yes"}), "Door bell: on")
        self.assertEqual(dr.door_bell_line({"door_bell": False, "telegram_token_ok": "not set"}), "Door bell: off")
        self.assertIn("does NOT accept", dr.door_bell_line({"door_bell": True, "telegram_token_ok": "no"}))
        self.assertIn("unknown", dr.door_bell_line({}))

    def test_report_shows_door_bell_and_button_count(self):
        import time
        ti.log_button(self.db)
        ti.log_button(self.db)
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO button_log (ts) VALUES ('2000-01-01T00:00:00')")   # too old to count
        conn.commit()
        conn.close()
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(minutes=1)
        text = dr.daily_text(self.db, now=now, status_fn=lambda: {"door_bell": True, "telegram_token_ok": "yes",
                                                                   "month_minutes": 3, "month_runs": 2})
        self.assertIn("Door bell: on", text)
        self.assertIn("Button presses received in the last 24h: 2", text)
        self.assertIn("GitHub minutes this month: 3 of 2000", text)

    def test_owner_button_press_is_counted_time_only(self):
        a = guardrails.request_approval("public_draft", "Draft x", "body", db_path=self.db)
        press = {"update_id": 1, "callback_query": {"id": "q", "data": f"v:{a}", "from": {"id": 1},
                                                    "message": {"chat": {"id": 42}}}}
        stranger = {"update_id": 2, "callback_query": {"id": "q", "data": f"v:{a}", "from": {"id": 9},
                                                       "message": {"chat": {"id": 9}}}}
        with mock.patch.object(ti, "fetch_updates", return_value=[press, stranger]), \
                mock.patch.object(ti, "_call", return_value={"ok": True}), \
                mock.patch.object(ti, "send_text", return_value=True):
            ti.process_updates("tok", "42", self.db)
        conn = sqlite3.connect(self.db)
        self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(button_log)")], ["ts"])   # time only
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM button_log").fetchone()[0], 1)
        conn.close()
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "pending")

    def test_empty_report_has_zero_buttons(self):
        self.assertIn("Button presses received in the last 24h: 0", dr.daily_text(self.db, now=MONDAY,
                                                                                    status_fn=lambda: {}))


class OncePerDayTests(Base):
    def run_send(self, now, ok=True):
        sent = []

        def send(text):
            sent.append(text)
            return ok
        result = dr.maybe_send(send, ti.get_state, ti.set_state, self.db, now=now, status_fn=lambda: {})
        return result, sent

    def test_once_per_day_after_0330(self):
        self.assertEqual(self.run_send(datetime.datetime(2026, 10, 12, 3, 0)), (False, []))   # too early
        sent, msgs = self.run_send(datetime.datetime(2026, 10, 12, 3, 31))
        self.assertTrue(sent)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(self.run_send(datetime.datetime(2026, 10, 12, 9, 0)), (False, []))   # already sent
        self.assertTrue(self.run_send(datetime.datetime(2026, 10, 13, 3, 45))[0])            # next day

    def test_retry_when_telegram_fails(self):
        self.assertFalse(self.run_send(MONDAY, ok=False)[0])
        self.assertTrue(self.run_send(MONDAY + datetime.timedelta(minutes=30))[0])

    def test_inbox_run_sends_report_to_owner_only(self):
        calls = []
        with mock.patch.object(ti.notify, "_telegram_configured", return_value=(True, "tok", "42")), \
                mock.patch.object(ti, "fetch_updates", return_value=[]), \
                mock.patch.object(ti, "_call", side_effect=AssertionError("no network in tests")), \
                mock.patch.object(ti, "send_text", side_effect=lambda tok, chat, text, buttons=None:
                                  calls.append(chat) or True), \
                mock.patch.object(dr, "_now", return_value=MONDAY), \
                mock.patch.object(dr, "_minutes", return_value="GitHub minutes this month: 1 of 2000 free"):
            result = ti.run(self.db)
        self.assertTrue(result["daily_report"])
        self.assertEqual(calls, ["42"])


class ProviderLogTests(Base):
    def test_tally_and_tests_never_touch_real_db(self):
        llm_router.log_provider("Gemini", True, self.db)
        llm_router.log_provider("Gemini", True, self.db)
        llm_router.log_provider("Gemini", False, self.db)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT ok, fail FROM provider_log").fetchone(), (2, 1))
        conn.close()
        with mock.patch.object(guardrails, "_TESTING", True), \
                mock.patch("sqlite3.connect", side_effect=AssertionError("no real db in tests")):
            llm_router.log_provider("Gemini", True)


if __name__ == "__main__":
    unittest.main()
