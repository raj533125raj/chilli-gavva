"""Unit tests for scout.py (SCOUT agent)."""
import datetime
import json
import os
import sqlite3
import tempfile
import unittest

import export_status
import guardrails
import niche_topics
import scout


class TestScout(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_rule_based_fallback(self):
        candidates = scout.get_candidate_ideas(llm_fn=None)
        self.assertEqual(len(candidates), 6)
        for c in candidates:
            self.assertIn("category", c)
            self.assertIn("idea", c)
            self.assertIn("total_score", c)
            self.assertGreater(c["total_score"], 0)

    def test_llm_parsing(self):
        fake_reply = json.dumps([
            {"category": "Printables", "idea": "Idea 1", "demand_score": 25, "competition_score": 25, "fit_score": 25, "effort_score": 25, "reason": "R1"},
            {"category": "Planners", "idea": "Idea 2", "demand_score": 20, "competition_score": 20, "fit_score": 20, "effort_score": 20, "reason": "R2"},
            {"category": "Templates", "idea": "Idea 3", "demand_score": 15, "competition_score": 15, "fit_score": 15, "effort_score": 15, "reason": "R3"},
            {"category": "Printables", "idea": "Idea 4", "demand_score": 10, "competition_score": 10, "fit_score": 10, "effort_score": 10, "reason": "R4"},
            {"category": "Planners", "idea": "Idea 5", "demand_score": 5, "competition_score": 5, "fit_score": 5, "effort_score": 5, "reason": "R5"},
        ])
        llm_fn = lambda prompt, **kw: fake_reply
        candidates = scout.get_candidate_ideas(llm_fn=llm_fn)
        self.assertEqual(len(candidates), 5)
        self.assertEqual(candidates[0]["idea"], "Idea 1")
        self.assertEqual(candidates[0]["total_score"], 100)

    def test_run_scout_stores_top_5_and_creates_approvals(self):
        ideas = scout.run_scout(db_path=self.db_path)
        self.assertEqual(len(ideas), 5)

        # Check DB
        conn = scout.connect(self.db_path)
        rows = conn.execute("SELECT id, category, idea, total_score, status, approval_id FROM scout_ideas").fetchall()
        conn.close()
        self.assertEqual(len(rows), 5)

        # Check guardrails pending approvals
        pending = guardrails.pending(db_path=self.db_path)
        scout_pending = [p for p in pending if p["kind"] == "scout_idea"]
        self.assertEqual(len(scout_pending), 5)

    def test_approval_flow_and_sync_to_niche_topics(self):
        ideas = scout.run_scout(db_path=self.db_path)
        first = ideas[0]
        approval_id = first["approval_id"]

        # Approve the first scout idea
        guardrails.decide(approval_id, approved=True, db_path=self.db_path)

        # Sync
        synced = scout.sync_approved_ideas(db_path=self.db_path)
        self.assertEqual(synced, 1)

        # Verify status in scout_ideas table
        conn = scout.connect(self.db_path)
        row = conn.execute("SELECT status, synced FROM scout_ideas WHERE approval_id = ?", (approval_id,)).fetchone()
        conn.close()
        self.assertEqual(row[0], "approved")
        self.assertEqual(row[1], 1)

        # Verify added to niche_topics pool
        nt_conn = niche_topics.connect(self.db_path)
        topic_row = nt_conn.execute("SELECT idea, source FROM niche_topics WHERE idea = ?", (first["idea"],)).fetchone()
        nt_conn.close()
        self.assertIsNotNone(topic_row)
        self.assertEqual(topic_row[0], first["idea"])
        self.assertEqual(topic_row[1], "scout")

    def test_rejection_flow(self):
        ideas = scout.run_scout(db_path=self.db_path)
        second = ideas[1]
        approval_id = second["approval_id"]

        # Reject the second scout idea
        guardrails.decide(approval_id, approved=False, db_path=self.db_path)

        synced = scout.sync_approved_ideas(db_path=self.db_path)
        self.assertEqual(synced, 1)

        conn = scout.connect(self.db_path)
        row = conn.execute("SELECT status, synced FROM scout_ideas WHERE approval_id = ?", (approval_id,)).fetchone()
        conn.close()
        self.assertEqual(row[0], "rejected")
        self.assertEqual(row[1], 1)

    def test_public_status_sanitization(self):
        ideas = scout.run_scout(db_path=self.db_path)
        status_file = os.path.join(os.path.dirname(self.db_path), "status.json")
        export_status.write_status(status_file, self.db_path)

        with open(status_file, encoding="utf-8") as f:
            written_status = json.load(f)

        # Ensure scout_ideas_count is in written status.json
        self.assertIn("scout_ideas_count", written_status)
        counts = written_status["scout_ideas_count"]
        self.assertEqual(counts["total"], 5)
        self.assertEqual(counts["pending"], 5)
        self.assertEqual(counts["approved"], 0)

        # Verify idea strings/text are NOT in pending_approvals titles for public status
        pending_titles = [p["title"] for p in written_status["pending_approvals"]]
        for title in pending_titles:
            for idea_item in ideas:
                self.assertNotIn(idea_item["idea"], title)

    def test_daily_run_frequency_guard(self):
        now = datetime.datetime(2026, 10, 18, 12, 0, 0, tzinfo=datetime.timezone.utc)
        res1 = scout.maybe_run(db_path=self.db_path, now=now)
        self.assertTrue(res1["ran"])
        self.assertEqual(len(res1["ideas"]), 5)

        # Second run same day
        res2 = scout.maybe_run(db_path=self.db_path, now=now)
        self.assertFalse(res2["ran"])
        self.assertEqual(res2["reason"], "already ran today")

        # Next day
        next_day = now + datetime.timedelta(days=1)
        res3 = scout.maybe_run(db_path=self.db_path, now=next_day)
        self.assertTrue(res3["ran"])

    def test_no_money_amounts_in_prompt_and_ideas(self):
        # Verify prompt has no money signs
        self.assertNotIn("$", scout.SCOUT_PROMPT)
        self.assertNotIn("USD", scout.SCOUT_PROMPT)

        candidates = scout.rule_based_scout()
        for c in candidates:
            self.assertNotIn("$", c["idea"])
            self.assertNotIn("$", c["reason"])


if __name__ == "__main__":
    unittest.main()
