"""CCZ-2: "Pack fixes leda Hold" is split into robot fix / AI wait / owner decision (live report 25 Nov)."""
import datetime
import os
import tempfile
import unittest
from unittest import mock

import fix_reasons
import guardrails
import product_builder
import product_counts
import daily_report

NOW = datetime.datetime(2027, 1, 4, 12, 0)

LIVE_NOTES = [
    ("retry", "Product #31: truth_or_build: Failed to parse spec", "robot"),
    ("retry", "Product #32: truth_or_build: Spec could not be read: reply is not valid JSON (after one rewrite retry)",
     "robot"),
    ("needs_fix", "Product #33: truth_or_build: Deep QA issue: empty_bottom - page 3", "robot"),
    ("retry", "Product #34: build: Stage failed (KeyError)", "robot"),
    ("retry", "Product #35: build: Daily AI call cap reached", "wait"),
    ("retry", "no reply from the model", "wait"),
    ("awaiting_encryption", "Truth check READY; awaiting private encryption", "wait"),
    ("retry", "Product #36: checker: Checker did not pass the approved draft", "owner"),
    ("retry", "Product #37: niche: Unsupported product niche", "owner"),
    ("council_rejected", "Council malli no cheppindi; oka improvement tarvata reject: quality", "owner"),
    ("not_for_sale", "", "owner"),
]


class ClassifyTests(unittest.TestCase):
    def test_live_shaped_notes(self):
        for status, note, side in LIVE_NOTES:
            with self.subTest(note=note):
                self.assertEqual(fix_reasons.classify(status, note)[0], side)
        self.assertEqual(fix_reasons.classify("retry", "Product #31: truth_or_build: Failed to parse spec")[1],
                         "truth_or_build: Failed to parse spec")
        self.assertEqual(fix_reasons.classify("retry", "x", held=True), ("owner", "Owner hold: NOT FOR SALE"))


class DbTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.db = os.path.join(tmp.name, "agent_data.db")
        guardrails.connect(self.db).close()
        self.ids = []
        for i, (status, note, _) in enumerate(LIVE_NOTES):
            aid = guardrails.request_approval("public_draft", f"Draft: [Budget Sheets Agent] Sheet {i}",
                                              "PRODUCT NAME: x", None, self.db)
            with guardrails.connect(self.db) as conn:
                conn.execute("UPDATE approvals SET status='approved' WHERE id=?", (aid,))
            product_builder._record(self.db, aid, status, note=note)
            self.ids.append(aid)
        rejected = guardrails.request_approval("public_draft", "Draft: [Budget Sheets Agent] gone", "x", None, self.db)
        with guardrails.connect(self.db) as conn:
            conn.execute("UPDATE approvals SET status='rejected' WHERE id=?", (rejected,))
        product_builder._record(self.db, rejected, "retry", note="Failed to parse spec")
        with guardrails.connect(self.db) as conn:
            conn.execute("UPDATE product_builds SET ts=?", ((NOW - datetime.timedelta(hours=2)).isoformat(),))

    def test_rows_table_and_counts(self):
        rows = fix_reasons.rows(self.db)
        self.assertEqual(len(rows), len(LIVE_NOTES))            # rejected draft is not counted
        self.assertEqual([r["side"] for r in rows], [side for _, _, side in LIVE_NOTES])
        table = fix_reasons.table(self.db)
        self.assertIn("| truth_or_build: Failed to parse spec | robot | 1 |", table)
        self.assertEqual(fix_reasons.counts(self.db, ready_ids={self.ids[0]})[fix_reasons.LABELS["robot"]], 3)

    def test_sell_test_blocks_line_splits_robot_and_owner(self):
        with mock.patch("auto_approve.waiting_report", return_value={"drafts": []}):
            blockers = product_counts.sell_test_progress(self.db, os.path.join(self.tmp, "products"), NOW)["blockers"]
        self.assertEqual(blockers["Robot fix chestondi"], 4)
        self.assertEqual(blockers["Owner decision kavali: /ready"], 4)
        self.assertEqual(blockers["AI/limit kosam wait"], 3)
        self.assertNotIn("Pack fixes leda Hold: /ready", blockers)

    def test_daily_needs_fix_line_names_both_sides(self):
        with mock.patch("product_pipeline.pending", return_value=[]):
            lines = daily_report.product_path_lines(self.db, NOW)
        line = next(x for x in lines if x.startswith("Needs fix today:"))
        # needs_fix / retry / failed rows of the last day: 9 of the 11 (awaiting_encryption, council_rejected,
        # not_for_sale are other statuses) + the rejected draft's row
        self.assertIn("robot fix chestondi 5", line)
        self.assertIn("owner decision kavali 2", line)
        self.assertIn("AI/limit wait 2", line)

    def test_cli_prints_table(self):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            fix_reasons.main(["--db", self.db])
        self.assertTrue(out.getvalue().startswith("| Reason | Side | Count |"))


if __name__ == "__main__":
    unittest.main()
