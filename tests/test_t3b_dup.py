"""tests/test_t3b_dup.py — Comprehensive unit tests for dup_check.py.

Tests cover at least 30 unit tests:
1. Tokenization of normal text, punctuation, empty text.
2. Word shingles (k=5) extraction, short text fallback, empty list.
3. Jaccard similarity edge cases (empty sets, disjoint sets, identical sets, partial overlap).
4. Listing file parsing with title, description, tags, markdown formats.
5. Missing or invalid listing files.
6. Tag normalization (case, spaces, symbols).
7. Pack content extraction without blog.md.
8. Pack content extraction with blog.md from marketing folder.
9. Common opening word detection for titles (>= 3 words, < 3 words, no match).
10. Comparing packs with zero similarity.
11. Comparing packs with moderate similarity (0.5 to 0.8).
12. Comparing packs with severe duplicate similarity (> 0.8).
13. Comparing packs with identical tag sets.
14. Comparing packs with high tag overlap but not identical.
15. Comparing packs with repeated title openings.
16. Suggestion output contains valid Tenglish house style phrases.
17. Summary line returns distinct/clean Tenglish when no duplicates exist.
18. Summary line returns severe warning Tenglish when pairs > 0.8 exist.
19. Summary line returns moderate warning when pairs between 0.5 and 0.8 exist.
20. Summary line flags repeated openings/tags even if text similarity is low.
21. Multi-pack analysis with 0 packs.
22. Multi-pack analysis with 1 pack.
23. Multi-pack analysis with 3+ packs and mixed similarities.
24. Generation of Markdown duplicate report structure.
25. Markdown report contains overview metrics and flagged sections.
26. Atomic report writing generates dup_report.json and dup_report.md.
27. JSON report is valid JSON and preserves structure.
28. Atomic write handles directory creation if output dir does not exist.
29. main() returns 0 when no severe duplicates (> 0.8) exist.
30. main() returns 1 when severe duplicates (> 0.8) exist.
31. Command-line flags --products-dir, --marketing-dir, and --out-dir work properly.
32. Report contains no money amounts or currency patterns.
"""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import dup_check


