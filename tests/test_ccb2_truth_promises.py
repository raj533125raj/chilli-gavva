"""CCB-2: the Truth Checker passes more often — honestly — because the GENERATOR states proven facts.

Why packs got NEEDS FIX (found by building every specs/bible spec and every product type):
  1. "Score 95+ without enough proof" — the listing named almost nothing the checker can verify.
  2. "Promises 'size: A4' / 'size: Letter': no A4 / Letter file" — format builders named their PDFs
     like habit_tracker_a4.pdf, so a TRUE paper size could not be proven.
The fixes are in product_builder.py only; truth_check.py is unchanged. One test class per fixed
promise type, plus tests that no line is ever written without proof. Temp folders only.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import product_builder as pb
import test_ccb1_theme_images as kinds      # spec_for() for all 12 product types; not re-collected here
import truth_check

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BUILT = {}
_TMP = None


def setUpModule():
    global _TMP
    _TMP = tempfile.mkdtemp(prefix="ccb2_")
    kinds.setUpModule()          # quick PNG writing for the big wall-art files (same pixels)


def tearDownModule():
    kinds.tearDownModule()
    shutil.rmtree(_TMP, ignore_errors=True)


def built(kind):
    """Folder of this product type (thin test spec, no theme), built once per module."""
    if kind not in _BUILT:
        out = os.path.join(_TMP, kind)
        os.makedirs(out)
        spec = kinds.spec_for(kind, theme=None)
        _BUILT[kind] = os.path.join(out, pb.build_product(copy.deepcopy(spec), 400 + kinds.ALL_KINDS.index(kind), out))
    return _BUILT[kind]


def what_you_get(folder):
    with open(os.path.join(folder, "listing.md"), encoding="utf-8") as handle:
        body = handle.read()
    return body.split("## What you get", 1)[1].split("## Tags", 1)[0]


def check(folder):
    return truth_check.check(folder, with_images=False)


class RedFlagClearedTests(unittest.TestCase):
    """Fix 1: the four built-in types that were NEEDS FIX only for 'not enough proof' are READY now."""

    def test_sheets_resume_planner_dated_planner_are_ready(self):
        for kind in ("sheets", "resume", "planner", "dated_planner"):
            with self.subTest(kind=kind):
                verdict = check(built(kind))
                self.assertEqual(verdict["verdict"], "ready", verdict["problems"])
                self.assertGreaterEqual(len(verdict["proven"]), truth_check.EXTRA_PROOF_PROMISES)

    def test_the_red_flag_rule_itself_is_unchanged(self):
        self.assertEqual((truth_check.READY_MIN, truth_check.RED_FLAG, truth_check.EXTRA_PROOF_PROMISES), (80, 95, 3))

    def test_every_bible_spec_stays_ready(self):
        import glob
        out = os.path.join(_TMP, "bible")
        os.makedirs(out, exist_ok=True)
        for index, path in enumerate(sorted(glob.glob(os.path.join(ROOT, "specs", "bible", "*.json")))):
            with self.subTest(spec=os.path.basename(path)):
                with open(path, encoding="utf-8") as handle:
                    spec = json.load(handle)
                folder = os.path.join(out, pb.build_product(spec, 300 + index, out))
                verdict = check(folder)
                self.assertEqual(verdict["verdict"], "ready", verdict["problems"])


class PaperSizeTests(unittest.TestCase):
    """Fix 2: a format builder's PDF that IS A4 / US Letter gets the standard name, so the size is proven."""

    def test_two_size_builders_get_standard_names(self):
        for kind in ("habit_tracker", "study_planner", "recipe_book"):
            with self.subTest(kind=kind):
                folder = built(kind)
                pdfs = sorted(name for name in os.listdir(folder) if name.endswith(".pdf"))
                short = kind.replace("_", "-")
                self.assertEqual(pdfs, [f"{short}-A4.pdf", f"{short}-Letter.pdf"])

    def test_size_promises_are_now_proven(self):
        for kind in ("habit_tracker", "study_planner", "recipe_book", "wall_art", "flashcards"):
            with self.subTest(kind=kind):
                verdict = check(built(kind))
                self.assertFalse([p for p in verdict["problems"] if "size:" in p], verdict["problems"])
                self.assertIn("size: A4", verdict["proven"])

    def test_letter_is_proven_where_there_is_a_letter_pdf(self):
        verdict = check(built("habit_tracker"))
        self.assertIn("size: Letter", verdict["proven"])

    def test_one_size_pdf_is_named_for_its_real_size(self):
        self.assertIn("wall-art-A4.pdf", os.listdir(built("wall_art")))
        self.assertIn("flashcards-A4.pdf", os.listdir(built("flashcards")))

    def test_returned_and_listed_file_names_follow_the_rename(self):
        folder = built("habit_tracker")
        text = what_you_get(folder)
        self.assertIn("`habit-tracker-A4.pdf` — PDF, page count: 5, A4", text)
        self.assertNotIn("habit_tracker_a4.pdf", text)
        with open(os.path.join(folder, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        self.assertIn("habit-tracker-Letter.pdf", readme)
        self.assertNotIn("habit_tracker_letter.pdf", readme)

    def test_unproven_size_keeps_its_name(self):
        folder = tempfile.mkdtemp(prefix="odd_", dir=_TMP)
        odd = b"%PDF-1.4\n1 0 obj << /Type /Page /MediaBox [0 0 500 500] >> endobj\n%%EOF"
        with open(os.path.join(folder, "square_card.pdf"), "wb") as handle:
            handle.write(odd)
        self.assertEqual(pb.standard_pdf_names(folder, {"short_name": "cards"}), {})
        self.assertEqual(os.listdir(folder), ["square_card.pdf"])

    def test_mixed_sizes_in_one_pdf_keep_their_name(self):
        folder = tempfile.mkdtemp(prefix="mixed_", dir=_TMP)
        mixed = (b"%PDF-1.4\n1 0 obj << /Type /Page /MediaBox [0 0 595.28 841.89] >> endobj\n"
                 b"2 0 obj << /Type /Page /MediaBox [0 0 612 792] >> endobj\n%%EOF")
        with open(os.path.join(folder, "both.pdf"), "wb") as handle:
            handle.write(mixed)
        self.assertEqual(pb.standard_pdf_names(folder, {"short_name": "x"}), {})

    def test_page_without_mediabox_is_not_trusted(self):
        folder = tempfile.mkdtemp(prefix="nobox_", dir=_TMP)
        with open(os.path.join(folder, "a.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n"
                         b"2 0 obj << /Type /Page /MediaBox [0 0 595.28 841.89] >> endobj\n%%EOF")
        self.assertEqual(pb._verified_pdf(os.path.join(folder, "a.pdf")), (2, None))

    def test_already_standard_names_are_left_alone(self):
        folder = built("planner")
        before = sorted(os.listdir(folder))
        self.assertEqual(pb.standard_pdf_names(folder, {"short_name": "test-adhd"}), {})
        self.assertEqual(sorted(os.listdir(folder)), before)

    def test_two_pdfs_of_one_size_never_overwrite_each_other(self):
        folder = tempfile.mkdtemp(prefix="two_", dir=_TMP)
        a4 = b"%PDF-1.4\n1 0 obj << /Type /Page /MediaBox [0 0 595.28 841.89] >> endobj\n%%EOF"
        for name in ("one_a4.pdf", "two_a4.pdf"):
            with open(os.path.join(folder, name), "wb") as handle:
                handle.write(a4 + name.encode())
        renamed = pb.standard_pdf_names(folder, {"short_name": "pack"})
        self.assertEqual(sorted(renamed.values()), ["pack-2-A4.pdf", "pack-A4.pdf"])
        self.assertEqual(len(os.listdir(folder)), 2)

    def test_text_files_naming_the_old_file_are_updated(self):
        folder = tempfile.mkdtemp(prefix="txt_", dir=_TMP)
        with open(os.path.join(folder, "art_a4.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4\n1 0 obj << /Type /Page /MediaBox [0 0 595.28 841.89] >> endobj\n%%EOF")
        with open(os.path.join(folder, "PRINTING.txt"), "w", encoding="utf-8") as handle:
            handle.write("Print art_a4.pdf at 100%.\n")
        pb.standard_pdf_names(folder, {"short_name": "Art Set!"})
        with open(os.path.join(folder, "PRINTING.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "Print art-set-A4.pdf at 100%.\n")


class PageCountTests(unittest.TestCase):
    """The listing states the page count only when the Truth Checker reads the same pages."""

    def test_planner_page_count_is_stated_and_proven(self):
        folder = built("planner")
        self.assertIn("- 4 printable pages in every PDF", what_you_get(folder))
        self.assertIn("count: 4 pages", check(folder)["proven"])

    def test_unequal_page_counts_say_at_least(self):
        facts = {"pages": 17}
        self.assertEqual(pb._proof_lines("x", facts, [17, 18])[0], "At least 17 printable pages in every PDF")

    def test_no_line_when_the_checker_reads_fewer_pages(self):
        self.assertEqual(pb._proof_lines("x", {"pages": 2}, [5, 5]), [])
        self.assertEqual(pb._proof_lines("x", {"pages": 0}, [5]), [])

    def test_no_line_for_huge_counts(self):
        self.assertEqual(pb._proof_lines("x", {"pages": 120}, [120]), [])

    def test_wall_art_states_the_real_page_count_of_the_file(self):
        folder = built("wall_art")
        real, _size = pb._verified_pdf(os.path.join(folder, "wall-art-A4.pdf"))
        self.assertIn(f"- {real} printable pages in every PDF", what_you_get(folder))
        self.assertLessEqual(real, truth_check.facts(folder)["pages"])
        self.assertIn(f"count: {real} pages", check(folder)["proven"])


class SheetTests(unittest.TestCase):
    """Sheet count and tab names are stated and proven."""

    def test_sheet_count_and_named_tabs(self):
        folder = built("sheets")
        self.assertIn("- 2 sheets in the Excel workbook: Instructions tab, Income Log tab", what_you_get(folder))
        proven = check(folder)["proven"]
        for claim in ("count: 2 sheets", "named part: Instructions tab", "named part: Income Log tab"):
            self.assertIn(claim, proven)

    def test_resume_job_tracker_tabs(self):
        folder = built("resume")
        self.assertIn("2 sheets in the Excel workbook: Instructions tab, Job Applications tab", what_you_get(folder))

    def test_old_tab_line_is_kept_unchanged(self):
        self.assertIn("— Excel (.xlsx); tabs: Instructions, Income Log", what_you_get(built("sheets")))

    def test_tabs_that_are_feature_claims_are_not_named(self):
        for tab in ("Dashboard", "Charts", "Calendar View", "Kanban"):
            with self.subTest(tab=tab):
                self.assertIsNone(pb._plain_tab_name(tab, "sheets"))

    def test_odd_or_generic_tab_names_are_not_named(self):
        for tab in ("data_2026", "notes", "Sheet", "The Tab", "Budget (old)", "A" * 3 + " " * 2, ""):
            with self.subTest(tab=tab):
                self.assertIsNone(pb._plain_tab_name(tab, "sheets"))

    def test_plain_names_are_kept(self):
        for tab in ("Ledger", "Monthly Budget", "Start Here", "Bills"):
            with self.subTest(tab=tab):
                self.assertEqual(pb._plain_tab_name(tab, "sheets"), tab)

    def test_resume_dashboard_tab_is_never_named(self):
        self.assertIsNone(pb._plain_tab_name("Dashboard", "resume"))


class FeatureTests(unittest.TestCase):
    """Formulas, drop-downs and charts are stated only when the workbook really has them."""

    def test_feature_lines_follow_the_facts(self):
        base = {"pages": 0, "sheets": ["Ledger"]}
        self.assertEqual(pb._proof_lines("x", base, []), ["1 sheets in the Excel workbook: Ledger tab"])
        lines = pb._proof_lines("x", dict(base, formulas=4, validations=2, charts=1), [])
        self.assertIn("Formulas built in: totals calculate automatically", lines)
        self.assertIn("Drop-down lists in the workbook", lines)
        self.assertIn("Charts in the workbook", lines)

    def test_formula_line_is_proven_by_the_checker(self):
        import glob
        out = os.path.join(_TMP, "formulas")
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(ROOT, "specs", "bible", "paycheck-budget-planner.json"), encoding="utf-8") as handle:
            spec = json.load(handle)
        folder = os.path.join(out, pb.build_product(spec, 333, out))
        text = what_you_get(folder)
        verdict = check(folder)
        if "Formulas built in" in text:
            self.assertIn("formulas / automatic calculation", verdict["proven"])
        if "Drop-down lists" in text:
            self.assertIn("drop-down menus", verdict["proven"])
        if "Charts in the workbook" in text:
            self.assertIn("charts / progress bars", verdict["proven"])
        self.assertEqual(verdict["verdict"], "ready", verdict["problems"])

    def test_no_feature_lines_for_a_pdf_only_pack(self):
        text = what_you_get(built("planner"))
        for line in ("Formulas", "Drop-down", "Charts"):
            self.assertNotIn(line, text)


class HonestyTests(unittest.TestCase):
    """Every added line is proven; nothing is promised that the files do not have."""

    def test_no_unmet_promise_in_any_product_type(self):
        for kind in kinds.ALL_KINDS:
            with self.subTest(kind=kind):
                verdict = check(built(kind))
                self.assertFalse([p for p in verdict["problems"] if p.startswith("Promises ")], verdict["problems"])

    def test_added_lines_never_carry_money(self):
        import upload_pack
        for kind in kinds.ALL_KINDS:
            with self.subTest(kind=kind):
                upload_pack._check_money(what_you_get(built(kind)), "listing")

    def test_truth_check_file_is_untouched_by_this_task(self):
        import subprocess
        result = subprocess.run(["git", "-C", ROOT, "diff", "--quiet", "HEAD", "--", "truth_check.py"], check=False)
        self.assertEqual(result.returncode, 0, "truth_check.py must not be changed (never weaken the checker)")

    def test_zip_members_are_untouched(self):
        folder = built("notion")
        for name in os.listdir(folder):
            if name.endswith(".zip"):
                with zipfile.ZipFile(os.path.join(folder, name)) as archive:
                    self.assertIsNone(archive.testzip())

    def test_survey_counts_ready_types(self):
        ready = [kind for kind in kinds.ALL_KINDS if check(built(kind))["verdict"] == "ready"]
        for kind in ("sheets", "resume", "planner", "dated_planner"):
            self.assertIn(kind, ready)


if __name__ == "__main__":
    unittest.main()
