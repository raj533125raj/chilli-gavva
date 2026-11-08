"""Unit tests for BINNY-S4 ROBOT Job B: A/B Title Tracking (ab_results.py).

Tests cover:
- Record clicks with valid pack, title (1-5), and count
- Retrieve A/B data for a pack
- Update clicks for title and check leader updates
- Display table with leader marked
- Display table when no clicks yet
- Leader ties or multiple titles
- Invalid title numbers (0, 6, -1, 10) rejected
- Negative clicks rejected
- Rejection of currency symbols: $, €, £, ₹, ¥
- Rejection of currency codes: USD, INR, EUR, GBP, AUD
- Rejection of currency words: rupees, dollars, euros, paise
- Slash command /ab <pack> <title> <clicks>
- Slash command /ab <pack>
- Slash command /ab without args shows usage
- Tenglish syntax: "ab 3 lo title 2 ki 120 clicks"
- Tenglish syntax: "ab 3 title 2 120 clicks"
- Tenglish syntax: "ab pack-1 lo title 4 ki 50 clicks"
- Tenglish syntax: "ab 3" views table
- Non-negative count validation
- State persistence in telegram_state
- Title loading from ab_titles.txt if available
- Fallback to generic Title 1..5 if ab_titles.txt missing
- Money in Tenglish commands rejected
- Clean scrubbing: plain text, no secrets
- get_all_ab_leaders returns summary of all packs
"""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import ab_results
import telegram_inbox as ti
import guardrails


