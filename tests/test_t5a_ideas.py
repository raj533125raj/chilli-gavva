"""tests/test_t5a_ideas.py — Unit tests for smart offline idea scoring.

Tests idea_scoring.py functionality:
- Score ranges 0-100 and determinism
- Demand proxy calculations (keyword bank presence, pack sharing, fallbacks)
- Novelty vs existing packs overlap penalty
- Season fit calculations (active season windows, evergreen utility, off-season)
- Simplicity format word rewards and complex word penalties
- Robustness against malformed/empty/missing input fields
- Tenglish house style reason output
- No money amounts or currency symbols
- No brand names or logos
- File parsing (list of ideas, wrapper objects, single idea)
- CLI execution and flags (--json, --out, --keywords, --products-dir)
- Atomic file writing
"""

from datetime import date
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import idea_scoring


class TestIdeaScoring(unittest.TestCase):
    def setUp(self):
        # Jules rules: clear GITHUB_* and RUNNER_ENVIRONMENT using mock.patch.dict
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp_dir = tempfile.mkdtemp(prefix="test_idea_scoring_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

    def test_01_tokenize_text_standard(self):
        tokens = idea_scoring.tokenize_text("Minimalist Habit Tracker Printable PDF for Busy Adults")
        self.assertIn("minimalist", tokens)
        self.assertIn("habit", tokens)
        self.assertIn("tracker", tokens)
        self.assertIn("printable", tokens)
        self.assertIn("pdf", tokens)
        self.assertNotIn("for", tokens)  # stop word

    def test_02_tokenize_text_empty_and_special_chars(self):
        self.assertEqual(idea_scoring.tokenize_text(""), [])
        self.assertEqual(idea_scoring.tokenize_text("   "), [])
        tokens = idea_scoring.tokenize_text("Daily! Study? #Planner 2026")
        self.assertIn("daily", tokens)
        self.assertIn("study", tokens)
        self.assertIn("planner", tokens)
        self.assertIn("2026", tokens)

    def test_03_load_keywords_bank_from_file(self):
        bank_file = Path(self.tmp_dir) / "keywords.json"
        bank_data = {
            "keywords": [
                {"keyword": "daily habit tracker", "score": 25, "pack_count": 3},
                {"keyword": "budget planner worksheet", "score": 20, "pack_count": 2},
            ]
        }
        bank_file.write_text(json.dumps(bank_data), encoding="utf-8")

        loaded = idea_scoring.load_keywords_bank(keywords_path=bank_file)
        self.assertEqual(len(loaded.get("keywords", [])), 2)
        self.assertEqual(loaded["keywords"][0]["keyword"], "daily habit tracker")

    def test_04_load_keywords_bank_nonexistent_fallback(self):
        non_existent = Path(self.tmp_dir) / "nonexistent.json"
        loaded = idea_scoring.load_keywords_bank(keywords_path=non_existent)
        self.assertIsInstance(loaded, dict)
        self.assertIn("keywords", loaded)

    def test_05_demand_score_with_high_matching_keywords(self):
        kw_bank = {
            "keywords": [
                {"keyword": "habit tracker printable", "score": 30, "pack_count": 4},
                {"keyword": "daily routine worksheet", "score": 25, "pack_count": 3},
            ]
        }
        idea = {
            "title": "Daily Routine Habit Tracker Printable",
            "tags": ["habit tracker printable", "daily routine worksheet"],
            "niche": "productivity",
        }
        score, meta = idea_scoring.calculate_demand_score(idea, kw_bank)
        self.assertGreaterEqual(score, 20)
        self.assertLessEqual(score, 30)
        self.assertGreater(len(meta["matched_keywords"]), 0)
        self.assertEqual(meta["max_pack_sharing"], 4)

    def test_06_demand_score_with_empty_bank_fallback(self):
        kw_bank = {"keywords": []}
        idea = {
            "title": "Simple Daily Tracker Printable",
            "tags": ["tracker", "printable"],
            "niche": "habits",
        }
        score, meta = idea_scoring.calculate_demand_score(idea, kw_bank)
        self.assertGreaterEqual(score, 10)
        self.assertLessEqual(score, 30)

    def test_07_demand_score_no_keywords_matched(self):
        kw_bank = {
            "keywords": [
                {"keyword": "astronomy star chart", "score": 20, "pack_count": 2},
            ]
        }
        idea = {
            "title": "Bonsai Tree Trimming Log",
            "tags": ["bonsai", "plants"],
            "niche": "gardening",
        }
        score, meta = idea_scoring.calculate_demand_score(idea, kw_bank)
        self.assertLessEqual(score, 12)

    def test_08_overlap_score_zero_existing_packs(self):
        idea = {"title": "Daily Habit Tracker", "tags": ["habit tracker"], "niche": "self care"}
        score, meta = idea_scoring.calculate_overlap_score(idea, [])
        self.assertEqual(score, 25)
        self.assertEqual(meta["max_overlap_ratio"], 0.0)
        self.assertIsNone(meta["closest_pack"])

    def test_09_overlap_score_high_overlap_penalty(self):
        existing_packs = [
            {
                "name": "daily-habit-tracker-pack",
                "title": "Daily Habit Tracker Printable Worksheet",
                "tags": ["daily habit tracker", "printable worksheet", "routine log"],
                "tokens": {"daily", "habit", "tracker", "printable", "worksheet", "routine", "log"},
            }
        ]
        idea = {
            "title": "Daily Habit Tracker Printable Worksheet",
            "tags": ["daily habit tracker", "printable worksheet", "routine log"],
            "niche": "productivity",
        }
        score, meta = idea_scoring.calculate_overlap_score(idea, existing_packs)
        self.assertLessEqual(score, 5)
        self.assertGreaterEqual(meta["max_overlap_ratio"], 0.7)
        self.assertEqual(meta["closest_pack"], "daily-habit-tracker-pack")

    def test_10_overlap_score_minor_safe_variation(self):
        existing_packs = [
            {
                "name": "fitness-planner-pack",
                "title": "Fitness Workout Log",
                "tags": ["fitness", "workout"],
                "tokens": {"fitness", "workout", "log", "gym"},
            }
        ]
        idea = {
            "title": "Student Exam Revision Timetable Printable",
            "tags": ["study", "revision", "timetable"],
            "niche": "education",
        }
        score, meta = idea_scoring.calculate_overlap_score(idea, existing_packs)
        self.assertEqual(score, 25)
        self.assertLess(meta["max_overlap_ratio"], 0.15)

    def test_11_load_existing_packs_from_folder(self):
        p_dir = Path(self.tmp_dir) / "products"
        pack_folder = p_dir / "upload" / "exam-prep-pack"
        pack_folder.mkdir(parents=True)
        listing_content = """Title: Exam Revision Timetable Planner
Tags: exam prep, study schedule, revision planner
Description: Useful revision timetable for students.
"""
        (pack_folder / "listing.txt").write_text(listing_content, encoding="utf-8")

        loaded = idea_scoring.load_existing_packs(products_dir=p_dir)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], "exam-prep-pack")
        self.assertIn("exam", loaded[0]["tokens"])
        self.assertIn("revision", loaded[0]["tokens"])

    def test_12_season_score_active_season_window(self):
        # Exam season in gift_guide runs Feb 1 to Apr 30
        feb_date = date(2026, 2, 15)
        idea = {
            "title": "Exam Revision Timetable & Study Session Tracker",
            "tags": ["exam revision", "study tracker", "finals prep"],
            "niche": "academics",
        }
        score, meta = idea_scoring.calculate_season_score(idea, target_date=feb_date)
        self.assertEqual(score, 25)
        self.assertEqual(meta["active_season"], "exam_season")

    def test_13_season_score_off_window_seasonal(self):
        # September 15 is not in exam season (Feb 1 - Apr 30)
        sep_date = date(2026, 9, 15)
        idea = {
            "title": "Exam Revision Timetable & Active Recall Guide",
            "tags": ["exam revision", "finals prep"],
            "niche": "academics",
        }
        score, meta = idea_scoring.calculate_season_score(idea, target_date=sep_date)
        self.assertEqual(score, 18)
        self.assertIsNone(meta["active_season"])
        self.assertIn("exam_season", meta["matching_seasons"])

    def test_14_season_score_evergreen_utility(self):
        idea = {
            "title": "Daily Habit Routine Checklist Printable",
            "tags": ["daily habit", "routine checklist"],
            "niche": "wellness",
        }
        score, meta = idea_scoring.calculate_season_score(idea, target_date=date(2026, 8, 15))
        self.assertEqual(score, 16)
        self.assertIn("Evergreen", meta["detail"])

    def test_15_season_score_neutral(self):
        idea = {
            "title": "Pottery Wheel Throwing Log",
            "tags": ["pottery", "ceramics"],
            "niche": "crafts",
        }
        score, meta = idea_scoring.calculate_season_score(idea, target_date=date(2026, 8, 15))
        self.assertEqual(score, 10)

    def test_16_simplicity_score_two_or_more_formats(self):
        idea = {
            "title": "Printable Habit Tracker Worksheet",
            "tags": ["printable", "worksheet", "tracker"],
            "niche": "productivity",
        }
        score, meta = idea_scoring.calculate_simplicity_score(idea)
        self.assertEqual(score, 20)
        self.assertGreaterEqual(len(meta["format_keywords"]), 2)

    def test_17_simplicity_score_single_format(self):
        idea = {
            "title": "Minimalist Monthly Planner",
            "tags": ["minimalist", "monthly"],
            "niche": "productivity",
        }
        score, meta = idea_scoring.calculate_simplicity_score(idea)
        self.assertEqual(score, 15)
        self.assertIn("planner", meta["format_keywords"])

    def test_18_simplicity_score_complex_format_penalty(self):
        idea = {
            "title": "Automated Excel Software Dashboard Tracker",
            "tags": ["excel", "software", "dashboard", "tracker"],
            "niche": "business",
        }
        score, meta = idea_scoring.calculate_simplicity_score(idea)
        self.assertLess(score, 15)
        self.assertIn("excel", meta["complex_keywords"])

    def test_19_full_score_idea_structure_and_keys(self):
        idea = {
            "title": "Minimalist Weekly Meal Planner Printable",
            "tags": ["meal planner", "printable", "weekly grocery list"],
            "niche": "home management",
        }
        result = idea_scoring.score_idea(idea)
        self.assertIn("title", result)
        self.assertIn("niche", result)
        self.assertIn("tags", result)
        self.assertIn("total_score", result)
        self.assertIn("components", result)
        self.assertIn("reasons", result)
        self.assertIn("tenglish_summary", result)
        self.assertGreaterEqual(result["total_score"], 0)
        self.assertLessEqual(result["total_score"], 100)

    def test_20_deterministic_scoring_same_inputs(self):
        idea = {
            "title": "Daily Focus Tracker Worksheet",
            "tags": ["daily focus", "tracker", "worksheet"],
            "niche": "productivity",
        }
        res1 = idea_scoring.score_idea(idea)
        res2 = idea_scoring.score_idea(idea)
        self.assertEqual(res1, res2)

    def test_21_tenglish_reasons_contain_house_style_words(self):
        idea = {
            "title": "Habit Tracker Printable",
            "tags": ["habit tracker", "printable"],
            "niche": "habits",
        }
        result = idea_scoring.score_idea(idea)
        reasons_combined = " ".join(result["reasons"])
        # Must contain typical Tenglish sentence words
        tenglish_markers = ["baavundi", "bagundi", "undi", "ledu", "cheyavachu", "cheyali", "manchidi"]
        self.assertTrue(any(marker in reasons_combined for marker in tenglish_markers))

    def test_22_no_currency_in_generated_reasons_or_output(self):
        idea = {
            "title": "Budget Planner Printable",
            "tags": ["budget planner", "financial sheet", "printable"],
            "niche": "finance",
        }
        result = idea_scoring.score_idea(idea)
        serialized = json.dumps(result)
        self.assertIsNone(idea_scoring.MONEY_PATTERN.search(serialized))

    def test_23_no_brand_names_in_reasons(self):
        idea = {
            "title": "Creative Habit Tracker Printable",
            "tags": ["habit tracker", "printable worksheet"],
            "niche": "creativity",
        }
        result = idea_scoring.score_idea(idea)
        for r in result["reasons"]:
            self.assertIsNone(idea_scoring.BRAND_WORDS.search(r))

    def test_24_score_ideas_list_sorting_descending(self):
        ideas = [
            {"title": "Weak Obscure Niche Guide", "tags": ["obscure"], "niche": "random"},
            {"title": "Printable Daily Habit Tracker Worksheet", "tags": ["habit tracker", "printable", "worksheet", "routine"], "niche": "productivity"},
        ]
        scored = idea_scoring.score_ideas_list(ideas)
        self.assertEqual(len(scored), 2)
        self.assertGreaterEqual(scored[0]["total_score"], scored[1]["total_score"])
        self.assertEqual(scored[0]["title"], "Printable Daily Habit Tracker Worksheet")

    def test_25_score_ideas_list_empty(self):
        self.assertEqual(idea_scoring.score_ideas_list([]), [])

    def test_26_atomic_write_json(self):
        out_file = Path(self.tmp_dir) / "output" / "scored_ideas.json"
        data = [{"title": "Test Idea", "score": 85}]
        idea_scoring.atomic_write_json(out_file, data)
        self.assertTrue(out_file.is_file())
        loaded = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertEqual(loaded[0]["title"], "Test Idea")

    def test_27_parse_ideas_file_list_format(self):
        inp_file = Path(self.tmp_dir) / "ideas.json"
        content = [
            {"title": "Idea 1", "tags": ["tag1"], "niche": "niche1"},
            {"title": "Idea 2", "tags": ["tag2"], "niche": "niche2"},
        ]
        inp_file.write_text(json.dumps(content), encoding="utf-8")
        parsed = idea_scoring.parse_ideas_file(inp_file)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["title"], "Idea 1")

    def test_28_parse_ideas_file_wrapped_dict_format(self):
        inp_file = Path(self.tmp_dir) / "ideas_wrapped.json"
        content = {
            "ideas": [
                {"title": "Idea A", "tags": ["tagA"], "niche": "nicheA"},
            ]
        }
        inp_file.write_text(json.dumps(content), encoding="utf-8")
        parsed = idea_scoring.parse_ideas_file(inp_file)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["title"], "Idea A")

    def test_29_parse_ideas_file_single_object_format(self):
        inp_file = Path(self.tmp_dir) / "single_idea.json"
        content = {"title": "Single Idea", "tags": ["tagS"], "niche": "nicheS"}
        inp_file.write_text(json.dumps(content), encoding="utf-8")
        parsed = idea_scoring.parse_ideas_file(inp_file)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["title"], "Single Idea")

    def test_30_parse_ideas_file_missing_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError):
            idea_scoring.parse_ideas_file(Path(self.tmp_dir) / "ghost.json")

    def test_31_cli_json_flag(self):
        inp_file = Path(self.tmp_dir) / "cli_ideas.json"
        inp_file.write_text(json.dumps([{"title": "Study Tracker", "tags": ["study", "tracker"], "niche": "education"}]), encoding="utf-8")

        with mock.patch("sys.stdout.write") as mock_write:
            ret = idea_scoring.main([str(inp_file), "--json"])
            self.assertEqual(ret, 0)
            written = "".join(call.args[0] for call in mock_write.call_args_list)
            data = json.loads(written)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["title"], "Study Tracker")

    def test_32_cli_out_flag(self):
        inp_file = Path(self.tmp_dir) / "cli_ideas.json"
        out_file = Path(self.tmp_dir) / "out_scored.json"
        inp_file.write_text(json.dumps([{"title": "Study Tracker", "tags": ["study", "tracker"], "niche": "education"}]), encoding="utf-8")

        ret = idea_scoring.main([str(inp_file), "--out", str(out_file)])
        self.assertEqual(ret, 0)
        self.assertTrue(out_file.is_file())
        data = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertEqual(data[0]["title"], "Study Tracker")

    def test_33_cli_file_error_returns_1(self):
        with mock.patch("sys.stderr.write"):
            ret = idea_scoring.main(["/invalid/path/to/missing_ideas.json"])
            self.assertEqual(ret, 1)

    def test_34_handle_malformed_idea_inputs(self):
        # Empty dict or None inputs should be handled gracefully without crashing
        res_empty = idea_scoring.score_idea({})
        self.assertIn("total_score", res_empty)

        res_none_tags = idea_scoring.score_idea({"title": "Test", "tags": None, "niche": None})
        self.assertIn("total_score", res_none_tags)

        res_str_tags = idea_scoring.score_idea({"title": "Test", "tags": "tag1, tag2", "niche": "testing"})
        self.assertEqual(len(res_str_tags["tags"]), 2)


if __name__ == "__main__":
    unittest.main()
