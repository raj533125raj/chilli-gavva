"""Fixes after the v2 review: growing boxes get writing lines (never extra checkboxes), a compact cover,
natural preview bullets, US/UK school-year planners (every day checked), and the ADHD Printable Planner Kit."""
import calendar
import datetime
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mockups  # noqa: E402
import pdf_design as pd  # noqa: E402
import product_builder as pb  # noqa: E402
import truth_check as tc  # noqa: E402

REBUILD = [os.path.join(ROOT, "specs", "rebuild", n) for n in ("1-v2.json", "2-v2.json", "3-v2.json")]
BUNDLE = os.path.join(ROOT, "specs", "bundles", "adhd-printable-planner-kit.json")
SAMPLES = {"us": os.path.join(ROOT, "specs", "samples", "2027-28-teacher-planner-us-test.json"),
           "uk": os.path.join(ROOT, "specs", "samples", "2027-28-teacher-planner-uk-test.json")}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def checkbox_count(pdf_path):
    with open(pdf_path, "rb") as f:
        return len(re.findall(rb" 9 9 re S", f.read()))


def expected_checkboxes(spec):
    pages = pd._content_pages(spec)
    return sum(len(s.get("items") or []) if s["type"] == "checklist" else
               int(s.get("count") or 0) if s["type"] == "checkboxes" else 0
               for p in pages for s in p["sections"])


class FillRuleTests(unittest.TestCase):
    def test_every_page_of_every_product_has_exactly_its_own_checkboxes(self):
        specs = [load(p) for p in REBUILD] + [pb.bundle_spec(BUNDLE)]
        for spec in specs:
            for size in ("A4", "Letter"):
                with self.subTest(product=spec["name"], size=size), tempfile.TemporaryDirectory() as d:
                    path = os.path.join(d, f"p-{size}.pdf")
                    pd.build_planner_pdf(path, spec, size)
                    self.assertEqual(checkbox_count(path), expected_checkboxes(spec))

    def test_top_3_priorities_has_3_checkboxes_then_notes_lines(self):
        spec = load(REBUILD[2])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p-A4.pdf")
            pd.build_planner_pdf(path, spec, "A4")
            page = next(p for p in tc._pdf_pages(path)
                        if any(f == "F2" and sz >= 18 and t == "Daily Focus Sheet" for f, sz, _, _, t in p["items"]))
            with open(path, "rb") as f:
                stream = re.findall(rb"stream\n(.*?)\nendstream", f.read(), re.S)[tc._pdf_pages(path).index(page)]
        texts = [t for *_, t in page["items"]]
        self.assertIn("TOP 3 PRIORITIES", texts)
        self.assertIn("NOTES", texts)
        self.assertEqual(stream.count(b" 9 9 re S"), 3)         # only the 3 priorities have boxes
        self.assertLess(page["low"], pd.BOTTOM + 30)              # and lines still reach the footer


class CoverTests(unittest.TestCase):
    def test_cover_title_box_is_compact_and_whats_inside_follows(self):
        for style in ("A", "B"):
            spec = dict(load(REBUILD[0]), style=style)
            with self.subTest(style=style), tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "p-A4.pdf")
                pd.build_planner_pdf(path, spec, "A4")
                cover = tc._pdf_pages(path)[0]["items"]
            title_y = min(y for f, s, _, y, t in cover if f == "F2" and s >= 18)
            inside_y = next(y for *_, y, t in cover if t == "WHAT’S INSIDE")
            self.assertGreater(title_y, 841.89 * 0.70)             # the title box stays in the top part
            self.assertLess(inside_y, title_y)
            self.assertGreater(inside_y, 841.89 * 0.55)


class BulletTests(unittest.TestCase):
    def test_short_natural_bullets(self):
        heads = ["Time anchors", "Top 3 (only 3)", "Now · next · later", "Waiting for / errands",
                 "Wins today (even small)", "Guess vs real time", "Focus blocks (15 min each)",
                 "Tomorrow’s first step", "Brain dump"]
        bullets = mockups.short_bullets(heads)
        self.assertEqual(bullets, ["Time anchors", "Top 3 (only 3)", "Now, next, later"])
        self.assertTrue(all(len(b) <= 26 and "Plus" not in b and "·" not in b for b in bullets))
        self.assertEqual(mockups.short_bullets(["A very long heading that goes on and on and on"]),
                         ["A very long heading that goes on and on and on"])


