"""CCB-1: three ADHD-friendly planners rebuilt to sell (energy, time-blindness, focus & brain dump).

24+ full pages each, header band + one accent colour, page numbers, How to use page, undated, labels never
cut, no medical claims. Reads the real PDF text from the uncompressed content streams (no poppler needed).
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import builder_adhd_planner as adhd  # noqa: E402
import format_registry  # noqa: E402

EDITIONS = ["energy", "time_blindness", "focus"]
MEDICAL = re.compile(r"\b(cure[sd]?|treat(?:s|ment)?|symptoms?|diagnos\w*|therap\w*|medication|disorder|"
                     r"guarantee\w*|proven|clinically|doctor[- ]approved)\b", re.I)


def pdf_pages(path):
    """Text of each page, from the content streams in page order."""
    data = Path(path).read_bytes().decode("latin-1")
    streams = re.findall(r"stream\n(.*?)\nendstream", data, re.S)
    return [" ".join(re.findall(r"\(((?:\\.|[^\\)])*)\) Tj", s)).replace("\\(", "(").replace("\\)", ")")
            for s in streams if " Tj" in s or " re f" in s]


@unittest.skipUnless(Image is not None, "needs Pillow")
class AdhdPlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = {}
        for edition in EDITIONS:
            out = Path(cls.tmp.name) / edition
            cls.out[edition] = (out, adhd.build({"edition": edition}, out))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_files_and_24_plus_pages_in_both_papers(self):
        for edition in EDITIONS:
            with self.subTest(edition):
                out, names = self.out[edition]
                self.assertEqual(sorted(names), sorted(["adhd_planner_a4.pdf", "adhd_planner_letter.pdf", "cover.png",
                                                        "preview_1.png", "preview_2.png", "preview_3.png",
                                                        "README.txt", "LICENSE.txt", "manifest.json"]))
                a4, letter = pdf_pages(out / "adhd_planner_a4.pdf"), pdf_pages(out / "adhd_planner_letter.pdf")
                self.assertGreaterEqual(len(a4), 24)
                self.assertEqual(len(a4), len(letter))
                self.assertIn(b"/MediaBox [0 0 595.28 841.89]", (out / "adhd_planner_a4.pdf").read_bytes())

    def test_every_page_has_band_title_page_number_and_is_undated(self):
        for edition in EDITIONS:
            with self.subTest(edition):
                out, _ = self.out[edition]
                pages = pdf_pages(out / "adhd_planner_letter.pdf")
                titles = [p[0] for p in adhd.page_list(edition)]
                self.assertIn("UNDATED PRINTABLE PLANNER", pages[0])
                self.assertTrue(titles[0].startswith("How to Use"))
                for number, text in enumerate(pages, 1):
                    self.assertIn(f"Page {number} of {len(pages)}", text)
                    if number > 1:
                        self.assertIn(titles[number - 2], text)
                    self.assertIsNone(re.search(r"\b20\d\d\b", text), text[:120])
                    self.assertIsNone(re.search(r"\b(January|February|March|April|June|July|August|September|"
                                                r"October|November|December)\b", text))
                raw = (out / "adhd_planner_letter.pdf").read_bytes().decode("latin-1")
                accent = adhd.EDITIONS[edition]["accent"]
                rgb = " ".join(f"{int(accent[i:i + 2], 16) / 255:.3f}".rstrip("0").rstrip(".") for i in (1, 3, 5))
                self.assertGreaterEqual(raw.count(" rg"), len(pages))   # filled header band on every page
                self.assertTrue(rgb.split()[0][:3] in raw)

    def test_how_to_use_and_no_medical_claims(self):
        for edition in EDITIONS:
            with self.subTest(edition):
                out, _ = self.out[edition]
                text = " ".join(pdf_pages(out / "adhd_planner_a4.pdf"))
                text += (out / "README.txt").read_text()
                self.assertIn("How it works", text)
                self.assertIn("not medical advice", text)
                self.assertIsNone(MEDICAL.search(text.replace("not medical advice", "")))

    def test_previews_are_real_and_different(self):
        import visual_qa
        for edition in EDITIONS:
            with self.subTest(edition):
                out, _ = self.out[edition]
                images = [out / n for n in ("cover.png", "preview_1.png", "preview_2.png", "preview_3.png")]
                for i, a in enumerate(images):
                    with Image.open(a) as im:
                        self.assertEqual(im.size, (1200, 800))
                    for b in images[i + 1:]:
                        self.assertFalse(visual_qa.duplicate_images(a, b), (a.name, b.name))

    @unittest.skipUnless(__import__("visual_qa").available(), "needs poppler (pdftoppm, pdftotext)")
    def test_visual_qa_zero_flags_and_full_pages(self):
        import visual_qa
        for edition in EDITIONS:
            with self.subTest(edition):
                out, _ = self.out[edition]
                report = visual_qa.check_pack(out, Path(self.tmp.name) / "qa" / edition, "adhd_planner")
                self.assertEqual(report["flags"], [])
                self.assertLessEqual(max(p["empty_bottom"] for p in report["pages"]), 0.15)

    def test_labels_are_never_cut(self):
        import pdf_design
        canvas = pdf_design.Pdf("A4")
        canvas.new_page()
        with self.assertRaises(adhd.LabelCut):
            adhd._fit(canvas, "A label far too long for a tiny box " * 3, 10, 40)
        with self.assertRaises(adhd.LabelCut):
            adhd._section(canvas, 50, 100, 200, 60, adhd.S("table", "Too many", columns=[("A", 1)],
                                                          rows=[str(i) for i in range(20)]), "#336699")

    def test_registered_and_editions_from_titles(self):
        format_registry.discover_builders(force_reload=True)
        self.assertIn("adhd_planner", format_registry.list_registered_formats())
        cases = {"Energy-Based Daily Planner for ADHD Adults": "energy",
                 "Time Blindness Routine Planner Printable": "time_blindness",
                 "Brain Dump Sheets and Focus Planner": "focus"}
        for title, edition in cases.items():
            self.assertEqual(format_registry.classify(title), "adhd_planner", title)
            self.assertEqual(adhd.normalize_spec({"title": title})["edition"], edition)
        with self.assertRaises(ValueError):
            adhd.normalize_spec("nope")


if __name__ == "__main__":
    unittest.main()
