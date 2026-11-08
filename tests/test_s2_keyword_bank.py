"""tests/test_s2_keyword_bank.py — Unit tests for keyword_bank.py.

At least 25 tests covering:
- Offline execution (no network)
- Built-in seed list loading and validation
- Stemming and plural/singular word expansions
- Long-tail combinations generation ("printable habit tracker pdf", etc.)
- Multi-pack keyword aggregation and pack sharing count
- Ranking by pack frequency and score
- Brand names filtering (no etsy, gumroad, notion, etc.)
- Money and currency filtering (no usd, inr, dollar, etc.)
- Atomic output writing to exported_content/marketing/keywords.json
- Idempotence and determinism
- Listing parsing (listing.txt and listing.md)
- Tag extraction and title ngrams
- Output JSON structure and key validation
- Empty packs directory handling
- CLI invocation
"""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import keyword_bank


class TestS2KeywordBank(unittest.TestCase):
    def setUp(self):
        # Jules rules: clear all GITHUB_* and RUNNER_ENVIRONMENT variables using mock.patch.dict
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp_dir = tempfile.mkdtemp(prefix="test_kw_bank_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

    def test_01_clean_term(self):
        self.assertEqual(keyword_bank.clean_term("  Habit-Tracker_Template!  "), "habit tracker template")
        self.assertEqual(keyword_bank.clean_term("Printable PDF..."), "printable pdf")

    def test_02_contains_brand_or_money_brands(self):
        self.assertTrue(keyword_bank.contains_brand_or_money("etsy digital download"))
        self.assertTrue(keyword_bank.contains_brand_or_money("notion template bundle"))
        self.assertTrue(keyword_bank.contains_brand_or_money("gumroad sales tracker"))
        self.assertTrue(keyword_bank.contains_brand_or_money("canva templates"))
        self.assertFalse(keyword_bank.contains_brand_or_money("habit tracker printable"))

    def test_03_contains_brand_or_money_currencies(self):
        self.assertTrue(keyword_bank.contains_brand_or_money("planner for 10 usd"))
        self.assertTrue(keyword_bank.contains_brand_or_money("habit tracker $5"))
        self.assertTrue(keyword_bank.contains_brand_or_money("routine checklist inr 200"))
        self.assertTrue(keyword_bank.contains_brand_or_money("minimalist planner rupees"))
        self.assertFalse(keyword_bank.contains_brand_or_money("daily routine checklist"))

    def test_04_get_stems_and_forms_plurals(self):
        forms = keyword_bank.get_stems_and_forms("planner")
        self.assertIn("planners", forms)

        forms_pl = keyword_bank.get_stems_and_forms("trackers")
        self.assertIn("tracker", forms_pl)

    def test_05_get_stems_and_forms_ies(self):
        forms = keyword_bank.get_stems_and_forms("activities")
        self.assertIn("activity", forms)

        forms_sg = keyword_bank.get_stems_and_forms("category")
        self.assertIn("categories", forms_sg)

    def test_06_get_stems_and_forms_participles(self):
        forms = keyword_bank.get_stems_and_forms("tracking")
        self.assertIn("track", forms)

    def test_07_generate_long_tail_combinations(self):
        combos = keyword_bank.generate_long_tail_combinations(["habit tracker"])
        self.assertIn("habit tracker", combos)
        self.assertIn("printable habit tracker", combos)
        self.assertIn("habit tracker printable", combos)
        self.assertIn("habit tracker template", combos)
        self.assertIn("printable habit tracker pdf", combos)

    def test_08_generate_long_tail_skips_brands_and_money(self):
        combos = keyword_bank.generate_long_tail_combinations(["etsy habit tracker", "tracker $10"])
        self.assertEqual(len(combos), 0)

    def test_09_extract_pack_terms_from_listing_txt(self):
        pack = Path(self.tmp_dir) / "pack_a"
        pack.mkdir()
        listing = """Title: Daily Study Planner Printable
Tags: study planner, daily study, exam tracker
Description: Clean study planner.
"""
        (pack / "listing.txt").write_text(listing, encoding="utf-8")
        terms = keyword_bank.extract_pack_terms(pack)
        self.assertIn("study planner", terms)
        self.assertIn("daily study", terms)
        self.assertIn("exam tracker", terms)
        self.assertIn("daily study planner printable", terms)

    def test_10_extract_pack_terms_from_listing_md(self):
        pack = Path(self.tmp_dir) / "pack_b"
        pack.mkdir()
        listing = """# Fitness Habit Tracker
## Tags
fitness tracker, workout log
"""
        (pack / "listing.md").write_text(listing, encoding="utf-8")
        terms = keyword_bank.extract_pack_terms(pack)
        self.assertIn("fitness tracker", terms)
        self.assertIn("workout log", terms)

    def test_11_extract_pack_terms_filters_brands(self):
        pack = Path(self.tmp_dir) / "pack_brand"
        pack.mkdir()
        listing = """Title: Notion Habit Tracker
Tags: notion template, habit tracker
"""
        (pack / "listing.txt").write_text(listing, encoding="utf-8")
        terms = keyword_bank.extract_pack_terms(pack)
        self.assertNotIn("notion template", terms)
        self.assertNotIn("notion habit tracker", terms)
        self.assertIn("habit tracker", terms)

    def test_12_extract_pack_terms_missing_file_returns_empty(self):
        pack = Path(self.tmp_dir) / "pack_empty"
        pack.mkdir()
        terms = keyword_bank.extract_pack_terms(pack)
        self.assertEqual(len(terms), 0)

    def test_13_build_keyword_bank_empty_products_uses_seeds(self):
        products_dir = Path(self.tmp_dir) / "products"
        products_dir.mkdir()
        bank = keyword_bank.build_keyword_bank(products_dir)
        self.assertEqual(bank["total_packs_analyzed"], 0)
        self.assertGreater(bank["total_keywords"], 20)
        self.assertIn("planner", [k["keyword"] for k in bank["keywords"]])

    def test_14_build_keyword_bank_counts_packs_sharing_terms(self):
        products = Path(self.tmp_dir) / "products" / "upload"
        products.mkdir(parents=True)

        pack1 = products / "pack1"
        pack1.mkdir()
        (pack1 / "listing.txt").write_text("Title: Habit Tracker 1\nTags: habit tracker, daily log\n", encoding="utf-8")

        pack2 = products / "pack2"
        pack2.mkdir()
        (pack2 / "listing.txt").write_text("Title: Habit Tracker 2\nTags: habit tracker, monthly goals\n", encoding="utf-8")

        bank = keyword_bank.build_keyword_bank(Path(self.tmp_dir) / "products")
        self.assertEqual(bank["total_packs_analyzed"], 2)

        # "habit tracker" should be shared by 2 packs
        ht_items = [k for k in bank["keywords"] if k["keyword"] == "habit tracker"]
        self.assertEqual(len(ht_items), 1)
        self.assertEqual(ht_items[0]["pack_count"], 2)
        self.assertEqual(set(ht_items[0]["matching_packs"]), {"pack1", "pack2"})

    def test_15_ranked_by_pack_count_and_score(self):
        products = Path(self.tmp_dir) / "products" / "upload"
        products.mkdir(parents=True)

        p1 = products / "p1"
        p1.mkdir()
        (p1 / "listing.txt").write_text("Title: Routine Tracker\nTags: routine tracker\n", encoding="utf-8")

        p2 = products / "p2"
        p2.mkdir()
        (p2 / "listing.txt").write_text("Title: Routine Tracker Two\nTags: routine tracker\n", encoding="utf-8")

        p3 = products / "p3"
        p3.mkdir()
        (p3 / "listing.txt").write_text("Title: Solo Habit Sheet\nTags: unique solo niche\n", encoding="utf-8")

        bank = keyword_bank.build_keyword_bank(Path(self.tmp_dir) / "products")
        keywords = bank["keywords"]
        # Top items should have higher pack counts or higher scores
        top_kws = keywords[:5]
        top_scores = [k["score"] for k in top_kws]
        self.assertEqual(top_scores, sorted(top_scores, reverse=True))

    def test_16_no_brand_names_in_keywords(self):
        products = Path(self.tmp_dir) / "products" / "upload"
        products.mkdir(parents=True)
        p = products / "p"
        p.mkdir()
        (p / "listing.txt").write_text("Title: Etsy Notion Canva Pack\nTags: etsy, canva, notion\n", encoding="utf-8")

        bank = keyword_bank.build_keyword_bank(Path(self.tmp_dir) / "products")
        for item in bank["keywords"]:
            kw = item["keyword"]
            words = set(kw.split())
            self.assertEqual(words.intersection(keyword_bank.BRAND_NAMES), set())

    def test_17_no_money_amounts_in_keywords(self):
        products = Path(self.tmp_dir) / "products" / "upload"
        products.mkdir(parents=True)
        p = products / "p"
        p.mkdir()
        (p / "listing.txt").write_text("Title: Planner for $5\nTags: 10 usd, cheap sale\n", encoding="utf-8")

        bank = keyword_bank.build_keyword_bank(Path(self.tmp_dir) / "products")
        for item in bank["keywords"]:
            kw = item["keyword"]
            self.assertFalse(keyword_bank._MONEY_PATTERN.search(kw))
            self.assertEqual(set(kw.split()).intersection(keyword_bank.CURRENCY_WORDS), set())

    def test_18_keyword_types_categorization(self):
        products = Path(self.tmp_dir) / "products"
        products.mkdir()
        bank = keyword_bank.build_keyword_bank(products)
        types = {k["type"] for k in bank["keywords"]}
        self.assertIn("seed", types)
        self.assertIn("phrase", types)
        self.assertIn("long_tail", types)

    def test_19_export_keyword_bank_atomic_write(self):
        products = Path(self.tmp_dir) / "products"
        products.mkdir()
        out_file = Path(self.tmp_dir) / "exported_content" / "marketing" / "keywords.json"

        saved_path = keyword_bank.export_keyword_bank(products, out_file)
        self.assertTrue(saved_path.is_file())
        self.assertEqual(saved_path, out_file)

        data = json.loads(saved_path.read_text(encoding="utf-8"))
        self.assertIn("total_keywords", data)
        self.assertIn("keywords", data)
        self.assertIsInstance(data["keywords"], list)

    def test_20_export_keyword_bank_idempotent(self):
        products = Path(self.tmp_dir) / "products"
        products.mkdir()
        out_file = Path(self.tmp_dir) / "keywords.json"

        keyword_bank.export_keyword_bank(products, out_file)
        content_1 = out_file.read_text(encoding="utf-8")

        keyword_bank.export_keyword_bank(products, out_file)
        content_2 = out_file.read_text(encoding="utf-8")

        self.assertEqual(content_1, content_2)

    def test_21_built_in_seed_words_non_empty(self):
        self.assertGreaterEqual(len(keyword_bank.SEED_WORDS), 20)
        self.assertIn("printable", keyword_bank.SEED_WORDS)
        self.assertIn("planner", keyword_bank.SEED_WORDS)
        self.assertIn("worksheet", keyword_bank.SEED_WORDS)

    def test_22_extra_seed_words_included(self):
        products = Path(self.tmp_dir) / "products"
        products.mkdir()
        bank = keyword_bank.build_keyword_bank(products, extra_seed_words=["calligraphy", "mindfulness"])
        all_kws = {k["keyword"] for k in bank["keywords"]}
        self.assertTrue(any("calligraphy" in k for k in all_kws))
        self.assertTrue(any("mindfulness" in k for k in all_kws))

    def test_23_keyword_bank_deduplication(self):
        products = Path(self.tmp_dir) / "products"
        products.mkdir()
        bank = keyword_bank.build_keyword_bank(products)
        kws = [k["keyword"] for k in bank["keywords"]]
        self.assertEqual(len(kws), len(set(kws)))

    def test_24_main_cli_execution(self):
        products = Path(self.tmp_dir) / "products"
        products.mkdir()
        out_file = Path(self.tmp_dir) / "marketing" / "keywords.json"

        with mock.patch("sys.argv", [
            "keyword_bank.py",
            "--products-dir", str(products),
            "--output", str(out_file),
            "--limit", "10",
        ]):
            keyword_bank.main()
            self.assertTrue(out_file.is_file())

    def test_25_no_network_requests(self):
        # Verify that urllib, requests, or socket are not called during execution
        with mock.patch("socket.socket") as mock_socket:
            products = Path(self.tmp_dir) / "products"
            products.mkdir()
            bank = keyword_bank.build_keyword_bank(products)
            self.assertFalse(mock_socket.called)
            self.assertGreater(bank["total_keywords"], 0)


if __name__ == "__main__":
    unittest.main()
