import datetime
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guardrails  # noqa: E402
import maintenance  # noqa: E402
import record_earning  # noqa: E402
import workforce  # noqa: E402


def days_ago(n):
    return (datetime.datetime.now() - datetime.timedelta(days=n)).isoformat()


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "m.db")
        self.bk = os.path.join(self.tmp.name, "backups")
        conn = workforce.connect(self.db)
        conn.execute("CREATE TABLE agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, agent_name TEXT, "
                     "department TEXT, status TEXT, message TEXT)")
        for i in range(300):
            conn.execute("INSERT INTO agent_logs (timestamp, agent_name, department, status, message) VALUES (?, 'a', 'd', 's', 'm')",
                         (days_ago(60),))
        conn.execute("INSERT INTO tasks (ts, kind, payload, status, finished_ts) VALUES (?, 'plan', '{}', 'done', ?)",
                     (days_ago(60), days_ago(60)))
        conn.execute("INSERT INTO tasks (ts, kind, payload, status, finished_ts) VALUES (?, 'plan', '{}', 'done', ?)",
                     (days_ago(1), days_ago(1)))
        conn.execute("INSERT INTO tasks (ts, kind, payload, status) VALUES (?, 'plan', '{}', 'queued')", (days_ago(90),))
        conn.execute("INSERT INTO usage_log (day, llm_calls) VALUES ('2020-01-01', 5)")
        conn.commit(); conn.close()
        guardrails.request_approval("public_draft", "old pending", "x", db_path=self.db)
        record_earning.add(7, "INV-OLD", "keep me", db_path=self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def count(self, table, where="1=1"):
        conn = sqlite3.connect(self.db)
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
        conn.close()
        return n

    def test_dry_run_changes_nothing(self):
        report = maintenance.run(self.db, self.bk, dry_run=True)
        self.assertEqual(report["pruned"]["agent_logs"], 100)
        self.assertEqual(self.count("agent_logs"), 300)
        self.assertFalse(os.path.exists(self.bk))

    def test_prune_removes_only_old_finished_data(self):
        report = maintenance.run(self.db, self.bk)
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(self.count("agent_logs"), maintenance.MIN_LOGS_KEPT)   # newest 200 survive
        self.assertEqual(self.count("tasks"), 2)                                # old finished one gone
        self.assertEqual(self.count("tasks", "status = 'queued'"), 1)           # unfinished kept even if old
        self.assertEqual(self.count("usage_log"), 0)
        self.assertEqual(self.count("approvals", "status = 'pending'"), 1)      # never pruned
        self.assertEqual(self.count("campaigns", "source = 'real'"), 1)         # real money never pruned
        self.assertEqual(self.count("earnings_proof"), 1)

    def test_backup_is_valid_and_rotated(self):
        names = []
        for i in range(maintenance.KEEP_BACKUPS + 3):
            target, _ = maintenance.backup(self.db, self.bk)
            os.rename(target, target.replace(".db", f"-{i:02d}.db"))             # unique names within one second
            names.append(target)
        left = os.listdir(self.bk)
        self.assertEqual(len(left), maintenance.KEEP_BACKUPS)
        conn = sqlite3.connect(os.path.join(self.bk, sorted(left)[-1]))
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM earnings_proof").fetchone()[0], 1)
        conn.close()

    def test_corrupt_database_is_not_touched(self):
        bad = os.path.join(self.tmp.name, "bad.db")
        with open(bad, "wb") as f:
            f.write(b"this is not a sqlite database at all" * 50)
        try:
            report = maintenance.run(bad, self.bk)
        except sqlite3.DatabaseError:
            report = {"stopped": "raised"}
        self.assertIn("stopped", report)
        self.assertFalse(os.path.exists(self.bk))

    def test_run_if_due_runs_once_per_period(self):
        self.assertIsNotNone(maintenance.run_if_due(self.db, self.bk))
        self.assertIsNone(maintenance.run_if_due(self.db, self.bk))


if __name__ == "__main__":
    unittest.main()
