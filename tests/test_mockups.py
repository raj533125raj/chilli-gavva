"""Tests for mockups.py: listing images are real pictures of THIS product, the right size, with Inter text,
only proven facts, no brand names and no cut text. Needs the free image tools (Pillow + poppler)."""
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mockups  # noqa: E402
import product_builder as pb  # noqa: E402
import truth_check  # noqa: E402

BRANDS = ("google", "excel", "notion", "linkedin", "microsoft", "canva", "etsy", "gumroad", "payhip")


def _reply(kind, extra):
    return json.dumps({"name": f"Test {kind} for Google Sheets", "short_name": f"test-{kind}",
                       "listing": {"title": f"Test {kind}", "description": "Simple and honest.", "price_usd": 7,
                                   "tags": ["a", "b", "c", "d", "e"], "buyer": "busy adults"},
                       "instructions": ["Open it."], **extra})


REPLIES = {
    "Budget Sheets Agent": {"sheets": [pb._SHEET_EXAMPLE]},
    "ADHD Planners Agent": {"daily_page": True, "pages": [{"title": "Brain Dump", "sections": [
        {"heading": "Thoughts", "type": "lines", "count": 6}]}]},
    "2027 Planners Agent": {"pages": [{"title": "Class Roster", "sections": [
        {"heading": "Students", "type": "lines", "count": 20}]}]},
    "Resume Bundle Agent": {"resume": {"sections": [{"heading": "Experience", "bullets": ["[Job Title], [Company]"]}]},
                            "cover_letter": {"paragraphs": ["Dear [Name],", "Kind regards,"]}},
    "Notion Templates Agent": {"pages": [{"title": "Start Here", "markdown": "# Start Here\n\nImport the files."}],
                               "databases": [{"name": "Clients", "columns": ["Name", "Status"],
                                              "rows": [["Ann", "Lead"]]}]},
}


def build(niche, out):
    spec, reason = pb.parse_spec(_reply(niche.split()[0].lower(), REPLIES[niche]), niche)
    assert reason is None, reason
    return os.path.join(out, pb.build_product(spec, 70, out))


def drawn_texts(folder):
    mockups.DRAWN = []
    try:
        mockups.make_images(folder)
        return list(mockups.DRAWN)
    finally:
        mockups.DRAWN = None


@unittest.skipUnless(mockups.available(), "needs Pillow and poppler (free image tools)")
class ImageTests(unittest.TestCase):
    def test_image_sizes_for_every_kind(self):
        from PIL import Image
        for niche in REPLIES:
            with self.subTest(niche=niche), tempfile.TemporaryDirectory() as d:
                folder = build(niche, d)
                sizes = {}
                for name in ("cover.png", "preview-1.png", "preview-2.png", "preview-3.png", "pinterest.png"):
                    with Image.open(os.path.join(folder, name)) as im:
                        sizes[name] = im.size
                self.assertEqual(sizes["cover.png"], (1280, 720))
                self.assertEqual(sizes["preview-1.png"], (1280, 720))
                self.assertEqual(sizes["preview-3.png"], (1280, 720))
                self.assertEqual(sizes["pinterest.png"], (1000, 1500))

    def test_facts_on_images_are_proven_and_nothing_is_cut(self):
        with tempfile.TemporaryDirectory() as d:
            folder = build("2027 Planners Agent", d)
            drawn = drawn_texts(folder)
            facts = truth_check.image_facts(folder)
        self.assertIn(f"{facts['pages']} pages", drawn)
        self.assertIn("A4 + US Letter", drawn)
        self.assertIn("Dated 2027", drawn)
        self.assertIn(f"INSIDE · PAGE 4 OF {facts['pages']}", drawn)     # January is page 4
        self.assertFalse([t for t in drawn if t.endswith("...") or "**" in t], drawn)
        joined = " ".join(drawn).lower()
        for brand in BRANDS:
            self.assertNotIn(brand, joined)

    def test_shown_pages_are_real_pages_of_this_product(self):
        with tempfile.TemporaryDirectory() as d:
            folder = build("ADHD Planners Agent", d)
            info = mockups.product_info(folder)
            titles = truth_check.facts(folder)["titles"]
        self.assertEqual(info["theme"], mockups.THEMES["B"])            # ADHD niche: Style B
        self.assertEqual([p["title"] for p in info["pages"]], ["Today", "Brain Dump"])
        for p in info["pages"]:
            self.assertIn(p["title"], titles)
            self.assertEqual(len(p["bullets"]), 3)

    def test_undated_planner_never_says_dated(self):
        with tempfile.TemporaryDirectory() as d:
            folder = build("ADHD Planners Agent", d)
            chips = mockups.chips(truth_check.image_facts(folder))
        self.assertFalse([c for c in chips if "Dated" in c])
        self.assertIn("A4 + US Letter", chips)

    def test_sheet_picture_shows_the_real_columns(self):
        with tempfile.TemporaryDirectory() as d:
            drawn = drawn_texts(build("Budget Sheets Agent", d))
        self.assertIn("Client", drawn)          # a real column of the workbook
        self.assertTrue(any(re.fullmatch(r"\d+ sheets", t) for t in drawn))

    def test_truth_checker_catches_an_unproven_page_count_on_an_image(self):
        f = {"kind": "pdf", "pages": 6, "sheets": [], "page_items": [], "texts": []}
        problems = truth_check.file_quality(f, ["Planner", "16 pages"])
        self.assertTrue([p for p in problems if "16 pages" in p], problems)


class HelperTests(unittest.TestCase):
    def test_no_brands_helper(self):
        self.assertEqual(mockups.no_brands("Budget Tracker for Google Sheets"), "Budget Tracker")
        self.assertEqual(mockups.no_brands("Résumé kit"), "Resume kit")
        self.assertEqual(mockups.no_brands("Planner in Notion"), "Planner")

    @unittest.skipUnless(mockups.available(), "needs Pillow")
    def test_long_names_shrink_and_wrap_but_are_never_cut(self):
        lines, f = mockups.fit("word " * 80, "bold", 64, 38, 470, 3)
        self.assertEqual(" ".join(lines).split(), ["word"] * 80)     # every word is still there
        self.assertEqual(f.size, 38)
        self.assertTrue(all(mockups.text_w(l, f) <= 470 for l in lines))

    def test_image_problem_never_blocks_the_product(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(mockups, "write_mockups",
                                                                   side_effect=RuntimeError("boom")):
            folder = build("Budget Sheets Agent", d)
            names = os.listdir(folder)
        self.assertIn("LICENSE.txt", names)
        self.assertNotIn("cover.png", names)

    def test_inter_font_and_licence_are_in_the_repo(self):
        for name in ("Inter-Regular.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf", "Inter-LICENSE.txt"):
            self.assertTrue(os.path.isfile(os.path.join(mockups.FONT_DIR, name)), name)
        with open(os.path.join(mockups.FONT_DIR, "Inter-LICENSE.txt"), encoding="utf-8") as f:
            self.assertIn("SIL OPEN FONT LICENSE", f.read().upper())


if __name__ == "__main__":
    unittest.main()
