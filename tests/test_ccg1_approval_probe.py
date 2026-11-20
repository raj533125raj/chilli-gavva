"""CCG-1: the approval probe (tools/approval_probe.py) seeds a queue shaped like the 15 Nov report.

The full probe runs the day simulator (about 30 s); here only the seeding is checked, fast and offline.
"""
import datetime as dt
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import guardrails

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import approval_probe  # noqa: E402


class ApprovalProbeSeedTests(unittest.TestCase):
    def test_queue_matches_the_report_shape(self):
        now = dt.datetime(2026, 11, 15, 12, 5, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "agent_data.db")
            guardrails.connect(db).close()
            ids = approval_probe.seed_queue(Path(db), now)
            self.assertEqual(ids[0], approval_probe.FIRST_ID)
            pending = guardrails.pending(db)
            self.assertEqual(len(pending), 7)                       # "7 drafts waiting"
            oldest = min(dt.datetime.fromisoformat(p["ts"]) for p in pending)
            self.assertEqual(round((now.replace(tzinfo=None) - oldest).total_seconds() / 3600), 76)
            kinds = {p["kind"] for p in pending}
            self.assertEqual(kinds, {"public_draft", "scout_idea", "trend_proposal", "code_patch"})
            conn = sqlite3.connect(db)
            owner = conn.execute("SELECT status, reason FROM approvals WHERE status='approved'").fetchall()
            rules = conn.execute("SELECT status FROM learned_rules").fetchall()
            conn.close()
            self.assertEqual(owner, [("approved", None)])            # owner button: no reason
            self.assertEqual(rules, [("proposed",)])                 # Self-Improvement Proposal

    def test_only_drafts_scenario(self):
        now = dt.datetime(2026, 11, 15, 12, 5, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "agent_data.db")
            guardrails.connect(db).close()
            approval_probe.seed_queue(Path(db), now, only_drafts=True)
            pending = guardrails.pending(db)
            self.assertTrue(pending)
            self.assertEqual({p["kind"] for p in pending}, {"public_draft"})
            self.assertTrue(all("[" in p["title"] and "Agent]" in p["title"] for p in pending))


if __name__ == "__main__":
    unittest.main()
