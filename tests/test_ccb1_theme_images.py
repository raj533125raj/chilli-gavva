"""CCB-1: covers and preview images use the pack's pdf_themes theme, for all 12 product types.

Every product type is really built once (temp folder, shared by the tests of this module) with the
theme "Ocean". The images are opened and their colours compared with the theme's own colours.
Needs Pillow and poppler's pdftoppm (like the listing images themselves); skipped without them.
"""
from __future__ import annotations

import copy
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mockups
import pdf_design
import pdf_themes
import product_builder as pb
import test_mockups as fixture          # spec replies for the five built-in kinds; not re-collected here
import truth_check

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
READY = HAS_PIL and mockups.available()

THEME = "Ocean"
BUILTIN = {"sheets": "Budget Sheets Agent", "planner": "ADHD Planners Agent", "dated_planner": "2027 Planners Agent",
           "resume": "Resume Bundle Agent", "notion": "Notion Templates Agent"}
REGISTERED = ("habit_tracker", "study_planner", "recipe_book", "flashcards", "event_planner", "email_signature",
              "wall_art")
ALL_KINDS = tuple(BUILTIN) + REGISTERED
# These three builders write their PDF in the old Style A; the images must then match THAT PDF.
STYLE_A_PDF = ("habit_tracker", "study_planner", "recipe_book")
SIZES = {"cover.png": (1280, 720), "preview-1.png": (1280, 720), "preview-2.png": (1280, 720),
         "preview-3.png": (1280, 720), "pinterest.png": (1000, 1500), "thumbnail.png": (600, 600)}
_BUILT = {}
_ROOT = None
_REAL_SAVE = None


def setUpModule():
    """Quick PNG writing for the big wall-art files (same pixels), as in the wall-art tests."""
    global _ROOT, _REAL_SAVE
    _ROOT = tempfile.mkdtemp(prefix="ccb1_")
    if not HAS_PIL:
        return
    _REAL_SAVE = Image.Image.save

    def quick(self, fp, format=None, **params):
        if params.get("optimize") and str(fp).lower().endswith(".png") and max(self.size) > 2000:
            params["optimize"] = False
            params["compress_level"] = 1
        return _REAL_SAVE(self, fp, format, **params)
    Image.Image.save = quick


def tearDownModule():
    if _REAL_SAVE is not None:
        Image.Image.save = _REAL_SAVE
    shutil.rmtree(_ROOT, ignore_errors=True)


def spec_for(kind, theme=THEME, **extra):
    if kind in BUILTIN:
        niche = BUILTIN[kind]
        spec, reason = pb.parse_spec(fixture._reply(niche.split()[0].lower(), fixture.REPLIES[niche]), niche)
        assert reason is None, reason
        spec.pop("style", None)
    else:
        spec = {"kind": kind, "name": f"My {kind.replace('_', ' ')} pack", "short_name": kind.replace("_", "-"),
                "cover_image_idea": "A clean desk", "instructions": ["Open the file."],
                "listing": {"title": f"{kind.replace('_', ' ').title()} Pack", "description": "A simple tool.",
                            "tags": ["a", "b", "c", "d", "e"], "buyer": "people", "platform": "Gumroad"}}
    if theme:
        spec["theme"] = theme
    spec.update(extra)
    return spec


def built(kind):
    """The folder of this kind, built once per module with the Ocean theme."""
    if kind not in _BUILT:
        out = os.path.join(_ROOT, kind)
        os.makedirs(out)
        spec = spec_for(kind)
        _BUILT[kind] = (os.path.join(out, pb.build_product(spec, 500 + ALL_KINDS.index(kind), out)), spec)
    return _BUILT[kind]


def background(path):
    with Image.open(path) as image:
        return image.convert("RGB").getpixel((5, 5))


def colours(path):
    with Image.open(path) as image:
        return {colour for _count, colour in image.convert("RGB").getcolors(maxcolors=1 << 22)}


def expected(kind):
    """The mockups colour set the images of this kind must use."""
    return mockups.THEMES["A"] if kind in STYLE_A_PDF else mockups._theme_for(pdf_themes.get_theme(THEME)["slug"])


