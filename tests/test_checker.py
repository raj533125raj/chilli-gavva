"""Tests for checker.py: product quality checks, AI & rule-based fallback, retries, and Telegram cards."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import checker
import guardrails
import telegram_inbox as ti
import workforce

GOOD_DRAFT = """PRODUCT NAME: Freelancer Invoice & Budget Tracker

BUYER: Freelancers and independent creators who need to manage client invoices and track monthly budgets.

PRICE: Listed in USD

PLATFORM: Gumroad

WHAT'S INSIDE:
- Client CRM database
- Automated Invoice Generator with formula totals
- Irregular Income Budget Planner
- Tax Estimation Dashboard

HOW TO USE:
1. Duplicate to your Notion workspace.
2. Enter client details.
3. Generate invoices and log income.

LISTING TITLE: Freelancer Invoice & Budget Tracker for Notion

LISTING DESCRIPTION: Unlike a simple blank spreadsheet or basic free template, this complete workspace gives freelancers pre-filled formulas, automated totals, and step-by-step guidance to manage cash flow effortlessly. Save hours every month.

SEARCH TAGS: freelancer budget, notion invoice, invoice tracker, budget template, freelancer tools"""

BAD_DRAFT = "Product for people. Buy it now."

MONEY_CLAIM_DRAFT = GOOD_DRAFT + "\nGuaranteed passive income! Earn money fast with zero risk."

LEAK_DRAFT = GOOD_DRAFT + "\nConfidential internal strategy: secret strategy do not share!"


class CheckerUnitTests(unittest.TestCase):
    def test_good_draft_scores_high(self):
        res = checker.rule_based_fallback(GOOD_DRAFT, topic="Freelancer Budget")
        self.assertGreaterEqual(res["score"], 70)
        self.assertTrue(res["passed"])
        self.assertIsInstance(res["reasons"], list)

    def test_bad_draft_scores_low(self):
        res = checker.rule_based_fallback(BAD_DRAFT, topic="Unknown")
        self.assertLess(res["score"], 70)
        self.assertFalse(res["passed"])
        self.assertTrue(len(res["reasons"]) > 0)

    def test_money_claims_deduct_points(self):
        res = checker.rule_based_fallback(MONEY_CLAIM_DRAFT, topic="Freelancer Budget")
        self.assertLess(res["score"], 70)
        self.assertIn("Contains prohibited financial or earnings promises", res["reasons"])

    def test_leak_deducts_points(self):
        res = checker.rule_based_fallback(LEAK_DRAFT, topic="Freelancer Budget")
        self.assertLess(res["score"], 70)
        self.assertIn("Contains unapproved internal information or leak", res["reasons"])

    def test_ai_response_parsing_valid_json(self):
        raw_json = '{"score": 82, "verdict": "pass", "reasons": ["Clear audience", "Explains value over free template"]}'
        parsed = checker.parse_checker_response(raw_json)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["score"], 82)
        self.assertEqual(parsed["verdict"], "pass")
        self.assertEqual(len(parsed["reasons"]), 2)
        self.assertTrue(parsed["passed"])

    def test_ai_response_parsing_invalid_json(self):
        self.assertIsNone(checker.parse_checker_response("Not JSON at all"))
        self.assertIsNone(checker.parse_checker_response('{"score": "invalid"}'))

    def test_check_draft_fallback_when_ai_fails(self):
        ctx = {"llm": lambda prompt, size=None: None}
        res = checker.check_draft(GOOD_DRAFT, topic="Freelancer Budget", ctx=ctx)
        self.assertIsNotNone(res)
        self.assertGreaterEqual(res["score"], 70)
        self.assertTrue(res["fallback"])

    def test_check_draft_uses_ai_when_available(self):
        ai_reply = '{"score": 88, "verdict": "pass", "reasons": ["Solid structure"]}'
        ctx = {"llm": lambda prompt, size=None: ai_reply}
        res = checker.check_draft(GOOD_DRAFT, topic="Freelancer Budget", ctx=ctx)
        self.assertEqual(res["score"], 88)
        self.assertFalse(res["fallback"])

    def test_format_and_parse_review(self):
        formatted = checker.format_review(82, ["Clear audience", "Beats free template"])
        self.assertIn("score 82/100", formatted)
        self.assertIn("Checker: 82/100", formatted)

        score, reasons = checker.parse_review(formatted)
        self.assertEqual(score, 82)
        self.assertEqual(reasons, ["Clear audience", "Beats free template"])


class CheckerWorkflowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "checker_test.db")

    def test_draft_under_70_triggers_retry(self):
        approval_id = guardrails.request_approval("public_draft", "Draft: test", BAD_DRAFT, db_path=self.db)
        ctx = {
            "db_path": self.db,
            "llm": lambda prompt, size=None: '{"score": 50, "verdict": "revise", "reasons": ["Target audience is unclear"]}',
            "rng": None
        }
        # First check (retries = 0) -> queues retry 1
        res1 = workforce.step_review({"payload": {"approval_id": approval_id, "topic": "test", "version_id": 1, "retries": 0}}, ctx)
        self.assertEqual(res1["score"], 50)
        self.assertEqual(len(res1["next"]), 1)
        self.assertEqual(res1["next"][0]["kind"], "rewrite")
        self.assertEqual(res1["next"][0]["payload"]["retries"], 1)

        # Second check (retries = 1) -> queues retry 2
        res2 = workforce.step_review({"payload": {"approval_id": approval_id, "topic": "test", "version_id": 1, "retries": 1}}, ctx)
        self.assertEqual(res2["score"], 50)
        self.assertEqual(len(res2["next"]), 1)
        self.assertEqual(res2["next"][0]["kind"], "rewrite")
        self.assertEqual(res2["next"][0]["payload"]["retries"], 2)

        # Third check (retries = 2) -> max retries reached, no further rewrite queued
        res3 = workforce.step_review({"payload": {"approval_id": approval_id, "topic": "test", "version_id": 1, "retries": 2}}, ctx)
        self.assertEqual(res3["score"], 50)
        self.assertEqual(len(res3["next"]), 0)

    def test_telegram_card_shows_checker_score_and_top_reasons(self):
        review_str = "score 82/100 (checklist v2) | Checker: 82/100 | CHECKER: PASS (AI 82) | top: Clear audience; Beats free template"
        item = {"id": 12, "title": "Freelancer Workspace Template", "review": review_str}
        card = ti.draft_card(item)
        lines = card.splitlines()
        self.assertEqual(lines[0], "#12 Freelancer Workspace Template")
        self.assertEqual(lines[1], "Checker: 82/100")
        self.assertEqual(lines[2], "- Clear audience")
        self.assertEqual(lines[3], "- Beats free template")


if __name__ == "__main__":
    unittest.main()
