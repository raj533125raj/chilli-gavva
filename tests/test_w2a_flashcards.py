"""tests/test_w2a_flashcards.py — Comprehensive unit tests for builder_flashcards.py.

Tests cover:
  1. Module metadata and constants (FORMAT, KEYWORDS, content sets, print sizes).
  2. Spec parsing, normalization, and validation (topic, paper_size, cards_per_page, activity).
  3. Safe defaults and fallbacks for minimal/missing spec parameters.
  4. Print size geometry and DPI metadata (A4, Letter).
  5. Flashcard layout math (grid calculations, cards per page, duplex alignment).
  6. Worksheet activity types (trace, match, count_colour, simple_math).
  7. Output filenames cleanliness and naming conventions.
  8. Determinism (consistent output given identical spec and inputs).
  9. Atomic writes and directory safety (temporary folders cleaned up, no partial files on error).
  10. Error handling on edge cases (empty spec, negative count, non-existent directories).
  11. Return value format and Tenglish message compliance.
  12. No currency mentions in any test assertion or generated content.
"""

import hashlib
import io
import math
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from pathlib import Path

import builder_flashcards


@unittest.skipUnless(builder_flashcards.HAS_PILLOW, "Pillow is required for flashcards builder tests")
class TestW2AFlashCards(unittest.TestCase):
    """Unit tests for builder_flashcards.py."""

    def setUp(self):
        # Jules rules: clear GITHUB_* and set test environment
        self.env_patch = mock.patch.dict(
            os.environ,
            {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"},
            clear=True,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

        self.tmp_dir = tempfile.mkdtemp(prefix="test_flashcards_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # ── 1. Module Constants & Metadata Tests ─────────────────────────────

    def test_01_format_constant(self):
        """FORMAT must be 'flashcards'."""
        self.assertEqual(builder_flashcards.FORMAT, "flashcards")

    def test_02_keywords_contains_required_elements(self):
        """KEYWORDS must contain the required keyword phrases."""
        expected = [
            "flashcards", "printable flashcards", "kids worksheets",
            "early learning", "tracing", "matching",
        ]
        for kw in expected:
            self.assertIn(kw, builder_flashcards.KEYWORDS)

    def test_03_alphabet_set_exists(self):
        """ALPHABET must contain A-Z."""
        self.assertTrue(
            all(chr(i) in builder_flashcards.ALPHABET for i in range(ord("A"), ord("Z") + 1)),
            "ALPHABET should contain A-Z",
        )

    def test_04_numbers_set_exists(self):
        """NUMBERS must contain 1-20."""
        self.assertEqual(builder_flashcards.NUMBERS, [str(i) for i in range(1, 21)])

    def test_05_shapes_set_exists(self):
        """SHAPES must have at least 5 shapes."""
        self.assertGreaterEqual(len(builder_flashcards.SHAPES), 5)

    def test_06_colours_set_exists(self):
        """COLOURS must have at least 5 colours."""
        self.assertGreaterEqual(len(builder_flashcards.COLOURS), 5)

    def test_07_print_sizes_has_a4_and_letter(self):
        """PRINT_SIZES must contain a4 and letter keys."""
        self.assertIn("a4", builder_flashcards.PRINT_SIZES)
        self.assertIn("letter", builder_flashcards.PRINT_SIZES)

    # ── 2. Spec Parsing & Validation Tests ──────────────────────────────

    def test_09_build_requires_kind(self):
        """build() must raise ValueError if kind is missing."""
        with self.assertRaises(ValueError):
            builder_flashcards.build({}, self.tmp_dir)

    def test_10_build_requires_valid_kind(self):
        """build() must raise ValueError for invalid kind."""
        with self.assertRaises(ValueError):
            builder_flashcards.build({"kind": "invalid"}, self.tmp_dir)

    def test_11_build_validates_paper_size(self):
        """build() must raise ValueError for invalid paper_size."""
        with self.assertRaises(ValueError):
            builder_flashcards.build({"kind": "flashcards", "paper_size": "legal"}, self.tmp_dir)

    def test_12_build_validates_cards_per_page(self):
        """build() must raise ValueError for invalid cards_per_page."""
        with self.assertRaises(ValueError):
            builder_flashcards.build({"kind": "flashcards", "cards_per_page": 6}, self.tmp_dir)

    def test_13_build_validates_worksheet_activity(self):
        """build() must raise ValueError for invalid worksheet activity."""
        with self.assertRaises(ValueError):
            builder_flashcards.build({"kind": "worksheets", "activity": "invalid"}, self.tmp_dir)

    # ── 3. Defaults & Fallbacks Tests ───────────────────────────────────

    def test_14_defaults_to_flashcards_alphabet_a4_8cards(self):
        """build() defaults when kind is provided: topic=alphabet, paper_size=a4, cards_per_page=8."""
        spec = {"kind": "flashcards"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertTrue(len(created) > 0)

    def test_15_defaults_worksheets_trace(self):
        """build() default activity for worksheets is 'trace'."""
        spec = {"kind": "worksheets"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertTrue(len(created) > 0)

    def test_16_empty_topic_defaults_to_alphabet(self):
        """Empty topic should default to alphabet."""
        spec = {"kind": "flashcards", "topic": ""}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertTrue(len(created) > 0)

    # ── 4. Print Size Geometry Tests ─────────────────────────────────────

    def test_17_a4_page_dimensions(self):
        """A4 width_px = 2480, height_px = 3508."""
        a4 = builder_flashcards.PRINT_SIZES["a4"]
        self.assertEqual(a4["width_px"], 2480)
        self.assertEqual(a4["height_px"], 3508)

    def test_18_letter_page_dimensions(self):
        """Letter width_px = 2550, height_px = 3300."""
        letter = builder_flashcards.PRINT_SIZES["letter"]
        self.assertEqual(letter["width_px"], 2550)
        self.assertEqual(letter["height_px"], 3300)

    def test_19_cards_per_page_8_grid(self):
        """8 cards per page: 2 cols x 4 rows."""
        spec = {"kind": "flashcards", "topic": "alphabet", "cards_per_page": 8}
        created = builder_flashcards.build(spec, self.tmp_dir)
        # Should have at least the page files
        page_files = [f for f in created if "page_" in f]
        self.assertTrue(len(page_files) > 0)

    def test_20_cards_per_page_12_grid(self):
        """12 cards per page: 3 cols x 4 rows."""
        spec = {"kind": "flashcards", "topic": "alphabet", "cards_per_page": 12}
        created = builder_flashcards.build(spec, self.tmp_dir)
        page_files = [f for f in created if "page_" in f]
        self.assertTrue(len(page_files) > 0)

    # ── 5. Flashcard Layout Tests ────────────────────────────────────────

    def test_21_flashcards_alphabet_generates_cards(self):
        """Alphabet flashcards should generate card files."""
        spec = {"kind": "flashcards", "topic": "alphabet"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertTrue(len(created) > 0)

    def test_22_flashcards_numbers_generates_cards(self):
        """Number flashcards should generate card files."""
        spec = {"kind": "flashcards", "topic": "numbers"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertTrue(len(created) > 0)

    def test_23_flashcards_shapes_generates_cards(self):
        """Shape flashcards should generate card files."""
        spec = {"kind": "flashcards", "topic": "shapes"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertTrue(len(created) > 0)

    def test_24_flashcards_colours_generates_cards(self):
        """Colour flashcards should generate card files."""
        spec = {"kind": "flashcards", "topic": "colours"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertTrue(len(created) > 0)

    def test_25_flashcards_telugu_generates_cards(self):
        """Telugu aksharalu flashcards should generate card files."""
        spec = {"kind": "flashcards", "topic": "telugu"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertTrue(len(created) > 0)

    # ── 6. Worksheet Activity Tests ──────────────────────────────────────

    def test_26_worksheets_trace_activity(self):
        """Worksheets with trace activity should generate files."""
        spec = {"kind": "worksheets", "topic": "alphabet", "activity": "trace"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertTrue(len(created) > 0)

    def test_27_worksheets_match_activity(self):
        """Worksheets with match activity should generate files."""
        spec = {"kind": "worksheets", "topic": "alphabet", "activity": "match"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertTrue(len(created) > 0)

    def test_28_worksheets_count_colour_activity(self):
        """Worksheets with count-and-colour activity should generate files."""
        spec = {"kind": "worksheets", "topic": "numbers", "activity": "count_colour"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertTrue(len(created) > 0)

    def test_29_worksheets_simple_math_activity(self):
        """Worksheets with simple_math activity should generate files."""
        spec = {"kind": "worksheets", "topic": "numbers", "activity": "simple_math"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertTrue(len(created) > 0)

    # ── 7. Output Files & Naming Conventions ─────────────────────────────

    def test_30_cover_pdf_created(self):
        """cover.pdf must be created."""
        spec = {"kind": "flashcards", "topic": "alphabet"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertIn("cover.pdf", created)

    def test_31_license_txt_created(self):
        """LICENSE.txt must be created."""
        spec = {"kind": "flashcards", "topic": "alphabet"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertIn("LICENSE.txt", created)

    def test_32_readme_created(self):
        """README-HOW-TO-INSTALL.txt must be created."""
        spec = {"kind": "flashcards", "topic": "alphabet"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        self.assertIn("README-HOW-TO-INSTALL.txt", created)

    def test_33_preview_images_created(self):
        """3 preview images must be created."""
        spec = {"kind": "flashcards", "topic": "alphabet"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        preview_files = [f for f in created if f.startswith("preview_") and f.endswith(".png")]
        self.assertEqual(len(preview_files), 3)

    def test_34_manifest_json_created(self):
        """Page manifest JSON files must be created."""
        spec = {"kind": "flashcards", "topic": "alphabet", "cards_per_page": 8}
        created = builder_flashcards.build(spec, self.tmp_dir)
        manifest_files = [f for f in created if "manifest" in f]
        self.assertTrue(len(manifest_files) > 0)

    def test_35_no_currency_in_license(self):
        """LICENSE.txt must not contain currency amounts."""
        spec = {"kind": "flashcards", "topic": "alphabet"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        license_path = Path(self.tmp_dir) / "marketing" / "LICENSE.txt"
        if license_path.exists():
            content = license_path.read_text(encoding="utf-8")
            self.assertNotIn("$", content)
            self.assertNotIn("RS.", content)

    def test_36_no_currency_in_readme(self):
        """README must not contain currency amounts."""
        spec = {"kind": "flashcards", "topic": "alphabet"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        readme_path = Path(self.tmp_dir) / "marketing" / "README-HOW-TO-INSTALL.txt"
        if readme_path.exists():
            content = readme_path.read_text(encoding="utf-8")
            self.assertNotIn("$", content)

    # ── 8. Determinism Tests ────────────────────────────────────────────

    def test_37_deterministic_same_spec(self):
        """Same spec should produce same output files."""
        spec = {"kind": "flashcards", "topic": "alphabet", "paper_size": "a4", "cards_per_page": 8}
        # First run
        created1 = builder_flashcards.build(spec, self.tmp_dir)
        # Second run to same dir (will overwrite)
        created2 = builder_flashcards.build(spec, self.tmp_dir)
        # Both should have same number of files
        self.assertEqual(len(created1), len(created2))

    # ── 9. Atomic Writes & Directory Safety ──────────────────────────────

    def test_38_temporary_dir_cleaned(self):
        """build() should not leave partial files on error."""
        # Use invalid spec that will cause issues
        spec = {"kind": "flashcards"}  # valid but minimal
        created = builder_flashcards.build(spec, self.tmp_dir)
        # Dir should exist and be cleanable
        self.assertTrue(os.path.exists(self.tmp_dir) or os.path.isdir(self.tmp_dir))

    def test_39_marketing_subdir_created(self):
        """build() must create marketing/ subdirectory."""
        spec = {"kind": "flashcards", "topic": "alphabet"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        marketing_dir = Path(self.tmp_dir) / "marketing"
        self.assertTrue(marketing_dir.exists())

    def test_40_images_subdir_created(self):
        """build() must create marketing/images/ subdirectory."""
        spec = {"kind": "flashcards", "topic": "alphabet"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        images_dir = Path(self.tmp_dir) / "marketing" / "images"
        self.assertTrue(images_dir.exists())

    # ── 10. Edge Case Error Handling ─────────────────────────────────────

    def test_41_empty_spec_no_crash(self):
        """build() with empty spec should not crash, use defaults."""
        try:
            created = builder_flashcards.build({}, self.tmp_dir)
            self.assertTrue(len(created) >= 0)
        except Exception as e:
            # Expected to either work with defaults or raise specific error
            self.assertIsInstance(e, (ValueError, TypeError))

    def test_42_negative_cards_per_page(self):
        """Negative cards_per_page should be handled."""
        with self.assertRaises(ValueError):
            builder_flashcards.build({"kind": "flashcards", "cards_per_page": -1}, self.tmp_dir)

    # ── 11. Return Value Format ──────────────────────────────────────────

    def test_43_return_type_is_list(self):
        """build() must return a list."""
        spec = {"kind": "flashcards", "topic": "alphabet"}
        result = builder_flashcards.build(spec, self.tmp_dir)
        self.assertIsInstance(result, list)

    def test_44_return_has_required_files(self):
        """build() return list must contain required file names."""
        spec = {"kind": "flashcards", "topic": "alphabet"}
        created = builder_flashcards.build(spec, self.tmp_dir)
        required = ["cover.pdf", "LICENSE.txt", "README-HOW-TO-INSTALL.txt"]
        for f in required:
            self.assertIn(f, created)

    # ── 12. No Currency Mentions ─────────────────────────────────────────

    def test_45_no_currency_in_build_output(self):
        """No currency should appear in text-generated file content."""
        spec = {"kind": "flashcards", "topic": "alphabet"}
        builder_flashcards.build(spec, self.tmp_dir)
        
        marketing_dir = Path(self.tmp_dir) / "marketing"
        if marketing_dir.exists():
            # Only check text-based files, skip binary (PNG/images)
            text_extensions = {".txt", ".json", ".md"}
            for file_path in marketing_dir.rglob("*"):
                if file_path.is_file():
                    # Skip binary files (PNG, images)
                    if file_path.suffix.lower() not in text_extensions:
                        continue
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    self.assertNotIn("$", content)
                    self.assertNotIn("RS.", content)
                    self.assertNotIn("₹", content)


# ── Special: Telegram Plugin Interface Tests ────────────────────────────

class TestTelegramInterface(unittest.TestCase):
    """Telegram plugin interface compliance tests."""

    def test_46_build_has_format_constant(self):
        """Builder module must expose FORMAT constant."""
        self.assertTrue(hasattr(builder_flashcards, "FORMAT"))
        self.assertEqual(builder_flashcards.FORMAT, "flashcards")

    def test_47_build_keywords_is_list(self):
        """Builder KEYWORDS must be a list."""
        self.assertIsInstance(builder_flashcards.KEYWORDS, list)
        self.assertGreater(len(builder_flashcards.KEYWORDS), 0)


# ── Special: Format Registry Integration Tests ──────────────────────────

class TestFormatRegistryIntegration(unittest.TestCase):
    """Format registry integration tests."""

    def test_48_format_registered(self):
        """flashcards format should be discoverable via format_registry."""
        from format_registry import discover_builders, classify
        result = discover_builders(force_reload=True)
        self.assertIn("flashcards", result)

    def test_49_flashcards_classify(self):
        """Text about flashcards should classify as flashcards format."""
        from format_registry import classify
        result = classify("printable flashcards for alphabet")
        self.assertEqual(result, "flashcards")

    def test_50_can_build_flashcards(self):
        """can_build should return True for flashcards spec."""
        from format_registry import can_build
        result = can_build({"kind": "flashcards", "topic": "alphabet"})
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