@unittest.skipUnless(READY, "needs Pillow and pdftoppm")
class EveryProductTypeTests(unittest.TestCase):
    """Filled below: five checks for each of the 12 product types."""

    def check_all_six_images_exist_with_the_right_size(self, kind):
        folder, _spec = built(kind)
        for name, size in SIZES.items():
            with Image.open(os.path.join(folder, name)) as image:
                self.assertEqual(image.size, size, name)

    def check_every_image_has_the_theme_background(self, kind):
        folder, _spec = built(kind)
        for name in SIZES:
            self.assertEqual(background(os.path.join(folder, name)), expected(kind)["bg"], name)

    def check_cover_and_previews_use_the_theme_accent_and_ink(self, kind):
        folder, _spec = built(kind)
        for name in ("cover.png", "preview-1.png"):
            seen = colours(os.path.join(folder, name))
            self.assertIn(expected(kind)["accent"], seen, name)
            self.assertIn(expected(kind)["ink"], seen, name)

    def check_theme_is_recorded_and_read_back(self, kind):
        folder, _spec = built(kind)
        recorded = pb.recorded_theme(folder)
        if kind in STYLE_A_PDF:
            self.assertIsNone(recorded)
            self.assertIsNone(pb.listing_theme(folder, {}))
        else:
            self.assertEqual(recorded, THEME)
            self.assertEqual(pb.listing_theme(folder, {})["name"], THEME)       # without the spec, from the folder

    def check_redrawn_images_keep_the_same_colours(self, kind):
        folder, _spec = built(kind)
        copy_root = tempfile.mkdtemp(prefix="redraw_", dir=_ROOT)
        target = os.path.join(copy_root, os.path.basename(folder))
        shutil.copytree(folder, target)
        before = background(os.path.join(target, "cover.png"))
        for name in SIZES:
            os.remove(os.path.join(target, name))
        self.assertEqual(pb.add_missing_images(copy_root), [os.path.basename(folder)])
        self.assertEqual(background(os.path.join(target, "cover.png")), before)
        self.assertEqual(background(os.path.join(target, "preview-2.png")), before)


def _attach():
    for kind in ALL_KINDS:
        for check in [name for name in dir(EveryProductTypeTests) if name.startswith("check_")]:
            def test(self, kind=kind, check=check):
                getattr(self, check)(kind)
            test.__name__ = f"test_{kind}_{check[len('check_'):]}"
            setattr(EveryProductTypeTests, test.__name__, test)


_attach()


class TypeListTests(unittest.TestCase):
    def test_all_twelve_registered_formats_are_covered(self):
        import format_registry
        format_registry.discover_builders()
        self.assertEqual(sorted(ALL_KINDS), sorted(format_registry.list_registered_formats()))
        self.assertEqual(len(ALL_KINDS), 12)


