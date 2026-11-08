#!/usr/bin/env python3
"""tests/test_s5_gift_guide.py — Unit tests for gift_guide.py."""

from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import gift_guide


class TestGiftGuide(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.test_dir = tempfile.mkdtemp(prefix="test_gift_guide_")
        self.products_dir = Path(self.test_dir) / "products"
        self.upload_dir = self.products_dir / "upload"
        self.out_dir = Path(self.test_dir) / "marketing" / "collections"

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_pack(
        self,
        folder_name: str,
        title: str,
        tags: str,
        desc: str = "A clean printable tool.",
    ) -> Path:
        pdir = self.upload_dir / folder_name
        pdir.mkdir(parents=True, exist_ok=True)
        content = (
            f"Title: {title}\n\n"
            f"Description: {desc}\n\n"
            f"Tags: {tags}\n"
        )
        (pdir / "listing.txt").write_text(content, encoding="utf-8")
        (pdir / "product.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        return pdir

    def test_01_all_six_seasons_present(self):
        expected = {"diwali", "new_year", "back_to_school", "exam_season", "wedding_season", "summer_holidays"}
        self.assertEqual(set(gift_guide.SEASONS.keys()), expected)

    def test_02_intro_word_counts_in_range(self):
        for k, data in gift_guide.SEASONS.items():
            wc = gift_guide.word_count(data["intro"])
            self.assertGreaterEqual(wc, 80, f"{k} intro has {wc} words (< 80)")
            self.assertLessEqual(wc, 120, f"{k} intro has {wc} words (> 120)")

    def test_03_all_seasons_have_13_tags(self):
        for k, data in gift_guide.SEASONS.items():
            self.assertEqual(len(data["tags"]), 13, f"{k} does not have exactly 13 tags")

    def test_04_all_seasons_have_valid_date_windows(self):
        for k, data in gift_guide.SEASONS.items():
            sm, sd = data["start_date"]
            em, ed = data["end_date"]
            self.assertTrue(1 <= sm <= 12)
            self.assertTrue(1 <= sd <= 31)
            self.assertTrue(1 <= em <= 12)
            self.assertTrue(1 <= ed <= 31)

    def test_05_word_count_helper(self):
        self.assertEqual(gift_guide.word_count("hello world from test"), 4)
        self.assertEqual(gift_guide.word_count(""), 0)

    def test_06_date_window_check_in_season(self):
        # Diwali: 10/1 to 11/20
        self.assertTrue(gift_guide.is_in_season_window("diwali", date(2025, 10, 15)))
        self.assertTrue(gift_guide.is_in_season_window("diwali", date(2025, 11, 1)))
        self.assertFalse(gift_guide.is_in_season_window("diwali", date(2025, 5, 1)))

    def test_07_date_window_check_year_wrap(self):
        # New Year: 12/1 to 1/31 (wraps year boundary)
        self.assertTrue(gift_guide.is_in_season_window("new_year", date(2025, 12, 10)))
        self.assertTrue(gift_guide.is_in_season_window("new_year", date(2026, 1, 15)))
        self.assertFalse(gift_guide.is_in_season_window("new_year", date(2025, 7, 10)))

    def test_08_extract_pack_tokens(self):
        p = self._create_pack("p-study", "Exam Study Tracker", "exams, revision, pomodoro")
        name, tokens = gift_guide.extract_pack_tokens(p)
        self.assertIn("study", tokens)
        self.assertIn("revision", tokens)
        self.assertIn("pomodoro", tokens)
        self.assertEqual(name, "Exam Study Tracker")

    def test_09_score_pack_diwali_match(self):
        p = self._create_pack("p-diwali", "Diwali Festival Planner", "diwali, pooja, festive, sweets")
        _, tokens = gift_guide.extract_pack_tokens(p)
        score = gift_guide.score_pack_for_season(tokens, "diwali")
        self.assertGreaterEqual(score, 3)

    def test_10_score_pack_unrelated_zero(self):
        p = self._create_pack("p-gym", "Gym Workout Log", "fitness, gym, lifting, strength")
        _, tokens = gift_guide.extract_pack_tokens(p)
        score = gift_guide.score_pack_for_season(tokens, "diwali")
        self.assertEqual(score, 0)

    def test_11_find_matching_packs_filters_below_threshold(self):
        p1 = self._create_pack("p1", "Diwali Puja Planner", "diwali, puja, festive")
        p2 = self._create_pack("p2", "Wedding Budget", "wedding, bride, reception")
        matches = gift_guide.find_matching_packs([p1, p2], "diwali", threshold=2)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][0], "Diwali Puja Planner")

    def test_12_generate_markdown_content(self):
        p = self._create_pack("p1", "Diwali Puja Planner", "diwali, puja, festive")
        matches = [( "Diwali Puja Planner", p, 3)]
        md = gift_guide.generate_season_collection_markdown("diwali", matches)
        self.assertIn("# Diwali Celebration & Festive Home Collection", md)
        self.assertIn("Featured Digital Guides & Planners", md)
        self.assertIn("- **Diwali Puja Planner**", md)
        self.assertIn("Collection Tags", md)
        self.assertIn("diwali planner", md)

    def test_13_no_money_in_generated_guide(self):
        p = self._create_pack("p1", "New Year Goal Planner", "goals, new year, habit, resolutions")
        matches = [("New Year Goal Planner", p, 4)]
        md = gift_guide.generate_season_collection_markdown("new_year", matches)
        self.assertNotIn("$", md)
        self.assertNotIn("USD", md)
        self.assertNotIn("INR", md)
        self.assertNotIn("Rs", md)

    def test_14_no_brand_names_in_generated_guide(self):
        for skey in gift_guide.SEASONS.keys():
            intro = gift_guide.SEASONS[skey]["intro"]
            self.assertIsNone(gift_guide.BRAND_WORDS.search(intro))

    def test_15_write_seasonal_collection_creates_file(self):
        p = self._create_pack("p-school", "Back To School Planner", "school, student, semester, academic")
        matches = [("Back To School Planner", p, 4)]
        out = gift_guide.write_seasonal_collection("back_to_school", matches, out_dir=self.out_dir)
        self.assertIsNotNone(out)
        self.assertTrue(out.is_file())
        self.assertEqual(out.name, "back-to-school.md")

    def test_16_write_seasonal_collection_skips_when_no_matches(self):
        out = gift_guide.write_seasonal_collection("diwali", [], out_dir=self.out_dir)
        self.assertIsNone(out)

    def test_17_atomic_write_leaves_no_tmp(self):
        p = self._create_pack("p-exam", "Exam Revision Kit", "exam, revision, test, finals")
        matches = [("Exam Revision Kit", p, 4)]
        gift_guide.write_seasonal_collection("exam_season", matches, out_dir=self.out_dir)
        tmps = list(self.out_dir.glob(".coll_*"))
        self.assertEqual(len(tmps), 0)

    def test_18_generate_all_seasonal_collections(self):
        self._create_pack("p-wedding", "Bridal Wedding Binder", "wedding, bridal, ceremony, vows")
        self._create_pack("p-summer", "Summer Vacation Planner", "summer, travel, vacation, itinerary")
        results = gift_guide.generate_all_seasonal_collections(
            products_dir=self.products_dir,
            out_dir=self.out_dir,
            threshold=2,
        )
        self.assertIn("wedding_season", results)
        self.assertIn("summer_holidays", results)
        self.assertTrue((self.out_dir / "wedding-season.md").is_file())
        self.assertTrue((self.out_dir / "summer-holidays.md").is_file())

    def test_19_generate_all_seasonal_collections_active_only(self):
        # Summer date July 1st
        self._create_pack("p-summer", "Summer Vacation Planner", "summer, travel, vacation, itinerary")
        results = gift_guide.generate_all_seasonal_collections(
            products_dir=self.products_dir,
            out_dir=self.out_dir,
            threshold=2,
            only_active_window=True,
            current_date=date(2025, 7, 1),
        )
        self.assertIn("summer_holidays", results)
        self.assertNotIn("new_year", results)

    def test_20_deterministic_output(self):
        p = self._create_pack("p-ny", "New Year Habits", "new year, habit, resolutions, goals")
        matches = [("New Year Habits", p, 4)]
        md1 = gift_guide.generate_season_collection_markdown("new_year", matches)
        md2 = gift_guide.generate_season_collection_markdown("new_year", matches)
        self.assertEqual(md1, md2)

    def test_21_cli_single_season(self):
        self._create_pack("p-d", "Diwali Joy", "diwali, celebration, festive")
        ret = gift_guide.main([
            "--season", "diwali",
            "--products-dir", str(self.products_dir),
            "--out-dir", str(self.out_dir),
            "--threshold", "2",
        ])
        self.assertEqual(ret, 0)
        self.assertTrue((self.out_dir / "diwali.md").is_file())

    def test_22_cli_all_seasons(self):
        self._create_pack("p-w", "Wedding Bliss", "wedding, bridal, ceremony")
        ret = gift_guide.main([
            "--products-dir", str(self.products_dir),
            "--out-dir", str(self.out_dir),
            "--threshold", "2",
        ])
        self.assertEqual(ret, 0)
        self.assertTrue((self.out_dir / "wedding-season.md").is_file())

    def test_23_compound_keyword_matching(self):
        p = self._create_pack("p-back", "Back To School Kit", "academic, student")
        _, tokens = gift_guide.extract_pack_tokens(p)
        self.assertIn("back to school", tokens)

    def test_24_sanitize_text_removes_money(self):
        raw = "Our seasonal guides offer values up to $100 and 500 INR."
        cleaned = gift_guide.sanitize_text(raw)
        self.assertNotIn("$100", cleaned)
        self.assertNotIn("500 INR", cleaned)

    def test_25_wedding_season_date_window_wrap(self):
        # Nov 1 to Feb 28
        self.assertTrue(gift_guide.is_in_season_window("wedding_season", date(2025, 11, 15)))
        self.assertTrue(gift_guide.is_in_season_window("wedding_season", date(2026, 1, 10)))
        self.assertTrue(gift_guide.is_in_season_window("wedding_season", date(2026, 2, 20)))
        self.assertFalse(gift_guide.is_in_season_window("wedding_season", date(2025, 6, 1)))

    def test_26_short_name_cut_at_40_chars(self):
        long_title = "Super Long Comprehensive Ultimate Academic Student Study Planner Template"
        p = self._create_pack("p-long", long_title, "school, student, class")
        name, _ = gift_guide.extract_pack_tokens(p)
        self.assertLessEqual(len(name), 40)


if __name__ == "__main__":
    unittest.main()
