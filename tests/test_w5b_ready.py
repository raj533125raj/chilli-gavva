"""tests/test_w5b_ready.py — Comprehensive unit tests for BINNY-W5B "Sellable Now" gate & /ready.

Covers:
  - Part 1: sell_ready.py core evaluation engine
      - Inspection of pack files (product.zip, listing.txt, checklist.txt)
      - License check (in root or inside zip)
      - Truth check integration and handling
      - Deep QA integration and handling
      - Listing lint integration and forbidden money text checks
      - Marketing asset detection (carousel, pins, reel, blog, covers, launch)
      - Store kits presence detection
      - Preview & cover image size, empty file, oversized file checks
      - Design theme presence detection
      - Scoring calculation, weights, and hard fail conditions
      - Status classification: SELLABLE, ALMOST, NOT YET
      - Top fixes extraction and prioritization
      - Formatter: summary text and pack cards
      - CLI flags (--all, --json, single pack)
  - Part 2: tg_ready.py Telegram plugin
      - COMMANDS structure and help strings
      - Plugin interface adherence (handle signature, returns dict or None)
      - Routing: /ready, /ready N, Tenglish phrases
      - Ignoring other commands (/status, /packs, /help, etc.)
      - Reply keyboard generation [Kit N] [Files N] [Marketing N]
      - Button taps (Kit, Files, Marketing) with explicit or state-backed pack IDs
      - Owner-only and privacy safety (no money amounts, no leaks)
      - Non-existent pack handling
      - Context mocking (send_text, send_document, state_get, state_set)
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

import sell_ready
import tg_ready

# Sample email and chat id per lessons
SAMPLE_EMAIL = "name@example.com"
SAMPLE_CHAT_ID = int("1234" "5678")

LONG_DESCRIPTION = (
    "A clean, minimal focus planner designed to help you stay organized without "
    "overwhelming you. Includes daily priority blocks, habit trackers, and notes sections. "
    "Print on standard A4 or US Letter paper for best results. Perfect for busy professionals, "
    "students, and anyone seeking daily clarity. Personal use licence included. "
    "No digital apps or complex setups required, just print and write."
)


def create_dummy_zip(zip_path: Path, files: dict[str, str | bytes] | None = None) -> None:
    """Create a test zip file with optional inner files."""
    if files is None:
        files = {
            "focus-planner.pdf": b"%PDF-1.4 dummy pdf content\n%%EOF",
            "README.md": "Personal use guide.\n",
            "LICENSE.txt": "Standard personal use licence.\n",
        }
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, data in files.items():
            if isinstance(data, str):
                zf.writestr(name, data)
            else:
                zf.writestr(name, data)


def create_mock_pack(
    root: Path,
    pack_name: str = "1-focus-planner",
    title: str = "Focus Planner Printable Pack",
    description: str = LONG_DESCRIPTION,
    has_zip: bool = True,
    has_listing: bool = True,
    has_checklist: bool = True,
    has_license: bool = True,
    corrupt_zip: bool = False,
    money_in_listing: bool = False,
    money_in_checklist: bool = False,
    theme: str | None = "Calm Sage",
) -> Path:
    """Helper to set up a mock pack directory with customizable features."""
    pack_dir = root / pack_name
    pack_dir.mkdir(parents=True, exist_ok=True)

    if has_zip:
        zip_path = pack_dir / "product.zip"
        if corrupt_zip:
            zip_path.write_bytes(b"corrupt non-zip data")
        else:
            inner_files = {
                "planner.pdf": b"%PDF-1.4 test\n%%EOF",
                "README.md": "Read me text",
            }
            if has_license:
                inner_files["LICENSE.txt"] = "Personal Licence"
            create_dummy_zip(zip_path, inner_files)

    if has_listing:
        tags = [
            "focus planner", "daily planner", "printable page", "desk pad",
            "calm routine", "habit tracker", "study timer", "weekly menu",
            "mindful notes", "goal grid", "brain dump", "weekly audit", "one page day"
        ]
        desc = description
        if money_in_listing:
            desc += " Only costs $10 on sale."
        content = (
            f"Title: {title}\n\n"
            f"Description:\n{desc}\n\n"
            f"Tags: {', '.join(tags)}\n\n"
            f"What you get:\n- A4 PDF file\n- US Letter PDF file\n- Readme guide\n"
        )
        if theme:
            content += f"\nTheme: {theme}\n"
        (pack_dir / "listing.txt").write_text(content, encoding="utf-8")

    if has_checklist:
        chk = "Checklist:\n- [ ] Upload product.zip\n- [ ] Upload cover.png\n- [ ] Price: owner decides\n"
        if money_in_checklist:
            chk += "- [ ] Price: $15\n"
        if theme:
            chk += f"- Theme: {theme}\n"
        (pack_dir / "checklist.txt").write_text(chk, encoding="utf-8")

    if has_license:
        (pack_dir / "LICENSE.txt").write_text("Personal use licence only.\n", encoding="utf-8")

    # Add dummy cover
    (pack_dir / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)

    return pack_dir


def create_mock_marketing(
    root: Path,
    pack_name: str = "1-focus-planner",
    has_carousel: bool = True,
    has_pins: bool = True,
    has_reel: bool = True,
    has_blog: bool = True,
    has_covers: bool = True,
    has_launch: bool = True,
) -> Path:
    """Helper to set up mock marketing assets for a pack."""
    mkt_dir = root / pack_name
    mkt_dir.mkdir(parents=True, exist_ok=True)

    if has_carousel:
        c_dir = mkt_dir / "carousel"
        c_dir.mkdir(parents=True, exist_ok=True)
        (c_dir / "slide_1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        (c_dir / "caption.txt").write_text("Instagram carousel caption", encoding="utf-8")

    if has_pins:
        p_dir = mkt_dir / "pins"
        p_dir.mkdir(parents=True, exist_ok=True)
        (p_dir / "pin_1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        (p_dir / "pins.json").write_text(json.dumps([{"title": "Focus Pin 1"}]), encoding="utf-8")

    if has_reel:
        r_dir = mkt_dir / "reel"
        r_dir.mkdir(parents=True, exist_ok=True)
        (r_dir / "frame_1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        (r_dir / "reel.txt").write_text("Reel script", encoding="utf-8")

    if has_blog:
        (mkt_dir / "blog.md").write_text("# Why Daily Focus Planners Work\n\nDaily planners bring clarity...", encoding="utf-8")

    if has_covers:
        cov_dir = mkt_dir / "covers"
        cov_dir.mkdir(parents=True, exist_ok=True)
        (cov_dir / "variant_a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    if has_launch:
        (mkt_dir / "LAUNCH.md").write_text("# Launch Checklist\n\n- [ ] Gumroad upload\n", encoding="utf-8")

    return mkt_dir


class TestSellReadyCore(unittest.TestCase):
    """Unit tests for sell_ready.py core inspection and evaluation logic."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.products_dir = self.root / "products"
        self.products_dir.mkdir()
        self.marketing_dir = self.root / "marketing"
        self.marketing_dir.mkdir()

    # 1. Money text detection
    def test_check_money_text_dollar(self):
        self.assertIsNotNone(sell_ready.check_money_text("Costs $12 today"))
        self.assertIsNotNone(sell_ready.check_money_text("Price is 12$ only"))

    def test_check_money_text_rupee(self):
        self.assertIsNotNone(sell_ready.check_money_text("Price ₹499 only"))
        self.assertIsNotNone(sell_ready.check_money_text("Rs 500 discount"))
        self.assertIsNotNone(sell_ready.check_money_text("Rs. 500 discount"))

    def test_check_money_text_currency_codes(self):
        self.assertIsNotNone(sell_ready.check_money_text("USD 50 for all"))
        self.assertIsNotNone(sell_ready.check_money_text("500 INR net"))

    def test_check_money_text_clean_returns_none(self):
        self.assertIsNone(sell_ready.check_money_text("Free personal licence included"))
        self.assertIsNone(sell_ready.check_money_text("Price: set by owner"))
        self.assertIsNone(sell_ready.check_money_text(""))
        self.assertIsNone(sell_ready.check_money_text(None))

    # 2. Pack files inspection
    def test_inspect_pack_files_complete(self):
        p_dir = create_mock_pack(self.products_dir)
        res = sell_ready.inspect_pack_files(p_dir)
        self.assertTrue(res["has_zip"])
        self.assertTrue(res["has_listing"])
        self.assertTrue(res["has_checklist"])
        self.assertTrue(res["zip_ok"])
        self.assertFalse(res["hard_fail"])
        self.assertEqual(res["score"], 100)
        self.assertEqual(res["issues"], [])

    def test_inspect_pack_files_missing_zip(self):
        p_dir = create_mock_pack(self.products_dir, has_zip=False)
        res = sell_ready.inspect_pack_files(p_dir)
        self.assertFalse(res["has_zip"])
        self.assertTrue(res["hard_fail"])
        self.assertEqual(res["score"], 0)
        self.assertTrue(any("Missing product.zip" in i for i in res["issues"]))

    def test_inspect_pack_files_missing_listing(self):
        p_dir = create_mock_pack(self.products_dir, has_listing=False)
        res = sell_ready.inspect_pack_files(p_dir)
        self.assertFalse(res["has_listing"])
        self.assertTrue(res["hard_fail"])
        self.assertEqual(res["score"], 0)
        self.assertTrue(any("Missing listing.txt" in i for i in res["issues"]))

    def test_inspect_pack_files_missing_checklist(self):
        p_dir = create_mock_pack(self.products_dir, has_checklist=False)
        res = sell_ready.inspect_pack_files(p_dir)
        self.assertFalse(res["has_checklist"])
        self.assertTrue(res["hard_fail"])
        self.assertTrue(any("Missing checklist.txt" in i for i in res["issues"]))

    def test_inspect_pack_files_corrupt_zip(self):
        p_dir = create_mock_pack(self.products_dir, corrupt_zip=True)
        res = sell_ready.inspect_pack_files(p_dir)
        self.assertFalse(res["zip_ok"])
        self.assertTrue(res["hard_fail"])
        self.assertTrue(any("corrupt" in i.lower() or "unreadable" in i.lower() for i in res["issues"]))

    def test_inspect_pack_files_money_in_listing_fails(self):
        p_dir = create_mock_pack(self.products_dir, money_in_listing=True)
        res = sell_ready.inspect_pack_files(p_dir)
        self.assertTrue(res["hard_fail"])
        self.assertTrue(any("Money text in listing.txt" in i for i in res["issues"]))

    def test_inspect_pack_files_money_in_checklist_fails(self):
        p_dir = create_mock_pack(self.products_dir, money_in_checklist=True)
        res = sell_ready.inspect_pack_files(p_dir)
        self.assertTrue(res["hard_fail"])
        self.assertTrue(any("Money text in checklist.txt" in i for i in res["issues"]))

    # 3. License inspection
    def test_inspect_license_in_pack_folder(self):
        p_dir = create_mock_pack(self.products_dir, has_license=True)
        res = sell_ready.inspect_license(p_dir)
        self.assertTrue(res["has_license"])
        self.assertEqual(res["score"], 100)
        self.assertEqual(res["issues"], [])

    def test_inspect_license_inside_zip_only(self):
        p_dir = create_mock_pack(self.products_dir, has_license=False)
        # Put license inside zip
        zip_path = p_dir / "product.zip"
        create_dummy_zip(zip_path, {"LICENSE.txt": "Licence info"})
        res = sell_ready.inspect_license(p_dir)
        self.assertTrue(res["has_license"])
        self.assertEqual(res["score"], 100)

    def test_inspect_license_missing(self):
        p_dir = create_mock_pack(self.products_dir, has_license=False)
        res = sell_ready.inspect_license(p_dir)
        self.assertFalse(res["has_license"])
        self.assertEqual(res["score"], 0)
        self.assertTrue(any("Missing LICENSE" in i for i in res["issues"]))

    # 4. Truth check inspection
    @mock.patch("truth_check.check")
    def test_inspect_truth_check_ready(self, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 90, "problems": []}
        p_dir = create_mock_pack(self.products_dir)
        res = sell_ready.inspect_truth_check(p_dir)
        self.assertEqual(res["verdict"], "ready")
        self.assertEqual(res["score"], 90)
        self.assertFalse(res["hard_fail"])
        self.assertEqual(res["issues"], [])

    @mock.patch("truth_check.check")
    def test_inspect_truth_check_needs_fix(self, mock_tc):
        mock_tc.return_value = {
            "verdict": "needs fix",
            "score": 60,
            "problems": ["Missing promised table"],
        }
        p_dir = create_mock_pack(self.products_dir)
        res = sell_ready.inspect_truth_check(p_dir)
        self.assertEqual(res["verdict"], "needs fix")
        self.assertTrue(res["hard_fail"])
        self.assertTrue(any("Missing promised table" in i for i in res["issues"]))

    @mock.patch("truth_check.check")
    def test_inspect_truth_check_score_below_80_hard_fails(self, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 75, "problems": ["Low page proof"]}
        p_dir = create_mock_pack(self.products_dir)
        res = sell_ready.inspect_truth_check(p_dir)
        self.assertTrue(res["hard_fail"])

    # 5. Deep QA inspection
    @mock.patch("product_qa_deep.check_folder")
    def test_inspect_deep_qa_ready(self, mock_qa):
        mock_qa.return_value = {"verdict": "ready", "score": 95, "problems": []}
        p_dir = create_mock_pack(self.products_dir)
        res = sell_ready.inspect_deep_qa(p_dir)
        self.assertEqual(res["verdict"], "ready")
        self.assertEqual(res["score"], 95)
        self.assertFalse(res["hard_fail"])

    @mock.patch("product_qa_deep.check_folder")
    def test_inspect_deep_qa_low_score_hard_fails(self, mock_qa):
        mock_qa.return_value = {
            "verdict": "fix",
            "score": 45,
            "problems": [{"detail": "Blank page found"}],
        }
        p_dir = create_mock_pack(self.products_dir)
        res = sell_ready.inspect_deep_qa(p_dir)
        self.assertTrue(res["hard_fail"])
        self.assertEqual(res["score"], 45)
        self.assertTrue(any("Blank page found" in i for i in res["issues"]))

    # 6. Listing lint inspection
    def test_inspect_listing_lint_good_listing(self):
        p_dir = create_mock_pack(self.products_dir)
        res = sell_ready.inspect_listing_lint(p_dir)
        self.assertTrue(res["lint_ok"])
        self.assertFalse(res["hard_fail"])
        self.assertGreaterEqual(res["score"], 80)

    def test_inspect_listing_lint_with_money_hard_fails(self):
        p_dir = create_mock_pack(self.products_dir, money_in_listing=True)
        res = sell_ready.inspect_listing_lint(p_dir)
        self.assertTrue(res["hard_fail"])

    def test_inspect_listing_lint_bad_tags(self):
        p_dir = create_mock_pack(self.products_dir)
        # Rewrite listing with only 2 tags
        (p_dir / "listing.txt").write_text("Title: Focus Planner Pack\n\nDescription:\n" + LONG_DESCRIPTION + "\n\nTags: one, two\n\nWhat you get:\n- PDF\n- Sheet\n- License", encoding="utf-8")
        res = sell_ready.inspect_listing_lint(p_dir)
        self.assertFalse(res["lint_ok"])
        self.assertTrue(any("tags" in i.lower() for i in res["issues"]))

    # 7. Marketing inspection
    def test_inspect_marketing_all_present(self):
        p_dir = create_mock_pack(self.products_dir)
        create_mock_marketing(self.marketing_dir)
        res = sell_ready.inspect_marketing(p_dir, self.marketing_dir)
        self.assertTrue(res["has_carousel"])
        self.assertTrue(res["has_pins"])
        self.assertTrue(res["has_reel"])
        self.assertTrue(res["has_blog"])
        self.assertTrue(res["has_covers"])
        self.assertTrue(res["has_launch"])
        self.assertEqual(res["found_count"], 6)
        self.assertEqual(res["score"], 100)
        self.assertEqual(res["issues"], [])

    def test_inspect_marketing_partial(self):
        p_dir = create_mock_pack(self.products_dir)
        create_mock_marketing(self.marketing_dir, has_reel=False, has_blog=False)
        res = sell_ready.inspect_marketing(p_dir, self.marketing_dir)
        self.assertFalse(res["has_reel"])
        self.assertFalse(res["has_blog"])
        self.assertEqual(res["found_count"], 4)
        self.assertTrue(any("Missing reel" in i for i in res["issues"]))
        self.assertTrue(any("Missing blog" in i for i in res["issues"]))

    def test_inspect_marketing_none(self):
        p_dir = create_mock_pack(self.products_dir)
        res = sell_ready.inspect_marketing(p_dir, self.root / "empty_marketing")
        # Cover in pack dir counts, checklist.txt counts as launch
        self.assertTrue(res["has_covers"])
        self.assertTrue(res["has_launch"])
        self.assertEqual(res["found_count"], 2)

    # 8. Store kits inspection
    def test_inspect_store_kits_in_subfolder(self):
        p_dir = create_mock_pack(self.products_dir)
        kits_dir = p_dir / "store-kits"
        kits_dir.mkdir()
        (kits_dir / "gumroad.txt").write_text("gumroad listing kit", encoding="utf-8")
        res = sell_ready.inspect_store_kits(p_dir)
        self.assertTrue(res["has_kits"])
        self.assertEqual(res["score"], 100)
        self.assertIn("gumroad", res["stores_found"])

    def test_inspect_store_kits_in_root(self):
        p_dir = create_mock_pack(self.products_dir)
        (p_dir / "kit-topmate.txt").write_text("topmate kit", encoding="utf-8")
        res = sell_ready.inspect_store_kits(p_dir)
        self.assertTrue(res["has_kits"])
        self.assertEqual(res["score"], 100)
        self.assertIn("topmate", res["stores_found"])

    def test_inspect_store_kits_missing(self):
        p_dir = create_mock_pack(self.products_dir)
        # Without the store_kits generator (W2C) and without kit files, kits are missing.
        import sys
        from unittest import mock as _mock
        with _mock.patch.dict(sys.modules, {"store_kits": None}):
            res = sell_ready.inspect_store_kits(p_dir)
        self.assertFalse(res["has_kits"])
        self.assertEqual(res["score"], 0)
        self.assertTrue(any("Missing store listing kits" in i for i in res["issues"]))

    # 9. Preview images inspection
    def test_inspect_preview_images_valid(self):
        p_dir = create_mock_pack(self.products_dir)
        res = sell_ready.inspect_preview_images(p_dir)
        self.assertGreaterEqual(res["image_count"], 1)
        self.assertEqual(res["score"], 100)
        self.assertEqual(res["issues"], [])

    def test_inspect_preview_images_missing(self):
        p_dir = self.products_dir / "no-images"
        p_dir.mkdir()
        res = sell_ready.inspect_preview_images(p_dir)
        self.assertEqual(res["image_count"], 0)
        self.assertEqual(res["score"], 0)
        self.assertTrue(any("No preview" in i for i in res["issues"]))

    def test_inspect_preview_images_oversized(self):
        p_dir = create_mock_pack(self.products_dir)
        # Create a mock 6MB file
        oversized = p_dir / "preview-huge.png"
        oversized.write_bytes(b"\x00" * (6 * 1024 * 1024))
        res = sell_ready.inspect_preview_images(p_dir)
        self.assertLess(res["score"], 100)
        self.assertTrue(any("too large" in i for i in res["issues"]))

    def test_inspect_preview_images_zero_byte(self):
        p_dir = create_mock_pack(self.products_dir)
        zero = p_dir / "preview-empty.png"
        zero.write_bytes(b"")
        res = sell_ready.inspect_preview_images(p_dir)
        self.assertLess(res["score"], 100)
        self.assertTrue(any("Empty (0-byte)" in i for i in res["issues"]))

    # 10. Design theme inspection
    def test_inspect_design_theme_in_listing(self):
        p_dir = create_mock_pack(self.products_dir, theme="Calm Sage")
        res = sell_ready.inspect_design_theme(p_dir)
        self.assertTrue(res["has_theme"])
        self.assertEqual(res["theme"], "Calm Sage")
        self.assertEqual(res["score"], 100)

    def test_inspect_design_theme_in_json_spec(self):
        p_dir = create_mock_pack(self.products_dir, theme=None)
        spec = {"theme": "Terracotta", "title": "Planner"}
        (p_dir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        res = sell_ready.inspect_design_theme(p_dir)
        self.assertTrue(res["has_theme"])
        self.assertEqual(res["theme"], "Terracotta")

    def test_inspect_design_theme_missing(self):
        p_dir = create_mock_pack(self.products_dir, theme=None)
        res = sell_ready.inspect_design_theme(p_dir)
        self.assertFalse(res["has_theme"])
        self.assertEqual(res["score"], 0)
        self.assertTrue(any("theme not specified" in i for i in res["issues"]))


class TestSellReadyEvaluation(unittest.TestCase):
    """Unit tests for evaluate_pack and overall status resolution."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.products_dir = self.root / "products"
        self.products_dir.mkdir()
        self.marketing_dir = self.root / "marketing"
        self.marketing_dir.mkdir()

    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_evaluate_pack_sellable(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 100, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 100, "problems": []}

        p_dir = create_mock_pack(self.products_dir, theme="Calm Sage")
        # Add store kit
        kits = p_dir / "store-kits"
        kits.mkdir()
        (kits / "gumroad.txt").write_text("kit", encoding="utf-8")
        # Add marketing
        create_mock_marketing(self.marketing_dir)

        res = sell_ready.evaluate_pack(p_dir, self.marketing_dir)
        self.assertEqual(res["status"], sell_ready.STATUS_SELLABLE)
        self.assertGreaterEqual(res["score"], sell_ready.SELLABLE_MIN_SCORE)
        self.assertEqual(res["hard_fails"], [])
        self.assertEqual(res["fixes"], [])

    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_evaluate_pack_almost(self, mock_qa, mock_tc):
        # Truth check and Deep QA are passing, but missing marketing files and store kits
        mock_tc.return_value = {"verdict": "ready", "score": 80, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 70, "problems": []}

        p_dir = create_mock_pack(self.products_dir, theme="Calm Sage")
        res = sell_ready.evaluate_pack(p_dir, self.root / "empty_mkt")
        self.assertEqual(res["status"], sell_ready.STATUS_ALMOST)
        self.assertGreaterEqual(res["score"], sell_ready.ALMOST_MIN_SCORE)
        self.assertLess(res["score"], 100)
        self.assertEqual(res["hard_fails"], [])
        self.assertGreater(len(res["top_fixes"]), 0)

    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_evaluate_pack_not_yet_on_truth_check_needs_fix(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "needs fix", "score": 70, "problems": ["Missing promised page"]}
        mock_qa.return_value = {"verdict": "ready", "score": 95, "problems": []}

        p_dir = create_mock_pack(self.products_dir)
        res = sell_ready.evaluate_pack(p_dir, self.marketing_dir)
        self.assertEqual(res["status"], sell_ready.STATUS_NOT_YET)
        self.assertTrue(any("Truth check" in hf for hf in res["hard_fails"]))

    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_evaluate_pack_not_yet_on_missing_zip(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 90, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 90, "problems": []}

        p_dir = create_mock_pack(self.products_dir, has_zip=False)
        res = sell_ready.evaluate_pack(p_dir, self.marketing_dir)
        self.assertEqual(res["status"], sell_ready.STATUS_NOT_YET)
        self.assertTrue(any("product.zip" in hf for hf in res["hard_fails"]))

    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_evaluate_pack_not_yet_on_money_text(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 90, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 90, "problems": []}

        p_dir = create_mock_pack(self.products_dir, money_in_listing=True)
        res = sell_ready.evaluate_pack(p_dir, self.marketing_dir)
        self.assertEqual(res["status"], sell_ready.STATUS_NOT_YET)
        self.assertTrue(any("money" in hf.lower() for hf in res["hard_fails"]))

    def test_evaluate_pack_non_existent_folder(self):
        missing = self.products_dir / "does_not_exist"
        res = sell_ready.evaluate_pack(missing)
        self.assertEqual(res["status"], sell_ready.STATUS_NOT_YET)
        self.assertEqual(res["score"], 0)
        self.assertIn("Pack directory does not exist", res["hard_fails"])

    # 11. Discovery & Batch evaluation
    def test_find_all_ready_packs(self):
        create_mock_pack(self.products_dir, pack_name="1-alpha", title="Alpha Pack")
        create_mock_pack(self.products_dir, pack_name="2-beta", title="Beta Pack")
        # Empty non-ready folder
        (self.products_dir / "3-draft").mkdir()

        packs = sell_ready.find_all_ready_packs(self.products_dir)
        self.assertEqual(len(packs), 2)
        self.assertEqual(packs[0][0], 1)
        self.assertEqual(packs[0][1], "Alpha Pack")
        self.assertEqual(packs[1][0], 2)
        self.assertEqual(packs[1][1], "Beta Pack")

    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_evaluate_all_packs(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 90, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 90, "problems": []}

        create_mock_pack(self.products_dir, pack_name="1-alpha")
        create_mock_pack(self.products_dir, pack_name="2-beta")

        evals = sell_ready.evaluate_all_packs(self.products_dir, self.marketing_dir)
        self.assertEqual(len(evals), 2)
        self.assertEqual(evals[0]["pack_name"], "1-alpha")
        self.assertEqual(evals[1]["pack_name"], "2-beta")

    # 12. Formatter tests
    def test_format_ready_summary_empty(self):
        text = sell_ready.format_ready_summary([])
        self.assertIn("Prasthuthaniki ready upload packs emi levu", text)

    def test_format_ready_summary_with_groups(self):
        evals = [
            {
                "pack_name": "1-alpha",
                "title": "Alpha Focus",
                "status": sell_ready.STATUS_SELLABLE,
                "score": 95,
                "top_fixes": [],
            },
            {
                "pack_name": "2-beta",
                "title": "Beta Planner",
                "status": sell_ready.STATUS_ALMOST,
                "score": 75,
                "top_fixes": ["Missing blog.md", "Missing reel"],
            },
            {
                "pack_name": "3-gamma",
                "title": "Gamma Sheet",
                "status": sell_ready.STATUS_NOT_YET,
                "score": 40,
                "top_fixes": ["Truth check needs fix"],
            },
        ]
        text = sell_ready.format_ready_summary(evals)
        self.assertIn("SELL READY GATE REPORT", text)
        self.assertIn("SELLABLE NOW", text)
        self.assertIn("ALMOST READY", text)
        self.assertIn("NOT YET", text)
        self.assertIn("Alpha Focus", text)
        self.assertIn("Beta Planner", text)
        self.assertIn("Missing blog.md", text)
        self.assertIn("Truth check needs fix", text)

    def test_format_pack_card(self):
        evaluation = {
            "pack_name": "1-alpha",
            "title": "Alpha Focus Planner",
            "status": sell_ready.STATUS_ALMOST,
            "score": 78,
            "checks": {
                "truth_check": {"verdict": "ready", "score": 90},
                "deep_qa": {"verdict": "ready", "score": 92},
                "listing_lint": {"lint_ok": True, "score": 85},
                "pack_files": {"hard_fail": False},
                "marketing": {"found_count": 4},
                "store_kits": {"has_kits": True},
                "preview_images": {"image_count": 2, "score": 100},
                "license": {"has_license": True},
                "design_theme": {"has_theme": True, "theme": "Calm Sage"},
            },
            "fixes": ["Missing blog.md", "Missing Instagram carousel"],
        }
        card = sell_ready.format_pack_card(evaluation)
        self.assertIn("Pack 1-alpha — Sell Ready Card", card)
        self.assertIn("Status: *ALMOST*", card)
        self.assertIn("Score: *78/100*", card)
        self.assertIn("Truth Check: ready", card)
        self.assertIn("Deep QA: ready", card)
        self.assertIn("Design Theme: Calm Sage", card)
        self.assertIn("Missing blog.md", card)

    # 13. CLI execution tests
    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_cli_all(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 100, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 100, "problems": []}
        p = create_mock_pack(self.products_dir, pack_name="1-test")

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            code = sell_ready.main(["--products-dir", str(self.products_dir), "--all"])
        self.assertIn("SELL READY GATE REPORT", buf.getvalue())

    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_cli_json(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 100, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 100, "problems": []}
        create_mock_pack(self.products_dir, pack_name="1-test")

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            code = sell_ready.main(["--products-dir", str(self.products_dir), "--json"])
        data = json.loads(buf.getvalue())
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["pack_name"], "1-test")

    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_cli_single_pack(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 100, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 100, "problems": []}
        p = create_mock_pack(self.products_dir, pack_name="1-test")

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            code = sell_ready.main(["1", "--products-dir", str(self.products_dir)])
        self.assertIn("Pack 1-test — Sell Ready Card", buf.getvalue())


class TestTgReadyPlugin(unittest.TestCase):
    """Unit tests for tg_ready.py Telegram plugin."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.products_dir = self.root / "products"
        self.products_dir.mkdir()
        self.marketing_dir = self.root / "marketing"
        self.marketing_dir.mkdir()

        self.sent_texts: list[tuple[str, any]] = []
        self.sent_docs: list[tuple[str, str]] = []
        self.state: dict[str, any] = {}

    def _ctx(self) -> dict:
        def _send_text(text, keyboard=None):
            self.sent_texts.append((text, keyboard))
            return True

        def _send_document(path, caption=""):
            self.sent_docs.append((path, caption))
            return True

        def _state_get(key, default=None):
            return self.state.get(key, default)

        def _state_set(key, val):
            self.state[key] = val

        return {
            "chat_id": SAMPLE_CHAT_ID,
            "send_text": _send_text,
            "send_document": _send_document,
            "state_get": _state_get,
            "state_set": _state_set,
            "products_dir": str(self.products_dir),
            "marketing_dir": str(self.marketing_dir),
        }

    # 1. Plugin structure
    def test_plugin_commands_format(self):
        self.assertTrue(hasattr(tg_ready, "COMMANDS"))
        self.assertIsInstance(tg_ready.COMMANDS, list)
        self.assertGreater(len(tg_ready.COMMANDS), 0)
        cmd = tg_ready.COMMANDS[0]
        self.assertEqual(cmd["cmd"], "/ready")
        self.assertIn("ready", cmd["words"])
        self.assertIn("help_en", cmd)
        self.assertIn("help_te", cmd)

    # 2. Ignored commands
    def test_handle_ignores_empty_or_non_string(self):
        ctx = self._ctx()
        self.assertIsNone(tg_ready.handle("", ctx))
        self.assertIsNone(tg_ready.handle(None, ctx))
        self.assertIsNone(tg_ready.handle(123, ctx))

    def test_handle_ignores_other_builtins(self):
        ctx = self._ctx()
        for cmd in ("/status", "/packs", "/pack 1", "/approve 1", "/reject 1", "/help", "/start", "/menu"):
            self.assertIsNone(tg_ready.handle(cmd, ctx), f"Should not intercept {cmd}")

    def test_handle_ignores_unrelated_text(self):
        ctx = self._ctx()
        self.assertIsNone(tg_ready.handle("random chit chat", ctx))
        self.assertIsNone(tg_ready.handle("how is the weather", ctx))

    # 3. Summary command: /ready and words
    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_handle_ready_summary_empty(self, mock_qa, mock_tc):
        ctx = self._ctx()
        res = tg_ready.handle("/ready", ctx)
        self.assertIsNotNone(res)
        self.assertIn("Prasthuthaniki ready upload packs emi levu", res["text"])

    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_handle_ready_summary_with_packs(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 90, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 90, "problems": []}
        create_mock_pack(self.products_dir, pack_name="1-planner", title="Daily Planner")

        ctx = self._ctx()
        res = tg_ready.handle("/ready", ctx)
        self.assertIsNotNone(res)
        self.assertIn("SELL READY GATE REPORT", res["text"])
        self.assertIn("Daily Planner", res["text"])

    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_handle_ready_words_triggers(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 90, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 90, "problems": []}
        create_mock_pack(self.products_dir, pack_name="1-planner")

        ctx = self._ctx()
        for word in ("ready", "sell ready", "ready packs", "sellable", "ready ga unnaya", "ready list"):
            res = tg_ready.handle(word, ctx)
            self.assertIsNotNone(res, f"Failed on word '{word}'")
            self.assertIn("SELL READY GATE REPORT", res["text"])

    # 4. Pack Card: /ready N
    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_handle_ready_pack_n(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 95, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 95, "problems": []}
        create_mock_pack(self.products_dir, pack_name="1-focus", title="Focus Planner")

        ctx = self._ctx()
        res = tg_ready.handle("/ready 1", ctx)
        self.assertTrue(res.get("handled"))
        self.assertEqual(len(self.sent_texts), 1)

        card_text, keyboard = self.sent_texts[0]
        self.assertIn("Pack 1-focus — Sell Ready Card", card_text)
        self.assertIn("Focus Planner", card_text)

        # Check reply keyboard buttons
        self.assertIsNotNone(keyboard)
        self.assertEqual(keyboard[0], ["Kit 1", "Files 1", "Marketing 1"])
        self.assertEqual(keyboard[1], ["📦 All Ready Packs"])

        # Check state recorded last ready pack ID
        self.assertEqual(self.state.get(tg_ready.STATE_KEY_LAST_READY_PACK), 1)

    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_handle_ready_tenglish_pack_queries(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 95, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 95, "problems": []}
        create_mock_pack(self.products_dir, pack_name="2-calm", title="Calm Daily")

        ctx = self._ctx()
        # Variations like "ready 2", "pack 2 ready", "pack 2 ready aa"
        for phr in ("ready 2", "pack 2 ready", "pack 2 ready aa", "2 ready"):
            self.sent_texts.clear()
            res = tg_ready.handle(phr, ctx)
            self.assertTrue(res.get("handled"), f"Failed on phrase '{phr}'")
            card_text, _ = self.sent_texts[0]
            self.assertIn("Pack 2-calm — Sell Ready Card", card_text)

    def test_handle_ready_non_existent_pack(self):
        ctx = self._ctx()
        res = tg_ready.handle("/ready 99", ctx)
        self.assertIsNotNone(res)
        self.assertIn("Pack #99 kanipinchaledu", res["text"])

    # 5. Button tap flows: Kit, Files, Marketing
    def test_handle_button_kit_with_existing_kits(self):
        p_dir = create_mock_pack(self.products_dir, pack_name="1-test")
        kits_dir = p_dir / "store-kits"
        kits_dir.mkdir()
        (kits_dir / "gumroad.txt").write_text("gumroad form fields", encoding="utf-8")

        ctx = self._ctx()
        # Tap "Kit 1"
        res = tg_ready.handle("Kit 1", ctx)
        self.assertIn("store kits pampinchanu", res["text"])
        self.assertEqual(len(self.sent_docs), 1)
        self.assertIn("gumroad.txt", self.sent_docs[0][0])

    def test_handle_button_kit_missing(self):
        create_mock_pack(self.products_dir, pack_name="1-test")
        ctx = self._ctx()
        res = tg_ready.handle("Kit 1", ctx)
        self.assertIn("store kits generate avvaledu", res["text"])

    def test_handle_button_files(self):
        create_mock_pack(self.products_dir, pack_name="1-test")
        ctx = self._ctx()
        res = tg_ready.handle("Files 1", ctx)
        self.assertIn("files pampinchanu", res["text"])
        self.assertGreaterEqual(len(self.sent_docs), 1)
        self.assertTrue(any("product.zip" in d[0] for d in self.sent_docs))

    def test_handle_button_marketing_present(self):
        create_mock_pack(self.products_dir, pack_name="1-test")
        create_mock_marketing(self.marketing_dir, pack_name="1-test")
        ctx = self._ctx()
        res = tg_ready.handle("Marketing 1", ctx)
        self.assertIn("Marketing Assets", res["text"])
        self.assertIn("Carousel slides", res["text"])
        self.assertIn("Pinterest pins", res["text"])

    def test_handle_button_marketing_missing(self):
        create_mock_pack(self.products_dir, pack_name="1-test")
        ctx = self._ctx()
        res = tg_ready.handle("Marketing 1", ctx)
        self.assertIn("marketing folder inka create avvaledu", res["text"])

    # 6. Button tap using state fallback (when button text is just "Kit")
    def test_handle_generic_button_kit_uses_state(self):
        p_dir = create_mock_pack(self.products_dir, pack_name="1-test")
        kits_dir = p_dir / "store-kits"
        kits_dir.mkdir()
        (kits_dir / "etsy.txt").write_text("etsy kit", encoding="utf-8")

        ctx = self._ctx()
        # Set state as if user previously viewed pack 1
        self.state[tg_ready.STATE_KEY_LAST_READY_PACK] = 1

        res = tg_ready.handle("Kit", ctx)
        self.assertIn("store kits pampinchanu", res["text"])
        self.assertEqual(len(self.sent_docs), 1)

    def test_handle_generic_button_without_state_ignored(self):
        ctx = self._ctx()
        self.state.clear()
        res = tg_ready.handle("Kit", ctx)
        self.assertIsNone(res)

    # 7. Tapping "All Ready Packs"
    @mock.patch("truth_check.check")
    @mock.patch("product_qa_deep.check_folder")
    def test_handle_all_ready_packs_button(self, mock_qa, mock_tc):
        mock_tc.return_value = {"verdict": "ready", "score": 90, "problems": []}
        mock_qa.return_value = {"verdict": "ready", "score": 90, "problems": []}
        create_mock_pack(self.products_dir, pack_name="1-test")

        ctx = self._ctx()
        res = tg_ready.handle("📦 All Ready Packs", ctx)
        self.assertIsNotNone(res)
        self.assertIn("SELL READY GATE REPORT", res["text"])


if __name__ == "__main__":
    unittest.main()
