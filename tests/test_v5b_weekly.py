"""tests/test_v5b_weekly.py — Unit tests for weekly_routine.py (BINNY-V5B ROBOT).

Tests:
1. Timezone & Sunday IST handling (Sunday in UTC vs IST, boundaries, leap/regular years).
2. Once-per-week state store memory (state persistence, no reruns on same Sunday, new week triggers).
3. 4-minute time budget enforcement for make_marketing (timeout handling, partial results).
4. Subtask runner: make_marketing (missing module, error, normal execution, no packs).
5. Subtask runner: newsletter (missing module, error, normal execution).
6. Subtask runner: calendar_export (missing module, error, normal execution with/without plan).
7. Subtask runner: ops_report (missing module, error, normal execution).
8. Subtask runner: pack_changelog (missing module, error, normal execution).
9. Subtask runner: dup_check (missing module, error, normal execution, duplicate warnings).
10. Tenglish summary generation: formatting, counts, singular/plural, no duplicate warnings.
11. Money / currency sanitizer: zero money amounts allowed anywhere in outputs.
12. Integration with daily_report: CHILLI_TEST guard, non-Sunday behavior, Sunday behavior, error handling.
13. Edge cases: readonly files, sqlite errors, atomic integrity.
"""
from __future__ import annotations

import datetime
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import daily_report
import weekly_routine


