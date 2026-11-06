"""Tests for auto_approve.py 24h auto-decision logic."""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auto_approve
import guardrails

OWNER = "111222333"


class AutoApproveTests(unittest.TestCase):
    def setUp(self):
        # Clear CI environment variables
        self.env_patcher = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
        }, clear=False)
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "auto.db")

        # Fixed fake clock: 2026-11-05 12:00:00 UTC
        self.fixed_now = datetime.datetime(2026, 11, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)

        self.sent = []
        p_send = mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, *a, **k: self.sent.append((chat, text)) or True)
        p_send.start()
        self.addCleanup(p_send.stop)

    def add_draft(self, hours_ago=25, score=75, title="Digital Planner"):
        ts = (self.fixed_now - datetime.timedelta(hours=hours_ago)).isoformat()
        conn = guardrails.connect(self.db)
        review = f"score {score}/100 | Checker: {score}/100"
        cur = conn.execute(
            "INSERT INTO approvals (ts, kind, title, payload, status, review) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, "public_draft", title, "payload", "pending", review)
        )
        conn.commit()
        aid = cur.lastrowid
        conn.close()
        return aid

    def test_draft_waiting_under_24h_is_not_decided(self):
        aid = self.add_draft(hours_ago=23, score=85)
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions), 0)
        item = guardrails.get_approval(aid, self.db)
        self.assertEqual(item["status"], "pending")

    def test_draft_waiting_over_24h_high_score_auto_approves(self):
        aid = self.add_draft(hours_ago=25, score=70, title="High Score Planner")
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["action"], "approved")
        self.assertEqual(decisions[0]["id"], aid)
        item = guardrails.get_approval(aid, self.db)
        self.assertEqual(item["status"], "approved")

    def test_draft_waiting_over_24h_low_score_auto_rejects_with_reason(self):
        aid = self.add_draft(hours_ago=25, score=69, title="Low Score Template")
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["action"], "rejected")
        self.assertEqual(decisions[0]["id"], aid)
        item = guardrails.get_approval(aid, self.db)
        self.assertEqual(item["status"], "rejected")
        conn = guardrails.connect(self.db)
        reason = conn.execute("SELECT reason FROM approvals WHERE id = ?", (aid,)).fetchone()[0]
        conn.close()
        self.assertEqual(reason, "Low score (auto)")

    def test_owner_decision_always_wins(self):
        aid = self.add_draft(hours_ago=25, score=60, title="Owner Rejected First")
        # Owner previously decided
        guardrails.decide(aid, False, self.db)
        conn = guardrails.connect(self.db)
        conn.execute("UPDATE approvals SET reason = ? WHERE id = ?", ("Too generic", aid))
        conn.commit()
        conn.close()

        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions), 0)

        # Owner previously approved
        aid2 = self.add_draft(hours_ago=25, score=50, title="Owner Approved First")
        guardrails.decide(aid2, True, self.db)
        decisions2 = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions2), 0)

    def test_respects_pause_switch(self):
        self.add_draft(hours_ago=25, score=80)
        guardrails.set_paused(True, self.db)
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions), 0)

    def test_respects_kill_switch(self):
        self.add_draft(hours_ago=25, score=80)
        with mock.patch("guardrails.is_killed", return_value=True):
            decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
            self.assertEqual(len(decisions), 0)

    def test_run_auto_decisions_sends_one_line_per_decision(self):
        aid1 = self.add_draft(hours_ago=26, score=85, title="Good Draft")
        aid2 = self.add_draft(hours_ago=26, score=40, title="Weak Draft")

        count = auto_approve.run_auto_decisions(
            self.db, token="tok", owner_chat_id=OWNER, now=self.fixed_now
        )
        self.assertEqual(count, 2)
        self.assertEqual(len(self.sent), 2)
        # Check that one line per decision was sent
        self.assertIn(f"#{aid1} auto-approved", self.sent[0][1])
        self.assertIn(f"#{aid2} auto-rejected", self.sent[1][1])


if __name__ == "__main__":
    unittest.main()
