import datetime
import os
import tempfile
import unittest
from unittest import mock

import cubes
import doctor
import guardrails
import llm_router
import telegram_inbox
import workforce


class TestThroughputTask(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.db = self.tmp.name
        self.tmp.close()
        workforce.connect(self.db)

    def tearDown(self):
        if os.path.exists(self.db):
            os.remove(self.db)

    def test_daily_pipeline_plan_quota_adaptation(self):
        # 1. Normal healthy state
        plan = workforce.get_daily_pipeline_plan(self.db)
        self.assertEqual(plan["health_mult"], 1.0)
        self.assertIn("executive", plan["cube_plans"])

        # 2. When Groq is in cooldown
        llm_router.set_cooldown("Groq", minutes=30, db_path=self.db)
        plan_groq_down = workforce.get_daily_pipeline_plan(self.db)
        self.assertEqual(plan_groq_down["health_mult"], 0.6)

        # 3. When both Groq and Gemini are in cooldown
        llm_router.set_cooldown("Gemini", minutes=30, db_path=self.db)
        plan_both_down = workforce.get_daily_pipeline_plan(self.db)
        self.assertEqual(plan_both_down["health_mult"], 0.25)

    def test_time_budget_stop(self):
        # Enqueue several tasks
        for _ in range(10):
            workforce.enqueue("research", {"topic": "test topic"}, db_path=self.db)

        # Run with max_time_seconds = 0 so time budget is immediately exceeded after 0 tasks
        res = workforce.run_once(max_tasks=10, db_path=self.db, llm=lambda p: "OK", max_time_seconds=0)
        self.assertEqual(res["ran"], 0)

    def test_disabled_cube_task_skipping(self):
        # Disable executive cube (kind 'plan')
        cubes.turn_off("executive", db_path=self.db)

        task_id = workforce.enqueue("plan", {"topic": "executive plan"}, db_path=self.db)
        claimed = workforce.claim_next(self.db)
        self.assertIsNone(claimed)

    def test_pipeline_command_text(self):
        # Test /pipeline command output
        txt = telegram_inbox.pipeline_text(db_path=self.db)
        self.assertIn("Product Pipeline Status", txt)
        self.assertIn("Today's Plan vs Done", txt)
        self.assertIn("Current Backlog", txt)
        self.assertIn("Stuck Items", txt)

        # Handle stuck item scenario
        # Insert a task that looks stuck (attempts >= 2, status queued)
        conn = workforce.connect(self.db)
        conn.execute(
            "INSERT INTO tasks (ts, kind, status, attempts, agent) VALUES (?, 'research', 'queued', 2, 'Research Worker')",
            (datetime.datetime.now().isoformat(),)
        )
        conn.commit()
        conn.close()

        txt_stuck = telegram_inbox.pipeline_text(db_path=self.db)
        self.assertIn("Stuck Items:", txt_stuck)


if __name__ == "__main__":
    unittest.main()
