"""CXH-1: reproducible backlog evidence with twelve drafts and a fake clock."""
import datetime
import random
import tempfile
import unittest
from pathlib import Path

import auto_approve
import checker
import guardrails
import improver_loop
import workforce

NOW = datetime.datetime(2026, 11, 22, 6, tzinfo=datetime.timezone.utc)


class QueueFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "queue.db")
        guardrails.connect(self.db).close()

    def add(self, name, score=85, hours=30, kind="public_draft", verdict="pass"):
        conn = guardrails.connect(self.db)
        cur = conn.execute("INSERT INTO approvals (ts, kind, title, payload, review) VALUES (?, ?, ?, ?, ?)",
                           ((NOW - datetime.timedelta(hours=hours)).isoformat(), kind, name,
                            " ".join(name.replace(" ", "") + str(i) for i in range(10)),
                            checker.format_review(score, ["Format check chudandi"] if verdict != "pass" else [], verdict)))
        conn.commit()
        conn.close()
        return cur.lastrowid


class QueueProbeTests(QueueFixture):
    def test_twelve_mixed_items_have_an_explicit_guard(self):
        young = self.add("Young planner", 95, 6)
        low = self.add("Unsupported format", 40, 80, verdict="reject")
        revise = self.add("Needs layout correction", 88, 50, verdict="revise")
        for i, score in enumerate((81, 83, 86, 89, 92, 94)):
            self.add(f"Distinct niche {i}", score, 30 + i)
        proposals = [self.add(name, 90, 100, kind=kind) for name, kind in
                     (("Scout proposal", "scout_idea"), ("Trend proposal", "trend_proposal"),
                      ("Code proposal", "code_patch"))]
        conn = improver_loop.connect(self.db)
        conn.execute("INSERT INTO learned_rules(rule_text, status, created_ts) VALUES (?, 'proposed', ?)",
                     ("Use readable margins", (NOW - datetime.timedelta(hours=80)).isoformat()))
        conn.commit()
        conn.close()
        plan = auto_approve._plan(self.db, NOW)
        self.assertEqual(len(plan), 12)
        reasons = {row["id"]: row["why"] for row in plan}
        self.assertEqual(reasons[young], "under_24h")
        self.assertEqual(reasons[low], "low_score")
        self.assertEqual(reasons[revise], "checker_revise")
        self.assertEqual({reasons[aid] for aid in proposals}, {"proposal"})
        self.assertIn("daily_limit", reasons.values())
        self.assertEqual(auto_approve.waiting_rule_proposals(self.db), 1)
        self.assertTrue(all(row.get("why") for row in plan))

    def test_twelve_identical_scores_cannot_be_auto_approved(self):
        for i in range(12):
            self.add(f"Unique printable subject {i}", 88, 30 + i)
        plan = auto_approve._plan(self.db, NOW)
        self.assertEqual(len(plan), 12)
        self.assertEqual({row["why"] for row in plan}, {"checker_signal"})
        self.assertEqual({row["action"] for row in plan}, {"wait"})

    def test_recent_duplicate_is_explained_separately(self):
        original = self.add("Exact same focus worksheet", 89, 80)
        guardrails.decide(original, True, self.db)
        candidate = self.add("Exact same focus worksheet", 87, 30)
        plan = auto_approve._plan(self.db, NOW)
        self.assertEqual([(row["id"], row["why"], row.get("match")) for row in plan],
                         [(candidate, "similar", original)])


if __name__ == "__main__":
    unittest.main()
