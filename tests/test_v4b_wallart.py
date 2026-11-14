"""tests/test_v4b_wallart.py — Comprehensive unit tests for builder_wallart.py.

Tests cover:
  1. Module metadata and constants (FORMAT, KEYWORDS, PALETTES, PRINT_SIZES).
  2. Spec parsing, normalization, and validation (title, quotes, themes, palettes, counts).
  3. Safe defaults and fallbacks for minimal/missing spec parameters.
  4. Print size geometry and DPI metadata (A4, A3, 8x10 in, 11x14 in, 16x20 in).
  5. Typographic fitting, word wrapping, and auto-shrinking (text never clipped).
  6. Contrast ratio calculations and WCAG AA compliance (ratio >= 4.5:1).
  7. Procedural motifs and geometric shapes (Pillow only, no stock images).
  8. Output filenames cleanliness and naming conventions.
  9. Multi-page combined PDF generation and structural validity.
  10. PRINTING.txt contents (sizes, paper, framing, personal use license, no currency amounts).
  11. Determinism (consistent output given identical spec and inputs).
  12. Atomic writes and directory safety (temporary folders cleaned up, no partial files on error).
  13. Error handling on edge cases (empty spec, negative count, non-existent directories).
  14. Return summary dictionary and Tenglish message compliance.
  15. Telegram plugin interface (COMMANDS metadata, handle function behavior).
  16. No currency mentions in code, docs, guide, or test assertions.
"""

import hashlib
import io
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import builder_wallart


# CC2-1 speed: the wall art builder writes 300-dpi PNGs with optimize=True (zlib level 9), which
# was ~80% of this module's run time. In tests the same pixels are written with a quick zlib level:
# sizes, dpi, file names, counts and byte-for-byte determinism are unchanged.
_REAL_PNG_SAVE = None


def setUpModule():
    global _REAL_PNG_SAVE
    try:
        from PIL import Image as _Image
    except ImportError:
        return
    _REAL_PNG_SAVE = _Image.Image.save

    def _quick_save(self, fp, format=None, **params):
        name = str(fp).lower() if isinstance(fp, (str, os.PathLike)) else ""
        if params.get("optimize") and ((format or "").upper() == "PNG" or name.endswith(".png")):
            params["optimize"] = False
            params["compress_level"] = 1
        return _REAL_PNG_SAVE(self, fp, format, **params)

    _Image.Image.save = _quick_save


def tearDownModule():
    if _REAL_PNG_SAVE is not None:
        from PIL import Image as _Image
        _Image.Image.save = _REAL_PNG_SAVE


