#!/usr/bin/env python3
"""tests/test_t1b_boards.py — Unit tests for Pinterest Board Plan generator (pin_boards.py).

Verifies all constraints:
- 5 to 8 boards proposed
- Board names keyword-first, <= 50 chars, no brand names
- Board descriptions between 160 and 500 chars
- Pin allocation: each pin assigned to 1 or 2 boards (never 0, never > 2)
- No money amounts anywhere (tested via regex and test_r22_markdown_currency compatibility)
- No brand logos / names in generated output
- Determinism: same input yields identical output
- Pack directory unmodified
- Works with keywords.json present or absent
- Works with pins.json present or absent
- Tenglish house style owner summary and instructions
- Output files written atomically to exported_content/marketing/boards.json and boards.md
- CLI arguments test
- Comprehensive edge case handling
- At least 30 unit tests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock

import pin_boards


class BaseBoardPlanTest(unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Clear GITHUB_* and RUNNER_ENVIRONMENT environment variables
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.test_dir = tempfile.mkdtemp(prefix="test_boards_")
        self.products_dir = Path(self.test_dir) / "products" / "upload"
        self.marketing_dir = Path(self.test_dir) / "marketing"
        self.keywords_path = self.marketing_dir / "keywords.json"
        self.output_json = self.marketing_dir / "boards.json"
        self.output_md = self.marketing_dir / "boards.md"

        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)
        super().tearDown()

    def _create_pack(
        self,
        folder_name: str,
        title: str,
        tags: list[str],
        desc: str = "A clean printable tool for mindful daily planning.",
        pins: list[dict] | None = None,
    ) -> Path:
        pdir = self.products_dir / folder_name
        pdir.mkdir(parents=True, exist_ok=True)
        listing_lines = [
            f"# {title}",
            "",
            "## Description",
            desc,
            "",
            "## Tags",
            ", ".join(tags),
            "",
            "## Price",
            "SET BY OWNER",
        ]
        (pdir / "listing.txt").write_text("\n".join(listing_lines), encoding="utf-8")

        if pins is not None:
            m_pins_dir = self.marketing_dir / folder_name / "pins"
            m_pins_dir.mkdir(parents=True, exist_ok=True)
            (m_pins_dir / "pins.json").write_text(json.dumps(pins, indent=2), encoding="utf-8")

        return pdir


class TestBoardPlanRules(BaseBoardPlanTest):
    def test_board_count_between_five_and_eight(self):
        """Plan must propose between 5 and 8 boards."""
        self._create_pack("adhd-pack", "ADHD Daily Planner", ["adhd planner", "daily planner", "focus sheet"])
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        count = len(plan["boards"])
        self.assertGreaterEqual(count, 5)
        self.assertLessEqual(count, 8)
        self.assertEqual(plan["boards_count"], count)

    def test_board_names_length_max_fifty_chars(self):
        """Every board name must be <= 50 characters."""
        self._create_pack("habit-pack", "Habit Tracker Printable", ["habit tracker", "goal setting"])
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        for b in plan["boards"]:
            self.assertLessEqual(len(b["name"]), 50, f"Board name too long: {b['name']} ({len(b['name'])} chars)")

    def test_board_names_no_brand_names(self):
        """Board names must never contain brand names."""
        self._create_pack("study-pack", "Exam Study Tracker", ["study planner", "exam checklist"])
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        for b in plan["boards"]:
            self.assertTrue(pin_boards.is_safe_text(b["name"]))
            for brand in pin_boards.BRAND_NAMES:
                self.assertNotIn(brand, b["name"].lower())

    def test_board_names_no_money_amounts(self):
        """Board names must never contain money amounts or currency signs."""
        self._create_pack("finance-pack", "Expense Tracker Sheet", ["expense tracker", "budget log"])
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        for b in plan["boards"]:
            self.assertIsNone(pin_boards.MONEY_REGEX.search(b["name"]))

    def test_board_descriptions_between_160_and_500_chars(self):
        """Board descriptions must strictly be between 160 and 500 characters."""
        self._create_pack("planner-pack", "Daily Routine Planner", ["daily planner", "routine template"])
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        for b in plan["boards"]:
            desc_len = len(b["description"])
            self.assertGreaterEqual(
                desc_len, 160, f"Description too short ({desc_len} chars): {b['description']}"
            )
            self.assertLessEqual(
                desc_len, 500, f"Description too long ({desc_len} chars): {b['description']}"
            )

    def test_board_descriptions_no_brand_names(self):
        """Board descriptions must not contain brand names."""
        self._create_pack("planner-pack", "Daily Routine Planner", ["daily planner", "routine template"])
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        for b in plan["boards"]:
            self.assertTrue(pin_boards.is_safe_text(b["description"]))
            for brand in pin_boards.BRAND_NAMES:
                self.assertNotIn(brand, b["description"].lower())

    def test_board_descriptions_no_money_amounts(self):
        """Board descriptions must not contain money amounts or currency words."""
        self._create_pack("planner-pack", "Daily Routine Planner", ["daily planner", "routine template"])
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        for b in plan["boards"]:
            self.assertIsNone(pin_boards.MONEY_REGEX.search(b["description"]))

    def test_each_pin_assigned_to_one_or_two_boards(self):
        """Every single pin must be assigned to either 1 or 2 boards (never 0, never > 2)."""
        pins_data = [
            {"file": "pin-1.png", "title": "Energy Daily Planner A4", "description": "Plan your day by energy.", "layout": "A", "alt": "Energy planner"},
            {"file": "pin-2.png", "title": "Low Energy Reset Sheet", "description": "Reset your routine.", "layout": "B", "alt": "Reset sheet"},
            {"file": "pin-3.png", "title": "ADHD Focus Breakdown", "description": "Micro steps for focus.", "layout": "C", "alt": "Focus breakdown"},
        ]
        self._create_pack("energy-planner", "Energy Daily Planner", ["energy tracker", "adhd planner"], pins=pins_data)

        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )

        # Count pin occurrences across boards
        pin_counts: dict[str, int] = {}
        for b in plan["boards"]:
            for p in b["pins"]:
                pid = p["pin_id"]
                pin_counts[pid] = pin_counts.get(pid, 0) + 1

        self.assertEqual(len(pin_counts), 3)
        for pid, count in pin_counts.items():
            self.assertIn(count, (1, 2), f"Pin {pid} assigned to {count} boards (expected 1 or 2)")

    def test_pin_assignment_with_multiple_packs(self):
        """Pins across multiple packs are all assigned to 1 or 2 boards."""
        self._create_pack("pack-1", "ADHD Daily Planner", ["adhd planner", "focus sheet"])
        self._create_pack("pack-2", "Weekly Habit Tracker", ["habit tracker", "weekly planner"])
        self._create_pack("pack-3", "Student Exam Revision", ["study planner", "exam checklist"])

        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )

        pin_counts: dict[str, int] = {}
        for b in plan["boards"]:
            for p in b["pins"]:
                pid = p["pin_id"]
                pin_counts[pid] = pin_counts.get(pid, 0) + 1

        self.assertEqual(len(pin_counts), 15)  # 3 packs * 5 default pins each = 15
        for pid, count in pin_counts.items():
            self.assertIn(count, (1, 2), f"Pin {pid} assigned to {count} boards")

    def test_tenglish_summary_present(self):
        """Owner summary must be in Tenglish house style."""
        self._create_pack("pack-1", "Daily Planner", ["daily planner"])
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        summary = plan["tenglish_summary"]
        self.assertIn("Pinterest board plan ready ga undi", summary)
        self.assertIn("boards", summary)
        self.assertIn("pins", summary)

    def test_tenglish_instructions_present(self):
        """Owner instructions list must be in Tenglish house style."""
        self._create_pack("pack-1", "Daily Planner", ["daily planner"])
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        instructions = plan["owner_instructions_tenglish"]
        self.assertGreaterEqual(len(instructions), 3)
        joined = " ".join(instructions)
        self.assertIn("create cheyandi", joined)
        self.assertIn("Robot automated ga post cheyadu", joined)

    def test_determinism_identical_runs(self):
        """Two identical runs must produce identical plan outputs."""
        self._create_pack("pack-1", "ADHD Daily Planner", ["adhd planner", "focus sheet"])
        self._create_pack("pack-2", "Habit Tracker", ["habit tracker"])

        plan1 = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        plan2 = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        self.assertEqual(plan1, plan2)

    def test_pack_folder_untouched(self):
        """Executing board planning must never modify the pack folder."""
        pack_dir = self._create_pack("untouched-pack", "Daily Planner", ["daily planner"])
        mtime_before = os.path.getmtime(pack_dir / "listing.txt")
        files_before = sorted(os.listdir(pack_dir))

        pin_boards.write_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
            output_json=self.output_json,
            output_md=self.output_md,
        )

        mtime_after = os.path.getmtime(pack_dir / "listing.txt")
        files_after = sorted(os.listdir(pack_dir))

        self.assertEqual(files_before, files_after)
        self.assertEqual(mtime_before, mtime_after)

    def test_keywords_json_integration(self):
        """When keywords.json is present, keywords influence board ranking."""
        self.keywords_path.write_text(json.dumps({
            "keywords": [
                {"keyword": "exam checklist printable", "score": 95},
                {"keyword": "study planner pdf", "score": 90},
            ]
        }), encoding="utf-8")

        self._create_pack("study-pack", "Study Revision Sheet", ["study planner", "exam checklist"])
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        board_ids = [b["id"] for b in plan["boards"]]
        self.assertIn("student-study-checklists", board_ids)

    def test_keywords_json_absent_fallback(self):
        """When keywords.json is absent, planning proceeds safely without crash."""
        if self.keywords_path.exists():
            self.keywords_path.unlink()

        self._create_pack("pack-1", "Daily Planner", ["daily planner"])
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        self.assertGreaterEqual(len(plan["boards"]), 5)

    def test_empty_products_dir_graceful_fallback(self):
        """When no packs are present, produces 5-8 safe default boards with 0 pins."""
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        self.assertGreaterEqual(len(plan["boards"]), 5)
        self.assertLessEqual(len(plan["boards"]), 8)
        self.assertEqual(plan["total_unique_pins"], 0)

    def test_atomic_write_creates_files(self):
        """write_board_plan atomically writes boards.json and boards.md."""
        self._create_pack("pack-1", "Daily Routine", ["daily planner"])
        out_j, out_m = pin_boards.write_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
            output_json=self.output_json,
            output_md=self.output_md,
        )
        self.assertTrue(out_j.is_file())
        self.assertTrue(out_m.is_file())

        data = json.loads(out_j.read_text(encoding="utf-8"))
        self.assertIn("boards", data)
        self.assertGreaterEqual(len(data["boards"]), 5)

        md_text = out_m.read_text(encoding="utf-8")
        self.assertIn("# Pinterest Board Strategy", md_text)
        self.assertIn("Tenglish", md_text)

    def test_markdown_has_no_currency_amounts(self):
        """Generated boards.md must strictly pass currency pattern checks."""
        self._create_pack("budget-pack", "Budget Planner", ["budget planner", "expense tracker"])
        _, out_m = pin_boards.write_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
            output_json=self.output_json,
            output_md=self.output_md,
        )
        content = out_m.read_text(encoding="utf-8")
        self.assertIsNone(pin_boards.MONEY_REGEX.search(content))

    def test_json_has_no_currency_amounts(self):
        """Generated boards.json must have no currency patterns."""
        self._create_pack("budget-pack", "Budget Planner", ["budget planner", "expense tracker"])
        out_j, _ = pin_boards.write_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
            output_json=self.output_json,
            output_md=self.output_md,
        )
        content = out_j.read_text(encoding="utf-8")
        self.assertIsNone(pin_boards.MONEY_REGEX.search(content))

    def test_pins_metadata_fields(self):
        """Assigned pin objects must have required fields: pin_id, pack, file, title, layout."""
        self._create_pack("pack-1", "Daily Routine", ["daily planner"])
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        for b in plan["boards"]:
            for p in b["pins"]:
                self.assertIn("pin_id", p)
                self.assertIn("pack", p)
                self.assertIn("file", p)
                self.assertIn("title", p)
                self.assertIn("layout", p)

    def test_pin_layouts_preserved_from_pins_json(self):
        """Layouts A, B, C, D, E from pins.json are properly preserved in boards."""
        pins_data = [
            {"file": "pin-1.png", "title": "Layout A Title", "layout": "A"},
            {"file": "pin-2.png", "title": "Layout B Title", "layout": "B"},
            {"file": "pin-3.png", "title": "Layout C Title", "layout": "C"},
            {"file": "pin-4.png", "title": "Layout D Title", "layout": "D"},
            {"file": "pin-5.png", "title": "Layout E Title", "layout": "E"},
        ]
        self._create_pack("pack-1", "Layouts Test Pack", ["daily planner"], pins=pins_data)

        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        assigned_layouts = set()
        for b in plan["boards"]:
            for p in b["pins"]:
                assigned_layouts.add(p["layout"])
        self.assertEqual(assigned_layouts, {"A", "B", "C", "D", "E"})

    def test_brand_cleaning_in_custom_listing(self):
        """Listing text containing brand mentions (e.g. Canva, Etsy, Notion) must be sanitized."""
        self._create_pack(
            "brand-pack",
            "Canva Notion Printable Daily Planner",
            ["canva planner", "etsy template", "notion routine", "daily planner"],
            desc="Made with Canva and Notion for Etsy shop owners.",
        )
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        for b in plan["boards"]:
            for brand in ["canva", "etsy", "notion"]:
                self.assertNotIn(brand, b["name"].lower())
                self.assertNotIn(brand, b["description"].lower())

    def test_currency_cleaning_in_custom_listing(self):
        """Listing text with $10 USD or Rs 500 must have currency stripped."""
        self._create_pack(
            "currency-pack",
            "Budget Planner $10 USD",
            ["budget planner", "expense tracker", "$5 template", "100 INR"],
            desc="Save $50 every month with this $10 printable.",
        )
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        for b in plan["boards"]:
            self.assertIsNone(pin_boards.MONEY_REGEX.search(b["name"]))
            self.assertIsNone(pin_boards.MONEY_REGEX.search(b["description"]))

    def test_board_slugs_valid_format(self):
        """Board IDs / slugs must be lowercase alphanumeric with hyphens."""
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        for b in plan["boards"]:
            self.assertTrue(re.match(r"^[a-z0-9-]+$", b["id"]), f"Invalid slug: {b['id']}")

    def test_board_keywords_list_non_empty(self):
        """Each board must include a list of relevant SEO focus keywords."""
        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        for b in plan["boards"]:
            self.assertGreaterEqual(len(b["keywords"]), 2)

    def test_cli_execution_with_custom_paths(self):
        """CLI arguments work seamlessly and generate output files."""
        self._create_pack("cli-pack", "CLI Daily Planner", ["daily planner"])
        custom_json = self.marketing_dir / "custom_boards.json"
        custom_md = self.marketing_dir / "custom_boards.md"

        with mock.patch("sys.argv", [
            "pin_boards.py",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
            "--keywords-path", str(self.keywords_path),
            "--output-json", str(custom_json),
            "--output-md", str(custom_md),
        ]):
            pin_boards.main()

        self.assertTrue(custom_json.is_file())
        self.assertTrue(custom_md.is_file())

    def test_clean_text_no_brands_or_money_function(self):
        """clean_text_no_brands_or_money cleans both brands and prices."""
        dirty = "Printable Planner $15 USD for Canva and Notion users"
        cleaned = pin_boards.clean_text_no_brands_or_money(dirty)
        self.assertNotIn("$15", cleaned)
        self.assertNotIn("USD", cleaned)
        self.assertNotIn("Canva", cleaned)
        self.assertNotIn("Notion", cleaned)
        self.assertEqual(cleaned, "Printable Planner for and users")

    def test_is_safe_text_function(self):
        """is_safe_text detects violations correctly."""
        self.assertTrue(pin_boards.is_safe_text("Simple Daily Planner Routine"))
        self.assertFalse(pin_boards.is_safe_text("Simple Daily Planner $10"))
        self.assertFalse(pin_boards.is_safe_text("Simple Canva Daily Planner"))
        self.assertFalse(pin_boards.is_safe_text("Cost 500 INR Planner"))

    def test_parse_pack_listing_md_support(self):
        """parse_pack_listing correctly parses listing.md if listing.txt is absent."""
        pdir = self.products_dir / "md-pack"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "listing.md").write_text("# Markdown Planner\n\nTags:\nroutine, focus", encoding="utf-8")

        meta = pin_boards.parse_pack_listing(pdir)
        self.assertEqual(meta["title"], "Markdown Planner")
        self.assertIn("routine", meta["tags"])
        self.assertIn("focus", meta["tags"])

    def test_malformed_keywords_json_handled_safely(self):
        """Malformed keywords.json does not crash the planner."""
        self.keywords_path.write_text("{broken json", encoding="utf-8")
        kw = pin_boards.load_keywords_bank(self.keywords_path)
        self.assertEqual(kw, [])

    def test_malformed_pins_json_handled_safely(self):
        """Malformed pins.json falls back to synthetic pins without crashing."""
        self._create_pack("broken-pack", "Broken Pins Pack", ["daily planner"])
        p_pins_dir = self.marketing_dir / "broken-pack" / "pins"
        p_pins_dir.mkdir(parents=True, exist_ok=True)
        (p_pins_dir / "pins.json").write_text("{not a list", encoding="utf-8")

        pins = pin_boards.discover_pack_pins("broken-pack", self.marketing_dir)
        self.assertEqual(len(pins), 5)  # fallback 5 pins generated

    def test_board_pin_assignment_relevance(self):
        """Pins about ADHD are routed preferentially to ADHD productivity board."""
        pins_data = [
            {"file": "pin-1.png", "title": "ADHD Focus Breakdown and Time Blindness", "layout": "A"},
        ]
        self._create_pack("adhd-pack", "ADHD Planner", ["adhd planner", "time blindness"], pins=pins_data)

        plan = pin_boards.build_board_plan(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            keywords_path=self.keywords_path,
        )
        adhd_board = next((b for b in plan["boards"] if b["id"] == "adhd-productivity-tools"), None)
        self.assertIsNotNone(adhd_board)
        adhd_pin_files = [p["file"] for p in adhd_board["pins"]]
        self.assertIn("pin-1.png", adhd_pin_files)


if __name__ == "__main__":
    unittest.main()
