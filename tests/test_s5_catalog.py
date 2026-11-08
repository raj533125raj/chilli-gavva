#!/usr/bin/env python3
"""tests/test_s5_catalog.py — Unit tests for catalog_sheet.py."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

import catalog_sheet


class TestCatalogSheet(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.test_dir = tempfile.mkdtemp(prefix="test_catalog_")
        self.products_dir = Path(self.test_dir) / "products"
        self.upload_dir = self.products_dir / "upload"
        self.out_dir = Path(self.test_dir) / "marketing" / "catalog"

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_pack(
        self,
        folder_name: str,
        title: str,
        desc: str = "A clean printable template.",
        what_you_get: str = "- 1 PDF Daily Habit Tracker\n- 1 PDF Weekly Review Sheet\n- Printable checklist guide",
        has_cover: bool = True,
    ) -> Path:
        pdir = self.upload_dir / folder_name
        pdir.mkdir(parents=True, exist_ok=True)
        content = (
            f"Title: {title}\n\n"
            f"Description: {desc}\n\n"
            f"What you get:\n{what_you_get}\n\n"
            f"Tags: planner, printable, focus\n"
        )
        (pdir / "listing.txt").write_text(content, encoding="utf-8")
        (pdir / "product.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)

        if has_cover and HAS_PILLOW:
            # Create a small dummy cover
            img = Image.new("RGB", (300, 200), (70, 130, 180))
            img.save(pdir / "cover.png")
        return pdir

    def test_01_constants_dimensions(self):
        self.assertEqual(catalog_sheet.PAGE_WIDTH, 2480)
        self.assertEqual(catalog_sheet.PAGE_HEIGHT, 3508)
        self.assertEqual(catalog_sheet.PACKS_PER_PAGE, 6)
        self.assertEqual(catalog_sheet.MARGIN_X, 120)
        self.assertEqual(catalog_sheet.MARGIN_Y, 120)

    def test_02_sanitize_text_strips_money(self):
        raw = "Complete habit guide worth $25 or 500 INR."
        cleaned = catalog_sheet.sanitize_text(raw)
        self.assertNotIn("$25", cleaned)
        self.assertNotIn("500 INR", cleaned)

    def test_03_sanitize_text_strips_brands(self):
        raw = "Works with Notion and Google Sheets template."
        cleaned = catalog_sheet.sanitize_text(raw)
        self.assertNotIn("Notion", cleaned)
        self.assertNotIn("Google Sheets", cleaned)

    def test_04_extract_pack_catalog_info(self):
        p = self._create_pack("p-01", "Daily Focus Journal")
        info = catalog_sheet.extract_pack_catalog_info(p)
        self.assertEqual(info["pack_id"], "p-01")
        self.assertEqual(info["short_name"], "Daily Focus Journal")
        self.assertEqual(len(info["bullets"]), 3)
        self.assertEqual(info["bullets"][0], "1 PDF Daily Habit Tracker")
        self.assertEqual(info["bullets"][1], "1 PDF Weekly Review Sheet")

    def test_05_extract_pack_fallback_bullets_when_missing(self):
        pdir = self.upload_dir / "p-nobullet"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "listing.txt").write_text("Title: Minimal Tracker\n\nDescription: Good\n", encoding="utf-8")
        (pdir / "product.zip").write_bytes(b"dummy")
        info = catalog_sheet.extract_pack_catalog_info(pdir)
        self.assertEqual(len(info["bullets"]), 3)
        self.assertIn("Instant digital download", info["bullets"])

    def test_06_wrap_text(self):
        font = catalog_sheet.get_font("regular", 20)
        text = "This is a clean long description for a printable calendar worksheet"
        wrapped = catalog_sheet.wrap_text(text, font, 200)
        self.assertGreater(len(wrapped), 1)
        self.assertEqual(" ".join(wrapped), text)

    def test_07_fit_title_font(self):
        title = "Super Long Ultimate Semester Academic Student Focus Planner"
        font, lines = catalog_sheet.fit_title_font(title, 400, 100)
        self.assertLessEqual(len(lines), 2)
        self.assertIsNotNone(font)

    @unittest.skipUnless(HAS_PILLOW, "Pillow required")
    def test_08_render_catalog_page_dimensions(self):
        packs = [
            {
                "pack_id": f"pack-{i}",
                "short_name": f"Product {i}",
                "bullets": ["Bullet 1", "Bullet 2", "Bullet 3"],
                "cover_path": None,
            }
            for i in range(1, 7)
        ]
        img = catalog_sheet.render_catalog_page(1, 1, packs)
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (2480, 3508))

    @unittest.skipUnless(HAS_PILLOW, "Pillow required")
    def test_09_render_catalog_page_with_covers(self):
        p = self._create_pack("p-cov", "Cover Pack", has_cover=True)
        info = catalog_sheet.extract_pack_catalog_info(p)
        img = catalog_sheet.render_catalog_page(1, 1, [info])
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (2480, 3508))

    def test_10_generate_index_markdown(self):
        packs = [
            {
                "pack_id": "p-1",
                "short_name": "Planner One",
                "bullets": ["Item A", "Item B", "Item C"],
                "cover_path": None,
            }
        ]
        md = catalog_sheet.generate_index_markdown(packs, 1)
        self.assertIn("# Product Catalog Index", md)
        self.assertIn("Total ready products: 1", md)
        self.assertIn("Planner One", md)
        self.assertIn("Item A", md)

    def test_11_build_catalog_single_page(self):
        for i in range(1, 4):
            self._create_pack(f"pack-{i}", f"Planner {i}")
        pages, index_md = catalog_sheet.build_catalog(self.products_dir, self.out_dir)
        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0].is_file())
        self.assertEqual(pages[0].name, "page-1.png")
        self.assertTrue(index_md.is_file())
        self.assertEqual(index_md.name, "index.md")

    def test_12_build_catalog_multiple_pages(self):
        # 8 packs should make 2 pages (6 per page)
        for i in range(1, 9):
            self._create_pack(f"pack-{i:02d}", f"Planner {i}")
        pages, index_md = catalog_sheet.build_catalog(self.products_dir, self.out_dir)
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].name, "page-1.png")
        self.assertEqual(pages[1].name, "page-2.png")

    def test_13_atomic_write_no_tmp_left(self):
        self._create_pack("pack-atm", "Atomic Pack")
        catalog_sheet.build_catalog(self.products_dir, self.out_dir)
        tmps = list(self.out_dir.glob(".*.tmp"))
        self.assertEqual(len(tmps), 0)

    def test_14_no_money_in_catalog_output(self):
        self._create_pack(
            "pack-money",
            "Rich Planner",
            desc="Value of 50 USD or $100 for your habits",
            what_you_get="- 1 PDF guide worth $10\n- Routine sheet\n- Goals",
        )
        _, index_md = catalog_sheet.build_catalog(self.products_dir, self.out_dir)
        content = index_md.read_text(encoding="utf-8")
        self.assertNotIn("$10", content)
        self.assertNotIn("50 USD", content)
        self.assertNotIn("$100", content)

    def test_15_cli_execution(self):
        self._create_pack("pack-cli", "CLI Test Pack")
        ret = catalog_sheet.main([
            "--products-dir", str(self.products_dir),
            "--out-dir", str(self.out_dir),
        ])
        self.assertEqual(ret, 0)
        self.assertTrue((self.out_dir / "page-1.png").is_file())
        self.assertTrue((self.out_dir / "index.md").is_file())

    def test_16_deterministic_page_ordering(self):
        self._create_pack("b-pack", "B Pack")
        self._create_pack("a-pack", "A Pack")
        _, index_md = catalog_sheet.build_catalog(self.products_dir, self.out_dir)
        content = index_md.read_text(encoding="utf-8")
        # a-pack should appear before b-pack
        pos_a = content.find("a-pack")
        pos_b = content.find("b-pack")
        self.assertGreater(pos_b, pos_a)

    def test_17_bullets_max_65_chars(self):
        long_bullet = "A" * 100
        pdir = self.upload_dir / "p-longb"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "listing.txt").write_text(f"Title: Test\n\nWhat you get:\n- {long_bullet}\n", encoding="utf-8")
        (pdir / "product.zip").write_bytes(b"dummy")
        info = catalog_sheet.extract_pack_catalog_info(pdir)
        self.assertLessEqual(len(info["bullets"][0]), 65)

    def test_18_qr_code_free_header(self):
        _, index_md = catalog_sheet.build_catalog(self.products_dir, self.out_dir)
        content = index_md.read_text(encoding="utf-8")
        self.assertIn("QR-code-free", content)

    def test_19_empty_products_catalog(self):
        pages, index_md = catalog_sheet.build_catalog(self.products_dir, self.out_dir)
        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0].is_file())
        content = index_md.read_text(encoding="utf-8")
        self.assertIn("Total ready products: 0", content)

    def test_20_get_font_fallbacks(self):
        f = catalog_sheet.get_font("regular", 16)
        if HAS_PILLOW:
            self.assertIsNotNone(f)

    def test_21_text_size_calculation(self):
        font = catalog_sheet.get_font("bold", 30)
        w, h = catalog_sheet.text_size("Hello", font)
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)

    def test_22_output_dir_outside_pack(self):
        pdir = self._create_pack("p-outside", "Pack Outside")
        catalog_sheet.build_catalog(self.products_dir, self.out_dir)
        self.assertFalse((pdir / "page-1.png").exists())
        self.assertFalse((pdir / "index.md").exists())

    def test_23_title_cut_to_40_chars(self):
        super_title = "The Most Extremely Long Title for an Incredible Daily Productivity Planner"
        p = self._create_pack("p-longtitle", super_title)
        info = catalog_sheet.extract_pack_catalog_info(p)
        self.assertLessEqual(len(info["short_name"]), 40)

    @unittest.skipUnless(HAS_PILLOW, "Pillow required")
    def test_24_footer_notice_on_page(self):
        p = self._create_pack("p-foot", "Footer Test Pack")
        info = catalog_sheet.extract_pack_catalog_info(p)
        img = catalog_sheet.render_catalog_page(1, 1, [info])
        self.assertEqual(img.size[0], 2480)
        self.assertEqual(img.size[1], 3508)

    def test_25_render_pack_card_without_pillow_handles_gracefully(self):
        # Should not raise exception
        with mock.patch("catalog_sheet.HAS_PILLOW", False):
            catalog_sheet.render_pack_card(None, {"short_name": "Test", "bullets": []}, 0, 0, 100, 100, None)

    def test_26_render_catalog_page_without_pillow_returns_none(self):
        with mock.patch("catalog_sheet.HAS_PILLOW", False):
            res = catalog_sheet.render_catalog_page(1, 1, [])
            self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
