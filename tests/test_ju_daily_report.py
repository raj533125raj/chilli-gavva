"""Tests for daily_report.py daily_text() additions (isolated temp databases)."""
import datetime
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auto_approve  # noqa: E402
import daily_report as dr  # noqa: E402
import guardrails  # noqa: E402
import product_builder  # noqa: E402

MONEY_PATTERN = re.compile(
    r"(\$\d+|\d+\s*\$|\b\d+\s*(?:dollars|USD|EUR|GBP|INR|Rupees)\b|[\$\€\£\₹]\d+)",
    re.IGNORECASE,
)


class JuDailyReportTests(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
        }, clear=False)
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "ju_daily.db")
        self.addCleanup(self.tmp.cleanup)

        # Base reference timestamp: 2026-11-06 12:00:00 UTC
        self.fixed_now = datetime.datetime(2026, 11, 6, 12, 0, 0)

    def _insert_draft(self, hours_ago=2, score=75, title="Budget Planner", status="pending",
                      decided_hours_ago=None, reason=None):
        ts = (self.fixed_now - datetime.timedelta(hours=hours_ago)).isoformat()
        decided_ts = (self.fixed_now - datetime.timedelta(hours=decided_hours_ago)).isoformat() if decided_hours_ago is not None else None
        conn = guardrails.connect(self.db)
        review = f"score {score}/100 | Checker: {score}/100"
        cur = conn.execute(
            "INSERT INTO approvals (ts, kind, title, payload, status, review, decided_ts, reason) "
            "VALUES (?, 'public_draft', ?, 'payload', ?, ?, ?, ?)",
            (ts, title, status, review, decided_ts, reason)
        )
        conn.commit()
        aid = cur.lastrowid
        conn.close()
        return aid

    def _insert_product(self, approval_id, folder="1-test", status="built", hours_ago=2):
        ts = (self.fixed_now - datetime.timedelta(hours=hours_ago)).isoformat()
        conn = product_builder.connect(self.db)
        conn.execute(
            "INSERT INTO product_builds (approval_id, status, tries, folder, note, ts) "
            "VALUES (?, ?, 1, ?, 'note', ?)",
            (approval_id, status, folder, ts)
        )
        conn.commit()
        conn.close()

    def _insert_button(self, hours_ago=2):
        ts = (self.fixed_now - datetime.timedelta(hours=hours_ago)).isoformat()
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE IF NOT EXISTS button_log (ts TEXT)")
        conn.execute("INSERT INTO button_log (ts) VALUES (?)", (ts,))
        conn.commit()
        conn.close()

    def test_auto_decided_counts_from_auto_approve_engine(self):
        # 1 draft waiting > 24h with score 80 -> auto-approved
        # 1 draft waiting > 24h with score 50 -> auto-rejected (Low score (auto))
        # 1 draft waiting 5h with score 90 -> pending
        aid1 = self._insert_draft(hours_ago=26, score=80, title="High Score Planner")
        aid2 = self._insert_draft(hours_ago=26, score=50, title="Low Score Template")
        aid3 = self._insert_draft(hours_ago=5, score=90, title="Recent Pending")

        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions), 2)

        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {})
        self.assertIn("Auto-decided (last 24h): 1 approved, 1 rejected", text)
        self.assertIn("Button presses received in the last 24h: 0", text)
        self.assertIn("Next step for owner: approve or reject drafts in /list", text)

    def test_button_presses_and_old_lines_preserved(self):
        self._insert_button(hours_ago=1)
        self._insert_button(hours_ago=5)
        self._insert_button(hours_ago=30)  # > 24h, must not be counted

        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {})
        self.assertIn("Button presses received in the last 24h: 2", text)
        self.assertIn("Errors: none", text)
        self.assertIn("Chilli Gavva daily report (2026-11-06)", text)

    def test_built_products_line_and_privacy_anonymisation(self):
        # Approved draft built
        aid1 = self._insert_draft(hours_ago=10, title="Productivity Workbook for ADHD Adults", status="approved", decided_hours_ago=10)
        self._insert_product(aid1, folder="1-prod-adhd", hours_ago=2)

        # Pending draft with built product (must NOT expose unapproved title, anonymised as Draft #<id>)
        aid2 = self._insert_draft(hours_ago=10, title="Secret Unapproved Private Draft Title", status="pending")
        self._insert_product(aid2, folder="2-secret", hours_ago=3)

        # Very long title should cut to 40 characters
        aid3 = self._insert_draft(hours_ago=10, title="A" * 60, status="approved", decided_hours_ago=10)
        self._insert_product(aid3, folder="3-long", hours_ago=4)

        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {})
        self.assertIn("Built products (last 24h): ", text)
        self.assertIn("Productivity Workbook for ADHD Adults"[:40], text)
        self.assertIn(f"Draft #{aid2}", text)
        self.assertNotIn("Secret Unapproved Private Draft Title", text)
        self.assertIn("A" * 40, text)
        self.assertNotIn("A" * 41, text)

    def test_built_products_max_5_titles(self):
        for i in range(1, 8):
            aid = self._insert_draft(hours_ago=10, title=f"Approved Product {i}", status="approved", decided_hours_ago=10)
            self._insert_product(aid, folder=f"{aid}-folder", hours_ago=1)

        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {})
        m = re.search(r"Built products \(last 24h\): (.+)", text)
        self.assertIsNotNone(m)
        titles = [t.strip() for t in m.group(1).split(",")]
        self.assertEqual(len(titles), 5)

    def test_built_products_none(self):
        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {})
        self.assertIn("Built products (last 24h): none", text)

    def test_next_step_waiting_drafts(self):
        self._insert_draft(hours_ago=2, status="pending")
        # Even if products built, waiting drafts take precedence
        self._insert_product(99, folder="99-p", hours_ago=1)
        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {})
        self.assertIn("Next step for owner: approve or reject drafts in /list", text)

    def test_next_step_products_built(self):
        # 0 waiting drafts, 1 product built
        self._insert_product(1, folder="1-p", hours_ago=1)
        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {})
        self.assertIn("Next step for owner: check upload packs", text)

    def test_next_step_nothing_needed(self):
        # 0 waiting drafts, 0 products built
        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {})
        self.assertIn("Next step for owner: nothing needed", text)

    def test_no_money_pattern_and_length_limit(self):
        # Populate with drafts, products, buttons, errors
        self._insert_draft(hours_ago=2, score=70, title="Planner", status="approved", decided_hours_ago=1)
        self._insert_product(1, folder="1-p", hours_ago=1)
        self._insert_button(hours_ago=1)

        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {
            "month_minutes": 250, "month_runs": 15, "door_bell": True, "telegram_token_ok": "yes"
        })
        self.assertLessEqual(len(text), 3500)
        money_matches = MONEY_PATTERN.findall(text)
        self.assertEqual(len(money_matches), 0, f"Prohibited money pattern found: {money_matches}")


if __name__ == "__main__":
    unittest.main()
