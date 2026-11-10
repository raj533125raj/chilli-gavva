"""Unit tests for BINNY-V2B: Telegram Reports Plugin (tg_reports.py).

Comprehensive test suite covering 60+ unit tests:
  - Plugin structure and interface conformance (COMMANDS, handle())
  - Command routing for all 11 commands (/ops, /dashboard, /seo, /lint, /faq, /dup, /keywords, /calendar, /versions, /ideas, /boards)
  - Natural-language / Tenglish word variations for each command
  - Missing underlying modules (ops_report, ops_dashboard, seo_score, etc.) returning Tenglish explanations
  - Missing data files (keywords.json, scout_ideas, dup_report.json, post_plan.ics)
  - Pack ID extraction and missing pack ID handling
  - Execution of each report command with valid and mock data
  - Privacy scrubbing: no sensitive system paths leaked
  - Strict money/currency scrubbing: no currencies leaked
  - Message character limit strictly <= 3500 characters
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tg_reports


class TestReportsPluginBase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.env_patcher = mock.patch.dict(os.environ, {})
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]
        os.environ["CHILLI_TEST"] = "1"

        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = str(self.temp_path / "test.db")
        self._init_db(self.db_path)

        self.sent_docs: list[tuple[str, str]] = []
        self.sent_texts: list[tuple[str, any]] = []

    def tearDown(self):
        self.env_patcher.stop()
        self.temp_dir.cleanup()
        super().tearDown()

    def _init_db(self, path: str):
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scout_ideas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                category TEXT,
                idea TEXT,
                demand_score INTEGER,
                competition_score INTEGER,
                fit_score INTEGER,
                effort_score INTEGER,
                total_score INTEGER,
                reason TEXT,
                niche TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY,
                status TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _make_ctx(self) -> dict:
        return {
            "db_path": self.db_path,
            "chat_id": int("1234" "5678"),
            "now": datetime.datetime.now(datetime.timezone.utc),
            "send_text": lambda text, keyboard=None: self.sent_texts.append((text, keyboard)),
            "send_document": lambda path, caption="": self.sent_docs.append((path, caption)),
            "send_photo": lambda path, caption="": None,
            "send_media_group": lambda paths, caption="": None,
            "state_get": lambda k, d=None: d,
            "state_set": lambda k, v: None,
        }

    def _create_mock_pack(self, pack_num: int = 1, pack_name: str = "daily-planner"):
        folder = self.temp_path / "exported_content" / "products" / "upload" / f"{pack_num:02d}-{pack_name}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "product.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        (folder / "checklist.txt").write_text("checklist items", encoding="utf-8")
        listing_content = f"""Title: Daily Focus Planner 2026 - Printable Organization Pages