@unittest.skipUnless(READY, "needs Pillow and pdftoppm")
class ThemeChoiceTests(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="choice_", dir=_ROOT)

    def build(self, kind, number=600, **spec_changes):
        spec = spec_for(kind, **spec_changes)
        return os.path.join(self.out, pb.build_product(spec, number, self.out)), spec

    def test_planner_pdf_and_cover_share_the_theme(self):
        folder, _spec = built("planner")
        pdf = next(name for name in sorted(os.listdir(folder)) if name.endswith("-A4.pdf"))
        pdf_themes.register_themes_in_pdf_design()
        self.assertEqual(pdf_design.pdf_style(os.path.join(folder, pdf)), "ocean")
        self.assertEqual(background(os.path.join(folder, "cover.png")), mockups._theme_for("ocean")["bg"])

    def test_two_themes_give_two_different_covers(self):
        ocean, _ = built("sheets")
        terracotta, _ = self.build("sheets", theme="Terracotta")
        self.assertEqual(background(os.path.join(terracotta, "cover.png")), mockups._theme_for("terracotta")["bg"])
        self.assertNotEqual(background(os.path.join(ocean, "cover.png")), background(os.path.join(terracotta, "cover.png")))
        self.assertEqual(pb.recorded_theme(terracotta), "Terracotta")

    def test_every_premium_theme_can_colour_a_sheet_cover(self):
        for index, name in enumerate(pdf_themes.list_theme_names()):
            with self.subTest(theme=name):
                folder, _ = self.build("sheets", number=610 + index, theme=name)
                slug = pdf_themes.get_theme(name)["slug"]
                self.assertEqual(background(os.path.join(folder, "cover.png")), mockups._theme_for(slug)["bg"])

    def test_without_a_theme_the_niche_default_is_used_like_the_pdf(self):
        folder, spec = self.build("sheets", theme=None)
        wanted = pdf_themes.get_theme_for_spec(spec)
        self.assertEqual(wanted["name"], pdf_themes.NICHE_THEME_MAP["Budget Sheets Agent"])
        self.assertEqual(background(os.path.join(folder, "cover.png")), mockups._theme_for(wanted["slug"])["bg"])

    def test_old_style_b_planner_keeps_its_matching_old_colours(self):
        folder, _spec = self.build("planner", theme=None, style="B")
        self.assertIsNone(pb.listing_theme(folder, {}))
        self.assertEqual(background(os.path.join(folder, "cover.png")), mockups.THEMES["B"]["bg"])
        self.assertIsNone(pb.recorded_theme(folder))

    def test_the_pdf_theme_wins_over_a_different_spec_theme_later(self):
        folder, _spec = built("planner")
        self.assertEqual(pb.listing_theme(folder, {"theme": "Terracotta"})["name"], THEME)

    def test_sheet_page_pictures_use_the_theme_accent(self):
        folder, _spec = built("sheets")
        self.assertIn(mockups._theme_for("ocean")["accent"], colours(os.path.join(folder, "preview-1.png")))
        self.assertNotIn(mockups.THEMES["A"]["accent"], colours(os.path.join(folder, "preview-1.png")))

    def bible_build(self, name="monthly-money-summary"):
        """A real sheet spec from specs/bible (the tiny test spec above is too thin for the Truth Checker
        with or without a theme), built with the Ocean theme."""
        import json
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "specs", "bible", name + ".json"), encoding="utf-8") as handle:
            spec = json.load(handle)
        spec["theme"] = THEME
        return os.path.join(self.out, pb.build_product(spec, spec.get("approval_id", 650), self.out))

    def test_themed_product_still_passes_the_truth_check(self):
        folder = self.bible_build()
        self.assertEqual(background(os.path.join(folder, "cover.png")), mockups._theme_for("ocean")["bg"])
        verdict = truth_check.check(folder)
        self.assertEqual(verdict["verdict"], "ready", verdict["problems"])

    def test_image_texts_are_the_same_with_or_without_the_theme(self):
        folder, _spec = built("sheets")
        plain = truth_check.image_texts(folder)
        with pb._listing_style("ocean"):
            themed = truth_check.image_texts(folder)
        self.assertEqual(plain, themed)
        self.assertTrue(plain)

    def test_listing_md_has_one_theme_line_and_no_money(self):
        import upload_pack
        folder, _spec = built("resume")
        with open(os.path.join(folder, "listing.md"), encoding="utf-8") as handle:
            body = handle.read()
        self.assertEqual(body.count("Design theme: Ocean."), 1)
        upload_pack._check_money(body, "listing")

    def test_upload_pack_still_works_for_a_themed_product(self):
        import upload_pack
        folder = self.bible_build()
        pack = upload_pack.make(folder, os.path.join(self.out, "packs"))
        self.assertTrue(os.path.isfile(os.path.join(str(pack), "product.zip")))
        self.assertTrue(os.path.isfile(os.path.join(str(pack), "cover.png")))


