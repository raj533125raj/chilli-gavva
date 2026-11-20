"""CCG-5: the flashcards 'worksheets' option makes real worksheet pages in the pack theme.

Before: plain light-blue boxes as PNG pages and three 400 x 260 "Preview" images, no PDF. Now: A4 + Letter
PDFs (how-to page, activity pages, answer key), PNG pages and 1200 x 1600 previews of the real pages, in the
colours of the pack theme; the truth check and the sell proof (real product_builder path) stay SELLABLE.
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import builder_flashcards as bf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _pdf_pages(path):
    data = Path(path).read_bytes()
    return len(re.findall(rb"/Type\s*/Page[^s]", data))


class WorksheetModelTests(unittest.TestCase):
    def test_every_activity_has_real_pages(self):
        for activity, has_key in (("trace", False), ("match", True), ("count_colour", True), ("simple_math", True)):
            with self.subTest(activity=activity):
                spec = {"kind": "worksheets", "topic": "numbers", "activity": activity, "title": "Numbers Fun"}
                w, h, pages = bf.worksheet_pages(spec, "A4")
                self.assertGreaterEqual(len(pages), 3)                # how-to + activity page(s) (+ key)
                texts = [op[3] for ops in pages for op in ops if op[0] == "text"]
                self.assertIn("Name: ______________", texts)
                self.assertEqual(any("Answer Key" in t for t in texts), has_key)
                for ops in pages:                                       # everything stays on the page
                    for op in ops:
                        if op[0] == "text":
                            self.assertTrue(0 <= op[1] <= w and 0 <= op[2] <= h, op)
                        if op[0] == "rect":
                            self.assertTrue(op[1] >= 0 and op[1] + op[3] <= w + 0.5 and op[2] >= 0
                                            and op[2] + op[4] <= h + 0.5, op)

    def test_answer_key_is_true(self):
        _w, _h, pages = bf.worksheet_pages({"kind": "worksheets", "topic": "numbers", "activity": "simple_math"},
                                           "Letter")
        key = [op[3] for op in pages[-1] if op[0] == "text" and " = " in op[3] and op[3][0].isdigit()]
        self.assertEqual(len(key), 24)
        for line in key:
            a, b, total = map(int, re.findall(r"(\d+) \+ (\d+) = (\d+)", line)[0])
            self.assertEqual(a + b, total)
            self.assertLessEqual(total, 10)

    def test_pages_use_the_pack_theme(self):
        spec = {"kind": "worksheets", "topic": "alphabet", "activity": "match", "theme": "Ocean"}
        tok = bf.theme_tokens(spec)
        import pdf_themes
        self.assertEqual(tok["accent"], pdf_themes.get_theme_for_spec(spec)["tokens"]["accent"])
        _w, _h, pages = bf.worksheet_pages(spec, "A4")
        colours = {c for ops in pages for op in ops for c in op if isinstance(c, str) and c.startswith("#")}
        self.assertIn(tok["accent"], colours)
        self.assertNotIn("lightblue", colours)

    def test_flashcards_set_worksheets_is_the_option(self):
        self.assertTrue(bf.is_worksheets({"kind": "flashcards", "set": "worksheets"}))
        self.assertTrue(bf.is_worksheets({"kind": "worksheets"}))
        self.assertFalse(bf.is_worksheets({"kind": "flashcards"}))


@unittest.skipUnless(HAS_PIL, "PNG pages and previews need Pillow")
class WorksheetBuildTests(unittest.TestCase):
    def test_build_makes_pdfs_pngs_and_normal_previews(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = {"kind": "flashcards", "set": "worksheets", "topic": "alphabet", "activity": "trace",
                    "short_name": "abc-tracing", "title": "ABC Tracing Worksheets"}
            created = bf.build(spec, tmp)
            self.assertIn("abc-tracing-A4.pdf", created)
            self.assertIn("abc-tracing-Letter.pdf", created)
            self.assertEqual(_pdf_pages(Path(tmp) / "abc-tracing-A4.pdf"), 1 + 5)   # how-to + 26 letters / 6
            pngs = [n for n in created if n.startswith("worksheet_trace_alphabet_page_")]
            self.assertEqual(len(pngs), 5)
            with Image.open(Path(tmp) / pngs[0]) as img:
                self.assertEqual(img.size, (2480, 3508))
                self.assertGreater(len(img.getcolors(1 << 20) or []), 3)          # not a plain box
            for name in ("preview_1.png", "preview_2.png", "preview_3.png"):
                with Image.open(Path(tmp) / name) as img:
                    self.assertEqual(img.size, bf.PREVIEW_SIZE)
            readme = (Path(tmp) / "README-HOW-TO-INSTALL.txt").read_text()
            self.assertIn("Trace and Write", readme)
            self.assertNotIn("Cut along the dashed cut lines", readme)

    def test_flashcards_deck_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            created = bf.build({"kind": "flashcards", "topic": "alphabet", "short_name": "abc"}, tmp)
            self.assertIn("abc-A4.pdf", created)
            self.assertFalse(any(n.startswith("worksheet_") for n in created))


@unittest.skipUnless(HAS_PIL, "the sell proof needs Pillow for listing images")
class WorksheetSellProofTests(unittest.TestCase):
    """The real product_builder path of tools/sell_proof.py with the worksheets option."""

    def test_truth_check_and_sell_proof_stay_sellable(self):
        import sell_proof
        base = sell_proof.spec_for("flashcards")
        for activity, topic in (("trace", "alphabet"), ("match", "alphabet"), ("count_colour", "shapes"),
                                ("simple_math", "numbers")):
            with self.subTest(activity=activity):
                title = f"Kids {activity.replace('_', ' ').title()} Worksheets"
                spec = dict(base, set="worksheets", activity=activity, topic=topic, name=title, title=title,
                            short_name=f"kids-{activity}")
                with tempfile.TemporaryDirectory() as tmp, \
                        mock.patch.object(sell_proof, "spec_for", return_value=spec):
                    row = sell_proof.prove("flashcards", Path(tmp))
                self.assertTrue(row["truth"].startswith("ready"), row)
                self.assertEqual(row["status"], "SELLABLE", row)
                self.assertEqual(row["zip"], "ok", row)
                self.assertTrue(row["sell_message"], row)


if __name__ == "__main__":
    unittest.main()