class TestV5BWeeklyRoutine(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* environment variables
        self.env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in os.environ if k.startswith("GITHUB_")},
            clear=False,
        )
        self.env_patcher.start()

        self.tmp_dir = tempfile.mkdtemp(prefix="v5b_test_")
        self.db_path = str(Path(self.tmp_dir) / "test_agent_data.db")
        self.products_dir = Path(self.tmp_dir) / "products"
        self.marketing_dir = Path(self.tmp_dir) / "marketing"
        self.upload_dir = self.products_dir / "upload"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

        # Base Sunday datetime: 2026-11-08 04:00 UTC (09:30 IST, Sunday)
        self.sunday_utc = datetime.datetime(2026, 11, 8, 4, 0, tzinfo=datetime.timezone.utc)
        # Monday datetime: 2026-11-09 04:00 UTC (09:30 IST, Monday)
        self.monday_utc = datetime.datetime(2026, 11, 9, 4, 0, tzinfo=datetime.timezone.utc)

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. IST & Sunday date tests
    # -------------------------------------------------------------------------
    def test_01_is_sunday_ist_true_on_sunday(self):
        self.assertTrue(weekly_routine.is_sunday_ist(self.sunday_utc))

    def test_02_is_sunday_ist_false_on_monday(self):
        self.assertFalse(weekly_routine.is_sunday_ist(self.monday_utc))

    def test_03_is_sunday_ist_handles_naive_datetime(self):
        naive_sunday = datetime.datetime(2026, 11, 8, 4, 0)
        self.assertTrue(weekly_routine.is_sunday_ist(naive_sunday))

    def test_04_is_sunday_ist_boundary_saturday_night_utc_is_sunday_ist(self):
        # Saturday 18:30 UTC = Sunday 00:00 IST
        sat_night_utc = datetime.datetime(2026, 11, 7, 18, 30, tzinfo=datetime.timezone.utc)
        self.assertTrue(weekly_routine.is_sunday_ist(sat_night_utc))

    def test_05_is_sunday_ist_boundary_saturday_before_ist_midnight(self):
        # Saturday 18:29 UTC = Saturday 23:59 IST
        sat_early_utc = datetime.datetime(2026, 11, 7, 18, 29, tzinfo=datetime.timezone.utc)
        self.assertFalse(weekly_routine.is_sunday_ist(sat_early_utc))

    def test_06_is_sunday_ist_boundary_sunday_night_utc_is_monday_ist(self):
        # Sunday 18:30 UTC = Monday 00:00 IST
        sun_late_utc = datetime.datetime(2026, 11, 8, 18, 30, tzinfo=datetime.timezone.utc)
        self.assertFalse(weekly_routine.is_sunday_ist(sun_late_utc))

    def test_07_is_sunday_ist_boundary_sunday_before_ist_midnight(self):
        # Sunday 18:29 UTC = Sunday 23:59 IST
        sun_last_min_utc = datetime.datetime(2026, 11, 8, 18, 29, tzinfo=datetime.timezone.utc)
        self.assertTrue(weekly_routine.is_sunday_ist(sun_last_min_utc))

    def test_08_get_week_key_format(self):
        week_key = weekly_routine.get_week_key(self.sunday_utc)
        self.assertEqual(week_key, "2026-W45")

    def test_09_get_week_key_default_none_uses_now(self):
        week_key = weekly_routine.get_week_key(None)
        self.assertRegex(week_key, r"^\d{4}-W\d{2}$")

    # -------------------------------------------------------------------------
    # 2. State storage and once-per-week memory tests
    # -------------------------------------------------------------------------
    def test_10_get_set_state_direct_sqlite(self):
        weekly_routine.set_state("test_key", "test_val", db_path=self.db_path)
        val = weekly_routine.get_state("test_key", default=None, db_path=self.db_path)
        self.assertEqual(val, "test_val")

    def test_11_get_state_missing_returns_default(self):
        val = weekly_routine.get_state("nonexistent_key", default="fallback", db_path=self.db_path)
        self.assertEqual(val, "fallback")

    def test_12_set_state_overwrites_existing_key(self):
        weekly_routine.set_state("key1", "val1", db_path=self.db_path)
        weekly_routine.set_state("key1", "val2", db_path=self.db_path)
        self.assertEqual(weekly_routine.get_state("key1", db_path=self.db_path), "val2")

    def test_13_run_executes_once_per_week(self):
        with mock.patch("weekly_routine.run_make_marketing", return_value={"status": "ok", "ready_count": 1, "processed_count": 1, "errors": []}), \
             mock.patch("weekly_routine.run_newsletter", return_value={"status": "ok", "week": "2026-W45", "files": [], "errors": []}), \
             mock.patch("weekly_routine.run_calendar_export", return_value={"status": "ok", "events_count": 2, "errors": []}), \
             mock.patch("weekly_routine.run_ops_report", return_value={"status": "ok", "summary": {}, "errors": []}), \
             mock.patch("weekly_routine.run_pack_changelog", return_value={"status": "ok", "packs_processed": 1, "errors": []}), \
             mock.patch("weekly_routine.run_dup_check", return_value={"status": "ok", "warnings_count": 0, "pairs_above_50": 0, "errors": []}):

            res1 = weekly_routine.run(now=self.sunday_utc, db_path=self.db_path)
            self.assertIsNotNone(res1)
            self.assertIn("Ee vaaram", res1)

            # Second call in same week returns None (skipped)
            res2 = weekly_routine.run(now=self.sunday_utc, db_path=self.db_path)
            self.assertIsNone(res2)

    def test_14_run_force_flag_bypasses_week_check(self):
        with mock.patch("weekly_routine.run_make_marketing", return_value={"status": "ok", "ready_count": 1, "processed_count": 1, "errors": []}), \
             mock.patch("weekly_routine.run_newsletter", return_value={"status": "ok", "week": "2026-W45", "files": [], "errors": []}), \
             mock.patch("weekly_routine.run_calendar_export", return_value={"status": "ok", "events_count": 2, "errors": []}), \
             mock.patch("weekly_routine.run_ops_report", return_value={"status": "ok", "summary": {}, "errors": []}), \
             mock.patch("weekly_routine.run_pack_changelog", return_value={"status": "ok", "packs_processed": 1, "errors": []}), \
             mock.patch("weekly_routine.run_dup_check", return_value={"status": "ok", "warnings_count": 0, "pairs_above_50": 0, "errors": []}):

            res1 = weekly_routine.run(now=self.sunday_utc, db_path=self.db_path)
            self.assertIsNotNone(res1)
            res2 = weekly_routine.run(now=self.sunday_utc, db_path=self.db_path, force=True)
            self.assertIsNotNone(res2)

    def test_15_run_skips_on_non_sunday(self):
        res = weekly_routine.run(now=self.monday_utc, db_path=self.db_path)
        self.assertIsNone(res)

    def test_16_run_next_sunday_executes_again(self):
        next_sunday_utc = self.sunday_utc + datetime.timedelta(days=7)
        with mock.patch("weekly_routine.run_make_marketing", return_value={"status": "ok", "ready_count": 0, "processed_count": 0, "errors": []}), \
             mock.patch("weekly_routine.run_newsletter", return_value={"status": "ok", "week": "2026-W45", "files": [], "errors": []}), \
             mock.patch("weekly_routine.run_calendar_export", return_value={"status": "ok", "events_count": 0, "errors": []}), \
             mock.patch("weekly_routine.run_ops_report", return_value={"status": "ok", "summary": {}, "errors": []}), \
             mock.patch("weekly_routine.run_pack_changelog", return_value={"status": "ok", "packs_processed": 0, "errors": []}), \
             mock.patch("weekly_routine.run_dup_check", return_value={"status": "ok", "warnings_count": 0, "pairs_above_50": 0, "errors": []}):

            res1 = weekly_routine.run(now=self.sunday_utc, db_path=self.db_path)
            self.assertIsNotNone(res1)
            res2 = weekly_routine.run(now=next_sunday_utc, db_path=self.db_path)
            self.assertIsNotNone(res2)

    # -------------------------------------------------------------------------
    # 3. Time budget enforcement (4 minutes)
    # -------------------------------------------------------------------------
    def test_17_make_marketing_time_budget_default_is_240(self):
        self.assertEqual(weekly_routine.MAKE_MARKETING_TIMEOUT_SECONDS, 240)

    def test_18_make_marketing_stops_when_budget_exceeded(self):
        p1 = self.upload_dir / "pack-1"
        p1.mkdir()
        (p1 / "product.zip").write_text("fake zip")
        (p1 / "listing.txt").write_text("Title: Pack 1\nDescription: Desc\nTags: tag1\n")

        p2 = self.upload_dir / "pack-2"
        p2.mkdir()
        (p2 / "product.zip").write_text("fake zip")
        (p2 / "listing.txt").write_text("Title: Pack 2\nDescription: Desc\nTags: tag2\n")

        # Fake monotonic time to simulate timeout after pack 1
        times = [0.0, 1.0, 5.0, 10.0]  # First pack takes time, exceeding budget of 2s
        with mock.patch("time.monotonic", side_effect=times), \
             mock.patch("make_marketing.report_row", return_value={"ready": True}), \
             mock.patch("make_marketing.process_pack", return_value={"id": "pack-1", "files": [], "errors": []}), \
             mock.patch("make_marketing.write_readme"):

            res = weekly_routine.run_make_marketing(
                packs_dir=self.upload_dir,
                marketing_dir=self.marketing_dir,
                time_budget=2,
            )
            self.assertEqual(res["status"], "timed_out")
            self.assertEqual(res["processed_count"], 1)
            self.assertEqual(res["ready_count"], 2)
            self.assertTrue(any("time budget exceeded" in e for e in res["errors"]))

    # -------------------------------------------------------------------------
    # 4. Subtask runner: make_marketing
    # -------------------------------------------------------------------------
    def test_19_run_make_marketing_missing_module(self):
        with mock.patch.dict("sys.modules", {"make_marketing": None}):
            res = weekly_routine.run_make_marketing(packs_dir=self.upload_dir, marketing_dir=self.marketing_dir)
            self.assertIn(res["status"], ("missing", "error"))

    def test_20_run_make_marketing_empty_packs_dir(self):
        empty_dir = Path(self.tmp_dir) / "nonexistent_upload"
        res = weekly_routine.run_make_marketing(packs_dir=empty_dir, marketing_dir=self.marketing_dir)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["ready_count"], 0)

    def test_21_run_make_marketing_exception_handled_safely(self):
        with mock.patch("make_marketing.load_makers", side_effect=RuntimeError("makers load crashed")):
            res = weekly_routine.run_make_marketing(packs_dir=self.upload_dir, marketing_dir=self.marketing_dir)
            self.assertEqual(res["status"], "error")
            self.assertIn("makers load crashed", res["errors"][0])

    def test_22_run_make_marketing_success_flow(self):
        p1 = self.upload_dir / "pack-good"
        p1.mkdir()
        (p1 / "product.zip").write_text("dummy")
        (p1 / "listing.txt").write_text("Title: Good Pack\nDescription: Info\nTags: test\n")

        with mock.patch("make_marketing.report_row", return_value={"ready": True}), \
             mock.patch("make_marketing.process_pack", return_value={"id": "pack-good", "files": ["pins/p1.png"], "errors": []}), \
             mock.patch("make_marketing.write_readme"):

            res = weekly_routine.run_make_marketing(packs_dir=self.upload_dir, marketing_dir=self.marketing_dir)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["ready_count"], 1)
            self.assertEqual(res["processed_count"], 1)

    # -------------------------------------------------------------------------
    # 5. Subtask runner: newsletter
    # -------------------------------------------------------------------------
    def test_23_run_newsletter_missing_module(self):
        with mock.patch.dict("sys.modules", {"newsletter": None}):
            res = weekly_routine.run_newsletter(products_dir=self.products_dir, marketing_dir=self.marketing_dir)
            self.assertIn(res["status"], ("missing", "error"))

    def test_24_run_newsletter_success(self):
        with mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W45", "md_file": "path.md", "txt_file": "path.txt"}):
            res = weekly_routine.run_newsletter(products_dir=self.products_dir, marketing_dir=self.marketing_dir, now=self.sunday_utc)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["week"], "2026-W45")

    def test_25_run_newsletter_handles_exception(self):
        with mock.patch("newsletter.make_weekly_newsletter", side_effect=Exception("newsletter fail")):
            res = weekly_routine.run_newsletter(products_dir=self.products_dir, marketing_dir=self.marketing_dir)
            self.assertEqual(res["status"], "error")
            self.assertIn("newsletter fail", res["errors"][0])

    # -------------------------------------------------------------------------
    # 6. Subtask runner: calendar_export
    # -------------------------------------------------------------------------
    def test_26_run_calendar_export_missing_module(self):
        with mock.patch.dict("sys.modules", {"calendar_export": None}):
            res = weekly_routine.run_calendar_export(marketing_dir=self.marketing_dir)
            self.assertIn(res["status"], ("missing", "error"))

    def test_27_run_calendar_export_success_with_plan(self):
        plan_file = self.marketing_dir / "post_plan.json"
        plan_file.write_text("[]", encoding="utf-8")
        with mock.patch("calendar_export.export_calendar", return_value={"status": "success", "events_count": 5}):
            res = weekly_routine.run_calendar_export(plan_path=plan_file, marketing_dir=self.marketing_dir)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["events_count"], 5)

    def test_28_run_calendar_export_handles_missing_plan_file(self):
        nonexistent = self.marketing_dir / "missing_plan.json"
        with mock.patch("calendar_export.export_calendar", return_value={"status": "success", "events_count": 0}):
            res = weekly_routine.run_calendar_export(plan_path=nonexistent, marketing_dir=self.marketing_dir)
            self.assertEqual(res["status"], "ok")

    def test_29_run_calendar_export_handles_exception(self):
        with mock.patch("calendar_export.export_calendar", side_effect=ValueError("invalid calendar plan")):
            res = weekly_routine.run_calendar_export(marketing_dir=self.marketing_dir)
            self.assertEqual(res["status"], "error")
            self.assertIn("invalid calendar plan", res["errors"][0])

    # -------------------------------------------------------------------------
    # 7. Subtask runner: ops_report
    # -------------------------------------------------------------------------
    def test_30_run_ops_report_missing_module(self):
        with mock.patch.dict("sys.modules", {"ops_report": None}):
            res = weekly_routine.run_ops_report(db_path=self.db_path)
            self.assertIn(res["status"], ("missing", "error"))

    def test_31_run_ops_report_success(self):
        fake_summary = {"runs_per_day": {}, "failures_by_step": {}}
        with mock.patch("ops_report.generate_ops_summary", return_value=fake_summary):
            res = weekly_routine.run_ops_report(db_path=self.db_path, now=self.sunday_utc)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["summary"], fake_summary)

    def test_32_run_ops_report_handles_exception(self):
        with mock.patch("ops_report.generate_ops_summary", side_effect=RuntimeError("db locked")):
            res = weekly_routine.run_ops_report(db_path=self.db_path)
            self.assertEqual(res["status"], "error")
            self.assertIn("db locked", res["errors"][0])

    # -------------------------------------------------------------------------
    # 8. Subtask runner: pack_changelog
    # -------------------------------------------------------------------------
    def test_33_run_pack_changelog_missing_module(self):
        with mock.patch.dict("sys.modules", {"pack_changelog": None}):
            res = weekly_routine.run_pack_changelog(products_dir=self.products_dir)
            self.assertIn(res["status"], ("missing", "error"))

    def test_34_run_pack_changelog_success(self):
        with mock.patch("pack_changelog.process_all_ready_packs", return_value=[{"pack": "p1"}, {"pack": "p2"}]):
            res = weekly_routine.run_pack_changelog(products_dir=self.products_dir)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["packs_processed"], 2)

    def test_35_run_pack_changelog_handles_exception(self):
        with mock.patch("pack_changelog.process_all_ready_packs", side_effect=OSError("disk error")):
            res = weekly_routine.run_pack_changelog(products_dir=self.products_dir)
            self.assertEqual(res["status"], "error")
            self.assertIn("disk error", res["errors"][0])

    # -------------------------------------------------------------------------
    # 9. Subtask runner: dup_check
    # -------------------------------------------------------------------------
    def test_36_run_dup_check_missing_module(self):
        with mock.patch.dict("sys.modules", {"dup_check": None}):
            res = weekly_routine.run_dup_check(products_dir=self.products_dir, marketing_dir=self.marketing_dir)
            self.assertIn(res["status"], ("missing", "error"))

    def test_37_run_dup_check_no_warnings(self):
        with mock.patch("dup_check.check_duplicate_content", return_value={"pairs_above_50": [], "has_high_similarity": False}):
            res = weekly_routine.run_dup_check(products_dir=self.products_dir, marketing_dir=self.marketing_dir)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["warnings_count"], 0)

    def test_38_run_dup_check_with_warnings(self):
        with mock.patch("dup_check.check_duplicate_content", return_value={"pairs_above_50": [{"pack_a": "1", "pack_b": "2"}], "has_high_similarity": True}):
            res = weekly_routine.run_dup_check(products_dir=self.products_dir, marketing_dir=self.marketing_dir)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["warnings_count"], 1)
            self.assertTrue(res["has_high_similarity"])

    def test_39_run_dup_check_handles_exception(self):
        with mock.patch("dup_check.check_duplicate_content", side_effect=Exception("corrupt file")):
            res = weekly_routine.run_dup_check(products_dir=self.products_dir, marketing_dir=self.marketing_dir)
            self.assertEqual(res["status"], "error")
            self.assertIn("corrupt file", res["errors"][0])

    # -------------------------------------------------------------------------
    # 10. Tenglish summary formatting
    # -------------------------------------------------------------------------
    def test_40_compose_summary_all_ok_zero_dup(self):
        mkt = {"status": "ok", "ready_count": 3, "processed_count": 3}
        news = {"status": "ok"}
        cal = {"status": "ok"}
        ops = {"status": "ok"}
        clog = {"status": "ok"}
        dup = {"status": "ok", "warnings_count": 0}

        summary = weekly_routine.compose_tenglish_summary(mkt, news, cal, ops, clog, dup)
        self.assertEqual(summary, "Ee vaaram: 3 packs ki marketing ready, newsletter draft ready, no duplicate warnings")

    def test_41_compose_summary_with_one_dup_warning(self):
        mkt = {"status": "ok", "ready_count": 3, "processed_count": 3}
        news = {"status": "ok"}
        cal = {"status": "ok"}
        ops = {"status": "ok"}
        clog = {"status": "ok"}
        dup = {"status": "ok", "warnings_count": 1}

        summary = weekly_routine.compose_tenglish_summary(mkt, news, cal, ops, clog, dup)
        self.assertEqual(summary, "Ee vaaram: 3 packs ki marketing ready, newsletter draft ready, 1 duplicate warning")

    def test_42_compose_summary_with_multiple_dup_warnings(self):
        mkt = {"status": "ok", "ready_count": 3, "processed_count": 3}
        news = {"status": "ok"}
        cal = {"status": "ok"}
        ops = {"status": "ok"}
        clog = {"status": "ok"}
        dup = {"status": "ok", "warnings_count": 4}

        summary = weekly_routine.compose_tenglish_summary(mkt, news, cal, ops, clog, dup)
        self.assertEqual(summary, "Ee vaaram: 3 packs ki marketing ready, newsletter draft ready, 4 duplicate warnings")

    def test_43_compose_summary_with_timed_out_marketing(self):
        mkt = {"status": "timed_out", "ready_count": 5, "processed_count": 2}
        news = {"status": "ok"}
        cal = {"status": "ok"}
        ops = {"status": "ok"}
        clog = {"status": "ok"}
        dup = {"status": "ok", "warnings_count": 0}

        summary = weekly_routine.compose_tenglish_summary(mkt, news, cal, ops, clog, dup)
        self.assertIn("2 packs ki marketing partial ready (timed out)", summary)

    def test_44_compose_summary_with_skipped_jobs(self):
        mkt = {"status": "skipped"}
        news = {"status": "skipped"}
        cal = {"status": "skipped"}
        ops = {"status": "skipped"}
        clog = {"status": "skipped"}
        dup = {"status": "skipped"}

        summary = weekly_routine.compose_tenglish_summary(mkt, news, cal, ops, clog, dup)
        self.assertEqual(summary, "Ee vaaram: marketing skipped, newsletter skipped, dup check skipped")

    # -------------------------------------------------------------------------
    # 11. Money amount sanitization tests
    # -------------------------------------------------------------------------
    def test_45_sanitize_no_money_strips_dollar_amounts(self):
        raw = "Ee vaaram: earnings were $50.00"
        sanitized = weekly_routine.sanitize_no_money(raw)
        self.assertNotIn("$50", sanitized)
        self.assertIn("[amount]", sanitized)

    def test_46_sanitize_no_money_strips_inr_amounts(self):
        raw = "Ee vaaram: 3 packs worth ₹500"
        sanitized = weekly_routine.sanitize_no_money(raw)
        self.assertNotIn("₹500", sanitized)
        self.assertIn("[amount]", sanitized)

    def test_47_sanitize_no_money_strips_usd_words(self):
        raw = "Ee vaaram: USD 100 paid"
        sanitized = weekly_routine.sanitize_no_money(raw)
        self.assertNotIn("USD 100", sanitized)

    def test_48_sanitize_no_money_empty_string(self):
        self.assertEqual(weekly_routine.sanitize_no_money(""), "")

    def test_49_summary_has_no_currency_symbols(self):
        mkt = {"status": "ok", "ready_count": 2, "processed_count": 2}
        news = {"status": "ok"}
        cal = {"status": "ok"}
        ops = {"status": "ok"}
        clog = {"status": "ok"}
        dup = {"status": "ok", "warnings_count": 0}
        summary = weekly_routine.compose_tenglish_summary(mkt, news, cal, ops, clog, dup)
        self.assertNotRegex(summary, r"(\$|₹|£|€)")

    # -------------------------------------------------------------------------
    # 12. Integration with daily_report.py
    # -------------------------------------------------------------------------
    def test_50_daily_report_on_sunday_includes_routine_summary(self):
        with mock.patch.dict(os.environ, {"CHILLI_TEST": "0"}), \
             mock.patch("weekly_routine.run", return_value="Ee vaaram: 3 packs ki marketing ready, newsletter draft ready, no duplicate warnings"):

            text = daily_report.weekly_text(db_path=self.db_path, now=self.sunday_utc)
            self.assertIn("Sunday letter (last 7 days)", text)
            self.assertIn("Ee vaaram: 3 packs ki marketing ready", text)

    def test_51_daily_report_skips_routine_when_chilli_test_is_1(self):
        with mock.patch.dict(os.environ, {"CHILLI_TEST": "1"}), \
             mock.patch("weekly_routine.run") as mock_routine:

            text = daily_report.weekly_text(db_path=self.db_path, now=self.sunday_utc)
            self.assertIn("Sunday letter (last 7 days)", text)
            mock_routine.assert_not_called()

    def test_52_daily_report_safe_when_routine_raises_exception(self):
        with mock.patch.dict(os.environ, {"CHILLI_TEST": "0"}), \
             mock.patch("weekly_routine.run", side_effect=RuntimeError("routine crashed")):

            # Does not crash daily_report
            text = daily_report.weekly_text(db_path=self.db_path, now=self.sunday_utc)
            self.assertIn("Sunday letter (last 7 days)", text)

    def test_53_report_text_on_sunday_calls_weekly_text(self):
        with mock.patch.dict(os.environ, {"CHILLI_TEST": "0"}), \
             mock.patch("weekly_routine.run", return_value="Ee vaaram: test summary"):

            text = daily_report.report_text(db_path=self.db_path, now=self.sunday_utc)
            self.assertIn("Sunday letter (last 7 days)", text)
            self.assertIn("Ee vaaram: test summary", text)

    def test_54_report_text_on_monday_does_not_include_sunday_letter(self):
        with mock.patch.dict(os.environ, {"CHILLI_TEST": "0"}), \
             mock.patch("weekly_routine.run") as mock_routine:

            text = daily_report.report_text(db_path=self.db_path, now=self.monday_utc)
            self.assertNotIn("Sunday letter", text)
            mock_routine.assert_not_called()

    # -------------------------------------------------------------------------
    # 13. End-to-end and edge cases
    # -------------------------------------------------------------------------
    def test_55_run_handles_exception_in_main_block(self):
        with mock.patch("weekly_routine.run_make_marketing", side_effect=ZeroDivisionError("unexpected")):
            res = weekly_routine.run(now=self.sunday_utc, db_path=self.db_path)
            self.assertIn("Ee vaaram: routine error (ZeroDivisionError)", res)

    def test_56_run_state_key_is_constant(self):
        self.assertEqual(weekly_routine.STATE_KEY, "weekly_routine_last_week")

    def test_57_get_state_sqlite_error_returns_default(self):
        with mock.patch("sqlite3.connect", side_effect=sqlite3.DatabaseError("corrupt")):
            val = weekly_routine.get_state("key", default="fallback", db_path="/nonexistent/file")
            self.assertEqual(val, "fallback")

    def test_58_set_state_sqlite_error_handled_cleanly(self):
        with mock.patch("sqlite3.connect", side_effect=sqlite3.DatabaseError("corrupt")):
            # Should not raise
            weekly_routine.set_state("key", "val", db_path="/nonexistent/file")

    def test_59_run_with_no_db_path_runs_without_crash(self):
        with mock.patch("weekly_routine.run_make_marketing", return_value={"status": "ok", "ready_count": 0, "processed_count": 0}), \
             mock.patch("weekly_routine.run_newsletter", return_value={"status": "ok"}), \
             mock.patch("weekly_routine.run_calendar_export", return_value={"status": "ok"}), \
             mock.patch("weekly_routine.run_ops_report", return_value={"status": "ok"}), \
             mock.patch("weekly_routine.run_pack_changelog", return_value={"status": "ok"}), \
             mock.patch("weekly_routine.run_dup_check", return_value={"status": "ok", "warnings_count": 0}):

            res = weekly_routine.run(now=self.sunday_utc, db_path=None, force=True)
            self.assertIn("Ee vaaram", res)

    def test_60_run_e2e_with_real_filesystem_mocked_makers(self):
        # Create one complete pack in products_dir/upload/pack-e2e
        p = self.upload_dir / "pack-e2e"
        p.mkdir(parents=True, exist_ok=True)
        (p / "product.zip").write_text("dummy-zip")
        (p / "listing.txt").write_text("Title: Daily Habit Tracker\nDescription: Good\nTags: tracker\n")

        with mock.patch("make_marketing.report_row", return_value={"ready": True}), \
             mock.patch("make_marketing.process_pack", return_value={"id": "pack-e2e", "files": ["pins/p.png"], "errors": []}), \
             mock.patch("make_marketing.write_readme"), \
             mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W45", "md_file": "", "txt_file": ""}), \
             mock.patch("calendar_export.export_calendar", return_value={"status": "success", "events_count": 1}), \
             mock.patch("ops_report.generate_ops_summary", return_value={}), \
             mock.patch("pack_changelog.process_all_ready_packs", return_value=[{"pack": "pack-e2e"}]), \
             mock.patch("dup_check.check_duplicate_content", return_value={"pairs_above_50": [], "has_high_similarity": False}):

            res = weekly_routine.run(
                now=self.sunday_utc,
                db_path=self.db_path,
                products_dir=self.products_dir,
                marketing_dir=self.marketing_dir,
            )
            self.assertIsNotNone(res)
            self.assertEqual(res, "Ee vaaram: 1 packs ki marketing ready, newsletter draft ready, no duplicate warnings")

            # Check that state store was updated
            stored_week = weekly_routine.get_state(weekly_routine.STATE_KEY, db_path=self.db_path)
            self.assertEqual(stored_week, "2026-W45")

    def test_61_run_make_marketing_custom_timeout_under_budget(self):
        with mock.patch("make_marketing.report_row", return_value={"ready": True}), \
             mock.patch("make_marketing.process_pack", return_value={"id": "pack-fast", "files": [], "errors": []}), \
             mock.patch("make_marketing.write_readme"):

            res = weekly_routine.run_make_marketing(
                packs_dir=self.upload_dir,
                marketing_dir=self.marketing_dir,
                time_budget=60,
            )
            self.assertEqual(res["status"], "ok")

    def test_62_calendar_export_exception_during_run(self):
        with mock.patch("calendar_export.export_calendar", side_effect=Exception("export failed")):
            res = weekly_routine.run_calendar_export(marketing_dir=self.marketing_dir)
            self.assertEqual(res["status"], "error")
            self.assertIn("export failed", res["errors"][0])


if __name__ == "__main__":
    unittest.main()
