"""Tests for mockups_v2.py: procedural scenes, geometry, safety and output files."""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mockups_v2 as mv  # noqa: E402

try:
    from PIL import Image, ImageDraw  # noqa: E402
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _pack(root: Path, pages: int = 3, cover: bool = True, readme: str = "# Demo Habit Planner"):
    pack = root / "12-demo-habit-planner"
    pack.mkdir()
    colours = ((64, 128, 120), (160, 104, 96), (82, 112, 176))
    for index in range(pages):
        im = Image.new("RGB", (800, 1100), (250, 248, 240))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 800, 150], fill=colours[index % len(colours)])
        d.text((40, 55), f"Page {index + 1}", fill="white")
        for y in range(220, 950, 70):
            d.line([50, y, 750, y], fill=(190, 190, 185), width=3)
        im.save(pack / f"page-{index + 1}.png")
    if cover:
        im = Image.new("RGB", (1280, 720), (52, 108, 184))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 440, 720], fill=(36, 78, 146))
        im.save(pack / "cover.png")
    if readme:
        (pack / "README.md").write_text(readme + "\n", encoding="utf-8")
    return pack


@unittest.skipUnless(HAS_PIL and mv.available(), "needs Pillow")
class SharedPack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.pack = _pack(Path(cls.tmp.name))
        cls.pages = mv.source_pages(cls.pack)
        cls.theme = mv.theme_from_cover(cls.pack / "cover.png")
        cls.name = mv.product_name(cls.pack)
        cls.out = Path(cls.tmp.name) / "built"
        cls.files = mv.build_all(cls.pack, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


@unittest.skipUnless(HAS_PIL and mv.available(), "needs Pillow")
class HelperTests(unittest.TestCase):
    def test_safe_text_removes_google(self):
        self.assertNotIn("google", mv.safe_text("Planner for Google Sheets").lower())

    def test_safe_text_removes_notion(self):
        self.assertNotIn("notion", mv.safe_text("Template in Notion").lower())

    def test_safe_text_removes_etsy(self):
        self.assertNotIn("etsy", mv.safe_text("Etsy planner").lower())

    def test_safe_text_removes_gumroad(self):
        self.assertNotIn("gumroad", mv.safe_text("Gumroad pack").lower())

    def test_safe_text_removes_dollar_price(self):
        self.assertNotIn("$", mv.safe_text("Planner $19 today"))

    def test_safe_text_removes_usd_price(self):
        self.assertNotIn("usd", mv.safe_text("Planner 19 USD").lower())

    def test_safe_text_removes_inr_price(self):
        self.assertNotIn("₹", mv.safe_text("Planner ₹499"))

    def test_safe_text_removes_review_words(self):
        text = mv.safe_text("Five-star reviews and testimonials").lower()
        self.assertNotIn("review", text)
        self.assertNotIn("testimonial", text)

    def test_safe_text_removes_url(self):
        self.assertNotIn("http", mv.safe_text("See https://example.com/x").lower())

    def test_safe_text_keeps_normal_words(self):
        self.assertEqual(mv.safe_text("  Printable  Habit Planner  "), "Printable Habit Planner")

    def test_safe_text_empty(self):
        self.assertEqual(mv.safe_text(None), "")

    def test_plain_text_curly_quotes(self):
        self.assertEqual(mv.plain_text("‘A’ “B”"), "'A' \"B\"")

    def test_plain_text_accents(self):
        self.assertEqual(mv.plain_text("Résumé"), "Resume")

    def test_plain_text_telugu_removed(self):
        self.assertNotIn("తె", mv.plain_text("abc తెలుగు"))

    def test_mix_zero(self):
        self.assertEqual(mv.mix((0, 10, 20), (100, 110, 120), 0), (0, 10, 20))

    def test_mix_one(self):
        self.assertEqual(mv.mix((0, 10, 20), (100, 110, 120), 1), (100, 110, 120))

    def test_mix_mid(self):
        self.assertEqual(mv.mix((0, 0, 0), (100, 100, 100), 0.5), (50, 50, 50))

    def test_luminance_black(self):
        self.assertEqual(mv.luminance((0, 0, 0)), 0)

    def test_luminance_white(self):
        self.assertAlmostEqual(mv.luminance((255, 255, 255)), 1.0, places=2)

    def test_contrast_dark_background(self):
        self.assertEqual(mv.contrast_text((20, 20, 20)), (255, 255, 255))

    def test_contrast_light_background(self):
        self.assertEqual(mv.contrast_text((245, 245, 245)), (23, 28, 30))

    def test_theme_keys(self):
        self.assertEqual(set(mv.theme_from_accent((30, 120, 130))),
                         {"bg", "accent", "ink", "body", "paper", "desk", "shadow"})

    def test_theme_accent_kept(self):
        self.assertEqual(mv.theme_from_accent((30, 120, 130))["accent"], (30, 120, 130))

    def test_theme_background_light(self):
        self.assertGreater(mv.luminance(mv.theme_from_accent((30, 120, 130))["bg"]), 0.7)

    def test_font_regular_returns_font(self):
        self.assertEqual(mv.font("regular", 20).size, 20)

    def test_font_bold_returns_font(self):
        self.assertEqual(mv.font("bold", 30).size, 30)

    def test_unknown_font_weight_falls_back(self):
        self.assertEqual(mv.font("unknown", 22).size, 22)

    def test_text_width_positive(self):
        self.assertGreater(mv.text_width("Planner", mv.font("regular", 20)), 0)

    def test_line_height_positive(self):
        self.assertGreater(mv.line_height(mv.font("regular", 20)), 20)

    def test_wrap_text_empty(self):
        self.assertEqual(mv.wrap_text("", mv.font("regular", 20), 200), [""])

    def test_wrap_text_short(self):
        self.assertEqual(mv.wrap_text("One line", mv.font("regular", 20), 500), ["One line"])

    def test_wrap_text_width(self):
        f = mv.font("regular", 30)
        lines = mv.wrap_text("A long planner title with many useful words", f, 140)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(mv.text_width(line, f) <= 140 for line in lines))

    def test_wrap_text_long_word(self):
        f = mv.font("regular", 30)
        lines = mv.wrap_text("X" * 200, f, 100)
        self.assertTrue(all(mv.text_width(line, f) <= 100 for line in lines))
        self.assertEqual("".join(lines), "X" * 200)

    def test_fit_text_returns_lines(self):
        lines, f = mv.fit_text("Small planner", "bold", 400, 2, 50, 12)
        self.assertTrue(lines)
        self.assertLessEqual(f.size, 50)

    def test_fit_text_shrinks(self):
        lines, f = mv.fit_text("A very long printable habit planner title for a listing", "bold", 100, 2, 64, 10)
        self.assertLess(f.size, 64)

    def test_fit_text_no_overflow_long_word(self):
        lines, f = mv.fit_text("Y" * 120, "bold", 80, 2, 40, 8)
        self.assertTrue(all(mv.text_width(line, f) <= 80 for line in lines))

    def test_fit_text_empty(self):
        lines, f = mv.fit_text("", "bold", 100, 1, 30, 8)
        self.assertEqual(lines, [""])

    def test_safe_text_preserves_ascii(self):
        self.assertEqual(mv.safe_text("Simple Planner"), "Simple Planner")


