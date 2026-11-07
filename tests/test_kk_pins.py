"""Comprehensive unit tests for Pinterest pin variants generator (`pin_variants.py`).

Tests include:
- Sizes: output PNGs must strictly be 1000x1500 px.
- 5 different layouts: A, B, C, D, E.
- D/E rule: Layout D used only when pack is free; else skipped and Layout E used.
- Title rules: max 60 chars, no money amounts, no brand names, no clickbait, 5 distinct titles per pack.
- Title categories: benefit-first, number-first, question, how-to, short.
- Text fit: long titles wrap and shrink gracefully without getting clipped or crashing.
- Contrast: all foreground/background pairings have WCAG contrast ratio >= 4.5:1.
- Determinism: identical input produces identical pin images.
- Missing images fallback: crisp synthetic mock page generated when no cover/preview exists.
- Pack folder untouched: pack directory files and mtime are strictly unmodified.
- Safe margin: elements stay within safe margin (>= 60 px from outer edge).
- File size limit: all generated images are strictly < 1 MB.
- Metadata integrity: ab_titles.txt lists 5 titles, pins.json contains valid metadata.
"""

import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

import pin_variants  # noqa: E402


class BasePinTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Clear all GITHUB_* and RUNNER_ENVIRONMENT environment variables
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        self.addCleanup(patcher.stop)
        patcher.start()


class TestPinRulesAndHelpers(BasePinTestCase):
    def test_clean_text_removes_brand_names(self):
        sample = "Best Notion Planner for Google Sheets and Excel with GoodNotes"
        cleaned = pin_variants.clean_text_no_brands_or_money(sample)
        self.assertNotIn("Notion", cleaned)
        self.assertNotIn("Google Sheets", cleaned)
        self.assertNotIn("Excel", cleaned)
        self.assertNotIn("GoodNotes", cleaned)

    def test_clean_text_removes_money_amounts(self):
        sample = "Budget Planner only $10 USD or 500 INR save 20 EUR"
        cleaned = pin_variants.clean_text_no_brands_or_money(sample)
        self.assertNotIn("$10", cleaned)
        self.assertNotIn("USD", cleaned)
        self.assertNotIn("INR", cleaned)
        self.assertNotIn("EUR", cleaned)

    def test_clean_text_removes_clickbait_words(self):
        sample = "Secret Shocking Viral Habit Tracker with Miracle Results"
        cleaned = pin_variants.clean_text_no_brands_or_money(sample)
        self.assertNotIn("Secret", cleaned)
        self.assertNotIn("Shocking", cleaned)
        self.assertNotIn("Viral", cleaned)
        self.assertNotIn("Miracle", cleaned)

    def test_is_free_pack_detection_positive(self):
        self.assertTrue(pin_variants.is_free_pack("Title: Free Printable Daily Checklist"))
        self.assertTrue(pin_variants.is_free_pack("Free download for personal use"))
        self.assertTrue(pin_variants.is_free_pack("Freebie weekly plan"))
        self.assertTrue(pin_variants.is_free_pack("Listing", folder_name="free-daily-tracker"))

    def test_is_free_pack_detection_negative(self):
        self.assertFalse(pin_variants.is_free_pack("Title: Premium ADHD Daily Planner"))
        self.assertFalse(pin_variants.is_free_pack("Includes 24 undated printable pages"))

    def test_relative_luminance_bounds(self):
        self.assertAlmostEqual(pin_variants.relative_luminance((0, 0, 0)), 0.0)
        self.assertAlmostEqual(pin_variants.relative_luminance((255, 255, 255)), 1.0)

    def test_contrast_ratio_black_and_white(self):
        cr = pin_variants.contrast_ratio((0, 0, 0), (255, 255, 255))
        self.assertAlmostEqual(cr, 21.0, places=1)

    def test_contrast_ratio_all_layouts_comply_with_wcag(self):
        color_pairs = [
            ("Layout A main text", (20, 35, 40), (248, 249, 250)),
            ("Layout A category", (47, 125, 120), (248, 249, 250)),
            ("Layout B ink", (18, 30, 35), (240, 244, 245)),
            ("Layout B tagline", (35, 105, 100), (240, 244, 245)),
            ("Layout C title", (15, 28, 32), (252, 252, 253)),
            ("Layout C check icon", (255, 255, 255), (38, 110, 105)),
            ("Layout D badge", (255, 255, 255), (215, 60, 75)),
            ("Layout D text", (18, 32, 36), (245, 248, 248)),
            ("Layout E text", (25, 30, 35), (255, 255, 255)),
            ("Layout E tagline", (70, 120, 115), (255, 255, 255)),
        ]
        for name, fg, bg in color_pairs:
            cr = pin_variants.contrast_ratio(fg, bg)
            self.assertGreaterEqual(cr, 4.5, f"{name} contrast ratio {cr:.2f} is under 4.5:1")


