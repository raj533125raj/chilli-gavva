"""Tests for the Truth Checker (truth_check.py) and how the Product Builder uses it."""
import csv
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import guardrails  # noqa: E402
import product_builder as pb  # noqa: E402
import truth_check as tc  # noqa: E402

OVERPROMISE = """# Notion Semester Planner
## Description
A clean Dashboard pulls every class onto one calendar. Each course is linked to assignments.
Ready-made views (calendar, list, Kanban). The Grade Tracker uses formulas for an instant GPA calculation.
A Resources database stores PDFs. The Courses database lists every class. Simply duplicate it.
"""


def notion_folder(root, listing=OVERPROMISE, readme="Use the New Assignment button."):
    folder = os.path.join(root, "4-notion")
    os.makedirs(folder)
    with open(os.path.join(folder, "listing.md"), "w") as f:
        f.write(listing)
    with open(os.path.join(folder, "README.md"), "w") as f:
        f.write("# Notion Semester Planner\n\n## How to use\n\n1. " + readme + "\n")
    buf = io.StringIO()
    csv.writer(buf).writerows([["Course", "Credits"], ["CS101", 3]])
    with zipfile.ZipFile(os.path.join(folder, "x-notion-import.zip"), "w") as z:
        z.writestr("courses.csv", buf.getvalue())
        z.writestr("start-here.md", "# Start Here\nClick Duplicate.")
    return folder


class NotionTruthTests(unittest.TestCase):
    def test_overpromising_notion_product_needs_fix(self):
        with tempfile.TemporaryDirectory() as d:
            r = tc.check(notion_folder(d), with_images=False)
        self.assertEqual(r["verdict"], "needs fix")
        text = " ".join(r["problems"])
        for promise in ("dashboard", "linked databases", "formulas", "calendar / board / timeline views", "buttons",
                        "duplicate link", "Resources database"):
            self.assertIn(promise, text)
        self.assertIn("only 3 or fewer sample rows", text)
        self.assertIn("Courses", " ".join(r["proven"]))   # what IS in the files still counts

    def test_telegram_text_ready_or_needs_fix_with_top_3(self):
        with tempfile.TemporaryDirectory() as d:
            r = tc.check(notion_folder(d), with_images=False)
        lines = tc.telegram_text(4, "Notion Semester Planner", r).splitlines()
        self.assertEqual(lines[0], "Product #4 Notion Semester Planner: NEEDS FIX")
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(l.startswith("- ") and len(l) <= 92 for l in lines[1:]))
        ok = tc.telegram_text(7, "Planner", {"verdict": "ready", "problems": []})
        self.assertEqual(ok, "Product #7 Planner: READY")


def planner_folder(root, listing, pages_spec, sizes=("A4", "Letter")):
    folder = os.path.join(root, "9-planner")
    os.makedirs(folder)
    spec = {"name": "Focus Sheets", "listing": {"title": "Focus Sheets", "buyer": "adults"}, "pages": pages_spec}
    for size in sizes:
        pb.build_planner_pdf(os.path.join(folder, f"focus-{size}.pdf"), spec, size)
    with open(os.path.join(folder, "listing.md"), "w") as f:
        f.write(listing)
    with open(os.path.join(folder, "README.md"), "w") as f:
        f.write("# Focus Sheets\n")
    return folder


PAGES = [{"title": "Brain Dump", "subtitle": "", "sections": [{"heading": "Thoughts", "type": "lines", "items": [],
                                                              "count": 20}]},
         {"title": "Daily Focus", "subtitle": "", "sections": [{"heading": "Top 3", "type": "checklist",
                                                               "items": ["One", "Two", "Three"], "count": 3},
                                                              {"heading": "Notes", "type": "lines", "items": [],
                                                               "count": 12}]}]


class PlannerTruthTests(unittest.TestCase):
    def test_honest_planner_is_ready_with_proof(self):
        listing = "Two printable pages: a Brain Dump page and a Daily Focus page. Print on A4 or US Letter."
        with tempfile.TemporaryDirectory() as d:
            r = tc.check(planner_folder(d, listing, PAGES), with_images=False)
        self.assertEqual(r["verdict"], "ready", r["problems"])
        self.assertIn("count: 2 pages", r["proven"])
        self.assertIn("size: Letter", r["proven"])

    def test_95_plus_without_proof_is_a_red_flag(self):
        """The Reality Checker rule: a perfect score with nothing proven is NOT ready."""
        with tempfile.TemporaryDirectory() as d:
            r = tc.check(planner_folder(d, "A nice planner.", PAGES, sizes=("A4",)), with_images=False)
        self.assertEqual(r["verdict"], "needs fix")
        self.assertIn("red flag", " ".join(r["problems"]))

    def test_missing_size_page_count_and_fillable(self):
        listing = "Six printable pages, fillable PDF, A4 and US Letter. Includes weekly spreads."
        with tempfile.TemporaryDirectory() as d:
            r = tc.check(planner_folder(d, listing, PAGES, sizes=("A4",)), with_images=False)
        text = " ".join(r["problems"])
        self.assertIn("promises 6 pages, the files have only 2", text)
        self.assertIn("no Letter file", text)
        self.assertIn("fillable", text)
        self.assertIn("weekly or daily dated pages", text)

    def test_old_pdf_problems_are_found(self):
        """Broken characters and cut-off text like the first planners had."""
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "2-old")
            os.makedirs(folder)
            pdf = pb.Pdf("A4")
            pdf.new_page()
            pdf.text(50, 770, "Morning routine", 20, True)
            pdf.text(50, 700, "Morning (6?10 am)", 10, True)
            pdf.text(50, 676, "Afternoon Follow-u", 10, True)
            pdf.text(50, 652, "Dont forget the plan", 11)
            pdf.text(50, 30, "footer", 8)
            pdf.new_page()
            pdf.text(50, 770, "Morning routine (cont.)", 20, True)
            pdf.text(50, 30, "footer", 8)
            pdf.save(os.path.join(folder, "old-A4.pdf"))
            problems = " ".join(tc.file_quality(tc.facts(folder)))
        self.assertIn("broken characters", problems)
        self.assertIn("text cut off on the page", problems)
        self.assertIn("missing apostrophes", problems)
        self.assertIn("page 2 is almost empty", problems)

    def test_looks_cut(self):
        for t in ("Afternoon Follow-u", "Midday (10 am-2 pm", "Plan the da"):
            self.assertTrue(tc.looks_cut(t), t)
        for t in ("Midday Maintenance", "Today's main focus", "Morning (6-10 am)", "Wake up"):
            self.assertFalse(tc.looks_cut(t), t)

    def test_image_problems_are_found(self):
        problems = " ".join(tc.file_quality({"kind": "unknown"}, ["**Bold** title", "Student Semester Planner..."]))
        self.assertIn("raw ** symbols", problems)
        self.assertIn("text cut off in the listing images", problems)


class BuilderTruthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "b.db")
        self.out = os.path.join(self.tmp.name, "products")
        os.makedirs(self.out)
        self.sent = []

    def notify(self, text):
        self.sent.append(text)
        return True

    def test_status_report_and_one_message_per_verdict(self):
        folder = notion_folder(self.out)
        name = os.path.basename(folder)
        pb._record(self.db, 40, "built", name, "notion")
        r = pb.truth_check_folder(40, name, self.db, self.out, self.notify)
        self.assertEqual(r["verdict"], "needs fix")
        b = [x for x in pb.builds(self.db) if x["approval_id"] == 40][0]
        self.assertEqual(b["status"], "needs_fix")
        with open(os.path.join(folder, "TRUTH_CHECK.md")) as f:
            self.assertIn("NEEDS FIX", f.read())
        pb.truth_check_folder(40, name, self.db, self.out, self.notify)    # same verdict: no second message
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].startswith("Product #40 "))

    def test_held_product_keeps_not_for_sale(self):
        folder = notion_folder(self.out)
        pb._record(self.db, 4, "built", os.path.basename(folder), "notion")
        pb.apply_owner_holds(self.db, self.out)
        pb.truth_check_folder(4, os.path.basename(folder), self.db, self.out, self.notify)
        self.assertEqual([x for x in pb.builds(self.db) if x["approval_id"] == 4][0]["status"], "not_for_sale")

    def test_new_build_needs_fix_is_not_called_built(self):
        pb._record(self.db, 12, "built", "x", "planner")
        folder = notion_folder(self.out)
        r = pb.truth_check_folder(12, os.path.basename(folder), self.db, self.out, self.notify)
        self.assertEqual(r["verdict"], "needs fix")
        self.assertEqual([x for x in pb.builds(self.db) if x["approval_id"] == 12][0]["status"], "needs_fix")
        pb.write_index(self.db, self.out)
        with open(os.path.join(self.out, "INDEX.md")) as f:
            self.assertIn("needs fix - see TRUTH_CHECK.md", f.read())

    def test_owner_holds_mark_4_and_6_not_for_sale_without_deleting(self):
        for aid in (4, 6):
            folder = os.path.join(self.out, f"{aid}-notion")
            os.makedirs(folder)
            with open(os.path.join(folder, "README.md"), "w") as f:
                f.write("# Title\n")
            open(os.path.join(folder, "x-notion-import.zip"), "wb").close()
            pb._record(self.db, aid, "built", f"{aid}-notion", "notion")
        self.assertEqual(pb.apply_owner_holds(self.db, self.out), [4, 6])
        self.assertEqual(pb.apply_owner_holds(self.db, self.out), [])       # once
        pb.write_index(self.db, self.out)
        with open(os.path.join(self.out, "INDEX.md")) as f:
            index = f.read()
        self.assertEqual(index.count("NOT FOR SALE - files do not match listing"), 2)
        with open(os.path.join(self.out, "4-notion", "README.md")) as f:
            body = f.read()
        self.assertTrue(body.startswith("> **NOT FOR SALE - files do not match listing**"))
        self.assertEqual(body.count("NOT FOR SALE"), 1)
        self.assertTrue(os.path.exists(os.path.join(self.out, "4-notion", "x-notion-import.zip")))

    def test_draft_5_is_never_touched(self):
        a = guardrails.request_approval("public_draft", "Draft 5", "x", db_path=self.db)
        pb.apply_owner_holds(self.db, self.out)
        pb.check_unchecked(self.db, self.out, self.notify)
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "pending")

    def test_old_products_get_checked_once(self):
        folder = notion_folder(self.out)
        pb._record(self.db, 9, "built", os.path.basename(folder), "notion")
        self.assertEqual(pb.check_unchecked(self.db, self.out, self.notify), [(9, "needs fix")])
        self.assertEqual(pb.check_unchecked(self.db, self.out, self.notify), [])


if __name__ == "__main__":
    unittest.main()