@unittest.skipUnless(HAS_PIL and mv.available(), "needs Pillow")
class GeometryTests(unittest.TestCase):
    def test_find_coeffs_identity(self):
        quad = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertEqual(mv.find_coeffs(quad, quad), [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0])

    def test_find_coeffs_returns_eight(self):
        coeffs = mv.find_coeffs([(1, 2), (10, 3), (9, 11), (0, 10)], [(0, 0), (9, 0), (9, 9), (0, 9)])
        self.assertEqual(len(coeffs), 8)

    def test_find_coeffs_translation(self):
        src = [(0, 0), (10, 0), (10, 10), (0, 10)]
        dst = [(5, 7), (15, 7), (15, 17), (5, 17)]
        coeffs = mv.find_coeffs(dst, src)
        self.assertAlmostEqual(coeffs[0], 1.0, places=6)
        self.assertAlmostEqual(coeffs[1], 0.0, places=6)
        self.assertAlmostEqual(coeffs[2], -5.0, places=5)

    def test_find_coeffs_scale(self):
        src = [(0, 0), (10, 0), (10, 10), (0, 10)]
        dst = [(0, 0), (20, 0), (20, 20), (0, 20)]
        coeffs = mv.find_coeffs(dst, src)
        self.assertAlmostEqual(coeffs[0], 0.5, places=6)
        self.assertAlmostEqual(coeffs[4], 0.5, places=6)

    def test_solve_singular_raises(self):
        with self.assertRaises(ValueError):
            mv.find_coeffs([(0, 0), (0, 0), (0, 0), (0, 0)], [(0, 0), (1, 0), (1, 1), (0, 1)])

    def test_warp_quad_size(self):
        page = Image.new("RGB", (100, 140), "red")
        warped = mv.warp_quad(page, [(20, 20), (120, 10), (130, 150), (10, 140)], (160, 180))
        self.assertEqual(warped.size, (160, 180))

    def test_warp_quad_transparent(self):
        page = Image.new("RGB", (100, 140), "red")
        warped = mv.warp_quad(page, [(20, 20), (120, 10), (130, 150), (10, 140)], (160, 180))
        self.assertEqual(warped.getpixel((0, 0))[3], 0)

    def test_warp_quad_keeps_colour(self):
        page = Image.new("RGB", (100, 140), (220, 20, 20))
        warped = mv.warp_quad(page, [(20, 20), (120, 20), (120, 160), (20, 160)], (160, 180))
        red, green, blue, alpha = warped.getpixel((70, 90))
        self.assertGreater(alpha, 240)
        self.assertGreater(red, 200)
        self.assertLess(green, 40)

    def test_warp_quad_output_is_rgba(self):
        page = Image.new("RGB", (50, 50), "blue")
        warped = mv.warp_quad(page, [(10, 10), (40, 10), (40, 40), (10, 40)], (60, 60))
        self.assertEqual(warped.mode, "RGBA")

    def test_paper_texture_size(self):
        self.assertEqual(mv.paper_texture((90, 70), 3).size, (90, 70))

    def test_paper_texture_deterministic(self):
        a = mv.paper_texture((80, 80), 3).tobytes()
        b = mv.paper_texture((80, 80), 3).tobytes()
        self.assertEqual(a, b)

    def test_paper_texture_different_seed(self):
        a = mv.paper_texture((80, 80), 3).tobytes()
        b = mv.paper_texture((80, 80), 4).tobytes()
        self.assertNotEqual(a, b)

    def test_apply_texture_size(self):
        im = Image.new("RGB", (50, 60), "white")
        self.assertEqual(mv.apply_texture(im, 2).size, (50, 60))

    def test_apply_texture_deterministic(self):
        im = Image.new("RGB", (50, 60), "white")
        self.assertEqual(mv.apply_texture(im, 2).tobytes(), mv.apply_texture(im, 2).tobytes())

    def test_add_shadow_alpha(self):
        canvas = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        shape = mv.rounded_layer((40, 40), 4, (255, 255, 255, 255))
        mv.add_shadow(canvas, shape, (20, 20), 4, 0.5, (2, 2))
        self.assertGreater(canvas.getpixel((45, 45))[3], 0)

    def test_paste_rgba(self):
        canvas = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
        layer = mv.rounded_layer((20, 20), 4, (255, 0, 0, 255))
        mv.paste_rgba(canvas, layer, (10, 10))
        self.assertEqual(canvas.getpixel((15, 15))[:3], (255, 0, 0))

    def test_rounded_layer_size(self):
        self.assertEqual(mv.rounded_layer((30, 20), 3, (1, 2, 3, 255)).size, (30, 20))

    def test_fit_page_size(self):
        self.assertEqual(mv.fit_page(Image.new("RGB", (200, 400), "white")).size, mv.PAGE_SIZE)


