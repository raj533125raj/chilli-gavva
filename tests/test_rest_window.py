"""Tests for the daily rest window (6:00-6:06 AM and PM IST)."""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guardrails  # noqa: E402
import rest_window as rw  # noqa: E402

D = datetime.datetime


def at(h, m):
    return D(2026, 10, 16, h, m)


class WindowTests(unittest.TestCase):
    def test_the_two_windows_in_utc(self):
        for t in (at(0, 30), at(0, 35), at(12, 30), at(12, 35)):
            self.assertTrue(rw.in_rest_window(t), t)
        for t in (at(0, 29), at(0, 36), at(12, 29), at(12, 36), at(6, 0), at(18, 0)):
            self.assertFalse(rw.in_rest_window(t), t)
        self.assertEqual(sum(rw.in_rest_window(at(h, m)) for h in range(24) for m in range(60)), 12)

    def test_ist_times(self):
        ist = datetime.timedelta(hours=5, minutes=30)
        self.assertTrue(rw.in_rest_window(D(2026, 10, 16, 6, 0) - ist))
        self.assertTrue(rw.in_rest_window(D(2026, 10, 16, 18, 5) - ist))
        self.assertFalse(rw.in_rest_window(D(2026, 10, 16, 6, 6) - ist))

    def test_night_slot_never_rests(self):
        """The night niche slot (hour 21, +/-1 hour) and any door bell time there are never paused."""
        for h in (20, 21, 22):
            for m in range(60):
                self.assertFalse(rw.in_rest_window(at(h, m)))

    def test_tests_ignore_the_real_clock(self):
        with mock.patch.object(guardrails, "_TESTING", True), mock.patch.object(rw, "_utc_now", return_value=at(0, 31)):
            self.assertFalse(rw.in_rest_window())
        with mock.patch.object(guardrails, "_TESTING", False), mock.patch.object(rw, "_utc_now", return_value=at(0, 31)):
            self.assertTrue(rw.in_rest_window())


class RestBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "r.db")
        p = mock.patch.object(rw, "in_rest_window", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_no_ai_work_no_drafts_no_recipes(self):
        import scheduler
        import workforce
        workforce.enqueue("research", {"topic": "x"}, db_path=self.db)
        called = []
        result = workforce.run_once(db_path=self.db, llm=lambda p, size=None: called.append(p) or "x")
        self.assertEqual(result["status"], "rest")
        self.assertEqual(called, [])
        self.assertEqual(guardrails.pending(self.db), [])
        self.assertTrue(scheduler.run_now(db_path=self.db).get("skipped"))

    def test_telegram_still_answers(self):
        import telegram_inbox as ti
        sent = []
        with mock.patch.object(ti, "fetch_updates", return_value=[{"update_id": 1, "message": {
                "chat": {"id": 42}, "text": "/list"}}]), \
                mock.patch.object(ti, "send_text", side_effect=lambda *a, **k: sent.append(a) or True):
            self.assertEqual(ti.process_updates("tok", "42", self.db), 1)
        self.assertTrue(sent)

    def test_cloud_runner_does_rest_tasks_only(self):
        import cloud_runner
        out = []
        with mock.patch.object(cloud_runner.workforce, "run_once", side_effect=AssertionError("no AI work")), \
                mock.patch.object(cloud_runner.export_status, "write_status", lambda: None), \
                mock.patch.object(cloud_runner.export_status, "purge_simulated", lambda: {}), \
                mock.patch.object(rw, "rest_tasks", return_value="Rest window: ok"), \
                mock.patch("builtins.print", side_effect=out.append):
            self.assertEqual(cloud_runner.main()["status"], "rest")
        self.assertIn("Rest window: ok", out)


class RestTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "r.db")
        self.locks = os.path.join(self.tmp.name, "locks")
        os.makedirs(self.locks)
        for name in ("2026-09-01_night.lock", "2026-10-15_night.lock", "notes.txt"):
            open(os.path.join(self.locks, name), "w").close()

    def test_cleanup_removes_only_old_things(self):
        conn = guardrails.connect(self.db)
        conn.execute("CREATE TABLE provider_log (day TEXT, provider TEXT, ok INTEGER, fail INTEGER)")
        conn.execute("INSERT INTO provider_log VALUES ('2026-07-01', 'Groq', 1, 0), ('2026-10-15', 'Groq', 1, 0)")
        conn.commit()
        conn.close()
        removed = rw.cleanup(at(0, 31), self.locks, self.db)
        self.assertEqual(removed, {"lock files": 1, "log rows": 1})
        self.assertEqual(sorted(os.listdir(self.locks)), ["2026-10-15_night.lock", "notes.txt"])

    def test_rest_tasks_summary_has_no_secrets(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_" + "FAKEFAKEFAKEFAKEFAKE12", "GITHUB_ACTIONS": "true"},
                             clear=True):
            line = rw.rest_tasks(at(0, 31), self.db, probe=lambda *a: (True, "", 0.1), lock_dir=self.locks)
        self.assertIn("no AI work, no drafts", line)
        self.assertIn("Groq OK", line)
        self.assertIn("1 old lock file(s)", line)
        self.assertIn("tests: passed", line)
        self.assertNotIn("FAKEFAKE", line)

    def test_rest_tasks_never_crash(self):
        with mock.patch.object(rw, "cleanup", side_effect=OSError("disk")), \
                mock.patch.object(rw, "ai_reachable", side_effect=RuntimeError("net")):
            line = rw.rest_tasks(at(0, 31), self.db)
        self.assertIn("cleanup failed: OSError", line)
        self.assertIn("AI check failed: RuntimeError", line)


if __name__ == "__main__":
    unittest.main()
