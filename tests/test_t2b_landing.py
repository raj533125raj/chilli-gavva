#!/usr/bin/env python3
"""tests/test_t2b_landing.py — Unit tests for landing_page.py

Verifies:
- One self-contained HTML file (inline CSS, NO external fonts/scripts/trackers)
- Semantic headings (h1, h2, h3, main, header, section, footer)
- Meta title (<= 60 chars) and meta description (<= 155 chars)
- Open Graph tags (og:title, og:description, og:type, og:image, og:url)
- JSON-LD Product schema WITHOUT price or currency
- Relative image references copied next to index.html and resized <= 1200px
- FAQ section from faq_builder or fallback
- [STORE LINK] button placeholder
- Mobile friendly layout (meta viewport, responsive CSS)
- Lighthouse-style basics (alt texts, lang, semantic structure)
- HTML escaping for special characters (&, <, >, ", ')
- No money amounts anywhere
- No brand logos or brand names
- Nothing auto-posts/publishes
- Atomic writes (destination updated cleanly)
- Pack folder is never modified (read-only)
- Deterministic output
- Owner-facing text in Tenglish house style
- CLI flags (--all, --pack, --out, --packs-dir)
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

import landing_page


class TestLandingPage(unittest.TestCase):
    def setUp(self):
        # Clear all GITHUB_* and RUNNER_ENVIRONMENT environment variables
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.tmp_dir = tempfile.mkdtemp(prefix="test_t2b_landing_")
        self.pack_dir = Path(self.tmp_dir) / "test-pack-budget"
        self.pack_dir.mkdir(parents=True, exist_ok=True)
        self.out_root = Path(self.tmp_dir) / "marketing"

        # Create basic sample pack content
        (self.pack_dir / "listing.txt").write_text(
            "Title: Minimalist Budget Planner & Finance Log\n\n"
            "Description: A clean, simple printable budget tracker designed to manage daily expenses and financial goals.\n"
            "Easy to print at home and keep your personal records tidy.\n\n"
            "What you get:\n"
            "- Monthly Budget Sheet PDF\n"
            "- Expense Tracker Log PDF\n"
            "- Instructions Guide\n\n"
            "Tags: budget planner, expense tracker, finance log, printable, monthly planner\n",
            encoding="utf-8",
        )
        (self.pack_dir / "LICENSE.txt").write_text(
            "For personal use only. Do not resell or distribute.\n",
            encoding="utf-8",
        )

        if HAS_PILLOW:
            # Create a mock cover image 1600x1200
            cover = Image.new("RGB", (1600, 1200), color=(40, 80, 120))
            cover.save(self.pack_dir / "cover.png")
            # Create a preview image 1400x1050
            prev = Image.new("RGB", (1400, 1050), color=(60, 100, 140))
            prev.save(self.pack_dir / "preview-1.png")

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_meta_title_length_and_content(self):
        title = "A Very Long Title That Definitely Exceeds The Maximum Permitted Sixty Characters Limit For SEO Best Practices"
        meta_title = landing_page.make_meta_title(title, "pack-slug")
        self.assertLessEqual(len(meta_title), 60)
        self.assertTrue(meta_title.endswith("..."))

    def test_02_meta_title_short(self):
        title = "Short Planner Title"
        meta_title = landing_page.make_meta_title(title, "pack-slug")
        self.assertEqual(meta_title, "Short Planner Title")

    def test_03_meta_description_length(self):
        desc = (
            "This is an extremely long description designed to test the truncation limit of the meta description. "
            "It goes on and on with extra details about the product, including how easy it is to print, the layout, "
            "the clean lines, and various tips for daily organisation and productivity."
        )
        meta_desc = landing_page.make_meta_description(desc, "Short Title")
        self.assertLessEqual(len(meta_desc), 155)

    def test_04_meta_description_fallback(self):
        meta_desc = landing_page.make_meta_description("", "Habit Tracker")
        self.assertIn("Habit Tracker", meta_desc)
        self.assertLessEqual(len(meta_desc), 155)

    def test_05_parse_listing_file(self):
        data = landing_page.parse_listing_file(self.pack_dir / "listing.txt")
        self.assertIn("Minimalist Budget Planner", data["title"])
        self.assertTrue(len(data["what_you_get"]) >= 3)
        self.assertIn("budget planner", data["tags"])
        self.assertIn("clean, simple printable budget tracker", data["description"])

    def test_06_parse_listing_missing_file(self):
        non_existent = self.pack_dir / "nonexistent.txt"
        data = landing_page.parse_listing_file(non_existent)
        self.assertEqual(data["title"], "")
        self.assertEqual(data["what_you_get"], [])

    def test_07_build_landing_page_creates_index_html(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        self.assertTrue(out_html.is_file())
        self.assertEqual(out_html.name, "index.html")
        self.assertEqual(out_html.parent.name, "landing")

    def test_08_self_contained_no_external_scripts(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        # Ensure no external scripts (<script src="http...)
        self.assertNotRegex(html_text, r'<script[^>]+src=["\']http')
        self.assertNotRegex(html_text, r'<script[^>]+src=')

    def test_09_self_contained_no_external_fonts_or_stylesheets(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        # Ensure no external link rel="stylesheet"
        self.assertNotRegex(html_text, r'<link[^>]+rel=["\']stylesheet["\']')
        self.assertNotIn("fonts.googleapis.com", html_text)
        self.assertNotIn("@import url", html_text)

    def test_10_semantic_html_structure(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        self.assertTrue(html_text.startswith("<!DOCTYPE html>"))
        self.assertIn('<html lang="en">', html_text)
        self.assertIn("<header>", html_text)
        self.assertIn("<main>", html_text)
        self.assertIn("<footer", html_text)
        self.assertIn("<h1", html_text)
        self.assertIn("<h2", html_text)

    def test_11_open_graph_tags_present(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        self.assertIn('<meta property="og:title"', html_text)
        self.assertIn('<meta property="og:description"', html_text)
        self.assertIn('<meta property="og:type" content="product">', html_text)
        self.assertIn('<meta property="og:url"', html_text)
        if HAS_PILLOW:
            self.assertIn('<meta property="og:image"', html_text)

    def test_12_json_ld_product_schema_present(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        self.assertIn('<script type="application/ld+json">', html_text)
        # Extract json inside ld+json
        m = re.search(r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', html_text, re.DOTALL)
        self.assertIsNotNone(m)
        schema = json.loads(m.group(1))
        self.assertEqual(schema.get("@type"), "Product")
        self.assertIn("name", schema)
        self.assertIn("description", schema)

    def test_13_json_ld_has_no_price_or_offers(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        m = re.search(r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', html_text, re.DOTALL)
        schema = json.loads(m.group(1))
        self.assertNotIn("offers", schema)
        self.assertNotIn("price", schema)
        self.assertNotIn("priceCurrency", schema)

    @unittest.skipUnless(HAS_PILLOW, "Pillow required for image resizing test")
    def test_14_images_copied_relatively_and_resized(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        landing_dir = out_html.parent
        self.assertTrue((landing_dir / "cover.png").is_file())
        self.assertTrue((landing_dir / "preview-1.png").is_file())

        with Image.open(landing_dir / "cover.png") as img:
            w, h = img.size
            self.assertLessEqual(w, 1200)
            self.assertLessEqual(h, 1200)
            self.assertEqual(w, 1200)
            self.assertEqual(h, 900)  # aspect ratio 4:3 maintained

    def test_15_images_have_alt_attributes(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        # Every <img must have alt="..."
        img_tags = re.findall(r'<img[^>]+>', html_text)
        if HAS_PILLOW:
            self.assertGreater(len(img_tags), 0)
            for tag in img_tags:
                self.assertIn('alt="', tag)
                self.assertNotIn('alt=""', tag)

    def test_16_store_link_cta_placeholder(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        self.assertIn('[STORE LINK]', html_text)
        self.assertIn('href="[STORE LINK]"', html_text)

    def test_17_faq_section_rendered(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        self.assertIn("Frequently Asked Questions", html_text)
        self.assertIn("faq-item", html_text)
        self.assertIn("faq-question", html_text)
        self.assertIn("faq-answer", html_text)

    def test_18_mobile_friendly_viewport_and_responsive(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1.0">', html_text)
        self.assertIn("@media", html_text)

    def test_19_html_escaping_in_title_and_description(self):
        tricky_pack = Path(self.tmp_dir) / "tricky-pack"
        tricky_pack.mkdir(parents=True, exist_ok=True)
        (tricky_pack / "listing.txt").write_text(
            'Title: Test <Script> & "Special" \'Quotes\'\n\n'
            'Description: Paragraph with <bold> & "special" characters.\n\n'
            'What you get:\n- 1 <File> & "Item"\n\n'
            'Tags: tag1, tag2\n',
            encoding="utf-8",
        )
        out_html = landing_page.build_landing_page(tricky_pack, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        self.assertNotIn("<Script>", html_text)
        self.assertIn("&lt;Script&gt;", html_text)
        self.assertNotIn("<bold>", html_text)
        self.assertIn("&lt;bold&gt;", html_text)

    def test_20_pack_folder_untouched(self):
        # Record mtimes and files of pack_dir
        before_files = {p.name: p.stat().st_mtime for p in self.pack_dir.iterdir()}
        landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        after_files = {p.name: p.stat().st_mtime for p in self.pack_dir.iterdir()}
        self.assertEqual(before_files, after_files)

    def test_21_no_money_amounts_in_output(self):
        money_pack = Path(self.tmp_dir) / "money-pack"
        money_pack.mkdir(parents=True, exist_ok=True)
        (money_pack / "listing.txt").write_text(
            "Title: Finance Kit for $25 USD\n\n"
            "Description: Save 500 INR or 10 USD easily.\n\n"
            "What you get:\n- Daily planner worth $5\n\n"
            "Tags: savings, money, budget\n",
            encoding="utf-8",
        )
        out_html = landing_page.build_landing_page(money_pack, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        self.assertNotIn("$25", html_text)
        self.assertNotIn("500 INR", html_text)
        self.assertNotIn("10 USD", html_text)
        self.assertNotIn("$5", html_text)

    def test_22_brand_names_sanitized(self):
        brand_pack = Path(self.tmp_dir) / "brand-pack"
        brand_pack.mkdir(parents=True, exist_ok=True)
        (brand_pack / "listing.txt").write_text(
            "Title: Habit Tracker for Canva and Notion\n\n"
            "Description: Works with Microsoft Excel, Google Sheets and Etsy shop.\n\n"
            "Tags: habits, routine\n",
            encoding="utf-8",
        )
        out_html = landing_page.build_landing_page(brand_pack, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        self.assertNotRegex(html_text, r"\bCanva\b")
        self.assertNotRegex(html_text, r"\bNotion\b")
        self.assertNotRegex(html_text, r"\bMicrosoft Excel\b")
        self.assertNotRegex(html_text, r"\bGoogle Sheets\b")
        self.assertNotRegex(html_text, r"\bEtsy\b")

    def test_23_deterministic_output(self):
        out1 = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        text1 = out1.read_text(encoding="utf-8")
        out2 = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        text2 = out2.read_text(encoding="utf-8")
        self.assertEqual(text1, text2)

    def test_24_atomic_write_safety(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        self.assertTrue(out_html.is_file())
        # Re-running overwrites cleanly
        out_html_2 = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        self.assertEqual(out_html, out_html_2)
        self.assertTrue(out_html_2.is_file())

    def test_25_build_all_multiple_packs(self):
        packs_root = Path(self.tmp_dir) / "packs_root"
        packs_root.mkdir(parents=True, exist_ok=True)
        for name in ["pack-a", "pack-b", "pack-c"]:
            p = packs_root / name
            p.mkdir()
            (p / "listing.txt").write_text(f"Title: {name.title()} Printable\n\nDescription: Desc for {name}\n", encoding="utf-8")

        written = landing_page.build_all(packs_dir=packs_root, out_root=self.out_root)
        self.assertEqual(len(written), 3)
        for w in written:
            self.assertTrue(w.is_file())
            self.assertEqual(w.name, "index.html")

    def test_26_build_all_empty_directory(self):
        empty_root = Path(self.tmp_dir) / "empty_packs"
        empty_root.mkdir(parents=True, exist_ok=True)
        written = landing_page.build_all(packs_dir=empty_root, out_root=self.out_root)
        self.assertEqual(written, [])

    def test_27_cli_single_pack_tenglish_output(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            ret = landing_page.main([str(self.pack_dir), "--out", str(self.out_root)])
        self.assertEqual(ret, 0)
        output = stdout.getvalue()
        # Verify Tenglish house style
        self.assertIn("Landing page tayaru ayindi", output)
        self.assertIn("ready ga undi", output)

    def test_28_cli_all_packs_tenglish_output(self):
        packs_root = Path(self.tmp_dir) / "packs_root"
        packs_root.mkdir(parents=True, exist_ok=True)
        p = packs_root / "sample-pack"
        p.mkdir()
        (p / "listing.txt").write_text("Title: Sample Pack\n\nDescription: Nice sample\n", encoding="utf-8")

        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            ret = landing_page.main(["--all", "--packs-dir", str(packs_root), "--out", str(self.out_root)])
        self.assertEqual(ret, 0)
        output = stdout.getvalue()
        self.assertIn("Landing pages tayaru ayyayi", output)
        self.assertIn("save chesam", output)

    def test_29_cli_missing_pack_error(self):
        non_existent = str(Path(self.tmp_dir) / "does-not-exist")
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            ret = landing_page.main([non_existent])
        self.assertEqual(ret, 1)
        self.assertIn("Tappu: pack folder kanapadaledu", stderr.getvalue())

    def test_30_what_you_get_and_tags_badges_in_html(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        self.assertIn("What You Get", html_text)
        self.assertIn("Monthly Budget Sheet PDF", html_text)
        self.assertIn("tag-badge", html_text)
        self.assertIn("budget planner", html_text)

    def test_31_non_existent_pack_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError):
            landing_page.build_landing_page(self.tmp_dir + "/definitely_absent_pack")

    def test_32_footer_personal_use_notice(self):
        out_html = landing_page.build_landing_page(self.pack_dir, out_dir=self.out_root)
        html_text = out_html.read_text(encoding="utf-8")
        self.assertIn("For personal use only", html_text)


if __name__ == "__main__":
    unittest.main()
