"""tests/test_s2_seo_meta.py — Unit tests for seo_meta.py.

At least 25 tests covering:
- File name format (lowercase-hyphen, keyword first, <= 60 chars)
- Alt text format (descriptive, <= 125 chars)
- Atomic write to exported_content/marketing/<pack>/seo_meta.json
- Pack folder is NEVER modified or touched (files never renamed in place)
- Check that flags images with no alt text in plan
- Brand names filtering in proposed filenames and alt texts
- Money amount filtering in proposed filenames and alt texts
- Handling of cover, preview, mockup image roles
- Existing alt.txt integration
- Missing alt.txt synthesis fallback
- Multi-pack batch processing
- Determinism and idempotence
- CLI options (--pack, --all, --check-missing)
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import seo_meta


class TestS2SeoMeta(unittest.TestCase):
    def setUp(self):
        # Jules rules: clear all GITHUB_* and RUNNER_ENVIRONMENT variables using mock.patch.dict
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp_dir = tempfile.mkdtemp(prefix="test_seo_meta_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

    def test_01_clean_slug_basic(self):
        slug = seo_meta.clean_slug("Daily Habit Tracker - Cover Image!")
        self.assertEqual(slug, "daily-habit-tracker-cover-image")

    def test_02_clean_slug_length_limit(self):
        long_title = "super-extremely-long-title-with-many-descriptive-words-that-exceeds-the-maximum-length"
        slug = seo_meta.clean_slug(long_title, max_len=40)
        self.assertLessEqual(len(slug), 40)
        self.assertFalse(slug.endswith("-"))

    def test_03_clean_slug_removes_brands(self):
        slug = seo_meta.clean_slug("Notion Canva Etsy Habit Tracker")
        self.assertNotIn("notion", slug)
        self.assertNotIn("canva", slug)
        self.assertNotIn("etsy", slug)
        self.assertIn("habit-tracker", slug)

    def test_04_clean_slug_removes_money(self):
        slug = seo_meta.clean_slug("Daily Planner $10 USD")
        self.assertNotIn("10", slug)
        self.assertNotIn("usd", slug)
        self.assertEqual(slug, "daily-planner")

    def test_05_clean_alt_text_basic(self):
        alt = seo_meta.clean_alt_text("Clean habit tracker cover mockup layout")
        self.assertEqual(alt, "Clean habit tracker cover mockup layout")

    def test_06_clean_alt_text_length_limit(self):
        very_long = "This is a very detailed and overly descriptive sentence meant to exceed the one hundred and twenty five character limit for alt text in images to ensure truncation works nicely."
        alt = seo_meta.clean_alt_text(very_long, max_len=125)
        self.assertLessEqual(len(alt), 125)

    def test_07_clean_alt_text_removes_brands_and_money(self):
        alt = seo_meta.clean_alt_text("Notion template for $5 habit tracker")
        self.assertNotIn("Notion", alt)
        self.assertNotIn("$5", alt)
        self.assertIn("habit tracker", alt)

    def test_08_extract_pack_info_reads_listing_and_images(self):
        pack = Path(self.tmp_dir) / "pack1"
        pack.mkdir()
        (pack / "cover.png").write_bytes(b"dummy")
        (pack / "preview_1.png").write_bytes(b"dummy")
        (pack / "preview_2.png").write_bytes(b"dummy")
        listing = """Title: Habit Tracker Printable
