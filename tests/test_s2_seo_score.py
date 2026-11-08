"""tests/test_s2_seo_score.py — Unit tests for seo_score.py.

At least 25 tests covering:
- Score 0-100 range and determinism
- Main keyword in first 40 chars of title (exact, partial, missing)
- Keyword in first sentence of description (exact, partial, missing)
- Tag coverage of title words (high, partial, low, empty)
- Tag duplicates detection (exact case-insensitive, stem duplicates like planner/planners)
- Description length and structure (bullets, 'What you get' section)
- Readability (avg sentence length <= 20 words)
- Alt text check (present, missing, partial)
- Output JSON format and keys
- Tenglish summary generation (high, medium, low scores)
- Parsing of listing.txt and listing.md
- CLI behavior and error handling
"""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import seo_score


class TestS2SeoScore(unittest.TestCase):
    def setUp(self):
        # Jules rules: clear all GITHUB_* and RUNNER_ENVIRONMENT variables using mock.patch.dict
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp_dir = tempfile.mkdtemp(prefix="test_seo_score_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

    def test_01_simple_stem(self):
        self.assertEqual(seo_score.simple_stem("planners"), "planner")
        self.assertEqual(seo_score.simple_stem("tracking"), "track")
        self.assertEqual(seo_score.simple_stem("activities"), "activity")
        self.assertEqual(seo_score.simple_stem("boxes"), "box")
        self.assertEqual(seo_score.simple_stem("cat"), "cat")

    def test_02_extract_keywords_from_title(self):
        words = seo_score.extract_keywords_from_title("Daily Habit Tracker Printable PDF for Adults")
        self.assertIn("daily", words)
        self.assertIn("habit", words)
        self.assertIn("tracker", words)
        self.assertIn("printable", words)
        self.assertIn("pdf", words)
        self.assertNotIn("for", words)

    def test_03_get_main_keyword_from_tags_match(self):
        kw = seo_score.get_main_keyword("Daily Habit Tracker Printable", ["habit tracker", "daily planner"])
        self.assertEqual(kw, "habit tracker")

    def test_04_get_main_keyword_fallback_words(self):
        kw = seo_score.get_main_keyword("Budget Planner Worksheet", ["finances"])
        self.assertEqual(kw, "budget planner")

    def test_05_parse_listing_text_standard(self):
        text = """Title: Habit Tracker Printable
Tags: habit tracker, printable planner, habit log
Description:
Track your daily habits with ease. This printable helps you stay consistent.
What you get:
- 12 monthly habit tracker pages
- Instructions guide
"""
        parsed = seo_score.parse_listing_text(text)
        self.assertEqual(parsed["title"], "Habit Tracker Printable")
        self.assertEqual(len(parsed["tags"]), 3)
        self.assertIn("Track your daily habits", parsed["description"])
        self.assertEqual(len(parsed["what_you_get"]), 2)

    def test_06_parse_listing_text_markdown_headings(self):
        text = """# Project Planner Workbook

## Tags
project planner, task tracker, goal sheet

## Description
Organize tasks effortlessly. Clean and simple design.

## What's Included
* Project overview sheet
* Milestone roadmap
"""
        parsed = seo_score.parse_listing_text(text)
        self.assertEqual(parsed["title"], "Project Planner Workbook")
        self.assertIn("project planner", parsed["tags"])
        self.assertEqual(len(parsed["what_you_get"]), 2)

    def test_07_check_tag_duplicates_clean(self):
        tags = ["habit tracker", "daily planner", "routine checklist"]
        issue_count, exact, near = seo_score.check_tag_duplicates(tags)
        self.assertEqual(issue_count, 0)
        self.assertEqual(len(exact), 0)
        self.assertEqual(len(near), 0)

    def test_08_check_tag_duplicates_exact(self):
        tags = ["habit tracker", "Habit Tracker", "daily planner"]
        issue_count, exact, near = seo_score.check_tag_duplicates(tags)
        self.assertGreaterEqual(issue_count, 1)
        self.assertIn("habit tracker", exact)

    def test_09_check_tag_duplicates_stems(self):
        tags = ["habit tracker", "habit trackers", "daily planner"]
        issue_count, exact, near = seo_score.check_tag_duplicates(tags)
        self.assertGreaterEqual(issue_count, 1)
        self.assertEqual(len(near), 1)

    def test_10_readability_short_sentences(self):
        text = "Track your habits every day. Build solid routines easily. Enjoy clear progress charts."
        avg_len, s_cnt, w_cnt = seo_score.calculate_readability(text)
        self.assertLessEqual(avg_len, 20.0)
        self.assertEqual(s_cnt, 3)

    def test_11_readability_long_sentences(self):
        text = "This is a ridiculously elongated and convoluted sentence that goes on and on without any punctuation or pause to ensure that the sentence length exceeds the twenty word threshold set by standard SEO readability recommendations and guidelines."
        avg_len, s_cnt, w_cnt = seo_score.calculate_readability(text)
        self.assertGreater(avg_len, 20.0)

    def test_12_perfect_seo_score(self):
        data = {
            "title": "Habit Tracker Printable Daily Routine Worksheet",
            "tags": ["habit tracker", "printable", "daily routine", "worksheet", "habits log"],
            "description": "Habit tracker printable designed to transform your daily routines. Easily track your goals every single morning. Enjoy organized progress without overwhelm.\n\nWhat you get:\n- 12 printable habit tracker pages\n- Weekly goal checklist",
            "what_you_get": ["12 printable habit tracker pages", "Weekly goal checklist"],
        }
        res = seo_score.score_listing(data)
        self.assertGreaterEqual(res["score"], 90)
        self.assertTrue(res["checks"]["keyword_in_title_front"]["passed"])
        self.assertTrue(res["checks"]["keyword_in_first_sentence"]["passed"])
        self.assertTrue(res["checks"]["tag_coverage"]["passed"])
        self.assertTrue(res["checks"]["tag_uniqueness"]["passed"])
        self.assertTrue(res["checks"]["description_structure"]["passed"])
        self.assertTrue(res["checks"]["readability"]["passed"])
        self.assertIn("baavundi", res["tenglish_summary"])

    def test_13_missing_keyword_in_title_front(self):
        data = {
            "title": "A Spectacular Complete Mega Bundle of Printable Daily Habit Tracker",
            "tags": ["habit tracker"],
            "description": "Habit tracker helps your daily life.",
            "what_you_get": ["1 Habit tracker"],
        }
        res = seo_score.score_listing(data)
        self.assertLess(res["checks"]["keyword_in_title_front"]["score"], 20)

    def test_14_missing_keyword_in_first_sentence(self):
        data = {
            "title": "Habit Tracker Printable Daily Planner",
            "tags": ["habit tracker", "daily planner"],
            "description": "Welcome to our store. We make lovely digital downloads for everyone. Habit tracker is included here.",
            "what_you_get": ["1 Habit tracker"],
        }
        res = seo_score.score_listing(data)
        # first sentence does not contain 'habit tracker'
        self.assertLess(res["checks"]["keyword_in_first_sentence"]["score"], 15)

    def test_15_low_tag_coverage(self):
        data = {
            "title": "Budget Financial Expenses Tracker Worksheet Template",
            "tags": ["recipes", "cooking"],
            "description": "Budget tracker worksheet template. Clean layout.\n- Sheet 1",
            "what_you_get": ["Sheet 1"],
        }
        res = seo_score.score_listing(data)
        self.assertFalse(res["checks"]["tag_coverage"]["passed"])
        self.assertLess(res["checks"]["tag_coverage"]["score"], 10)

    def test_16_duplicate_tags_penalized(self):
        data = {
            "title": "Habit Tracker Printable",
            "tags": ["habit tracker", "Habit Tracker", "habit trackers", "trackers"],
            "description": "Habit tracker for you.\n- Page 1",
            "what_you_get": ["Page 1"],
        }
        res = seo_score.score_listing(data)
        self.assertFalse(res["checks"]["tag_uniqueness"]["passed"])
        self.assertLess(res["checks"]["tag_uniqueness"]["score"], 15)

    def test_17_description_structure_missing_bullets_and_what_you_get(self):
        data = {
            "title": "Habit Tracker Printable",
            "tags": ["habit tracker", "printable"],
            "description": "Short plain sentence.",
            "what_you_get": [],
        }
        res = seo_score.score_listing(data)
        self.assertFalse(res["checks"]["description_structure"]["passed"])

    def test_18_alt_texts_present_with_pack_images(self):
        pack_path = Path(self.tmp_dir) / "pack_1"
        pack_path.mkdir()
        (pack_path / "cover.png").write_bytes(b"dummy image data")
        (pack_path / "preview.png").write_bytes(b"dummy image data 2")
        (pack_path / "alt.txt").write_text("cover: Clean habit tracker cover\npreview: Monthly habit overview\n", encoding="utf-8")

        data = {
            "title": "Habit Tracker Printable",
            "tags": ["habit tracker", "printable"],
            "description": "Habit tracker printable page.\n- 1 page",
            "what_you_get": ["1 page"],
        }
        res = seo_score.score_listing(data, pack_dir=pack_path)
        self.assertTrue(res["checks"]["alt_texts_present"]["passed"])
        self.assertEqual(res["checks"]["alt_texts_present"]["score"], 10)

    def test_19_alt_texts_missing_with_pack_images(self):
        pack_path = Path(self.tmp_dir) / "pack_2"
        pack_path.mkdir()
        (pack_path / "cover.png").write_bytes(b"dummy image data")

        data = {
            "title": "Habit Tracker Printable",
            "tags": ["habit tracker", "printable"],
            "description": "Habit tracker printable page.\n- 1 page",
            "what_you_get": ["1 page"],
        }
        res = seo_score.score_listing(data, pack_dir=pack_path, alt_texts={})
        self.assertFalse(res["checks"]["alt_texts_present"]["passed"])
        self.assertEqual(res["checks"]["alt_texts_present"]["score"], 0)

    def test_20_score_pack_folder_reads_listing_txt(self):
        pack_path = Path(self.tmp_dir) / "pack_txt"
        pack_path.mkdir()
        listing = """Title: Daily Routine Checklist Printable
Tags: daily routine, checklist, printable, daily planner
Description:
Daily routine checklist printable designed for quick morning organization. Follow easy habits. Stay focused.

What you get:
- Morning routine sheet
- Evening wind down list
"""
        (pack_path / "listing.txt").write_text(listing, encoding="utf-8")
        res = seo_score.score_pack_folder(pack_path)
        self.assertIsInstance(res, dict)
        self.assertIn("score", res)
        self.assertIn("tenglish_summary", res)
        self.assertGreater(res["score"], 70)

    def test_21_score_pack_folder_reads_listing_md(self):
        pack_path = Path(self.tmp_dir) / "pack_md"
        pack_path.mkdir()
        listing = """# Fitness Workout Log Printable

## Tags
fitness workout, workout log, printable log

## Description
Fitness workout log printable to record gym exercises. Track sets and reps. Simple to print.

## What you get
- Upper body workout sheet
- Lower body workout sheet
"""
        (pack_path / "listing.md").write_text(listing, encoding="utf-8")
        res = seo_score.score_pack_folder(pack_path)
        self.assertEqual(res["main_keyword"], "fitness workout")
        self.assertGreater(res["score"], 70)

    def test_22_score_pack_folder_missing_folder_raises(self):
        with self.assertRaises(FileNotFoundError):
            seo_score.score_pack_folder(Path(self.tmp_dir) / "nonexistent")

    def test_23_tenglish_summary_levels(self):
        # 95 -> baavundi
        sum_high = seo_score.generate_tenglish_summary(95, {})
        self.assertIn("baavundi", sum_high)

        # 78 -> manchidi
        sum_mid = seo_score.generate_tenglish_summary(78, {"tag_coverage": {"score": 5, "max": 15}})
        self.assertIn("manchidi", sum_mid)

        # 55 -> maatrame
        sum_low = seo_score.generate_tenglish_summary(55, {"tag_coverage": {"score": 0, "max": 15}})
        self.assertIn("maatrame", sum_low)

        # 30 -> low
        sum_vlow = seo_score.generate_tenglish_summary(30, {})
        self.assertIn("low", sum_vlow)

    def test_24_deterministic_scoring(self):
        data = {
            "title": "Minimalist Weekly Planner Printable",
            "tags": ["weekly planner", "minimalist planner", "printable planner"],
            "description": "Minimalist weekly planner printable for busy professionals. Stay organized each week. Simple layout.\n\nWhat you get:\n- 52 undated weekly pages",
            "what_you_get": ["52 undated weekly pages"],
        }
        res1 = seo_score.score_listing(data)
        res2 = seo_score.score_listing(data)
        self.assertEqual(res1, res2)

    def test_25_main_cli_json_output(self):
        listing_file = Path(self.tmp_dir) / "listing.txt"
        listing_file.write_text("Title: Study Planner\nTags: study planner\nDescription: Study planner.\n", encoding="utf-8")

        stdout_mock = io_mock = mock.MagicMock()
        with mock.patch("sys.stdout.write") as mock_write, mock.patch("sys.argv", ["seo_score.py", str(listing_file), "--json"]):
            seo_score.main()
            written = "".join(call.args[0] for call in mock_write.call_args_list)
            data = json.loads(written)
            self.assertIn("score", data)
            self.assertIn("checks", data)

    def test_26_main_cli_text_output(self):
        listing_file = Path(self.tmp_dir) / "listing.txt"
        listing_file.write_text("Title: Study Planner\nTags: study planner\nDescription: Study planner.\n", encoding="utf-8")

        with mock.patch("sys.stdout.write") as mock_write, mock.patch("sys.argv", ["seo_score.py", str(listing_file)]):
            seo_score.main()
            written = "".join(call.args[0] for call in mock_write.call_args_list)
            self.assertIn("SEO Score:", written)
            self.assertIn("Tenglish Summary:", written)


if __name__ == "__main__":
    unittest.main()
