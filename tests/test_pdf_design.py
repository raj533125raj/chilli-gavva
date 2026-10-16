"""The approved page design (pdf_design.py): page fill, footer page numbers, nothing cut, Style A / B."""
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pdf_design as pd  # noqa: E402
import truth_check as tc  # noqa: E402

LONG = "Check the class list and update every seating plan for each group you teach this term, please"
PLANNER = {"name": "Time-Blindness Daily Planner", "niche": "ADHD Planners Agent", "daily_page": True,
           "listing": {"title": "x", "buyer": "adults who lose track of time"}, "instructions": ["Print it."],
           "pages": [{"title": "Weekly Overview with a very long title that has to wrap onto a second line",
                      "subtitle": "See the week", "sections": [
                          {"heading": "Goals", "type": "checklist", "items": [LONG, "Two", "Three"]},
                          {"heading": "Appointments", "type": "lines", "count": 4},
                          {"heading": "Day slots", "type": "schedule",
                           "items": ["Monday", "Afternoon follow-up calls with clients", "Wednesday"]},
                          {"heading": "Reflection", "type": "lines", "count": 3}]},
                     {"title": "Notes", "sections": [{"heading": "Open notes", "type": "lines", "count": 40},
                                                     {"heading": "More notes", "type": "lines", "count": 30}]}]}
DATED = {"name": "2027 Teacher Planner", "niche": "2027 Planners Agent",
         "listing": {"title": "x", "buyer": "classroom teachers"},
         "pages": [{"title": "Class Roster", "sections": [{"heading": "Students", "type": "lines", "count": 30}]}]}


def built(spec, size, dated=False):
    d = tempfile.mkdtemp()
    path = os.path.join(d, f"p-{size}.pdf")
    (pd.build_dated_planner_pdf if dated else pd.build_planner_pdf)(path, spec, size)
    return path


class DesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {(name, size): built(spec, size, name == "dated")
                     for name, spec in (("planner", PLANNER), ("dated", DATED)) for size in ("A4", "Letter")}

    def pages(self, key):
        return tc._pdf_pages(self.files[key])

    def test_every_page_is_filled_down_to_the_footer(self):
        for key, path in self.files.items():
            for n, page in enumerate(tc._pdf_pages(path), 1):
                with self.subTest(file=key, page=n):
                    self.assertIsNotNone(page["low"])
                    self.assertLess(page["low"], pd.BOTTOM + 30)       # no big empty area at the bottom

    def test_footer_on_every_page_with_page_x_of_n(self):
        for (name, size), path in self.files.items():
            pages = tc._pdf_pages(path)
            for n, page in enumerate(pages, 1):
                texts = [t for _, s, _, y, t in page["items"] if y < 45]
                with self.subTest(file=name, size=size, page=n):
                    self.assertIn(f"Page {n} of {len(pages)}", texts)
                    self.assertTrue(any(f"· {size} · for personal use" in t for t in texts), texts)

    def test_no_text_runs_off_the_page_or_into_the_footer(self):
        for (name, size), path in self.files.items():
            w = pd.PAGE_SIZES[size][0]
            for n, page in enumerate(tc._pdf_pages(path), 1):
                for font, s, x, y, t in page["items"]:
                    with self.subTest(file=name, size=size, page=n, text=t):
                        self.assertLessEqual(x + pd.text_width(t, s, font == "F2"), w - pd.MARGIN + 1.5)
                        self.assertTrue(y >= pd.BOTTOM or y < 45)

    def test_long_text_wraps_and_every_word_is_printed(self):
        texts = " ".join(t for p in self.pages(("planner", "Letter")) for _, _, _, _, t in p["items"])
        for word in LONG.split() + "Afternoon follow-up calls with clients".split() + ["wrap", "second"]:
            self.assertIn(word, texts)
        self.assertFalse(tc.file_quality(tc.facts(os.path.dirname(self.files[("planner", "Letter")]))))

    def test_cover_lists_the_real_page_numbers(self):
        pages = self.pages(("dated", "A4"))
        cover = [t for _, _, _, _, t in pages[0]["items"]]
        self.assertIn("January to December 2027 (one page per month)", cover)
        self.assertIn("Pages 4-15", cover)
        january = [t for _, s, _, _, t in pages[3]["items"] if s >= 18]
        self.assertEqual(january, ["January", "2027"])
        self.assertIn("How to use", [t for _, s, _, _, t in pages[1]["items"] if s >= 18])

    def test_month_grid_has_six_rows_and_faded_other_days(self):
        jan = self.pages(("dated", "A4"))[3]["items"]
        bold_days = [t for f, s, _, _, t in jan if f == "F2" and s == 9 and t.isdigit()]
        faded = [t for f, s, _, _, t in jan if f == "F1" and s == 8 and t.isdigit()]
        self.assertEqual(bold_days, [str(d) for d in range(1, 32)])
        self.assertEqual(len(bold_days) + len(faded), 42)                # 6 weeks x 7 days
        self.assertEqual(faded[:5], ["27", "28", "29", "30", "31"])      # December 2026, faded
        for box in ("THIS MONTH’S FOCUS", "KEY DATES", "NOTES"):
            self.assertIn(box, [t for _, _, _, _, t in jan])

    def test_today_page_has_all_the_parts(self):
        today = [t for _, _, _, _, t in self.pages(("planner", "A4"))[2]["items"]]
        for part in ("TIME ANCHORS", "TOP 3 (ONLY 3)", "NOW · NEXT · LATER", "WAITING FOR / ERRANDS",
                     "WINS TODAY (EVEN SMALL)", "GUESS VS REAL TIME", "FOCUS BLOCKS (15 MIN EACH)",
                     "TOMORROW’S FIRST STEP", "BRAIN DUMP", "Date", "Energy", "My guess", "Real time"):
            self.assertIn(part, today)
        with open(self.files[("planner", "A4")], "rb") as f:
            page3 = re.findall(rb"stream\n(.*?)\nendstream", f.read(), re.S)[2]
        self.assertGreaterEqual(page3.count(b" c h B"), 16)                 # the 16 focus boxes

    def test_styles(self):
        self.assertEqual(pd.style_for({"niche": "ADHD Planners Agent"}), "B")
        for niche in ("2027 Planners Agent", "Budget Sheets Agent", "Resume Bundle Agent"):
            self.assertEqual(pd.style_for({"niche": niche}), "A")
        self.assertEqual(pd.pdf_style(self.files[("planner", "A4")]), "B")
        self.assertEqual(pd.pdf_style(self.files[("dated", "A4")]), "A")
        with open(self.files[("planner", "A4")], "rb") as f:
            data = f.read()
        self.assertIn(b"1.000 0.980 0.953 rg 0.0 0.0 595.3 841.9 re f", data)     # Style B page #fffaf3
        with open(self.files[("dated", "A4")], "rb") as f:
            self.assertIn(b"0.184 0.490 0.471 rg", f.read())                       # Style A teal #2f7d78
        with open(os.path.join(ROOT, "pdf_design.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertIn("/BaseFont /Helvetica", source)                                # built-in font kept

    def test_rebuild_specs_are_complete(self):
        folder = os.path.join(ROOT, "specs", "rebuild")
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
        self.assertEqual(names, ["1-v2.json", "2-v2.json", "3-v2.json"])
        for n in names:
            with open(os.path.join(folder, n), encoding="utf-8") as f:
                spec = json.load(f)
            self.assertEqual(pd.style_for(spec), "B")
            self.assertEqual(spec["listing"]["platform"], "Gumroad")
            text = json.dumps(spec, ensure_ascii=False)
            self.assertNotIn("Follow-u\"", text)
            self.assertNotIn("?", text.replace("\\u", ""))
        with open(os.path.join(folder, "2-v2.json"), encoding="utf-8") as f:
            self.assertTrue(json.load(f)["daily_page"])


class RebuildTests(unittest.TestCase):
    def test_new_version_keeps_the_old_folder_and_is_not_for_sale_until_the_owner_decides(self):
        import shutil
        import product_builder as pb
        with tempfile.TemporaryDirectory() as d:
            db, out, specs = os.path.join(d, "a.db"), os.path.join(d, "products"), os.path.join(d, "specs")
            os.makedirs(os.path.join(out, "2-time-blindness-planner"))
            os.makedirs(specs)
            shutil.copy(os.path.join(ROOT, "specs", "rebuild", "2-v2.json"), specs)
            pb._record(db, 2, "needs_fix", "2-time-blindness-planner", "old")
            sent = []
            done = pb.rebuild_versions(db, out, specs, notify_fn=lambda m: sent.append(m) or True)
            self.assertEqual(done, [(2, "2-v2-time-blindness-planner", "ready")])
            self.assertEqual(pb.rebuild_versions(db, out, specs, notify_fn=sent.append), [])     # only once
            self.assertTrue(os.path.isdir(os.path.join(out, "2-time-blindness-planner")))       # old kept
            self.assertEqual(sent, ["Product #2 Time-Blindness Printable Planner: READY"])
            pb.write_index(db, out)
            with open(os.path.join(out, "INDEX.md"), encoding="utf-8") as f:
                index = f.read()
        self.assertIn("2-v2-time-blindness-planner", index)
        self.assertIn("the owner decides if it goes on sale", index)
        self.assertIn("## Older versions (kept, not for sale)", index)
        self.assertIn("[2-time-blindness-planner]", index)


if __name__ == "__main__":
    unittest.main()
