"""PDF pages show dashes, quotes and apostrophes correctly, never cut text off, and have no near-empty pages."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mockups  # noqa: E402
import product_builder as pb  # noqa: E402
import truth_check as tc  # noqa: E402


def read_texts(path):
    return [t for p in tc._pdf_pages(path) for _, _, _, _, t in p["items"]]


class CharacterTests(unittest.TestCase):
    def test_dashes_quotes_apostrophes_are_real_characters(self):
        raw = pb._pdf_text("6–10 am — “plan” it’s ‘ok’ … • café")
        self.assertNotIn("?", raw)
        self.assertIn("\x96", raw)            # en dash in the PDF's Windows-1252 font encoding
        self.assertIn("\x97", raw)            # em dash
        self.assertIn("\x92", raw)            # right single quote (apostrophe)

    def test_unknown_symbols_never_become_question_marks(self):
        raw = pb._pdf_text("→ ≥ ∑ ✓ ‑ Ω \U0001F600 中")
        self.assertNotIn("?", raw)
        self.assertIn("->", raw)
        self.assertIn(">=", raw)

    def test_round_trip_in_a_real_pdf(self):
        spec = {"name": "Day Plan", "listing": {"title": "Day Plan"},
                "pages": [{"title": "Day – Plan", "subtitle": "", "sections": [
                    {"heading": "Parents’ evening", "type": "schedule", "items": ["Morning (6–10 am)"],
                     "count": 1}]}]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p-A4.pdf")
            pb.build_planner_pdf(path, spec, "A4")
            texts = read_texts(path)
        self.assertIn("Day – Plan", texts)
        self.assertIn("PARENTS’ EVENING", texts)           # section headings are printed in capitals
        self.assertIn("Morning (6\u201310 am)", texts)     # whole label, dash intact (no "?")
        self.assertFalse(any("?" in t for t in texts))


class NothingCutTests(unittest.TestCase):
    def test_long_title_item_and_label_wrap_instead_of_being_cut(self):
        long_item = "Check the class list and update the seating plans for every group you teach this term please"
        spec = {"name": "P", "listing": {"title": "P"}, "pages": [{
            "title": "A very long page title that would never fit on one single line of the page",
            "subtitle": "", "sections": [
                {"heading": "Checklist", "type": "checklist", "items": [long_item], "count": 1},
                {"heading": "Times", "type": "schedule", "items": ["Afternoon Follow-up calls"], "count": 1}]}]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p-A4.pdf")
            pb.build_planner_pdf(path, spec, "A4")
            texts = read_texts(path)
            items = [i for page in tc._pdf_pages(path) for i in page["items"]]
        joined = " ".join(texts)
        for word in long_item.split() + "A very long page title that would never fit on one single line".split():
            self.assertIn(word, joined)
        self.assertIn("Follow-up", joined)
        for font, size, x, _, t in items:     # every line ends inside the page (A4 is 595 points wide)
            self.assertLessEqual(x + pb.text_width(t, size, font == "F2"), 595 - 40, t)

    def test_wrap_text(self):
        lines = pb.wrap_text("one two three four five six seven eight", 10, 60)
        self.assertEqual(" ".join(lines), "one two three four five six seven eight")
        self.assertTrue(all(pb.text_width(l, 10) <= 60 for l in lines))
        self.assertTrue(all(pb.text_width(l, 10) <= 60 for l in pb.wrap_text("x" * 80, 10, 60)))


class OverflowTests(unittest.TestCase):
    def test_no_almost_empty_page_after_overflow(self):
        spec = {"name": "P", "listing": {"title": "P"}, "pages": [{
            "title": "Week plan", "subtitle": "", "sections": [
                {"heading": f"Block {i}", "type": "lines", "items": [], "count": 12} for i in range(9)]}]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p-A4.pdf")
            n = pb.build_planner_pdf(path, spec, "A4")
            quality = tc.file_quality(tc.facts(d))
        self.assertGreaterEqual(n, 4)                  # cover, How to use, and the page really overflowed
        self.assertFalse([q for q in quality if "almost empty" in q], quality)

    def test_short_page_grows_writing_lines_to_the_footer(self):
        spec = {"name": "P", "listing": {"title": "P"}, "pages": [{
            "title": "Tiny", "subtitle": "", "sections": [
                {"heading": "Only", "type": "checklist", "items": ["One"], "count": 1}]}]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p-A4.pdf")
            pb.build_planner_pdf(path, spec, "A4")
            page = tc._pdf_pages(path)[2]
        self.assertGreater(page["drawn"], 20)
        self.assertLess(page["low"], pb.BOTTOM + 25)          # lines reach down to the footer


class ImageTextTests(unittest.TestCase):
    def test_images_keep_apostrophes_and_dashes(self):
        self.assertEqual(mockups.no_brands("Parents’ evening – term"), "Parents' evening - term")

    def test_image_text_keeps_every_letter(self):
        self.assertEqual(mockups.no_brands("Tomorrow’s first step — 6–10 am"), "Tomorrow's first step - 6-10 am")


if __name__ == "__main__":
    unittest.main()
