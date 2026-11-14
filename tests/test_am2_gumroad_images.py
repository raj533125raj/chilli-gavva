"""Tests for gumroad_images.py: sizes, upload order, safety, fallback and determinism."""
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

import gumroad_images as gi  # noqa: E402

try:
    from PIL import Image, ImageDraw  # noqa: E402
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _pack(root: Path, pages: int = 3, cover: bool = True,
          readme: str = "# Demo Planner\n\nSimple printable pages for a calm week."):
    pack = root / "12-demo-planner"
    pack.mkdir()
    for index in range(pages):
        im = Image.new("RGB", (800, 1100), (250, 248, 240))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 800, 150], fill=(70 + index * 20, 120, 135))
        d.text((40, 55), f"Page {index + 1}", fill="white")
        for y in range(220, 950, 70):
            d.line([50, y, 750, y], fill=(190, 190, 185), width=3)
        im.save(pack / f"page-{index + 1}.png")
    if cover:
        im = Image.new("RGB", (1280, 720), (52, 108, 184))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 420, 720], fill=(33, 74, 140))
        im.save(pack / "cover.png")
    if readme:
        (pack / "README.md").write_text(readme + "\n", encoding="utf-8")
    return pack


@unittest.skipUnless(HAS_PIL and gi.available(), "needs Pillow")
class SharedBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.pack = _pack(Path(cls.tmp.name))
        cls.meta = gi._product_meta(cls.pack)
        cls.theme = gi._theme(cls.pack)
        cls.pages = gi._pages(cls.pack, cls.theme, cls.meta["name"])
        cls.out = Path(cls.tmp.name) / "gumroad"
        cls.files = gi.build_gumroad_images(cls.pack, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


@unittest.skipUnless(HAS_PIL and gi.available(), "needs Pillow")
class HelperTests(unittest.TestCase):
    def test_safe_text_removes_google(self):
        self.assertNotIn("google", gi.safe_text("Planner for Google Sheets").lower())

    def test_safe_text_removes_notion(self):
        self.assertNotIn("notion", gi.safe_text("Notion template").lower())

    def test_safe_text_removes_etsy(self):
        self.assertNotIn("etsy", gi.safe_text("Etsy planner").lower())

    def test_safe_text_removes_gumroad(self):
        self.assertNotIn("gumroad", gi.safe_text("Gumroad listing").lower())

    def test_safe_text_removes_dollar(self):
        self.assertNotIn("$", gi.safe_text("Planner $19"))

    def test_safe_text_removes_usd(self):
        self.assertNotIn("usd", gi.safe_text("Planner 19 USD").lower())

    def test_safe_text_removes_inr(self):
        self.assertNotIn("₹", gi.safe_text("Planner ₹499"))

    def test_safe_text_removes_reviews(self):
        text = gi.safe_text("5-star reviews and testimonials").lower()
        self.assertNotIn("review", text)
        self.assertNotIn("testimonial", text)

    def test_safe_text_removes_url(self):
        self.assertNotIn("http", gi.safe_text("https://example.com/planner").lower())

    def test_safe_text_keeps_normal(self):
        self.assertEqual(gi.safe_text("  Printable  Planner  "), "Printable Planner")

    def test_safe_text_empty(self):
        self.assertEqual(gi.safe_text(None), "")

    def test_plain_text_accents(self):
        self.assertEqual(gi.plain_text("Résumé"), "Resume")

    def test_plain_text_curly(self):
        self.assertEqual(gi.plain_text("‘A’ “B”"), "'A' \"B\"")

    def test_plain_text_telugu(self):
        self.assertNotIn("తె", gi.plain_text("abc తెలుగు"))

    def test_mix_zero(self):
        self.assertEqual(gi.mix((0, 10, 20), (100, 110, 120), 0), (0, 10, 20))

    def test_mix_one(self):
        self.assertEqual(gi.mix((0, 10, 20), (100, 110, 120), 1), (100, 110, 120))

    def test_mix_mid(self):
        self.assertEqual(gi.mix((0, 0, 0), (100, 100, 100), 0.5), (50, 50, 50))

    def test_luminance_black(self):
        self.assertEqual(gi.luminance((0, 0, 0)), 0)

    def test_luminance_white(self):
        self.assertAlmostEqual(gi.luminance((255, 255, 255)), 1.0, places=2)

    def test_ink_on_dark(self):
        self.assertEqual(gi.ink_on((20, 20, 20)), (255, 255, 255))

    def test_ink_on_light(self):
        self.assertEqual(gi.ink_on((240, 240, 240)), (23, 28, 30))

    def test_font_regular(self):
        self.assertEqual(gi.font("regular", 20).size, 20)

    def test_font_bold(self):
        self.assertEqual(gi.font("bold", 28).size, 28)

    def test_font_unknown(self):
        self.assertEqual(gi.font("other", 18).size, 18)

    def test_text_width(self):
        self.assertGreater(gi.text_width("Planner", gi.font("regular", 20)), 0)

    def test_line_height(self):
        self.assertGreater(gi.line_height(gi.font("regular", 20)), 20)

    def test_wrap_empty(self):
        self.assertEqual(gi.wrap_text("", gi.font("regular", 20), 100), [""])

    def test_wrap_short(self):
        self.assertEqual(gi.wrap_text("Short", gi.font("regular", 20), 300), ["Short"])

    def test_wrap_width(self):
        f = gi.font("regular", 28)
        lines = gi.wrap_text("A long planner title with many words", f, 120)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(gi.text_width(line, f) <= 120 for line in lines))

    def test_wrap_long_word(self):
        f = gi.font("regular", 26)
        lines = gi.wrap_text("Z" * 150, f, 90)
        self.assertTrue(all(gi.text_width(line, f) <= 90 for line in lines))
        self.assertEqual("".join(lines), "Z" * 150)

    def test_fit_text_returns(self):
        lines, f = gi.fit_text("Planner", "bold", 300, 2, 50, 12)
        self.assertTrue(lines)
        self.assertLessEqual(f.size, 50)

    def test_fit_text_shrinks(self):
        lines, f = gi.fit_text("A very long printable planner title for the store", "bold", 90, 2, 60, 8)
        self.assertLess(f.size, 60)

    def test_fit_text_no_overflow(self):
        lines, f = gi.fit_text("W" * 120, "bold", 70, 2, 40, 8)
        self.assertTrue(all(gi.text_width(line, f) <= 70 for line in lines))

    def test_fit_text_empty(self):
        self.assertEqual(gi.fit_text("", "bold", 100, 1, 20, 8)[0], [""])

    def test_gallery_count_positive(self):
        self.assertEqual(gi.gallery_count(2), 4)

    def test_gallery_count_zero(self):
        self.assertEqual(gi.gallery_count(0), 3)

    def test_gallery_count_min(self):
        self.assertEqual(gi.gallery_count(-1), 3)

    def test_upload_names_count(self):
        self.assertEqual(len(gi.upload_names(3)), 5)

    def test_upload_names_max(self):
        self.assertEqual(len(gi.upload_names(9)), 7)

    def test_upload_names_first_two(self):
        names = gi.upload_names(4)
        self.assertEqual(names[:2], ["01-thumbnail-600x600.jpg", "02-cover-1280x720.jpg"])

    def test_upload_names_gallery_numbers(self):
        names = gi.upload_names(3)
        self.assertEqual(names[2:], ["03-gallery-1-1600x1200.jpg",
                                     "04-gallery-2-1600x1200.jpg",
                                     "05-gallery-3-1600x1200.jpg"])

    def test_alt_thumbnail(self):
        self.assertIn("thumbnail", gi.alt_text("thumbnail", "Demo").lower())

    def test_alt_cover(self):
        self.assertIn("cover", gi.alt_text("cover", "Demo").lower())

    def test_alt_gallery(self):
        self.assertIn("gallery", gi.alt_text("gallery", "Demo").lower())

    def test_alt_brand_removed(self):
        self.assertNotIn("notion", gi.alt_text("gallery", "Demo for Notion").lower())