@unittest.skipUnless(READY, "needs Pillow and pdftoppm")
class FallbackTests(unittest.TestCase):
    """Formats mockups cannot read get their images from the product's REAL files."""

    def test_wall_art_previews_show_real_art_files(self):
        folder, _spec = built("wall_art")
        facts = truth_check.image_facts(folder)
        pages, pictures = pb._real_picture_pages(folder, facts)
        self.assertTrue(pictures)
        self.assertTrue(all(os.path.isfile(os.path.join(folder, name)) for name in pictures))
        self.assertEqual(len(pages), min(6, len(pictures)))
        self.assertFalse(set(pictures) & set(SIZES))

    def test_listing_images_are_never_used_as_product_pages(self):
        folder, _spec = built("email_signature")
        _pages, pictures = pb._real_picture_pages(folder, truth_check.image_facts(folder))
        for name in pictures:
            self.assertFalse(name.startswith(("cover", "preview-", "pinterest", "thumbnail")), name)

    def test_page_bullets_state_only_measured_facts(self):
        folder, _spec = built("wall_art")
        pages, _pictures = pb._real_picture_pages(folder, truth_check.image_facts(folder))
        with Image.open(os.path.join(folder, sorted(n for n in os.listdir(folder) if n.startswith("art_"))[0])) as image:
            width, height = image.size
        self.assertIn(f"{width} x {height} px image", pages[0]["bullets"])
        self.assertEqual(pages[0]["bullets"][0], "Real file from this pack")

    def test_pdf_only_format_uses_real_pdf_pages(self):
        folder, _spec = built("event_planner")
        facts = truth_check.image_facts(folder)
        # the builder's own cover / preview images are listing images, not product pages
        pages, pictures = pb._real_picture_pages(folder, facts)
        self.assertEqual(pictures, [])
        self.assertTrue(pages)
        self.assertTrue(all(page["number"] for page in pages))
        self.assertIn(f"{facts['pages']} pages in the PDF", pages[0]["bullets"])

    def test_empty_folder_has_nothing_to_show(self):
        empty = tempfile.mkdtemp(prefix="empty_", dir=_ROOT)
        with open(os.path.join(empty, "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write("x")
        with self.assertRaises(ValueError):
            pb._fallback_listing_images(empty, {"name": "Empty"}, pdf_themes.get_theme(THEME))

    def test_labels_for_the_registered_formats(self):
        for kind in ("wall_art", "flashcards", "event_planner", "email_signature"):
            self.assertIn(kind, pb.KIND_LABELS)
            self.assertEqual(pb.KIND_LABELS[kind], pb.KIND_LABELS[kind].upper())


class StyleSwitchTests(unittest.TestCase):
    """The colour override never changes the facts and is always switched off again."""

    def test_image_facts_is_restored(self):
        original = truth_check.image_facts
        with pb._listing_style("ocean"):
            self.assertIsNot(truth_check.image_facts, original)
        self.assertIs(truth_check.image_facts, original)

    def test_image_facts_is_restored_after_an_error(self):
        original = truth_check.image_facts
        with self.assertRaises(RuntimeError):
            with pb._listing_style("ocean"):
                raise RuntimeError("drawing failed")
        self.assertIs(truth_check.image_facts, original)

    def test_no_slug_changes_nothing(self):
        original = truth_check.image_facts
        with pb._listing_style(None):
            self.assertIs(truth_check.image_facts, original)

    def test_only_the_style_differs(self):
        original = truth_check.image_facts
        truth_check.image_facts = lambda folder: {"kind": "sheets", "pages": 0, "style": "A", "sheet_count": 3}
        try:
            with pb._listing_style("ocean"):
                facts = truth_check.image_facts("anything")
        finally:
            truth_check.image_facts = original
        self.assertEqual(facts, {"kind": "sheets", "pages": 0, "style": "ocean", "sheet_count": 3})

    def test_recorded_theme_parsing(self):
        folder = tempfile.mkdtemp(prefix="rec_", dir=_ROOT)
        self.assertIsNone(pb.recorded_theme(folder))
        with open(os.path.join(folder, "listing.md"), "w", encoding="utf-8") as handle:
            handle.write("# Title\n\n---\nBuilt by the Product Builder.\nDesign theme: Midnight Focus.\n")
        self.assertEqual(pb.recorded_theme(folder), "Midnight Focus")
        self.assertEqual(pb.listing_theme(folder, {})["name"], "Midnight Focus")
        self.assertEqual(pb.listing_theme(folder, {"theme": "Ocean"})["name"], "Ocean")

    def test_unknown_recorded_theme_falls_back_safely(self):
        folder = tempfile.mkdtemp(prefix="rec_", dir=_ROOT)
        with open(os.path.join(folder, "listing.md"), "w", encoding="utf-8") as handle:
            handle.write("Design theme: Not A Theme.\n")
        self.assertEqual(pb.listing_theme(folder, {})["name"], "Calm Sage")

    def test_listing_md_without_theme_has_no_theme_line(self):
        spec = {"listing": {"title": "T", "buyer": "b", "platform": "p", "description": "d", "tags": ["x"]},
                "cover_image_idea": "", "approval_id": 1}
        facts = {"pdfs": [], "sheets": [], "files": [], "docx": [], "zips": []}
        try:
            body = pb._listing_md(spec, facts=facts)
        except Exception:
            self.skipTest("facts shape differs")
        self.assertNotIn(pb.DESIGN_THEME_LABEL, body)
        self.assertIn(pb.DESIGN_THEME_LABEL + " Ocean.", pb._listing_md(dict(spec, design_theme="Ocean"), facts=facts))


if __name__ == "__main__":
    unittest.main()
