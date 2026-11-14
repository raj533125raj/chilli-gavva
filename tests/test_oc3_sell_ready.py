"""Regression tests for sell_ready fixes from the OC3 review."""

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import sell_ready


class TestFindMarketingDirFallback(unittest.TestCase):
    def test_default_marketing_dir_does_not_depend_on_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            pack = repo / "exported_content" / "marketing" / "1-focus-planner"
            pack.mkdir(parents=True)
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            old_cwd = os.getcwd()
            os.chdir(elsewhere)
            self.addCleanup(os.chdir, old_cwd)
            with mock.patch.object(sell_ready, "__file__", str(repo / "sell_ready.py")):
                found = sell_ready.find_marketing_dir(root / "products" / "1-focus-planner")
            self.assertEqual(Path(found).resolve(), pack.resolve())


class TestSellableStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pack = self.root / "products" / "1-focus-planner"
        self.pack.mkdir(parents=True)

    @mock.patch("sell_ready.inspect_design_theme")
    @mock.patch("sell_ready.inspect_preview_images")
    @mock.patch("sell_ready.inspect_store_kits")
    @mock.patch("sell_ready.inspect_marketing")
    @mock.patch("sell_ready.inspect_listing_lint")
    @mock.patch("sell_ready.inspect_deep_qa")
    @mock.patch("sell_ready.inspect_truth_check")
    @mock.patch("sell_ready.inspect_license")
    @mock.patch("sell_ready.inspect_pack_files")
    def test_soft_warnings_do_not_block_sellable(self, files, lic, truth, qa, lint, mkt, kits, previews, theme):
        files.return_value = {"score": 100, "hard_fail": False, "issues": []}
        lic.return_value = {"score": 100, "issues": []}
        truth.return_value = {"score": 100, "verdict": "ready", "hard_fail": False, "issues": []}
        qa.return_value = {"score": 100, "verdict": "ready", "hard_fail": False, "issues": []}
        lint.return_value = {"score": 100, "lint_ok": True, "hard_fail": False, "issues": []}
        mkt.return_value = {"score": 83, "found_count": 5, "issues": ["Missing blog.md post"]}
        kits.return_value = {"score": 100, "has_kits": True, "issues": []}
        previews.return_value = {"score": 100, "image_count": 2, "issues": []}
        theme.return_value = {"score": 100, "has_theme": True, "theme": "Calm Sage", "issues": []}

        result = sell_ready.evaluate_pack(self.pack)
        self.assertEqual(result["hard_fails"], [])
        self.assertTrue(result["fixes"])
        self.assertEqual(result["status"], sell_ready.STATUS_SELLABLE)


class TestDesignThemeScore(unittest.TestCase):
    def test_missing_theme_scores_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "pack"
            pack.mkdir()
            result = sell_ready.inspect_design_theme(pack)
        self.assertFalse(result["has_theme"])
        self.assertEqual(result["score"], 0)


class TestOc3MediumFixes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_fuzzy_slug_marketing_match(self):
        base = self.root / "marketing"
        child = base / "ADHD Planner"
        child.mkdir(parents=True)
        pack = self.root / "adhd-planner"
        pack.mkdir()
        self.assertEqual(sell_ready.find_marketing_dir(pack, base), child)

    def test_missing_previews_is_a_hard_fail(self):
        pack = self.root / "products" / "1-focus-planner"
        pack.mkdir(parents=True)
        (pack / "listing.txt").write_text("Title: Focus Planner\n", encoding="utf-8")
        (pack / "checklist.txt").write_text("Checklist\n", encoding="utf-8")
        with zipfile.ZipFile(pack / "product.zip", "w") as zf:
            zf.writestr("planner.pdf", b"%PDF-1.4\n")

        previews = sell_ready.inspect_preview_images(pack)
        self.assertFalse(previews["image_count"])
        self.assertTrue(previews["hard_fail"])

        with mock.patch("truth_check.check", return_value={"verdict": "ready", "score": 100, "problems": []}), \
             mock.patch("product_qa_deep.check_folder", return_value={"verdict": "ready", "score": 100, "problems": []}):
            result = sell_ready.evaluate_pack(pack, self.root / "marketing")
        self.assertEqual(result["status"], sell_ready.STATUS_NOT_YET)
        self.assertTrue(any("no cover or preview images" in hf for hf in result["hard_fails"]))

    def test_store_kits_module_presence_is_not_a_kit(self):
        pack = self.root / "2-planner"
        pack.mkdir()
        (pack / "listing.txt").write_text("Title: Planner\n", encoding="utf-8")
        result = sell_ready.inspect_store_kits(pack)
        self.assertFalse(result["has_kits"])
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["stores_found"], [])
