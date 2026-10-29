"""Unit tests for product_council.py: 5 stages, multi-round loop, cap/skip, fallback, and Telegram card formatting."""

import os
import re
import sqlite3
import tempfile
import unittest

import guardrails
import product_council
import telegram_inbox


SAMPLE_DRAFT = """PRODUCT NAME: Creator Tool
BUYER: Small content creators looking to organize their schedule
PRICE: 10 USD
PLATFORM: Gumroad
WHAT'S INSIDE: Schedule planner, post tracker, analytics dashboard
HOW TO USE: 1. Download spreadsheet 2. Fill schedule 3. Track performance
LISTING TITLE: Ultimate Creator Schedule Planner
LISTING DESCRIPTION: Organize your content strategy efficiently with ready-made formulas and dashboards.
SEARCH TAGS: planner, creator, content, schedule, dashboard"""


class TestProductCouncil(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        self.conn = product_council.connect(self.db)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db):
            os.remove(self.db)

    # --- Stage Tests & Fallbacks ---
    def test_critic_fallback(self):
        res = product_council.critic_fallback(SAMPLE_DRAFT, topic="Creator Tool")
        self.assertTrue(res["fallback"])
        self.assertIn("weaknesses", res)
        self.assertIn(res["verdict"], ("FLAGGED", "OK"))

    def test_critic_stage_no_ai(self):
        res, calls = product_council.run_critic(SAMPLE_DRAFT, topic="Creator Tool", allow_ai=False)
        self.assertEqual(calls, 0)
        self.assertTrue(res["fallback"])

    def test_buyer_fallback(self):
        res = product_council.buyer_fallback(SAMPLE_DRAFT, topic="Creator Tool")
        self.assertTrue(res["fallback"])
        self.assertIn("answer", res)
        self.assertGreaterEqual(res["conviction"], 0)
        self.assertLessEqual(res["conviction"], 10)

    def test_buyer_stage_no_ai(self):
        res, calls = product_council.run_buyer(SAMPLE_DRAFT, topic="Creator Tool", allow_ai=False)
        self.assertEqual(calls, 0)
        self.assertTrue(res["fallback"])

    def test_fixer_stage_no_ai(self):
        fixed, calls = product_council.run_fixer(SAMPLE_DRAFT, "Creator Tool", ["Weak description"], "Better value", allow_ai=False)
        self.assertEqual(calls, 0)
        self.assertEqual(fixed, SAMPLE_DRAFT)

    def test_judge_fallback(self):
        res = product_council.judge_fallback(SAMPLE_DRAFT, "Creator Tool", qa_score=85, conviction=8, weaknesses=[], db_path=self.db)
        self.assertTrue(res["fallback"])
        self.assertEqual(res["verdict"], "PASS")

        res_low = product_council.judge_fallback(SAMPLE_DRAFT, "Creator Tool", qa_score=50, conviction=4, weaknesses=["Lacks depth"], db_path=self.db)
        self.assertEqual(res_low["verdict"], "RETRY")

    # --- Multi-Round Debate Loop & Database Logging ---
    def test_run_council_multi_round_pass(self):
        # Using fallback / no-AI mock ctx
        ctx = {"llm": lambda prompt, **kwargs: None}
        res = product_council.run_council(101, SAMPLE_DRAFT, topic="Creator Tool", ctx=ctx, db_path=self.db, max_rounds=3)

        self.assertEqual(res["status"], "completed")
        self.assertIn(res["verdict"], ("PASS", "NEEDS YOU"))

        # Verify database debate log entries
        conn = sqlite3.connect(self.db)
        rows = conn.execute("SELECT round_num, stage, verdict FROM council_debates WHERE approval_id = 101").fetchall()
        conn.close()

        self.assertGreater(len(rows), 0)
        stages = [r[1] for r in rows]
        self.assertIn("critic", stages)
        self.assertIn("buyer_voice", stages)
        self.assertIn("judge", stages)
        self.assertIn("council_summary", stages)

    def test_run_council_needs_you(self):
        # Draft with low quality forcing NEEDS YOU after max_rounds
        weak_draft = "Short draft"
        ctx = {"llm": lambda prompt, **kwargs: None}
        res = product_council.run_council(102, weak_draft, topic="Short", ctx=ctx, db_path=self.db, max_rounds=2)

        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["verdict"], "NEEDS YOU")

        summary = product_council.get_council_summary(102, db_path=self.db)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["verdict"], "NEEDS YOU")

    # --- Daily Cap & Skip Logic ---
    def test_council_skip_when_daily_cap_reached(self):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE IF NOT EXISTS usage_log (day TEXT PRIMARY KEY, llm_calls INTEGER DEFAULT 0)")
        day = guardrails.today()
        conn.execute("INSERT INTO usage_log (day, llm_calls) VALUES (?, 1000)", (day,))
        conn.commit()
        conn.close()

        res = product_council.run_council(103, SAMPLE_DRAFT, topic="Creator Tool", db_path=self.db)
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["verdict"], "SKIPPED")

    # --- Telegram Card Format Text ---
    def test_telegram_card_formatting(self):
        # Create approval and debate log in db
        approval_id = guardrails.request_approval("public_draft", "Draft: Creator Tool", SAMPLE_DRAFT, db_path=self.db)

        product_council.log_debate(approval_id, 2, "council_summary", "PASS",
                                   "Clear value proposition over free templates",
                                   conviction=7, top_fix="Refined description and search tags", db_path=self.db)

        item = guardrails.get_approval(approval_id, db_path=self.db)
        card_text = telegram_inbox.draft_card(item, db_path=self.db)

        self.assertIn("Council: PASS after 2 round(s)", card_text)
        self.assertIn("buyer conviction 7/10", card_text)
        self.assertIn("top fix: Refined description and search tags", card_text)

    # --- Money Amount Check Rule Verification ---
    def test_no_money_amounts_in_code_and_comments(self):
        code_files = ["product_council.py", "workforce.py", "telegram_inbox.py", "tests/test_product_council.py"]
        # Exclude expected string formats in draft fixtures or prompt templates if any, but verify no real money amounts ($ amounts or currency symbols like $, €, £, ₹)
        money_pattern = re.compile(r"(\$\d+|\d+\s*\$|\b\d+\s*(?:dollars|USD|EUR|GBP|INR|Rupees)\b|[\$\€\£\₹]\d+)", re.IGNORECASE)

        for filename in code_files:
            if not os.path.exists(filename):
                continue
            with open(filename, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for idx, line in enumerate(lines, 1):
                # Ignore sample draft fixture line 'PRICE: 10 USD' if standard test fixture
                if "PRICE:" in line:
                    continue
                matches = money_pattern.findall(line)
                self.assertEqual(len(matches), 0, f"Prohibited money amount found in {filename}:{idx}: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