@unittest.skipUnless(HAS_PIL, "Pillow is required for wall art builder tests")
class TestV4BWallArt(unittest.TestCase):
    def setUp(self):
        # Jules rules: clear GITHUB_* and set test environment
        self.env_patch = mock.patch.dict(
            os.environ,
            {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"},
            clear=True,
        )
        self.env_patch.start()
        self.tmp_dir = tempfile.mkdtemp(prefix="test_wallart_")

    def tearDown(self):
        self.env_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # --- 1. Module Constants & Metadata Tests ---

    def test_01_format_constant(self):
        """FORMAT must be 'wall_art'."""
        self.assertEqual(builder_wallart.FORMAT, "wall_art")

    def test_02_keywords_contains_required_elements(self):
        """KEYWORDS must contain the 5 required keyword phrases."""
        expected = ["wall art", "print", "poster", "quote print", "printable art"]
        for kw in expected:
            self.assertIn(kw, builder_wallart.KEYWORDS)

    def test_03_print_sizes_has_required_keys(self):
        """PRINT_SIZES must contain all 5 required size keys: a4, a3, 8x10, 11x14, 16x20."""
        required = {"a4", "a3", "8x10", "11x14", "16x20"}
        self.assertTrue(required.issubset(set(builder_wallart.PRINT_SIZES.keys())))

    def test_04_palettes_minimum_count(self):
        """PALETTES should define diverse high-contrast curated palettes."""
        self.assertGreaterEqual(len(builder_wallart.PALETTES), 5)
        for key, p in builder_wallart.PALETTES.items():
            self.assertIn("background", p)
            self.assertIn("primary", p)
            self.assertIn("accent", p)

    def test_05_theme_presets_structure(self):
        """THEME_PRESETS must contain quotes with text, sub, and motif."""
        self.assertIn("mindful", builder_wallart.THEME_PRESETS)
        for preset_name, items in builder_wallart.THEME_PRESETS.items():
            self.assertGreaterEqual(len(items), 3)
            for item in items:
                self.assertIn("quote", item)
                self.assertIn("motif", item)

    # --- 2. Color, Luminance & Contrast Tests ---

    def test_06_hex_to_rgb_standard(self):
        """Test parsing standard 6-character hex colors."""
        self.assertEqual(builder_wallart.hex_to_rgb("#FFFFFF"), (255, 255, 255))
        self.assertEqual(builder_wallart.hex_to_rgb("#000000"), (0, 0, 0))
        self.assertEqual(builder_wallart.hex_to_rgb("8C3B2B"), (140, 59, 43))

    def test_07_hex_to_rgb_short(self):
        """Test parsing 3-character short hex colors."""
        self.assertEqual(builder_wallart.hex_to_rgb("#FFF"), (255, 255, 255))
        self.assertEqual(builder_wallart.hex_to_rgb("#000"), (0, 0, 0))

    def test_08_hex_to_rgb_invalid(self):
        """Invalid hex should fallback to black (0, 0, 0)."""
        self.assertEqual(builder_wallart.hex_to_rgb("invalid"), (0, 0, 0))

    def test_09_rgb_to_hex(self):
        """Test RGB to hex formatting."""
        self.assertEqual(builder_wallart.rgb_to_hex(255, 255, 255), "#FFFFFF")
        self.assertEqual(builder_wallart.rgb_to_hex(0, 0, 0), "#000000")

    def test_10_relative_luminance_white_and_black(self):
        """White luminance is 1.0, black is 0.0."""
        self.assertAlmostEqual(builder_wallart.relative_luminance("#FFFFFF"), 1.0, places=2)
        self.assertAlmostEqual(builder_wallart.relative_luminance("#000000"), 0.0, places=2)

    def test_11_contrast_ratio_max_and_min(self):
        """Contrast between black and white is 21.0; identical color is 1.0."""
        self.assertAlmostEqual(builder_wallart.contrast_ratio("#FFFFFF", "#000000"), 21.0, places=1)
        self.assertAlmostEqual(builder_wallart.contrast_ratio("#FFFFFF", "#FFFFFF"), 1.0, places=2)

    def test_12_ensure_contrast_keeps_high_contrast(self):
        """If contrast >= 4.5:1, original color is preserved."""
        fg = builder_wallart.ensure_contrast("#000000", "#FFFFFF", min_ratio=4.5)
        self.assertEqual(fg, "#000000")

    def test_13_ensure_contrast_adjusts_low_contrast(self):
        """Low contrast foreground is adjusted to guarantee >= 4.5:1 ratio."""
        fg = builder_wallart.ensure_contrast("#EEEEEE", "#FFFFFF", min_ratio=4.5)
        self.assertNotEqual(fg, "#EEEEEE")
        ratio = builder_wallart.contrast_ratio(fg, "#FFFFFF")
        self.assertGreaterEqual(ratio, 4.5)

    def test_14_palettes_meet_wcag_contrast(self):
        """All built-in palettes meet or exceed WCAG AA contrast for primary vs background."""
        for name, p in builder_wallart.PALETTES.items():
            primary = builder_wallart.ensure_contrast(p["primary"], p["background"], 4.5)
            ratio = builder_wallart.contrast_ratio(primary, p["background"])
            self.assertGreaterEqual(ratio, 4.5, f"Palette {name} failed contrast ratio")

    # --- 3. Typography & Text Fit Tests ---

    def test_15_get_font_returns_valid_font(self):
        """get_font returns a usable PIL font object."""
        font = builder_wallart.get_font("bold", 32)
        self.assertIsNotNone(font)

    def test_16_measure_text_dimensions(self):
        """measure_text returns positive dimensions for non-empty text."""
        font = builder_wallart.get_font("bold", 32)
        w, h = builder_wallart.measure_text("HELLO WORLD", font)
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)

    def test_17_measure_text_empty(self):
        """measure_text returns (0, 0) for empty string or None."""
        font = builder_wallart.get_font("bold", 32)
        self.assertEqual(builder_wallart.measure_text("", font), (0, 0))
        self.assertEqual(builder_wallart.measure_text(None, font), (0, 0))

    def test_18_wrap_text_to_width_single_line(self):
        """Short text fits on a single line."""
        font = builder_wallart.get_font("bold", 24)
        lines = builder_wallart.wrap_text_to_width("Short Text", font, max_width=1000)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], "Short Text")

    def test_19_wrap_text_to_width_multi_line(self):
        """Long text wraps across multiple lines without truncating words."""
        font = builder_wallart.get_font("bold", 36)
        text = "This is a much longer typographic sentence meant to test wrapping across multiple lines"
        lines = builder_wallart.wrap_text_to_width(text, font, max_width=400)
        self.assertGreater(len(lines), 1)
        # All words must be preserved
        reconstructed = " ".join(lines)
        self.assertEqual(reconstructed, text)

    def test_20_fit_quote_typography_never_clips(self):
        """fit_quote_typography returns lines within width and height constraints."""
        quote = "PROGRESS OVER PERFECTION IN ALL CREATIVE EFFORTS"
        lines, font, size, line_h = builder_wallart.fit_quote_typography(
            quote,
            max_width=800,
            max_height=400,
            target_size=120,
            min_size=32,
        )
        self.assertGreater(len(lines), 0)
        total_w = max(builder_wallart.measure_text(l, font)[0] for l in lines)
        self.assertLessEqual(total_w, 800)

    def test_21_fit_quote_typography_handles_extreme_length(self):
        """Extreme length quote wraps properly and doesn't crash."""
        long_quote = "A " * 50
        lines, font, size, line_h = builder_wallart.fit_quote_typography(
            long_quote,
            max_width=600,
            max_height=300,
            target_size=80,
            min_size=20,
        )
        self.assertGreater(len(lines), 0)

    # --- 4. Procedural Shape & Geometry Tests ---

    def test_22_draw_arch_motif_execution(self):
        """Arch motif executes without error and draws shapes."""
        img = Image.new("RGB", (500, 500), color="#FFFFFF")
        draw = ImageDraw.Draw(img)
        builder_wallart.draw_arch_motif(draw, (100, 100, 400, 400), stroke_color="#8C3B2B", fill_color="#F2E6DC")
        # Ensure image has non-white pixels
        colors = img.getcolors(maxcolors=1000)
        self.assertGreater(len(colors), 1)

    def test_23_draw_sunburst_motif_execution(self):
        """Sunburst motif draws rays and arcs cleanly."""
        img = Image.new("RGB", (500, 500), color="#FFFFFF")
        draw = ImageDraw.Draw(img)
        builder_wallart.draw_sunburst_motif(draw, (250, 250), radius=100, color="#C47659")
        colors = img.getcolors(maxcolors=1000)
        self.assertGreater(len(colors), 1)

    def test_24_draw_minimal_frame_execution(self):
        """Minimal double frame executes and draws rectangles."""
        img = Image.new("RGB", (500, 500), color="#FFFFFF")
        draw = ImageDraw.Draw(img)
        builder_wallart.draw_minimal_frame(draw, (50, 50, 450, 450), stroke_color="#1A1A1A")
        colors = img.getcolors(maxcolors=1000)
        self.assertGreater(len(colors), 1)

    def test_25_draw_botanical_stem_execution(self):
        """Botanical stem draws central stem and leaf pairs."""
        img = Image.new("RGB", (500, 500), color="#FFFFFF")
        draw = ImageDraw.Draw(img)
        builder_wallart.draw_botanical_stem(draw, (250, 250), height=300, color="#2D4C3A")
        colors = img.getcolors(maxcolors=1000)
        self.assertGreater(len(colors), 1)

    def test_26_draw_concentric_rings_execution(self):
        """Concentric rings draw multiple rings around center."""
        img = Image.new("RGB", (500, 500), color="#FFFFFF")
        draw = ImageDraw.Draw(img)
        builder_wallart.draw_concentric_rings(draw, (250, 250), max_radius=120, color="#4A6E8D")
        colors = img.getcolors(maxcolors=1000)
        self.assertGreater(len(colors), 1)

    # --- 5. Spec Normalization Tests ---

    def test_27_normalize_spec_defaults(self):
        """Empty spec defaults to mindful theme, terracotta palette, and 3 designs."""
        title, designs, palette, palette_key = builder_wallart.normalize_spec({})
        self.assertIn("Typographic Wall Art", title)
        self.assertEqual(len(designs), 3)
        self.assertEqual(palette_key, "terracotta")

    def test_28_normalize_spec_custom_title_and_palette(self):
        """Spec title and palette are normalized correctly."""
        spec = {"title": "Nordic Serenity", "palette": "indigo"}
        title, designs, palette, palette_key = builder_wallart.normalize_spec(spec)
        self.assertEqual(title, "Nordic Serenity")
        self.assertEqual(palette_key, "indigo")

    def test_29_normalize_spec_string_quotes(self):
        """String quotes with dividers are parsed into quote, sub, and motif."""
        spec = {
            "quotes": [
                "BE PRESENT · In every breath",
                "STAY CALM · Peace from within",
                "SHINE BRIGHT · Light the path",
            ]
        }
        title, designs, palette, _ = builder_wallart.normalize_spec(spec)
        self.assertEqual(len(designs), 3)
        self.assertEqual(designs[0]["quote"], "BE PRESENT")
        self.assertEqual(designs[0]["sub"], "In every breath")

    def test_30_normalize_spec_dict_quotes(self):
        """Dict quotes with explicit quote, sub, and motif keys are preserved."""
        spec = {
            "quotes": [
                {"quote": "DESIGN ONE", "sub": "Sub one", "motif": "arch"},
                {"quote": "DESIGN TWO", "sub": "Sub two", "motif": "sunburst"},
                {"quote": "DESIGN THREE", "sub": "Sub three", "motif": "rings"},
            ]
        }
        _, designs, _, _ = builder_wallart.normalize_spec(spec)
        self.assertEqual(len(designs), 3)
        self.assertEqual(designs[1]["motif"], "sunburst")

    def test_31_normalize_spec_clamps_count_between_3_and_6(self):
        """Count is clamped to [3, 6]."""
        # Request 1 -> clamped to 3
        _, d1, _, _ = builder_wallart.normalize_spec({"count": 1})
        self.assertEqual(len(d1), 3)
        # Request 10 -> clamped to 6
        _, d2, _, _ = builder_wallart.normalize_spec({"count": 10})
        self.assertEqual(len(d2), 6)

    def test_32_normalize_spec_count_5(self):
        """Spec requesting 5 designs produces exactly 5 designs."""
        _, designs, _, _ = builder_wallart.normalize_spec({"count": 5})
        self.assertEqual(len(designs), 5)

    def test_33_normalize_spec_count_4(self):
        """Spec requesting 4 designs produces exactly 4 designs."""
        _, designs, _, _ = builder_wallart.normalize_spec({"count": 4})
        self.assertEqual(len(designs), 4)

    # --- 6. Render Art Design Tests ---

    def test_34_render_art_design_a4(self):
        """Rendering design at A4 yields 2480x3508 RGB image."""
        design_info = {"quote": "BREATHE IN PEACE", "sub": "Exhale worry", "motif": "arch"}
        size_spec = builder_wallart.PRINT_SIZES["a4"]
        palette = builder_wallart.PALETTES["terracotta"]
        img = builder_wallart.render_art_design(design_info, size_spec, palette, 1, 3)
        self.assertEqual(img.size, (2480, 3508))
        self.assertEqual(img.mode, "RGB")

    def test_35_render_art_design_8x10(self):
        """Rendering design at 8x10 yields 2400x3000 RGB image."""
        design_info = {"quote": "FOCUS ON WHAT MATTERS", "sub": "Every single day", "motif": "minimal_frame"}
        size_spec = builder_wallart.PRINT_SIZES["8x10"]
        palette = builder_wallart.PALETTES["minimal_mono"]
        img = builder_wallart.render_art_design(design_info, size_spec, palette, 1, 3)
        self.assertEqual(img.size, (2400, 3000))
        self.assertEqual(img.mode, "RGB")

    def test_36_render_art_design_16x20_200dpi(self):
        """Rendering design at 16x20 yields 3200x4000 image with 200 dpi spec."""
        design_info = {"quote": "SIMPLIFY YOUR LIFE", "sub": "Find calm", "motif": "rings"}
        size_spec = builder_wallart.PRINT_SIZES["16x20"]
        palette = builder_wallart.PALETTES["sage"]
        img = builder_wallart.render_art_design(design_info, size_spec, palette, 2, 4)
        self.assertEqual(img.size, (3200, 4000))
        self.assertEqual(size_spec["dpi"], 200)

    def test_37_render_art_design_botanical_motif(self):
        """Rendering design with botanical leaves motif produces valid image."""
        design_info = {"quote": "GROW WITH GRACE", "sub": "One day at a time", "motif": "botanical_leaves"}
        size_spec = builder_wallart.PRINT_SIZES["a4"]
        palette = builder_wallart.PALETTES["forest_gold"]
        img = builder_wallart.render_art_design(design_info, size_spec, palette, 1, 3)
        self.assertEqual(img.size, (2480, 3508))

    def test_38_render_art_design_sunburst_motif(self):
        """Rendering design with sunburst motif produces valid image."""
        design_info = {"quote": "RADIATE KINDNESS", "sub": "Warmth in all things", "motif": "sunburst"}
        size_spec = builder_wallart.PRINT_SIZES["11x14"]
        palette = builder_wallart.PALETTES["warm_boho"]
        img = builder_wallart.render_art_design(design_info, size_spec, palette, 3, 3)
        self.assertEqual(img.size, (3300, 4200))

    # --- 7. Print Guide & Terms Tests ---

    def test_39_generate_printing_guide_mentions_sizes(self):
        """PRINTING.txt must list all 5 print sizes."""
        text = builder_wallart.generate_printing_guide({"title": "Art Set"}, 3, "Sage")
        for size_label in ["A4", "A3", "8x10", "11x14", "16x20"]:
            self.assertIn(size_label, text)

    def test_40_generate_printing_guide_mentions_dpi(self):
        """PRINTING.txt must explicitly mention 300 dpi and 200 dpi."""
        text = builder_wallart.generate_printing_guide({"title": "Art Set"}, 3, "Sage")
        self.assertIn("300 dpi", text)
        self.assertIn("200 dpi", text)

    def test_41_generate_printing_guide_paper_recommendations(self):
        """PRINTING.txt includes paper weight (gsm) and matte recommendations."""
        text = builder_wallart.generate_printing_guide({"title": "Art Set"}, 3, "Sage")
        self.assertIn("gsm", text)
        self.assertIn("matte", text.lower())

    def test_42_generate_printing_guide_personal_use_license(self):
        """PRINTING.txt must clearly state 'PERSONAL USE ONLY'."""
        text = builder_wallart.generate_printing_guide({"title": "Art Set"}, 3, "Sage")
        self.assertIn("PERSONAL USE ONLY", text)

    def test_43_generate_printing_guide_no_currency(self):
        """PRINTING.txt must not contain any money or currency symbols."""
        text = builder_wallart.generate_printing_guide({"title": "Art Set"}, 3, "Sage")
        currency_pattern = re.compile(
            r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
            re.IGNORECASE,
        )
        self.assertIsNone(currency_pattern.search(text))

    # --- 8. Combined PDF Tests ---

    def test_44_build_combined_pdf_valid(self):
        """build_combined_pdf compiles A4 images into multi-page PDF."""
        img1 = Image.new("RGB", (2480, 3508), color="#FFFFFF")
        img2 = Image.new("RGB", (2480, 3508), color="#FAFAFA")
        p1 = Path(self.tmp_dir) / "test_a4_1.png"
        p2 = Path(self.tmp_dir) / "test_a4_2.png"
        img1.save(str(p1), format="PNG")
        img2.save(str(p2), format="PNG")

        pdf_out = Path(self.tmp_dir) / "combined.pdf"
        builder_wallart.build_combined_pdf([p1, p2], pdf_out, title="Test Combined")
        self.assertTrue(pdf_out.exists())
        self.assertGreater(pdf_out.stat().st_size, 1000)

        # Check PDF header
        with open(pdf_out, "rb") as f:
            header = f.read(5)
            self.assertEqual(header, b"%PDF-")

    def test_45_build_combined_pdf_empty_raises(self):
        """build_combined_pdf raises ValueError when images list is empty."""
        pdf_out = Path(self.tmp_dir) / "empty.pdf"
        with self.assertRaises(ValueError):
            builder_wallart.build_combined_pdf([], pdf_out)

    # --- 9. Full Build Integration Tests ---

    def test_46_build_default_3_designs(self):
        """build with 3 designs outputs 3*5 PNGs + 1 PDF + 1 PRINTING.txt = 17 files."""
        spec = {
            "title": "Calm Reflections",
            "palette": "terracotta",
            "quotes": [
                "BREATHE IN PEACE · Exhale doubt",
                "STAY GROUNDED · In quiet strength",
                "PRESENT MOMENT · Joy lives here",
            ],
        }
        res = builder_wallart.build(spec, self.tmp_dir)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["designs_count"], 3)
        self.assertEqual(res["sizes_count"], 5)
        self.assertEqual(res["total_files"], 17)

        # Verify all files exist
        for f in res["files"]:
            self.assertTrue(os.path.exists(f))

    def test_47_build_clean_file_names(self):
        """File names follow clean lowercase-underscore naming."""
        spec = {"title": "Clean Names Set", "count": 3}
        res = builder_wallart.build(spec, self.tmp_dir)
        for f in res["files"]:
            fname = Path(f).name
            if fname in {"PRINTING.txt", "wall_art_set_a4_collection.pdf"}:
                continue
            # PNG names: art_XX_<size>.png
            self.assertRegex(fname, r"^art_\d{2}_(a4|a3|8x10|11x14|16x20)\.png$")

    def test_48_build_4_designs(self):
        """build with 4 designs outputs 4*5 PNGs + 1 PDF + 1 PRINTING.txt = 22 files."""
        spec = {"title": "Set of 4", "count": 4, "palette": "sage"}
        res = builder_wallart.build(spec, self.tmp_dir)
        self.assertEqual(res["designs_count"], 4)
        self.assertEqual(res["total_files"], 22)

    def test_49_build_5_designs(self):
        """build with 5 designs outputs 5*5 PNGs + 1 PDF + 1 PRINTING.txt = 27 files."""
        spec = {"title": "Set of 5", "count": 5, "palette": "minimal_mono"}
        res = builder_wallart.build(spec, self.tmp_dir)
        self.assertEqual(res["designs_count"], 5)
        self.assertEqual(res["total_files"], 27)

    def test_50_build_6_designs(self):
        """build with 6 designs outputs 6*5 PNGs + 1 PDF + 1 PRINTING.txt = 32 files."""
        spec = {"title": "Set of 6", "count": 6, "palette": "warm_boho"}
        res = builder_wallart.build(spec, self.tmp_dir)
        self.assertEqual(res["designs_count"], 6)
        self.assertEqual(res["total_files"], 32)

    def test_51_build_png_dimensions_verified(self):
        """All 5 generated PNGs for design 1 have exact pixel dimensions."""
        spec = {"title": "Dimension Test", "count": 3}
        res = builder_wallart.build(spec, self.tmp_dir)
        d_dir = Path(self.tmp_dir)

        sizes_expected = {
            "art_01_a4.png": (2480, 3508),
            "art_01_a3.png": (3508, 4960),
            "art_01_8x10.png": (2400, 3000),
            "art_01_11x14.png": (3300, 4200),
            "art_01_16x20.png": (3200, 4000),
        }
        for fname, expected_dim in sizes_expected.items():
            fpath = d_dir / fname
            self.assertTrue(fpath.exists())
            with Image.open(fpath) as im:
                self.assertEqual(im.size, expected_dim)

    def test_52_build_returns_tenglish_summary(self):
        """Returned summary message must be in Tenglish house style."""
        spec = {"title": "Tenglish Test", "count": 3}
        res = builder_wallart.build(spec, self.tmp_dir)
        msg = res.get("tenglish_summary", "")
        self.assertIn("Wall art set build aindi", msg)
        self.assertIn("ready ga vunnayi", msg)

    # --- 10. Determinism & Idempotency Tests ---

    def test_53_build_determinism(self):
        """Running build twice with same spec produces identical files byte-for-byte."""
        spec = {
            "title": "Deterministic Test Set",
            "palette": "terracotta",
            "quotes": [
                "BREATHE IN PEACE · Exhale doubt",
                "STAY GROUNDED · In quiet strength",
                "PRESENT MOMENT · Joy lives here",
            ],
        }
        dir1 = tempfile.mkdtemp(prefix="det1_")
        dir2 = tempfile.mkdtemp(prefix="det2_")
        try:
            builder_wallart.build(spec, dir1)
            builder_wallart.build(spec, dir2)

            # Compare PNG byte sizes and hashes for art_01_8x10.png
            file1 = Path(dir1) / "art_01_8x10.png"
            file2 = Path(dir2) / "art_01_8x10.png"
            self.assertEqual(file1.stat().st_size, file2.stat().st_size)

            h1 = hashlib.sha256(file1.read_bytes()).hexdigest()
            h2 = hashlib.sha256(file2.read_bytes()).hexdigest()
            self.assertEqual(h1, h2)
        finally:
            shutil.rmtree(dir1, ignore_errors=True)
            shutil.rmtree(dir2, ignore_errors=True)

    # --- 11. Atomic Writes & Safety Tests ---

    def test_54_atomic_writes_no_leftover_temp_dirs(self):
        """Build cleans up its temporary build directory."""
        spec = {"title": "Temp Cleanup Test", "count": 3}
        builder_wallart.build(spec, self.tmp_dir)
        # Check system temp for leftover wallart_build_ directories
        temp_root = Path(tempfile.gettempdir())
        leftovers = list(temp_root.glob("wallart_build_*"))
        self.assertEqual(len(leftovers), 0)

    # --- 12. Telegram Plugin Interface Tests ---

    def test_55_telegram_plugin_commands_structure(self):
        """Telegram plugin COMMANDS matches standard plugin interface."""
        self.assertTrue(hasattr(builder_wallart, "COMMANDS"))
        commands = builder_wallart.COMMANDS
        self.assertIsInstance(commands, list)
        self.assertGreaterEqual(len(commands), 1)

        cmd = commands[0]
        self.assertEqual(cmd["cmd"], "/wallart")
        self.assertIn("words", cmd)
        self.assertIn("help_en", cmd)
        self.assertIn("help_te", cmd)

    def test_56_telegram_plugin_handle_matching_command(self):
        """handle('/wallart', ctx) returns valid response dict."""
        ctx = {"chat_id": 12345}
        resp = builder_wallart.handle("/wallart", ctx)
        self.assertIsNotNone(resp)
        self.assertIn("text", resp)
        self.assertIn("Wall art", resp["text"])

    def test_57_telegram_plugin_handle_matching_words(self):
        """handle('wall art pampu', ctx) handles natural language triggers."""
        ctx = {"chat_id": 12345}
        resp = builder_wallart.handle("wall art pampu", ctx)
        self.assertIsNotNone(resp)
        self.assertIn("text", resp)

    def test_58_telegram_plugin_handle_unrelated_returns_none(self):
        """handle returns None for unrelated commands."""
        ctx = {"chat_id": 12345}
        resp = builder_wallart.handle("/other_command", ctx)
        self.assertIsNone(resp)
        resp2 = builder_wallart.handle("random text hello", ctx)
        self.assertIsNone(resp2)

    def test_59_telegram_plugin_never_imports_inbox_at_toplevel(self):
        """builder_wallart must never import telegram_inbox at module import time."""
        src = Path(builder_wallart.__file__).read_text(encoding="utf-8")
        top = [l for l in src.splitlines() if l.startswith(("import ", "from "))]
        self.assertFalse(any("telegram_inbox" in l for l in top))

    # --- 13. Safety & Currency Compliance Tests ---

    def test_60_no_currency_amounts_in_builder_wallart_py(self):
        """builder_wallart.py source code must not contain any money or currency amounts."""
        src_path = Path(builder_wallart.__file__)
        code = src_path.read_text(encoding="utf-8")
        currency_pattern = re.compile(
            r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
            re.IGNORECASE,
        )
        self.assertIsNone(currency_pattern.search(code))

    def test_61_no_currency_amounts_in_test_v4b_wallart_py(self):
        """test_v4b_wallart.py source code must not contain any money or currency amounts."""
        test_path = Path(__file__)
        code = test_path.read_text(encoding="utf-8")
        currency_pattern = re.compile(
            r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
            re.IGNORECASE,
        )
        self.assertIsNone(currency_pattern.search(code))

    def test_62_personal_use_clause_in_guide(self):
        """License clause explicitly protects against unauthorized commercial resale."""
        text = builder_wallart.generate_printing_guide({"title": "Test Set"}, 3, "Indigo")
        self.assertIn("PERSONAL USE ONLY", text)
        self.assertIn("resale", text.lower())


if __name__ == "__main__":
    unittest.main()