Tags: habit tracker, daily habits
What you get:
- 12 monthly trackers
- Goal setting worksheet
"""
        (pack / "listing.txt").write_text(listing, encoding="utf-8")

        info = seo_meta.extract_pack_info(pack)
        self.assertEqual(info["title"], "Habit Tracker Printable")
        self.assertEqual(info["tags"], ["habit tracker", "daily habits"])
        self.assertEqual(len(info["images"]), 3)
        self.assertEqual(len(info["benefits"]), 2)

    def test_09_propose_seo_meta_plan_filenames(self):
        pack = Path(self.tmp_dir) / "pack1"
        pack.mkdir()
        (pack / "cover.png").write_bytes(b"dummy")
        (pack / "preview_1.png").write_bytes(b"dummy")
        (pack / "listing.txt").write_text("Title: Study Planner\nTags: study planner\n", encoding="utf-8")

        plan = seo_meta.propose_seo_meta_plan(pack)
        images = plan["images"]
        self.assertEqual(len(images), 2)
        for img in images:
            p_name = img["proposed_filename"]
            self.assertLessEqual(len(p_name), 60)
            self.assertTrue(p_name.islower())
            self.assertTrue(p_name.startswith("study-planner"))
            self.assertTrue(img["has_alt"])
            self.assertLessEqual(len(img["alt_text"]), 125)

    def test_10_propose_seo_meta_plan_does_not_rename_files(self):
        pack = Path(self.tmp_dir) / "pack_untouched"
        pack.mkdir()
        c_file = pack / "cover.png"
        c_file.write_bytes(b"original data")
        (pack / "listing.txt").write_text("Title: Daily Log\nTags: daily log\n", encoding="utf-8")

        plan = seo_meta.propose_seo_meta_plan(pack)
        # Original file MUST still exist with original name and content
        self.assertTrue(c_file.is_file())
        self.assertEqual(c_file.read_bytes(), b"original data")
        self.assertFalse(plan["renamed_in_place"])

    def test_11_check_missing_alt_texts_none_missing(self):
        plan = {
            "images": [
                {"original_filename": "cover.png", "alt_text": "Good alt", "has_alt": True},
                {"original_filename": "preview.png", "alt_text": "Another alt", "has_alt": True},
            ]
        }
        missing = seo_meta.check_missing_alt_texts(plan)
        self.assertEqual(len(missing), 0)

    def test_12_check_missing_alt_texts_flags_missing(self):
        plan = {
            "images": [
                {"original_filename": "cover.png", "alt_text": "Good alt", "has_alt": True},
                {"original_filename": "preview.png", "alt_text": "", "has_alt": False},
            ]
        }
        missing = seo_meta.check_missing_alt_texts(plan)
        self.assertEqual(missing, ["preview.png"])

    def test_13_generate_pack_seo_meta_atomic_write(self):
        pack = Path(self.tmp_dir) / "pack_atomic"
        pack.mkdir()
        (pack / "cover.png").write_bytes(b"dummy")
        (pack / "listing.txt").write_text("Title: Budget Sheet\nTags: budget sheet\n", encoding="utf-8")

        m_dir = Path(self.tmp_dir) / "marketing"
        out_file = seo_meta.generate_pack_seo_meta(pack, marketing_dir=m_dir)
        self.assertTrue(out_file.is_file())
        self.assertEqual(out_file, m_dir / "pack_atomic" / "seo_meta.json")

        plan = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertEqual(plan["pack_name"], "pack_atomic")
        self.assertEqual(plan["title"], "Budget Sheet")

    def test_14_uses_existing_alt_txt_if_present(self):
        pack = Path(self.tmp_dir) / "pack_alt"
        pack.mkdir()
        (pack / "cover.png").write_bytes(b"dummy")
        (pack / "listing.txt").write_text("Title: Habit Tracker\nTags: habit tracker\n", encoding="utf-8")
        (pack / "alt.txt").write_text("cover: Hand-crafted habit tracker cover design\n", encoding="utf-8")

        plan = seo_meta.propose_seo_meta_plan(pack)
        img_entry = plan["images"][0]
        self.assertEqual(img_entry["alt_text"], "Hand-crafted habit tracker cover design")

    def test_15_synthesizes_alts_using_benefits(self):
        pack = Path(self.tmp_dir) / "pack_benefits"
        pack.mkdir()
        (pack / "cover.png").write_bytes(b"dummy")
        (pack / "preview_1.png").write_bytes(b"dummy")
        listing = """Title: Habit Tracker
