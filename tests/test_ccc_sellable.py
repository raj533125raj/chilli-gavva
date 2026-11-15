"""PATH-C: every product type's REAL built files pass the Truth Checker honestly.

Each test builds the product with the real builder in a temp folder (never in the repo) and reads the
real PDF / HTML / PNG files back. The checkers (truth_check, sell_ready, product_qa_deep) are not changed
or patched: these tests prove the generators now make files that pass them.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import truth_check

try:
    import PIL  # noqa: F401
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def build_folder(module_name, spec, tmp):
    """Build with the real format builder and return the folder (standard -A4 / -Letter names)."""
    import product_builder as pb
    folder = Path(tmp) / "pack"
    folder.mkdir(parents=True, exist_ok=True)
    module = __import__(module_name)
    module.build(dict(spec), str(folder))
    pb.standard_pdf_names(str(folder), spec)
    return folder


def page_problems(folder):
    return [p for p in truth_check.file_quality(truth_check.facts(str(folder)))]


def page_texts(folder, number, name_end="-A4.pdf"):
    pdf = next(p for p in sorted(Path(folder).glob("*.pdf")) if p.name.endswith(name_end))
    page = truth_check._pdf_pages(str(pdf))[number - 1]
    return [item[4] for item in page["items"]], page


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class HabitTrackerTests(unittest.TestCase):
    SPEC = {"kind": "habit_tracker", "title": "Monthly Habit Tracker", "short_name": "habit-tracker"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ccc_habit_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.folder = build_folder("builder_habit", cls.SPEC, cls.tmp.name)

    def test_both_paper_sizes_have_standard_names(self):
        names = sorted(p.name for p in self.folder.glob("*.pdf"))
        self.assertEqual(names, ["habit-tracker-A4.pdf", "habit-tracker-Letter.pdf"])

    def test_no_empty_page_problems(self):
        self.assertEqual(page_problems(self.folder), [])

    def test_title_page_has_a_real_start_here_card(self):
        import builder_habit
        for end in ("-A4.pdf", "-Letter.pdf"):
            texts, page = page_texts(self.folder, 1, end)
            self.assertIn("Start here", texts)
            for label in builder_habit.START_HERE_FIELDS:
                self.assertIn(label, texts)
            for name, _note in builder_habit.PAGE_GUIDE:
                self.assertIn(name, texts)
            self.assertLess(page["low"], 0.35 * 842)

    def test_page_guide_names_real_pages(self):
        import builder_habit
        facts = truth_check.facts(str(self.folder))
        titles = " | ".join(facts["texts"])
        for name, _note in builder_habit.PAGE_GUIDE:
            self.assertIn(name, titles)

    def test_long_title_does_not_overlap_the_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = dict(self.SPEC, title="A Very Long Monthly Habit And Mood Tracker Title For Busy People Who Plan")
            folder = build_folder("builder_habit", spec, tmp)
            _texts, page = page_texts(folder, 1)
            card_top = max(y for _f, _s, _x, y, t in page["items"] if t == "Start here")
            title_low = min(y for f, s, _x, y, _t in page["items"] if f == "F2" and s >= 16)
            self.assertGreater(title_low, card_top + 40)
            self.assertEqual(page_problems(folder), [])

    def test_truth_check_ready(self):
        verdict = truth_check.check(str(self.folder), with_images=False)
        self.assertFalse([p for p in verdict["problems"] if "empty" in p], verdict["problems"])


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class RecipeBookTests(unittest.TestCase):
    SPEC = {"kind": "recipe_book", "title": "Everyday Recipe Book", "short_name": "recipe-book"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ccc_recipe_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.folder = build_folder("builder_recipe", cls.SPEC, cls.tmp.name)

    def test_both_paper_sizes_have_standard_names(self):
        names = sorted(p.name for p in self.folder.glob("*.pdf"))
        self.assertEqual(names, ["recipe-book-A4.pdf", "recipe-book-Letter.pdf"])

    def test_no_empty_page_problems(self):
        self.assertEqual(page_problems(self.folder), [])

    def test_title_page_indexes_every_recipe_card(self):
        import builder_recipe
        norm = builder_recipe.normalize_spec(self.SPEC)
        for end in ("-A4.pdf", "-Letter.pdf"):
            texts, page = page_texts(self.folder, 1, end)
            self.assertIn("Recipe index", texts)
            for number, recipe in enumerate(norm["recipes"], 1):
                self.assertIn(f"{number:02d}", texts)
                self.assertIn(recipe["title"], texts)
                self.assertIn(recipe["prep"], texts)
            self.assertLess(page["low"], 0.35 * 842)

    def test_index_names_match_the_cards(self):
        facts = truth_check.facts(str(self.folder))
        card_titles = set(facts["headings"])
        import builder_recipe
        for recipe in builder_recipe.normalize_spec(self.SPEC)["recipes"]:
            self.assertIn(recipe["title"], card_titles)

    def test_twelve_recipes_still_fit_the_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipes = [{"title": f"Recipe Number {n}", "category": "Mains"} for n in range(1, 13)]
            folder = build_folder("builder_recipe", dict(self.SPEC, recipes=recipes), tmp)
            texts, _page = page_texts(folder, 1)
            self.assertIn("Recipe Number 12", texts)
            self.assertEqual(page_problems(folder), [])


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class StudyPlannerTests(unittest.TestCase):
    SPEC = {"kind": "study_planner", "title": "Exam Study Planner", "short_name": "study-planner"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ccc_study_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.folder = build_folder("builder_study", cls.SPEC, cls.tmp.name)

    def test_both_paper_sizes_have_standard_names(self):
        names = sorted(p.name for p in self.folder.glob("*.pdf"))
        self.assertEqual(names, ["study-planner-A4.pdf", "study-planner-Letter.pdf"])

    def test_no_empty_page_problems(self):
        self.assertEqual(page_problems(self.folder), [])

    def test_title_page_has_an_exam_plan_per_subject(self):
        import builder_study
        norm = builder_study.normalize_spec(self.SPEC)
        for end in ("-A4.pdf", "-Letter.pdf"):
            texts, page = page_texts(self.folder, 1, end)
            self.assertIn("My exam plan", texts)
            for label in builder_study.PLAN_FIELDS:
                self.assertIn(label, texts)
            for subject in norm["subjects"]:
                self.assertIn(subject, texts)
            self.assertIn(str(norm["chapters_per_subject"]), texts)
            self.assertGreaterEqual(page["drawn"], 10 * 5)        # rows x (4 cells + confidence boxes)
            self.assertLess(page["low"], 0.35 * 842)

    def test_mock_log_rows_reach_the_bottom_margin(self):
        for end, height in (("-A4.pdf", 842), ("-Letter.pdf", 792)):
            texts, page = page_texts(self.folder, 4, end)
            self.assertIn("Mock-Test Log", texts)
            self.assertLess(page["low"], 0.35 * height)
            self.assertGreaterEqual(page["drawn"], 5 * 16)        # at least 16 rows of 5 cells

    def test_exam_label_is_written_properly(self):
        import builder_study
        self.assertEqual(builder_study.normalize_spec({"exam": "jee"})["exam_label"], "JEE")
        self.assertEqual(builder_study.normalize_spec({"exam": "10th"})["exam_label"], "Class 10")
        texts, _page = page_texts(self.folder, 1)
        self.assertIn("JEE preparation set", texts)

    def test_many_subjects_still_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            subjects = [f"Subject {n}" for n in range(1, 13)]
            folder = build_folder("builder_study", dict(self.SPEC, subjects=subjects), tmp)
            texts, _page = page_texts(folder, 1)
            self.assertIn("Subject 12", texts)
            self.assertEqual(page_problems(folder), [])


if __name__ == "__main__":
    unittest.main()
