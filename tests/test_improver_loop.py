"""Tests for improver_loop.py and learned rules integration."""

import datetime
import os
import sqlite3
import tempfile
import unittest

import checker
import guardrails
import improver_loop
import telegram_inbox
import workforce


class TestImproverLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        self.conn = improver_loop.connect(self.db)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db):
            os.unlink(self.db)

    def test_problem_detection_and_proposals(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        # Seed approvals with checker notes and rejections
        guardrails.request_approval("public_draft", "Draft 1", "payload", db_path=self.db)
        guardrails.set_review(1, "score 50/100 | top: missing target audience; missing structure", db_path=self.db)

        guardrails.request_approval("public_draft", "Draft 2", "payload", db_path=self.db)
        guardrails.decide(2, False, db_path=self.db)
        guardrails.set_reason(2, "too generic", db_path=self.db)

        # Seed truth checks needs fix
        self.conn.execute("CREATE TABLE IF NOT EXISTS truth_checks (approval_id INTEGER PRIMARY KEY, verdict TEXT)")
        self.conn.execute("INSERT INTO truth_checks (approval_id, verdict) VALUES (3, 'needs_fix')")
        self.conn.commit()

        # Seed task retries and failures
        workforce.connect(self.db)
        self.conn.execute("INSERT INTO tasks (kind, status, attempts, error, ts) VALUES ('rewrite', 'done', 2, NULL, ?)", (now.isoformat(),))
        self.conn.execute("INSERT INTO tasks (kind, status, attempts, error, ts) VALUES ('draft', 'failed', 3, 'AIUnavailable error', ?)", (now.isoformat(),))
        self.conn.commit()

        candidates = improver_loop.analyze_database(db_path=self.db, days=30, now=now)
        self.assertTrue(len(candidates) >= 3)

        proposals = improver_loop.generate_proposals(db_path=self.db, days=30, limit=3, now=now)
        self.assertEqual(len(proposals), 3)

        # Check proposal format and text
        card_text, buttons = improver_loop.format_proposal_card(proposals[0])
        self.assertIn("Self-Improvement Proposal #", card_text)
        self.assertIn("Proposed Rule", card_text)
        self.assertEqual(len(buttons[0]), 2)
        self.assertIn("✅ Approve", buttons[0][0][0])
        self.assertIn("❌ Reject", buttons[0][1][0])

    def test_approve_reject_and_switch_off(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        # Create a proposal
        self.conn.execute(
            "INSERT INTO learned_rules (rule_text, category, problem_key, status, evidence, created_ts) VALUES "
            "('Test rule for writer', 'writer', 'test:problem1', 'proposed', '1 occurrence', ?)",
            (now.isoformat(),)
        )
        self.conn.commit()

        # Approve rule
        cb_reply = improver_loop.handle_callback("imp:a:1", db_path=self.db)
        self.assertIn("Approved rule #1", cb_reply)
        active_rules = improver_loop.get_active_rules("writer", db_path=self.db)
        self.assertIn("Test rule for writer", active_rules)

        # Check active rule block format
        block = improver_loop.get_active_rules_block("writer", db_path=self.db)
        self.assertIn("Learned Rules (follow these strictly):", block)
        self.assertIn("- Test rule for writer", block)

        # List rules via Telegram format
        text, buttons = improver_loop.format_rules_list(db_path=self.db)
        self.assertIn("Active Learned Rules", text)
        self.assertEqual(buttons[0][0][0], "Off #1")

        # Turn rule off
        off_reply = improver_loop.handle_callback("imp:off:1", db_path=self.db)
        self.assertIn("Switched off learned rule #1", off_reply)
        self.assertEqual(len(improver_loop.get_active_rules("writer", db_path=self.db)), 0)

        # Test rejection of a new proposal
        self.conn.execute(
            "INSERT INTO learned_rules (rule_text, category, problem_key, status, evidence, created_ts) VALUES "
            "('Rejected rule text', 'writer', 'test:problem2', 'proposed', '1 occurrence', ?)",
            (now.isoformat(),)
        )
        self.conn.commit()
        rej_reply = improver_loop.handle_callback("imp:r:2", db_path=self.db)
        self.assertIn("Rejected proposal #2", rej_reply)

        # Confirm rejected proposal is remembered and not proposed again
        proposals = improver_loop.generate_proposals(db_path=self.db, days=30, limit=3, now=now)
        self.assertNotIn("test:problem2", [p["problem_key"] for p in proposals])

    def test_rules_applied_to_prompts(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        self.conn.execute(
            "INSERT INTO learned_rules (rule_text, category, problem_key, status, evidence, created_ts) VALUES "
            "('Always specify buyer persona in BUYER section', 'writer', 'test:writer1', 'approved', '1 occurrence', ?)",
            (now.isoformat(),)
        )
        self.conn.execute(
            "INSERT INTO learned_rules (rule_text, category, problem_key, status, evidence, created_ts) VALUES "
            "('Ensure strict check for USD price', 'checker', 'test:checker1', 'approved', '1 occurrence', ?)",
            (now.isoformat(),)
        )
        self.conn.commit()

        # Test draft step incorporates writer rules
        captured_prompts = []
        def dummy_llm(prompt, size=None):
            captured_prompts.append(prompt)
            return "PRODUCT NAME: Test\nBUYER: Anyone\nPRICE: standard\nPLATFORM: Gumroad\nWHAT'S INSIDE: Features\nHOW TO USE: Steps\nLISTING TITLE: Title\nLISTING DESCRIPTION: Desc\nSEARCH TAGS: tag1, tag2, tag3, tag4, tag5"

        ctx = {"db_path": self.db, "llm": dummy_llm, "rng": None}
        task = {"payload": {"topic": "Planner Tool", "research": "Some notes"}}
        workforce.step_draft(task, ctx)

        self.assertTrue(len(captured_prompts) > 0)
        self.assertIn("Always specify buyer persona in BUYER section", captured_prompts[0])

        # Test checker incorporates checker rules
        check_prompts = []
        def dummy_checker_llm(prompt, size=None):
            check_prompts.append(prompt)
            return '{"score": 90, "verdict": "pass", "issues": []}'

        ctx_checker = {"llm": dummy_checker_llm}
        checker.check_draft("Draft text", topic="Planner Tool", ctx=ctx_checker, db_path=self.db)

        self.assertTrue(len(check_prompts) > 0)
        self.assertIn("Ensure strict check for USD price", check_prompts[0])

    def test_telegram_rules_command(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        self.conn.execute(
            "INSERT INTO learned_rules (rule_text, category, problem_key, status, evidence, created_ts) VALUES "
            "('Rule 1 text', 'writer', 'test:rules1', 'approved', '1 occurrence', ?)",
            (now.isoformat(),)
        )
        self.conn.commit()

        reply = telegram_inbox.handle_command("rules", [], db_path=self.db)
        self.assertIn("Rule 1 text", reply)


if __name__ == "__main__":
    unittest.main()
