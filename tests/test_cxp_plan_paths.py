"""All remaining plan readers use the planner's saved list, independently of cwd."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import calendar_export
import doctor
import post_planner
import report_pdf
import state_guard
import weekly_routine


class SharedPlanPathTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.relative = Path("exported_content/products/post_plan.json")
        self.path = self.root / self.relative
        self.plan = [{"day": 1, "date": "2026-11-22", "channel": "pinterest",
                      "pack_id": "calm-week", "caption_short": "Plan a calm week",
                      "asset": "pin.png", "status": "todo"}]
        self.assertTrue(post_planner.save_plan(self.plan, self.path))
        self.legacy = self.root / "exported_content/post_plan.json"
        self.legacy.write_text("{half-written legacy file", encoding="utf-8")
        for module, name, value in (
            (post_planner, "PLAN_PATH", str(self.relative)),
            (report_pdf, "BASE_DIR", self.root),
            (weekly_routine, "REPO_ROOT", self.root),
            (doctor, "__file__", str(self.root / "doctor.py")),
            (state_guard, "ROOT", self.root),
        ):
            patcher = mock.patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_report_reads_current_plan_after_import(self):
        self.assertEqual(report_pdf._load_plan_with_warning(None), (self.plan, None))

    def test_weekly_exports_actual_saved_rows(self):
        output = self.root / "calendar"
        result = weekly_routine.run_calendar_export(marketing_dir=output)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["events_count"], len(self.plan))
        events = calendar_export.parse_ics((output / "post_plan.ics").read_text())
        self.assertEqual(len(events), len(self.plan))
        self.assertIn("calm-week", (output / "post_plan.csv").read_text())

    def test_doctor_accepts_the_real_planner_list(self):
        self.assertEqual(doctor.check_post_plan_valid_json()["status"], "ok")

    def test_guard_removes_only_damaged_current_plan_and_not_legacy(self):
        self.path.write_text('[{"day":', encoding="utf-8")
        sent = []
        self.assertEqual(state_guard.check_state_files(send=sent.append), ["post_plan.json"])
        self.assertFalse(self.path.exists())
        self.assertTrue(self.legacy.exists())
        self.assertEqual(len(sent), 1)
        self.assertEqual(state_guard.check_state_files(send=sent.append), [])

    def test_guard_keeps_complete_current_plan(self):
        self.assertEqual(state_guard.check_state_files(send=lambda _: True), [])
        self.assertEqual(json.loads(self.path.read_text()), self.plan)

    def test_absolute_configured_plan_works_for_all_readers(self):
        with mock.patch.object(post_planner, "PLAN_PATH", str(self.path)):
            self.assertEqual(report_pdf._load_plan(None), self.plan)
            self.assertEqual(doctor.check_post_plan_valid_json()["status"], "ok")
            result = weekly_routine.run_calendar_export(marketing_dir=self.root / "absolute-calendar")
            self.assertEqual(result["events_count"], len(self.plan))
            self.path.write_text("[", encoding="utf-8")
            self.assertEqual(state_guard.check_state_files(send=lambda _: True), ["post_plan.json"])

    def test_explicit_overrides_still_win(self):
        override = self.root / "override.json"
        override.write_text("[]", encoding="utf-8")
        self.assertEqual(report_pdf._load_plan(override), [])
        self.assertEqual(doctor.check_post_plan_valid_json(str(override))["status"], "ok")
        result = weekly_routine.run_calendar_export(override, self.root / "explicit-calendar")
        self.assertEqual(result["events_count"], 0)
        self.assertEqual(state_guard.check_state_files(files=[override], send=lambda _: True), [])

    def test_doctor_still_rejects_wrong_root_and_broken_json(self):
        for contents in ('{"schedule": []}', '[{"day":'):
            self.path.write_text(contents, encoding="utf-8")
            self.assertEqual(doctor.check_post_plan_valid_json()["status"], "fail")
