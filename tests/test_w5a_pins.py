"""Comprehensive unit tests for BINNY-W5A Pinterest pin & post image variants generator.

Tests:
1. Exact dimensions: 6 pins (1000x1500), 3 square posts (1080x1080), 2 stories (1080x1920)
2. All 6 layouts: hero mockup, 3-page fan, checklist teaser, before/after, quote card, what's inside grid
3. Text fitting, auto-wrap, and safe margins (>= 60px)
4. WCAG contrast ratio (>= 4.5:1) across theme palettes
5. Determinism: identical input produces byte-identical or identical hash output
6. Missing images fallback: clean synthetic pages rendered
7. Captions (English + Tenglish house style) and Alt Text in JSON metadata
8. No money amounts or currency symbols anywhere in code, metadata, or output text
9. No brand names or clickbait in generated text
"""

import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

import pin_variants


# CC2-1 speed: eight tests each rendered the same default pack (11 images, ~1.5 s) only to read
# the result. The real generator now runs once for them; every test still checks real output.
_SHARED = {}


def tearDownModule():
    holder = _SHARED.pop("dir", None)
    _SHARED.clear()
    if holder is not None:
        holder.cleanup()


class BaseW5APinTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()

    def _default_pack_variants(self):
        """generate_social_variants() result for the default mock pack, rendered once per module."""
        if "result" not in _SHARED:
            _SHARED["dir"] = tempfile.TemporaryDirectory()
            pack_dir = self._create_mock_pack(_SHARED["dir"].name)
            out_dir = pathlib.Path(_SHARED["dir"].name) / "out"
            _SHARED["result"] = pin_variants.generate_social_variants(pack_dir, out_dir=out_dir)
        return _SHARED["result"]

    def _create_mock_pack(
        self,
        base_dir: str | pathlib.Path,
        name: str = "daily-focus-planner",
        title: str = "Daily Focus & Priority Planner",
        desc: str = "A clean printable template designed for mindful daily structure and priority tracking.",
        theme: str = "calm sage",
        is_free: bool = False,
        with_images: bool = True,
    ) -> pathlib.Path:
        pdir = pathlib.Path(base_dir) / name
        pdir.mkdir(parents=True, exist_ok=True)
        listing = [
            f"Title: {title}",
            f"Theme: {theme}",
            f"Description: {desc}",
            "Tags: planner, daily focus, productivity, printable",
        ]
        if is_free:
            listing.append("Free printable download")
        (pdir / "listing.txt").write_text("\n".join(listing) + "\n", encoding="utf-8")

        if with_images and HAS_PILLOW:
            c = Image.new("RGB", (600, 800), (240, 245, 245))
            c.save(pdir / "cover.png")
            p1 = Image.new("RGB", (600, 800), (250, 250, 250))
            p1.save(pdir / "preview-01.png")
            p2 = Image.new("RGB", (600, 800), (255, 255, 255))
            p2.save(pdir / "preview-02.png")

        return pdir


class TestImageDimensionsAndFormats(BaseW5APinTestCase):
    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_01_pins_exact_dimensions(self):
        if True:  # shared render (was a fresh render per test)
            res = self._default_pack_variants()
            pins_dir = pathlib.Path(res["pins_dir"])
            for i in range(1, 7):
                pins = list(pins_dir.glob(f"*_{i}_*.png"))
                self.assertEqual(len(pins), 1, f"Missing pin index {i}")
                with Image.open(pins[0]) as im:
                    self.assertEqual(im.size, (1000, 1500))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_02_square_posts_exact_dimensions(self):
        if True:  # shared render (was a fresh render per test)
            res = self._default_pack_variants()
            posts_dir = pathlib.Path(res["posts_dir"])
            for i in range(1, 4):
                f = posts_dir / f"post_{i}_"
                matches = list(posts_dir.glob(f"post_{i}_*.png"))
                self.assertEqual(len(matches), 1, f"Missing post {i}")
                with Image.open(matches[0]) as im:
                    self.assertEqual(im.size, (1080, 1080))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_03_story_images_exact_dimensions(self):
        if True:  # shared render (was a fresh render per test)
            res = self._default_pack_variants()
            posts_dir = pathlib.Path(res["posts_dir"])
            for i in range(1, 3):
                matches = list(posts_dir.glob(f"story_{i}_*.png"))
                self.assertEqual(len(matches), 1, f"Missing story {i}")
                with Image.open(matches[0]) as im:
                    self.assertEqual(im.size, (1080, 1920))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_04_six_pins_count(self):
        if True:  # shared render (was a fresh render per test)
            res = self._default_pack_variants()
            self.assertEqual(len(res["pins_meta"]), 6)

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_05_three_square_posts_count(self):
        if True:  # shared render (was a fresh render per test)
            res = self._default_pack_variants()
            self.assertEqual(len(res["posts_meta"]), 5)  # 3 posts + 2 stories = 5 post items

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_06_total_eleven_variants_generated(self):
        if True:  # shared render (was a fresh render per test)
            res = self._default_pack_variants()
            self.assertEqual(len(res["all_meta"]), 11)

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_07_all_image_files_under_one_mb(self):
        if True:  # shared render (was a fresh render per test)
            res = self._default_pack_variants()
            pins_dir = pathlib.Path(res["pins_dir"])
            posts_dir = pathlib.Path(res["posts_dir"])
            for f in list(pins_dir.glob("*.png")) + list(posts_dir.glob("*.png")):
                size = f.stat().st_size
                self.assertLess(size, 1024 * 1024, f"{f.name} exceeds 1MB ({size} bytes)")