@unittest.skipUnless(HAS_PIL and gi.available(), "needs Pillow")
class ProductTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_meta_name_readme(self):
        self.assertEqual(gi._product_meta(_pack(self.root))["name"], "Demo Planner")

    def test_meta_description_readme(self):
        self.assertIn("Simple printable pages", gi._product_meta(_pack(self.root))["description"])

    def test_meta_listing_title(self):
        pack = self.root / "8-pack"
        pack.mkdir()
        (pack / "listing.json").write_text(json.dumps({"title": "Budget Sheet"}), encoding="utf-8")
        self.assertEqual(gi._product_meta(pack)["name"], "Budget Sheet")

    def test_meta_listing_description(self):
        pack = self.root / "8-pack"
        pack.mkdir()
        (pack / "listing.json").write_text(
            json.dumps({"title": "Budget", "description": "A clean budget."}), encoding="utf-8")
        self.assertEqual(gi._product_meta(pack)["description"], "A clean budget")

    def test_meta_folder_fallback(self):
        pack = self.root / "7-simple-study-planner"
        pack.mkdir()
        self.assertEqual(gi._product_meta(pack)["name"], "Simple Study Planner")

    def test_meta_brand_removed(self):
        pack = self.root / "9-pack"
        pack.mkdir()
        (pack / "README.md").write_text("# Planner for Notion\n", encoding="utf-8")
        self.assertNotIn("notion", gi._product_meta(pack)["name"].lower())

    def test_meta_description_fallback(self):
        pack = self.root / "10-pack"
        pack.mkdir()
        self.assertTrue(gi._product_meta(pack)["description"])

    def test_cover_path(self):
        self.assertEqual(gi._cover_path(_pack(self.root)).name, "cover.png")

    def test_theme_default_without_mv(self):
        with mock.patch.object(gi, "MV", None):
            self.assertEqual(gi._theme(_pack(self.root)), gi.DEFAULT_THEME)

    def test_theme_keys(self):
        self.assertEqual(set(gi._theme(_pack(self.root))),
                         {"bg", "accent", "ink", "body", "paper", "desk", "shadow"})

    def test_theme_uses_blue_cover(self):
        theme = gi._theme(_pack(self.root))
        self.assertGreater(theme["accent"][2], theme["accent"][0])

    def test_page_files_found(self):
        self.assertEqual(len(gi._page_files(_pack(self.root))), 3)

    def test_page_files_excludes_cover(self):
        self.assertNotIn("cover.png", [p.name for p in gi._page_files(_pack(self.root))])

    def test_page_files_excludes_preview(self):
        pack = _pack(self.root)
        (pack / "preview-1.png").write_bytes((pack / "page-1.png").read_bytes())
        self.assertNotIn("preview-1.png", [p.name for p in gi._page_files(pack)])

    def test_page_files_limit(self):
        self.assertEqual(len(gi._page_files(_pack(self.root, pages=7))), 5)

    def test_own_pages_count(self):
        pack = _pack(self.root)
        self.assertEqual(len(gi._own_pages(pack, gi.DEFAULT_THEME, "Demo")), 3)

    def test_own_pages_size(self):
        pack = _pack(self.root)
        self.assertEqual(gi._own_pages(pack, gi.DEFAULT_THEME, "Demo")[0].size, (1200, 1600))

    def test_own_pages_fallback(self):
        pack = self.root / "empty"
        pack.mkdir()
        self.assertEqual(len(gi._own_pages(pack, gi.DEFAULT_THEME, "Demo")), 3)

    def test_fallback_page_size(self):
        self.assertEqual(gi.fallback_page("Demo").size, (1200, 1600))

    def test_fallback_page_deterministic(self):
        self.assertEqual(gi.fallback_page("Demo", 1).tobytes(), gi.fallback_page("Demo", 1).tobytes())

    def test_pages_with_mv(self):
        pack = _pack(self.root)
        self.assertEqual(len(gi._pages(pack, gi.DEFAULT_THEME, "Demo")), 3)

    def test_pages_without_mv(self):
        pack = _pack(self.root)
        with mock.patch.object(gi, "MV", None):
            self.assertEqual(len(gi._pages(pack, gi.DEFAULT_THEME, "Demo")), 3)