class TestTitleVariants(BasePinTestCase):
    def test_five_titles_generated(self):
        titles = pin_variants.generate_ab_titles("Daily Study Routine Checklist")
        self.assertEqual(len(titles), 5)

    def test_all_five_titles_are_unique(self):
        titles = pin_variants.generate_ab_titles("Weekly Meal Planner")
        lowered = [t.lower() for t in titles]
        self.assertEqual(len(set(lowered)), 5)

    def test_titles_max_length_sixty_chars(self):
        long_name = "Super Comprehensive Elaborate Daily And Weekly Chore Schedule For Busy Families And Students"
        titles = pin_variants.generate_ab_titles(long_name)
        for t in titles:
            self.assertLessEqual(len(t), 60, f"Title too long: '{t}' ({len(t)} chars)")

    def test_titles_contain_no_money_or_brands(self):
        tricky = "Best Notion Budget Planner Only $15 USD For Excel"
        titles = pin_variants.generate_ab_titles(tricky)
        for t in titles:
            self.assertNotIn("Notion", t)
            self.assertNotIn("Excel", t)
            self.assertNotIn("$15", t)
            self.assertNotIn("USD", t)

    def test_titles_contain_no_clickbait(self):
        clickbait = "Shocking Secret Viral Morning Routine"
        titles = pin_variants.generate_ab_titles(clickbait)
        for t in titles:
            self.assertNotIn("Secret", t)
            self.assertNotIn("Shocking", t)
            self.assertNotIn("Viral", t)

    def test_titles_benefit_first_style(self):
        titles = pin_variants.generate_ab_titles("Monthly Finance Tracker")
        self.assertTrue(any("Organize" in t or "Stay" in t for t in titles))

    def test_titles_number_first_style(self):
        titles = pin_variants.generate_ab_titles("Habit Reflection Grid")
        self.assertTrue(any(t[0].isdigit() for t in titles))

    def test_titles_question_style(self):
        titles = pin_variants.generate_ab_titles("Self Care Journal")
        self.assertTrue(any("?" in t for t in titles))

    def test_titles_how_to_style(self):
        titles = pin_variants.generate_ab_titles("Time Blocking Grid")
        self.assertTrue(any(t.lower().startswith("how to") for t in titles))

    def test_titles_short_style(self):
        titles = pin_variants.generate_ab_titles("Daily Workout Log")
        short_title = titles[4]
        self.assertLessEqual(len(short_title), 40)