class TestLayoutRenderers(BaseW5APinTestCase):
    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_08_render_hero_mockup_pin(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_hero_mockup("Daily Focus Planner", "focus", [dummy], (1000, 1500))
        self.assertEqual(img.size, (1000, 1500))
        self.assertEqual(img.mode, "RGB")

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_09_render_hero_mockup_square(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_hero_mockup("Daily Focus Planner", "focus", [dummy], (1080, 1080))
        self.assertEqual(img.size, (1080, 1080))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_10_render_hero_mockup_story(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_hero_mockup("Daily Focus Planner", "focus", [dummy], (1080, 1920))
        self.assertEqual(img.size, (1080, 1920))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_11_render_three_page_fan_pin(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_three_page_fan("Daily Focus Planner", "focus", [dummy], (1000, 1500))
        self.assertEqual(img.size, (1000, 1500))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_12_render_three_page_fan_story(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_three_page_fan("Daily Focus Planner", "focus", [dummy], (1080, 1920))
        self.assertEqual(img.size, (1080, 1920))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_13_render_checklist_teaser_pin(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_checklist_teaser("Daily Focus Planner", "focus", [dummy], (1000, 1500))
        self.assertEqual(img.size, (1000, 1500))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_14_render_checklist_teaser_square(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_checklist_teaser("Daily Focus Planner", "focus", [dummy], (1080, 1080))
        self.assertEqual(img.size, (1080, 1080))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_15_render_before_after_pin(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_before_after("Daily Focus Planner", "focus", [dummy], (1000, 1500))
        self.assertEqual(img.size, (1000, 1500))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_16_render_before_after_story(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_before_after("Daily Focus Planner", "focus", [dummy], (1080, 1920))
        self.assertEqual(img.size, (1080, 1920))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_17_render_quote_card_pin(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_quote_card("Daily Focus Planner", "focus", [dummy], (1000, 1500))
        self.assertEqual(img.size, (1000, 1500))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_18_render_quote_card_square(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_quote_card("Daily Focus Planner", "focus", [dummy], (1080, 1080))
        self.assertEqual(img.size, (1080, 1080))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_19_render_whats_inside_grid_pin(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img = pin_variants.render_whats_inside_grid("Daily Focus Planner", "focus", [dummy], (1000, 1500))
        self.assertEqual(img.size, (1000, 1500))


class TestColorContrastAndPalettes(BaseW5APinTestCase):
    def test_20_calm_sage_contrast_ink(self):
        th = pin_variants.THEMES["calm sage"]
        cr = pin_variants.contrast_ratio(th["ink"], th["bg"])
        self.assertGreaterEqual(cr, 4.5)

    def test_21_calm_sage_contrast_primary(self):
        th = pin_variants.THEMES["calm sage"]
        cr = pin_variants.contrast_ratio(th["primary"], th["bg"])
        self.assertGreaterEqual(cr, 4.5)

    def test_22_midnight_focus_contrast_ink(self):
        th = pin_variants.THEMES["midnight focus"]
        cr = pin_variants.contrast_ratio(th["ink"], th["bg"])
        self.assertGreaterEqual(cr, 4.5)

    def test_23_midnight_focus_contrast_primary(self):
        th = pin_variants.THEMES["midnight focus"]
        cr = pin_variants.contrast_ratio(th["primary"], th["bg"])
        self.assertGreaterEqual(cr, 4.5)

    def test_24_terracotta_contrast_ink(self):
        th = pin_variants.THEMES["terracotta"]
        cr = pin_variants.contrast_ratio(th["ink"], th["bg"])
        self.assertGreaterEqual(cr, 4.5)

    def test_25_terracotta_contrast_primary(self):
        th = pin_variants.THEMES["terracotta"]
        cr = pin_variants.contrast_ratio(th["primary"], th["bg"])
        self.assertGreaterEqual(cr, 4.5)

    def test_26_ocean_contrast_ink(self):
        th = pin_variants.THEMES["ocean"]
        cr = pin_variants.contrast_ratio(th["ink"], th["bg"])
        self.assertGreaterEqual(cr, 4.5)

    def test_27_ocean_contrast_primary(self):
        th = pin_variants.THEMES["ocean"]
        cr = pin_variants.contrast_ratio(th["primary"], th["bg"])
        self.assertGreaterEqual(cr, 4.5)

    def test_28_minimal_mono_contrast_ink(self):
        th = pin_variants.THEMES["minimal mono"]
        cr = pin_variants.contrast_ratio(th["ink"], th["bg"])
        self.assertGreaterEqual(cr, 4.5)

    def test_29_minimal_mono_contrast_primary(self):
        th = pin_variants.THEMES["minimal mono"]
        cr = pin_variants.contrast_ratio(th["primary"], th["bg"])
        self.assertGreaterEqual(cr, 4.5)

    def test_30_festive_marigold_contrast_ink(self):
        th = pin_variants.THEMES["festive marigold"]
        cr = pin_variants.contrast_ratio(th["ink"], th["bg"])
        self.assertGreaterEqual(cr, 4.5)

    def test_31_festive_marigold_contrast_primary(self):
        th = pin_variants.THEMES["festive marigold"]
        cr = pin_variants.contrast_ratio(th["primary"], th["bg"])
        self.assertGreaterEqual(cr, 4.5)

    def test_32_resolve_theme_fallback(self):
        th = pin_variants.resolve_theme("nonexistent theme", "unknown pack")
        self.assertIn("bg", th)
        self.assertIn("ink", th)
        self.assertIn("primary", th)

    def test_33_resolve_theme_by_pack_niche_adhd(self):
        th = pin_variants.resolve_theme(None, "adhd-planner-2026")
        self.assertEqual(th["name"], "Calm Sage")

    def test_34_resolve_theme_by_pack_niche_budget(self):
        th = pin_variants.resolve_theme(None, "monthly-budget-tracker")
        self.assertEqual(th["name"], "Midnight Focus")

    def test_35_resolve_theme_by_pack_niche_wedding(self):
        th = pin_variants.resolve_theme(None, "wedding-guest-planner")
        self.assertEqual(th["name"], "Terracotta")


class TestTextFitAndWrapping(BaseW5APinTestCase):
    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_36_wrap_text_basic(self):
        f = pin_variants.get_font("regular", 24)
        lines = pin_variants.wrap_text("A quick brown fox jumps over the lazy dog", f, 200)
        self.assertGreater(len(lines), 1)

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_37_wrap_text_single_word_fits(self):
        f = pin_variants.get_font("regular", 24)
        lines = pin_variants.wrap_text("Productivity", f, 300)
        self.assertEqual(lines, ["Productivity"])

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_38_fit_text_reduces_size_for_long_title(self):
        long_title = "The Comprehensive Undated Daily Habit Tracker And Reflection Journal For Busy Professionals"
        lines, f = pin_variants.fit_text(long_title, "bold", 60, 24, 600, 3)
        self.assertLessEqual(len(lines), 3)
        for l in lines:
            self.assertLessEqual(pin_variants.text_width(l, f), 600)

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_39_fit_text_handles_short_title(self):
        lines, f = pin_variants.fit_text("Daily Focus", "bold", 60, 24, 800, 3)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], "Daily Focus")

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_40_safe_margins_constant_at_least_60(self):
        self.assertGreaterEqual(pin_variants.SAFE_MARGIN, 60)


class TestMissingImagesFallback(BaseW5APinTestCase):
    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_41_fallback_page_created_without_images(self):
        page = pin_variants.load_or_create_page(None, "Habit Log", (600, 800))
        self.assertEqual(page.size, (600, 800))
        self.assertEqual(page.mode, "RGB")

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_42_fallback_page_created_from_nonexistent_file(self):
        page = pin_variants.load_or_create_page(pathlib.Path("/no/such/file.png"), "Checklist", (500, 700))
        self.assertEqual(page.size, (500, 700))

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_43_generate_social_variants_without_images_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td, with_images=False)
            out_dir = pathlib.Path(td) / "out"
            res = pin_variants.generate_social_variants(pack_dir, out_dir=out_dir)
            self.assertEqual(len(res["all_meta"]), 11)


class TestDeterminismAndAtomicity(BaseW5APinTestCase):
    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_44_render_hero_mockup_is_deterministic(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img1 = pin_variants.render_hero_mockup("Focus Routine", "det-pack", [dummy], (1000, 1500))
        img2 = pin_variants.render_hero_mockup("Focus Routine", "det-pack", [dummy], (1000, 1500))
        self.assertEqual(img1.tobytes(), img2.tobytes())

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_45_render_fan_is_deterministic(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img1 = pin_variants.render_three_page_fan("Fan Routine", "det-pack", [dummy], (1000, 1500))
        img2 = pin_variants.render_three_page_fan("Fan Routine", "det-pack", [dummy], (1000, 1500))
        self.assertEqual(img1.tobytes(), img2.tobytes())

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_46_render_checklist_is_deterministic(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img1 = pin_variants.render_checklist_teaser("Check Routine", "det-pack", [dummy], (1000, 1500))
        img2 = pin_variants.render_checklist_teaser("Check Routine", "det-pack", [dummy], (1000, 1500))
        self.assertEqual(img1.tobytes(), img2.tobytes())

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_47_render_before_after_is_deterministic(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img1 = pin_variants.render_before_after("Before After", "det-pack", [dummy], (1000, 1500))
        img2 = pin_variants.render_before_after("Before After", "det-pack", [dummy], (1000, 1500))
        self.assertEqual(img1.tobytes(), img2.tobytes())

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_48_render_quote_is_deterministic(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img1 = pin_variants.render_quote_card("Mindset Focus", "det-pack", [dummy], (1000, 1500))
        img2 = pin_variants.render_quote_card("Mindset Focus", "det-pack", [dummy], (1000, 1500))
        self.assertEqual(img1.tobytes(), img2.tobytes())

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_49_render_grid_is_deterministic(self):
        dummy = Image.new("RGB", (600, 800), (255, 255, 255))
        img1 = pin_variants.render_whats_inside_grid("Inside Grid", "det-pack", [dummy], (1000, 1500))
        img2 = pin_variants.render_whats_inside_grid("Inside Grid", "det-pack", [dummy], (1000, 1500))
        self.assertEqual(img1.tobytes(), img2.tobytes())

    @unittest.skipUnless(HAS_PILLOW, "needs Pillow")
    def test_50_pack_folder_never_modified_by_social_variants(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td)
            before_files = set(pack_dir.rglob("*"))
            before_mtimes = {f: f.stat().st_mtime for f in before_files if f.is_file()}
            out_dir = pathlib.Path(td) / "out"
            pin_variants.generate_social_variants(pack_dir, out_dir=out_dir)
            after_files = set(pack_dir.rglob("*"))
            after_mtimes = {f: f.stat().st_mtime for f in after_files if f.is_file()}
            self.assertEqual(before_files, after_files)
            self.assertEqual(before_mtimes, after_mtimes)


class TestCaptionsAltTextAndHouseStyle(BaseW5APinTestCase):
    def test_51_generate_captions_contains_tenglish_words(self):
        alt, en, te = pin_variants.generate_image_captions_and_alt(
            "daily-focus", "Focus Planner", "hero mockup", "pin", "Organize daily"
        )
        self.assertIn("kosam", te)
        self.assertIn("ready ga undi", te)
        self.assertIn("Planner", te)  # Common English noun preserved

    def test_52_generate_captions_3_page_fan_tenglish(self):
        alt, en, te = pin_variants.generate_image_captions_and_alt(
            "daily-focus", "Focus Planner", "3-page fan", "pin", "Organize daily"
        )
        self.assertIn("pack lo", te)
        self.assertIn("unnay", te)

    def test_53_generate_captions_checklist_teaser_tenglish(self):
        alt, en, te = pin_variants.generate_image_captions_and_alt(
            "daily-focus", "Focus Planner", "checklist teaser", "pin", "Organize daily"
        )
        self.assertIn("tho", te)
        self.assertIn("complete cheyandi", te)

    def test_54_generate_captions_before_after_tenglish(self):
        alt, en, te = pin_variants.generate_image_captions_and_alt(
            "daily-focus", "Focus Planner", "before/after", "pin", "Organize daily"
        )
        self.assertIn("vadilesi", te)
        self.assertIn("marandi", te)

    def test_55_generate_captions_quote_card_tenglish(self):
        alt, en, te = pin_variants.generate_image_captions_and_alt(
            "daily-focus", "Focus Planner", "quote card", "pin", "Organize daily"
        )
        self.assertIn("help chestundi", te)

    def test_56_generate_captions_whats_inside_grid_tenglish(self):
        alt, en, te = pin_variants.generate_image_captions_and_alt(
            "daily-focus", "Focus Planner", "what's inside grid", "pin", "Organize daily"
        )
        self.assertIn("overview chudandi", te)

    def test_57_alt_text_length_limit(self):
        long_title = "Super " * 50
        alt, en, te = pin_variants.generate_image_captions_and_alt(
            "long-pack", long_title, "hero mockup", "pin", "Desc"
        )
        self.assertLessEqual(len(alt), 400)

    def test_58_posts_json_created_and_valid(self):
        if True:  # shared render (was a fresh render per test)
            res = self._default_pack_variants()
            p_json = pathlib.Path(res["posts_dir"]) / "posts.json"
            self.assertTrue(p_json.is_file())
            data = json.loads(p_json.read_text(encoding="utf-8"))
            self.assertEqual(len(data), 5)
            for item in data:
                self.assertIn("file", item)
                self.assertIn("layout", item)
                self.assertIn("caption_en", item)
                self.assertIn("caption_te", item)
                self.assertIn("alt", item)


class TestNoMoneyNoBrandsNoClickbait(BaseW5APinTestCase):
    def test_59_clean_text_removes_dollar_amount(self):
        cleaned = pin_variants.clean_text_no_brands_or_money("Planner for $25.00 only")
        self.assertNotIn("$", cleaned)
        self.assertNotIn("25", cleaned)

    def test_60_clean_text_removes_inr_amount(self):
        cleaned = pin_variants.clean_text_no_brands_or_money("Tracker costs 499 INR")
        self.assertNotIn("INR", cleaned)
        self.assertNotIn("499", cleaned)

    def test_61_clean_text_removes_notion_brand(self):
        cleaned = pin_variants.clean_text_no_brands_or_money("Best Notion Daily Planner")
        self.assertNotIn("Notion", cleaned)

    def test_62_clean_text_removes_canva_brand(self):
        cleaned = pin_variants.clean_text_no_brands_or_money("Canva Template for Study")
        self.assertNotIn("Canva", cleaned)

    def test_63_clean_text_removes_viral_clickbait(self):
        cleaned = pin_variants.clean_text_no_brands_or_money("Viral Miracle Routine Hack")
        self.assertNotIn("Viral", cleaned)
        self.assertNotIn("Miracle", cleaned)
        self.assertNotIn("Hack", cleaned)

    def test_64_generated_metadata_has_no_currency(self):
        with tempfile.TemporaryDirectory() as td:
            pack_dir = self._create_mock_pack(td, desc="Contains 12 pages for $15 USD or 500 rupees")
            out_dir = pathlib.Path(td) / "out"
            res = pin_variants.generate_social_variants(pack_dir, out_dir=out_dir)
            posts_json = pathlib.Path(res["posts_dir"]) / "posts.json"
            content = posts_json.read_text(encoding="utf-8")
            self.assertNotIn("$", content)
            self.assertNotIn("USD", content)
            self.assertNotIn("INR", content)
            self.assertNotIn("rupees", content)


if __name__ == "__main__":
    unittest.main()
