"""CCD-1: tools/showroom.py really builds samples and writes contact sheets + index.html (temp folder)."""
import json
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

from tools import showroom


@unittest.skipUnless(Image is not None, "needs Pillow")
class ShowroomTests(unittest.TestCase):
    def test_builds_contact_sheets_and_index(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "showroom"
            rows = showroom.build(out, ["debt_payoff", "adhd_planner", "resume"])
            self.assertEqual([r["label"] for r in rows],
                             ["adhd_planner-energy", "adhd_planner-time_blindness", "adhd_planner-focus",
                              "debt_payoff", "resume"])
            for row in rows:
                self.assertTrue(row["ok"], row)
                self.assertEqual(row["tiles"][:4], ["cover.png", "preview-1.png", "preview-2.png", "preview-3.png"])
                with Image.open(out / row["sheet"]) as im:
                    self.assertEqual(im.size, (3 * showroom.TILE_W + 40, 70 + 2 * (showroom.TILE_H + showroom.LABEL_H) + 30))
            self.assertEqual(rows[0]["pages_shown"], 2)
            page = (out / "index.html").read_text()
            self.assertIn("5/5 products with real pictures", page)
            self.assertIn("<img src='debt_payoff.png'", page)
            self.assertEqual(len(json.loads((out / "showroom.json").read_text())), 5)

    def test_refuses_output_inside_the_repo(self):
        with self.assertRaises(ValueError):
            showroom.build(showroom.ROOT / "showroom-out", ["resume"])


if __name__ == "__main__":
    unittest.main()
