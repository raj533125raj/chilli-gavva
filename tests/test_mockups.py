"""Tests for mockups.py: listing images are valid PNGs of the right size, with no brand names."""
import os
import struct
import sys
import tempfile
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mockups  # noqa: E402
import product_builder as pb  # noqa: E402

SPECS = {
    "sheets": {"kind": "sheets", "name": "Freelancer Income Tracker for Google Sheets",
               "sheets": [pb._SHEET_EXAMPLE, {"name": "Excel Bills", "columns": [{"name": "Bill"}, {"name": "Due"}]}]},
    "planner": {"kind": "planner", "name": "Energy-Based Daily Planner",
                "pages": [{"title": "Daily Plan", "sections": [
                    {"heading": "Energy", "type": "checklist", "items": ["High", "Low"], "count": 3},
                    {"heading": "Notes", "type": "notes", "items": [], "count": 3}]}]},
    "notion": {"kind": "notion", "name": "Notion Client CRM",
               "pages": [{"title": "Start Here", "markdown": "# Start Here\n\nOpen Notion and import."}],
               "databases": [{"name": "Clients", "columns": ["Name", "Status"], "rows": [["Ann", "Lead"]]}]},
    "resume": {"kind": "resume", "name": "ATS Resume Bundle for LinkedIn",
               "resume": [{"heading": "Experience", "bullets": ["[Job Title]"]}],
               "cover_letter": ["Dear [Name],"], "tracker": pb._SHEET_EXAMPLE},
}
BRANDS = ("google", "sheets", "excel", "notion", "linkedin", "microsoft", "canva", "etsy", "gumroad", "payhip")


def png_info(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0]
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        chunks[kind] = chunks.get(kind, b"") + body
        pos += 12 + length
    w, h = struct.unpack(">II", chunks[b"IHDR"][:8])
    raw = zlib.decompress(chunks[b"IDAT"])
    assert len(raw) == h * (1 + 3 * w)
    return w, h


class MockupTests(unittest.TestCase):
    def test_all_images_for_every_kind(self):
        for kind, spec in SPECS.items():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as d:
                files = mockups.write_mockups(d, spec)
                self.assertEqual(files, ["cover.png", "preview-1.png", "preview-2.png", "preview-3.png",
                                         "pinterest.png"])
                sizes = {f: png_info(open(os.path.join(d, f), "rb").read()) for f in files}
                self.assertEqual(sizes["cover.png"], (1280, 720))
                self.assertEqual(sizes["preview-2.png"], (1280, 720))
                self.assertEqual(sizes["pinterest.png"], (1000, 1500))
                self.assertLess(max(os.path.getsize(os.path.join(d, f)) for f in files), 300_000)

    def test_no_brand_names_are_drawn(self):
        drawn = []
        real = mockups.Canvas.text

        def spy(self, x, y, text, color, scale=2):
            drawn.append(text)
            return real(self, x, y, text, color, scale)
        with mock.patch.object(mockups.Canvas, "text", spy), tempfile.TemporaryDirectory() as d:
            for spec in SPECS.values():
                mockups.write_mockups(d, spec)
        joined = " ".join(drawn).lower()
        for brand in BRANDS:
            self.assertNotIn(brand, joined)
        self.assertIn("Freelancer Income Tracker", " ".join(drawn))
        self.assertIn("Daily Plan", " ".join(drawn))       # previews come from the product's first pages

    def test_no_brands_helper(self):
        self.assertEqual(mockups.no_brands("Budget Tracker for Google Sheets"), "Budget Tracker")
        self.assertEqual(mockups.no_brands("Résumé kit"), "Resume kit")
        self.assertEqual(mockups.no_brands("Planner in Notion"), "Planner")

    def test_previews_always_three(self):
        self.assertEqual(len(mockups.preview_pages(SPECS["planner"])), 3)
        self.assertEqual(len(mockups.preview_pages({"kind": "planner", "name": "X", "pages": []})), 1)

    def test_long_names_are_cut(self):
        lines, scale = mockups._fit_title("word " * 80, 900, 3, 9)
        self.assertLessEqual(len(lines), 3)
        self.assertGreaterEqual(scale, 3)

    def test_image_problem_never_blocks_the_product(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(mockups, "write_mockups",
                                                                   side_effect=RuntimeError("boom")):
            spec = pb.parse_spec(_spec_json(), "Budget Sheets Agent")[0]
            folder = pb.build_product(spec, 7, out_dir=d)
            names = os.listdir(os.path.join(d, folder))
        self.assertIn("LICENSE.txt", names)
        self.assertNotIn("cover.png", names)


def _spec_json():
    import json
    return json.dumps({"name": "Budget Sheet", "short_name": "budget-sheet",
                       "listing": {"title": "Budget Sheet", "description": "Simple.", "price_usd": 7,
                                   "tags": ["a", "b", "c", "d", "e"], "buyer": "freelancers", "platform": "Payhip"},
                       "instructions": ["Open it."], "sheets": [pb._SHEET_EXAMPLE]})


if __name__ == "__main__":
    unittest.main()