@unittest.skipUnless(HAS_PIL and gi.available(), "needs Pillow")
class ImageTests(SharedBuild):
    def test_thumbnail_size(self):
        self.assertEqual(gi.thumbnail_image(self.pages[0], self.theme, self.meta["name"]).size,
                         gi.THUMBNAIL_SIZE)

    def test_thumbnail_rgb(self):
        self.assertEqual(gi.thumbnail_image(self.pages[0], self.theme, self.meta["name"]).mode, "RGB")

    def test_cover_size(self):
        self.assertEqual(gi.cover_image(self.pages, self.theme, self.meta["name"],
                                        self.meta["description"]).size, gi.COVER_SIZE)

    def test_cover_rgb(self):
        self.assertEqual(gi.cover_image(self.pages, self.theme, self.meta["name"],
                                        self.meta["description"]).mode, "RGB")

    def test_gallery_image_size(self):
        image = gi.gallery_image(self.pages, self.theme, self.meta["name"], 0)
        self.assertEqual(image.size, gi.GALLERY_SIZE)

    def test_gallery_image_rgb(self):
        image = gi.gallery_image(self.pages, self.theme, self.meta["name"], 1)
        self.assertEqual(image.mode, "RGB")

    def test_gallery_scene_0_differs(self):
        a = gi.gallery_image(self.pages, self.theme, self.meta["name"], 0).tobytes()
        b = gi.gallery_image(self.pages, self.theme, self.meta["name"], 1).tobytes()
        self.assertNotEqual(a, b)

    def test_plain_gallery_size_without_mv(self):
        with mock.patch.object(gi, "MV", None):
            self.assertEqual(gi.gallery_image(self.pages, self.theme, self.meta["name"], 0).size,
                             gi.GALLERY_SIZE)

    def test_plain_gallery_rgb_without_mv(self):
        with mock.patch.object(gi, "MV", None):
            self.assertEqual(gi.gallery_image(self.pages, self.theme, self.meta["name"], 1).mode, "RGB")

    def test_plain_gallery_deterministic(self):
        with mock.patch.object(gi, "MV", None):
            a = gi.gallery_image(self.pages, self.theme, self.meta["name"], 2).tobytes()
            b = gi.gallery_image(self.pages, self.theme, self.meta["name"], 2).tobytes()
        self.assertEqual(a, b)

    def test_thumbnail_long_title_no_exception(self):
        image = gi.thumbnail_image(self.pages[0], self.theme, "Long " * 60)
        self.assertEqual(image.size, gi.THUMBNAIL_SIZE)

    def test_cover_long_description_no_exception(self):
        image = gi.cover_image(self.pages, self.theme, "Demo", "Word " * 300)
        self.assertEqual(image.size, gi.COVER_SIZE)


