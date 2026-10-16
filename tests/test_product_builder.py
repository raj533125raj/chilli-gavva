"""Tests for product_builder.py (offline: the LLM is a stub, files go to a temp folder)."""
import csv
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guardrails  # noqa: E402
import product_builder as pb  # noqa: E402

LISTING = {"title": "Freelancer Irregular-Income Budget", "description": "A calm monthly budget for uneven income.",
           "tags": ["freelancer budget", "google sheets"], "price_usd": 7, "buyer": "freelancers",
           "platform": "Payhip"}
SPECS = {
    "Budget Sheets Agent": {
        "name": "Freelancer Budget", "short_name": "freelancer-budget", "listing": LISTING,
        "instructions": ["Make a copy.", "Log every payment."], "cover_image_idea": "Laptop with a green sheet",
        "sheets": [
            {"name": "Income Log", "columns": [{"name": "Date", "type": "date"}, {"name": "Client", "type": "text"},
                                               {"name": "Amount", "type": "money"},
                                               {"name": "Status", "type": "text", "options": ["Paid", "Unpaid"]},
                                               {"name": "Due Date", "type": "date"}],
             "rows": [["2026-01-05", "Client A", 450, "Unpaid", "2026-01-20"],
                      ["2026-01-09", "Client B", "1,200", "Paid", "2026-01-25"]],
             "overdue": {"due": "Due Date", "status": "Status", "done": "Paid"},
             "balance": {"in": "Amount", "out": ""}},
            {"name": "Bills", "columns": [{"name": "Bill", "type": "text"}, {"name": "Cost", "type": "money"}],
             "rows": [["Rent", 900]]}]},
    "ADHD Planners Agent": {
        "name": "Energy Planner", "short_name": "energy-planner",
        "listing": dict(LISTING, title="Energy-Based Daily Planner", price_usd="8"),
        "pages": [{"title": "Daily Plan", "subtitle": "Plan by energy",
                   "sections": [{"heading": "Energy check", "type": "checklist", "items": ["High", "Low"]},
                                {"heading": "Top 3", "type": "lines", "count": 3},
                                {"heading": "Blocks", "type": "schedule", "slots": ["Morning", "Evening"]}]},
                  {"title": "Brain Dump", "sections": [{"heading": "Notes", "type": "notes", "count": 12},
                                                       {"heading": "More", "type": "lines", "count": 12},
                                                       {"heading": "Even more", "type": "lines", "count": 12}]}]},
    "Notion Templates Agent": {
        "name": "Client CRM", "short_name": "client-crm", "listing": dict(LISTING, title="Notion Client CRM"),
        "pages": [{"title": "Start Here", "markdown": "How to use the CRM."}],
        "databases": [{"name": "Clients", "columns": ["Name", "Status", "Next Follow-up"],
                       "rows": [["Client A", "Lead", "2026-01-10"]]}]},
    "Resume Bundle Agent": {
        "name": "ATS Resume Bundle", "short_name": "ats-resume", "listing": dict(LISTING, title="ATS Resume Bundle"),
        "resume": {"sections": [{"heading": "Professional Summary", "bullets": ["[Two lines about you]"]},
                                {"heading": "Experience", "bullets": ["[Job Title], [Company]"]}]},
        "cover_letter": {"paragraphs": ["Dear [Hiring Manager Name],", "[Why this role]", "[Your Name]"]}},
}
TITLES = {n: f"Draft: [{n}] sample product (v1, llm)" for n in SPECS}


class StubLLM:
    def __init__(self, reply):
        self.reply, self.calls, self.sizes = reply, 0, []

    def __call__(self, prompt, size=None):
        self.calls += 1
        self.sizes.append(size)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply(prompt) if callable(self.reply) else self.reply


