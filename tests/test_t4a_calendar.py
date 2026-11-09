"""tests/test_t4a_calendar.py — Tests for calendar_export.py (BINNY-T4A ROBOT).

Covers:
- RFC 5545 compliance (VCALENDAR, VEVENT, UID, PRODID, VERSION:2.0, METHOD:PUBLISH)
- Line folding strictly at 75 octets with CRLF + space continuation
- Escaping special characters: commas, semicolons, backslashes, newlines
- All-day DTSTART/DTEND format vs 19:00 IST time option
- SUMMARY formatting for pinterest, instagram, reel, carousel, previews, covers
- DESCRIPTION with caption, hashtags, asset, pack, and status
- Stable deterministic UID generation across runs
- Parsing back ICS and CSV for round-trip validation
- CSV format, header fields, quoting, and row count
- Atomic writing to disk (both .ics and .csv) without leaving partial files
- Empty plan handling
- Tenglish house style summary messages for owner CLI
- Rejection of currency symbols or money amounts in input and output
- CLI argument parsing and error handling
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import calendar_export
import post_planner


class TestT4ACalendarExport(unittest.TestCase):
    def setUp(self):
        # Clear all GITHUB_* and RUNNER_ENVIRONMENT per repo guidelines
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"
        }
        patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    # 1. Escape RFC 5545 text
    def test_01_escape_ics_text_empty_and_normal(self):
        self.assertEqual(calendar_export.escape_ics_text(""), "")
        self.assertEqual(calendar_export.escape_ics_text("Simple text"), "Simple text")

    def test_02_escape_ics_text_special_chars(self):
        raw = "Hello, world; test \\ backslash and\nnew line"
        escaped = calendar_export.escape_ics_text(raw)
        self.assertEqual(escaped, "Hello\\, world\\; test \\\\ backslash and\\nnew line")

    def test_03_escape_ics_text_crlf(self):
        raw = "Line 1\r\nLine 2\rLine 3\nLine 4"
        escaped = calendar_export.escape_ics_text(raw)
        self.assertEqual(escaped, "Line 1\\nLine 2\\nLine 3\\nLine 4")

    # 2. Line folding (75 octets)
    def test_04_fold_line_short_line(self):
        line = "SUMMARY:Short text"
        folded = calendar_export.fold_line(line, max_octets=75)
        self.assertEqual(folded, line + "\r\n")

    def test_05_fold_line_long_line_75_octets(self):
        line = "A" * 150
        folded = calendar_export.fold_line(line, max_octets=75)
        lines = folded.split("\r\n")
        # Removing last empty string after final \r\n
        if lines[-1] == "":
            lines.pop()
        self.assertEqual(len(lines), 3)
        self.assertEqual(len(lines[0].encode("utf-8")), 75)
        self.assertTrue(lines[1].startswith(" "))
        self.assertEqual(len(lines[1].encode("utf-8")), 75)
        self.assertTrue(lines[2].startswith(" "))
        self.assertEqual(len(lines[2].encode("utf-8")), 2)

    def test_06_fold_line_multibyte_utf8(self):
        # Telugu text or accented characters (3-byte UTF-8)
        line = "DESCRIPTION:" + "బాగుంది " * 20
        folded = calendar_export.fold_line(line, max_octets=75)
        raw_lines = folded.split("\r\n")
        if raw_lines[-1] == "":
            raw_lines.pop()
        for idx, subline in enumerate(raw_lines):
            sub_octets = len(subline.encode("utf-8"))
            self.assertLessEqual(sub_octets, 75)
            if idx > 0:
                self.assertTrue(subline.startswith(" "))

    # 3. Stable UID generation
    def test_07_make_stable_uid_deterministic(self):
        item = {
            "date": "2026-11-01",
            "channel": "pinterest",
            "pack_id": "daily-habits",
            "asset": "pins/pin_1.png",
            "day": 1,
        }
        uid1 = calendar_export.make_stable_uid(item, 0)
        uid2 = calendar_export.make_stable_uid(item, 0)
        self.assertEqual(uid1, uid2)
        self.assertTrue(uid1.startswith("post-2026-11-01-pinterest-"))
        self.assertTrue(uid1.endswith("@chilligavva.local"))

    def test_08_make_stable_uid_differs_for_different_items(self):
        item1 = {"date": "2026-11-01", "channel": "pinterest", "pack_id": "p1", "asset": "a1", "day": 1}
        item2 = {"date": "2026-11-01", "channel": "pinterest", "pack_id": "p2", "asset": "a1", "day": 1}
        uid1 = calendar_export.make_stable_uid(item1, 0)
        uid2 = calendar_export.make_stable_uid(item2, 0)
        self.assertNotEqual(uid1, uid2)

    # 4. Pack short name cleaning
    def test_09_clean_pack_short_name(self):
        self.assertEqual(calendar_export.clean_pack_short_name("daily-focus-planner"), "Daily Focus Planner")
        self.assertEqual(calendar_export.clean_pack_short_name("weekly_meal_prep"), "Weekly Meal Prep")
        self.assertEqual(calendar_export.clean_pack_short_name(""), "Pack")

    # 5. Summary formatting
    def test_10_build_summary_pinterest_pin(self):
        item = {"channel": "pinterest", "pack_id": "study-planner", "asset": "pins/pin_2_a.png"}
        s = calendar_export.build_summary(item)
        self.assertEqual(s, "Pinterest: pin 2 (Study Planner)")

    def test_11_build_summary_instagram_reel(self):
        item = {"channel": "reel", "pack_id": "fitness-log", "asset": "reel/frame-1.png"}
        s = calendar_export.build_summary(item)
        self.assertEqual(s, "Instagram Reel: reel (Fitness Log)")

    def test_12_build_summary_instagram_carousel(self):
        item = {"channel": "carousel", "pack_id": "budget-kit", "asset": "carousel/slide-1.png"}
        s = calendar_export.build_summary(item)
        self.assertEqual(s, "Instagram Carousel: carousel (Budget Kit)")

    def test_13_build_summary_cover_and_preview(self):
        item1 = {"channel": "pinterest", "pack_id": "recipe-card", "asset": "cover.png"}
        self.assertEqual(calendar_export.build_summary(item1), "Pinterest: cover (Recipe Card)")

        item2 = {"channel": "instagram", "pack_id": "recipe-card", "asset": "preview-3.png"}
        self.assertEqual(calendar_export.build_summary(item2), "Instagram: preview 3 (Recipe Card)")

    # 6. Description formatting
    def test_14_build_description(self):
        item = {
            "caption_short": "Printable study planner",
            "hashtags": ["study", "planner"],
            "asset": "pins/pin_1.png",
            "pack_id": "study-planner",
            "status": "todo",
        }
        desc = calendar_export.build_description(item)
        self.assertIn("Caption: Printable study planner", desc)
        self.assertIn("Hashtags: #study #planner", desc)
        self.assertIn("Asset: pins/pin_1.png", desc)
        self.assertIn("Pack: study-planner", desc)
        self.assertIn("Status: todo", desc)

    # 7. iCalendar RFC 5545 Structure
    def test_15_build_ics_content_all_day_mode(self):
        plan = [
            {
                "day": 1,
                "date": "2026-11-05",
                "channel": "pinterest",
                "pack_id": "planner-kit",
                "asset": "pins/pin_1.png",
                "caption_short": "Plan your week",
                "hashtags": ["planner"],
                "status": "todo",
            }
        ]
        ics = calendar_export.build_ics_content(plan, time_mode="all_day")
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertIn("VERSION:2.0", ics)
        self.assertIn("PRODID:-//Chilli Gavva//Robot Post Planner 1.0//EN", ics)
        self.assertIn("BEGIN:VEVENT", ics)
        self.assertIn("DTSTART;VALUE=DATE:20261105", ics)
        self.assertIn("DTEND;VALUE=DATE:20261106", ics)
        self.assertIn("SUMMARY:Pinterest: pin 1 (Planner Kit)", ics)
        self.assertIn("STATUS:TENTATIVE", ics)
        self.assertIn("END:VEVENT", ics)
        self.assertIn("END:VCALENDAR", ics)

    def test_16_build_ics_content_19_00_mode(self):
        plan = [
            {
                "day": 1,
                "date": "2026-11-05",
                "channel": "pinterest",
                "pack_id": "planner-kit",
                "asset": "pins/pin_1.png",
                "caption_short": "Plan your week",
                "hashtags": ["planner"],
                "status": "done",
            },
            {
                "day": 1,
                "date": "2026-11-05",
                "channel": "instagram",
                "pack_id": "planner-kit",
                "asset": "cover.png",
                "caption_short": "Plan your week",
                "hashtags": ["planner"],
                "status": "posted",
            },
        ]
        ics = calendar_export.build_ics_content(plan, time_mode="19:00")
        self.assertIn("DTSTART;TZID=Asia/Kolkata:20261105T190000", ics)
        self.assertIn("DTEND;TZID=Asia/Kolkata:20261105T191500", ics)
        self.assertIn("DTSTART;TZID=Asia/Kolkata:20261105T191500", ics)
        self.assertIn("DTEND;TZID=Asia/Kolkata:20261105T193000", ics)
        self.assertIn("STATUS:CONFIRMED", ics)

    # 8. Round-trip parse_ics validation
    def test_17_parse_ics_single_event(self):
        plan = [
            {
                "day": 1,
                "date": "2026-11-01",
                "channel": "pinterest",
                "pack_id": "daily-focus",
                "asset": "pins/pin_2.png",
                "caption_short": "Morning routines, tasks, and habits",
                "hashtags": ["focus", "daily"],
                "status": "todo",
            }
        ]
        ics = calendar_export.build_ics_content(plan, time_mode="all_day")
        events = calendar_export.parse_ics(ics)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["DTSTART"], "20261101")
        self.assertEqual(ev["DTEND"], "20261102")
        self.assertEqual(ev["SUMMARY"], "Pinterest: pin 2 (Daily Focus)")
        self.assertIn("Morning routines, tasks, and habits", ev["DESCRIPTION"])
        self.assertEqual(ev["STATUS"], "TENTATIVE")

    def test_18_parse_ics_unfolding_roundtrip(self):
        long_caption = "A " * 80 + "end of caption"
        plan = [
            {
                "day": 1,
                "date": "2026-11-01",
                "channel": "pinterest",
                "pack_id": "daily-focus",
                "asset": "pins/pin_1.png",
                "caption_short": long_caption,
                "hashtags": ["focus"],
                "status": "todo",
            }
        ]
        ics = calendar_export.build_ics_content(plan)
        events = calendar_export.parse_ics(ics)
        self.assertEqual(len(events), 1)
        self.assertIn("end of caption", events[0]["DESCRIPTION"])

    # 9. CSV generation and parsing
    def test_19_build_csv_content(self):
        plan = [
            {
                "day": 1,
                "date": "2026-11-01",
                "channel": "pinterest",
                "pack_id": "study-guide",
                "asset": "pins/pin_1.png",
                "caption_short": "Study tips and template",
                "hashtags": ["study", "tips"],
                "status": "todo",
            }
        ]
        csv_text = calendar_export.build_csv_content(plan)
        self.assertIn("date,channel,pack_id,asset,caption,hashtags,status,summary,uid", csv_text)
        self.assertIn("2026-11-01,pinterest,study-guide,pins/pin_1.png", csv_text)

    def test_20_parse_csv_roundtrip(self):
        plan = [
            {
                "day": 1,
                "date": "2026-11-01",
                "channel": "pinterest",
                "pack_id": "study-guide",
                "asset": "pins/pin_1.png",
                "caption_short": "Study tips, notes, & templates",
                "hashtags": ["study", "notes"],
                "status": "todo",
            }
        ]
        csv_text = calendar_export.build_csv_content(plan)
        rows = calendar_export.parse_csv(csv_text)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["date"], "2026-11-01")
        self.assertEqual(r["channel"], "pinterest")
        self.assertEqual(r["pack_id"], "study-guide")
        self.assertEqual(r["caption"], "Study tips, notes, & templates")
        self.assertEqual(r["hashtags"], "#study #notes")
        self.assertEqual(r["status"], "todo")
        self.assertEqual(r["summary"], "Pinterest: pin 1 (Study Guide)")

    # 10. Atomic writes
    def test_21_atomic_write_text_creates_file(self):
        target = self.root / "sub" / "output.txt"
        calendar_export._atomic_write_text(target, "Hello world")
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(encoding="utf-8"), "Hello world")

    def test_22_atomic_write_text_overwrites_cleanly(self):
        target = self.root / "sub" / "output.txt"
        calendar_export._atomic_write_text(target, "First version")
        calendar_export._atomic_write_text(target, "Second version")
        self.assertEqual(target.read_text(encoding="utf-8"), "Second version")

    # 11. Full export_calendar workflow
    def test_23_export_calendar_writes_both_files(self):
        plan = [
            {
                "day": 1,
                "date": "2026-11-01",
                "channel": "pinterest",
                "pack_id": "p1",
                "asset": "pin_1.png",
                "caption_short": "Caption 1",
                "hashtags": ["tag1"],
                "status": "todo",
            },
            {
                "day": 1,
                "date": "2026-11-01",
                "channel": "instagram",
                "pack_id": "p1",
                "asset": "cover.png",
                "caption_short": "Caption 2",
                "hashtags": ["tag2"],
                "status": "done",
            },
        ]
        ics_path = self.root / "marketing" / "post_plan.ics"
        csv_path = self.root / "marketing" / "post_plan.csv"
        res = calendar_export.export_calendar(
            plan=plan,
            ics_path=ics_path,
            csv_path=csv_path,
            time_mode="all_day",
        )
        self.assertTrue(ics_path.is_file())
        self.assertTrue(csv_path.is_file())
        self.assertEqual(res["count"], 2)
        self.assertIn("Post plan calendar export ready: 2 posts", res["tenglish_summary"])

    def test_24_export_calendar_loads_from_plan_path(self):
        plan_file = self.root / "post_plan.json"
        plan = [
            {
                "day": 1,
                "date": "2026-11-01",
                "channel": "pinterest",
                "pack_id": "p1",
                "asset": "cover.png",
                "caption_short": "Caption",
                "hashtags": [],
                "status": "todo",
            }
        ]
        plan_file.write_text(json.dumps(plan), encoding="utf-8")

        ics_path = self.root / "out.ics"
        csv_path = self.root / "out.csv"

        res = calendar_export.export_calendar(
            plan=None,
            plan_path=plan_file,
            ics_path=ics_path,
            csv_path=csv_path,
        )
        self.assertEqual(res["count"], 1)
        self.assertTrue(ics_path.is_file())
        self.assertTrue(csv_path.is_file())

    # 12. Empty plan handling
    def test_25_export_calendar_empty_plan(self):
        ics_path = self.root / "empty.ics"
        csv_path = self.root / "empty.csv"
        res = calendar_export.export_calendar(
            plan=[],
            ics_path=ics_path,
            csv_path=csv_path,
        )
        self.assertEqual(res["count"], 0)
        events = calendar_export.parse_ics(ics_path.read_text(encoding="utf-8"))
        rows = calendar_export.parse_csv(csv_path.read_text(encoding="utf-8"))
        self.assertEqual(len(events), 0)
        self.assertEqual(len(rows), 0)

    # 13. Tenglish summary format
    def test_26_tenglish_summary_words(self):
        res = calendar_export.export_calendar(
            plan=[],
            ics_path=self.root / "t1.ics",
            csv_path=self.root / "t1.csv",
        )
        summary = res["tenglish_summary"]
        self.assertIn("Post plan calendar export ready", summary)
        self.assertIn("save ayyayi", summary)

    # 14. Currency and money pattern rejection
    def test_27_rejection_of_currency_in_plan(self):
        plan_with_money = [
            {
                "day": 1,
                "date": "2026-11-01",
                "channel": "pinterest",
                "pack_id": "budget-planner",
                "asset": "pin_1.png",
                "caption_short": "Get it for $10 today",
                "hashtags": ["planner"],
                "status": "todo",
            }
        ]
        with self.assertRaises(ValueError):
            calendar_export.export_calendar(
                plan=plan_with_money,
                ics_path=self.root / "bad.ics",
                csv_path=self.root / "bad.csv",
            )

    def test_28_rejection_of_inr_currency_symbol(self):
        plan_with_rupee = [
            {
                "day": 1,
                "date": "2026-11-01",
                "channel": "pinterest",
                "pack_id": "pack-inr",
                "asset": "pin_1.png",
                "caption_short": "Price is ₹500 only",
                "hashtags": ["planner"],
                "status": "todo",
            }
        ]
        with self.assertRaises(ValueError):
            calendar_export.export_calendar(
                plan=plan_with_rupee,
                ics_path=self.root / "bad.ics",
                csv_path=self.root / "bad.csv",
            )

    # 15. CLI integration
    def test_29_cli_main_success(self):
        plan_file = self.root / "plan.json"
        plan = [
            {
                "day": 1,
                "date": "2026-11-01",
                "channel": "pinterest",
                "pack_id": "p1",
                "asset": "pins/pin_1.png",
                "caption_short": "Planner item",
                "hashtags": ["tags"],
                "status": "todo",
            }
        ]
        plan_file.write_text(json.dumps(plan), encoding="utf-8")
        ics_out = self.root / "cli.ics"
        csv_out = self.root / "cli.csv"

        argv = [
            "--plan-path", str(plan_file),
            "--ics-out", str(ics_out),
            "--csv-out", str(csv_out),
            "--time", "all_day",
        ]
        with mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = calendar_export.main(argv)
            self.assertEqual(code, 0)
            self.assertTrue(ics_out.is_file())
            self.assertTrue(csv_out.is_file())
            self.assertIn("Post plan calendar export ready", mock_out.getvalue())

    def test_30_cli_main_with_19_00_ist(self):
        plan_file = self.root / "plan.json"
        plan = [
            {
                "day": 1,
                "date": "2026-11-01",
                "channel": "instagram",
                "pack_id": "p1",
                "asset": "reel/frame-1.png",
                "caption_short": "Reel item",
                "hashtags": ["tags"],
                "status": "todo",
            }
        ]
        plan_file.write_text(json.dumps(plan), encoding="utf-8")
        ics_out = self.root / "cli_19.ics"
        csv_out = self.root / "cli_19.csv"

        argv = [
            "--plan-path", str(plan_file),
            "--ics-out", str(ics_out),
            "--csv-out", str(csv_out),
            "--time", "19:00",
        ]
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            code = calendar_export.main(argv)
            self.assertEqual(code, 0)
        content = ics_out.read_text(encoding="utf-8")
        self.assertIn("190000", content)
        self.assertIn("Asia/Kolkata", content)

    # 16. Error handling
    def test_31_cli_main_error_handling(self):
        argv = [
            "--plan-path", "/non/existent/path/for/plan.json",
            "--ics-out", "/dev/null/forbidden/out.ics",
        ]
        with mock.patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            code = calendar_export.main(argv)
            self.assertEqual(code, 1)
            self.assertIn("Error exporting calendar", mock_err.getvalue())

    def test_32_line_folding_exact_75_octets(self):
        # A line whose length is exactly 75 chars should not be folded
        line = "X" * 75
        folded = calendar_export.fold_line(line, 75)
        self.assertEqual(folded, line + "\r\n")

        # A line of 76 chars should be folded into 75 and 1 (+ continuation space = 2)
        line76 = "X" * 76
        folded76 = calendar_export.fold_line(line76, 75)
        self.assertEqual(folded76, "X" * 75 + "\r\n X\r\n")


if __name__ == "__main__":
    unittest.main()