@unittest.skipUnless(HAS_PIL and gi.available(), "needs Pillow")
class BuildTests(SharedBuild):
    def test_returns_six_images(self):
        self.assertEqual(len(self.files), 6)

    def test_upload_order(self):
        names = [Path(path).name for path in self.files]
        self.assertEqual(names, gi.upload_names(4))

    def test_files_exist(self):
        self.assertTrue(all(Path(path).is_file() for path in self.files))

    def test_thumbnail_size(self):
        with Image.open(self.files[0]) as im:
            self.assertEqual(im.size, gi.THUMBNAIL_SIZE)

    def test_cover_size(self):
        with Image.open(self.files[1]) as im:
            self.assertEqual(im.size, gi.COVER_SIZE)

    def test_gallery_count(self):
        self.assertEqual(len(self.files[2:]), 4)

    def test_gallery_sizes(self):
        for path in self.files[2:]:
            with Image.open(path) as im:
                self.assertEqual(im.size, gi.GALLERY_SIZE)

    def test_alt_exists(self):
        self.assertTrue((self.out / gi.ALT_NAME).is_file())

    def test_alt_valid_json(self):
        data = json.loads((self.out / gi.ALT_NAME).read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

    def test_alt_maps_every_file(self):
        data = json.loads((self.out / gi.ALT_NAME).read_text(encoding="utf-8"))
        self.assertEqual(set(data), {Path(path).name for path in self.files})

    def test_alt_no_brands(self):
        text = (self.out / gi.ALT_NAME).read_text(encoding="utf-8").lower()
        for brand in ("google", "notion", "etsy", "gumroad", "canva", "payhip"):
            self.assertNotIn(brand, text)

    def test_alt_no_prices(self):
        text = (self.out / gi.ALT_NAME).read_text(encoding="utf-8")
        self.assertFalse(re.search(r"[$€£₹]", text))

    def test_alt_no_reviews(self):
        text = (self.out / gi.ALT_NAME).read_text(encoding="utf-8").lower()
        self.assertNotIn("testimonial", text)
        self.assertNotIn("5-star", text)

    def test_no_png_files(self):
        self.assertFalse([p for p in self.files if p.endswith(".png")])

    def test_output_dir_created(self):
        self.assertTrue(self.out.is_dir())

    def test_deterministic_bytes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        a = gi.build_gumroad_images(pack, Path(tmp.name) / "a")
        b = gi.build_gumroad_images(pack, Path(tmp.name) / "b")
        with Image.open(a[0]) as ia, Image.open(b[0]) as ib:
            self.assertEqual(ia.tobytes(), ib.tobytes())

    def test_deterministic_alt(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        a = gi.build_gumroad_images(pack, Path(tmp.name) / "a")
        b = gi.build_gumroad_images(pack, Path(tmp.name) / "b")
        self.assertEqual((Path(a[0]).parent / gi.ALT_NAME).read_text(encoding="utf-8"),
                         (Path(b[0]).parent / gi.ALT_NAME).read_text(encoding="utf-8"))

    def test_for_pack_default_out(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        files = gi.for_pack(pack)
        self.assertTrue(all(Path(p).parent == pack.resolve() / "gumroad-images" for p in files))

    def test_for_pack_order(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        files = gi.for_pack(pack, Path(tmp.name) / "out")
        self.assertEqual([Path(p).name for p in files], gi.upload_names(4))

    def test_for_pack_reuses_existing(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        out = Path(tmp.name) / "out"
        first = gi.for_pack(pack, out)
        with mock.patch.object(gi, "build_gumroad_images") as builder:
            second = gi.for_pack(pack, out)
        builder.assert_not_called()
        self.assertEqual(first, second)

    def test_for_pack_builds_when_missing(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        out = Path(tmp.name) / "out"
        files = gi.for_pack(pack, out)
        self.assertTrue(all(Path(p).is_file() for p in files))

    def test_build_without_mv(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        with mock.patch.object(gi, "MV", None):
            files = gi.build_gumroad_images(pack, Path(tmp.name) / "out")
        self.assertEqual(len(files), 6)

    def test_missing_pages_fallback(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = Path(tmp.name) / "empty"
        pack.mkdir()
        files = gi.build_gumroad_images(pack, Path(tmp.name) / "out")
        self.assertEqual(len(files), 6)

    def test_cli_outputs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        out = Path(tmp.name) / "cli"
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            code = gi.main([str(pack), "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertIn("Gumroad images:", stdout.getvalue())
        self.assertTrue((out / "01-thumbnail-600x600.jpg").is_file())

    def test_main_returns_zero(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pack = _pack(Path(tmp.name), pages=1)
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(gi.main([str(pack), "--out", str(Path(tmp.name) / "out")]), 0)

    def test_gallery_count_between_three_and_five(self):
        self.assertGreaterEqual(gi.gallery_count(len(self.pages)), gi.GALLERY_MIN)
        self.assertLessEqual(gi.gallery_count(len(self.pages)), gi.GALLERY_MAX)


if __name__ == "__main__":
    unittest.main()