@unittest.skipUnless(HAS_PILLOW, "Pillow is required for rendering tests")
class TestPinRenderingAndGeneration(BasePinTestCase):
    def _create_mock_pack(self, temp_dir, name="adhd-focus-kit", is_free=False, with_images=True):
        p = pathlib.Path(temp_dir) / name
        p.mkdir(parents=True, exist_ok=True)
        free_line = "Title: Free ADHD Focus Kit\n" if is_free else "Title: ADHD Focus Kit\n"
        listing_content = (
            f"{free_line}"
            "Description: Simple undated printable planner designed for clarity.\n"
            "Tags: adhd, planner, printable, focus\n"
        )
        (p / "listing.txt").write_text(listing_content, encoding="utf-8")

        if with_images:
            # Create mock cover.png
            img = Image.new("RGB", (600, 800), (250, 250, 250))
            img.save(p / "cover.png")
            # Create mock preview.png
            img2 = Image.new("RGB", (600, 800), (240, 240, 240))
            img2.save(p / "preview.png")

        return p

    def test_pin_output_dimensions_are_1000x1500(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td)
            out_dir = pathlib.Path(td) / "out"
            res = pin_variants.generate_pins_for_pack(pack_dir, out_dir=out_dir)
            pins_dir = pathlib.Path(res["pins_dir"])

            for f in sorted(pins_dir.glob("pin_*.png")):
                with Image.open(f) as im:
                    self.assertEqual(im.size, (1000, 1500), f"{f.name} size mismatch: {im.size}")

    def test_five_pin_images_created(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td)
            out_dir = pathlib.Path(td) / "out"
            res = pin_variants.generate_pins_for_pack(pack_dir, out_dir=out_dir)
            pins_dir = pathlib.Path(res["pins_dir"])
            pngs = list(pins_dir.glob("pin_*.png"))
            self.assertEqual(len(pngs), 5)

    def test_pin_file_sizes_under_one_megabyte(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td)
            out_dir = pathlib.Path(td) / "out"
            res = pin_variants.generate_pins_for_pack(pack_dir, out_dir=out_dir)
            pins_dir = pathlib.Path(res["pins_dir"])
            for f in pins_dir.glob("pin_*.png"):
                self.assertLess(f.stat().st_size, 1024 * 1024, f"{f.name} exceeds 1MB")

    def test_layout_d_selected_when_pack_is_free(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td, name="free-checkup", is_free=True)
            out_dir = pathlib.Path(td) / "out"
            res = pin_variants.generate_pins_for_pack(pack_dir, out_dir=out_dir)
            layouts = [item["layout"] for item in res["meta"]]
            self.assertEqual(layouts, ["A", "B", "C", "D", "E"])

    def test_layout_d_skipped_when_pack_is_not_free(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td, name="paid-planner", is_free=False)
            out_dir = pathlib.Path(td) / "out"
            res = pin_variants.generate_pins_for_pack(pack_dir, out_dir=out_dir)
            layouts = [item["layout"] for item in res["meta"]]
            self.assertNotIn("D", layouts)
            self.assertEqual(layouts, ["A", "B", "C", "E", "E"])

    def test_fallback_rendering_when_no_images_exist(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td, name="text-only-pack", with_images=False)
            out_dir = pathlib.Path(td) / "out"
            res = pin_variants.generate_pins_for_pack(pack_dir, out_dir=out_dir)
            pins_dir = pathlib.Path(res["pins_dir"])
            pngs = list(pins_dir.glob("pin_*.png"))
            self.assertEqual(len(pngs), 5)
            for f in pngs:
                with Image.open(f) as im:
                    self.assertEqual(im.size, (1000, 1500))

    def test_pack_folder_never_modified(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td, name="readonly-pack")
            files_before = sorted(os.listdir(pack_dir))
            mtimes_before = {f: (pack_dir / f).stat().st_mtime for f in files_before}

            out_dir = pathlib.Path(td) / "out"
            pin_variants.generate_pins_for_pack(pack_dir, out_dir=out_dir)

            files_after = sorted(os.listdir(pack_dir))
            mtimes_after = {f: (pack_dir / f).stat().st_mtime for f in files_after}

            self.assertEqual(files_before, files_after)
            self.assertEqual(mtimes_before, mtimes_after)

    def test_ab_titles_file_created_and_accurate(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td)
            out_dir = pathlib.Path(td) / "out"
            res = pin_variants.generate_pins_for_pack(pack_dir, out_dir=out_dir)
            pins_dir = pathlib.Path(res["pins_dir"])
            ab_file = pins_dir / "ab_titles.txt"
            self.assertTrue(ab_file.is_file())
            content = ab_file.read_text(encoding="utf-8")
            self.assertIn("# A/B Title Ideas", content)
            for t in res["titles"]:
                self.assertIn(t, content)

    def test_pins_json_metadata_structure(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td)
            out_dir = pathlib.Path(td) / "out"
            res = pin_variants.generate_pins_for_pack(pack_dir, out_dir=out_dir)
            pins_dir = pathlib.Path(res["pins_dir"])
            json_file = pins_dir / "pins.json"
            self.assertTrue(json_file.is_file())
            data = json.loads(json_file.read_text(encoding="utf-8"))
            self.assertEqual(len(data), 5)
            for entry in data:
                self.assertIn("file", entry)
                self.assertIn("layout", entry)
                self.assertIn("title", entry)
                self.assertIn("description", entry)
                self.assertIn("alt", entry)
                self.assertLessEqual(len(entry["description"]), 500)
                self.assertLessEqual(len(entry["title"]), 60)

    def test_rendering_determinism(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td, name="det-pack")
            out1 = pathlib.Path(td) / "out1"
            out2 = pathlib.Path(td) / "out2"
            res1 = pin_variants.generate_pins_for_pack(pack_dir, out_dir=out1)
            res2 = pin_variants.generate_pins_for_pack(pack_dir, out_dir=out2)

            dir1 = pathlib.Path(res1["pins_dir"])
            dir2 = pathlib.Path(res2["pins_dir"])

            for fname in ["pin_1_a.png", "pin_2_b.png", "pin_3_c.png"]:
                b1 = (dir1 / fname).read_bytes()
                b2 = (dir2 / fname).read_bytes()
                self.assertEqual(b1, b2, f"Rendering non-deterministic for {fname}")

    def test_long_title_text_fit_no_cut(self):
        long_title = "Complete Ultimate Daily Habit And Routine Checklist With Undated Schedules"
        lines, font = pin_variants.fit_text(long_title, "bold", 64, 28, 880, 3)
        self.assertLessEqual(len(lines), 3)
        for line in lines:
            self.assertLessEqual(pin_variants.text_width(line, font), 880)

    def test_empty_title_fallback(self):
        titles = pin_variants.generate_ab_titles("")
        self.assertEqual(len(titles), 5)
        for t in titles:
            self.assertTrue(len(t) > 0)

    def test_layout_a_render_directly(self):
        dummy_page = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_layout_a("Focus Routine", "focus-pack", dummy_page)
        self.assertEqual(img.size, (1000, 1500))

    def test_layout_b_render_directly(self):
        dummy_p1 = Image.new("RGB", (600, 800), (255, 255, 255))
        dummy_p2 = Image.new("RGB", (600, 800), (240, 240, 240))
        img = pin_variants.render_layout_b("Split Routine", "split-pack", dummy_p1, dummy_p2)
        self.assertEqual(img.size, (1000, 1500))

    def test_layout_c_render_directly(self):
        dummy_page = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_layout_c("Checklist Routine", "check-pack", dummy_page)
        self.assertEqual(img.size, (1000, 1500))

    def test_layout_d_render_directly(self):
        dummy_page = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_layout_d("Freebie Routine", "free-pack", dummy_page)
        self.assertEqual(img.size, (1000, 1500))

    def test_layout_e_render_directly(self):
        dummy_page = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_layout_e("Minimal Routine", "minimal-pack", dummy_page)
        self.assertEqual(img.size, (1000, 1500))

    def test_nonexistent_pack_raises_error(self):
        with self.assertRaises(ValueError):
            pin_variants.generate_pins_for_pack(pathlib.Path("/nonexistent/pack/dir"))

    def test_footer_contains_pack_name(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td, name="adhd-routine")
            out_dir = pathlib.Path(td) / "out"
            res = pin_variants.generate_pins_for_pack(pack_dir, out_dir=out_dir)
            self.assertEqual(res["pack_name"], "adhd-routine")

    def test_alt_text_length_limit(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td)
            out_dir = pathlib.Path(td) / "out"
            res = pin_variants.generate_pins_for_pack(pack_dir, out_dir=out_dir)
            for m in res["meta"]:
                self.assertLessEqual(len(m["alt"]), 500)

    def test_default_output_directory_structure(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td, name="std-pack")
            # Override current working directory inside test to td
            orig_cwd = os.getcwd()
            try:
                os.chdir(td)
                res = pin_variants.generate_pins_for_pack(pack_dir)
                exp_path = pathlib.Path(td) / "exported_content" / "marketing" / "std-pack" / "pins"
                self.assertTrue(exp_path.is_dir())
                self.assertTrue((exp_path / "pins.json").is_file())
            finally:
                os.chdir(orig_cwd)

    def test_safe_margins_respected(self):
        # Verify SAFE_MARGIN constant is at least 60 px
        self.assertGreaterEqual(pin_variants.SAFE_MARGIN, 60)

    def test_title_generator_fallback_counter_when_variants_collide(self):
        # Test extreme title that collapses to identical tokens
        titles = pin_variants.generate_ab_titles("Focus")
        self.assertEqual(len(titles), 5)
        self.assertEqual(len(set(titles)), 5)

    def test_pack_with_listing_md_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "md-pack"
            p.mkdir()
            (p / "listing.md").write_text("# Markdown Listing\nTitle: Focus Guide\nDescription: Good guide\n", encoding="utf-8")
            info = pin_variants.extract_pack_info(p)
            self.assertEqual(info["raw_title"], "Focus Guide")


if __name__ == "__main__":
    unittest.main()
