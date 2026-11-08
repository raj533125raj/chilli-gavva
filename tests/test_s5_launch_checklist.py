#!/usr/bin/env python3
"""tests/test_s5_launch_checklist.py — Unit tests for launch_checklist.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import launch_checklist


class TestLaunchChecklist(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* env in setUp with mock.patch.dict
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.test_dir = tempfile.mkdtemp(prefix="test_launch_chk_")
        self.products_dir = Path(self.test_dir) / "products"
        self.upload_dir = self.products_dir / "upload"
        self.marketing_dir = Path(self.test_dir) / "marketing"

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_pack(
        self,
        pack_name: str,
        title: str = "Focus Habit Tracker",
        desc: str = "A clean printable habit tracker for daily focus.",
        tags: str = "habit, focus, planner, routine, goals",
        what_you_get: str = "- 1 PDF Daily Habit Tracker\n- 1 PDF Weekly Review Sheet",
        extra_files: list[str] | None = None,
    ) -> Path:
        pdir = self.upload_dir / pack_name
        pdir.mkdir(parents=True, exist_ok=True)

        content = (
            f"Title: {title}\n\n"
            f"Description: {desc}\n\n"
            f"What you get:\n{what_you_get}\n\n"
            f"Tags: {tags}\n"
        )
        (pdir / "listing.txt").write_text(content, encoding="utf-8")
        (pdir / "product.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        (pdir / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        if extra_files:
            for ef in extra_files:
                (pdir / ef).write_text(f"dummy content for {ef}", encoding="utf-8")
        return pdir

    def test_01_discover_ready_packs_empty(self):
        packs = launch_checklist.discover_ready_packs(self.products_dir)
        self.assertEqual(len(packs), 0)

    def test_02_discover_ready_packs_finds_pack(self):
        self._create_pack("pack-1")
        packs = launch_checklist.discover_ready_packs(self.products_dir)
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0].name, "pack-1")

    def test_03_discover_ready_packs_ignores_hidden_and_non_packs(self):
        (self.upload_dir / ".hidden_folder").mkdir()
        (self.upload_dir / "not_a_pack").mkdir()  # no listing.txt
        self._create_pack("real-pack")
        packs = launch_checklist.discover_ready_packs(self.products_dir)
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0].name, "real-pack")

    def test_04_get_pack_files_to_upload_defaults(self):
        pdir = self._create_pack("pack-files")
        files = launch_checklist.get_pack_files_to_upload(pdir)
        self.assertIn("product.zip", files)

    def test_05_get_pack_files_to_upload_with_license(self):
        pdir = self._create_pack("pack-license", extra_files=["LICENSE.txt", "bonus.pdf"])
        files = launch_checklist.get_pack_files_to_upload(pdir)
        self.assertIn("product.zip", files)
        self.assertIn("LICENSE.txt", files)
        self.assertIn("bonus.pdf", files)

    def test_06_get_cover_and_preview_order(self):
        pdir = self._create_pack("pack-images")
        (pdir / "preview-2.png").write_bytes(b"dummy")
        (pdir / "preview-1.png").write_bytes(b"dummy")
        (pdir / "preview-10.png").write_bytes(b"dummy")

        order = launch_checklist.get_cover_and_preview_order(pdir)
        self.assertEqual(order[0], "cover.png")
        self.assertEqual(order[1], "preview-1.png")
        self.assertEqual(order[2], "preview-2.png")
        self.assertEqual(order[3], "preview-10.png")

    def test_07_get_pin_titles_from_pins_json(self):
        mktg_pack = self.marketing_dir / "pack-pins"
        pins_dir = mktg_pack / "pins"
        pins_dir.mkdir(parents=True, exist_ok=True)
        pins_data = [
            {"title": "How to Build Habits in 30 Days", "file": "pin1.png"},
            {"title": "The Daily Focus Method", "file": "pin2.png"},
            {"title": "Track Your Habits", "file": "pin3.png"},
            {"title": "Simple Habit Routine", "file": "pin4.png"},
            {"title": "Minimal Daily Habit Tracker", "file": "pin5.png"},
        ]
        (pins_dir / "pins.json").write_text(json.dumps(pins_data), encoding="utf-8")

        titles = launch_checklist.get_pin_titles(mktg_pack)
        self.assertEqual(len(titles), 5)
        self.assertEqual(titles[0], "How to Build Habits in 30 Days")
        self.assertEqual(titles[4], "Minimal Daily Habit Tracker")

    def test_08_get_pin_titles_from_ab_titles_fallback(self):
        mktg_pack = self.marketing_dir / "pack-ab"
        pins_dir = mktg_pack / "pins"
        pins_dir.mkdir(parents=True, exist_ok=True)
        ab_content = "1. First Habit Title\n2. Second Habit Title\n3. Third Habit Title\n4. Fourth\n5. Fifth\n"
        (pins_dir / "ab_titles.txt").write_text(ab_content, encoding="utf-8")

        titles = launch_checklist.get_pin_titles(mktg_pack)
        self.assertEqual(len(titles), 5)
        self.assertEqual(titles[0], "First Habit Title")

    def test_09_get_pin_titles_empty_when_no_pins(self):
        mktg_pack = self.marketing_dir / "pack-empty"
        titles = launch_checklist.get_pin_titles(mktg_pack)
        self.assertEqual(titles, [])

    def test_10_get_marketing_paths(self):
        paths = launch_checklist.get_marketing_paths("pack-xyz", self.marketing_dir)
        mktg_str = str(self.marketing_dir).replace("\\", "/")
        self.assertEqual(paths["carousel_dir"], f"{mktg_str}/pack-xyz/carousel")
        self.assertEqual(paths["carousel_caption"], f"{mktg_str}/pack-xyz/carousel/caption.txt")
        self.assertEqual(paths["pins_dir"], f"{mktg_str}/pack-xyz/pins")
        self.assertEqual(paths["blog_md"], f"{mktg_str}/pack-xyz/blog.md")

    def test_11_generate_launch_markdown_structure(self):
        pdir = self._create_pack("pack-struct")
        md = launch_checklist.generate_launch_markdown("pack-struct", pdir, self.marketing_dir)
        self.assertIn("# Launch Checklist: Focus Habit Tracker", md)
        self.assertIn("Price: SET BY OWNER", md)
        self.assertIn("GUMROAD", md)
        self.assertIn("TOPMATE", md)
        self.assertIn("INSTAMOJO", md)
        self.assertIn("- [ ]", md)

    def test_12_generate_launch_markdown_checkboxes(self):
        pdir = self._create_pack("pack-chk")
        md = launch_checklist.generate_launch_markdown("pack-chk", pdir, self.marketing_dir)
        # Check presence of several specific manual check items
        self.assertIn("- [ ] Verify `product.zip` contents", md)
        self.assertIn("- [ ] Upload file: `product.zip`", md)
        self.assertIn("- [ ] Slot 1 (Cover Image): `cover.png`", md)
        self.assertIn("- [ ] Set price manually: **SET BY OWNER**", md)
        self.assertIn("- [ ] Post Instagram carousel manually", md)

    def test_13_generate_launch_markdown_includes_listing_fields_blocks(self):
        pdir = self._create_pack("pack-blocks")
        md = launch_checklist.generate_launch_markdown("pack-blocks", pdir, self.marketing_dir)
        self.assertIn("Name: Focus Habit Tracker", md)
        self.assertIn("Short description:", md)
        self.assertIn("Long description:", md)

    def test_14_generate_launch_markdown_with_pins_included(self):
        pdir = self._create_pack("pack-with-pins")
        mktg_pack = self.marketing_dir / "pack-with-pins"
        pins_dir = mktg_pack / "pins"
        pins_dir.mkdir(parents=True, exist_ok=True)
        (pins_dir / "pins.json").write_text(
            json.dumps([{"title": "Awesome Pin 1"}, {"title": "Awesome Pin 2"}]),
            encoding="utf-8",
        )
        md = launch_checklist.generate_launch_markdown("pack-with-pins", pdir, self.marketing_dir)
        self.assertIn("Awesome Pin 1", md)
        self.assertIn("Awesome Pin 2", md)

    def test_15_write_launch_checklist_creates_file(self):
        pdir = self._create_pack("pack-write")
        out_file = launch_checklist.write_launch_checklist(pdir, self.marketing_dir)
        self.assertTrue(out_file.is_file())
        self.assertEqual(out_file.name, "LAUNCH.md")
        self.assertEqual(out_file.parent.name, "pack-write")

    def test_16_write_launch_checklist_outside_pack_folder(self):
        pdir = self._create_pack("pack-outside")
        out_file = launch_checklist.write_launch_checklist(pdir, self.marketing_dir)
        # The checklist must NOT be inside the product pack folder
        self.assertFalse((pdir / "LAUNCH.md").exists())
        self.assertTrue(str(out_file).startswith(str(self.marketing_dir)))

    def test_17_no_money_amounts_in_checklist(self):
        pdir = self._create_pack("pack-nomoney")
        md = launch_checklist.generate_launch_markdown("pack-nomoney", pdir, self.marketing_dir)
        hit = launch_checklist.check_for_money(md)
        self.assertIsNone(hit, f"Money found in checklist: {hit}")
        self.assertNotIn("$", md)
        self.assertNotIn("USD", md)
        self.assertNotIn("INR", md)

    def test_18_strips_accidental_money_in_description(self):
        pdir = self._create_pack(
            "pack-leaked-money",
            desc="Get this bundle normally worth 50 USD for great focus.",
        )
        md = launch_checklist.generate_launch_markdown("pack-leaked-money", pdir, self.marketing_dir)
        self.assertNotIn("50 USD", md)
        hit = launch_checklist.check_for_money(md)
        self.assertIsNone(hit)

    def test_19_atomic_write_leaves_no_tmp_files(self):
        pdir = self._create_pack("pack-atomic")
        launch_checklist.write_launch_checklist(pdir, self.marketing_dir)
        mktg_pack = self.marketing_dir / "pack-atomic"
        tmp_files = [f for f in mktg_pack.iterdir() if f.name.startswith(".launch_")]
        self.assertEqual(len(tmp_files), 0)

    def test_20_deterministic_output(self):
        pdir = self._create_pack("pack-determ")
        md1 = launch_checklist.generate_launch_markdown("pack-determ", pdir, self.marketing_dir)
        md2 = launch_checklist.generate_launch_markdown("pack-determ", pdir, self.marketing_dir)
        self.assertEqual(md1, md2)

    def test_21_process_all_ready_packs(self):
        self._create_pack("pack-a")
        self._create_pack("pack-b")
        results = launch_checklist.process_all_ready_packs(self.products_dir, self.marketing_dir)
        self.assertEqual(len(results), 2)
        self.assertTrue((self.marketing_dir / "pack-a" / "LAUNCH.md").is_file())
        self.assertTrue((self.marketing_dir / "pack-b" / "LAUNCH.md").is_file())

    def test_22_missing_listing_txt_raises(self):
        bad_dir = self.upload_dir / "bad-pack"
        bad_dir.mkdir()
        with self.assertRaises(FileNotFoundError):
            launch_checklist.generate_launch_markdown("bad-pack", bad_dir, self.marketing_dir)

    def test_23_cli_single_pack(self):
        self._create_pack("cli-single")
        ret = launch_checklist.main([
            "--pack", "cli-single",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(ret, 0)
        self.assertTrue((self.marketing_dir / "cli-single" / "LAUNCH.md").is_file())

    def test_24_cli_all_packs(self):
        self._create_pack("cli-all-1")
        self._create_pack("cli-all-2")
        ret = launch_checklist.main([
            "--all",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(ret, 0)
        self.assertTrue((self.marketing_dir / "cli-all-1" / "LAUNCH.md").is_file())
        self.assertTrue((self.marketing_dir / "cli-all-2" / "LAUNCH.md").is_file())

    def test_25_cli_nonexistent_pack_returns_error(self):
        ret = launch_checklist.main([
            "--pack", "does-not-exist",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(ret, 1)

    def test_26_clean_no_money_or_brands_function(self):
        text = "This item is worth $45.00 or 100 USD or Rs. 500."
        cleaned = launch_checklist.clean_no_money_or_brands(text)
        self.assertNotIn("$45.00", cleaned)
        self.assertNotIn("100 USD", cleaned)
        self.assertNotIn("Rs. 500", cleaned)


if __name__ == "__main__":
    unittest.main()
