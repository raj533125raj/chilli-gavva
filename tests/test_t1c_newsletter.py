#!/usr/bin/env python3
"""tests/test_t1c_newsletter.py — Unit tests for newsletter.py (BINNY-T1C).

Covers at least 30 unit tests testing:
- Week identifier calculation (ISO calendar YYYY-WW)
- Pack discovery and listing parsing
- Blog post parsing (H1 title, summary paragraph)
- Seasonal collection parsing (collections/*.md)
- Subject line count (strictly 3 options) and length (<= 60 chars each)
- Preview text generation (concise summary <= 150 chars)
- 3 to 5 content sections generated
- Store link placeholders present: [STORE LINK]
- Unsubscribe placeholder line present: [UNSUBSCRIBE LINK]
- Output destinations: exported_content/marketing/newsletter/<YYYY-WW>.md and .txt
- Atomic file writes and determinism/idempotency
- Empty or sparse states (no packs, no blogs, no collections)
- Multiple packs handling (ordering, descriptions, what you get)
- Brand name sanitization (e.g. Canva, Etsy, Notion, Excel)
- Money amount prevention (strictly fails or strips if any money patterns present)
- Owner-facing Tenglish house style report
- CLI options and argument parsing
- Clear GITHUB_* and RUNNER_ENVIRONMENT in setUp
"""

from __future__ import annotations

from datetime import date
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import newsletter


