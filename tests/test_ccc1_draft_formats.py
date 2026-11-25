"""CCC-1: drafts point at the money-sheet and ADHD products; every new draft names a buildable format.

Queue shaped like 24 Nov: many "[Budget Sheets Agent]" / "[ADHD Planners Agent]" drafts with
"(v4, llm) (rewritten)" titles, plus resume / 2027 planner drafts.
"""
import random
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import format_registry
import guardrails
import product_builder as pb
import product_pipeline as pipeline

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ("PRODUCT NAME: {name}\nBUYER: adults\nPRICE: [amount]\nPLATFORM: Gumroad\nWHAT'S INSIDE: tabs and pages\n"
         "HOW TO USE: Open it.\nLISTING TITLE: {name}\nLISTING DESCRIPTION: A simple tool.\n"
         "SEARCH TAGS: a, b, c, d, e\n")
NOV24 = [
    ("Draft: [Budget Sheets Agent] Google Sheets debt snowball payoff tracker with a progress (v4, llm) (rewritten)", "debt_payoff"),
    ("Draft: [Budget Sheets Agent] Google Sheets shared budget for couples who split bills (v4, llm)", "couples_budget"),
    ("Draft: [Budget Sheets Agent] Google Sheets irregular-income monthly budget for freelancer (v3, llm)", "freelance_budget"),
    ("Draft: [Budget Sheets Agent] Sinking funds tracker for yearly costs (v4, llm)", "sinking_funds"),
    ("Draft: [Budget Sheets Agent] Google Sheets monthly household budget (v4, llm)", "sheets"),
    ("Draft: [ADHD Planners Agent] printable energy-based daily planner for ADHD adults (v4, llm)", "adhd_planner"),
    ("Draft: [ADHD Planners Agent] printable time-blindness routine planner with visual (v4, llm) (rewritten)", "adhd_planner"),
    ("Draft: [ADHD Planners Agent] printable focus sheets pack (brain dump, one-task focus (v4, llm)", "adhd_planner"),
    ("Draft: [ADHD Planners Agent] printable weekly chore planner for busy parents (v4, llm)", "planner"),
    ("Draft: [Resume Bundle Agent] ATS-friendly one-page resume template (v4, llm)", "resume"),
    ("Draft: [2027 Planners Agent] printable 2027 monthly planner for teachers (v4, llm)", "dated_planner"),
]


class DraftFormatTests(unittest.TestCase):
    def test_nov24_titles_route_to_the_dedicated_builders(self):
        for title, expected in NOV24:
            with self.subTest(title):
                self.assertEqual(pb.draft_format(title, DRAFT.format(name="x")), expected)
                niche = pb.niche_for_draft(title, DRAFT.format(name="x"))
                self.assertEqual(pb.NICHES[niche]["kind"], expected)
                self.assertTrue(format_registry.can_build({"kind": expected}))

    def test_named_format_line_is_honoured_only_for_the_same_file_type(self):
        title = "Draft: [Budget Sheets Agent] Google Sheets monthly budget (v4, llm)"
        self.assertEqual(pb.draft_format(title, DRAFT.format(name="x") + "FORMAT: sinking_funds\n"), "sinking_funds")
        # A spreadsheet draft never turns into a PDF planner, whatever the line says.
        self.assertEqual(pb.draft_format(title, DRAFT.format(name="x") + "FORMAT: adhd_planner\n"), "sheets")

    def test_upgraded_niche_keeps_the_agent_buyer_and_price(self):
        niche = pb.niche_for_draft(NOV24[0][0], "")
        self.assertEqual(pb.NICHES[niche]["price"], pb.NICHES["Budget Sheets Agent"]["price"])
        self.assertEqual(pb.NICHES[niche]["tags"][0], "debt payoff")
        self.assertIn("Product type: debt_payoff", pb.spec_prompt(niche, DRAFT.format(name="x")))
        self.assertIn("FIXED and made by code", pb.spec_prompt(niche, ""))

    def test_every_recipe_topic_names_a_buildable_format(self):
        found = set()
        for recipe in sorted((ROOT / "recipes").glob("niche_*.yaml")):
            for topic in re.findall(r'topic: "(.*)"', recipe.read_text()):
                fmt = pb.draft_format(topic, "")
                self.assertTrue(fmt and format_registry.can_build({"kind": fmt}), topic)
                found.add(fmt)
        self.assertTrue({"debt_payoff", "couples_budget", "freelance_budget", "adhd_planner"} <= found, found)

    def test_queue_rotation_keeps_variety_with_the_new_formats(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / "q.db")
            for title, _ in NOV24 * 3:
                aid = guardrails.request_approval("public_draft", title, DRAFT.format(name="x"), db_path=db)
                with guardrails.connect(db) as conn:
                    conn.execute("UPDATE approvals SET status='approved' WHERE id=?", (aid,))
            first = [pipeline.queue_niche(i["title"], i["payload"]) for i in pipeline.pending(db)[:8]]
            self.assertEqual(len(set(first)), 8, first)   # eight different products before any repeat


class NewDraftTests(unittest.TestCase):
    def test_step_draft_writes_the_format_line(self):
        import workforce
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / "w.db")
            guardrails.connect(db).close()
            llm = lambda prompt, size=None: DRAFT.format(name="Debt Snowball Tracker")
            topic = "[Budget Sheets Agent] Google Sheets debt snowball payoff tracker for adults"
            with mock.patch.object(workforce, "active_draft_backlog", return_value=0):
                result = workforce.step_draft({"payload": {"topic": topic, "research": "notes"}},
                                              {"db_path": db, "llm": llm, "rng": random.Random(1)})
            payload = guardrails.get_approval(result["approval_id"], db)["payload"]
            self.assertTrue(payload.rstrip().endswith("FORMAT: debt_payoff"))
            self.assertEqual(pb.niche_for_draft(guardrails.get_approval(result["approval_id"], db)["title"],
                                                payload), "Debt Payoff Agent")

    def test_unbuildable_draft_is_kept_without_a_format_line_and_rewrite_keeps_the_line(self):
        import workforce
        self.assertEqual(workforce.with_format_line("demo", "Some text"), "Some text")
        named = workforce.with_format_line("[Budget Sheets Agent] couples budget spreadsheet", "Body")
        self.assertTrue(named.endswith("FORMAT: couples_budget\n"))
        self.assertEqual(workforce.with_format_line("x", named), named)


class ScoutTests(unittest.TestCase):
    def test_scout_prefers_top_niches_keeps_variety_and_names_registered_formats(self):
        import scout
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / "s.db")
            guardrails.connect(db).close()
            with mock.patch.object(guardrails, "can_call_llm", return_value=False):
                scout.run_scout(db_path=db, llm=None)
            with guardrails.connect(db) as conn:
                rows = conn.execute("SELECT payload FROM approvals WHERE title LIKE '[Scout Idea]%'").fetchall()
        formats = [re.search(r"(?m)^Format: (\S+)$", p).group(1) for (p,) in rows]
        self.assertTrue(formats)
        for fmt in formats:
            self.assertIsNotNone(format_registry.get_format_info(fmt), fmt)
        self.assertTrue(set(formats) & scout.TOP_NICHE_FORMATS, formats)
        self.assertGreaterEqual(len(set(formats)), 3, formats)


if __name__ == "__main__":
    unittest.main()
