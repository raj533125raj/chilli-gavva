"""GM1 builder tests: habit tracker, recipe book, and study planner packs."""

import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import builder_habit
import builder_recipe
import builder_study

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    HAS_PILLOW = False


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pdf_pages(path):
    match = re.search(rb"/Count (\d+)", Path(path).read_bytes())
    return int(match.group(1)) if match else None


def _pack_text(root):
    root = Path(root)
    return "\n".join((root / name).read_text(encoding="utf-8") for name in ("README.txt", "LICENSE.txt", "manifest.json"))


@unittest.skipUnless(HAS_PILLOW, "Pillow is required for printable pack builds")
class HabitTrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gm1_habit_test_"))
        cls.files = builder_habit.build({}, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_format_name(self):
        self.assertEqual(builder_habit.FORMAT, "habit_tracker")

    def test_keywords_cover_habit_and_mood(self):
        keywords = {word.lower() for word in builder_habit.KEYWORDS}
        self.assertIn("habit tracker", keywords)
        self.assertIn("mood tracker", keywords)
        self.assertIn("streak tracker", keywords)

    def test_paper_sizes_are_a4_and_letter(self):
        self.assertEqual(set(builder_habit.PAPER_SIZES), {"a4", "letter"})
        self.assertEqual(set(builder_habit.PAPER_SIZES.values()), {"A4", "Letter"})

    def test_default_habits_present(self):
        norm = builder_habit.normalize_spec({})
        self.assertGreaterEqual(len(norm["habits"]), 5)
        self.assertIn("Drink water", norm["habits"])

    def test_default_moods_have_five_labels(self):
        norm = builder_habit.normalize_spec({})
        self.assertEqual(len(norm["moods"]), 5)
        self.assertIn("Great", norm["moods"])

    def test_default_month_is_deterministic(self):
        norm = builder_habit.normalize_spec({})
        self.assertEqual((norm["year"], norm["month"]), (2027, 1))
        self.assertEqual(norm["days_in_month"], 31)

    def test_february_leap_year_has_29_days(self):
        norm = builder_habit.normalize_spec({"year": 2028, "month": 2})
        self.assertEqual(norm["days_in_month"], 29)

    def test_february_non_leap_year_has_28_days(self):
        norm = builder_habit.normalize_spec({"year": 2027, "month": 2})
        self.assertEqual(norm["days_in_month"], 28)

    def test_april_has_30_days(self):
        norm = builder_habit.normalize_spec({"month": 4})
        self.assertEqual(norm["days_in_month"], 30)

    def test_january_has_31_days(self):
        norm = builder_habit.normalize_spec({"month": 1})
        self.assertEqual(norm["days_in_month"], 31)

    def test_month_name_matches_number(self):
        self.assertEqual(builder_habit.normalize_spec({"month": 9})["month_name"], "September")

    def test_first_weekday_is_valid(self):
        norm = builder_habit.normalize_spec({"year": 2028, "month": 2})
        self.assertIn(norm["first_weekday"], range(7))

    def test_invalid_month_low_raises(self):
        with self.assertRaises(ValueError):
            builder_habit.normalize_spec({"month": 0})

    def test_invalid_month_high_raises(self):
        with self.assertRaises(ValueError):
            builder_habit.normalize_spec({"month": 13})

    def test_invalid_year_low_raises(self):
        with self.assertRaises(ValueError):
            builder_habit.normalize_spec({"year": 1899})

    def test_invalid_year_high_raises(self):
        with self.assertRaises(ValueError):
            builder_habit.normalize_spec({"year": 2201})

    def test_non_integer_year_raises(self):
        with self.assertRaises(ValueError):
            builder_habit.normalize_spec({"year": "soon"})

    def test_non_integer_month_raises(self):
        with self.assertRaises(ValueError):
            builder_habit.normalize_spec({"month": "soon"})

    def test_spec_must_be_dictionary(self):
        with self.assertRaises(ValueError):
            builder_habit.normalize_spec(["not", "a", "dict"])

    def test_none_spec_uses_defaults(self):
        self.assertEqual(builder_habit.normalize_spec(None)["title"], builder_habit.DEFAULT_TITLE)

    def test_custom_habits_are_deduplicated(self):
        norm = builder_habit.normalize_spec({"habits": ["Walk", "Walk", "Read", " "]})
        self.assertEqual(norm["habits"], ["Walk", "Read"])

    def test_habits_are_capped_at_eight(self):
        norm = builder_habit.normalize_spec({"habits": [f"Habit {i}" for i in range(20)]})
        self.assertEqual(len(norm["habits"]), 8)

    def test_custom_moods_are_kept(self):
        norm = builder_habit.normalize_spec({"moods": ["Calm", "Busy", "Tired"]})
        self.assertEqual(norm["moods"], ["Calm", "Busy", "Tired"])

    def test_build_returns_sorted_names(self):
        self.assertEqual(self.files, sorted(self.files))

    def test_all_expected_files_exist(self):
        expected = {
            "habit_tracker_a4.pdf", "habit_tracker_letter.pdf",
            "cover.png", "preview_1.png", "preview_2.png", "preview_3.png",
            "README.txt", "LICENSE.txt", "manifest.json",
        }
        self.assertEqual(set(self.files), expected)
        for name in expected:
            self.assertTrue((self.tmp / name).is_file(), name)
            self.assertGreater((self.tmp / name).stat().st_size, 0, name)

    def test_no_duplicate_names(self):
        self.assertEqual(len(self.files), len(set(self.files)))

    def test_a4_pdf_has_pdf_header(self):
        self.assertTrue((self.tmp / "habit_tracker_a4.pdf").read_bytes().startswith(b"%PDF"))

    def test_letter_pdf_has_pdf_header(self):
        self.assertTrue((self.tmp / "habit_tracker_letter.pdf").read_bytes().startswith(b"%PDF"))

    def test_a4_pdf_has_five_pages(self):
        self.assertEqual(_pdf_pages(self.tmp / "habit_tracker_a4.pdf"), 5)

    def test_letter_pdf_has_five_pages(self):
        self.assertEqual(_pdf_pages(self.tmp / "habit_tracker_letter.pdf"), 5)

    def test_pdf_contains_title(self):
        data = (self.tmp / "habit_tracker_a4.pdf").read_bytes()
        self.assertIn(b"Monthly Habit & Mood Tracker", data)

    def test_cover_is_valid_png(self):
        self.assertEqual((self.tmp / "cover.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        with Image.open(self.tmp / "cover.png") as image:
            self.assertEqual(image.format, "PNG")

    def test_cover_dimensions(self):
        with Image.open(self.tmp / "cover.png") as image:
            self.assertEqual(image.size, (1200, 800))

    def test_cover_and_previews_are_visually_distinct(self):
        hashes = {_sha256(self.tmp / name) for name in ("cover.png", "preview_1.png", "preview_2.png", "preview_3.png")}
        self.assertEqual(len(hashes), 4)

    def test_preview_images_open(self):
        for index in range(1, 4):
            with Image.open(self.tmp / f"preview_{index}.png") as image:
                self.assertEqual(image.size, (1200, 800))

    def test_readme_mentions_both_paper_sizes(self):
        text = (self.tmp / "README.txt").read_text(encoding="utf-8")
        self.assertIn("A4", text)
        self.assertIn("US Letter", text)

    def test_readme_has_print_instructions(self):
        text = (self.tmp / "README.txt").read_text(encoding="utf-8")
        self.assertIn("HOW TO PRINT", text)
        self.assertIn("LICENSE", text)

    def test_license_is_personal_use_only(self):
        text = (self.tmp / "LICENSE.txt").read_text(encoding="utf-8")
        self.assertIn("personal use only", text.lower())
        self.assertIn("may not resell", text.lower())

    def test_manifest_is_valid_json(self):
        manifest = json.loads((self.tmp / "manifest.json").read_text(encoding="utf-8"))
        self.assertIsInstance(manifest, dict)

    def test_manifest_records_format_and_month(self):
        manifest = json.loads((self.tmp / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["format"], "habit_tracker")
        self.assertEqual(manifest["month"], 1)
        self.assertEqual(manifest["year"], 2027)
        self.assertEqual(manifest["days_in_month"], 31)

    def test_manifest_files_match_build_output(self):
        manifest = json.loads((self.tmp / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["files"], self.files)

    def test_manifest_lists_paper_sizes(self):
        manifest = json.loads((self.tmp / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["paper_sizes"], ["a4", "letter"])
        self.assertEqual(manifest["pages_per_pdf"], 5)

    def test_build_is_deterministic(self):
        with tempfile.TemporaryDirectory() as other:
            builder_habit.build({}, other)
            for name in self.files:
                self.assertEqual(_sha256(self.tmp / name), _sha256(Path(other) / name), name)

    def test_a_different_month_changes_pdf(self):
        with tempfile.TemporaryDirectory() as other:
            builder_habit.build({"month": 2}, other)
            self.assertNotEqual(
                _sha256(self.tmp / "habit_tracker_a4.pdf"),
                _sha256(Path(other) / "habit_tracker_a4.pdf"),
            )

    def test_long_title_is_accepted(self):
        title = "My Very Long Personal Habit And Mood Tracker For A Focused Year"
        with tempfile.TemporaryDirectory() as other:
            builder_habit.build({"title": title}, other)
            manifest = json.loads((Path(other) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["title"], title)

    def test_output_directory_is_created(self):
        with tempfile.TemporaryDirectory() as parent:
            nested = Path(parent) / "one" / "two"
            files = builder_habit.build({"month": 3}, nested)
            self.assertIn("habit_tracker_a4.pdf", files)
            self.assertTrue((nested / "habit_tracker_a4.pdf").is_file())

    def test_text_files_have_no_currency_amounts(self):
        self.assertIsNone(re.search(r"(USD|INR|Rs\.?)\s*\d|\d[\d,.]*\s*(USD|INR|Rs\.?)", _pack_text(self.tmp)))

    def test_manifest_habits_match_normalized_spec(self):
        manifest = json.loads((self.tmp / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["habits"], builder_habit.normalize_spec({})["habits"])



@unittest.skipUnless(HAS_PILLOW, "Pillow is required for printable pack builds")
class RecipeBookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gm1_recipe_test_"))
        cls.files = builder_recipe.build({}, cls.tmp)
        cls.manifest = json.loads((cls.tmp / "manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_format_name(self):
        self.assertEqual(builder_recipe.FORMAT, "recipe_book")

    def test_keywords_cover_recipe_and_meal_planning(self):
        keywords = {word.lower() for word in builder_recipe.KEYWORDS}
        self.assertIn("recipe book", keywords)
        self.assertIn("recipe cards", keywords)
        self.assertIn("meal planner", keywords)
        self.assertIn("grocery list", keywords)

    def test_paper_sizes_are_a4_and_letter(self):
        self.assertEqual(set(builder_recipe.PAPER_SIZES), {"a4", "letter"})

    def test_supported_recipes_per_page(self):
        self.assertEqual(builder_recipe.RECIPES_PER_PAGE, (1, 2, 4))

    def test_cuisine_sets_include_indian_and_global(self):
        self.assertIn("indian", builder_recipe.CUISINE_SETS)
        self.assertIn("global", builder_recipe.CUISINE_SETS)
        self.assertIn("mixed", builder_recipe.CUISINE_SETS)

    def test_indian_categories_are_curated(self):
        self.assertIn("Dal & Curry", builder_recipe.CUISINE_SETS["indian"])
        self.assertIn("Roti & Bread", builder_recipe.CUISINE_SETS["indian"])

    def test_global_categories_are_curated(self):
        self.assertIn("Salads", builder_recipe.CUISINE_SETS["global"])
        self.assertIn("Soups", builder_recipe.CUISINE_SETS["global"])

    def test_diet_tags_include_veg_and_non_veg(self):
        self.assertIn("Veg", builder_recipe.DIET_TAGS)
        self.assertIn("Non-veg", builder_recipe.DIET_TAGS)

    def test_default_recipes_have_eight_cards(self):
        norm = builder_recipe.normalize_spec({})
        self.assertEqual(len(norm["recipes"]), 8)

    def test_default_recipe_has_ingredients_and_method(self):
        recipe = builder_recipe.normalize_spec({})["recipes"][0]
        self.assertGreaterEqual(len(recipe["ingredients"]), 3)
        self.assertGreaterEqual(len(recipe["method"]), 3)

    def test_defaults_are_deterministic(self):
        norm = builder_recipe.normalize_spec({})
        self.assertEqual(norm["cuisine"], "mixed")
        self.assertEqual(norm["recipes_per_page"], 2)
        self.assertEqual(norm["title"], builder_recipe.DEFAULT_TITLE)

    def test_none_spec_uses_defaults(self):
        self.assertEqual(builder_recipe.normalize_spec(None)["cuisine"], "mixed")

    def test_spec_must_be_dictionary(self):
        with self.assertRaises(ValueError):
            builder_recipe.normalize_spec("not a dict")

    def test_unknown_cuisine_falls_back_to_mixed(self):
        self.assertEqual(builder_recipe.normalize_spec({"cuisine": "martian"})["cuisine"], "mixed")

    def test_cuisine_categories_match_the_set(self):
        norm = builder_recipe.normalize_spec({"cuisine": "indian"})
        self.assertEqual(norm["categories"], builder_recipe.CUISINE_SETS["indian"])

    def test_half_page_cards_are_supported(self):
        self.assertEqual(builder_recipe.normalize_spec({"recipes_per_page": 1})["recipes_per_page"], 1)

    def test_four_cards_per_page_are_supported(self):
        self.assertEqual(builder_recipe.normalize_spec({"recipes_per_page": 4})["recipes_per_page"], 4)

    def test_invalid_recipes_per_page_raises(self):
        with self.assertRaises(ValueError):
            builder_recipe.normalize_spec({"recipes_per_page": 3})

    def test_non_integer_recipes_per_page_raises(self):
        with self.assertRaises(ValueError):
            builder_recipe.normalize_spec({"recipes_per_page": "two"})

    def test_custom_recipes_are_normalized(self):
        norm = builder_recipe.normalize_spec({"recipes": [{"title": "Test Dish", "diet": "non veg"}]})
        self.assertEqual(norm["recipes"][0]["title"], "Test Dish")
        self.assertEqual(norm["recipes"][0]["diet"], "Non-veg")

    def test_recipe_strings_are_accepted(self):
        norm = builder_recipe.normalize_spec({"recipes": ["Simple Soup"]})
        self.assertEqual(norm["recipes"][0]["title"], "Simple Soup")

    def test_recipes_are_capped_at_twelve(self):
        norm = builder_recipe.normalize_spec({"recipes": [{"title": f"Recipe {i}"} for i in range(20)]})
        self.assertEqual(len(norm["recipes"]), 12)

    def test_recipe_ingredients_are_capped_at_eight(self):
        norm = builder_recipe.normalize_spec({"recipes": [{"title": "Big", "ingredients": [f"Item {i}" for i in range(20)]}]})
        self.assertEqual(len(norm["recipes"][0]["ingredients"]), 8)

    def test_recipe_method_is_capped_at_six(self):
        norm = builder_recipe.normalize_spec({"recipes": [{"title": "Big", "method": [f"Step {i}" for i in range(20)]}]})
        self.assertEqual(len(norm["recipes"][0]["method"]), 6)

    def test_default_aisles_are_present(self):
        norm = builder_recipe.normalize_spec({})
        self.assertIn("Produce", norm["aisles"])
        self.assertIn("Spices", norm["aisles"])

    def test_custom_aisles_are_kept(self):
        norm = builder_recipe.normalize_spec({"aisles": ["Fresh", "Dry Goods"]})
        self.assertEqual(norm["aisles"], ["Fresh", "Dry Goods"])

    def test_custom_pantry_items_are_kept(self):
        norm = builder_recipe.normalize_spec({"pantry_items": ["Rice", "Salt"]})
        self.assertEqual(norm["pantry_items"], ["Rice", "Salt"])

    def test_build_returns_sorted_names(self):
        self.assertEqual(self.files, sorted(self.files))

    def test_all_expected_files_exist(self):
        expected = {
            "recipe_book_a4.pdf", "recipe_book_letter.pdf",
            "cover.png", "preview_1.png", "preview_2.png", "preview_3.png",
            "README.txt", "LICENSE.txt", "manifest.json",
        }
        self.assertEqual(set(self.files), expected)
        for name in expected:
            self.assertTrue((self.tmp / name).is_file(), name)
            self.assertGreater((self.tmp / name).stat().st_size, 0, name)

    def test_no_duplicate_names(self):
        self.assertEqual(len(self.files), len(set(self.files)))

    def test_a4_pdf_has_pdf_header(self):
        self.assertTrue((self.tmp / "recipe_book_a4.pdf").read_bytes().startswith(b"%PDF"))

    def test_letter_pdf_has_pdf_header(self):
        self.assertTrue((self.tmp / "recipe_book_letter.pdf").read_bytes().startswith(b"%PDF"))

    def test_two_card_layout_has_eight_pages(self):
        self.assertEqual(_pdf_pages(self.tmp / "recipe_book_a4.pdf"), 8)

    def test_letter_pdf_has_same_page_count(self):
        self.assertEqual(_pdf_pages(self.tmp / "recipe_book_letter.pdf"), 8)

    def test_one_card_layout_has_twelve_pages(self):
        with tempfile.TemporaryDirectory() as other:
            builder_recipe.build({"recipes_per_page": 1}, other)
            self.assertEqual(_pdf_pages(Path(other) / "recipe_book_a4.pdf"), 12)

    def test_four_card_layout_has_six_pages(self):
        with tempfile.TemporaryDirectory() as other:
            builder_recipe.build({"recipes_per_page": 4}, other)
            self.assertEqual(_pdf_pages(Path(other) / "recipe_book_a4.pdf"), 6)

    def test_pdf_contains_title(self):
        self.assertIn(b"Everyday Recipe Book & Meal Planner", (self.tmp / "recipe_book_a4.pdf").read_bytes())

    def test_cover_is_valid_png(self):
        self.assertEqual((self.tmp / "cover.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        with Image.open(self.tmp / "cover.png") as image:
            self.assertEqual(image.format, "PNG")

    def test_cover_dimensions(self):
        with Image.open(self.tmp / "cover.png") as image:
            self.assertEqual(image.size, (1200, 800))

    def test_cover_and_previews_are_visually_distinct(self):
        hashes = {_sha256(self.tmp / name) for name in ("cover.png", "preview_1.png", "preview_2.png", "preview_3.png")}
        self.assertEqual(len(hashes), 4)

    def test_preview_images_open(self):
        for index in range(1, 4):
            with Image.open(self.tmp / f"preview_{index}.png") as image:
                self.assertEqual(image.size, (1200, 800))

    def test_readme_mentions_both_paper_sizes(self):
        text = (self.tmp / "README.txt").read_text(encoding="utf-8")
        self.assertIn("A4", text)
        self.assertIn("US Letter", text)

    def test_readme_has_print_instructions(self):
        text = (self.tmp / "README.txt").read_text(encoding="utf-8")
        self.assertIn("HOW TO PRINT", text)
        self.assertIn("LICENSE", text)

    def test_license_is_personal_use_only(self):
        text = (self.tmp / "LICENSE.txt").read_text(encoding="utf-8")
        self.assertIn("personal use only", text.lower())

    def test_manifest_records_format_and_counts(self):
        self.assertEqual(self.manifest["format"], "recipe_book")
        self.assertEqual(self.manifest["recipe_count"], 8)
        self.assertEqual(self.manifest["recipes_per_page"], 2)

    def test_manifest_files_match_build_output(self):
        self.assertEqual(self.manifest["files"], self.files)

    def test_manifest_lists_categories_and_aisles(self):
        self.assertEqual(self.manifest["categories"], builder_recipe.CUISINE_SETS["mixed"])
        self.assertIn("Produce", self.manifest["aisles"])

    def test_manifest_lists_paper_sizes(self):
        self.assertEqual(self.manifest["paper_sizes"], ["a4", "letter"])
        self.assertEqual(self.manifest["pages_per_pdf"], 8)

    def test_build_is_deterministic(self):
        with tempfile.TemporaryDirectory() as other:
            builder_recipe.build({}, other)
            for name in self.files:
                self.assertEqual(_sha256(self.tmp / name), _sha256(Path(other) / name), name)

    def test_a_different_cuisine_changes_pdf(self):
        with tempfile.TemporaryDirectory() as other:
            builder_recipe.build({"cuisine": "indian"}, other)
            self.assertNotEqual(
                _sha256(self.tmp / "recipe_book_a4.pdf"),
                _sha256(Path(other) / "recipe_book_a4.pdf"),
            )

    def test_long_title_is_accepted(self):
        title = "My Family Recipe Book And Weekly Meal Planner For Busy Weeknights"
        with tempfile.TemporaryDirectory() as other:
            builder_recipe.build({"title": title}, other)
            manifest = json.loads((Path(other) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["title"], title)

    def test_output_directory_is_created(self):
        with tempfile.TemporaryDirectory() as parent:
            nested = Path(parent) / "one" / "two"
            files = builder_recipe.build({"recipes_per_page": 4}, nested)
            self.assertIn("recipe_book_a4.pdf", files)
            self.assertTrue((nested / "recipe_book_a4.pdf").is_file())

    def test_text_files_have_no_currency_amounts(self):
        self.assertIsNone(re.search(r"(USD|INR|Rs\.?)\s*\d|\d[\d,.]*\s*(USD|INR|Rs\.?)", _pack_text(self.tmp)))

    def test_recipe_titles_appear_in_manifest(self):
        titles = [recipe["title"] for recipe in builder_recipe.normalize_spec({})["recipes"]]
        self.assertEqual(self.manifest["recipes"], titles)



@unittest.skipUnless(HAS_PILLOW, "Pillow is required for printable pack builds")
class StudyPlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gm1_study_test_"))
        cls.files = builder_study.build({}, cls.tmp)
        cls.manifest = json.loads((cls.tmp / "manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_format_name(self):
        self.assertEqual(builder_study.FORMAT, "study_planner")

    def test_keywords_cover_study_and_exam_planning(self):
        keywords = {word.lower() for word in builder_study.KEYWORDS}
        self.assertIn("study planner", keywords)
        self.assertIn("exam planner", keywords)
        self.assertIn("revision timetable", keywords)
        self.assertIn("chapter tracker", keywords)

    def test_paper_sizes_are_a4_and_letter(self):
        self.assertEqual(set(builder_study.PAPER_SIZES), {"a4", "letter"})

    def test_subject_sets_cover_indian_exams(self):
        self.assertEqual(set(builder_study.SUBJECT_SETS), {"class_10", "inter", "eamcet", "jee", "neet"})

    def test_class_10_subjects_are_present(self):
        self.assertIn("Maths", builder_study.SUBJECT_SETS["class_10"])
        self.assertIn("Science", builder_study.SUBJECT_SETS["class_10"])

    def test_inter_sets_include_mpc_subjects(self):
        self.assertIn("Maths A", builder_study.SUBJECT_SETS["inter"])
        self.assertIn("Physics", builder_study.SUBJECT_SETS["inter"])

    def test_eamcet_set_has_six_subjects(self):
        self.assertEqual(len(builder_study.SUBJECT_SETS["eamcet"]), 6)

    def test_jee_default_has_three_subjects(self):
        self.assertEqual(builder_study.normalize_spec({})["subjects"], ["Physics", "Chemistry", "Maths"])

    def test_neet_default_includes_biology(self):
        self.assertEqual(builder_study.normalize_spec({"exam": "neet"})["subjects"][-1], "Biology")

    def test_exam_alias_10th_maps_to_class_10(self):
        self.assertEqual(builder_study.normalize_spec({"exam": "10th"})["exam"], "class_10")

    def test_exam_alias_mpc_maps_to_inter(self):
        self.assertEqual(builder_study.normalize_spec({"exam": "mpc"})["exam"], "inter")

    def test_exam_alias_emcet_maps_to_eamcet(self):
        self.assertEqual(builder_study.normalize_spec({"exam": "emcet"})["exam"], "eamcet")

    def test_unknown_exam_falls_back_to_jee(self):
        self.assertEqual(builder_study.normalize_spec({"exam": "olympiad"})["exam"], "jee")

    def test_none_spec_uses_defaults(self):
        norm = builder_study.normalize_spec(None)
        self.assertEqual(norm["title"], builder_study.DEFAULT_TITLE)
        self.assertEqual(norm["exam"], "jee")

    def test_spec_must_be_dictionary(self):
        with self.assertRaises(ValueError):
            builder_study.normalize_spec(["not", "a", "dict"])

    def test_custom_subjects_are_kept(self):
        norm = builder_study.normalize_spec({"subjects": ["Botany", "Zoology"]})
        self.assertEqual(norm["subjects"], ["Botany", "Zoology"])

    def test_custom_subjects_are_deduplicated(self):
        norm = builder_study.normalize_spec({"subjects": ["Maths", "Maths", "Physics"]})
        self.assertEqual(norm["subjects"], ["Maths", "Physics"])

    def test_subjects_are_capped_at_twelve(self):
        norm = builder_study.normalize_spec({"subjects": [f"Subject {i}" for i in range(20)]})
        self.assertEqual(len(norm["subjects"]), 12)

    def test_default_chapters_are_twelve(self):
        self.assertEqual(builder_study.normalize_spec({})["chapters_per_subject"], 12)

    def test_chapters_accept_lower_bound(self):
        self.assertEqual(builder_study.normalize_spec({"chapters_per_subject": 6})["chapters_per_subject"], 6)

    def test_chapters_accept_upper_bound(self):
        self.assertEqual(builder_study.normalize_spec({"chapters_per_subject": 20})["chapters_per_subject"], 20)

    def test_chapters_below_six_raise(self):
        with self.assertRaises(ValueError):
            builder_study.normalize_spec({"chapters_per_subject": 5})

    def test_chapters_above_twenty_raise(self):
        with self.assertRaises(ValueError):
            builder_study.normalize_spec({"chapters_per_subject": 21})

    def test_non_integer_chapters_raise(self):
        with self.assertRaises(ValueError):
            builder_study.normalize_spec({"chapters_per_subject": "many"})

    def test_default_pomodoro_cycles_are_eight(self):
        self.assertEqual(builder_study.normalize_spec({})["pomodoro_cycles"], 8)

    def test_pomodoro_cycles_accept_lower_bound(self):
        self.assertEqual(builder_study.normalize_spec({"pomodoro_cycles": 4})["pomodoro_cycles"], 4)

    def test_pomodoro_cycles_accept_upper_bound(self):
        self.assertEqual(builder_study.normalize_spec({"pomodoro_cycles": 12})["pomodoro_cycles"], 12)

    def test_pomodoro_cycles_below_four_raise(self):
        with self.assertRaises(ValueError):
            builder_study.normalize_spec({"pomodoro_cycles": 3})

    def test_pomodoro_cycles_above_twelve_raise(self):
        with self.assertRaises(ValueError):
            builder_study.normalize_spec({"pomodoro_cycles": 13})

    def test_exam_date_is_kept(self):
        self.assertEqual(builder_study.normalize_spec({"exam_date": "15 March"})["exam_date"], "15 March")

    def test_default_checklist_has_key_items(self):
        checklist = builder_study.normalize_spec({})["checklist"]
        self.assertIn("Hall ticket / admit card", checklist)
        self.assertIn("Photo ID", checklist)

    def test_custom_checklist_is_kept(self):
        self.assertEqual(builder_study.normalize_spec({"checklist": ["Pen", "Water"]})["checklist"], ["Pen", "Water"])

    def test_build_returns_sorted_names(self):
        self.assertEqual(self.files, sorted(self.files))

    def test_all_expected_files_exist(self):
        expected = {
            "study_planner_a4.pdf", "study_planner_letter.pdf",
            "cover.png", "preview_1.png", "preview_2.png", "preview_3.png",
            "README.txt", "LICENSE.txt", "manifest.json",
        }
        self.assertEqual(set(self.files), expected)
        for name in expected:
            self.assertTrue((self.tmp / name).is_file(), name)
            self.assertGreater((self.tmp / name).stat().st_size, 0, name)

    def test_no_duplicate_names(self):
        self.assertEqual(len(self.files), len(set(self.files)))

    def test_a4_pdf_has_pdf_header(self):
        self.assertTrue((self.tmp / "study_planner_a4.pdf").read_bytes().startswith(b"%PDF"))

    def test_letter_pdf_has_pdf_header(self):
        self.assertTrue((self.tmp / "study_planner_letter.pdf").read_bytes().startswith(b"%PDF"))

    def test_a4_pdf_has_six_pages(self):
        self.assertEqual(_pdf_pages(self.tmp / "study_planner_a4.pdf"), 6)

    def test_letter_pdf_has_six_pages(self):
        self.assertEqual(_pdf_pages(self.tmp / "study_planner_letter.pdf"), 6)

    def test_pdf_contains_title(self):
        self.assertIn(b"Exam & Study Planner", (self.tmp / "study_planner_a4.pdf").read_bytes())

    def test_cover_is_valid_png(self):
        self.assertEqual((self.tmp / "cover.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        with Image.open(self.tmp / "cover.png") as image:
            self.assertEqual(image.format, "PNG")

    def test_cover_dimensions(self):
        with Image.open(self.tmp / "cover.png") as image:
            self.assertEqual(image.size, (1200, 800))

    def test_cover_and_previews_are_visually_distinct(self):
        hashes = {_sha256(self.tmp / name) for name in ("cover.png", "preview_1.png", "preview_2.png", "preview_3.png")}
        self.assertEqual(len(hashes), 4)

    def test_preview_images_open(self):
        for index in range(1, 4):
            with Image.open(self.tmp / f"preview_{index}.png") as image:
                self.assertEqual(image.size, (1200, 800))

    def test_readme_mentions_both_paper_sizes(self):
        text = (self.tmp / "README.txt").read_text(encoding="utf-8")
        self.assertIn("A4", text)
        self.assertIn("US Letter", text)

    def test_readme_has_print_instructions(self):
        text = (self.tmp / "README.txt").read_text(encoding="utf-8")
        self.assertIn("HOW TO PRINT", text)
        self.assertIn("LICENSE", text)

    def test_license_is_personal_use_only(self):
        text = (self.tmp / "LICENSE.txt").read_text(encoding="utf-8")
        self.assertIn("personal use only", text.lower())

    def test_manifest_records_format_and_exam(self):
        self.assertEqual(self.manifest["format"], "study_planner")
        self.assertEqual(self.manifest["exam"], "jee")
        self.assertEqual(self.manifest["subjects"], ["Physics", "Chemistry", "Maths"])

    def test_manifest_files_match_build_output(self):
        self.assertEqual(self.manifest["files"], self.files)

    def test_manifest_records_tracker_settings(self):
        self.assertEqual(self.manifest["chapters_per_subject"], 12)
        self.assertEqual(self.manifest["pomodoro_cycles"], 8)
        self.assertEqual(self.manifest["pages_per_pdf"], 6)

    def test_manifest_lists_paper_sizes(self):
        self.assertEqual(self.manifest["paper_sizes"], ["a4", "letter"])

    def test_build_is_deterministic(self):
        with tempfile.TemporaryDirectory() as other:
            builder_study.build({}, other)
            for name in self.files:
                self.assertEqual(_sha256(self.tmp / name), _sha256(Path(other) / name), name)

    def test_a_different_exam_changes_pdf(self):
        with tempfile.TemporaryDirectory() as other:
            builder_study.build({"exam": "neet"}, other)
            self.assertNotEqual(
                _sha256(self.tmp / "study_planner_a4.pdf"),
                _sha256(Path(other) / "study_planner_a4.pdf"),
            )

    def test_eamcet_pack_builds_with_six_subjects(self):
        with tempfile.TemporaryDirectory() as other:
            files = builder_study.build({"exam": "eamcet"}, other)
            self.assertIn("study_planner_a4.pdf", files)
            manifest = json.loads((Path(other) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["subjects"]), 6)

    def test_twelve_pomodoro_cycles_build(self):
        with tempfile.TemporaryDirectory() as other:
            builder_study.build({"pomodoro_cycles": 12}, other)
            self.assertEqual(_pdf_pages(Path(other) / "study_planner_a4.pdf"), 6)

    def test_long_title_is_accepted(self):
        title = "My Complete Exam Revision And Study Planner For The Final Semester"
        with tempfile.TemporaryDirectory() as other:
            builder_study.build({"title": title}, other)
            manifest = json.loads((Path(other) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["title"], title)

    def test_output_directory_is_created(self):
        with tempfile.TemporaryDirectory() as parent:
            nested = Path(parent) / "one" / "two"
            files = builder_study.build({"exam": "class_10"}, nested)
            self.assertIn("study_planner_a4.pdf", files)
            self.assertTrue((nested / "study_planner_a4.pdf").is_file())

    def test_text_files_have_no_currency_amounts(self):
        self.assertIsNone(re.search(r"(USD|INR|Rs\.?)\s*\d|\d[\d,.]*\s*(USD|INR|Rs\.?)", _pack_text(self.tmp)))


class BuilderRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import format_registry
        cls.builders = format_registry.discover_builders(force_reload=True)

    def test_habit_builder_is_discovered(self):
        self.assertIn("habit_tracker", self.builders)

    def test_recipe_builder_is_discovered(self):
        self.assertIn("recipe_book", self.builders)

    def test_study_builder_is_discovered(self):
        self.assertIn("study_planner", self.builders)

    def test_discovered_builders_expose_build(self):
        for name in ("habit_tracker", "recipe_book", "study_planner"):
            info = self.builders[name]
            self.assertTrue(callable(info.get("build")), name)
            self.assertTrue(info.get("keywords"), name)

    def test_discovered_builder_modules_are_loaded(self):
        for name in ("habit_tracker", "recipe_book", "study_planner"):
            self.assertTrue(self.builders[name].get("module", "").startswith("builder_"), name)


if __name__ == "__main__":
    unittest.main()