@unittest.skipUnless(HAS_PIL and mv.available(), "needs Pillow")
class PageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_page_files_found(self):
        pack = _pack(self.root)
        self.assertEqual(len(mv._page_files(pack)), 3)

    def test_page_files_excludes_cover(self):
        pack = _pack(self.root)
        self.assertNotIn("cover.png", [p.name for p in mv._page_files(pack)])

    def test_page_files_excludes_preview(self):
        pack = _pack(self.root)
        (pack / "preview-1.png").write_bytes((pack / "page-1.png").read_bytes())
        self.assertNotIn("preview-1.png", [p.name for p in mv._page_files(pack)])

    def test_page_files_limit(self):
        pack = _pack(self.root, pages=7)
        self.assertEqual(len(mv._page_files(pack)), mv.MAX_SOURCE_PAGES)

    def test_source_pages_count(self):
        self.assertEqual(len(mv.source_pages(_pack(self.root))), 3)

    def test_source_pages_size(self):
        self.assertEqual(mv.source_pages(_pack(self.root))[0].size, mv.PAGE_SIZE)

    def test_source_pages_fallback_missing(self):
        pack = self.root / "empty-pack"
        pack.mkdir()
        (pack / "cover.png").write_bytes(b"not a real image")
        self.assertEqual(len(mv.source_pages(pack)), 3)

    def test_fallback_size(self):
        self.assertEqual(mv.fallback_page("Demo", 0).size, mv.PAGE_SIZE)

    def test_fallback_deterministic(self):
        self.assertEqual(mv.fallback_page("Demo", 1).tobytes(), mv.fallback_page("Demo", 1).tobytes())

    def test_product_name_readme(self):
        self.assertEqual(mv.product_name(_pack(self.root)), "Demo Habit Planner")

    def test_product_name_folder(self):
        pack = self.root / "7-simple-study-planner"
        pack.mkdir()
        self.assertEqual(mv.product_name(pack), "Simple Study Planner")

    def test_product_name_listing_json(self):
        pack = self.root / "8-pack"
        pack.mkdir()
        (pack / "listing.json").write_text(json.dumps({"title": "Budget Sheet"}), encoding="utf-8")
        self.assertEqual(mv.product_name(pack), "Budget Sheet")

    def test_product_name_brand_removed(self):
        pack = self.root / "9-pack"
        pack.mkdir()
        (pack / "README.md").write_text("# Planner for Notion\n", encoding="utf-8")
        self.assertNotIn("notion", mv.product_name(pack).lower())

    def test_cover_path(self):
        pack = _pack(self.root)
        self.assertEqual(mv._cover_path(pack).name, "cover.png")

    def test_theme_from_cover_default(self):
        self.assertEqual(mv.theme_from_cover(None), mv.DEFAULT_THEME)

    def test_theme_from_cover_uses_blue(self):
        theme = mv.theme_from_cover(_pack(self.root) / "cover.png")
        self.assertGreater(theme["accent"][2], theme["accent"][0])

    def test_theme_from_cover_deterministic(self):
        path = _pack(self.root) / "cover.png"
        self.assertEqual(mv.theme_from_cover(path), mv.theme_from_cover(path))