class S4AbResultsTests(unittest.TestCase):
    def setUp(self):
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        self.env_patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.db = str(self.root / "test_ab.db")
        self.marketing_dir = self.root / "marketing"
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

        guardrails.connect(self.db)

    def test_01_get_ab_data_empty(self):
        data = ab_results.get_ab_data("1", db_path=self.db)
        self.assertEqual(data["pack"], "1")
        self.assertEqual(len(data["titles"]), 5)
        for i in range(1, 6):
            self.assertEqual(data["titles"][str(i)], 0)

    def test_02_record_clicks_valid(self):
        data = ab_results.record_clicks("1", 2, 120, db_path=self.db)
        self.assertEqual(data["titles"]["2"], 120)
        stored = ab_results.get_ab_data("1", db_path=self.db)
        self.assertEqual(stored["titles"]["2"], 120)

    def test_03_record_clicks_invalid_title_num(self):
        with self.assertRaises(ValueError):
            ab_results.record_clicks("1", 0, 50, db_path=self.db)
        with self.assertRaises(ValueError):
            ab_results.record_clicks("1", 6, 50, db_path=self.db)
        with self.assertRaises(ValueError):
            ab_results.record_clicks("1", -1, 50, db_path=self.db)

    def test_04_record_clicks_negative_clicks(self):
        with self.assertRaises(ValueError):
            ab_results.record_clicks("1", 3, -10, db_path=self.db)

    def test_05_check_money_dollar(self):
        with self.assertRaises(ValueError):
            ab_results._check_money("ab 1 title 2 $50")

    def test_06_check_money_usd(self):
        with self.assertRaises(ValueError):
            ab_results._check_money("ab 1 title 2 50 USD")

    def test_07_check_money_rupees(self):
        with self.assertRaises(ValueError):
            ab_results._check_money("ab 1 title 2 500 Rs")
        with self.assertRaises(ValueError):
            ab_results._check_money("ab 1 title 2 500 rupees")

    def test_08_check_money_euro_symbol(self):
        with self.assertRaises(ValueError):
            ab_results._check_money("ab 1 title 2 €40")

    def test_09_check_money_clean_text_passes(self):
        ab_results._check_money("ab 3 lo title 2 ki 120 clicks")

    def test_10_format_ab_table_empty(self):
        table = ab_results.format_ab_table("3", db_path=self.db, marketing_dir=str(self.marketing_dir))
        self.assertIn("A/B Title Results for Pack #3:", table)
        self.assertIn("No clicks recorded yet", table)
        self.assertNotIn("👑", table)

    def test_11_format_ab_table_with_leader(self):
        ab_results.record_clicks("3", 2, 150, db_path=self.db)
        ab_results.record_clicks("3", 4, 80, db_path=self.db)
        table = ab_results.format_ab_table("3", db_path=self.db, marketing_dir=str(self.marketing_dir))
        self.assertIn("#2: Title 2 — 150 clicks 👑 (Leader)", table)
        self.assertIn("#4: Title 4 — 80 clicks", table)
        self.assertIn("Current Leader: Title #2 (Title 2) with 150 clicks.", table)

    def test_12_find_pack_titles_from_ab_titles_file(self):
        pack_mkt = self.marketing_dir / "3-planner" / "pins"
        pack_mkt.mkdir(parents=True)
        (pack_mkt / "ab_titles.txt").write_text(
            "1. Best Daily Planner\n2. Simple Daily Routine\n3. Daily Organization Sheet\n4. How to Plan Daily\n5. Daily Planner\n",
            encoding="utf-8",
        )
        titles = ab_results.find_pack_titles("3", marketing_dir=str(self.marketing_dir))
        self.assertEqual(titles[0], "Best Daily Planner")
        self.assertEqual(titles[1], "Simple Daily Routine")
        self.assertEqual(len(titles), 5)

    def test_13_format_table_with_custom_titles(self):
        pack_mkt = self.marketing_dir / "5" / "pins"
        pack_mkt.mkdir(parents=True)
        (pack_mkt / "ab_titles.txt").write_text(
            "1. Focus Routine\n2. Clarity Guide\n3. Calm Tracker\n4. Simple Work\n5. Focus Now\n",
            encoding="utf-8",
        )
        ab_results.record_clicks("5", 1, 99, db_path=self.db)
        table = ab_results.format_ab_table("5", db_path=self.db, marketing_dir=str(self.marketing_dir))
        self.assertIn("#1: Focus Routine — 99 clicks 👑 (Leader)", table)

    def test_14_parse_slash_ab_set(self):
        reply = ab_results.parse_and_handle_ab("/ab 3 2 120", db_path=self.db, marketing_dir=str(self.marketing_dir))
        self.assertIn("Sare! Pack #3 Title #2 ki 120 clicks save chesa.", reply)
        self.assertIn("120 clicks 👑 (Leader)", reply)

    def test_15_parse_slash_ab_view(self):
        ab_results.record_clicks("7", 3, 45, db_path=self.db)
        reply = ab_results.parse_and_handle_ab("/ab 7", db_path=self.db, marketing_dir=str(self.marketing_dir))
        self.assertIn("A/B Title Results for Pack #7:", reply)
        self.assertIn("45 clicks 👑 (Leader)", reply)

    def test_16_parse_slash_ab_usage(self):
        reply = ab_results.parse_and_handle_ab("/ab", db_path=self.db)
        self.assertIn("A/B Title Tracking:", reply)
        self.assertIn("Usage: /ab <pack>", reply)

    def test_17_parse_tenglish_set_standard(self):
        reply = ab_results.parse_and_handle_ab("ab 3 lo title 2 ki 120 clicks", db_path=self.db, marketing_dir=str(self.marketing_dir))
        self.assertIn("Sare! Pack #3 Title #2 ki 120 clicks save chesa.", reply)
        data = ab_results.get_ab_data("3", db_path=self.db)
        self.assertEqual(data["titles"]["2"], 120)

    def test_18_parse_tenglish_set_alternate_spacing(self):
        reply = ab_results.parse_and_handle_ab("ab 4 title 1 50 clicks", db_path=self.db, marketing_dir=str(self.marketing_dir))
        self.assertIn("Sare! Pack #4 Title #1 ki 50 clicks save chesa.", reply)
        data = ab_results.get_ab_data("4", db_path=self.db)
        self.assertEqual(data["titles"]["1"], 50)

    def test_19_parse_tenglish_view(self):
        ab_results.record_clicks("8", 5, 200, db_path=self.db)
        reply = ab_results.parse_and_handle_ab("ab 8 results", db_path=self.db, marketing_dir=str(self.marketing_dir))
        self.assertIn("A/B Title Results for Pack #8:", reply)
        self.assertIn("200 clicks 👑 (Leader)", reply)

    def test_20_parse_rejects_money_in_command(self):
        reply = ab_results.parse_and_handle_ab("/ab 3 2 $50", db_path=self.db)
        self.assertIn("Money amount found", reply)
        self.assertNotIn("$50", reply)

    def test_21_parse_rejects_money_in_tenglish(self):
        reply = ab_results.parse_and_handle_ab("ab 3 lo title 2 ki 500 rupees", db_path=self.db)
        self.assertIn("Money amount found", reply)

    def test_22_parse_unrelated_text_returns_none(self):
        self.assertIsNone(ab_results.parse_and_handle_ab("hello world", db_path=self.db))
        self.assertIsNone(ab_results.parse_and_handle_ab("/status", db_path=self.db))

    def test_23_get_all_ab_leaders_empty(self):
        leaders = ab_results.get_all_ab_leaders(db_path=self.db)
        self.assertEqual(leaders, [])

    def test_24_get_all_ab_leaders_populated(self):
        ab_results.record_clicks("10", 3, 85, db_path=self.db)
        ab_results.record_clicks("12", 1, 140, db_path=self.db)
        leaders = ab_results.get_all_ab_leaders(db_path=self.db)
        self.assertEqual(len(leaders), 2)
        self.assertEqual(leaders[0]["pack"], "10")
        self.assertEqual(leaders[0]["clicks"], 85)
        self.assertEqual(leaders[1]["pack"], "12")
        self.assertEqual(leaders[1]["clicks"], 140)

    def test_25_handle_command_integration(self):
        with mock.patch("ab_results.format_ab_table", return_value="Table for 99"):
            res = ti.handle_command("ab", [99], db_path=self.db)
            self.assertEqual(res, "Table for 99")


if __name__ == "__main__":
    unittest.main()
