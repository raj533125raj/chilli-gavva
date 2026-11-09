#!/usr/bin/env python3
"""tests/test_t5c_index.py — Unit tests for marketing_index.py."""

from __future__ import annotations

import html
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock

import marketing_index

CURRENCY_AMOUNT = re.compile(
    r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
    re.IGNORECASE,
)


class TestMarketingIndex(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* env in setUp with mock.patch.dict
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.test_dir = tempfile.mkdtemp(prefix="test_t5c_mkt_")
        self.marketing_dir = Path(self.test_dir) / "marketing"
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_full_pack(self, pack_id: str) -> Path:
        """Create a complete sample pack with all marketing assets."""
        pdir = self.marketing_dir / pack_id
        pdir.mkdir(parents=True, exist_ok=True)

        # Covers
        covers_dir = pdir / "covers"
        covers_dir.mkdir(parents=True, exist_ok=True)
        (covers_dir / "cover-a.png").write_bytes(b"\x89PNG\r\n\x1a\nfakecovera")
        (covers_dir / "cover-b.png").write_bytes(b"\x89PNG\r\n\x1a\nfakecoverb")

        # Pins
        pins_dir = pdir / "pins"
        pins_dir.mkdir(parents=True, exist_ok=True)
        (pins_dir / "pin-1.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepin1")
        (pins_dir / "pin-2.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepin2")
        (pins_dir / "ab_titles.txt").write_text("Title A\nTitle B\n", encoding="utf-8")

        # Carousel
        carousel_dir = pdir / "carousel"
        carousel_dir.mkdir(parents=True, exist_ok=True)
        (carousel_dir / "slide-1.png").write_bytes(b"\x89PNG\r\n\x1a\nfakeslide1")
        (carousel_dir / "slide-2.png").write_bytes(b"\x89PNG\r\n\x1a\nfakeslide2")
        (carousel_dir / "caption.txt").write_text("Clean daily planner slides", encoding="utf-8")

        # Reel
        reel_dir = pdir / "reel"
        reel_dir.mkdir(parents=True, exist_ok=True)
        (reel_dir / "frame-1.png").write_bytes(b"\x89PNG\r\n\x1a\nfakeframe1")
        (reel_dir / "frame-2.png").write_bytes(b"\x89PNG\r\n\x1a\nfakeframe2")
        (reel_dir / "shorts.md").write_text("# Shorts Script\n15-second routine", encoding="utf-8")

        # Markdown / HTML documents
        (pdir / "blog.md").write_text("# Habits That Stick\nBest ways to track", encoding="utf-8")
        (pdir / "LAUNCH.md").write_text("# Launch Checklist\n- [ ] Gumroad upload", encoding="utf-8")

        # Landing page
        landing_dir = pdir / "landing"
        landing_dir.mkdir(parents=True, exist_ok=True)
        (landing_dir / "index.html").write_text("<!DOCTYPE html><html><body>Landing</body></html>", encoding="utf-8")

        # Review requests
        (pdir / "review_request.md").write_text("# Review Request\nThank you for downloading!", encoding="utf-8")

        # Extra files
        (pdir / "seo_meta.json").write_text('{"main_keyword": "planner"}', encoding="utf-8")

        return pdir

    def test_01_empty_marketing_directory(self):
        """Empty marketing directory returns empty list and renders empty state."""
        packs = marketing_index.scan_marketing(self.marketing_dir)
        self.assertEqual(packs, [])
        html_out = marketing_index.render_html(packs)
        self.assertIn("Packs levu", html_out)
        self.assertIn("Packs: <strong>0</strong>", html_out)

    def test_02_nonexistent_marketing_directory(self):
        """Non-existent directory path returns empty list safely."""
        missing_dir = self.marketing_dir / "does_not_exist"
        packs = marketing_index.scan_marketing(missing_dir)
        self.assertEqual(packs, [])

    def test_03_excludes_global_non_pack_dirs(self):
        """Global directories like catalog, collections, newsletter, site, ops are excluded."""
        for g_name in ("catalog", "collections", "newsletter", "site", "ops", "bundles", "shared", "common"):
            g_dir = self.marketing_dir / g_name
            g_dir.mkdir(parents=True, exist_ok=True)
            (g_dir / "file.txt").write_text("sample", encoding="utf-8")

        # Real pack
        real_pack = self.marketing_dir / "focus-pack"
        real_pack.mkdir(parents=True, exist_ok=True)
        (real_pack / "blog.md").write_text("# Focus", encoding="utf-8")

        packs = marketing_index.scan_marketing(self.marketing_dir)
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0]["id"], "focus-pack")

    def test_04_custom_exclude_dirs(self):
        """Custom excluded directories via exclude_dirs are honored."""
        (self.marketing_dir / "pack-one").mkdir()
        (self.marketing_dir / "pack-ignore").mkdir()
        packs = marketing_index.scan_marketing(self.marketing_dir, exclude_dirs={"pack-ignore"})
        pack_ids = [p["id"] for p in packs]
        self.assertIn("pack-one", pack_ids)
        self.assertNotIn("pack-ignore", pack_ids)

    def test_05_hidden_folders_excluded(self):
        """Hidden folders starting with dot are skipped."""
        (self.marketing_dir / ".hidden_pack").mkdir()
        (self.marketing_dir / "visible_pack").mkdir()
        packs = marketing_index.scan_marketing(self.marketing_dir)
        pack_ids = [p["id"] for p in packs]
        self.assertNotIn(".hidden_pack", pack_ids)
        self.assertIn("visible_pack", pack_ids)

    def test_06_pack_with_full_assets(self):
        """Pack with all marketing assets has all statuses ready and ready_count=9."""
        self._create_full_pack("full-habit-pack")
        packs = marketing_index.scan_marketing(self.marketing_dir)
        self.assertEqual(len(packs), 1)
        pack = packs[0]
        self.assertEqual(pack["id"], "full-habit-pack")
        self.assertEqual(pack["ready_count"], 9)
        self.assertEqual(pack["total_count"], 9)
        self.assertTrue(pack["is_complete"])
        for key in ("covers", "pins", "carousel", "reel", "blog", "launch", "shorts", "landing", "reviews"):
            self.assertEqual(pack["status"][key], "ready")

    def test_07_pack_with_zero_assets(self):
        """Completely empty pack folder has all statuses missing and ready_count=0."""
        empty_pack = self.marketing_dir / "empty-pack"
        empty_pack.mkdir()
        packs = marketing_index.scan_marketing(self.marketing_dir)
        self.assertEqual(len(packs), 1)
        pack = packs[0]
        self.assertEqual(pack["id"], "empty-pack")
        self.assertEqual(pack["ready_count"], 0)
        self.assertEqual(pack["total_count"], 9)
        self.assertFalse(pack["is_complete"])
        for key in pack["status"]:
            self.assertEqual(pack["status"][key], "missing")

    def test_08_pack_with_partial_subset_covers_only(self):
        """Pack with only covers has covers ready and all others missing."""
        pdir = self.marketing_dir / "covers-only-pack"
        cov = pdir / "covers"
        cov.mkdir(parents=True)
        (cov / "cover.png").write_bytes(b"dummy")
        packs = marketing_index.scan_marketing(self.marketing_dir)
        pack = packs[0]
        self.assertEqual(pack["status"]["covers"], "ready")
        self.assertEqual(pack["status"]["pins"], "missing")
        self.assertEqual(pack["status"]["carousel"], "missing")
        self.assertEqual(pack["ready_count"], 1)

    def test_09_pack_with_partial_subset_pins_only(self):
        """Pack with only pins has pins ready and all others missing."""
        pdir = self.marketing_dir / "pins-only-pack"
        pins = pdir / "pins"
        pins.mkdir(parents=True)
        (pins / "pin-1.png").write_bytes(b"dummy")
        packs = marketing_index.scan_marketing(self.marketing_dir)
        pack = packs[0]
        self.assertEqual(pack["status"]["pins"], "ready")
        self.assertEqual(pack["status"]["covers"], "missing")
        self.assertEqual(pack["ready_count"], 1)

    def test_10_pack_with_partial_subset_carousel_only(self):
        """Pack with only carousel slides has carousel ready and all others missing."""
        pdir = self.marketing_dir / "carousel-only-pack"
        car = pdir / "carousel"
        car.mkdir(parents=True)
        (car / "slide-1.png").write_bytes(b"dummy")
        packs = marketing_index.scan_marketing(self.marketing_dir)
        pack = packs[0]
        self.assertEqual(pack["status"]["carousel"], "ready")
        self.assertEqual(pack["status"]["reel"], "missing")
        self.assertEqual(pack["ready_count"], 1)

    def test_11_pack_with_partial_subset_reel_only(self):
        """Pack with only reel frames has reel ready and all others missing."""
        pdir = self.marketing_dir / "reel-only-pack"
        reel = pdir / "reel"
        reel.mkdir(parents=True)
        (reel / "frame-1.png").write_bytes(b"dummy")
        packs = marketing_index.scan_marketing(self.marketing_dir)
        pack = packs[0]
        self.assertEqual(pack["status"]["reel"], "ready")
        self.assertEqual(pack["status"]["carousel"], "missing")
        self.assertEqual(pack["ready_count"], 1)

    def test_12_pack_with_blog_md(self):
        """Detects blog.md in pack root."""
        pdir = self.marketing_dir / "blog-pack"
        pdir.mkdir()
        (pdir / "blog.md").write_text("# Article", encoding="utf-8")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(pack["status"]["blog"], "ready")
        self.assertEqual(pack["blog"], "blog-pack/blog.md")

    def test_13_pack_with_blog_in_subdir(self):
        """Detects blog/blog.md inside a blog subdirectory."""
        pdir = self.marketing_dir / "blog-sub-pack"
        b_sub = pdir / "blog"
        b_sub.mkdir(parents=True)
        (b_sub / "blog.md").write_text("# Article Sub", encoding="utf-8")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(pack["status"]["blog"], "ready")
        self.assertEqual(pack["blog"], "blog-sub-pack/blog/blog.md")

    def test_14_pack_with_launch_md(self):
        """Detects LAUNCH.md checklist."""
        pdir = self.marketing_dir / "launch-pack"
        pdir.mkdir()
        (pdir / "LAUNCH.md").write_text("# Checklist", encoding="utf-8")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(pack["status"]["launch"], "ready")
        self.assertEqual(pack["launch"], "launch-pack/LAUNCH.md")

    def test_15_pack_with_launch_lowercase(self):
        """Detects lowercase launch.md."""
        pdir = self.marketing_dir / "launch-lower-pack"
        pdir.mkdir()
        (pdir / "launch.md").write_text("# Checklist Lower", encoding="utf-8")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(pack["status"]["launch"], "ready")
        self.assertEqual(pack["launch"], "launch-lower-pack/launch.md")

    def test_16_pack_with_shorts_in_reel(self):
        """Detects shorts.md script inside reel/ subdir."""
        pdir = self.marketing_dir / "shorts-reel-pack"
        r_dir = pdir / "reel"
        r_dir.mkdir(parents=True)
        (r_dir / "shorts.md").write_text("# Shorts Script", encoding="utf-8")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(pack["status"]["shorts"], "ready")
        self.assertEqual(pack["shorts"], "shorts-reel-pack/reel/shorts.md")

    def test_17_pack_with_shorts_in_root(self):
        """Detects shorts.md script inside pack root."""
        pdir = self.marketing_dir / "shorts-root-pack"
        pdir.mkdir()
        (pdir / "shorts.md").write_text("# Shorts Root", encoding="utf-8")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(pack["status"]["shorts"], "ready")
        self.assertEqual(pack["shorts"], "shorts-root-pack/shorts.md")

    def test_18_pack_with_landing_page(self):
        """Detects landing/index.html page."""
        pdir = self.marketing_dir / "landing-pack"
        l_dir = pdir / "landing"
        l_dir.mkdir(parents=True)
        (l_dir / "index.html").write_text("<html>Landing</html>", encoding="utf-8")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(pack["status"]["landing"], "ready")
        self.assertEqual(pack["landing"], "landing-pack/landing/index.html")

    def test_19_pack_with_landing_page_direct_html(self):
        """Detects landing.html directly under pack folder."""
        pdir = self.marketing_dir / "landing-direct-pack"
        pdir.mkdir()
        (pdir / "landing.html").write_text("<html>Landing Direct</html>", encoding="utf-8")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(pack["status"]["landing"], "ready")
        self.assertEqual(pack["landing"], "landing-direct-pack/landing.html")

    def test_20_pack_with_review_request_md(self):
        """Detects review_request.md."""
        pdir = self.marketing_dir / "review-md-pack"
        pdir.mkdir()
        (pdir / "review_request.md").write_text("# Review", encoding="utf-8")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(pack["status"]["reviews"], "ready")
        self.assertEqual(pack["reviews"], "review-md-pack/review_request.md")

    def test_21_pack_with_review_requests_txt(self):
        """Detects review_requests.txt."""
        pdir = self.marketing_dir / "review-txt-pack"
        pdir.mkdir()
        (pdir / "review_requests.txt").write_text("Please review", encoding="utf-8")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(pack["status"]["reviews"], "ready")
        self.assertEqual(pack["reviews"], "review-txt-pack/review_requests.txt")

    def test_22_pack_with_reviews_in_subdir(self):
        """Detects review file inside reviews/ subdirectory."""
        pdir = self.marketing_dir / "reviews-sub-pack"
        r_dir = pdir / "reviews"
        r_dir.mkdir(parents=True)
        (r_dir / "review_request.md").write_text("# Review in sub", encoding="utf-8")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(pack["status"]["reviews"], "ready")
        self.assertEqual(pack["reviews"], "reviews-sub-pack/reviews/review_request.md")

    def test_23_status_row_rendering(self):
        """HTML output contains status row with ready and missing badges."""
        self._create_full_pack("ready-pack")
        (self.marketing_dir / "empty-pack").mkdir()
        packs = marketing_index.scan_marketing(self.marketing_dir)
        html_out = marketing_index.render_html(packs)

        self.assertIn('<div class="status-row">', html_out)
        self.assertIn('<span class="status-cell ready">', html_out)
        self.assertIn('<span class="status-cell missing">', html_out)
        self.assertIn('<span class="badge ready">ready</span>', html_out)
        self.assertIn('<span class="badge missing">missing</span>', html_out)

    def test_24_thumbnails_have_relative_links_and_img_tags(self):
        """Thumbnail cards contain relative links and img src tags matching pack assets."""
        self._create_full_pack("thumb-pack")
        packs = marketing_index.scan_marketing(self.marketing_dir)
        html_out = marketing_index.render_html(packs)

        self.assertIn('<a href="thumb-pack/covers/cover-a.png"', html_out)
        self.assertIn('<img src="thumb-pack/covers/cover-a.png"', html_out)
        self.assertIn('<a href="thumb-pack/pins/pin-1.png"', html_out)
        self.assertIn('<a href="thumb-pack/carousel/slide-1.png"', html_out)
        self.assertIn('<a href="thumb-pack/reel/frame-1.png"', html_out)

    def test_25_covers_in_root_folder(self):
        """Detects cover.png directly in pack folder when covers/ subfolder is absent."""
        pdir = self.marketing_dir / "root-cover-pack"
        pdir.mkdir()
        (pdir / "cover.png").write_bytes(b"coverbytes")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(pack["status"]["covers"], "ready")
        self.assertIn("root-cover-pack/cover.png", pack["covers"])

    def test_26_image_formats_supported(self):
        """Supports png, jpg, jpeg, and webp images in asset folders."""
        pdir = self.marketing_dir / "format-pack"
        cov = pdir / "covers"
        cov.mkdir(parents=True)
        (cov / "cover1.png").write_bytes(b"png")
        (cov / "cover2.jpg").write_bytes(b"jpg")
        (cov / "cover3.jpeg").write_bytes(b"jpeg")
        (cov / "cover4.webp").write_bytes(b"webp")
        (cov / "cover5.gif").write_bytes(b"gif")  # Not in allowed image set

        pack = marketing_index.scan_pack(pdir, self.marketing_dir)
        self.assertEqual(len(pack["covers"]), 4)
        exts = [Path(p).suffix.lower() for p in pack["covers"]]
        self.assertIn(".png", exts)
        self.assertIn(".jpg", exts)
        self.assertIn(".jpeg", exts)
        self.assertIn(".webp", exts)
        self.assertNotIn(".gif", exts)

    def test_27_html_escaping_pack_names(self):
        """Pack names containing special characters are safely escaped in HTML."""
        evil_name = 'pack_<script>&_safe'
        pdir = self.marketing_dir / evil_name
        pdir.mkdir(parents=True)
        (pdir / "blog.md").write_text("# Test", encoding="utf-8")
        packs = marketing_index.scan_marketing(self.marketing_dir)
        html_out = marketing_index.render_html(packs)

        self.assertNotIn('<script>', html_out)
        self.assertIn(html.escape(evil_name), html_out)

    def test_28_html_escaping_file_names(self):
        """Filenames containing special characters are escaped in HTML."""
        pdir = self.marketing_dir / "special-files-pack"
        pins = pdir / "pins"
        pins.mkdir(parents=True)
        evil_file = pins / "pin_<1>&2.png"
        evil_file.write_bytes(b"fake")

        packs = marketing_index.scan_marketing(self.marketing_dir)
        html_out = marketing_index.render_html(packs)

        self.assertNotIn("<1>&2", html_out)
        self.assertIn(html.escape("pin_<1>&2.png"), html_out)

    def test_29_self_contained_html(self):
        """Generated HTML has no external assets, stylesheets, or remote scripts."""
        self._create_full_pack("self-pack")
        packs = marketing_index.scan_marketing(self.marketing_dir)
        html_out = marketing_index.render_html(packs)

        # No external URLs
        self.assertNotIn("http://", html_out)
        self.assertNotIn("https://", html_out)
        self.assertNotIn("googleapis.com", html_out)
        self.assertNotIn("<script", html_out)
        self.assertIn("<style>", html_out)

    def test_30_deterministic_html_output(self):
        """Generating HTML multiple times for identical input produces exact identical string."""
        self._create_full_pack("pack-alpha")
        self._create_full_pack("pack-beta")
        packs1 = marketing_index.scan_marketing(self.marketing_dir)
        html1 = marketing_index.render_html(packs1)

        packs2 = marketing_index.scan_marketing(self.marketing_dir)
        html2 = marketing_index.render_html(packs2)

        self.assertEqual(html1, html2)

    def test_31_packs_sorted_alphabetically(self):
        """Packs appear in deterministic alphabetical order."""
        for name in ("zebra-pack", "apple-pack", "mango-pack"):
            (self.marketing_dir / name).mkdir()

        packs = marketing_index.scan_marketing(self.marketing_dir)
        names = [p["id"] for p in packs]
        self.assertEqual(names, ["apple-pack", "mango-pack", "zebra-pack"])

    def test_32_atomic_write_creates_file(self):
        """atomic_write writes file properly and leaves no temporary files behind."""
        target = self.marketing_dir / "index.html"
        written = marketing_index.atomic_write(target, "<h1>Hello</h1>")
        self.assertTrue(written.is_file())
        self.assertEqual(written.read_text(encoding="utf-8"), "<h1>Hello</h1>")

        # Confirm no temporary files left behind
        temp_files = list(self.marketing_dir.glob(".marketing_idx_*.tmp"))
        self.assertEqual(temp_files, [])

    def test_33_build_index_default_output(self):
        """build_index writes to <marketing_dir>/index.html by default."""
        self._create_full_pack("default-pack")
        out_path = marketing_index.build_index(self.marketing_dir)
        self.assertEqual(out_path, self.marketing_dir / "index.html")
        self.assertTrue(out_path.is_file())
        content = out_path.read_text(encoding="utf-8")
        self.assertIn("default-pack", content)

    def test_34_build_index_custom_output(self):
        """build_index writes to custom output path when specified."""
        self._create_full_pack("custom-pack")
        custom_out = Path(self.test_dir) / "custom" / "custom_index.html"
        out_path = marketing_index.build_index(self.marketing_dir, output_path=custom_out)
        self.assertEqual(out_path, custom_out.resolve())
        self.assertTrue(custom_out.is_file())
        self.assertIn("custom-pack", custom_out.read_text(encoding="utf-8"))

    def test_35_cli_main_success(self):
        """main CLI runner succeeds with arguments and returns 0."""
        self._create_full_pack("cli-pack")
        code = marketing_index.main([
            "--marketing-dir", str(self.marketing_dir),
            "--title", "Custom CLI Hub",
        ])
        self.assertEqual(code, 0)
        idx = self.marketing_dir / "index.html"
        self.assertTrue(idx.is_file())
        content = idx.read_text(encoding="utf-8")
        self.assertIn("Custom CLI Hub", content)
        self.assertIn("cli-pack", content)

    def test_36_tenglish_phrases_present(self):
        """HTML output contains owner guidance in Tenglish house style."""
        self._create_full_pack("tenglish-pack")
        packs = marketing_index.scan_marketing(self.marketing_dir)
        html_out = marketing_index.render_html(packs)

        self.assertIn("Mee marketing assets anni okka page lo chusukondi", html_out)
        self.assertIn("review cheyandi", html_out)
        self.assertIn("auto-post ledhu", html_out)
        self.assertIn("Mee packs marketing overview", html_out)

    def test_37_no_currency_amounts(self):
        """HTML output strictly contains no currency amounts or symbols."""
        self._create_full_pack("clean-pack")
        packs = marketing_index.scan_marketing(self.marketing_dir)
        html_out = marketing_index.render_html(packs)

        match = CURRENCY_AMOUNT.search(html_out)
        self.assertIsNone(match, f"Found currency pattern in HTML output: {match.group(0) if match else ''}")

    def test_38_other_files_collected(self):
        """Non-image files like seo_meta.json or ab_titles.txt appear in other files section."""
        pdir = self.marketing_dir / "other-files-pack"
        pdir.mkdir()
        (pdir / "seo_meta.json").write_text('{"key": "val"}', encoding="utf-8")
        (pdir / "caption.txt").write_text("Daily plan", encoding="utf-8")
        pack = marketing_index.scan_pack(pdir, self.marketing_dir)

        self.assertIn("other-files-pack/seo_meta.json", pack["other_files"])
        self.assertIn("other-files-pack/caption.txt", pack["other_files"])

        html_out = marketing_index.render_html([pack])
        self.assertIn("Inka unna files:", html_out)
        self.assertIn("seo_meta.json", html_out)
        self.assertIn("caption.txt", html_out)

    def test_39_multiple_packs_overview_table(self):
        """Overview table contains row for each pack with readiness marks and totals."""
        self._create_full_pack("pack-one")
        (self.marketing_dir / "pack-two").mkdir()
        packs = marketing_index.scan_marketing(self.marketing_dir)
        html_out = marketing_index.render_html(packs)

        self.assertIn('<table class="overview-table">', html_out)
        self.assertIn('href="#pack-pack-one"', html_out)
        self.assertIn('href="#pack-pack-two"', html_out)
        self.assertIn("9/9", html_out)
        self.assertIn("0/9", html_out)


if __name__ == "__main__":
    unittest.main()