class SchoolYearTests(unittest.TestCase):
    def check_every_day(self, key, first_month, first_weekday, heads_order):
        spec = load(SAMPLES[key])
        months, fw = pd.planner_months(spec)
        self.assertEqual(months[0], (2027, first_month))
        self.assertEqual(len(months), 12)
        self.assertEqual(fw, first_weekday)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p-A4.pdf")
            pd.build_dated_planner_pdf(path, spec, "A4")
            pages = tc._pdf_pages(path)
        seen = []
        for page in pages:
            title = " ".join(t for f, s, _, _, t in page["items"] if f == "F2" and s >= 18)
            m = re.fullmatch(r"(\w+) (\d{4})", title)
            if not m or m.group(1) not in calendar.month_name[1:]:
                continue
            year, month = int(m.group(2)), list(calendar.month_name).index(m.group(1))
            seen.append((year, month))
            heads = sorted((x, t) for f, s, x, _, t in page["items"] if s == 9 and t in pd.weekdays(0))
            self.assertEqual([t for _, t in heads], heads_order)
            xs = [x for x, _ in heads]
            days = [(int(t), x) for f, s, x, _, t in page["items"] if f == "F2" and s == 9 and t.isdigit()]
            self.assertEqual([d for d, _ in days], list(range(1, calendar.monthrange(year, month)[1] + 1)))
            for day, x in days:                                     # EVERY day under its real weekday
                col = min(range(7), key=lambda c: abs(xs[c] - x))
                self.assertEqual(heads_order[col], datetime.date(year, month, day).strftime("%a"),
                                 (key, year, month, day))
        self.assertEqual(seen, months)

    def test_us_school_year_august_to_july_sunday_start(self):
        self.check_every_day("us", 8, 6, ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
        self.assertEqual(datetime.date(2027, 8, 1).strftime("%A"), "Sunday")

    def test_uk_school_year_september_to_august_monday_start(self):
        self.check_every_day("uk", 9, 0, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        self.assertEqual(datetime.date(2027, 9, 1).strftime("%A"), "Wednesday")

    def test_terms_and_semesters(self):
        us = json.dumps(load(SAMPLES["us"]))
        uk = json.dumps(load(SAMPLES["uk"]))
        for word in ("Fall semester", "Spring semester"):
            self.assertIn(word, us)
        for word in ("Autumn term", "Spring term", "Summer term"):
            self.assertIn(word, uk)

    def test_school_year_products_pass_the_truth_check(self):
        for key in ("us", "uk"):
            spec = load(SAMPLES[key])
            with self.subTest(key=key), tempfile.TemporaryDirectory() as d:
                folder = pb.build_product(spec, 0, d)
                r = tc.check(os.path.join(d, folder))
                facts = tc.image_facts(os.path.join(d, folder))
            self.assertEqual(r["verdict"], "ready", r["problems"])
            self.assertEqual(facts["dated"], "2027-28")
            self.assertEqual(facts["week_start"], "Sunday" if key == "us" else "Monday")
            self.assertIn("12 monthly calendar pages", r["proven"])

    def test_ai_calendar_choice_is_checked(self):
        reply = json.dumps({"name": "X", "listing": {"title": "X", "description": "Y"}, "calendar": "uk-academic"})
        self.assertEqual(pb.parse_spec(reply, "2027 Planners Agent")[0]["calendar"], "uk-academic")
        reply = reply.replace("uk-academic", "mars-year")
        self.assertEqual(pb.parse_spec(reply, "2027 Planners Agent")[0]["calendar"], "jan-dec")


class BundleTests(unittest.TestCase):
    def test_bundle_is_the_real_pages_of_the_three_products(self):
        spec = pb.bundle_spec(BUNDLE)
        parts = [load(p) for p in REBUILD]
        self.assertEqual(spec["pages"], [p for s in parts for p in s["pages"]])
        self.assertTrue(spec["daily_page"])
        self.assertEqual(pd.style_for(spec), "B")
        self.assertEqual(spec["listing"]["price_usd"], 3.0)               # the owner's price (was 5, then 7)
        self.assertEqual(len(spec["listing"]["tags"]), 10)

    def test_bundle_build_ready_once_and_listed_not_for_sale(self):
        with tempfile.TemporaryDirectory() as d:
            db, out, specs = os.path.join(d, "a.db"), os.path.join(d, "products"), os.path.join(d, "b")
            os.makedirs(out)
            os.makedirs(specs)
            shutil.copy(BUNDLE, specs)
            sent = []
            done = pb.build_bundles(db, out, specs, notify_fn=lambda m: sent.append(m) or True)
            self.assertEqual(done, [("bundle-adhd-printable-planner-kit", "ready")])
            self.assertEqual(pb.build_bundles(db, out, specs, notify_fn=sent.append), [])
            self.assertEqual(sent, ["Bundle: ADHD Printable Planner Kit: READY"])
            folder = os.path.join(out, "bundle-adhd-printable-planner-kit")
            titles = tc.facts(folder)["titles"]
            pb.write_index(db, out)
            with open(os.path.join(out, "INDEX.md"), encoding="utf-8") as f:
                index = f.read()
            with open(os.path.join(folder, "listing.md"), encoding="utf-8") as f:
                listing = f.read()
        self.assertEqual(titles[:3], ["ADHD Printable Planner Kit", "How to use", "Today"])
        self.assertEqual(len(titles), 14)
        self.assertIn("the owner decides if it goes on sale", index)
        self.assertIn("**Price:** 3.00 USD", listing)
        self.assertIn("## Tags (10)", listing)
        self.assertIn("WHAT'S INSIDE (14 pages)", listing)
        self.assertIn("products #1 v2, #2 v2 and #3 v2", listing)


class RevisionTests(unittest.TestCase):
    def test_a_new_revision_rebuilds_the_same_folder_once(self):
        with tempfile.TemporaryDirectory() as d:
            db, out, specs = os.path.join(d, "a.db"), os.path.join(d, "products"), os.path.join(d, "s")
            os.makedirs(os.path.join(out, "3-v2-printable-focus-sheets"))     # built before revisions
            os.makedirs(specs)
            shutil.copy(REBUILD[2], specs)
            pb._record(db, 3, "built", "3-v2-printable-focus-sheets", "ready")
            done = pb.rebuild_versions(db, out, specs, notify_fn=lambda m: True)
            self.assertEqual(done, [(3, "3-v2-printable-focus-sheets", "ready")])
            self.assertEqual(pb.rebuild_versions(db, out, specs, notify_fn=lambda m: True), [])
            self.assertTrue(os.path.isfile(os.path.join(out, "3-v2-printable-focus-sheets", "cover.png"))
                            or not mockups.available())


if __name__ == "__main__":
    unittest.main()
