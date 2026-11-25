"""CCZ-1: approved drafts must not die at "Failed to parse spec" (live report 25 Nov).

Offline: the model is a plain function, files go to temp folders only.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import guardrails
import launch_pack
import product_builder as pb
import product_pipeline as pipeline

BUDGET = "Budget Sheets Agent"
ADHD = "ADHD Planners Agent"

LIVE_DRAFT = """PRODUCT NAME: Google Sheets Monthly Budget Tracker
BUYER: people who want a calm monthly view of spending
PRICE: [amount]
PLATFORM: Gumroad
WHAT'S INSIDE: Instructions tab, Income tab, Expenses tab with category and date columns
HOW TO USE: Make a copy. Type income. Type expenses.
LISTING TITLE: Monthly Budget Tracker for Google Sheets - Income and Expense Log
LISTING DESCRIPTION: A simple monthly budget spreadsheet for Google Sheets and Excel.
Log income and expenses by category and see the totals in one place.
SEARCH TAGS: budget tracker, google sheets, expense log, monthly budget, spreadsheet
"""

SHEETS = [{"name": "Expenses", "columns": [{"name": "Date", "type": "date"},
                                           {"name": "Category", "type": "text"},
                                           {"name": "Amount", "type": "money"}],
           "rows": [["2027-01-03", "Groceries", ""]]}]


def good_budget(**extra):
    spec = {"name": "Monthly Budget Tracker", "short_name": "monthly-budget",
            "listing": {"title": "Monthly Budget Tracker", "description": "A simple budget sheet for one month.",
                        "tags": ["budget", "sheets", "tracker", "monthly", "expenses"]},
            "instructions": ["Make a copy."], "sheets": SHEETS}
    spec.update(extra)
    return spec


class ExtractJsonTests(unittest.TestCase):
    def test_reply_shapes_from_free_models(self):
        body = json.dumps(good_budget())
        cases = {
            "fenced": "Here is the spec you asked for:\n```json\n" + body + "\n```\nHope this helps!",
            "prose": "Sure! {not json} The product spec: " + body + " Let me know {if} needed.",
            "trailing": body.replace("]}]", "],}],").replace('"monthly"]', '"monthly",]'),
            "python": body.replace("[\"2027-01-03\", \"Groceries\", \"\"]", '["2027-01-03", "Groceries", None]')
                          .replace('"instructions"', '"dashboard": True, "instructions"'),
            "smart": body.replace('"Make a copy."', "“Make a copy.”"),
            "comments": body.replace('"instructions"', '// the steps\n"instructions"'),
            "wrapped": json.dumps({"spec": good_budget()}),
            "unclosed_fence": "```json\n" + body,
        }
        for name, reply in cases.items():
            with self.subTest(name):
                data, reason = pb.extract_json_object(reply)
                self.assertIsNone(reason)
                self.assertEqual(data["listing"]["title"], "Monthly Budget Tracker")
                spec, why = pb.parse_spec(reply, BUDGET)
                self.assertIsNone(why, name)
                self.assertEqual(spec["sheets"][0]["name"], "Expenses")

    def test_strings_with_hash_comma_brace_and_words_survive_repair(self):
        raw = '{"listing": {"title": "Plan #1, {easy} None True", "description": "x, ]"},}'
        data, reason = pb.extract_json_object(raw)
        self.assertIsNone(reason)
        self.assertEqual(data["listing"]["title"], "Plan #1, {easy} None True")
        self.assertEqual(data["listing"]["description"], "x, ]")

    def test_reply_cut_at_token_limit_is_closed(self):
        body = json.dumps(good_budget())
        cut = body[:body.index('"rows"') + 25]   # stops inside the sample row
        spec, why = pb.parse_spec(cut, BUDGET)
        self.assertIsNone(why)
        self.assertEqual([c["name"] for c in spec["sheets"][0]["columns"]], ["Date", "Category", "Amount"])

    def test_no_reply_and_no_json_reasons(self):
        self.assertEqual(pb.parse_spec(None, BUDGET), (None, pb.AI_DOWN))
        self.assertEqual(pb.parse_spec("   ", BUDGET), (None, pb.AI_DOWN))
        self.assertEqual(pb.parse_spec("I cannot help with that.", BUDGET)[1], "reply has no JSON object")
        self.assertEqual(pb.parse_spec("{ totally : broken ]] ", BUDGET)[1], "reply is not valid JSON")


class SafeDefaultTests(unittest.TestCase):
    def test_missing_listing_is_filled_from_the_approved_draft(self):
        reply = json.dumps({"name": "Budget", "sheets": SHEETS})
        self.assertEqual(pb.parse_spec(reply, BUDGET)[1], "spec has no listing")   # no draft: still strict
        spec, why = pb.parse_spec(reply, BUDGET, LIVE_DRAFT)
        self.assertIsNone(why)
        self.assertTrue(spec["listing"]["title"].startswith("Monthly Budget Tracker for Google Sheets"))
        self.assertIn("Log income and expenses", spec["listing"]["description"])
        self.assertEqual(spec["listing"]["tags"][:2], ["budget tracker", "google sheets"])

    def test_string_tags_and_instructions_are_split(self):
        spec, why = pb.parse_spec(json.dumps(good_budget(instructions="1. Make a copy\n2. Type income")), BUDGET)
        self.assertIsNone(why)
        self.assertEqual(len(spec["instructions"]), 2)
        data = good_budget()
        data["listing"]["tags"] = "budget, sheets, money planner"
        spec, why = pb.parse_spec(json.dumps(data), BUDGET)
        self.assertEqual(spec["listing"]["tags"][:3], ["budget", "sheets", "money planner"])

    def test_live_title_markers_are_cleaned(self):
        self.assertEqual(pb.clean_draft_title(
            "Draft: [Budget Sheets Agent] Google Sheets Debt Payoff Tracker (v4, llm) (rewritten)"),
            "Google Sheets Debt Payoff Tracker")

    def test_checker_stays_strict_on_structure_and_claims(self):
        self.assertEqual(pb.parse_spec(json.dumps(good_budget(sheets=[])), BUDGET, LIVE_DRAFT)[1],
                         "spec has no usable sheets")
        self.assertEqual(pb.parse_spec(json.dumps({"listing": {"title": "Plan", "description": "d"}}),
                                       ADHD)[1], "spec has no usable planner pages")
        risky = good_budget()
        risky["listing"]["description"] = "Guaranteed to cure anxiety in a week."
        self.assertTrue(pb.parse_spec(json.dumps(risky), BUDGET)[1].startswith("risky claim"))


class RewriteRetryTests(unittest.TestCase):
    def test_one_rewrite_retry_fixes_a_bad_first_answer(self):
        replies = ["Sorry, here it is: {\"name\": \"x\"", json.dumps(good_budget())]
        prompts = []
        spec, why = pb.request_spec(lambda p: prompts.append(p) or replies.pop(0), BUDGET, LIVE_DRAFT)
        self.assertIsNone(why)
        self.assertEqual(len(prompts), 2)
        self.assertIn("could not be used. Problem: spec has no usable sheets", prompts[1])

    def test_truly_unparseable_gives_clear_reason_after_exactly_one_retry(self):
        prompts = []
        spec, why = pb.request_spec(lambda p: prompts.append(p) or "no json here", BUDGET, LIVE_DRAFT)
        self.assertIsNone(spec)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(why, "reply has no JSON object (after one rewrite retry)")

    def test_ai_down_never_uses_the_retry(self):
        prompts = []
        self.assertEqual(pb.request_spec(lambda p: prompts.append(p), BUDGET, LIVE_DRAFT), (None, pb.AI_DOWN))
        self.assertEqual(len(prompts), 1)


class LaunchPackReasonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = str(Path(temp.name) / "t.db")
        self.out = str(Path(temp.name) / "products")
        self.aid = guardrails.request_approval(
            "public_draft", "Draft: [Budget Sheets Agent] Google Sheets Budget (v4, llm) (rewritten)",
            LIVE_DRAFT, None, self.db)
        patch = mock.patch.object(launch_pack, "is_eligible", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_unparseable_spec_names_the_real_reason(self):
        res = launch_pack.build_launch_pack(self.aid, db_path=self.db, out_dir=self.out,
                                            llm=lambda p, size=None: "I can't produce JSON today.")
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["reason"], "Spec could not be read: reply has no JSON object (after one rewrite retry)")
        self.assertNotIn("Failed to parse spec", res["reason"])

    def test_no_ai_reply_or_provider_error_is_waiting_not_failed(self):
        def boom(prompt, size=None):
            raise TimeoutError("slow")
        for llm in (lambda p, size=None: None, boom):
            res = launch_pack.build_launch_pack(self.aid, db_path=self.db, out_dir=self.out, llm=llm)
            self.assertEqual(res["status"], "waiting_ai")

    def test_fenced_answer_with_trailing_commas_builds_the_product(self):
        reply = "```json\n" + json.dumps(good_budget()).replace("]}]", "],}],") + "\n```"
        with mock.patch.object(pb, "build_product", side_effect=RuntimeError("built")) as build:
            with self.assertRaisesRegex(RuntimeError, "built"):
                launch_pack.build_launch_pack(self.aid, db_path=self.db, out_dir=self.out,
                                              llm=lambda p, size=None: reply)
        self.assertEqual(build.call_args[0][0]["sheets"][0]["name"], "Expenses")


class PipelineWaitingTests(unittest.TestCase):
    def test_waiting_ai_gives_the_daily_slot_back(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / "q.db")
            import checker
            import product_council
            aid = guardrails.request_approval("public_draft", "Draft: [Budget Sheets Agent] Google Sheets Budget",
                                              LIVE_DRAFT, checker.format_review(90, [], "pass"), db)
            with guardrails.connect(db) as conn:
                conn.execute("UPDATE approvals SET status='approved' WHERE id=?", (aid,))
            product_council.log_debate(aid, 1, "council_summary", "PASS", "ok", db_path=db)
            waiting = {"approval_id": aid, "status": "waiting_ai", "reason": "AI reply raaledu"}
            with mock.patch.object(pipeline, "_allowed", return_value=True), \
                    mock.patch.dict(os.environ, {"DB_KEY": ""}), \
                    mock.patch.object(pipeline, "_marketing_lines", return_value={}), \
                    mock.patch("launch_pack.build_launch_pack", return_value=waiting):
                res = pipeline.run_pending(db, str(Path(temp) / "products"), lambda *a, **k: "", day="2027-01-04")
            self.assertEqual(res[0]["status"], "waiting_ai")
            with pipeline.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM product_delivery_attempts").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT status FROM product_deliveries").fetchone()[0], "waiting_ai")


if __name__ == "__main__":
    unittest.main()
