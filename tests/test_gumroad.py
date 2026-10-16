"""Gumroad is the one store: every niche recipe, the draft format and every built listing say Gumroad."""
import glob
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import product_builder as pb  # noqa: E402
import workforce  # noqa: E402


class GumroadTests(unittest.TestCase):
    def test_recipes_use_gumroad(self):
        paths = glob.glob(os.path.join(ROOT, "recipes", "niche_*.yaml"))
        self.assertEqual(len(paths), 6)   # 5 in the rotation + the paused Notion recipe (kept for later)
        for path in paths:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("payhip", text.lower(), path)
            self.assertIn("Gumroad", text, path)

    def test_draft_format_and_niches(self):
        self.assertIn("PLATFORM: Gumroad\n", workforce.DRAFT_FORMAT)
        self.assertEqual({n["platform"] for n in pb.NICHES.values()}, {"Gumroad"})

    def test_listing_platform_is_always_gumroad(self):
        reply = json.dumps({"name": "Budget Sheet", "short_name": "budget-sheet", "instructions": ["Open it."],
                            "listing": {"title": "Budget Sheet", "description": "Simple.", "price_usd": 7,
                                        "tags": ["a", "b", "c", "d", "e"], "buyer": "x", "platform": "Payhip"},
                            "sheets": [pb._SHEET_EXAMPLE]})
        spec, reason = pb.parse_spec(reply, "Budget Sheets Agent")
        self.assertIsNone(reason)
        self.assertEqual(spec["listing"]["platform"], "Gumroad")


if __name__ == "__main__":
    unittest.main()
