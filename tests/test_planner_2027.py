"""2027 dated planners: every date comes from code, and the PDF really shows the right calendar."""
import calendar
import datetime
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import product_builder as pb  # noqa: E402
import recipe_runner  # noqa: E402
import scheduler  # noqa: E402
import truth_check  # noqa: E402

REPLY = json.dumps({
    "name": "2027 Teacher Planner", "short_name": "2027-teacher-planner",
    "listing": {"title": "2027 Teacher Planner for Classroom Teachers - Printable Monthly Calendar",
                "description": "A dated 2027 planner for classroom teachers. It includes a 2027 year-at-a-glance page "
                               "and 12 monthly calendar pages, each with space for lesson goals, key dates and "
                               "notes. Two extra pages: a Class Roster page and a Term Overview page. Print it on "
                               "A4 or US Letter paper.",
                "tags": ["teacher planner", "2027 planner", "printable", "monthly calendar", "classroom"],
                "price_usd": 9, "buyer": "classroom teachers"},
    "instructions": ["Print it."], "cover_image_idea": "x",
    "monthly_sections": ["Lesson goals", "Key dates – parents’ evenings", "Notes"],
    "pages": [{"title": "Class Roster", "sections": [{"heading": "Students", "type": "lines", "count": 30}]},
              {"title": "Term Overview", "sections": [
                  {"heading": "Term dates", "type": "schedule",
                   "items": ["Autumn term (Sep–Dec)", "Spring term (Jan–Apr)"]}]}]})


def spec():
    s, reason = pb.parse_spec(REPLY, "2027 Planners Agent")
    assert reason is None, reason
    return s


def month_pages(pdf_path):
    """{month: [(day, column)]} read back from the PDF: the column comes from where each number is drawn."""
    pages = truth_check._pdf_pages(pdf_path)
    out = {}
    for page in pages:
        titles = " ".join(t for font, size, _, _, t in page["items"] if font == "F2" and size >= 18)
        m = re.fullmatch(r"(\w+) 2027", titles)
        if not m or m.group(1) not in calendar.month_name[1:]:
            continue
        heads = sorted(x for font, size, x, _, t in page["items"] if t in pb.WEEKDAYS_SUN and size == 9)
        # this month's days are bold; the faded days of the months before and after are not
        days = [(int(t), x) for font, size, x, _, t in page["items"] if font == "F2" and size == 9 and t.isdigit()]
        cols = [min(range(7), key=lambda c: abs(heads[c] - x)) for _, x in days]
        out[list(calendar.month_name).index(m.group(1))] = [(d, c) for (d, _), c in zip(days, cols)]
    return out


class CalendarTests(unittest.TestCase):
    def test_first_of_january_2027_is_a_friday(self):
        self.assertEqual(datetime.date(2027, 1, 1).strftime("%A"), "Friday")
        self.assertEqual(pb.month_weeks(2027, 1)[0], [0, 0, 0, 0, 0, 1, 2])   # Sun..Sat

    def test_every_month_of_2027_grid_is_correct(self):
        for month in range(1, 13):
            weeks = pb.month_weeks(2027, month)
            days = [d for w in weeks for d in w if d]
            last = calendar.monthrange(2027, month)[1]
            self.assertEqual(days, list(range(1, last + 1)), month)
            for w in weeks:
                for col, d in enumerate(w):
                    if d:   # column 0 = Sunday
                        self.assertEqual((datetime.date(2027, month, d).weekday() + 1) % 7, col, (month, d))

    def test_pdf_shows_every_day_under_the_right_weekday(self):
        with tempfile.TemporaryDirectory() as d:
            for size in ("A4", "Letter"):
                path = os.path.join(d, f"p-{size}.pdf")
                pb.build_dated_planner_pdf(path, spec(), size)
                months = month_pages(path)
                self.assertEqual(sorted(months), list(range(1, 13)), size)
                for month, cells in months.items():
                    last = calendar.monthrange(2027, month)[1]
                    self.assertEqual([day for day, _ in cells], list(range(1, last + 1)), (size, month))
                    for day, col in cells:
                        expected = (datetime.date(2027, month, day).weekday() + 1) % 7
                        self.assertEqual(col, expected, (size, month, day))

    def test_page_list_and_nothing_cut_or_broken(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p-A4.pdf")
            pages = pb.build_dated_planner_pdf(path, spec(), "A4")
            f_titles = truth_check.facts(d)["titles"]
        self.assertEqual(pages, 17)    # cover, How to use, year overview, 12 months, 2 extra pages
        self.assertEqual(f_titles, ["2027 Teacher Planner", "How to use", "2027 at a glance"]
                         + [f"{calendar.month_name[m]} 2027" for m in range(1, 13)] + ["Class Roster", "Term Overview"])

    def test_dates_are_never_asked_from_the_ai(self):
        prompt = pb.spec_prompt("2027 Planners Agent", "draft")
        self.assertIn("FIXED and made by code", prompt)
        self.assertIn("Never promise weekly or daily dated pages", prompt)
        self.assertNotIn('"days"', prompt)

    def test_whole_product_passes_the_truth_check(self):
        with tempfile.TemporaryDirectory() as d:
            folder = pb.build_product(spec(), 101, d)
            result = truth_check.check(os.path.join(d, folder))
        self.assertEqual(result["verdict"], "ready", result["problems"])
        self.assertIn("12 monthly calendar pages", result["proven"])
        self.assertGreaterEqual(len(result["proven"]), truth_check.EXTRA_PROOF_PROMISES)


class RotationTests(unittest.TestCase):
    def test_thursday_is_2027_planners_and_notion_is_paused_not_deleted(self):
        slots = scheduler._load_schedule(os.path.join(ROOT, "schedule.yaml"))
        thursday = datetime.date(2026, 10, 15)
        self.assertEqual(thursday.strftime("%A"), "Thursday")
        self.assertEqual(scheduler.resolve_recipe(slots[0], thursday), "niche_planners_2027")
        rotation = [n.strip() for n in slots[0]["rotate"].split(",")]
        self.assertNotIn("niche_notion_templates", rotation)
        self.assertTrue(os.path.exists(os.path.join(ROOT, "recipes", "niche_notion_templates.yaml")))
        self.assertIn("Notion Templates Agent", pb.NICHES)          # code kept for later

    def test_recipe_has_three_different_clear_buyers(self):
        recipe = recipe_runner._load_yaml(os.path.join(ROOT, "recipes", "niche_planners_2027.yaml"))
        topics = [t["payload"]["topic"] for t in recipe["tasks"]]
        self.assertEqual(len(topics), 3)
        for buyer in ("teachers", "small business owners", "university students"):
            self.assertEqual(sum(buyer in t for t in topics), 1, buyer)
        self.assertTrue(all("sold on Gumroad" in t for t in topics))


if __name__ == "__main__":
    unittest.main()
