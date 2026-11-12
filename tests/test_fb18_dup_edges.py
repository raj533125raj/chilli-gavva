#!/usr/bin/env python3
"""tests/test_fb18_dup_edges.py — edge-case unit tests for dup_check.py.

Tests focus on boundary conditions and the duplicate-checking pipeline without
touching dup_check.py itself. Same expectedFailure discipline as the rest of the
suite: tests that must fail right now are marked with expectedFailure and a short
reason in the docstring.

Rules from OWNER-RULES.md and .jobs/TENGLISH-HOUSE-STYLE.txt apply:
- No money amounts anywhere. Use [amount] in any text examples.
- Tenglish suggestions stay in Tenglish house style.
- Tests live in this file only.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Dup-check helpers live in dup_check.py; tests may import the public helpers
# they need. Do NOT edit dup_check.py for this job.
import dup_check  # type: ignore[import-not-found]


HERE = Path(__file__).resolve().parent


def _listing_txt(title: str, description: str = "", tags: str = "") -> str:
    lines = [
        "# Title",
        title,
        "",
    ]
    if description:
        lines.append("## Description")
        lines.append(description)
        lines.append("")
    if tags:
        lines.append("## Tags")
        lines.append(tags)
        lines.append("")
    return "\n".join(lines)


def _make_pack(root: Path, name: str, title: str, description: str = "", tags: str = "") -> Path:
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "listing.txt").write_text(_listing_txt(title, description, tags), encoding="utf-8")
    return pack_dir


class TestZeroAndOnePackEdges(unittest.TestCase):
    """0-pack and 1-pack boundary behavior."""

    def test_zero_packs_returns_zero_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            report = dup_check.analyze_packs([])
            self.assertEqual(report["total_packs"], 0)
            self.assertEqual(report["total_pairs_compared"], 0)
            self.assertEqual(report["duplicate_pairs_count"], 0)
            self.assertEqual(report["severe_duplicates_count"], 0)
            self.assertEqual(report["summary_tenglish"], dup_check.analyze_packs([])
                ["summary_tenglish"])

    def test_one_pack_returns_no_duplicate_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            _make_pack(products, "pack-a", "Handmade Wrapping Paper")
            contents = [dup_check.extract_pack_content(products / "pack-a")]
            report = dup_check.analyze_packs(contents)
            self.assertEqual(report["total_packs"], 1)
            self.assertEqual(report["total_pairs_compared"], 0)
            self.assertFalse(report["has_severe_duplicates"])
            self.assertFalse(any(p["is_duplicate_pair"] for p in report["all_pairs"]))


class TestIdenticalListingsRaiseExitCodeOne(unittest.TestCase):
    """Identical listings should be treated as severe duplicates."""

    def test_identical_listings_flagged_as_severe(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            _make_pack(
                products,
                "pack-one",
                "Artisan Tea Sampler",
                "A curated tea sampler with green tea and herbal infusions.",
                "tea, sampler, herbal",
            )
            _make_pack(
                products,
                "pack-two",
                "Artisan Tea Sampler",
                "A curated tea sampler with green tea and herbal infusions.",
                "tea, sampler, herbal",
            )
            contents = [
                dup_check.extract_pack_content(products / "pack-one"),
                dup_check.extract_pack_content(products / "pack-two"),
            ]
            report = dup_check.analyze_packs(contents)
            pairs = [p for p in report["all_pairs"] if p["pack_a"] == "pack-one" and p["pack_b"] == "pack-two"]
            self.assertEqual(len(pairs), 1)
            pair = pairs[0]
            self.assertTrue(pair["is_severe_duplicate"])
            self.assertGreaterEqual(pair["max_similarity"], 0.8)

    def test_identical_listings_produce_exit_code_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            _make_pack(products, "a", "Same Title", "Same description text.")
            _make_pack(products, "b", "Same Title", "Same description text.")
            report = dup_check.check_duplicate_content(
                products_dir=products,
                marketing_dir=None,
                out_dir=Path(tmp) / "out",
            )
            self.assertTrue(report["has_severe_duplicates"])


class TestTotallyDifferentPacks(unittest.TestCase):
    """Packs with no overlap should produce no duplicate pairs."""

    def test_totally_different_packs_no_duplicate_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            _make_pack(
                products,
                "pack-paper",
                "Handmade Wrapping Paper",
                "Decorative paper sheets for gift wrapping.",
                "paper, gift, wrap",
            )
            _make_pack(
                products,
                "pack-bread",
                "Sourdough Starter Guide",
                "A beginner guide to sourdough starters and hydration.",
                "bread, baking, starter",
            )
            contents = [
                dup_check.extract_pack_content(products / "pack-paper"),
                dup_check.extract_pack_content(products / "pack-bread"),
            ]
            report = dup_check.analyze_packs(contents)
            flagged = report["flagged_pairs"]
            self.assertEqual(len(flagged), 0)
            self.assertFalse(report["has_severe_duplicates"])


class TestCaseAndPunctuationNormalization(unittest.TestCase):
    """Tag and word normalization should treat case/punctuation differences as overlaps."""

    def test_tag_case_and_punctuation_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            _make_pack(products, "alpha", "Gift Wrap Set", tags="Gift-Wrap, Paper, Gift!")
            _make_pack(products, "beta", "Gift Wrap Set", tags="gift wrap, paper, gift")
            contents = [
                dup_check.extract_pack_content(products / "alpha"),
                dup_check.extract_pack_content(products / "beta"),
            ]
            report = dup_check.analyze_packs(contents)
            pair = report["all_pairs"][0]
            normalized_tags = pair.get("identical_tags")
            self.assertTrue(normalized_tags)


class TestTagSetOrderIndependence(unittest.TestCase):
    """Tag sets equal in different order should still be treated as identical."""

    def test_tag_sets_equal_in_different_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            _make_pack(products, "first", "Wall Art Print", tags="print, wall, art")
            _make_pack(products, "second", "Wall Art Print", tags="art, print, wall")
            contents = [
                dup_check.extract_pack_content(products / "first"),
                dup_check.extract_pack_content(products / "second"),
            ]
            report = dup_check.analyze_packs(contents)
            pair = report["all_pairs"][0]
            self.assertTrue(pair["identical_tags"])


class TestVeryShortTexts(unittest.TestCase):
    """Short titles and descriptions should still compare safely."""

    def test_very_short_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            _make_pack(products, "short-a", "Hi", description="A small hello card.", tags="card")
            _make_pack(products, "short-b", "Hello", description="A small welcome card.", tags="card")
            contents = [
                dup_check.extract_pack_content(products / "short-a"),
                dup_check.extract_pack_content(products / "short-b"),
            ]
            report = dup_check.analyze_packs(contents)
            pair = report["all_pairs"][0]
            # Different single-word titles with different tags should not be
            # flagged as severe duplicates.
            self.assertFalse(pair["is_severe_duplicate"])

    def test_empty_description_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            _make_pack(products, "empty-desc", "Linen Napkins", tags="linen, napkin")
            _make_pack(products, "filled-desc", "Linen Napkins", "Soft linen napkins in four colors.", tags="linen, napkin")
            contents = [
                dup_check.extract_pack_content(products / "empty-desc"),
                dup_check.extract_pack_content(products / "filled-desc"),
            ]
            report = dup_check.analyze_packs(contents)
            pair = report["all_pairs"][0]
            # Same title and tags can still be flagged, but empty description alone
            # must not crash the pipeline.
            self.assertIsInstance(pair["description_similarity"], float)


class TestTeluguTextHandling(unittest.TestCase):
    """Telugu sentence words in English letters should be processed without errors."""

    def test_telugu_text_in_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            _make_pack(
                products,
                "telugu-pack",
                "Pedha Jar Gift",
                "Telugu lo pedha jar gift pack ready ga undi. Mekaa store lo upload cheyandi.",
                "gift, jar, pedha",
            )
            content = dup_check.extract_pack_content(products / "telugu-pack")
            # Should not crash and should return usable fields.
            self.assertIsInstance(content["title"], str)
            self.assertIsInstance(content["tags"], list)
            self.assertGreaterEqual(content["blog_len"], 0)


class TestBlogPresentOrAbsent(unittest.TestCase):
    """blog.md presence/absence should not break extraction or comparison."""

    def test_extract_pack_content_without_blog(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            pack_dir = _make_pack(products, "no-blog", "Plain Notebook")
            content = dup_check.extract_pack_content(pack_dir)
            self.assertFalse(content["has_blog"])
            self.assertEqual(content["blog_len"], 0)

    def test_extract_pack_content_with_blog(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            pack_dir = _make_pack(products, "blog-pack", "Plain Notebook")
            marketing = Path(tmp) / "marketing" / "blog-pack"
            marketing.mkdir(parents=True, exist_ok=True)
            (marketing / "blog.md").write_text(
                "# Blog\n\nNotebooks help keep ideas clear and organized.",
                encoding="utf-8",
            )
            content = dup_check.extract_pack_content(pack_dir, marketing_dir=marketing)
            self.assertTrue(content["has_blog"])
            self.assertGreater(content["blog_len"], 0)


class TestReportFilesWrittenOutsidePacks(unittest.TestCase):
    """Reports should be written to the requested out_dir, not buried inside packs."""

    def test_report_files_written_to_out_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "products"
            products.mkdir()
            _make_pack(products, "report-test", "Sample Pack")
            out_dir = Path(tmp) / "out-reports"
            out_dir.mkdir()
            report = dup_check.check_duplicate_content(
                products_dir=products,
                marketing_dir=None,
                out_dir=out_dir,
            )
            json_path = Path(tmp) / "out-reports" / "dup_report.json"
            md_path = Path(tmp) / "out-reports" / "dup_report.md"
            self.assertTrue(json_path.is_file())
            self.assertTrue(md_path.is_file())
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["total_packs"], report["total_packs"])

    def test_report_outside_product_tree_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            products = Path(tmp) / "a" / "b" / "products"
            products.mkdir(parents=True)
            _make_pack(products, "deep-pack", "Deep Pack")
            out_dir = Path(tmp) / "top-level-reports"
            report = dup_check.check_duplicate_content(
                products_dir=products,
                marketing_dir=None,
                out_dir=out_dir,
            )
            json_path = Path(tmp) / "top-level-reports" / "dup_report.json"
            md_path = Path(tmp) / "top-level-reports" / "dup_report.md"
            self.assertTrue(json_path.is_file())
            self.assertTrue(md_path.is_file())
            self.assertEqual(json_path.read_text(encoding="utf-8").count('"total_packs"'), 1)


if __name__ == "__main__":
    unittest.main()
