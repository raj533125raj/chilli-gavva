#!/usr/bin/env python3
"""tests/test_t2c_sitemap.py — Comprehensive unit tests for sitemap_builder.py.

Tests cover:
  - MetaTagParser parsing title, meta description, og:title, og:description, canonical
  - HTML malformed handling and fallback titles
  - Scanning marketing/*/landing/index.html deterministically
  - Ignoring site/ directory itself or non-landing directories
  - Handling missing landing directories, missing index.html, empty files
  - Sitemap XML generation, XML validity (via xml.etree.ElementTree), namespace, tags
  - robots.txt generation, format, and sitemap directive
  - index.html generation, escaping, cards, links, and accessibility
  - Atomic writes and deterministic output
  - Tenglish summary strings
  - CLI arguments (--base-url, --marketing-dir, --site-dir, --json, --quiet)
  - No money amounts anywhere in outputs
  - At least 30 unit tests.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

import sitemap_builder


class TestSitemapBuilder(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* env in setUp with mock.patch.dict
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.test_dir = tempfile.mkdtemp(prefix="test_sitemap_")
        self.marketing_dir = Path(self.test_dir) / "marketing"
        self.site_dir = self.marketing_dir / "site"
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_landing_page(
        self,
        pack_slug: str,
        title: str = "Test Title",
        desc: str = "Test Description",
        custom_html: str | None = None,
    ) -> Path:
        pack_dir = self.marketing_dir / pack_slug / "landing"
        pack_dir.mkdir(parents=True, exist_ok=True)
        index_file = pack_dir / "index.html"
        if custom_html is not None:
            content = custom_html
        else:
            content = f"""<!doctype html>
<html>
<head>
  <title>{title}</title>
  <meta name="description" content="{desc}">
</head>
<body>
  <h1>{title}</h1>