class TestT3BDuplicateChecker(unittest.TestCase):
    def setUp(self):
        # Clear all GITHUB_* and RUNNER_ENVIRONMENT variables per repo guidelines
        env_clean = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"
        }
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp_dir = tempfile.mkdtemp(prefix="test_dup_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

        self.root = Path(self.tmp_dir)
        self.products_dir = self.root / "products" / "upload"
        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir = self.root / "marketing"
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def _create_sample_pack(
        self,
        pack_name: str,
        title: str,
        desc: str,
        tags: str,
        blog_content: str = "",
    ) -> Path:
        pdir = self.products_dir / pack_name
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "listing.txt").write_text(
            f"Title: {title}\nDescription:\n{desc}\nTags: {tags}\n",
            encoding="utf-8",
        )
        if blog_content:
            mkt_pdir = self.marketing_dir / pack_name
            mkt_pdir.mkdir(parents=True, exist_ok=True)
            (mkt_pdir / "blog.md").write_text(blog_content, encoding="utf-8")
        return pdir

    # 1. Tokenize tests
    def test_01_tokenize_words_normal(self):
        words = dup_check.tokenize_words("Hello World! This is a test 123.")
        self.assertEqual(words, ["hello", "world", "this", "is", "a", "test", "123"])

    def test_02_tokenize_words_empty_and_whitespace(self):
        self.assertEqual(dup_check.tokenize_words(""), [])
        self.assertEqual(dup_check.tokenize_words("   \n\t  "), [])
        self.assertEqual(dup_check.tokenize_words("--- @@@ ###"), [])

    # 2. Word shingles tests
    def test_03_word_shingles_k5(self):
        words = ["one", "two", "three", "four", "five", "six", "seven"]
        shingles = dup_check.get_word_shingles(words, k=5)
        self.assertEqual(len(shingles), 3)
        self.assertIn("one two three four five", shingles)
        self.assertIn("two three four five six", shingles)
        self.assertIn("three four five six seven", shingles)

    def test_04_word_shingles_short_fallback(self):
        words = ["quick", "brown", "fox"]
        shingles = dup_check.get_word_shingles(words, k=5)
        self.assertEqual(shingles, {"quick brown fox"})

    def test_05_word_shingles_empty(self):
        self.assertEqual(dup_check.get_word_shingles([]), set())

    # 3. Jaccard similarity tests
    def test_06_jaccard_empty(self):
        self.assertEqual(dup_check.jaccard_similarity(set(), {"a"}), 0.0)
        self.assertEqual(dup_check.jaccard_similarity({"a"}, set()), 0.0)
        self.assertEqual(dup_check.jaccard_similarity(set(), set()), 0.0)

    def test_07_jaccard_identical(self):
        s = {"apple", "banana", "cherry"}
        self.assertEqual(dup_check.jaccard_similarity(s, s), 1.0)

    def test_08_jaccard_disjoint(self):
        s1 = {"a", "b"}
        s2 = {"c", "d"}
        self.assertEqual(dup_check.jaccard_similarity(s1, s2), 0.0)

    def test_09_jaccard_partial(self):
        s1 = {"a", "b", "c"}
        s2 = {"b", "c", "d"}
        # intersection=2, union=4 -> 0.5
        self.assertEqual(dup_check.jaccard_similarity(s1, s2), 0.5)

    # 4. Listing parsing
    def test_10_parse_listing_file_standard(self):
        lfile = self.root / "sample_listing.txt"
        lfile.write_text(
            "Title: Digital Daily Planner\n"
            "Description:\nOrganize your life with daily goals and priorities.\n"
            "Tags: planner, daily, printable, digital\n",
            encoding="utf-8",
        )
        parsed = dup_check.parse_listing_file(lfile)
        self.assertEqual(parsed["title"], "Digital Daily Planner")
        self.assertIn("Organize your life", parsed["description"])
        self.assertEqual(parsed["tags"], ["planner", "daily", "printable", "digital"])

    def test_11_parse_listing_file_markdown_headings(self):
        lfile = self.root / "sample_listing.md"
        lfile.write_text(
            "# Weekly Habit Kit\n"
            "### Description\nTrack your habits each week with ease.\n"
            "### Tags\nhabits, tracker, routine\n",
            encoding="utf-8",
        )
        parsed = dup_check.parse_listing_file(lfile)
        self.assertEqual(parsed["title"], "Weekly Habit Kit")
        self.assertIn("Track your habits", parsed["description"])
        self.assertEqual(parsed["tags"], ["habits", "tracker", "routine"])

    def test_12_parse_listing_nonexistent_file(self):
        parsed = dup_check.parse_listing_file(self.root / "missing.txt")
        self.assertEqual(parsed["title"], "")
        self.assertEqual(parsed["description"], "")
        self.assertEqual(parsed["tags"], [])

    # 5. Tag normalization
    def test_13_normalize_tag(self):
        self.assertEqual(dup_check.normalize_tag("  Daily Planner!  "), "daily planner")
        self.assertEqual(dup_check.normalize_tag("habit-tracker"), "habit tracker")
        self.assertEqual(dup_check.normalize_tag(""), "")

    # 6. Pack content extraction
    def test_14_extract_pack_content_without_blog(self):
        pdir = self._create_sample_pack(
            "pack-alpha",
            "Budget Planner Template",
            "Track your monthly income and expenses with simple clean layout tables.",
            "budget, finance, monthly, planner",
        )
        data = dup_check.extract_pack_content(pdir, marketing_dir=self.marketing_dir)
        self.assertEqual(data["pack_name"], "pack-alpha")
        self.assertEqual(data["title"], "Budget Planner Template")
        self.assertFalse(data["has_blog"])
        self.assertGreater(len(data["all_words"]), 5)

    def test_15_extract_pack_content_with_blog(self):
        pdir = self._create_sample_pack(
            "pack-beta",
            "Workout Routine Guide",
            "Comprehensive guide to weekly workouts and fitness progress.",
            "fitness, workout, health, routine",
            blog_content="# Complete Guide to Workout Routines\nStay fit every day with our easy plan.",
        )
        data = dup_check.extract_pack_content(pdir, marketing_dir=self.marketing_dir)
        self.assertTrue(data["has_blog"])
        self.assertGreater(data["blog_len"], 0)
        self.assertTrue(len(data["blog_shingles"]) > 0)

    # 7. Common opening detection
    def test_16_find_common_opening_matches(self):
        w1 = ["digital", "budget", "planner", "printable", "sheet"]
        w2 = ["digital", "budget", "planner", "workbook", "kit"]
        opening = dup_check.find_common_opening(w1, w2, min_words=3)
        self.assertEqual(opening, "digital budget planner")

    def test_17_find_common_opening_less_than_threshold(self):
        w1 = ["digital", "budget", "planner"]
        w2 = ["digital", "fitness", "planner"]
        # only "digital" matches (1 word < 3)
        opening = dup_check.find_common_opening(w1, w2, min_words=3)
        self.assertEqual(opening, "")

    # 8. Pack comparisons
    def test_18_compare_distinct_packs(self):
        p1 = dup_check.extract_pack_content(
            self._create_sample_pack(
                "p1",
                "Kindergarten Alphabet Worksheets",
                "Help children learn early writing and phonics through traced letters and coloring.",
                "kindergarten, alphabet, letters, phonics",
            )
        )
        p2 = dup_check.extract_pack_content(
            self._create_sample_pack(
                "p2",
                "Freelancer Invoice Template",
                "Simple professional billing documents for independent contractors and consultants.",
                "invoice, billing, freelance, accounting",
            )
        )
        cmp = dup_check.compare_packs(p1, p2)
        self.assertFalse(cmp["is_duplicate_pair"])
        self.assertFalse(cmp["is_severe_duplicate"])
        self.assertLess(cmp["max_similarity"], 0.2)
        self.assertFalse(cmp["identical_tags"])
        self.assertEqual(cmp["common_title_opening"], "")

    def test_19_compare_moderate_duplicate_packs(self):
        desc1 = (
            "A structured weekly layout designed for habit tracking and everyday routines. "
            "Includes morning checklists, evening reflection, daily goal setting, and milestone notes."
        )
        desc2 = (
            "A structured weekly layout designed for habit tracking and everyday routines. "
            "Includes morning checklists, evening reflection, weekly goal planning, and progress notes."
        )
        p1 = dup_check.extract_pack_content(
            self._create_sample_pack("p-habits-1", "Habit Tracker Pack One", desc1, "habits, routine, planner")
        )
        p2 = dup_check.extract_pack_content(
            self._create_sample_pack("p-habits-2", "Habit Tracker Pack Two", desc2, "habits, routine, productivity")
        )
        cmp = dup_check.compare_packs(p1, p2)
        self.assertTrue(cmp["is_duplicate_pair"])
        self.assertGreaterEqual(cmp["max_similarity"], 0.5)
        self.assertTrue(any("konchem" in s.lower() or "variation" in s.lower() for s in cmp["tenglish_suggestions"]))

    def test_20_compare_severe_duplicate_packs(self):
        desc = (
            "This comprehensive monthly budget worksheet helps you track every dollar, "
            "categorize recurring expenses, organize cash flow, manage debt payoffs, and achieve savings goals."
        )
        p1 = dup_check.extract_pack_content(
            self._create_sample_pack("p-budget-a", "Monthly Budget Worksheet A", desc, "budget, finance, money, savings")
        )
        p2 = dup_check.extract_pack_content(
            self._create_sample_pack("p-budget-b", "Monthly Budget Worksheet B", desc, "budget, finance, money, savings")
        )
        cmp = dup_check.compare_packs(p1, p2)
        self.assertTrue(cmp["is_severe_duplicate"])
        self.assertGreaterEqual(cmp["max_similarity"], 0.8)
        self.assertTrue(any("chaala ekkuva" in s.lower() for s in cmp["tenglish_suggestions"]))

    def test_21_compare_identical_tags(self):
        p1 = dup_check.extract_pack_content(
            self._create_sample_pack("t1", "Study Planner", "Description one", "study, exam, student")
        )
        p2 = dup_check.extract_pack_content(
            self._create_sample_pack("t2", "Class Schedule", "Description two", "exam, study, student")
        )
        cmp = dup_check.compare_packs(p1, p2)
        self.assertTrue(cmp["identical_tags"])
        self.assertTrue(any("identical tags" in s.lower() for s in cmp["tenglish_suggestions"]))

    def test_22_compare_repeated_title_openings(self):
        p1 = dup_check.extract_pack_content(
            self._create_sample_pack("op1", "Minimalist Clean Aesthetic Daily Planner", "Desc 1", "tag1")
        )
        p2 = dup_check.extract_pack_content(
            self._create_sample_pack("op2", "Minimalist Clean Aesthetic Weekly Layout", "Desc 2", "tag2")
        )
        cmp = dup_check.compare_packs(p1, p2)
        self.assertEqual(cmp["common_title_opening"], "minimalist clean aesthetic")
        self.assertTrue(any("oke opening words" in s.lower() for s in cmp["tenglish_suggestions"]))

    # 9. Analysis of pack lists
    def test_23_analyze_empty_and_single_pack(self):
        res0 = dup_check.analyze_packs([])
        self.assertEqual(res0["total_packs"], 0)
        self.assertEqual(res0["duplicate_pairs_count"], 0)
        self.assertIn("clean", res0["summary_tenglish"].lower())

        p1 = dup_check.extract_pack_content(
            self._create_sample_pack("single", "Single Product", "Solo description", "solo, tag")
        )
        res1 = dup_check.analyze_packs([p1])
        self.assertEqual(res1["total_packs"], 1)
        self.assertEqual(res1["total_pairs_compared"], 0)
        self.assertIn("clean", res1["summary_tenglish"].lower())

    def test_24_analyze_multiple_packs_summary_levels(self):
        # 3 packs with one severe duplicate pair
        desc_dup = (
            "Detailed daily focus and goal planning template with priority matrix and schedule blocks. "
            "Designed to boost productivity and reduce cognitive overload each workday."
        )
        p1 = dup_check.extract_pack_content(self._create_sample_pack("d1", "Focus Daily 1", desc_dup, "focus, daily"))
        p2 = dup_check.extract_pack_content(self._create_sample_pack("d2", "Focus Daily 2", desc_dup, "focus, daily"))
        p3 = dup_check.extract_pack_content(self._create_sample_pack("d3", "Recipe Book", "Cook healthy food", "food, cooking"))

        analysis = dup_check.analyze_packs([p1, p2, p3])
        self.assertEqual(analysis["total_packs"], 3)
        self.assertEqual(analysis["total_pairs_compared"], 3)
        self.assertTrue(analysis["has_severe_duplicates"])
        self.assertEqual(analysis["severe_duplicates_count"], 1)
        self.assertIn("severe duplicate content undi", analysis["summary_tenglish"])

    # 10. Report generation tests
    def test_25_markdown_report_formatting(self):
        p1 = dup_check.extract_pack_content(self._create_sample_pack("r1", "Title A", "Common description words here", "t1, t2"))
        p2 = dup_check.extract_pack_content(self._create_sample_pack("r2", "Title B", "Common description words here", "t1, t2"))
        analysis = dup_check.analyze_packs([p1, p2])
        md = dup_check.generate_markdown_report(analysis)
        self.assertIn("# Duplicate Content Report Across Ready Packs", md)
        self.assertIn("## Overview Metrics", md)
        self.assertIn("## Owner Action Checklist", md)
        self.assertIn("Tenglish Summary", md)

    def test_26_atomic_write_reports(self):
        out_dir = self.root / "reports"
        dummy_data = {
            "total_packs": 2,
            "total_pairs_compared": 1,
            "duplicate_pairs_count": 0,
            "severe_duplicates_count": 0,
            "repeated_openings_count": 0,
            "identical_tag_pairs_count": 0,
            "has_severe_duplicates": False,
            "summary_tenglish": "All clean ga undi ✓.",
            "flagged_pairs": [],
            "repeated_title_openings": [],
            "identical_tag_pairs": [],
        }
        json_path, md_path = dup_check.write_reports_atomically(dummy_data, out_dir)
        self.assertTrue(json_path.is_file())
        self.assertTrue(md_path.is_file())

        with open(json_path, encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["total_packs"], 2)
        self.assertEqual(loaded["summary_tenglish"], "All clean ga undi ✓.")

    # 11. End-to-end and CLI tests
    def test_27_check_duplicate_content_e2e(self):
        self._create_sample_pack("p-a", "Pack A Title", "Unique content for product pack A.", "alpha, first")
        self._create_sample_pack("p-b", "Pack B Title", "Unique content for product pack B.", "beta, second")

        out_dir = self.marketing_dir / "reports_test"
        report = dup_check.check_duplicate_content(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            out_dir=out_dir,
        )
        self.assertEqual(report["total_packs"], 2)
        self.assertTrue((out_dir / "dup_report.json").is_file())
        self.assertTrue((out_dir / "dup_report.md").is_file())

    def test_28_main_cli_exit_code_zero_when_no_severe(self):
        self._create_sample_pack("pack1", "Pack 1 Unique", "Completely unique text for pack one.", "pack1, unique")
        self._create_sample_pack("pack2", "Pack 2 Unique", "Completely different text for pack two.", "pack2, different")

        code = dup_check.main([
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
            "--out-dir", str(self.marketing_dir / "mkt"),
        ])
        self.assertEqual(code, 0)

    def test_29_main_cli_exit_code_one_when_severe(self):
        identical_text = (
            "This is a shared text across both listings that repeats word for word for multiple sentences. "
            "We want to make sure the duplicate check flags this with maximum similarity index above eighty percent."
        )
        self._create_sample_pack("dup1", "Repeated Pack 1", identical_text, "shared, tags")
        self._create_sample_pack("dup2", "Repeated Pack 2", identical_text, "shared, tags")

        code = dup_check.main([
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
            "--out-dir", str(self.marketing_dir / "mkt2"),
        ])
        self.assertEqual(code, 1)

    def test_30_blog_similarity_integration(self):
        # Two packs with different descriptions but identical blog post articles
        blog_art = (
            "# Ultimate Guide to Planning Routines\n"
            "Planning routines every morning helps keep your day structured and productive. "
            "Follow these simple steps to organize your daily schedule with ease and focus."
        )
        p1 = dup_check.extract_pack_content(
            self._create_sample_pack("b1", "Planner One", "Unique desc 1", "tag1", blog_content=blog_art),
            marketing_dir=self.marketing_dir,
        )
        p2 = dup_check.extract_pack_content(
            self._create_sample_pack("b2", "Planner Two", "Unique desc 2", "tag2", blog_content=blog_art),
            marketing_dir=self.marketing_dir,
        )
        cmp = dup_check.compare_packs(p1, p2)
        self.assertEqual(cmp["blog_similarity"], 1.0)
        self.assertTrue(cmp["is_severe_duplicate"])

    def test_31_no_currency_amounts_in_generated_output(self):
        # Ensure generated markdown and json never contain currency amounts
        import re
        currency_regex = re.compile(
            r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
            re.IGNORECASE,
        )
        p1 = dup_check.extract_pack_content(self._create_sample_pack("c1", "Pack One", "Description 1", "tag1"))
        p2 = dup_check.extract_pack_content(self._create_sample_pack("c2", "Pack Two", "Description 2", "tag2"))
        analysis = dup_check.analyze_packs([p1, p2])
        md = dup_check.generate_markdown_report(analysis)
        self.assertIsNone(currency_regex.search(md))
        self.assertIsNone(currency_regex.search(json.dumps(analysis)))


if __name__ == "__main__":
    unittest.main()
