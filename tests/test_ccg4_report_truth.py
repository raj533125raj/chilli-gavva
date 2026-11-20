"""CCG-4: the daily report and /today say the truth about the product path.

Product drafts waiting (oldest hours), proposals waiting (apart), approved today (owner / auto), built today,
needs fix today with the top reason. Before: proposals were counted as drafts and the owner's own approvals as
"auto-approved" (CCG-1 cause 4).
"""
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import guardrails

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import approval_probe  # noqa: E402
import daily_report  # noqa: E402
import product_builder  # noqa: E402

NOW = datetime.datetime(2026, 11, 15, 12, 5)            # naive UTC like the report
NOW_UTC = NOW.replace(tzinfo=datetime.timezone.utc)
CHAT = int("1234" "5678")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "agent_data.db")
        guardrails.connect(self.db).close()
        approval_probe.seed_queue(Path(self.db), NOW_UTC)   # the 15 Nov queue (7 waiting, oldest 76 h)

    def record(self, aid, status, note, hours=2):
        product_builder._record(self.db, aid, status, note=note)
        conn = guardrails.connect(self.db)
        conn.execute("UPDATE product_builds SET ts=? WHERE approval_id=?",
                     ((NOW - datetime.timedelta(hours=hours)).isoformat(), aid))
        conn.commit()
        conn.close()


class ProductPathTests(Base):
    def test_numbers_split_drafts_and_proposals(self):
        p = daily_report.product_path(self.db, NOW)
        self.assertEqual((p["drafts_waiting"], p["oldest_hours"]), (4, 60))    # product drafts only
        self.assertEqual((p["proposals_waiting"], p["rule_proposals"]), (3, 1))  # Scout + Trend + code patch
        self.assertEqual((p["approved"], p["approved_owner"], p["approved_auto"]), (1, 1, 0))

    def test_needs_fix_top_reason(self):
        first = approval_probe.FIRST_ID
        self.record(first + 2, "built", "READY; encrypted private pack")
        self.record(first + 4, "retry", "Product #9005: checker: Checker did not pass the approved draft")
        self.record(first + 6, "needs_fix", "Product #9007: checker: Checker did not pass the approved draft")
        self.record(first + 5, "needs_fix", "needs fix: listing promises 12 pages, file has 10")
        self.record(first + 1, "needs_fix", "old one", hours=40)                       # not today
        p = daily_report.product_path(self.db, NOW)
        self.assertEqual((p["built"], p["needs_fix"]), (1, 3))
        self.assertEqual(p["top_reason"], "checker: Checker did not pass the approved draft")

    def test_daily_text_tells_the_truth(self):
        text = daily_report.daily_text(self.db, now=NOW, status_fn=lambda: {})
        self.assertIn("Product drafts waiting: 4 (oldest 60h; enduku: /autoapprove status)", text)
        self.assertIn("Proposals waiting: 3 + 1 Self-Improvement (product drafts kaadu, meere decide)", text)
        self.assertIn("Approved today: 1 (owner 1, auto 0)", text)
        self.assertIn("Auto-decided (last 24h): 0 approved", text)      # the owner's approval is not "auto"
        self.assertIn("Needs fix today: 0", text)
        self.assertIn("Drafts: 1 made, 4 waiting, 1 approved", text)   # the 2 h old Freelancer draft
        self.assertNotRegex(text, r"[$₹€£]")

    def test_waiting_line_counts_proposals_apart(self):
        import telegram_inbox
        line = telegram_inbox.daily_waiting_line(self.db, NOW_UTC.timestamp())
        self.assertEqual(line, "Drafts waiting: 4 (oldest 60h); proposals waiting: 3")


class TodayCommandTests(Base):
    def test_today_through_process_updates(self):
        import telegram_inbox
        sent = []
        update = {"update_id": 5, "message": {"message_id": 5, "date": int(NOW_UTC.timestamp()), "text": "/today",
                                              "from": {"id": CHAT}, "chat": {"id": CHAT, "type": "private"}}}
        with mock.patch.object(telegram_inbox, "fetch_updates", return_value=[update]), \
                mock.patch.object(telegram_inbox, "send_text",
                                  side_effect=lambda tok, chat, text, *a, **k: sent.append(text) or True), \
                mock.patch.object(telegram_inbox, "_send_text_kw",
                                  side_effect=lambda tok, chat, text, keyboard=None: sent.append(text) or True), \
                mock.patch.object(telegram_inbox, "acknowledge_update", return_value=True), \
                mock.patch.object(telegram_inbox, "_call", return_value={"ok": True}), \
                mock.patch.object(telegram_inbox, "today_posts_text", return_value="Post plan ledu."):
            telegram_inbox.process_updates("tok", CHAT, self.db)
        text = "\n".join(sent)
        self.assertIn("Post plan ledu.", text)
        self.assertIn("Product path (last 24h):", text)
        self.assertIn("Product drafts waiting: 4", text)
        self.assertIn("Proposals waiting: 3", text)
        self.assertIn("Built today: 0", text)
        self.assertIn("Needs fix today: 0", text)

    def test_today_still_shows_posts_if_numbers_fail(self):
        import telegram_inbox
        with mock.patch.object(daily_report, "product_path", side_effect=RuntimeError("x")):
            self.assertIn("chupinchalekapoyanu", telegram_inbox.today_product_text(self.db))


if __name__ == "__main__":
    unittest.main()