</body>
</html>"""
        index_file.write_text(content, encoding="utf-8")
        return index_file

    # Test 1: Parser extracts basic title and description
    def test_01_parse_page_metadata_standard(self):
        html_doc = """
        <html>
        <head>
            <title>Focus Habit Tracker</title>
            <meta name="description" content="Daily routine checklist for deep work.">
        </head>
        </html>
        """
        meta = sitemap_builder.parse_page_metadata(html_doc)
        self.assertEqual(meta["title"], "Focus Habit Tracker")
        self.assertEqual(meta["description"], "Daily routine checklist for deep work.")

    # Test 2: Parser falls back to og:title and og:description if name=description missing
    def test_02_parse_page_metadata_og_fallback(self):
        html_doc = """
        <html>
        <head>
            <meta property="og:title" content="Social OG Title">
            <meta property="og:description" content="Social OG Description">
        </head>
        </html>
        """
        meta = sitemap_builder.parse_page_metadata(html_doc)
        self.assertEqual(meta["title"], "Social OG Title")
        self.assertEqual(meta["description"], "Social OG Description")

    # Test 3: Parser handles canonical link
    def test_03_parse_page_metadata_canonical(self):
        html_doc = """
        <html>
        <head>
            <link rel="canonical" href="https://example.com/canonical-page">
        </head>
        </html>
        """
        meta = sitemap_builder.parse_page_metadata(html_doc)
        self.assertEqual(meta["canonical"], "https://example.com/canonical-page")

    # Test 4: Parser fallback title when title tag is completely missing
    def test_04_parse_page_metadata_fallback_title(self):
        html_doc = "<html><head></head><body>No title here</body></html>"
        meta = sitemap_builder.parse_page_metadata(html_doc, fallback_title="Fallback Habit Plan")
        self.assertEqual(meta["title"], "Fallback Habit Plan")
        self.assertEqual(meta["description"], "")

    # Test 5: Parser handles malformed or fragmented HTML safely
    def test_05_parse_page_metadata_malformed(self):
        html_doc = "<title>Broken Title without closing tags <meta name=description content='broken quote"
        meta = sitemap_builder.parse_page_metadata(html_doc, fallback_title="Default")
        self.assertTrue(isinstance(meta["title"], str))
        self.assertTrue(isinstance(meta["description"], str))

    # Test 6: Parser handles special HTML characters and entities
    def test_06_parse_page_metadata_special_characters(self):
        html_doc = """
        <html>
        <head>
            <title>Work &amp; Life Balance &lt;Planner&gt;</title>
            <meta name="description" content="Organize tasks &amp; goals &quot;peacefully&quot;.">
        </head>
        </html>
        """
        meta = sitemap_builder.parse_page_metadata(html_doc)
        self.assertIn("Work & Life Balance <Planner>", meta["title"])
        self.assertIn('Organize tasks & goals "peacefully".', meta["description"])

    # Test 7: Clean base URL handles http, https, trailing slashes, empty
    def test_07_clean_base_url(self):
        self.assertEqual(sitemap_builder.clean_base_url("https://mysite.org/"), "https://mysite.org")
        self.assertEqual(sitemap_builder.clean_base_url("http://example.com"), "http://example.com")
        self.assertEqual(sitemap_builder.clean_base_url("example.com/"), "https://example.com")
        self.assertEqual(sitemap_builder.clean_base_url(""), sitemap_builder.DEFAULT_BASE_URL)
        self.assertEqual(sitemap_builder.clean_base_url(None), sitemap_builder.DEFAULT_BASE_URL)

    # Test 8: Scan returns empty list when directory does not exist
    def test_08_scan_nonexistent_directory(self):
        entries = sitemap_builder.scan_landing_pages(self.marketing_dir / "does_not_exist")
        self.assertEqual(entries, [])

    # Test 9: Scan returns empty list when no packs exist
    def test_09_scan_empty_directory(self):
        entries = sitemap_builder.scan_landing_pages(self.marketing_dir)
        self.assertEqual(entries, [])

    # Test 10: Scan ignores the site/ directory itself
    def test_10_scan_ignores_site_dir(self):
        site_landing = self.marketing_dir / "site" / "landing"
        site_landing.mkdir(parents=True, exist_ok=True)
        (site_landing / "index.html").write_text("<title>Site Title</title>", encoding="utf-8")

        entries = sitemap_builder.scan_landing_pages(self.marketing_dir)
        self.assertEqual(len(entries), 0)

    # Test 11: Scan ignores pack folders without landing/index.html
    def test_11_scan_ignores_packs_without_landing(self):
        (self.marketing_dir / "pack-without-landing").mkdir(parents=True)
        (self.marketing_dir / "pack-with-empty-landing" / "landing").mkdir(parents=True)

        entries = sitemap_builder.scan_landing_pages(self.marketing_dir)
        self.assertEqual(len(entries), 0)

    # Test 12: Scan finds single landing page and extracts fields
    def test_12_scan_single_pack(self):
        self._create_landing_page("daily-habit-tracker", title="Daily Habit Planner", desc="Track your daily wins")
        entries = sitemap_builder.scan_landing_pages(self.marketing_dir)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["pack_slug"], "daily-habit-tracker")
        self.assertEqual(entries[0]["title"], "Daily Habit Planner")
        self.assertEqual(entries[0]["description"], "Track your daily wins")
        self.assertEqual(entries[0]["rel_url"], "daily-habit-tracker/landing/")
        self.assertTrue(re.match(r"^\d{4}-\d{2}-\d{2}$", entries[0]["mtime_iso"]))

    # Test 13: Scan is deterministic and sorts alphabetically by slug
    def test_13_scan_deterministic_alphabetical_order(self):
        self._create_landing_page("zebra-pack", title="Zebra Planner")
        self._create_landing_page("apple-pack", title="Apple Planner")
        self._create_landing_page("mango-pack", title="Mango Planner")

        entries = sitemap_builder.scan_landing_pages(self.marketing_dir)
        slugs = [e["pack_slug"] for e in entries]
        self.assertEqual(slugs, ["apple-pack", "mango-pack", "zebra-pack"])

    # Test 14: Sitemap XML is valid XML parseable by ElementTree
    def test_14_sitemap_xml_validity(self):
        self._create_landing_page("pack-alpha", title="Alpha")
        self._create_landing_page("pack-beta", title="Beta")
        entries = sitemap_builder.scan_landing_pages(self.marketing_dir)

        xml_str = sitemap_builder.generate_sitemap_xml(entries, "https://teststore.com")
        root = ET.fromstring(xml_str)
        self.assertEqual(root.tag, f"{{{sitemap_builder.XML_NAMESPACE}}}urlset")
        urls = root.findall(f"{{{sitemap_builder.XML_NAMESPACE}}}url")
        # Root index + 2 packs = 3 urls
        self.assertEqual(len(urls), 3)

    # Test 15: Sitemap XML contains loc, lastmod, changefreq, priority
    def test_15_sitemap_xml_elements(self):
        self._create_landing_page("sample-guide", title="Sample Guide")
        entries = sitemap_builder.scan_landing_pages(self.marketing_dir)

        xml_str = sitemap_builder.generate_sitemap_xml(entries, "https://teststore.com")
        root = ET.fromstring(xml_str)
        ns = {"ns": sitemap_builder.XML_NAMESPACE}
        urls = root.findall("ns:url", ns)

        first_url = urls[0]
        self.assertEqual(first_url.find("ns:loc", ns).text, "https://teststore.com/")
        self.assertEqual(first_url.find("ns:changefreq", ns).text, "weekly")

        pack_url = urls[1]
        self.assertEqual(pack_url.find("ns:loc", ns).text, "https://teststore.com/sample-guide/landing/")
        self.assertEqual(pack_url.find("ns:changefreq", ns).text, "monthly")
        self.assertEqual(pack_url.find("ns:priority", ns).text, "0.8")

    # Test 16: Sitemap XML escapes XML entities properly
    def test_16_sitemap_xml_escaping(self):
        # Pack slug with special characters (if any)
        self._create_landing_page("pack-with-special-chars", title="AT&T <Guide>")
        entries = sitemap_builder.scan_landing_pages(self.marketing_dir)

        xml_str = sitemap_builder.generate_sitemap_xml(entries, "https://teststore.com/?foo=1&bar=2")
        root = ET.fromstring(xml_str)
        self.assertIsNotNone(root)

    # Test 17: Robots.txt content and sitemap link
    def test_17_robots_txt_format(self):
        txt = sitemap_builder.generate_robots_txt("https://myplannerstore.com")
        self.assertIn("User-agent: *", txt)
        self.assertIn("Allow: /", txt)
        self.assertIn("Sitemap: https://myplannerstore.com/sitemap.xml", txt)

    # Test 18: Robots.txt with custom base URL
    def test_18_robots_txt_custom_base_url(self):
        txt = sitemap_builder.generate_robots_txt("http://localhost:8080/")
        self.assertIn("Sitemap: http://localhost:8080/sitemap.xml", txt)

    # Test 19: Index HTML contains header and footer
    def test_19_index_html_structure(self):
        html_out = sitemap_builder.generate_index_html([], "https://example.com")
        self.assertIn("<!doctype html>", html_out)
        self.assertIn("<title>Products Directory", html_out)
        self.assertIn("<h1>Products Directory</h1>", html_out)
        self.assertIn("<footer>", html_out)

    # Test 20: Index HTML displays empty message when no entries
    def test_20_index_html_empty_message(self):
        html_out = sitemap_builder.generate_index_html([], "https://example.com")
        self.assertIn("Landing pages emi levu", html_out)

    # Test 21: Index HTML cards contain pack title, slug badge, link
    def test_21_index_html_with_entries(self):
        self._create_landing_page("study-tracker", title="Study Schedule Template", desc="Plan exams easily.")
        entries = sitemap_builder.scan_landing_pages(self.marketing_dir)

        html_out = sitemap_builder.generate_index_html(entries, "https://example.com")
        self.assertIn("Study Schedule Template", html_out)
        self.assertIn("Plan exams easily.", html_out)
        self.assertIn("study-tracker", html_out)
        self.assertIn('href="../study-tracker/landing/"', html_out)

    # Test 22: Index HTML escapes special characters in titles and descriptions
    def test_22_index_html_escaping(self):
        self._create_landing_page(
            "safety-pack",
            custom_html="""<!doctype html><html><head>
            <title>Safety &lt;Rules&gt; &amp; Tips</title>
            <meta name="description" content="Always &quot;check&quot; &amp; verify.">
            </head></html>"""
        )
        entries = sitemap_builder.scan_landing_pages(self.marketing_dir)

        html_out = sitemap_builder.generate_index_html(entries, "https://example.com")
        self.assertIn("Safety &lt;Rules&gt; &amp; Tips", html_out)
        self.assertIn("Always &quot;check&quot; &amp; verify.", html_out)
        self.assertNotIn("<Rules>", html_out)

    # Test 23: Atomic write writes file and overwrites cleanly
    def test_23_atomic_write(self):
        target = self.site_dir / "test_file.txt"
        sitemap_builder.atomic_write(target, "First Version")
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(encoding="utf-8"), "First Version")

        sitemap_builder.atomic_write(target, "Second Version")
        self.assertEqual(target.read_text(encoding="utf-8"), "Second Version")

    # Test 24: Format Tenglish summary for 0, 1, and many packs
    def test_24_format_tenglish_summary(self):
        p = Path("exported_content/marketing/site")
        s0 = sitemap_builder.format_tenglish_summary(0, p)
        self.assertIn("Landing pages emi levu", s0)

        s1 = sitemap_builder.format_tenglish_summary(1, p)
        self.assertIn("1 landing page sitemap, robots.txt mariyu index ready ayyindi", s1)
        self.assertIn("✓", s1)

        s3 = sitemap_builder.format_tenglish_summary(3, p)
        self.assertIn("3 landing pages sitemap, robots.txt mariyu index ready ayyindi", s3)
        self.assertIn("✓", s3)

    # Test 25: Build site index end-to-end writes sitemap.xml, robots.txt, index.html
    def test_25_build_site_index_full(self):
        self._create_landing_page("budget-simple", title="Monthly Budget Template", desc="Track finances.")
        self._create_landing_page("fitness-core", title="Workout Log", desc="Fitness sheets.")

        res = sitemap_builder.build_site_index(
            marketing_dir=self.marketing_dir,
            site_dir=self.site_dir,
            base_url="https://plannercentral.com",
        )

        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["count"], 2)
        self.assertEqual(res["base_url"], "https://plannercentral.com")

        # Check files exist
        self.assertTrue((self.site_dir / "sitemap.xml").is_file())
        self.assertTrue((self.site_dir / "robots.txt").is_file())
        self.assertTrue((self.site_dir / "index.html").is_file())

        # Validate sitemap XML structure
        xml_content = (self.site_dir / "sitemap.xml").read_text(encoding="utf-8")
        root = ET.fromstring(xml_content)
        self.assertEqual(len(root), 3)  # root + 2 pages

        # Validate robots.txt
        robots_content = (self.site_dir / "robots.txt").read_text(encoding="utf-8")
        self.assertIn("Sitemap: https://plannercentral.com/sitemap.xml", robots_content)

        # Validate index.html
        index_content = (self.site_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn("Monthly Budget Template", index_content)
        self.assertIn("Workout Log", index_content)

    # Test 26: CLI execution with --json flag
    def test_26_cli_json_flag(self):
        self._create_landing_page("quick-notes", title="Quick Notes")
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            exit_code = sitemap_builder.main([
                "--marketing-dir", str(self.marketing_dir),
                "--site-dir", str(self.site_dir),
                "--base-url", "https://mystore.test",
                "--json",
            ])
        self.assertEqual(exit_code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["count"], 1)
        self.assertEqual(output["base_url"], "https://mystore.test")
        self.assertEqual(output["pages"][0]["pack_slug"], "quick-notes")

    # Test 27: CLI execution with --quiet flag
    def test_27_cli_quiet_flag(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            exit_code = sitemap_builder.main([
                "--marketing-dir", str(self.marketing_dir),
                "--site-dir", str(self.site_dir),
                "--quiet",
            ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue().strip(), "")

    # Test 28: CLI default output displays Tenglish summary
    def test_28_cli_default_output(self):
        self._create_landing_page("pack-one", title="Pack One")
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            exit_code = sitemap_builder.main([
                "--marketing-dir", str(self.marketing_dir),
                "--site-dir", str(self.site_dir),
            ])
        self.assertEqual(exit_code, 0)
        self.assertIn("1 landing page sitemap", stdout.getvalue())

    # Test 29: No currency or money symbols in generated outputs
    def test_29_no_money_amounts_in_outputs(self):
        self._create_landing_page("money-test", title="Personal Cash Tracker", desc="Track savings.")
        sitemap_builder.build_site_index(
            marketing_dir=self.marketing_dir,
            site_dir=self.site_dir,
            base_url="https://example.com",
        )
        currency_pattern = re.compile(
            r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
            re.IGNORECASE,
        )
        for fname in ["sitemap.xml", "robots.txt", "index.html"]:
            text = (self.site_dir / fname).read_text(encoding="utf-8")
            match = currency_pattern.search(text)
            self.assertIsNone(match, f"Found currency amount in {fname}: {match}")

    # Test 30: Handles empty index.html gracefully
    def test_30_empty_index_html_handling(self):
        self._create_landing_page("empty-pack", custom_html="")
        entries = sitemap_builder.scan_landing_pages(self.marketing_dir)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["pack_slug"], "empty-pack")
        self.assertEqual(entries[0]["title"], "Empty Pack")  # fallback title from slug
        self.assertEqual(entries[0]["description"], "")

    # Test 31: Handles unreadable or directory error during scan safely
    def test_31_scan_error_resilience(self):
        non_dir_file = self.marketing_dir / "not_a_dir.txt"
        non_dir_file.write_text("hello", encoding="utf-8")
        entries = sitemap_builder.scan_landing_pages(self.marketing_dir)
        self.assertEqual(entries, [])

    # Test 32: Deterministic multiple runs produce exact identical byte output
    def test_32_deterministic_identical_output(self):
        self._create_landing_page("beta-tool", title="Beta Tool", desc="Description B")
        self._create_landing_page("alpha-tool", title="Alpha Tool", desc="Description A")

        sitemap_builder.build_site_index(self.marketing_dir, self.site_dir, "https://example.com")
        xml_run1 = (self.site_dir / "sitemap.xml").read_bytes()
        txt_run1 = (self.site_dir / "robots.txt").read_bytes()
        html_run1 = (self.site_dir / "index.html").read_bytes()

        # Run 2
        sitemap_builder.build_site_index(self.marketing_dir, self.site_dir, "https://example.com")
        xml_run2 = (self.site_dir / "sitemap.xml").read_bytes()
        txt_run2 = (self.site_dir / "robots.txt").read_bytes()
        html_run2 = (self.site_dir / "index.html").read_bytes()

        self.assertEqual(xml_run1, xml_run2)
        self.assertEqual(txt_run1, txt_run2)
        self.assertEqual(html_run1, html_run2)


if __name__ == "__main__":
    unittest.main()
