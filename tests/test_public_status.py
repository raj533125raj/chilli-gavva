"""The public status file: only the safe fields, UTC time, and no private values."""
import json
import os
import sqlite3
import tempfile
import unittest

import export_status
import guardrails
import workforce

ALLOWED_TOP = {"generated_at", "kill_switch", "llm", "agents", "task_counts", "recent_tasks", "pending_approvals", "worker"}


class PublicStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        self._kill = guardrails.KILL_FILE
        guardrails.KILL_FILE = os.path.join(self.tmp.name, "KILL")
        self._wf = workforce.CONFIG_PATH
        workforce.CONFIG_PATH = os.path.join(self.tmp.name, "wf_config.json")
        with open(workforce.CONFIG_PATH, "w") as f:
            f.write('{"daily_drafts": 1}')
        workforce.enqueue("plan", {"topic": "demo"}, db_path=self.db)
        workforce.run_once(max_tasks=10, db_path=self.db, llm=lambda p, size=None: "PRODUCT NAME: Demo\nPRICE: 5 USD")
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO approvals (ts, kind, title, review, status) VALUES (?, ?, ?, ?, 'pending')",
                     ("2026-10-21T03:00:00", "post", "Post a draft", "SECRET REVIEW TEXT gsk_example"))
        conn.commit()
        conn.close()

    def tearDown(self):
        guardrails.KILL_FILE = self._kill
        workforce.CONFIG_PATH = self._wf
        self.tmp.cleanup()

    def test_only_the_safe_top_level_fields(self):
        self.assertEqual(set(export_status.build_status(self.db)), ALLOWED_TOP)

    def test_generated_at_is_utc_with_z(self):
        stamp = export_status.build_status(self.db)["generated_at"]
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_agents_have_only_the_safe_keys(self):
        for agent in export_status.build_status(self.db)["agents"]:
            self.assertEqual(set(agent), {"name", "level", "role", "kinds", "state", "tasks_done"})

    def test_recent_tasks_and_approvals_have_only_the_safe_keys(self):
        status = export_status.build_status(self.db)
        for task in status["recent_tasks"]:
            self.assertEqual(set(task), {"id", "kind", "status", "agent", "attempts"})
        self.assertGreaterEqual(len(status["pending_approvals"]), 1)
        for approval in status["pending_approvals"]:
            self.assertEqual(set(approval), {"id", "ts", "kind", "title"})

    def test_approval_review_text_never_appears(self):
        blob = json.dumps(export_status.build_status(self.db))
        self.assertNotIn("SECRET REVIEW TEXT", blob)
        self.assertNotIn("gsk_example", blob)

    def test_no_money_prompts_logs_or_validation(self):
        blob = json.dumps(export_status.build_status(self.db))
        for key in ('"money"', '"prompts"', '"logs"', '"validation"', '"service_requests"', '"built_tools"', '"llm_providers"', '"info"'):
            self.assertNotIn(key, blob)

    def test_private_snapshot_keeps_the_local_fields(self):
        full = export_status.build_status_private(self.db)
        self.assertIn("money", full)
        self.assertIn("prompts", full)


if __name__ == "__main__":
    unittest.main()