def reply_for_prompt(prompt):
    niche = next(n for n in SPECS if f"Product type: {pb.NICHES[n]['kind']}." in prompt)
    return "Here is the spec:\n```json\n" + json.dumps(SPECS[niche]) + "\n```"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pb.db")
        self.out = os.path.join(self.tmp, "products")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, title, status="approved", payload="An approved draft about the product."):
        conn = guardrails.connect(self.db)
        cur = conn.execute("INSERT INTO approvals (ts, kind, title, payload, status) VALUES (?, ?, ?, ?, ?)",
                           ("2026-10-14T10:00:00", "public_draft", title, payload, status))
        conn.commit()
        conn.close()
        return cur.lastrowid

    def run_builder(self, llm, max_builds=2):
        return pb.build_pending(db_path=self.db, llm=llm, max_builds=max_builds, out_dir=self.out)

    def build_one(self, niche):
        aid = self.add(TITLES[niche])
        res = self.run_builder(StubLLM(reply_for_prompt))
        # the files are built; whether they are "ready" is the Truth Checker's job (tests/test_truth_check.py)
        self.assertIn(res[0]["status"], ("built", "needs_fix"), res)
        folder = os.path.join(self.out, res[0]["folder"])
        self.assertTrue(res[0]["folder"].startswith(f"{aid}-"))
        return folder

    def assert_common_files(self, folder):
        for name in ("listing.md", "README.md", "cover_image_idea.txt", "LICENSE.txt", "cover.png",
                     "preview-1.png", "preview-2.png", "preview-3.png", "pinterest.png"):
            self.assertTrue(os.path.isfile(os.path.join(folder, name)), name)
        with open(os.path.join(folder, "LICENSE.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "Personal use only. Do not resell or share the files.")
        with open(os.path.join(folder, "listing.md"), encoding="utf-8") as f:
            listing = f.read()
        self.assertIn("USD", listing)
        self.assertIn("**Buyer:**", listing)
        self.assertIn("**Platform:**", listing)
        tags = listing.split("## Tags (5)")[1].split("##")[0]
        self.assertEqual(len(re.findall(r"^- ", tags, re.M)), 5)


def xml_parts(path):
    with zipfile.ZipFile(path) as z:
        self_check = z.testzip()
        assert self_check is None, self_check
        return {n: ET.fromstring(z.read(n)) for n in z.namelist() if n.endswith((".xml", ".rels"))}


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class FileTypeTests(Base):
    def test_budget_sheet_xlsx(self):
        folder = self.build_one("Budget Sheets Agent")
        self.assert_common_files(folder)
        parts = xml_parts(os.path.join(folder, "freelancer-budget.xlsx"))
        names = [s.get("name") for s in parts["xl/workbook.xml"].iter(f"{{{NS['m']}}}sheet")]
        self.assertEqual(names, ["Instructions", "Dashboard", "Income Log", "Bills"])
        income = parts["xl/worksheets/sheet3.xml"]
        formulas = [f.text for f in income.iter(f"{{{NS['m']}}}f")]
        self.assertIn("SUM(C2:C3)", formulas)
        self.assertTrue(any("OVERDUE" in f and "TODAY()" in f for f in formulas))
        self.assertTrue(any(f.startswith("N(F2)+") for f in formulas))  # running balance
        self.assertIsNotNone(income.find(".//m:pane[@state='frozen']", NS))
        self.assertIsNotNone(income.find(".//m:dataValidation", NS))
        dash = [f.text for f in parts["xl/worksheets/sheet2.xml"].iter(f"{{{NS['m']}}}f")]
        self.assertIn("'Income Log'!C5", dash)
        values = [v.text for v in income.iter(f"{{{NS['m']}}}v")]
        self.assertIn("1200.0", values)  # "1,200" became a real number

    def test_planner_pdfs_a4_and_letter(self):
        folder = self.build_one("ADHD Planners Agent")
        self.assert_common_files(folder)
        for size, box in (("A4", "595.28 841.89"), ("Letter", "612.0 792.0")):
            with open(os.path.join(folder, f"energy-planner-{size}.pdf"), "rb") as f:
                data = f.read()
            self.assertTrue(data.startswith(b"%PDF-1.4"))
            self.assertTrue(data.rstrip().endswith(b"%%EOF"))
            pages = len(re.findall(rb"/Type /Page ", data))
            self.assertGreaterEqual(pages, 5)  # cover, How to use, then the planner pages
            self.assertIn(f"/Count {pages}".encode(), data)
            self.assertIn(f"/MediaBox [0 0 {box}]".encode(), data)
            self.assertIn(b"(ENERGY CHECK) Tj", data)
            self.assertIn(f"(Page {pages} of {pages}) Tj".encode(), data)
            # every xref offset must point at its object
            xref = int(re.search(rb"startxref\n(\d+)", data).group(1))
            offsets = re.findall(rb"(\d{10}) 00000 n", data[xref:])
            for i, off in enumerate(offsets, 1):
                self.assertTrue(data[int(off):].startswith(f"{i} 0 obj".encode()))

    def test_notion_zip_and_guide(self):
        folder = self.build_one("Notion Templates Agent")
        self.assert_common_files(folder)
        self.assertTrue(os.path.isfile(os.path.join(folder, "HOW_TO_IMPORT.md")))
        with zipfile.ZipFile(os.path.join(folder, "client-crm-notion-import.zip")) as z:
            self.assertEqual(sorted(z.namelist()), ["clients.csv", "start-here.md"])
            rows = list(csv.reader(io.StringIO(z.read("clients.csv").decode())))
            self.assertTrue(z.read("start-here.md").decode().startswith("# Start Here"))
        self.assertEqual(rows[0], ["Name", "Status", "Next Follow-up"])
        self.assertEqual(rows[1][0], "Client A")

    def test_resume_docx_and_tracker(self):
        folder = self.build_one("Resume Bundle Agent")
        self.assert_common_files(folder)
        resume = ET.tostring(xml_parts(os.path.join(folder, "ats-resume-resume.docx"))["word/document.xml"],
                             encoding="unicode")
        self.assertIn("[Your Name]", resume)
        self.assertIn("Professional Summary", resume)
        letter = ET.tostring(xml_parts(os.path.join(folder, "ats-resume-cover-letter.docx"))["word/document.xml"],
                             encoding="unicode")
        self.assertIn("Dear [Hiring Manager Name],", letter)
        tracker = xml_parts(os.path.join(folder, "ats-resume-job-tracker.xlsx"))
        names = [s.get("name") for s in tracker["xl/workbook.xml"].iter(f"{{{NS['m']}}}sheet")]
        self.assertIn("Job Applications", names)
        formulas = [f.text for f in tracker["xl/worksheets/sheet3.xml"].iter(f"{{{NS['m']}}}f")]
        self.assertTrue(any("OVERDUE" in f for f in formulas))  # default tracker used when spec has none


class FlowTests(Base):
    def test_only_approved_drafts(self):
        self.add(TITLES["Budget Sheets Agent"], status="pending")
        self.add(TITLES["Budget Sheets Agent"], status="rejected")
        llm = StubLLM(reply_for_prompt)
        self.assertEqual(self.run_builder(llm), [])
        self.assertEqual(llm.calls, 0)
        self.assertFalse(os.path.exists(self.out))

    def test_non_niche_draft_is_skipped_without_llm(self):
        aid = self.add("Draft: a free mini-tool page (v1, llm)")
        llm = StubLLM(reply_for_prompt)
        self.assertEqual(self.run_builder(llm), [{"id": aid, "status": "skipped"}])
        self.assertEqual(llm.calls, 0)
        self.assertEqual(self.run_builder(llm), [])  # remembered

    def test_never_rebuilds(self):
        self.add(TITLES["Budget Sheets Agent"])
        llm = StubLLM(reply_for_prompt)
        self.assertIn(self.run_builder(llm)[0]["status"], ("built", "needs_fix"))
        self.assertEqual(self.run_builder(llm), [])
        self.assertEqual(llm.calls, 1)
        with open(os.path.join(self.out, "INDEX.md"), encoding="utf-8") as f:
            self.assertRegex(f.read(), r"ready \(truth check passed\)|needs fix - see TRUTH_CHECK.md")

    def test_existing_folder_counts_as_built(self):
        aid = self.add(TITLES["Budget Sheets Agent"])
        os.makedirs(os.path.join(self.out, f"{aid}-old-copy"))
        llm = StubLLM(reply_for_prompt)
        self.assertEqual(self.run_builder(llm), [])
        self.assertEqual(llm.calls, 0)
        self.assertEqual(pb.builds(self.db)[0]["status"], "built")

    def test_bad_json_retries_then_gives_up(self):
        aid = self.add(TITLES["Budget Sheets Agent"])
        llm = StubLLM("Sure! {not json")
        for expected in ("retry", "retry", "failed"):
            self.assertEqual(self.run_builder(llm)[0]["status"], expected)
        self.assertEqual(self.run_builder(llm), [])
        self.assertEqual(llm.calls, pb.MAX_TRIES)
        self.assertFalse(os.path.exists(os.path.join(self.out, f"{aid}-freelancer-budget")))

    def test_retry_succeeds_on_next_run(self):
        self.add(TITLES["Budget Sheets Agent"])
        replies = iter(["no json here", json.dumps(SPECS["Budget Sheets Agent"])])
        llm = StubLLM(lambda p: next(replies))
        self.assertEqual(self.run_builder(llm)[0]["status"], "retry")
        self.assertIn(self.run_builder(llm)[0]["status"], ("built", "needs_fix"))

    def test_model_error_is_a_retry(self):
        self.add(TITLES["Budget Sheets Agent"])
        res = self.run_builder(StubLLM(RuntimeError("rate limit")))
        self.assertEqual(res[0]["status"], "retry")
        self.assertIn("model error", res[0]["reason"])

    def test_risky_listing_is_not_built(self):
        self.add(TITLES["Resume Bundle Agent"])
        risky = dict(SPECS["Resume Bundle Agent"],
                     listing=dict(LISTING, description="Use this resume and get hired in a week."))
        res = self.run_builder(StubLLM(json.dumps(risky)))
        self.assertEqual(res[0]["status"], "retry")
        self.assertIn("risky", res[0]["reason"])

    def test_at_most_max_builds_per_run(self):
        for niche in SPECS:
            self.add(TITLES[niche])
        llm = StubLLM(reply_for_prompt)
        self.assertEqual(len(self.run_builder(llm, max_builds=2)), 2)
        self.assertEqual(len(self.run_builder(llm, max_builds=2)), 2)
        self.assertEqual(llm.calls, 4)
        self.assertEqual(llm.sizes, ["large"] * 4)


class ParseTests(unittest.TestCase):
    def test_tags_padded_to_five_and_price_cleaned(self):
        spec, _ = pb.parse_spec(json.dumps(SPECS["ADHD Planners Agent"]), "ADHD Planners Agent")
        self.assertEqual(len(spec["listing"]["tags"]), 5)
        self.assertEqual(spec["listing"]["price_usd"], 8.0)

    def test_unusable_specs(self):
        for reply in (None, "", "[1]", "{}", '{"listing": {"title": "x"}}',
                      json.dumps({"listing": LISTING, "sheets": []})):
            with self.subTest(reply=reply):
                spec, reason = pb.parse_spec(reply, "Budget Sheets Agent")
                self.assertIsNone(spec)
                self.assertTrue(reason)

    def test_niche_from_title(self):
        self.assertEqual(pb.niche_of(TITLES["Notion Templates Agent"]), "Notion Templates Agent")
        self.assertIsNone(pb.niche_of("Draft: [Unknown Agent] x"))
        self.assertIsNone(pb.niche_of(None))


class OldProductImageTests(Base):
    def test_old_products_get_images_without_changing_files(self):
        folder = self.build_one("ADHD Planners Agent")
        for name in ("cover.png", "preview-1.png", "preview-2.png", "preview-3.png", "pinterest.png", "LICENSE.txt"):
            os.remove(os.path.join(folder, name))             # like a product built before images existed
        before = {n: open(os.path.join(folder, n), "rb").read() for n in os.listdir(folder)}
        self.assertEqual(pb.add_missing_images(self.out), [os.path.basename(folder)])
        after = set(os.listdir(folder))
        self.assertTrue({"cover.png", "preview-1.png", "preview-2.png", "preview-3.png", "pinterest.png",
                         "LICENSE.txt"} <= after)
        for n, data in before.items():
            self.assertEqual(open(os.path.join(folder, n), "rb").read(), data, n)   # product files untouched
        self.assertEqual(pb.add_missing_images(self.out), [])                      # nothing done twice

    def test_planner_pages_read_back_from_pdf(self):
        folder = self.build_one("ADHD Planners Agent")
        pdf = next(n for n in os.listdir(folder) if n.endswith("-A4.pdf"))
        pages = pb.planner_pages_from_pdf(os.path.join(folder, pdf))
        spec = SPECS["ADHD Planners Agent"]
        self.assertEqual([p["title"] for p in pages], [p["title"] for p in spec["pages"]])
        first = pages[0]["sections"][0]
        self.assertEqual(first["heading"].lower(), spec["pages"][0]["sections"][0]["heading"].lower())

    def test_ai_down_does_not_use_up_tries(self):
        aid = self.add(TITLES["ADHD Planners Agent"])
        for _ in range(5):
            res = self.run_builder(StubLLM(lambda p: None))
            self.assertEqual(res[0]["status"], "retry")
        row = [b for b in pb.builds(self.db) if b["approval_id"] == aid][0]
        self.assertEqual(row["tries"], 0)

    def test_old_ai_down_failure_gets_another_chance(self):
        aid = self.add(TITLES["ADHD Planners Agent"])
        pb._record(self.db, aid, "failed", note=pb.AI_DOWN, add_try=True)
        res = self.run_builder(StubLLM(reply_for_prompt))
        self.assertIn(res[0]["status"], ("built", "needs_fix"))     # it was built this time
        pb._record(self.db, aid + 100, "failed", note="reply is not valid JSON")
        self.assertNotIn(aid + 100, [t["id"] for t in pb._todo(self.db, self.out)])


if __name__ == "__main__":
    unittest.main()
