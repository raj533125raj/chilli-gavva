"""Unit tests for R72-JC Scout and Trend learning from rejects, history, and caps.

Covers:
- Repeat idea blocked (fuzzy title match within 30 days).
- Reject-reason-matching idea rewritten or dropped.
- Niche preference & variety cap (max 2 ideas per niche per day).
- Why this should pass note attached (generic, no money amounts).
- Daily AI cap reached = no AI call.
- Cube off = nothing / paused.
"""

import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import cubes
import guardrails
import public_export
import scout
import trend


class TestR72ScoutLearning(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        # Initialize tables
        scout.connect(self.db_path).close()
        trend.connect(self.db_path).close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_repeat_idea_blocked_fuzzy_title_match(self):
        """Never propose an idea already approved/rejected in the last 30 days (fuzzy title match)."""
        now = datetime.datetime.now(datetime.timezone.utc)
        # Seed an approved idea in approvals 10 days ago
        conn = guardrails.connect(self.db_path)
        past_date = (now - datetime.timedelta(days=10)).isoformat()
        conn.execute(
            "INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
            "VALUES (?, 'scout_idea', '[Scout Idea] Time-Block Daily Focus Planner for Remote Workers', 'payload', 'approved', ?)",
            (past_date, past_date)
        )
        # Seed a rejected idea in scout_ideas 5 days ago
        conn.execute(
            "INSERT INTO scout_ideas (ts, category, idea, total_score, status) "
            "VALUES (?, 'Printables', 'Habit and Routine Printable Worksheet for Students', 85, 'rejected')",
            (past_date,)
        )
        conn.commit()
        conn.close()

        # Run scout with fallback candidates
        ideas = scout.run_scout(db_path=self.db_path)
        proposed_titles = [i["idea"] for i in ideas]

        # Both the approved and rejected ideas should be blocked
        for p in proposed_titles:
            self.assertNotIn("Time-Block Daily Focus Planner", p)
            self.assertNotIn("Habit and Routine Printable Worksheet", p)

    def test_reject_reason_matching_idea_rewritten_or_dropped(self):
        """Before proposing an idea, score against top recent reject reasons and drop or rewrite."""
        conn = guardrails.connect(self.db_path)
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # Seed a reject reason: 'Target audience unclear'
        conn.execute(
            "INSERT INTO approvals (ts, kind, title, payload, status, review, decided_ts, reason) "
            "VALUES (?, 'public_draft', 'Old Draft', 'payload', 'rejected', 'Checker: 30/100 | issues: target audience unclear', ?, 'too generic')",
            (now_str, now_str)
        )
        conn.commit()
        conn.close()

        # Verify public_export returns reject reasons
        reasons = public_export.get_public_reject_reasons(db_path=self.db_path)
        self.assertTrue(any(r["reason"] == "Target audience unclear" for r in reasons))

        # A candidate lacking target audience should be rewritten
        candidate = {
            "category": "Templates",
            "idea": "All-in-One Inventory Spreadsheet",
            "demand_score": 20, "competition_score": 20, "fit_score": 20, "effort_score": 20,
            "total_score": 80, "reason": "Basic layout"
        }
        rewritten, dropped = scout.evaluate_against_reject_reasons(candidate, reasons)
        self.assertFalse(dropped)
        self.assertIn("Remote Creators", rewritten["idea"])

        # An unsafe / prohibited money claim candidate should be dropped completely
        unsafe_candidate = {
            "category": "Printables",
            "idea": "Guaranteed Passive Income Worksheet",
            "demand_score": 25, "competition_score": 25, "fit_score": 25, "effort_score": 25,
            "total_score": 100, "reason": "Make money fast"
        }
        _, dropped_unsafe = scout.evaluate_against_reject_reasons(unsafe_candidate, reasons)
        self.assertTrue(dropped_unsafe)

    def test_niche_cap_and_niche_preference(self):
        """Prefer niches whose past drafts were approved or built READY, keeping max 2 ideas per niche."""
        conn = guardrails.connect(self.db_path)
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # Record past approvals for niche_budget_sheets
        conn.execute(
            "INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
            "VALUES (?, 'public_draft', 'Budget Planner for niche_budget_sheets', 'niche_budget_sheets payload', 'approved', ?)",
            (now_str, now_str)
        )
        # Record READY build for niche_budget_sheets
        conn.execute(
            "CREATE TABLE IF NOT EXISTS product_builds (approval_id INTEGER, status TEXT, folder TEXT, ts TEXT)"
        )
        conn.execute(
            "INSERT INTO product_builds (approval_id, status, folder, ts) "
            "VALUES (1, 'built', '1-niche_budget_sheets', ?)",
            (now_str,)
        )
        conn.commit()
        conn.close()

        track = scout.get_niche_track_record(db_path=self.db_path)
        self.assertIn("niche_budget_sheets", track)
        self.assertGreater(track["niche_budget_sheets"], 0)

        # Candidates containing 3 ideas for niche_budget_sheets
        custom_candidates = [
            {"category": "Templates", "idea": "Budget Sheet Option 1", "niche": "niche_budget_sheets", "demand_score": 25, "competition_score": 25, "fit_score": 25, "effort_score": 25, "reason": "R1"},
            {"category": "Templates", "idea": "Budget Sheet Option 2", "niche": "niche_budget_sheets", "demand_score": 24, "competition_score": 24, "fit_score": 24, "effort_score": 24, "reason": "R2"},
            {"category": "Templates", "idea": "Budget Sheet Option 3", "niche": "niche_budget_sheets", "demand_score": 23, "competition_score": 23, "fit_score": 23, "effort_score": 23, "reason": "R3"},
            {"category": "Printables", "idea": "Student Routine Tracker", "niche": "niche_adhd_planners", "demand_score": 20, "competition_score": 20, "fit_score": 20, "effort_score": 20, "reason": "R4"},
            {"category": "Planners", "idea": "Remote Work Daily Planner", "niche": "niche_planners_2027", "demand_score": 19, "competition_score": 19, "fit_score": 19, "effort_score": 19, "reason": "R5"},
        ]

        with mock.patch("scout.get_candidate_ideas", return_value=[scout.score_candidate(c) for c in custom_candidates]):
            ideas = scout.run_scout(db_path=self.db_path)

            # Check max 2 per niche
            budget_ideas = [i for i in ideas if i["niche"] == "niche_budget_sheets"]
            self.assertLessEqual(len(budget_ideas), 2)
            self.assertEqual(len(ideas), 4)

    def test_why_this_should_pass_note_generic_no_money(self):
        """Each proposed idea carries a short 'why this should pass' note (generic, no money)."""
        ideas = scout.run_scout(db_path=self.db_path)
        self.assertGreater(len(ideas), 0)

        conn = guardrails.connect(self.db_path)
        approvals = conn.execute("SELECT payload, review FROM approvals WHERE kind = 'scout_idea'").fetchall()
        conn.close()

        for item in ideas:
            self.assertIn("why_pass", item)
            self.assertTrue(len(item["why_pass"]) > 5)
            # Ensure no currency or money symbols
            for forbidden in ("$", "USD", "EUR", "GBP", "rupee", "money", "income"):
                self.assertNotIn(forbidden, item["why_pass"].lower())

        for payload, review in approvals:
            self.assertIn("Why this should pass:", payload)
            self.assertIn("Why pass:", review)
            for forbidden in ("$", "USD", "EUR", "GBP"):
                self.assertNotIn(forbidden, payload)
                self.assertNotIn(forbidden, review)

    def test_daily_ai_cap_reached_means_no_ai_call(self):
        """Respect the daily AI cap; no new AI calls if the cap is reached."""
        conn = guardrails.connect(self.db_path)
        # Exceed daily cap in usage_log
        today = guardrails.today()
        cap = guardrails.daily_cap(self.db_path)
        conn.execute("INSERT OR REPLACE INTO usage_log (day, llm_calls) VALUES (?, ?)", (today, cap + 10))
        conn.commit()
        conn.close()

        self.assertFalse(guardrails.can_call_llm(self.db_path))

        mock_llm = mock.MagicMock(return_value="[]")

        # In scout
        candidates = scout.get_candidate_ideas(llm_fn=mock_llm, db_path=self.db_path)
        mock_llm.assert_not_called()
        self.assertEqual(len(candidates), 6)  # fallback used

        # In trend
        items = [{"title": "Tool A", "snippet": "S1"}]
        scored = trend.score_items(items, llm_fn=mock_llm, db_path=self.db_path)
        mock_llm.assert_not_called()
        self.assertEqual(len(scored), 1)

    def test_cube_off_returns_nothing(self):
        """When research cube is off, scout and trend do nothing / are paused."""
        cubes.turn_off("research", db_path=self.db_path)

        self.assertTrue(scout.is_paused(db_path=self.db_path))
        self.assertTrue(trend.is_paused(db_path=self.db_path))

        scout_res = scout.run_scout(db_path=self.db_path)
        self.assertEqual(scout_res, [])

        trend_res = trend.run_trend(db_path=self.db_path)
        self.assertEqual(trend_res, [])


if __name__ == "__main__":
    unittest.main()