Tags: habit tracker
What you get:
- Monthly sleep log sheet
"""
        (pack / "listing.txt").write_text(listing, encoding="utf-8")

        plan = seo_meta.propose_seo_meta_plan(pack)
        p1 = plan["images"][1]
        self.assertIn("Monthly sleep log sheet", p1["alt_text"])

    def test_16_generate_all_seo_meta(self):
        products = Path(self.tmp_dir) / "products" / "upload"
        products.mkdir(parents=True)

        for name in ("pack_a", "pack_b"):
            p = products / name
            p.mkdir()
            (p / "cover.png").write_bytes(b"dummy")
            (p / "listing.txt").write_text(f"Title: Pack {name}\nTags: {name}\n", encoding="utf-8")

        m_dir = Path(self.tmp_dir) / "marketing"
        written = seo_meta.generate_all_seo_meta(products_dir=Path(self.tmp_dir) / "products", marketing_dir=m_dir)
        self.assertEqual(len(written), 2)
        for p in written:
            self.assertTrue(p.is_file())

    def test_17_idempotence(self):
        pack = Path(self.tmp_dir) / "pack_idemp"
        pack.mkdir()
        (pack / "cover.png").write_bytes(b"dummy")
        (pack / "listing.txt").write_text("Title: Routine Plan\nTags: routine plan\n", encoding="utf-8")

        m_dir = Path(self.tmp_dir) / "marketing"
        out1 = seo_meta.generate_pack_seo_meta(pack, marketing_dir=m_dir)
        c1 = out1.read_text(encoding="utf-8")

        out2 = seo_meta.generate_pack_seo_meta(pack, marketing_dir=m_dir)
        c2 = out2.read_text(encoding="utf-8")

        self.assertEqual(c1, c2)

    def test_18_no_brands_in_proposed_filenames(self):
        pack = Path(self.tmp_dir) / "pack_brand_img"
        pack.mkdir()
        (pack / "canva_mockup.png").write_bytes(b"dummy")
        (pack / "listing.txt").write_text("Title: Notion Habit Tracker\nTags: notion, habit tracker\n", encoding="utf-8")

        plan = seo_meta.propose_seo_meta_plan(pack)
        img = plan["images"][0]
        self.assertNotIn("canva", img["proposed_filename"])
        self.assertNotIn("notion", img["proposed_filename"])

    def test_19_no_money_in_proposed_filenames(self):
        pack = Path(self.tmp_dir) / "pack_money_img"
        pack.mkdir()
        (pack / "preview_10usd.png").write_bytes(b"dummy")
        (pack / "listing.txt").write_text("Title: Study Tracker $10\nTags: study tracker\n", encoding="utf-8")

        plan = seo_meta.propose_seo_meta_plan(pack)
        img = plan["images"][0]
        self.assertNotIn("10usd", img["proposed_filename"])
        self.assertNotIn("10", img["proposed_filename"])

    def test_20_pack_with_no_images(self):
        pack = Path(self.tmp_dir) / "pack_no_img"
        pack.mkdir()
        (pack / "listing.txt").write_text("Title: Audio Pack\nTags: audio\n", encoding="utf-8")

        plan = seo_meta.propose_seo_meta_plan(pack)
        self.assertEqual(plan["images_count"], 0)
        self.assertEqual(len(plan["images"]), 0)
        self.assertFalse(plan["has_missing_alts"])

    def test_21_markdown_listing_parsing(self):
        pack = Path(self.tmp_dir) / "pack_md"
        pack.mkdir()
        (pack / "cover.jpg").write_bytes(b"dummy")
        (pack / "listing.md").write_text("# Fitness Journal\n## Tags\nfitness journal, gym log\n", encoding="utf-8")

        plan = seo_meta.propose_seo_meta_plan(pack)
        self.assertEqual(plan["title"], "Fitness Journal")
        self.assertEqual(plan["keyword_slug"], "fitness-journal")

    def test_22_cli_pack_option(self):
        pack = Path(self.tmp_dir) / "pack_cli"
        pack.mkdir()
        (pack / "cover.png").write_bytes(b"dummy")
        (pack / "listing.txt").write_text("Title: Daily Plan\nTags: daily plan\n", encoding="utf-8")

        m_dir = Path(self.tmp_dir) / "marketing"
        with mock.patch("sys.argv", [
            "seo_meta.py",
            "--pack", str(pack),
            "--marketing-dir", str(m_dir),
        ]):
            seo_meta.main()
            self.assertTrue((m_dir / "pack_cli" / "seo_meta.json").is_file())

    def test_23_cli_all_option(self):
        products = Path(self.tmp_dir) / "products" / "upload"
        products.mkdir(parents=True)
        p = products / "pack_all_test"
        p.mkdir()
        (p / "cover.png").write_bytes(b"dummy")
        (p / "listing.txt").write_text("Title: All Test\nTags: test\n", encoding="utf-8")

        m_dir = Path(self.tmp_dir) / "marketing"
        with mock.patch("sys.argv", [
            "seo_meta.py",
            "--all",
            "--products-dir", str(Path(self.tmp_dir) / "products"),
            "--marketing-dir", str(m_dir),
        ]):
            seo_meta.main()
            self.assertTrue((m_dir / "pack_all_test" / "seo_meta.json").is_file())

    def test_24_cli_check_missing_flag_success(self):
        pack = Path(self.tmp_dir) / "pack_check"
        pack.mkdir()
        (pack / "cover.png").write_bytes(b"dummy")
        (pack / "listing.txt").write_text("Title: Daily Plan\nTags: daily plan\n", encoding="utf-8")

        m_dir = Path(self.tmp_dir) / "marketing"
        with mock.patch("sys.argv", [
            "seo_meta.py",
            "--pack", str(pack),
            "--marketing-dir", str(m_dir),
            "--check-missing",
        ]):
            seo_meta.main()
            self.assertTrue((m_dir / "pack_check" / "seo_meta.json").is_file())

    def test_25_cli_no_args_prints_help(self):
        with mock.patch("sys.argv", ["seo_meta.py"]), mock.patch.object(argparse.ArgumentParser, "print_help") as mock_help:
            seo_meta.main()
            mock_help.assert_called_once()


if __name__ == "__main__":
    unittest.main()