@unittest.skipUnless(HAS_PIL and mv.available(), "needs Pillow")
class SceneTests(SharedPack):
    pass


def _scene_test(scene, mode):
    def test(self):
        image = mv._scene_image(scene, self.pages, self.theme, self.name, (320, 320), mode != "no_title")
        self.assertIsInstance(image, Image.Image)
        if mode == "size":
            self.assertEqual(image.size, (320, 320))
        elif mode == "rgb":
            self.assertEqual(image.mode, "RGB")
        elif mode == "deterministic":
            again = mv._scene_image(scene, self.pages, self.theme, self.name, (320, 320))
            self.assertEqual(image.tobytes(), again.tobytes())
        elif mode == "no_title":
            self.assertEqual(image.size, (320, 320))
    return test


for _scene in mv.SCENE_NAMES:
    for _mode in ("size", "rgb", "deterministic", "no_title"):
        setattr(SceneTests, f"test_{_scene}_{_mode}", _scene_test(_scene, _mode))


@unittest.skipUnless(HAS_PIL and mv.available(), "needs Pillow")
class BuildTests(SharedPack):
    def test_build_returns_files(self):
        self.assertTrue(self.files)

    def test_listing_jpeg_count(self):
        names = [Path(p).name for p in self.files]
        self.assertEqual(len([n for n in names if re.match(r"mockup-\d\d-.*\.jpg$", n)]), 8)

    def test_listing_png_count(self):
        names = [Path(p).name for p in self.files]
        self.assertEqual(len([n for n in names if re.match(r"mockup-\d\d-.*\.png$", n)]), 8)

    def test_pin_sizes_jpg(self):
        path = next(Path(p) for p in self.files if p.endswith("mockup-pin-1000x1500.jpg"))
        with Image.open(path) as im:
            self.assertEqual(im.size, mv.PIN_SIZE)

    def test_pin_sizes_png(self):
        path = next(Path(p) for p in self.files if p.endswith("mockup-pin-1000x1500.png"))
        with Image.open(path) as im:
            self.assertEqual(im.size, mv.PIN_SIZE)

    def test_square_sizes_jpg(self):
        path = next(Path(p) for p in self.files if p.endswith("mockup-square-1080x1080.jpg"))
        with Image.open(path) as im:
            self.assertEqual(im.size, mv.SQUARE_SIZE)

    def test_square_sizes_png(self):
        path = next(Path(p) for p in self.files if p.endswith("mockup-square-1080x1080.png"))
        with Image.open(path) as im:
            self.assertEqual(im.size, mv.SQUARE_SIZE)

    def test_listing_sizes(self):
        path = next(Path(p) for p in self.files if p.endswith("-tablet_desk-2000x2000.jpg"))
        with Image.open(path) as im:
            self.assertEqual(im.size, mv.LISTING_SIZE)

    def test_alt_json_exists(self):
        self.assertTrue((self.out / "mockups-alt.json").is_file())

    def test_alt_json_maps_every_file(self):
        alt = json.loads((self.out / "mockups-alt.json").read_text(encoding="utf-8"))
        names = {Path(p).name for p in self.files if p.endswith((".jpg", ".png"))}
        self.assertEqual(set(alt), names)

    def test_alt_no_brands(self):
        alt = (self.out / "mockups-alt.json").read_text(encoding="utf-8").lower()
        for brand in ("google", "notion", "etsy", "gumroad", "canva"):
            self.assertNotIn(brand, alt)

    def test_alt_no_prices(self):
        alt = (self.out / "mockups-alt.json").read_text(encoding="utf-8")
        self.assertFalse(re.search(r"[$€£₹]", alt))

    def test_alt_no_reviews(self):
        alt = (self.out / "mockups-alt.json").read_text(encoding="utf-8").lower()
        self.assertNotIn("testimonial", alt)
        self.assertNotIn("5-star", alt)

    def test_file_names_order(self):
        names = [Path(p).name for p in self.files]
        self.assertTrue(names[0].startswith("mockup-01-tablet_desk"))
        self.assertTrue(names[14].startswith("mockup-08-page_stack"))
        self.assertEqual(names[-5], "mockup-pin-1000x1500.jpg")
        self.assertEqual(names[-4], "mockup-pin-1000x1500.png")
        self.assertEqual(names[-3], "mockup-square-1080x1080.jpg")
        self.assertEqual(names[-2], "mockup-square-1080x1080.png")
        self.assertEqual(names[-1], "mockups-alt.json")

    def test_default_out_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        files = mv.build_all(pack, include_png=False, scenes=("page_stack",))
        self.assertTrue(all(Path(p).parent == pack.resolve() / "mockups-v2" for p in files))

    def test_no_png(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        files = mv.build_all(pack, Path(tmp.name) / "out", include_png=False, scenes=("clipboard",))
        self.assertFalse([p for p in files if p.endswith(".png")])

    def test_scene_subset(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        files = mv.build_all(pack, Path(tmp.name) / "out", include_png=False,
                             scenes=("clipboard", "binder"))
        jpgs = [p for p in files if p.endswith(".jpg")]
        self.assertEqual(len(jpgs), 4)

    def test_unknown_scene_raises(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        with self.assertRaises(ValueError):
            mv.build_all(pack, Path(tmp.name) / "out", scenes=("nope",))

    def test_build_missing_pages_fallback(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = Path(tmp.name) / "empty"
        pack.mkdir()
        files = mv.build_all(pack, Path(tmp.name) / "out", include_png=False, scenes=("page_stack",))
        self.assertTrue([p for p in files if p.endswith(".jpg")])

    def test_build_deterministic_jpeg_bytes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        a = mv.build_all(pack, Path(tmp.name) / "a", include_png=False, scenes=("clipboard",))
        b = mv.build_all(pack, Path(tmp.name) / "b", include_png=False, scenes=("clipboard",))
        ja = next(p for p in a if p.endswith(".jpg"))
        jb = next(p for p in b if p.endswith(".jpg"))
        self.assertEqual(Path(ja).read_bytes(), Path(jb).read_bytes())

    def test_build_deterministic_alt(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        a = mv.build_all(pack, Path(tmp.name) / "a", include_png=False, scenes=("clipboard",))
        b = mv.build_all(pack, Path(tmp.name) / "b", include_png=False, scenes=("clipboard",))
        self.assertEqual(Path(a[-1]).read_text(encoding="utf-8"), Path(b[-1]).read_text(encoding="utf-8"))

    def test_output_folder_created(self):
        self.assertTrue(self.out.is_dir())

    def test_cli_outputs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        out = Path(tmp.name) / "cli"
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            code = mv.main([str(pack), "--out", str(out), "--no-png", "--scenes", "clipboard"])
        self.assertEqual(code, 0)
        self.assertIn("Mockups:", stdout.getvalue())
        self.assertTrue(list(out.glob("mockup-*.jpg")))

    def test_cli_no_title(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        out = Path(tmp.name) / "cli"
        with mock.patch("sys.stdout", io.StringIO()):
            mv.main([str(pack), "--out", str(out), "--no-png", "--no-title", "--scenes", "binder"])
        self.assertTrue(list(out.glob("mockup-*.jpg")))

    def test_cli_default_out(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        with mock.patch("sys.stdout", io.StringIO()):
            mv.main([str(pack), "--no-png", "--scenes", "page_stack"])
        self.assertTrue((pack / "mockups-v2" / "mockups-alt.json").is_file())

    def test_main_returns_zero(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(mv.main([str(pack), "--no-png", "--scenes", "page_stack"]), 0)


if __name__ == "__main__":
    unittest.main()
