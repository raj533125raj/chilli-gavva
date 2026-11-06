"""Unit tests for the 10 JR Bible specs in specs/bible/."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import product_builder as pb  # noqa: E402
import truth_check as tc  # noqa: E402

BIBLE_DIR = os.path.join(ROOT, "specs", "bible")

EXPECTED_FILES = [
    "paycheck-budget-planner.json",
    "bills-debt-tracker.json",
    "savings-goals-planner.json",
    "daily-habit-tracker.json",
    "monthly-habit-grid.json",
    "small-business-income-log.json",
    "sales-inventory-sheet.json",
    "pricing-calculator.json",
    "monthly-money-summary.json",
    "weekly-planner.json",
]


def build_bible_product(spec, out_dir):
    """Build a product from a Bible spec using builder functions.

    If the spec has PDF planner pages and also defines sheets, both are built
    into the folder so truth_check validates all deliverables.
    """
    approval_id = spec.get("approval_id", 1)
    folder_name = f"{approval_id}-{spec['short_name']}"
    folder = os.path.join(out_dir, folder_name)
    os.makedirs(folder, exist_ok=True)

    kind = spec["kind"]
    if kind == "planner":
        files, how_to = pb._build_planner(folder, spec)
        if "sheets" in spec and spec["sheets"]:
            wb_name = f"{spec['short_name']}.xlsx"
            pb.build_workbook(os.path.join(folder, wb_name), spec, spec["sheets"])
            files.append(wb_name)
            how_to.append("Open the .xlsx file in Excel.")
    elif kind == "sheets":
        files, how_to = pb._build_sheets(folder, spec)
    else:
        files, how_to = pb.BUILDERS[kind](folder, spec)

    facts = tc.facts(folder)
    spec["listing"]["title"] = pb._sanitize_text(spec["listing"]["title"], spec, facts=facts)
    spec["listing"]["description"] = pb._sanitize_text(spec["listing"]["description"], spec, facts=facts)
    spec["instructions"] = [pb._sanitize_text(inst, spec, facts=facts) for inst in spec["instructions"]]

    with open(os.path.join(folder, "listing.md"), "w", encoding="utf-8") as f:
        f.write(pb._listing_md(spec))
    with open(os.path.join(folder, "cover_image_idea.txt"), "w", encoding="utf-8") as f:
        f.write((spec["cover_image_idea"] or "A clean flat-lay of the product on a laptop screen.") + "\n")
    with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8") as f:
        f.write(pb.LICENSE_TEXT)
    with open(os.path.join(folder, "README.md"), "w", encoding="utf-8") as f:
        extra = ["listing.md", "cover_image_idea.txt", "LICENSE.txt"]
        f.write(pb._readme_md(spec, files + extra, how_to))

    return folder


class TestJrBibleSpecs(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.addCleanup(patcher.stop)
        patcher.start()
        for var in ("GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", "GITHUB_RUN_ID"):
            os.environ.pop(var, None)

    def test_all_10_bible_spec_files_exist(self):
        self.assertTrue(os.path.isdir(BIBLE_DIR), f"Missing {BIBLE_DIR}")
        for filename in EXPECTED_FILES:
            filepath = os.path.join(BIBLE_DIR, filename)
            self.assertTrue(os.path.isfile(filepath), f"Missing spec file: {filename}")

    def test_bible_specs_pass_truth_check(self):
        for filename in EXPECTED_FILES:
            with self.subTest(spec_file=filename):
                filepath = os.path.join(BIBLE_DIR, filename)
                with open(filepath, "r", encoding="utf-8") as f:
                    spec = json.load(f)

                with tempfile.TemporaryDirectory() as temp_dir:
                    product_folder = build_bible_product(spec, temp_dir)
                    result = tc.check(product_folder, with_images=False)
                    self.assertEqual(
                        result["verdict"],
                        "ready",
                        f"Spec {filename} verdict was '{result['verdict']}' (problems: {result.get('problems')})"
                    )
                    self.assertGreaterEqual(result["score"], tc.READY_MIN)
                    self.assertEqual(len(result["problems"]), 0, f"Problems found in {filename}: {result['problems']}")


if __name__ == "__main__":
    unittest.main()
