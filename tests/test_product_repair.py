"""Tests for product_repair.py — Repairing old "NEEDS FIX" products safely and cheaply.

All tests run in temporary directories with fake products, never touching real files or archives.
Verifies:
  1. Listing-only fix -> READY
  2. Small rebuild fix -> READY
  3. Still failing -> NEEDS FIX with reason
  4. Daily cap of 3 repairs
  5. Repair error does not stop the pipeline (run_daily never raises)
  6. Respects daily AI cap / guardrails
  7. No real files or archives touched
"""

import os
import shutil
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import pdf_design
import product_builder
import product_repair
import truth_check


class TestProductRepair(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_agent_data.db")
        self.out_dir = os.path.join(self.tmp_dir, "products")
        os.makedirs(self.out_dir, exist_ok=True)
        self.conn = product_repair.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_fake_sheet_product(self, approval_id=1, folder_name="1-fake-budget"):
        """Create a fake sheets product folder with 2 sheets in .xlsx."""
        folder = os.path.join(self.out_dir, folder_name)
        os.makedirs(folder, exist_ok=True)

        xlsx_path = os.path.join(folder, "fake-budget.xlsx")
        with zipfile.ZipFile(xlsx_path, "w") as z:
            z.writestr(
                "xl/workbook.xml",
                '<workbook><sheets><sheet name="Monthly Budget"/><sheet name="Expense Log"/></sheets></workbook>',
            )
            z.writestr("xl/worksheets/sheet1.xml", "<worksheet></worksheet>")

        # Listing makes claims not supported by the files (e.g. dashboard, charts)
        with open(os.path.join(folder, "listing.md"), "w", encoding="utf-8") as f:
            f.write(
                "# Fake Budget Sheet\n\n"
                "**Buyer:** Freelancers  \n"
                "**Platform:** Gumroad\n\n"
                "## Description\n\n"
                "Includes a complete dashboard, progress bars, and charts / progress bars. "
                "Also includes Settings tab and duplicate link.\n"
            )

        with open(os.path.join(folder, "README.md"), "w", encoding="utf-8") as f:
            f.write(
                "# Fake Budget Sheet\n\n"
                "## Description\n\n"
                "Features dashboard and charts / progress bars.\n"
            )

        # Record in product_builds and truth_checks as needs_fix
        self.conn.execute(
            "INSERT INTO product_builds (approval_id, status, tries, folder, note) "
            "VALUES (?, 'needs_fix', 1, ?, 'needs fix: dashboard; charts')",
            (approval_id, folder_name),
        )
        self.conn.execute(
            "INSERT INTO truth_checks (approval_id, verdict, score, notified) "
            "VALUES (?, 'needs fix', 60, 'needs fix')",
            (approval_id,),
        )
        self.conn.commit()
        return folder

    def _create_fake_planner_product(self, approval_id=2, folder_name="2-fake-planner"):
        """Create a fake PDF planner product folder with A4 and Letter PDFs."""
        folder = os.path.join(self.out_dir, folder_name)
        os.makedirs(folder, exist_ok=True)

        for size in ("A4", "Letter"):
            pdf_path = os.path.join(folder, f"fake-planner-{size}.pdf")
            p = pdf_design.Pdf(size)
            p.new_page()
            p.text(50.0, 700.0, "Overview", size=18, bold=True)
            p.line(50, 600, 200, 600)
            p.new_page()
            p.text(50.0, 700.0, "Daily Habit", size=18, bold=True)
            p.line(50, 600, 200, 600)
            p.save(pdf_path)

        # Listing promises weekly or daily dated pages (unsupported initially)
        with open(os.path.join(folder, "listing.md"), "w", encoding="utf-8") as f:
            f.write(
                "# Fake Planner\n\n"
                "**Buyer:** Students  \n"
                "**Platform:** Gumroad\n\n"
                "## Description\n\n"
                "Includes weekly spreads and daily dated pages. "
                "Delivered in A4 and Letter with 2 pages.\n"
            )

        with open(os.path.join(folder, "README.md"), "w", encoding="utf-8") as f:
            f.write(
                "# Fake Planner\n\n"
                "Includes weekly or daily dated pages.\n"
            )

        self.conn.execute(
            "INSERT INTO product_builds (approval_id, status, tries, folder, note) "
            "VALUES (?, 'needs_fix', 1, ?, 'needs fix: weekly or daily dated pages')",
            (approval_id, folder_name),
        )
        self.conn.execute(
            "INSERT INTO truth_checks (approval_id, verdict, score, notified) "
            "VALUES (?, 'needs fix', 76, 'needs fix')",
            (approval_id,),
        )
        self.conn.commit()
        return folder

    def test_listing_only_fix_becomes_ready(self):
        """Repairing unproven claims via listing/README rewrite turns verdict to READY."""
        self._create_fake_sheet_product(approval_id=1, folder_name="1-fake-budget")

        # Verify initial check is needs fix
        init_res = truth_check.check(os.path.join(self.out_dir, "1-fake-budget"), with_images=False)
        self.assertEqual(init_res["verdict"], "needs fix")

        # Repair
        res = product_repair.repair_product(1, "1-fake-budget", db_path=self.db_path, out_dir=self.out_dir)
        self.assertEqual(res["verdict"], "ready")

        # Check DB updated
        row = self.conn.execute("SELECT status, note FROM product_builds WHERE approval_id = 1").fetchone()
        self.assertEqual(row[0], "built")
        self.assertEqual(row[1], "ready")

        tc_row = self.conn.execute("SELECT verdict, score FROM truth_checks WHERE approval_id = 1").fetchone()
        self.assertEqual(tc_row[0], "ready")
        self.assertGreaterEqual(tc_row[1], 80)

    def test_small_rebuild_fix_becomes_ready(self):
        """A planner with missing small deliverable can be rebuilt once and becomes READY."""
        self._create_fake_planner_product(approval_id=2, folder_name="2-fake-planner")

        init_res = truth_check.check(os.path.join(self.out_dir, "2-fake-planner"), with_images=False)
        self.assertEqual(init_res["verdict"], "needs fix")

        # Repair with small rebuild
        res = product_repair.repair_product(2, "2-fake-planner", db_path=self.db_path, out_dir=self.out_dir)
        self.assertEqual(res["verdict"], "ready")
        self.assertEqual(res["action"], "small_rebuild")

        # Check DB updated
        row = self.conn.execute("SELECT status, note FROM product_builds WHERE approval_id = 2").fetchone()
        self.assertEqual(row[0], "built")

        tc_row = self.conn.execute("SELECT verdict FROM truth_checks WHERE approval_id = 2").fetchone()
        self.assertEqual(tc_row[0], "ready")

    def test_still_failing_records_needs_fix_with_reason(self):
        """If built product has intrinsic defect (e.g. broken PDF with 1 page), remains NEEDS FIX with reason."""
        folder = os.path.join(self.out_dir, "3-broken-pdf")
        os.makedirs(folder, exist_ok=True)

        pdf_path = os.path.join(folder, "broken-A4.pdf")
        p = pdf_design.Pdf("A4")
        p.new_page()
        # Empty page / only 1 page
        p.text(50.0, 700.0, "Only One", size=18, bold=True)
        p.save(pdf_path)

        with open(os.path.join(folder, "listing.md"), "w", encoding="utf-8") as f:
            f.write("# Broken Planner\n\nOnly 1 page.\n")
        with open(os.path.join(folder, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Broken Planner\n")

        self.conn.execute(
            "INSERT INTO product_builds (approval_id, status, tries, folder, note) "
            "VALUES (3, 'needs_fix', 1, '3-broken-pdf', 'needs fix: only 1 page(s)')"
        )
        self.conn.execute(
            "INSERT INTO truth_checks (approval_id, verdict, score, notified) "
            "VALUES (3, 'needs fix', 40, 'needs fix')"
        )
        self.conn.commit()

        # Run repair
        res = product_repair.repair_product(3, "3-broken-pdf", db_path=self.db_path, out_dir=self.out_dir)
        self.assertEqual(res["verdict"], "needs fix")
        self.assertTrue(len(res["problems"]) > 0)

        # Database must record needs_fix with reason
        row = self.conn.execute("SELECT status, note FROM product_builds WHERE approval_id = 3").fetchone()
        self.assertEqual(row[0], "needs_fix")
        self.assertIn("needs fix", row[1])

        tc_row = self.conn.execute("SELECT verdict FROM truth_checks WHERE approval_id = 3").fetchone()
        self.assertEqual(tc_row[0], "needs fix")

    def test_daily_cap_of_three_repairs(self):
        """run_daily processes at most 3 repairs per day."""
        for i in range(10, 15):
            self._create_fake_sheet_product(approval_id=i, folder_name=f"{i}-fake-sheet-{i}")

        summary = product_repair.run_daily(db_path=self.db_path, out_dir=self.out_dir, max_repairs=3)
        self.assertEqual(summary["attempted"], 3)
        self.assertEqual(summary["repaired"], 3)
        self.assertEqual(len(summary["results"]), 3)

        # Verify only 3 products were updated to 'built'
        built_rows = self.conn.execute("SELECT approval_id FROM product_builds WHERE status = 'built'").fetchall()
        self.assertEqual(len(built_rows), 3)

    def test_repair_error_does_not_stop_the_pipeline(self):
        """Unexpected exception during repair is caught, recorded, and run_daily returns summary."""
        self._create_fake_sheet_product(approval_id=20, folder_name="20-fake-sheet-20")

        # Mock repair_product to raise an unexpected exception
        with patch("product_repair.repair_product", side_effect=RuntimeError("Simulated disk error")):
            summary = product_repair.run_daily(db_path=self.db_path, out_dir=self.out_dir, max_repairs=3)

        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["attempted"], 1)
        self.assertEqual(summary["still_needs_fix"], 1)
        self.assertIn("Simulated disk error", summary["results"][0]["error"])

    def test_respects_guardrails_kill_and_hold(self):
        """run_daily respects kill switch and owner policy automation holds."""
        self._create_fake_sheet_product(approval_id=25, folder_name="25-fake-sheet-25")

        with patch("guardrails.is_killed", return_value=True):
            res_killed = product_repair.run_daily(db_path=self.db_path, out_dir=self.out_dir)
            self.assertEqual(res_killed["status"], "killed")
            self.assertEqual(res_killed["attempted"], 0)

        with patch("guardrails.automation_allowed", return_value=False):
            res_held = product_repair.run_daily(db_path=self.db_path, out_dir=self.out_dir)
            self.assertEqual(res_held["status"], "held")
            self.assertEqual(res_held["attempted"], 0)

    def test_no_real_files_touched(self):
        """Repair operates completely inside out_dir and db_path, not touching repo root state."""
        self._create_fake_sheet_product(approval_id=30, folder_name="30-fake-sheet-30")
        real_files_before = set(os.listdir(product_repair.BASE_DIR))

        product_repair.run_daily(db_path=self.db_path, out_dir=self.out_dir, max_repairs=3)

        real_files_after = set(os.listdir(product_repair.BASE_DIR))
        # Ensure no unexpected new artifacts in repo root
        diff = real_files_after - real_files_before
        allowed_new = {"__pycache__"}
        self.assertTrue(diff.issubset(allowed_new), f"Unexpected files created in repo root: {diff}")


if __name__ == "__main__":
    unittest.main()
