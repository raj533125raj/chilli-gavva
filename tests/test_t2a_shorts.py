"""tests/test_t2a_shorts.py — Comprehensive unit tests for shorts_script.py.

Testing constraints:
- Shorts script produced at exported_content/marketing/<pack>/reel/shorts.md
- Duration strictly between 15 and 30 seconds
- Timing specified per frame (matches frame count, e.g. 8 frames)
- On-screen text strictly <= 6 words per frame
- Voice-over lines in English AND Tenglish for every frame
- Exactly 3 title options, each strictly <= 60 characters
- Description strictly <= 500 characters
- Exactly 5 clean hashtags (no brands, no money, lowercase, alphanumeric)
- Facts strictly from listing (no invented features, no money amounts, no brands)
- No auto-posting or publishing commands
- Deterministic output and atomic writing
- Safe handling of missing packs, missing listing, or missing frames
- Command-line arguments (--pack, --all, --products-dir, --marketing-dir)
"""

import io
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock

import shorts_script


class TestT2AShorts(unittest.TestCase):
    def setUp(self):
        # Clear all GITHUB_* and RUNNER_ENVIRONMENT variables using mock.patch.dict
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp_dir = tempfile.mkdtemp(prefix="test_shorts_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

        self.root = Path(self.tmp_dir)
        self.products_dir = self.root / "products" / "upload"
        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir = self.root / "marketing"
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def _create_sample_pack(
        self,
        pack_name="minimal-habit-tracker",
        title="Minimal Habit Tracker Printable",
        desc="A simple clean daily tracker to build consistent habits over time.",
        tags="habits, tracker, printable, minimal, routine, goals, organization",
        benefits=None,
        create_reel_frames=True,
        frame_count=8,
    ):
        pack_dir = self.products_dir / pack_name
        pack_dir.mkdir(parents=True, exist_ok=True)

        if benefits is None:
            benefits = [
                "Daily habit checklist with streak counter",
                "Monthly habit grid for tracking progress",
                "Weekly reflection and habit notes area",
                "Customizable goal categories section",
                "Clean minimal layout for easy printing",
            ]

        what_text = "\n".join(f"- {b}" for b in benefits)
        listing_content = (
            f"Title: {title}\n\n"
            f"Tags: {tags}\n\n"
            f"Description:\n{desc}\n\n"
            f"What you get:\n{what_text}\n"
        )
        (pack_dir / "listing.txt").write_text(listing_content, encoding="utf-8")

        reel_dir = self.marketing_dir / pack_name / "reel"
        if create_reel_frames:
            reel_dir.mkdir(parents=True, exist_ok=True)
            for i in range(1, frame_count + 1):
                (reel_dir / f"frame-{i}.png").write_bytes(b"dummy_frame_content")

        return pack_dir

    # 1. Output destination exists in reel/shorts.md
    def test_01_output_file_created(self):
        pack = self._create_sample_pack()
        out = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        self.assertTrue(out.is_file())
        self.assertEqual(out.name, "shorts.md")
        self.assertEqual(out.parent.name, "reel")

    # 2. Output destination also mirrored in marketing/<pack>/shorts.md
    def test_02_pack_level_mirrored_file_created(self):
        pack = self._create_sample_pack()
        shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        mirrored = self.marketing_dir / pack.name / "shorts.md"
        self.assertTrue(mirrored.is_file())

    # 3. Overall duration is between 15 and 30 seconds
    def test_03_duration_in_15_to_30_seconds(self):
        pack = self._create_sample_pack()
        out = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        content = out.read_text(encoding="utf-8")
        match = re.search(r"\*\*Total Duration\*\*:\s*([0-9.]+)\s*seconds", content)
        self.assertIsNotNone(match, "Total duration not found in Overview")
        dur = float(match.group(1))
        self.assertGreaterEqual(dur, 15.0)
        self.assertLessEqual(dur, 30.0)

    # 4. Timing is specified for every frame
    def test_04_timing_specified_per_frame(self):
        pack = self._create_sample_pack(frame_count=8)
        out = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        content = out.read_text(encoding="utf-8")
        for i in range(1, 9):
            pattern = rf"\| Frame {i} \| [0-9.]+s – [0-9.]+s \|"
            self.assertRegex(content, pattern, f"Frame {i} timing missing or malformed")

    # 5. On-screen text is strictly <= 6 words per frame
    def test_05_on_screen_text_max_6_words(self):
        pack = self._create_sample_pack()
        out = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        content = out.read_text(encoding="utf-8")
        table_rows = [line for line in content.splitlines() if re.match(r"^\|\s*Frame\s+\d+", line)]
        self.assertEqual(len(table_rows), 8)
        for row in table_rows:
            cols = [c.strip() for c in row.split("|")[1:-1]]
            ost = cols[2]  # On-screen text
            ost_clean = ost.replace("**", "").strip()
            word_count = len(ost_clean.split())
            self.assertLessEqual(word_count, 6, f"OST '{ost_clean}' exceeds 6 words: {word_count}")

    # 6. Voice-over English lines present for each frame
    def test_06_voice_over_english_present(self):
        pack = self._create_sample_pack()
        out = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        content = out.read_text(encoding="utf-8")
        table_rows = [line for line in content.splitlines() if re.match(r"^\|\s*Frame\s+\d+", line)]
        self.assertEqual(len(table_rows), 8)
        for row in table_rows:
            cols = [c.strip() for c in row.split("|")[1:-1]]
            vo_en = cols[3]
            self.assertTrue(len(vo_en) > 5, f"Voice-over EN too short: {vo_en}")

    # 7. Voice-over Tenglish lines present for each frame
    def test_07_voice_over_tenglish_present(self):
        pack = self._create_sample_pack()
        out = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        content = out.read_text(encoding="utf-8")
        table_rows = [line for line in content.splitlines() if re.match(r"^\|\s*Frame\s+\d+", line)]
        self.assertEqual(len(table_rows), 8)
        for row in table_rows:
            cols = [c.strip() for c in row.split("|")[1:-1]]
            vo_te = cols[4]
            self.assertTrue(len(vo_te) > 5, f"Voice-over TE too short: {vo_te}")
            # Check Tenglish marker words (e.g., lo, cheyandi, undi, chuddam, mee, kosam)
            te_markers = ["lo", "cheyandi", "undi", "chuddam", "mee", "kosam", "ga", "intlo", "intlone"]
            has_te_marker = any(m in vo_te.lower().split() for m in te_markers)
            self.assertTrue(has_te_marker, f"Tenglish line '{vo_te}' missing characteristic Tenglish word")

    # 8. Exactly 3 title options
    def test_08_exactly_3_title_options(self):
        titles = shorts_script.generate_title_options("Daily Habit Tracker", "daily-habit-tracker")
        self.assertEqual(len(titles), 3)

    # 9. Each title option is strictly <= 60 characters
    def test_09_title_options_length_lte_60(self):
        long_title = "The Ultimate Super Comprehensive Productivity And Habit Planner Kit 2026 Edition"
        titles = shorts_script.generate_title_options(long_title, "ultimate-planner")
        self.assertEqual(len(titles), 3)
        for idx, t in enumerate(titles, 1):
            self.assertLessEqual(len(t), 60, f"Title option {idx} exceeds 60 characters: {len(t)}")

    # 10. Title options are unique
    def test_10_title_options_are_distinct(self):
        titles = shorts_script.generate_title_options("Daily Habit Tracker", "daily-habit-tracker")
        self.assertEqual(len(set(titles)), 3)

    # 11. Description length is strictly <= 500 characters
    def test_11_description_length_lte_500(self):
        long_desc = "Very long description " * 40
        bens = [f"Very long benefit item number {i} with lots of descriptive text" for i in range(10)]
        tags = ["#habit", "#tracker", "#goals", "#routine", "#focus"]
        desc = shorts_script.generate_description("Long Planner", long_desc, bens, tags)
        self.assertLessEqual(len(desc), 500)
        self.assertGreater(len(desc), 50)

    # 12. Exactly 5 clean hashtags generated
    def test_12_exactly_5_hashtags(self):
        raw_tags = ["habit", "planner", "routine", "goal", "organize", "focus", "journal"]
        tags = shorts_script.clean_hashtags(raw_tags, count=5)
        self.assertEqual(len(tags), 5)

    # 13. Hashtags are lowercase and alphanumeric with # prefix
    def test_13_hashtags_format(self):
        raw_tags = ["Habit Tracker", "Daily Routine!", "#GoalSet", "minimal_planner"]
        tags = shorts_script.clean_hashtags(raw_tags, count=5)
        self.assertEqual(len(tags), 5)
        for t in tags:
            self.assertTrue(t.startswith("#"))
            slug = t[1:]
            self.assertTrue(slug.isalnum(), f"Hashtag {t} is not alphanumeric")
            self.assertEqual(t, t.lower(), f"Hashtag {t} is not lowercase")

    # 14. Brand names are stripped from hashtags
    def test_14_hashtags_strip_brands(self):
        raw_tags = ["notion template", "goodnotes sticker", "canva design", "planner", "habits"]
        tags = shorts_script.clean_hashtags(raw_tags, count=5)
        for t in tags:
            for brand in shorts_script.BRAND_WORDS:
                self.assertNotIn(brand, t.lower())

    # 15. Currency and money words are stripped from hashtags
    def test_15_hashtags_strip_currency(self):
        raw_tags = ["$10 sale", "usd pricing", "habit", "tracker", "planner", "routine"]
        tags = shorts_script.clean_hashtags(raw_tags, count=5)
        for t in tags:
            for curr in ["usd", "inr", "price", "sale", "dollar"]:
                self.assertNotIn(curr, t.lower())

    # 16. Fallback hashtags used when raw tags are insufficient
    def test_16_hashtags_fallback_used(self):
        raw_tags = []
        tags = shorts_script.clean_hashtags(raw_tags, count=5)
        self.assertEqual(len(tags), 5)
        self.assertEqual(len(set(tags)), 5)

    # 17. No money amounts in rendered shorts.md
    def test_17_no_money_amounts_in_rendered_markdown(self):
        pack = self._create_sample_pack(
            desc="This planner costs $15 USD and saves money. Great discount.",
            tags="planner, $5, habits, daily, routine",
        )
        out = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        content = out.read_text(encoding="utf-8")
        self.assertFalse(shorts_script.contains_money(content), "Markdown contains forbidden currency pattern")

    # 18. No brand names in rendered titles or descriptions
    def test_18_no_brands_in_titles_or_description(self):
        pack = self._create_sample_pack(
            title="Notion Habit Tracker Canva",
            desc="Created for Goodnotes and Excel users.",
            tags="notion, goodnotes, excel, planner, habit",
        )
        out = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        content = out.read_text(encoding="utf-8")
        content_lower = content.lower()
        for b in ["notion", "goodnotes", "canva"]:
            # Neither title nor description nor hashtags should contain these brands
            self.assertNotIn(f" {b} ", content_lower)
            self.assertNotIn(f"#{b}", content_lower)

    # 19. Facts only from listing (benefits reflected in frames)
    def test_19_facts_only_from_listing_reflected(self):
        custom_benefits = [
            "Streak tracking chart for daily habits",
            "Morning review checklist",
            "Evening reflection score log",
        ]
        pack = self._create_sample_pack(benefits=custom_benefits)
        out = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        content = out.read_text(encoding="utf-8")
        self.assertIn("streak tracking chart", content.lower())
        self.assertIn("morning review checklist", content.lower())

    # 20. Atomic write does not leave temporary files
    def test_20_atomic_writes_no_temp_left(self):
        pack = self._create_sample_pack()
        shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        reel_dir = self.marketing_dir / pack.name / "reel"
        temp_files = list(reel_dir.glob(".*tmp*"))
        self.assertEqual(len(temp_files), 0)

    # 21. Deterministic output on successive runs
    def test_21_deterministic_output(self):
        pack = self._create_sample_pack()
        out1 = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        c1 = out1.read_text(encoding="utf-8")
        out2 = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        c2 = out2.read_text(encoding="utf-8")
        self.assertEqual(c1, c2)

    # 22. Pack folder in products/ is strictly untouched (read-only)
    def test_22_pack_folder_untouched(self):
        pack = self._create_sample_pack()
        files_before = sorted([f.name for f in pack.iterdir()])
        shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        files_after = sorted([f.name for f in pack.iterdir()])
        self.assertEqual(files_before, files_after)

    # 23. Works when listing.md is present instead of listing.txt
    def test_23_supports_listing_md(self):
        pack_dir = self.products_dir / "md-pack"
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "listing.md").write_text(
            "Title: Project Tracker\n\nTags: project, tasks, focus\n\nDescription:\nA clean project sheet.\n",
            encoding="utf-8",
        )
        reel_dir = self.marketing_dir / "md-pack" / "reel"
        reel_dir.mkdir(parents=True, exist_ok=True)
        (reel_dir / "frame-1.png").write_bytes(b"frame")

        out = shorts_script.generate_shorts_script_for_pack(pack_dir, self.marketing_dir)
        self.assertTrue(out.is_file())
        content = out.read_text(encoding="utf-8")
        self.assertIn("Project Tracker", content)

    # 24. Handles missing reel directory by using default frame count
    def test_24_handles_missing_reel_dir_gracefully(self):
        pack_dir = self.products_dir / "no-reel-yet"
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "listing.txt").write_text("Title: Weekly Goal Grid\n\nTags: goals, weekly\n", encoding="utf-8")
        # Do not create marketing reel dir prior to call
        out = shorts_script.generate_shorts_script_for_pack(pack_dir, self.marketing_dir)
        self.assertTrue(out.is_file())
        content = out.read_text(encoding="utf-8")
        self.assertIn("**Total Frames**: 8", content)

    # 25. Custom frame count (e.g. 6 frames -> duration within 15–30s)
    def test_25_custom_frame_count_duration(self):
        pack = self._create_sample_pack(pack_name="six-frame-pack", frame_count=6)
        out = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        content = out.read_text(encoding="utf-8")
        self.assertIn("**Total Frames**: 6", content)
        match = re.search(r"\*\*Total Duration\*\*:\s*([0-9.]+)\s*seconds", content)
        self.assertIsNotNone(match)
        dur = float(match.group(1))
        self.assertGreaterEqual(dur, 15.0)
        self.assertLessEqual(dur, 30.0)

    # 26. Parse listing utility extracts section data correctly
    def test_26_parse_listing_file(self):
        sample = (
            "Title: Student Exam Planner\n\n"
            "Tags: exam, student, study, revision\n\n"
            "Description:\nOrganize revision timetables easily.\n\n"
            "What you get:\n- Revision timetable\n- Study session log\n"
        )
        path = self.root / "sample_listing.txt"
        path.write_text(sample, encoding="utf-8")
        data = shorts_script.parse_listing_file(path)
        self.assertEqual(data["title"], "Student Exam Planner")
        self.assertEqual(data["tags"], ["exam", "student", "study", "revision"])
        self.assertEqual(data["benefits"], ["Revision timetable", "Study session log"])

    # 27. Parse listing returns empty dict on non-existent file
    def test_27_parse_listing_file_non_existent(self):
        missing = self.root / "does_not_exist.txt"
        data = shorts_script.parse_listing_file(missing)
        self.assertEqual(data["title"], "")
        self.assertEqual(data["tags"], [])
        self.assertEqual(data["benefits"], [])

    # 28. Discover packs with reels
    def test_28_discover_packs_with_reels(self):
        p1 = self._create_sample_pack(pack_name="pack-with-reel")
        p2 = self.products_dir / "pack-no-reel"
        p2.mkdir(parents=True, exist_ok=True)
        (p2 / "listing.txt").write_text("Title: No Reel Pack\n", encoding="utf-8")

        matched = shorts_script.discover_packs_with_reels(self.products_dir, self.marketing_dir)
        names = [p.name for p, _ in matched]
        self.assertIn("pack-with-reel", names)
        self.assertNotIn("pack-no-reel", names)

    # 29. CLI --pack generates script for single pack
    def test_29_cli_pack_flag(self):
        self._create_sample_pack(pack_name="cli-pack")
        ret = shorts_script.main([
            "--pack", "cli-pack",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(ret, 0)
        out = self.marketing_dir / "cli-pack" / "reel" / "shorts.md"
        self.assertTrue(out.is_file())

    # 30. CLI --all generates scripts for all matching packs
    def test_30_cli_all_flag(self):
        self._create_sample_pack(pack_name="pack-a")
        self._create_sample_pack(pack_name="pack-b")
        ret = shorts_script.main([
            "--all",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(ret, 0)
        out_a = self.marketing_dir / "pack-a" / "reel" / "shorts.md"
        out_b = self.marketing_dir / "pack-b" / "reel" / "shorts.md"
        self.assertTrue(out_a.is_file())
        self.assertTrue(out_b.is_file())

    # 31. Owner checklist checkboxes present and hand-tickable (- [ ])
    def test_31_owner_checklist_hand_tickable(self):
        pack = self._create_sample_pack()
        out = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        content = out.read_text(encoding="utf-8")
        self.assertIn("## Owner Checklist (Manual Upload)", content)
        checkboxes = [line for line in content.splitlines() if line.startswith("- [ ]")]
        self.assertGreaterEqual(len(checkboxes), 4)

    # 32. Never uploads, publishes, or executes network/browser actions
    def test_32_never_posts_or_publishes(self):
        pack = self._create_sample_pack()
        out = shorts_script.generate_shorts_script_for_pack(pack, self.marketing_dir)
        content = out.read_text(encoding="utf-8").lower()
        self.assertNotIn("auto-post", content)
        self.assertNotIn("publish now", content)
        self.assertNotIn("curl ", content)
        self.assertNotIn("api_key", content)


if __name__ == "__main__":
    unittest.main()