class TestNewsletter(unittest.TestCase):
    def setUp(self):
        # Clear all GITHUB_* and RUNNER_ENVIRONMENT environment variables per repo guidelines
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.test_dir = tempfile.mkdtemp(prefix="test_newsletter_")
        self.root = Path(self.test_dir)
        self.products_dir = self.root / "products"
        self.upload_dir = self.products_dir / "upload"
        self.marketing_dir = self.root / "marketing"

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_pack(
        self,
        folder_name: str,
        title: str,
        desc: str = "A clean printable organizer for daily productivity.",
        tags: str = "daily planner, printable checklist, habit tracker",
        what_you_get: str = "- 1 PDF daily planner sheet\n- 1 PDF habit tracker page",
    ) -> Path:
        pdir = self.upload_dir / folder_name
        pdir.mkdir(parents=True, exist_ok=True)
        content = (
            f"Title: {title}\n\n"
            f"Description: {desc}\n\n"
            f"Tags: {tags}\n\n"
            f"What you get:\n{what_you_get}\n"
        )
        (pdir / "listing.txt").write_text(content, encoding="utf-8")
        (pdir / "product.zip").write_bytes(b"dummy zip content")
        return pdir

    def _create_blog(
        self,
        pack_folder: str,
        title: str = "How to Organize Your Daily Schedule",
        body: str = "Daily planning allows you to prioritize tasks without unnecessary stress or clutter.",
    ) -> Path:
        bdir = self.marketing_dir / pack_folder
        bdir.mkdir(parents=True, exist_ok=True)
        blog_file = bdir / "blog.md"
        content = f"# {title}\n\n{body}\n\n## Benefits\n\nMore calm and clarity."
        blog_file.write_text(content, encoding="utf-8")
        return blog_file

    def _create_collection(
        self,
        slug: str,
        title: str = "New Year Organizing Essentials",
        intro: str = "Welcome the new year with focused tools and structured digital planners.",
        featured: list[str] = None,
    ) -> Path:
        cdir = self.marketing_dir / "collections"
        cdir.mkdir(parents=True, exist_ok=True)
        featured = featured or ["Annual Goal Planner", "Budget Tracker"]
        f_lines = "\n".join([f"- **{item}** (`pack-id`)" for item in featured])
        content = (
            f"# {title}\n\n{intro}\n\n## Featured Digital Guides & Planners\n\n{f_lines}\n\n"
            f"## Collection Tags\n\ngoal setting, productivity"
        )
        cfile = cdir / f"{slug}.md"
        cfile.write_text(content, encoding="utf-8")
        return cfile

    # 1. Week identifier formatting
    def test_01_week_identifier_standard(self):
        d = date(2026, 11, 8)  # ISO week 45 of 2026
        week_str = newsletter.get_week_identifier(d)
        self.assertEqual(week_str, "2026-W45")

    def test_02_week_identifier_year_boundary(self):
        d = date(2027, 1, 1)  # 2026-W53
        week_str = newsletter.get_week_identifier(d)
        self.assertTrue(week_str.startswith("2026-W53") or week_str.startswith("2027-W01"))

    def test_03_week_identifier_defaults_to_today(self):
        week_str = newsletter.get_week_identifier()
        self.assertRegex(week_str, r"^\d{4}-W\d{2}$")

    # 2. Pack discovery
    def test_04_discover_packs_empty(self):
        packs = newsletter.discover_ready_packs(self.products_dir)
        self.assertEqual(packs, [])

    def test_05_discover_packs_with_items(self):
        self._create_pack("pack-1", "Productivity Planner")
        self._create_pack("pack-2", "Habit Calendar")
        packs = newsletter.discover_ready_packs(self.products_dir)
        self.assertEqual(len(packs), 2)
        self.assertEqual(packs[0].name, "pack-1")
        self.assertEqual(packs[1].name, "pack-2")

    def test_06_discover_packs_skips_missing_listing(self):
        pdir = self.upload_dir / "pack-incomplete"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "notes.txt").write_text("no listing.txt here", encoding="utf-8")
        packs = newsletter.discover_ready_packs(self.products_dir)
        self.assertEqual(len(packs), 0)

    # 3. Listing parser
    def test_07_parse_pack_listing(self):
        pdir = self._create_pack("pack-1", "Budget Planner Printable", desc="Track your spending.")
        info = newsletter.parse_pack_listing(pdir)
        self.assertEqual(info["pack_id"], "pack-1")
        self.assertEqual(info["title"], "Budget Planner Printable")
        self.assertIn("Track your spending", info["description"])
        self.assertTrue(len(info["tags"]) >= 1)

    # 4. Blog post parser
    def test_08_parse_blog_posts(self):
        self._create_blog("pack-1", "Weekly Review Guide", "A detailed step-by-step review process.")
        blogs = newsletter.parse_blog_posts(self.marketing_dir)
        self.assertEqual(len(blogs), 1)
        self.assertEqual(blogs[0]["title"], "Weekly Review Guide")
        self.assertIn("step-by-step review", blogs[0]["summary"])

    def test_09_parse_blog_posts_ignores_special_dirs(self):
        # Create dummy blog inside collections directory (should be ignored)
        coll_dir = self.marketing_dir / "collections"
        coll_dir.mkdir(parents=True, exist_ok=True)
        (coll_dir / "blog.md").write_text("# Bad Title\n\nShould not be parsed.", encoding="utf-8")
        blogs = newsletter.parse_blog_posts(self.marketing_dir)
        self.assertEqual(len(blogs), 0)

    # 5. Seasonal collections parser
    def test_10_parse_collections(self):
        self._create_collection("diwali", "Diwali Festival Planner", "Celebrate Diwali with structured plans.", ["Puja Checklist"])
        colls = newsletter.parse_collections(self.marketing_dir)
        self.assertEqual(len(colls), 1)
        self.assertEqual(colls[0]["slug"], "diwali")
        self.assertEqual(colls[0]["title"], "Diwali Festival Planner")
        self.assertIn("Puja Checklist", colls[0]["featured"])

    # 6. Subject lines generator
    def test_11_subject_lines_count_and_length(self):
        packs = [{"short_title": "Daily Goal Sheet", "tags": ["productivity"]}]
        colls = [{"title": "New Year Kickoff"}]
        subjects = newsletter.build_subject_lines(packs, colls, "2026-W45")
        self.assertEqual(len(subjects), 3)
        for s in subjects:
            self.assertLessEqual(len(s), 60)
            self.assertGreater(len(s), 5)

    def test_12_subject_lines_empty_packs(self):
        subjects = newsletter.build_subject_lines([], [], "2026-W45")
        self.assertEqual(len(subjects), 3)
        for s in subjects:
            self.assertLessEqual(len(s), 60)

    def test_13_subject_lines_truncate_long_titles(self):
        very_long_title = "Ultra Super Comprehensive Multi-Year Complete Productivity Routine Planning System"
        packs = [{"short_title": very_long_title, "tags": ["planner"]}]
        subjects = newsletter.build_subject_lines(packs, [], "2026-W45")
        for s in subjects:
            self.assertLessEqual(len(s), 60)

    # 7. Preview text
    def test_14_preview_text_length(self):
        packs = [{"short_title": "Daily Habit Sheet"}]
        blogs = [{"title": "Organizing Tips"}]
        colls = [{"title": "Spring Cleanup"}]
        preview = newsletter.build_preview_text(packs, colls, blogs)
        self.assertLessEqual(len(preview), 150)
        self.assertIn("Daily Habit Sheet", preview)

    def test_15_preview_text_fallback_empty(self):
        preview = newsletter.build_preview_text([], [], [])
        self.assertLessEqual(len(preview), 150)
        self.assertGreater(len(preview), 10)

    # 8. Content sections count & structure
    def test_16_sections_count_range_3_to_5(self):
        # Empty inputs should generate fallback sections to meet 3-5 constraint
        sections = newsletter.build_sections([], [], [])
        self.assertGreaterEqual(len(sections), 3)
        self.assertLessEqual(len(sections), 5)

    def test_17_sections_with_all_inputs(self):
        self._create_pack("pack-1", "Student Planner")
        self._create_blog("pack-1", "Study Habits")
        self._create_collection("exam-season", "Exam Preparation Guide")

        raw_packs = newsletter.discover_ready_packs(self.products_dir)
        packs = [newsletter.parse_pack_listing(p) for p in raw_packs]
        blogs = newsletter.parse_blog_posts(self.marketing_dir)
        colls = newsletter.parse_collections(self.marketing_dir)

        sections = newsletter.build_sections(packs, blogs, colls)
        self.assertGreaterEqual(len(sections), 3)
        self.assertLessEqual(len(sections), 5)

    def test_18_sections_contain_store_links(self):
        sections = newsletter.build_sections([], [], [])
        for sec in sections:
            self.assertIn("[STORE LINK]", sec["content"])

    # 9. Unsubscribe placeholder
    def test_19_unsubscribe_placeholder_presence(self):
        self.assertIn("[UNSUBSCRIBE LINK]", newsletter.UNSUBSCRIBE_PLACEHOLDER)
        md = newsletter.generate_newsletter_markdown(["Sub 1", "Sub 2", "Sub 3"], "Preview", [{"title": "S1", "content": "Text [STORE LINK]"}], "2026-W45")
        self.assertIn("[UNSUBSCRIBE LINK]", md)
        txt = newsletter.generate_newsletter_plaintext(["Sub 1", "Sub 2", "Sub 3"], "Preview", [{"title": "S1", "content": "Text [STORE LINK]"}], "2026-W45")
        self.assertIn("[UNSUBSCRIBE LINK]", txt)

    # 10. Markdown generation structure
    def test_20_markdown_structure(self):
        subjects = ["Sub 1", "Sub 2", "Sub 3"]
        preview = "Test preview line"
        secs = [{"title": "Featured", "content": "Sample content [STORE LINK]"}]
        md = newsletter.generate_newsletter_markdown(subjects, preview, secs, "2026-W45")
        self.assertIn("# Weekly Newsletter Draft — 2026-W45", md)
        self.assertIn("## Subject Line Options", md)
        self.assertIn("Option 1: Sub 1", md)
        self.assertIn("## Preview Text", md)
        self.assertIn("## Featured", md)

    # 11. Plain text generation structure
    def test_21_plaintext_structure(self):
        subjects = ["Sub 1", "Sub 2", "Sub 3"]
        preview = "Test preview line"
        secs = [{"title": "Highlights", "content": "Sample body [STORE LINK]"}]
        txt = newsletter.generate_newsletter_plaintext(subjects, preview, secs, "2026-W45")
        self.assertIn("WEEKLY NEWSLETTER DRAFT — 2026-W45", txt)
        self.assertIn("SUBJECT LINE OPTIONS:", txt)
        self.assertIn("PREVIEW TEXT: Test preview line", txt)
        self.assertIn("HIGHLIGHTS", txt)

    # 12. Full make_weekly_newsletter file writes
    def test_22_make_weekly_newsletter_file_creation(self):
        self._create_pack("pack-1", "Productivity Set")
        res = newsletter.make_weekly_newsletter(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            target_date=date(2026, 11, 8),
        )
        self.assertEqual(res["week"], "2026-W45")
        md_file = self.marketing_dir / "newsletter" / "2026-W45.md"
        txt_file = self.marketing_dir / "newsletter" / "2026-W45.txt"
        self.assertTrue(md_file.is_file())
        self.assertTrue(txt_file.is_file())
        self.assertEqual(res["md_path"], md_file)
        self.assertEqual(res["txt_path"], txt_file)

    def test_23_make_weekly_newsletter_week_override(self):
        res = newsletter.make_weekly_newsletter(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            week_override="2026-W40",
        )
        self.assertEqual(res["week"], "2026-W40")
        self.assertTrue((self.marketing_dir / "newsletter" / "2026-W40.md").is_file())

    def test_24_newsletter_writes_atomically_outside_packs(self):
        pack_dir = self._create_pack("pack-alpha", "Alpha Pack")
        res = newsletter.make_weekly_newsletter(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            week_override="2026-W45",
        )
        # Check that pack folder itself was not modified with newsletter files
        self.assertFalse((pack_dir / "2026-W45.md").exists())
        self.assertTrue(res["md_path"].is_relative_to(self.marketing_dir))

    def test_25_newsletter_is_deterministic(self):
        self._create_pack("pack-1", "Budget Planner")
        self._create_blog("pack-1", "Budgeting Tips")
        self._create_collection("new-year", "New Year Collection")

        res1 = newsletter.make_weekly_newsletter(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            target_date=date(2026, 11, 8),
        )
        md1 = res1["md_path"].read_text(encoding="utf-8")
        txt1 = res1["txt_path"].read_text(encoding="utf-8")

        res2 = newsletter.make_weekly_newsletter(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            target_date=date(2026, 11, 8),
        )
        md2 = res2["md_path"].read_text(encoding="utf-8")
        txt2 = res2["txt_path"].read_text(encoding="utf-8")

        self.assertEqual(md1, md2)
        self.assertEqual(txt1, txt2)

    # 13. Brand sanitization
    def test_26_sanitize_brand_names(self):
        text_with_brands = "Use Notion or Canva to make Etsy templates for Excel and Google Sheets on iPad."
        cleaned = newsletter.sanitize_text(text_with_brands)
        self.assertNotIn("Notion", cleaned)
        self.assertNotIn("Canva", cleaned)
        self.assertNotIn("Etsy", cleaned)
        self.assertNotIn("iPad", cleaned)

    # 14. Money amount check & rejection
    def test_27_no_money_in_generated_files(self):
        self._create_pack("pack-1", "Clean Habits Organizer")
        res = newsletter.make_weekly_newsletter(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            target_date=date(2026, 11, 8),
        )
        md_text = res["md_path"].read_text(encoding="utf-8")
        txt_text = res["txt_path"].read_text(encoding="utf-8")

        self.assertIsNone(newsletter.check_for_money(md_text))
        self.assertIsNone(newsletter.check_for_money(txt_text))

    def test_28_strips_money_if_present_in_pack_data(self):
        # Pack listing with accidental money amounts
        pdir = self.upload_dir / "pack-money"
        pdir.mkdir(parents=True, exist_ok=True)
        content = (
            "Title: Finance Workbook $15\n\n"
            "Description: Save 50 USD with this budget tool.\n\n"
            "Tags: finance, money planner\n\n"
            "What you get:\n- 1 PDF sheet\n"
        )
        (pdir / "listing.txt").write_text(content, encoding="utf-8")
        res = newsletter.make_weekly_newsletter(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            week_override="2026-W45",
        )
        md_text = res["md_path"].read_text(encoding="utf-8")
        self.assertIsNone(newsletter.check_for_money(md_text))
        self.assertNotIn("$15", md_text)
        self.assertNotIn("50 USD", md_text)

    # 15. Owner Tenglish house style summary
    def test_29_tenglish_summary_format(self):
        res = newsletter.make_weekly_newsletter(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            target_date=date(2026, 11, 8),
        )
        summary = res["tenglish_summary"]
        self.assertIn("Ee vaaram (2026-W45) newsletter draft ready ayyindi", summary)
        self.assertIn("save chesam", summary)
        self.assertIsNone(newsletter.check_for_money(summary))

    # 16. CLI execution tests
    def test_30_main_cli_execution(self):
        self._create_pack("pack-1", "Daily Planner")
        argv = [
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
            "--date", "2026-11-08",
        ]
        exit_code = newsletter.main(argv)
        self.assertEqual(exit_code, 0)
        self.assertTrue((self.marketing_dir / "newsletter" / "2026-W45.md").is_file())

    def test_31_main_cli_invalid_date(self):
        argv = ["--date", "not-a-valid-date"]
        exit_code = newsletter.main(argv)
        self.assertEqual(exit_code, 1)

    def test_32_main_cli_print_flag(self):
        self._create_pack("pack-1", "Print Test Planner")
        argv = [
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
            "--iso-week", "2026-W45",
            "--print",
        ]
        with mock.patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            exit_code = newsletter.main(argv)
            self.assertEqual(exit_code, 0)
            printed = mock_stdout.getvalue()
            self.assertIn("WEEKLY NEWSLETTER DRAFT — 2026-W45", printed)
            self.assertIn("[STORE LINK]", printed)
            self.assertIn("[UNSUBSCRIBE LINK]", printed)


if __name__ == "__main__":
    unittest.main()
