"""End-to-end integration test simulating a full day cycle through cloud_runner.main()."""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import cloud_runner
import cubes
import export_status
import guardrails
import marketing_agency
import product_builder
import product_council
import scout
import trend
import workforce


def fake_llm_response(prompt, size=None, **kwargs):
    prompt_lower = (prompt or "").lower()

    if "product council critic" in prompt_lower:
        return json.dumps({"weaknesses": [], "verdict": "OK"})
    if "buyer's voice" in prompt_lower:
        return json.dumps({"answer": "Provides automated layout and daily focus dashboard", "conviction": 8})
    if "judge agent" in prompt_lower:
        return json.dumps({"verdict": "PASS", "reasons": ["Clear layout and buyer value"], "top_fix": "Refined structure"})
    if "qa checker" in prompt_lower or "check this draft" in prompt_lower:
        return json.dumps({"score": 88, "verdict": "pass", "issues": []})
    if "scout" in prompt_lower:
        return json.dumps([
            {"category": "Planners", "idea": "Daily Focus Planner Sheet", "demand_score": 22,
             "competition_score": 20, "fit_score": 24, "effort_score": 22, "reason": "High demand among creators"}
        ] * 8)
    if "trend" in prompt_lower:
        return json.dumps([{"title": "Open Source Multi-Agent Framework", "score": 85, "why": "Useful for workspace automation"}])
    if "pinterest" in prompt_lower or "instagram" in prompt_lower or "shorts" in prompt_lower or "seo" in prompt_lower:
        return "Pin 1 Title: Digital Focus Planner\nPin 1 Description: Clean daily focus planner layout for creators.\nKeywords: planner, digital download\nBoard Name: Digital Planners"

    # Default product draft response with required listing sections
    return (
        "PRODUCT NAME: Daily Freelancer Focus Sheet\n"
        "BUYER: Freelancers and remote creators needing daily focus\n"
        "PRICE: 7 USD\n"
        "PLATFORM: Gumroad\n"
        "WHAT'S INSIDE: Tab 1 Daily Tasks (Columns: Task, Priority, Time, Status), Tab 2 Weekly Goals\n"
        "HOW TO USE: Step 1 open template, Step 2 enter daily tasks, Step 3 track progress\n"
        "LISTING TITLE: Daily Freelancer Focus Sheet Digital Download\n"
        "LISTING DESCRIPTION: Streamline your daily workflow with this clean digital planner layout.\n"
        "SEARCH TAGS: freelancer, planner, printable, template, organization"
    )


class E2ESimulatedDayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "simulated_day.db")
        self.status_path = os.path.join(self.tmp.name, "web", "data", "status.json")

        self.orig_db = guardrails.DB_PATH
        self.orig_status_path = export_status.STATUS_PATH
        guardrails.DB_PATH = self.db_path
        export_status.STATUS_PATH = self.status_path

        workforce.connect(self.db_path)
        product_builder.connect(self.db_path)

    def tearDown(self):
        guardrails.DB_PATH = self.orig_db
        export_status.STATUS_PATH = self.orig_status_path

    @mock.patch("rest_window.in_rest_window", return_value=False)
    @mock.patch("guardrails.is_killed", return_value=False)
    @mock.patch("llm_router.chat", side_effect=fake_llm_response)
    def test_full_simulated_day_e2e(self, mock_chat, mock_killed, mock_rest):
        # 1. Enqueue initial plan and research tasks
        workforce.enqueue("plan", {"topic": "a digital focus sheet for freelancers"}, db_path=self.db_path)

        # 2. Run cloud_runner.main() — executes workforce tasks, scout, trend, marketing, and exports status
        res1 = cloud_runner.main()
        self.assertEqual(res1["status"], "ok")
        self.assertGreaterEqual(res1["ran"], 1)

        # 3. Process remaining queue (research -> draft -> council -> review)
        for _ in range(3):
            workforce.run_once(max_tasks=5, db_path=self.db_path, llm=fake_llm_response)

        # Confirm approvals were generated
        pending = guardrails.pending(db_path=self.db_path)
        self.assertGreaterEqual(len(pending), 1)

        # Approve draft so product_builder & marketing_agency can process it
        approval_id = pending[0]["id"]
        guardrails.decide(approval_id, "approved", reason="E2E test", db_path=self.db_path)

        conn = guardrails.connect(self.db_path)
        conn.execute(
            "INSERT INTO product_builds (approval_id, status, folder) VALUES (?, 'built', 'niche_budget_sheets')",
            (approval_id,)
        )
        conn.commit()
        conn.close()

        # 4. Run scout, trend, marketing agency, and doctor checks
        scout_counts = scout.get_scout_ideas_count(db_path=self.db_path)
        self.assertGreaterEqual(scout_counts["total"], 1)

        trend_counts = trend.get_trend_items_count(db_path=self.db_path)
        self.assertGreaterEqual(trend_counts["total"], 1)

        mkt_kits = marketing_agency.process_ready_products(db_path=self.db_path, llm_fn=fake_llm_response)
        self.assertGreaterEqual(len(mkt_kits), 1)

        # 5. Export status and validate shape
        export_status.write_status(path=self.status_path, db_path=self.db_path)
        self.assertTrue(os.path.exists(self.status_path))

        with open(self.status_path, encoding="utf-8") as f:
            status = json.load(f)

        expected_keys = {
            "generated_at", "kill_switch", "llm", "agents", "task_counts", "recent_tasks",
            "pending_approvals", "worker", "handoffs", "ai_health", "minutes",
            "scout_ideas_count", "trend_items_count", "media_items_count", "cubes", "marketing_kits_count",
            "products", "reject_reasons", "report", "workers", "pipeline",
            "agent_budgets", "loop_events", "activity_events", "schedules", "receipts",
            "workspaces", "cloud_heartbeat", "changelog"
        }
        self.assertEqual(set(status.keys()), expected_keys)

        # Validate recent_tasks contains ts and finished_ts
        for task in status["recent_tasks"]:
            self.assertIn("ts", task)
            self.assertIn("finished_ts", task)

        # Validate cubes structure
        self.assertIsInstance(status["cubes"], list)
        self.assertGreaterEqual(len(status["cubes"]), 1)
        cube_ids = [c["id"] for c in status["cubes"]]
        self.assertIn("research", cube_ids)
        self.assertIn("marketing", cube_ids)

        # 6. Verify Cube Off really stops its agents
        cubes.turn_off("research", db_path=self.db_path)
        self.assertFalse(cubes.is_kind_enabled("research", db_path=self.db_path))

        # Attempt to run scout while research cube is OFF
        scout_paused = scout.maybe_run(db_path=self.db_path, llm=fake_llm_response)
        self.assertFalse(scout_paused.get("ran"))
        self.assertEqual(scout_paused.get("reason"), "paused")

        # Attempt to claim a research task while research cube is OFF
        workforce.enqueue("research", {"topic": "off test"}, db_path=self.db_path)
        claimed = workforce.claim_next(db_path=self.db_path)
        self.assertIsNone(claimed)

        # Turn Research cube back ON
        cubes.turn_on("research", db_path=self.db_path)
        self.assertTrue(cubes.is_kind_enabled("research", db_path=self.db_path))
        claimed_on = workforce.claim_next(db_path=self.db_path)
        self.assertIsNotNone(claimed_on)


if __name__ == "__main__":
    unittest.main()
