"""Tests for post_planner.py and owner_commands matchers (BINNY-KG ROBOT).

Comprehensive suite covering:
- Plan rotation (no product 2 days in a row)
- Per-day caps (max 3 pins + 1 Instagram post)
- Pack distribution (every pack >= 2 times in 30 days)
- Empty products directory behavior
- Caption word boundary truncation and cleaning
- Clean hashtags (lowercase, no spaces, no brands, no money, <= 8)
- No money patterns or currency in texts
- Atomic file saving & resilient JSON loading/rebuilding
- mark_posted updates
- week_summary formatting
- owner_commands new matchers (English & Tenglish)
- Existing matchers untouched
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import owner_commands
import post_planner


PNG_BYTES = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
             b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
             b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


class TestKGPostPlanner(unittest.TestCase):
    def setUp(self):
        # Clear all GITHUB_* and RUNNER_ENVIRONMENT per repo guidelines
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.products_dir = self.root / "exported_content" / "products" / "upload"
        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.plan_path = self.root / "exported_content" / "post_plan.json"

    def _create_pack(self, pack_id: str, title: str, tags: list[str], images: list[str]) -> Path:
        pack_folder = self.products_dir / pack_id
        pack_folder.mkdir(parents=True, exist_ok=True)
        listing_content = (
            f"Title: {title}\n\n"
            f"Description:\nPersonal planner kit for focus and daily habits.\n\n"
            f"Tags: {', '.join(tags)}\n\n"
            f"What you get:\nPDF file\n"
        )
        (pack_folder / "listing.txt").write_text(listing_content, encoding="utf-8")
        for img in images:
            img_path = pack_folder / img
            img_path.parent.mkdir(parents=True, exist_ok=True)
            img_path.write_bytes(PNG_BYTES)
        return pack_folder

    # 1. Empty products directory
    def test_01_empty_products_dir_returns_empty_plan(self):
        empty_dir = self.root / "empty_products"
        empty_dir.mkdir(parents=True, exist_ok=True)
        plan = post_planner.build_plan(empty_dir, "2026-11-01", days=30)
        self.assertEqual(plan, [])

    def test_02_empty_products_dir_cli_message(self):
        empty_dir = self.root / "empty_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)
        with mock.patch("sys.stdout") as mock_out:
            code = post_planner.main(["--build", "--products-dir", str(empty_dir)])
            self.assertEqual(code, 0)

    # 2. Plan length and per-day caps
    def test_03_per_day_caps_max_3_pins_1_instagram(self):
        self._create_pack("pack-1", "Daily Focus Planner", ["focus", "planner"], ["cover.png", "pin_1.png"])
        self._create_pack("pack-2", "Weekly Habit Tracker", ["habits", "routine"], ["cover.png", "pin_2.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=30)
        self.assertEqual(len(plan), 30 * 4)

        # Check each day
        for day in range(1, 31):
            day_posts = [p for p in plan if p["day"] == day]
            pins = [p for p in day_posts if p["channel"] == "pinterest"]
            igs = [p for p in day_posts if p["channel"] == "instagram"]
            self.assertLessEqual(len(pins), 3)
            self.assertLessEqual(len(igs), 1)
            self.assertEqual(len(pins), 3)
            self.assertEqual(len(igs), 1)

    def test_04_status_is_todo_initially(self):
        self._create_pack("pack-1", "Daily Focus Planner", ["focus"], ["cover.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=5)
        for post in plan:
            self.assertEqual(post["status"], "todo")

    # 3. Product rotation: not shown 2 days in a row
    def test_05_rotation_no_product_shown_two_days_in_a_row_two_packs(self):
        self._create_pack("pack-1", "Planner A", ["focus"], ["cover.png"])
        self._create_pack("pack-2", "Planner B", ["habits"], ["cover.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=30)

        for d in range(1, 30):
            day_packs = {p["pack_id"] for p in plan if p["day"] == d}
            next_day_packs = {p["pack_id"] for p in plan if p["day"] == d + 1}
            self.assertTrue(day_packs.isdisjoint(next_day_packs), f"Day {d} and {d+1} overlap: {day_packs & next_day_packs}")

    def test_06_rotation_no_product_shown_two_days_in_a_row_multiple_packs(self):
        for i in range(1, 6):
            self._create_pack(f"pack-{i}", f"Planner {i}", ["focus"], ["cover.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=30)

        for d in range(1, 30):
            day_packs = {p["pack_id"] for p in plan if p["day"] == d}
            next_day_packs = {p["pack_id"] for p in plan if p["day"] == d + 1}
            self.assertTrue(day_packs.isdisjoint(next_day_packs), f"Day {d} and {d+1} overlap: {day_packs & next_day_packs}")

    def test_07_single_pack_fallback(self):
        # With only 1 pack, rotation between distinct packs is impossible, but code should not crash
        self._create_pack("pack-single", "Single Planner", ["single"], ["cover.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=3)
        self.assertEqual(len(plan), 12)
        for p in plan:
            self.assertEqual(p["pack_id"], "pack-single")

    # 4. Distribution: every pack at least 2 times in 30 days
    def test_08_every_pack_spread_at_least_twice_in_30_days(self):
        for i in range(1, 11):
            self._create_pack(f"pack-{i}", f"Product {i}", ["tag"], ["cover.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=30)

        counts = {}
        for p in plan:
            counts[p["pack_id"]] = counts.get(p["pack_id"], 0) + 1

        for i in range(1, 11):
            pid = f"pack-{i}"
            self.assertGreaterEqual(counts.get(pid, 0), 2, f"{pid} was used fewer than 2 times")

    def test_09_every_pack_spread_with_many_packs(self):
        for i in range(1, 21):
            self._create_pack(f"pack-{i}", f"Product {i}", ["tag"], ["cover.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=30)

        counts = {}
        for p in plan:
            counts[p["pack_id"]] = counts.get(p["pack_id"], 0) + 1

        for i in range(1, 21):
            pid = f"pack-{i}"
            self.assertGreaterEqual(counts.get(pid, 0), 2, f"{pid} used only {counts.get(pid, 0)} times")

    # 5. Captions truncation and word boundary
    def test_10_caption_cut_at_word_boundary_under_100_chars(self):
        long_title = "This is a very long title designed to exceed the maximum character limit for testing post planner caption"
        self.assertGreater(len(long_title), 100)
        short = post_planner.clean_caption(long_title, 100)
        self.assertLessEqual(len(short), 100)
        # Check it didn't slice a word in half ("caption" was truncated)
        self.assertFalse(short.endswith("capti"))
        self.assertTrue(short.endswith("planner"))

    def test_11_caption_takes_first_line(self):
        multiline = "First line of the title\nSecond line with extra details"
        short = post_planner.clean_caption(multiline, 100)
        self.assertEqual(short, "First line of the title")

    def test_12_caption_strips_markdown_headers(self):
        title = "### Title: Minimalist Habit Tracker Kit"
        short = post_planner.clean_caption(title, 100)
        self.assertEqual(short, "Minimalist Habit Tracker Kit")

    # 6. Hashtags cleaning: lowercase, no spaces, no brands, no money, <= 8
    def test_13_hashtags_cleaned_format(self):
        raw_tags = ["Printable Planner", "DAILY ROUTINE", "Focus & Calm", "ADHD Tips"]
        cleaned = post_planner.clean_hashtags(raw_tags, 8)
        self.assertEqual(cleaned, ["printableplanner", "dailyroutine", "focuscalm", "adhdtips"])

    def test_14_hashtags_max_8(self):
        raw_tags = [f"tag{i}" for i in range(1, 20)]
        cleaned = post_planner.clean_hashtags(raw_tags, 8)
        self.assertEqual(len(cleaned), 8)

    def test_15_hashtags_filters_brand_names(self):
        raw_tags = ["etsy", "gumroad", "Topmate", "Notion template", "pinterest pins", "instagram", "goodnotes", "habit tracker"]
        cleaned = post_planner.clean_hashtags(raw_tags, 8)
        self.assertNotIn("etsy", cleaned)
        self.assertNotIn("gumroad", cleaned)
        self.assertNotIn("topmate", cleaned)
        self.assertNotIn("notiontemplate", cleaned)
        self.assertNotIn("pinterestpins", cleaned)
        self.assertNotIn("instagram", cleaned)
        self.assertNotIn("goodnotes", cleaned)
        self.assertIn("habittracker", cleaned)

    def test_16_hashtags_filters_money_and_currencies(self):
        raw_tags = ["$10 planner", "sale 50%", "inr 500", "free download", "cost tracker", "routine"]
        cleaned = post_planner.clean_hashtags(raw_tags, 8)
        self.assertNotIn("10planner", cleaned)
        self.assertNotIn("inr500", cleaned)
        self.assertNotIn("free", cleaned)
        self.assertNotIn("sale", cleaned)
        self.assertIn("routine", cleaned)

    # 7. No money patterns in any generated texts
    def test_17_no_money_patterns_in_plan_and_today_text(self):
        self._create_pack("pack-price", "Planner Kit with $15 USD Special Sale", ["budget", "$5 deal", "dollar savings"], ["cover.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=2)
        today_msg = post_planner.today_text(plan, "2026-11-01")

        self.assertNotIn("$15", today_msg)
        self.assertNotIn("$5", today_msg)
        self.assertNotIn("USD", today_msg)
        self.assertNotIn("deal", today_msg)

    # 8. Assets relative path and resolution
    def test_18_asset_is_relative_inside_pack(self):
        self._create_pack("pack-assets", "Asset Planner", ["plan"], ["cover.png", "pin_1.png", "pin_2.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=3)
        for post in plan:
            self.assertNotIn(str(self.temp_dir.name), post["asset"])
            self.assertIn(".png", post["asset"])
            self.assertTrue(post["asset"].startswith("pin_") or post["asset"].startswith("cover"))

    # 9. today_text format and constraints
    def test_19_today_text_format_and_length(self):
        self._create_pack("pack-1", "Focus Planner", ["focus"], ["cover.png", "pin_1.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=1)
        text = post_planner.today_text(plan, "2026-11-01")

        self.assertIn("Today post (3 pins, 1 Instagram):", text)
        self.assertIn("1) pack-1", text)
        self.assertIn("Reply: posted 1", text)
        self.assertLessEqual(len(text), 1200)

    def test_20_today_text_no_posts_message(self):
        plan = []
        text = post_planner.today_text(plan, "2026-11-01")
        self.assertIn("No posts scheduled", text)

    # 10. mark_posted
    def test_21_mark_posted_valid(self):
        self._create_pack("pack-1", "Focus Planner", ["focus"], ["cover.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=2)

        self.assertEqual(plan[0]["status"], "todo")
        ok = post_planner.mark_posted(plan, "2026-11-01", 1)
        self.assertTrue(ok)
        self.assertEqual(plan[0]["status"], "done")
        self.assertEqual(plan[1]["status"], "todo")

    def test_22_mark_posted_invalid_n_or_date(self):
        self._create_pack("pack-1", "Focus Planner", ["focus"], ["cover.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=2)

        self.assertFalse(post_planner.mark_posted(plan, "2026-11-01", 0))
        self.assertFalse(post_planner.mark_posted(plan, "2026-11-01", 99))
        self.assertFalse(post_planner.mark_posted(plan, "2099-01-01", 1))

    # 11. week_summary
    def test_23_week_summary_format(self):
        self._create_pack("pack-1", "Focus Planner", ["focus"], ["cover.png"])
        # Monday 2026-11-02
        plan = post_planner.build_plan(self.products_dir, "2026-11-02", days=7)
        # 7 days * 4 posts = 28 posts
        post_planner.mark_posted(plan, "2026-11-02", 1)
        post_planner.mark_posted(plan, "2026-11-02", 2)
        post_planner.mark_posted(plan, "2026-11-03", 1)

        summary = post_planner.week_summary(plan, "2026-11-04")
        self.assertEqual(summary, "This week: 3 of 28 posts done")

    def test_24_week_summary_empty(self):
        summary = post_planner.week_summary([])
        self.assertEqual(summary, "This week: 0 of 0 posts done")

    # 12. Atomic save and load
    def test_25_save_and_load_plan(self):
        self._create_pack("pack-1", "Focus Planner", ["focus"], ["cover.png"])
        plan = post_planner.build_plan(self.products_dir, "2026-11-01", days=2)
        ok = post_planner.save_plan(plan, self.plan_path)
        self.assertTrue(ok)
        self.assertTrue(self.plan_path.is_file())

        loaded = post_planner.load_plan(self.plan_path)
        self.assertEqual(len(loaded), len(plan))
        self.assertEqual(loaded[0]["pack_id"], plan[0]["pack_id"])

    def test_26_atomic_save_no_partial_file_on_error(self):
        with mock.patch("json.dump", side_effect=OSError("Disk write error")):
            ok = post_planner.save_plan([{"day": 1}], self.plan_path)
            self.assertFalse(ok)
            self.assertFalse(self.plan_path.exists())

    def test_27_bad_json_rebuild_or_recovery(self):
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_text("CORRUPTED JSON { [", encoding="utf-8")

        # Without products_dir, loads empty list safely without crashing
        loaded = post_planner.load_plan(self.plan_path)
        self.assertEqual(loaded, [])

        # With products_dir, rebuilds plan automatically
        self._create_pack("pack-rec", "Recovery Pack", ["rec"], ["cover.png"])
        rebuilt = post_planner.load_plan(self.plan_path, products_dir=self.products_dir)
        self.assertGreater(len(rebuilt), 0)

    # 13. CLI execution tests
    def test_28_cli_today_and_posted_and_week(self):
        self._create_pack("pack-cli", "CLI Pack", ["cli"], ["cover.png"])
        today_date = datetime.date.today().isoformat()

        # 1. Build
        code_build = post_planner.main([
            "--build",
            "--products-dir", str(self.products_dir),
            "--plan-path", str(self.plan_path),
            "--date", today_date,
        ])
        self.assertEqual(code_build, 0)
        self.assertTrue(self.plan_path.exists())

        # 2. Today
        with mock.patch("sys.stdout") as mock_out:
            code_today = post_planner.main([
                "--today",
                "--plan-path", str(self.plan_path),
                "--date", today_date,
            ])
            self.assertEqual(code_today, 0)

        # 3. Posted 1
        code_posted = post_planner.main([
            "--posted", "1",
            "--plan-path", str(self.plan_path),
            "--date", today_date,
        ])
        self.assertEqual(code_posted, 0)

        # Verify status is done in saved plan
        plan = post_planner.load_plan(self.plan_path)
        self.assertEqual(plan[0]["status"], "done")

        # 4. Week
        with mock.patch("sys.stdout") as mock_out:
            code_week = post_planner.main([
                "--week",
                "--plan-path", str(self.plan_path),
                "--date", today_date,
            ])
            self.assertEqual(code_week, 0)

    # 14. owner_commands matchers (post_today & post_done)
    def test_29_owner_commands_post_today_english(self):
        cases = [
            "today posts",
            "today post",
            "posts today",
            "/today",
            "what to post today",
        ]
        for c in cases:
            res = owner_commands.parse(c)
            self.assertEqual(res["intent"], "post_today", f"Failed on {c}")
            self.assertFalse(res["needs_confirm"])
            self.assertGreater(res["confidence"], 0.9)

    def test_30_owner_commands_post_today_tenglish(self):
        cases = [
            "ee roju posts",
            "eroju posts",
            "ee roju post chupinchu",
        ]
        for c in cases:
            res = owner_commands.parse(c)
            self.assertEqual(res["intent"], "post_today", f"Failed on {c}")
            self.assertFalse(res["needs_confirm"])

    def test_31_owner_commands_post_done_english(self):
        cases = [
            ("posted 1", 1),
            ("posted 2", 2),
            ("posted three", 3),
        ]
        for text, exp_n in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "post_done", f"Failed on {text}")
            self.assertEqual(res["args"].get("n"), exp_n)
            self.assertFalse(res["needs_confirm"])

    def test_32_owner_commands_post_done_tenglish(self):
        cases = [
            ("post chesa 1", 1),
            ("post chesa 2", 2),
            ("post chesanu okati", 1),
            ("post chesam 3", 3),
        ]
        for text, exp_n in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "post_done", f"Failed on {text}")
            self.assertEqual(res["args"].get("n"), exp_n)
            self.assertFalse(res["needs_confirm"])

    def test_33_existing_owner_commands_unchanged(self):
        # Sample of original commands to ensure no regression
        cases = [
            ("approve draft 42", "approve_draft"),
            ("reject draft 12 reason weak niche", "reject_draft"),
            ("list drafts", "list_drafts"),
            ("show status", "show_status"),
            ("pause all", "pause_all"),
            ("resume all", "resume_all"),
            ("list packs", "list_packs"),
            ("show pack 3", "show_pack"),
        ]
        for text, exp_intent in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], exp_intent)


if __name__ == "__main__":
    unittest.main()
