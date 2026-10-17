"""Roadmap #12: draft Pinterest pins for the ADHD Printable Planner Kit (real pages, true facts, nothing posted)."""
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mockups  # noqa: E402
import product_builder as pb  # noqa: E402
import truth_check as tc  # noqa: E402

BUNDLE = os.path.join(ROOT, "specs", "bundles", "adhd-printable-planner-kit.json")


class PinSpecTests(unittest.TestCase):
    def test_five_angles_true_text_ai_disclosed(self):
        with open(BUNDLE, encoding="utf-8") as f:
            spec = json.load(f)
        pins = spec["pins"]
        self.assertEqual([p["angle"] for p in pins], ["Today page", "Energy planner", "Low-energy reset",
                                                       "What's inside", "Who it is for"])
        self.assertEqual(len(spec["pin_keywords"]), 10)
        self.assertEqual(len(set(spec["pin_keywords"])), 10)
        self.assertEqual(len(spec["pin_boards"]), 2)
        for p in pins:
            self.assertLessEqual(len(p["title"]), 100, p["file"])           # Pinterest title limit
            self.assertLessEqual(len(p["description"]), 500, p["file"])     # Pinterest description limit
            self.assertIn("help from AI tools", p["description"])
            self.assertNotIn("checked page by page", p["description"])      # not proven by the files
            for n in re.findall(r"\b(\d+) (?:undated |printable )?pages\b", p["description"] + p["headline"]):
                self.assertEqual(n, "14")


@unittest.skipUnless(mockups.available(), "needs Pillow and poppler (free image tools)")
class PinBuildTests(unittest.TestCase):
    def test_pins_are_1000x1500_drafts_of_real_pages(self):
        from PIL import Image
        spec = pb.bundle_spec(BUNDLE)
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, pb.build_product(spec, "bundle", d, folder_name="kit"))
            names = sorted(os.listdir(os.path.join(folder, "pins")))
            self.assertEqual(names, ["PINS.md"] + sorted(p["file"] for p in spec["pins"]))
            for p in spec["pins"]:
                with Image.open(os.path.join(folder, "pins", p["file"])) as im:
                    self.assertEqual(im.size, (1000, 1500))
            titles = tc.facts(folder)["titles"]
            for p in spec["pins"]:
                for page in p["pages"]:
                    self.assertTrue(page == "cover" or page in titles, page)
            with open(os.path.join(folder, "pins", "PINS.md"), encoding="utf-8") as f:
                md = f.read()
            self.assertIn("DRAFTS ONLY - nothing is posted", md)
            self.assertEqual(tc.check(folder)["verdict"], "ready")

    def test_a_page_that_is_not_in_the_product_is_refused(self):
        spec = pb.bundle_spec(BUNDLE)
        spec["pins"] = [dict(spec["pins"][0], pages=["Weekly Spread"])]
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, pb.build_product(dict(spec, pins=[]), "bundle", d, folder_name="kit"))
            with self.assertRaises(ValueError):
                mockups.write_pins(folder, spec)


if __name__ == "__main__":
    unittest.main()