Description: This is a beautiful daily planner designed for desk organization and simple routine tracking.
It features clear sections, clean layout, undated pages, and printable sheets.
What you get:
- 1 PDF Planner Document
- 1 Instruction Sheet
- 1 Bonus Notes Template
Tags: planner, daily planner, focus, desk organization, routine tracker, printable sheets, undated planner, organization, simple, productivity, bullet journal, habits, stationery
"""
        (folder / "listing.txt").write_text(listing_content, encoding="utf-8")
        return folder


class TestPluginInterfaceAndStructure(TestReportsPluginBase):
    def test_commands_list_exists_and_valid(self):
        self.assertIsInstance(tg_reports.COMMANDS, list)
        self.assertGreaterEqual(len(tg_reports.COMMANDS), 11)
        expected_cmds = {"/ops", "/dashboard", "/seo", "/lint", "/faq", "/dup", "/keywords", "/calendar", "/versions", "/ideas", "/boards"}
        found_cmds = {c["cmd"] for c in tg_reports.COMMANDS}
        self.assertTrue(expected_cmds.issubset(found_cmds))

        for c in tg_reports.COMMANDS:
            self.assertIn("cmd", c)
            self.assertIn("words", c)
            self.assertIn("help_en", c)
            self.assertIn("help_te", c)
            self.assertTrue(isinstance(c["words"], list))

    def test_handle_non_matching_text(self):
        ctx = self._make_ctx()
        self.assertIsNone(tg_reports.handle("hello there", ctx))
        self.assertIsNone(tg_reports.handle("/unknown", ctx))
        self.assertIsNone(tg_reports.handle("", ctx))
        self.assertIsNone(tg_reports.handle(None, ctx))
        self.assertIsNone(tg_reports.handle(12345, ctx))

    def test_handle_case_insensitivity(self):
        ctx = self._make_ctx()
        res_upper = tg_reports.handle("/OPS", ctx)
        self.assertIsNotNone(res_upper)
        self.assertIn("text", res_upper)

        res_words = tg_reports.handle("Ops Report", ctx)
        self.assertIsNotNone(res_words)


class TestMissingModulesGuards(TestReportsPluginBase):
    def test_missing_ops_report(self):
        with mock.patch.dict("sys.modules", {"ops_report": None}):
            res = tg_reports.handle("/ops", self._make_ctx())
            self.assertIn("Ops report module ledu", res["text"])

    def test_missing_ops_dashboard(self):
        with mock.patch.dict("sys.modules", {"ops_dashboard": None}):
            res = tg_reports.handle("/dashboard", self._make_ctx())
            self.assertIn("Ops dashboard module ledu", res["text"])

    def test_missing_seo_score(self):
        self._create_mock_pack(1)
        with mock.patch("tg_reports.find_pack_dir", return_value=self.temp_path):
            with mock.patch.dict("sys.modules", {"seo_score": None}):
                res = tg_reports.handle("/seo 1", self._make_ctx())
                self.assertIn("SEO score module ledu", res["text"])

    def test_missing_listing_lint(self):
        self._create_mock_pack(1)
        with mock.patch("tg_reports.find_pack_dir", return_value=self.temp_path):
            with mock.patch.dict("sys.modules", {"listing_lint": None}):
                res = tg_reports.handle("/lint 1", self._make_ctx())
                self.assertIn("Listing lint module ledu", res["text"])

    def test_missing_faq_builder(self):
        self._create_mock_pack(1)
        with mock.patch("tg_reports.find_pack_dir", return_value=self.temp_path):
            with mock.patch.dict("sys.modules", {"faq_builder": None}):
                res = tg_reports.handle("/faq 1", self._make_ctx())
                self.assertIn("FAQ builder module ledu", res["text"])

    def test_missing_dup_check(self):
        with mock.patch("pathlib.Path.is_file", return_value=False):
            with mock.patch.dict("sys.modules", {"dup_check": None}):
                res = tg_reports.handle("/dup", self._make_ctx())
                self.assertIn("Duplicate check module ledu", res["text"])

    def test_missing_calendar_export(self):
        with mock.patch.dict("sys.modules", {"calendar_export": None}):
            res = tg_reports.handle("/calendar", self._make_ctx())
            self.assertIn("Calendar export module ledu", res["text"])

    def test_missing_pack_changelog(self):
        self._create_mock_pack(1)
        with mock.patch("tg_reports.find_pack_dir", return_value=self.temp_path):
            with mock.patch.dict("sys.modules", {"pack_changelog": None}):
                res = tg_reports.handle("/versions 1", self._make_ctx())
                self.assertIn("Pack changelog module ledu", res["text"])

    def test_missing_idea_scoring(self):
        with mock.patch.dict("sys.modules", {"idea_scoring": None}):
            res = tg_reports.handle("/ideas", self._make_ctx())
            self.assertIn("Idea scoring module ledu", res["text"])

    def test_missing_pin_boards(self):
        with mock.patch("pathlib.Path.is_file", return_value=False):
            with mock.patch.dict("sys.modules", {"pin_boards": None}):
                res = tg_reports.handle("/boards", self._make_ctx())
                self.assertIn("Pin boards module ledu", res["text"])


class TestOpsCommand(TestReportsPluginBase):
    def test_ops_slash_command(self):
        ctx = self._make_ctx()
        res = tg_reports.handle("/ops", ctx)
        self.assertIsNotNone(res)
        self.assertIn("Chilli Gavva Weekly Ops Summary", res["text"])

    def test_ops_natural_words(self):
        ctx = self._make_ctx()
        for phrase in ["ops", "ops report", "operations report", "ops vivaralu"]:
            res = tg_reports.handle(phrase, ctx)
            self.assertIsNotNone(res)
            self.assertIn("Ops Summary", res["text"])

    def test_ops_empty_summary(self):
        ctx = self._make_ctx()
        with mock.patch("ops_report.format_ops_text", return_value=""):
            res = tg_reports.handle("/ops", ctx)
            self.assertIn("Ops summary lo emi data ledu", res["text"])

    def test_ops_with_error(self):
        ctx = self._make_ctx()
        with mock.patch("ops_report.generate_ops_summary", side_effect=RuntimeError("disk err")):
            res = tg_reports.handle("/ops", ctx)
            self.assertIn("error vachindi", res["text"])


class TestDashboardCommand(TestReportsPluginBase):
    def test_dashboard_slash_command_generates_and_sends(self):
        ctx = self._make_ctx()
        with mock.patch("pathlib.Path.is_file", return_value=True):
            res = tg_reports.handle("/dashboard", ctx)
            self.assertEqual(res, {"handled": True})
            self.assertEqual(len(self.sent_docs), 1)
            self.assertIn("Private ops dashboard", self.sent_docs[0][1])

    def test_dashboard_natural_words(self):
        ctx = self._make_ctx()
        with mock.patch("pathlib.Path.is_file", return_value=True):
            for phrase in ["dashboard", "ops dashboard", "dashboard doc", "dashboard pampu"]:
                self.sent_docs.clear()
                res = tg_reports.handle(phrase, ctx)
                self.assertEqual(res, {"handled": True})
                self.assertEqual(len(self.sent_docs), 1)

    def test_dashboard_no_send_document_capability(self):
        ctx = self._make_ctx()
        del ctx["send_document"]
        res = tg_reports.handle("/dashboard", ctx)
        self.assertIn("capability ledu", res["text"])

    def test_dashboard_generation_failure(self):
        ctx = self._make_ctx()
        with mock.patch("pathlib.Path.is_file", return_value=False):
            with mock.patch("ops_dashboard.render_dashboard_to_file", side_effect=OSError("write err")):
                res = tg_reports.handle("/dashboard", ctx)
                self.assertIn("error vachindi", res["text"])


class TestSeoCommand(TestReportsPluginBase):
    def test_seo_missing_pack_number(self):
        ctx = self._make_ctx()
        res = tg_reports.handle("/seo", ctx)
        self.assertIn("E pack kosam SEO score kavali", res["text"])

    def test_seo_pack_not_found(self):
        ctx = self._make_ctx()
        res = tg_reports.handle("/seo 999", ctx)
        self.assertIn("Pack #999 ready folder dorakaledu", res["text"])

    def test_seo_success_full(self):
        pack_folder = self._create_mock_pack(1, "daily-focus")
        ctx = self._make_ctx()
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            res = tg_reports.handle("/seo 1", ctx)
            self.assertIsNotNone(res)
            self.assertIn("SEO Score for Pack #1", res["text"])
            self.assertIn("/100", res["text"])

    def test_seo_natural_words(self):
        pack_folder = self._create_mock_pack(2, "budget-tracker")
        ctx = self._make_ctx()
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            for phrase in ["seo 2", "seo score 2", "seo check 2", "seo vivaralu 2"]:
                res = tg_reports.handle(phrase, ctx)
                self.assertIsNotNone(res)
                self.assertIn("SEO Score for Pack #2", res["text"])

    def test_seo_exception_handling(self):
        pack_folder = self._create_mock_pack(1, "daily-focus")
        ctx = self._make_ctx()
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            with mock.patch("seo_score.score_pack_folder", side_effect=ValueError("bad pack")):
                res = tg_reports.handle("/seo 1", ctx)
                self.assertIn("error vachindi", res["text"])


class TestLintCommand(TestReportsPluginBase):
    def test_lint_missing_pack_id(self):
        ctx = self._make_ctx()
        res = tg_reports.handle("/lint", ctx)
        self.assertIn("E pack kosam lint check kavali", res["text"])

    def test_lint_pack_not_found(self):
        ctx = self._make_ctx()
        res = tg_reports.handle("/lint 999", ctx)
        self.assertIn("Pack #999 ready folder dorakaledu", res["text"])

    def test_lint_success_output(self):
        pack_folder = self._create_mock_pack(1, "fitness-journal")
        ctx = self._make_ctx()
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            res = tg_reports.handle("/lint 1", ctx)
            self.assertIn("Listing Lint for Pack #1:", res["text"])
            self.assertIn("PASS:", res["text"])

    def test_lint_natural_words(self):
        pack_folder = self._create_mock_pack(3, "habit-tracker")
        ctx = self._make_ctx()
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            for phrase in ["lint 3", "listing lint 3", "lint check 3", "listing check 3"]:
                res = tg_reports.handle(phrase, ctx)
                self.assertIsNotNone(res)
                self.assertIn("Listing Lint for Pack #3", res["text"])

    def test_lint_exception_handling(self):
        pack_folder = self._create_mock_pack(1, "fitness-journal")
        ctx = self._make_ctx()
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            with mock.patch("listing_lint.lint_folder", side_effect=RuntimeError("parse err")):
                res = tg_reports.handle("/lint 1", ctx)
                self.assertIn("error vachindi", res["text"])


class TestFaqCommand(TestReportsPluginBase):
    def test_faq_missing_pack_id(self):
        ctx = self._make_ctx()
        res = tg_reports.handle("/faq", ctx)
        self.assertIn("E pack kosam FAQ kavali", res["text"])

    def test_faq_pack_not_found(self):
        ctx = self._make_ctx()
        res = tg_reports.handle("/faq 999", ctx)
        self.assertIn("Pack #999 ready folder dorakaledu", res["text"])

    def test_faq_short_text_reply(self):
        pack_folder = self._create_mock_pack(1, "planner-pages")
        ctx = self._make_ctx()
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            res = tg_reports.handle("/faq 1", ctx)
            self.assertIsNotNone(res)
            self.assertIn("text", res)
            self.assertIn("buyer FAQ", res["text"])
            self.assertEqual(len(self.sent_docs), 0)

    def test_faq_long_sends_document(self):
        pack_folder = self._create_mock_pack(1, "planner-pages")
        ctx = self._make_ctx()
        long_faq = "# Big FAQ\n" + ("## Question?\nAnswer line.\n" * 300)
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            with mock.patch("faq_builder.build_faq", return_value=long_faq):
                res = tg_reports.handle("/faq 1", ctx)
                self.assertEqual(res, {"handled": True})
                self.assertEqual(len(self.sent_docs), 1)
                self.assertIn("Pack #1 Buyer FAQ Markdown document", self.sent_docs[0][1])

    def test_faq_natural_words(self):
        pack_folder = self._create_mock_pack(1, "planner-pages")
        ctx = self._make_ctx()
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            for phrase in ["faq 1", "buyer faq 1", "faq text 1", "faq pampu 1"]:
                res = tg_reports.handle(phrase, ctx)
                self.assertIsNotNone(res)

    def test_faq_exception_handling(self):
        pack_folder = self._create_mock_pack(1, "planner-pages")
        ctx = self._make_ctx()
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            with mock.patch("faq_builder.build_faq", side_effect=Exception("faq crash")):
                res = tg_reports.handle("/faq 1", ctx)
                self.assertIn("error vachindi", res["text"])


class TestDupCommand(TestReportsPluginBase):
    def test_dup_slash_command_with_clean_report(self):
        ctx = self._make_ctx()
        clean_report = {
            "total_packs": 3,
            "summary_tenglish": "Duplicate content ledu, ready packs antha clean ga distinct ga unnayi ✓.",
            "flagged_pairs": [],
        }
        with mock.patch("dup_check.check_duplicate_content", return_value=clean_report):
            with mock.patch("pathlib.Path.is_file", return_value=False):
                res = tg_reports.handle("/dup", ctx)
                self.assertIn("Duplicate Content Report (3 packs)", res["text"])
                self.assertIn("0.5 kante ekkuva duplicate unna pairs levu", res["text"])

    def test_dup_flagged_pairs_over_half(self):
        ctx = self._make_ctx()
        dup_report = {
            "total_packs": 2,
            "summary_tenglish": "Mee packs lo duplicate content undi.",
            "flagged_pairs": [
                {
                    "pack_a": "01-planner",
                    "pack_b": "02-planner",
                    "max_similarity": 0.65,
                    "reasons": ["descriptions share 65% word shingles"],
                }
            ],
        }
        with mock.patch("dup_check.check_duplicate_content", return_value=dup_report):
            with mock.patch("pathlib.Path.is_file", return_value=False):
                res = tg_reports.handle("/dup", ctx)
                self.assertIn("Pairs with similarity > 0.5 (1)", res["text"])
                self.assertIn("01-planner vs 02-planner: 65%", res["text"])

    def test_dup_natural_words(self):
        ctx = self._make_ctx()
        clean_report = {
            "total_packs": 1,
            "summary_tenglish": "clean",
            "flagged_pairs": [],
        }
        with mock.patch("dup_check.check_duplicate_content", return_value=clean_report):
            with mock.patch("pathlib.Path.is_file", return_value=False):
                for phrase in ["dup", "dup check", "duplicate check", "duplicates", "saripolchu"]:
                    res = tg_reports.handle(phrase, ctx)
                    self.assertIsNotNone(res)

    def test_dup_exception_handling(self):
        ctx = self._make_ctx()
        with mock.patch("pathlib.Path.is_file", return_value=False):
            with mock.patch("dup_check.check_duplicate_content", side_effect=Exception("dup err")):
                res = tg_reports.handle("/dup", ctx)
                self.assertIn("error vachindi", res["text"])


class TestKeywordsCommand(TestReportsPluginBase):
    def test_keywords_slash_command(self):
        ctx = self._make_ctx()
        mock_kws = [{"term": f"printable-{i}", "score": 10 - i} for i in range(25)]
        with mock.patch("keyword_bank.build_keyword_bank", return_value=mock_kws):
            with mock.patch("pathlib.Path.is_file", return_value=False):
                res = tg_reports.handle("/keywords", ctx)
                self.assertIn("Top 20 Keywords", res["text"])
                self.assertIn("1. printable-0 (score 10)", res["text"])
                self.assertIn("20. printable-19 (score -9)", res["text"])

    def test_keywords_plain_strings(self):
        ctx = self._make_ctx()
        mock_kws = [f"planner keyword {i}" for i in range(10)]
        with mock.patch("keyword_bank.build_keyword_bank", return_value=mock_kws):
            with mock.patch("pathlib.Path.is_file", return_value=False):
                res = tg_reports.handle("/keywords", ctx)
                self.assertIn("Top 10 Keywords", res["text"])
                self.assertIn("planner keyword 0", res["text"])

    def test_keywords_file_missing_and_no_builder(self):
        ctx = self._make_ctx()
        with mock.patch("pathlib.Path.is_file", return_value=False):
            with mock.patch("keyword_bank.build_keyword_bank", side_effect=Exception("no bank")):
                res = tg_reports.handle("/keywords", ctx)
                self.assertIn("Keywords data file ledu", res["text"])

    def test_keywords_natural_words(self):
        ctx = self._make_ctx()
        for phrase in ["keywords", "top keywords", "keywords list", "mukhyamaina padalu"]:
            res = tg_reports.handle(phrase, ctx)
            self.assertIsNotNone(res)


class TestCalendarCommand(TestReportsPluginBase):
    def test_calendar_slash_command_success(self):
        ctx = self._make_ctx()
        with mock.patch("pathlib.Path.is_file", return_value=True):
            res = tg_reports.handle("/calendar", ctx)
            self.assertEqual(res, {"handled": True})
            self.assertEqual(len(self.sent_docs), 1)
            self.assertIn("Post plan .ics calendar file", self.sent_docs[0][1])

    def test_calendar_no_send_doc_capability(self):
        ctx = self._make_ctx()
        del ctx["send_document"]
        res = tg_reports.handle("/calendar", ctx)
        self.assertIn("capability ledu", res["text"])

    def test_calendar_natural_words(self):
        ctx = self._make_ctx()
        with mock.patch("pathlib.Path.is_file", return_value=True):
            for phrase in ["calendar", "calendar export", "ics file", "calendar pampu"]:
                self.sent_docs.clear()
                res = tg_reports.handle(phrase, ctx)
                self.assertEqual(res, {"handled": True})
                self.assertEqual(len(self.sent_docs), 1)

    def test_calendar_generation_failure(self):
        ctx = self._make_ctx()
        with mock.patch("pathlib.Path.is_file", return_value=False):
            with mock.patch("calendar_export.export_calendar", side_effect=Exception("cal err")):
                res = tg_reports.handle("/calendar", ctx)
                self.assertIn("error vachindi", res["text"])


class TestVersionsCommand(TestReportsPluginBase):
    def test_versions_missing_pack_id(self):
        ctx = self._make_ctx()
        res = tg_reports.handle("/versions", ctx)
        self.assertIn("E pack kosam versions chudali", res["text"])

    def test_versions_pack_not_found(self):
        ctx = self._make_ctx()
        res = tg_reports.handle("/versions 999", ctx)
        self.assertIn("Pack #999 ready folder dorakaledu", res["text"])

    def test_versions_success_output(self):
        pack_folder = self._create_mock_pack(1, "wedding-binder")
        ctx = self._make_ctx()
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            res = tg_reports.handle("/versions 1", ctx)
            self.assertIn("Version Report for", res["text"])
            self.assertIn("Current Version: v1.0", res["text"])

    def test_versions_natural_words(self):
        pack_folder = self._create_mock_pack(2, "meal-planner")
        ctx = self._make_ctx()
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            for phrase in ["versions 2", "pack version 2", "changelog 2", "version vivaralu 2"]:
                res = tg_reports.handle(phrase, ctx)
                self.assertIsNotNone(res)
                self.assertIn("Version Report", res["text"])

    def test_versions_with_diff_changes(self):
        pack_folder = self._create_mock_pack(1, "wedding-binder")
        ctx = self._make_ctx()
        mock_res = {
            "version": "1.1",
            "previous_version": "1.0",
            "tenglish_summary": "minor version bump",
            "diff": {
                "added": ["bonus.pdf"],
                "modified": ["listing.txt"],
                "removed": ["old.txt"],
            },
        }
        with mock.patch("tg_reports.find_pack_dir", return_value=pack_folder):
            with mock.patch("pack_changelog.process_pack", return_value=mock_res):
                res = tg_reports.handle("/versions 1", ctx)
                self.assertIn("Added: bonus.pdf", res["text"])
                self.assertIn("Modified: listing.txt", res["text"])
                self.assertIn("Removed: old.txt", res["text"])


class TestIdeasCommand(TestReportsPluginBase):
    def test_ideas_no_ideas_found(self):
        ctx = self._make_ctx()
        with mock.patch("pathlib.Path.is_file", return_value=False):
            res = tg_reports.handle("/ideas", ctx)
            self.assertIn("Scout ideas database lo emi levu", res["text"])

    def test_ideas_from_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO scout_ideas (idea, category, niche) VALUES ('Self Care Routine Checklist', 'Printables', 'wellness')")
        conn.commit()
        conn.close()

        ctx = self._make_ctx()
        res = tg_reports.handle("/ideas", ctx)
        self.assertIn("Scout Ideas Scoring (1 ideas)", res["text"])
        self.assertIn("Self Care Routine Checklist", res["text"])
        self.assertIn("Score:", res["text"])

    def test_ideas_natural_words(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO scout_ideas (idea, category, niche) VALUES ('Study Schedule Template', 'Planners', 'students')")
        conn.commit()
        conn.close()

        ctx = self._make_ctx()
        for phrase in ["ideas", "scout ideas", "idea score", "ideas scoring"]:
            res = tg_reports.handle(phrase, ctx)
            self.assertIsNotNone(res)
            self.assertIn("Scout Ideas Scoring", res["text"])

    def test_ideas_exception_handling(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO scout_ideas (idea, category, niche) VALUES ('Broken Idea', 'Planners', 'test')")
        conn.commit()
        conn.close()

        ctx = self._make_ctx()
        with mock.patch("idea_scoring.score_ideas_list", side_effect=Exception("scoring err")):
            res = tg_reports.handle("/ideas", ctx)
            self.assertIn("error vachindi", res["text"])


class TestBoardsCommand(TestReportsPluginBase):
    def test_boards_slash_command(self):
        ctx = self._make_ctx()
        mock_plan = {
            "boards_count": 5,
            "total_unique_pins": 15,
            "tenglish_summary": "Pinterest board plan ready ga undi: 5 boards create cheyandi.",
            "boards": [
                {"name": "Mindful Productivity Planners", "pins": [{"id": "p1"}, {"id": "p2"}]},
                {"name": "Desk Organization Printables", "pins": [{"id": "p3"}]},
            ],
        }
        with mock.patch("pin_boards.build_board_plan", return_value=mock_plan):
            with mock.patch("pathlib.Path.is_file", return_value=False):
                res = tg_reports.handle("/boards", ctx)
                self.assertIn("Pinterest Boards Plan (5 boards, 15 unique pins)", res["text"])
                self.assertIn("Mindful Productivity Planners (2 pins mapped)", res["text"])

    def test_boards_natural_words(self):
        ctx = self._make_ctx()
        for phrase in ["boards", "pinterest boards", "board plan", "boards summary"]:
            res = tg_reports.handle(phrase, ctx)
            self.assertIsNotNone(res)

    def test_boards_exception_handling(self):
        ctx = self._make_ctx()
        with mock.patch("pathlib.Path.is_file", return_value=False):
            with mock.patch("pin_boards.build_board_plan", side_effect=Exception("boards err")):
                res = tg_reports.handle("/boards", ctx)
                self.assertIn("error vachindi", res["text"])


class TestPrivacyAndScrubbing(TestReportsPluginBase):
    def test_scrub_outgoing_replaces_system_paths(self):
        raw = "Error in /home/user/app/temp.py and /var/log/syslog.log but check /app/exported_content/products/upload/01-pack/listing.txt"
        scrubbed = tg_reports.scrub_outgoing(raw)
        self.assertNotIn("/home/user/app", scrubbed)
        self.assertNotIn("/var/log", scrubbed)
        self.assertIn("exported_content/products/upload/01-pack/listing.txt", scrubbed)
        self.assertIn("temp.py", scrubbed)
        self.assertIn("syslog.log", scrubbed)

    def test_scrub_outgoing_removes_money(self):
        for raw in ["Price is $10 or 500 INR", "Only €15.99 for all sheets", "Save 200 Rs on digital pack"]:
            scrubbed = tg_reports.scrub_outgoing(raw)
            self.assertNotIn("$10", scrubbed)
            self.assertNotIn("500 INR", scrubbed)
            self.assertNotIn("€15.99", scrubbed)
            self.assertNotIn("200 Rs", scrubbed)

    def test_scrub_outgoing_enforces_max_length(self):
        raw = "word " * 1000
        scrubbed = tg_reports.scrub_outgoing(raw)
        self.assertLessEqual(len(scrubbed), 3500)
        self.assertIn("...(cut, message length limit)", scrubbed)

    def test_find_pack_dir_variants(self):
        p1_dir = self.temp_path / "upload" / "01-daily-planner"
        p1_dir.mkdir(parents=True, exist_ok=True)
        (p1_dir / "product.zip").write_bytes(b"data")
        (p1_dir / "listing.txt").write_text("Title: One")

        p2_dir = self.temp_path / "upload" / "02-fitness-journal"
        p2_dir.mkdir(parents=True, exist_ok=True)
        (p2_dir / "product.zip").write_bytes(b"data")
        (p2_dir / "listing.txt").write_text("Title: Two")

        p1 = tg_reports.find_pack_dir(1, products_dir=self.temp_path)
        self.assertIsNotNone(p1)
        self.assertIn("01-daily-planner", p1.name)

        p2 = tg_reports.find_pack_dir("02", products_dir=self.temp_path)
        self.assertIsNotNone(p2)
        self.assertIn("02-fitness-journal", p2.name)

        p_none = tg_reports.find_pack_dir(99, products_dir=self.temp_path)
        self.assertIsNone(p_none)

    def test_extract_pack_id(self):
        self.assertEqual(tg_reports.extract_pack_id("/seo 1"), "1")
        self.assertEqual(tg_reports.extract_pack_id("/seo pack 5"), "5")
        self.assertEqual(tg_reports.extract_pack_id("seo #12"), "12")
        self.assertEqual(tg_reports.extract_pack_id("lint 03"), "03")
        self.assertEqual(tg_reports.extract_pack_id("faq"), None)
        self.assertEqual(tg_reports.extract_pack_id(""), None)


if __name__ == "__main__":
    unittest.main()
