"""Unit tests for doctor.py — Doctor agent playbook matching, safe remedies, scrubbing,
loop & stuck task detection, limits, Telegram cards, and /doctor command.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import doctor
import guardrails


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = self.tmp.name
        doctor.connect(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.unlink(self.db_path)
            except OSError:
                pass

    def test_scrub_text_removes_secrets_and_personal_emails(self):
        env_dict = {
            "GROQ_API_KEY": "gsk_test12345678901234567890",
            "TELEGRAM_BOT_TOKEN": "12345678:AAtest1234567890123456789012345",
            "TELEGRAM_CHAT_ID": "987654321",
        }
        with mock.patch.dict(os.environ, env_dict):
            raw = (
                f"Error with key {env_dict['GROQ_API_KEY']} and bot token {env_dict['TELEGRAM_BOT_TOKEN']} "
                f"in chat {env_dict['TELEGRAM_CHAT_ID']}. Contact user@personaldomain.com or ghp_123456789012345678901234567890."
            )
            scrubbed = doctor.scrub_text(raw)
            self.assertNotIn("gsk_test12345678901234567890", scrubbed)
            self.assertNotIn("12345678:AAtest1234567890123456789012345", scrubbed)
            self.assertNotIn("user@personaldomain.com", scrubbed)
            self.assertIn("[REDACTED]", scrubbed)
            self.assertIn("name@example.com", scrubbed)

    def test_collect_facts_returns_scrubbed_data_and_last_actions(self):
        conn = doctor.connect(self.db_path)
        conn.execute(
            "INSERT INTO tasks (ts, kind, status, agent, error) VALUES ('2026-03-31T12:00:00', 'draft', 'failed', 'Draft Worker', 'Error 1')"
        )
        conn.commit()
        conn.close()

        err = ValueError("Invalid key gsk_secretkey1234567890123456")
        facts = doctor.collect_facts("Draft Worker", "draft", err, db_path=self.db_path)

        self.assertEqual(facts["agent"], "Draft Worker")
        self.assertEqual(facts["step"], "draft")
        self.assertEqual(facts["error_class"], "ValueError")
        self.assertNotIn("gsk_secretkey1234567890123456", facts["error_message"])
        self.assertEqual(len(facts["last_5_actions"]), 1)

    def test_playbook_matching_all_categories(self):
        cases = [
            ("rate_limit", "HTTPError", "429 Rate limit exceeded (RPM)"),
            ("quota", "HTTPError", "429 Daily quota exceeded for provider"),
            ("timeout", "TimeoutError", "Request timed out after 25s"),
            ("empty_ai", "AIUnavailable", "no answer from any AI model"),
            ("json_parse", "JSONDecodeError", "Expecting value in json response"),
            ("db_locked", "OperationalError", "database is locked"),
            ("missing_file", "FileNotFoundError", "No such file or directory"),
            ("telegram_api", "URLError", "Telegram API error: 401 (bot token not accepted)"),
            ("github_api", "ApiError", "GitHub API workflow dispatch failed"),
            ("code_error", "TypeError", "'NoneType' object is not subscriptable"),
        ]

        for expected_category, err_cls, err_msg in cases:
            facts = {
                "agent": "Test Agent",
                "step": "test_step",
                "error_class": err_cls,
                "error_message": err_msg,
            }
            cat, diag = doctor.match_playbook(facts)
            self.assertEqual(cat, expected_category, f"Failed for {err_cls}: {err_msg}")

    def test_remedy_rate_limit(self):
        conn = doctor.connect(self.db_path)
        conn.execute("INSERT INTO tasks (kind, status) VALUES ('draft', 'running')")
        task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        res = doctor.diagnose_and_remedy(
            "Draft Worker", "draft", "429 Too Many Requests (RPM)", task_id=task_id, db_path=self.db_path
        )
        self.assertEqual(res["category"], "rate_limit")
        self.assertEqual(res["status"], "resolved")

        conn = doctor.connect(self.db_path)
        task = conn.execute("SELECT status, error FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        self.assertEqual(task["status"], "queued")
        self.assertIn("Doctor: backoff retry", task["error"])

    def test_remedy_quota(self):
        conn = doctor.connect(self.db_path)
        conn.execute("INSERT INTO tasks (kind, status) VALUES ('draft', 'running')")
        task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        res = doctor.diagnose_and_remedy(
            "Draft Worker", "draft", "429 Daily quota exceeded", task_id=task_id, db_path=self.db_path
        )
        self.assertEqual(res["category"], "quota")
        self.assertEqual(res["status"], "skipped")

        conn = doctor.connect(self.db_path)
        task = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        self.assertEqual(task["status"], "deferred")

    def test_remedy_code_error_triggers_autofix_and_card(self):
        conn = doctor.connect(self.db_path)
        conn.execute("INSERT INTO tasks (kind, status) VALUES ('code_proposal', 'running')")
        task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        with mock.patch("autofix.dispatch_fix", return_value=True) as mock_dispatch, \
             mock.patch("notify._telegram_configured", return_value=(True, "tok", "chat")), \
             mock.patch("telegram_inbox.send_text", return_value=True) as mock_send:
            res = doctor.diagnose_and_remedy(
                "Coder Worker", "code_proposal", TypeError("NoneType has no attribute 'get'"),
                task_id=task_id, db_path=self.db_path
            )
            self.assertEqual(res["category"], "code_error")
            self.assertEqual(res["status"], "autofix_triggered")
            mock_dispatch.assert_called_once()
            mock_send.assert_called_once()

    def test_doctor_self_loop_prevention_limit(self):
        for i in range(3):
            doctor.record_case(
                {"agent": "Repeat Agent", "step": "repeat_step", "error_class": "TypeError",
                 "error_message": "err", "last_5_actions": []},
                "code_error", "diag", "remedy", "autofix_triggered", actions_count=i+1, db_path=self.db_path
            )

        res = doctor.diagnose_and_remedy(
            "Repeat Agent", "repeat_step", TypeError("err"), db_path=self.db_path
        )
        self.assertEqual(res["category"], "loop_limit")
        self.assertEqual(res["status"], "needs_owner")

    def test_repeat_loop_detection(self):
        conn = doctor.connect(self.db_path)
        for _ in range(3):
            conn.execute("INSERT INTO tasks (agent, kind, status, attempts, error) VALUES ('QA Checker', 'review', 'failed', 3, 'TimeoutError')")
        conn.commit()
        conn.close()

        is_loop = doctor.detect_repeat_loop("QA Checker", "review", "TimeoutError", db_path=self.db_path)
        self.assertTrue(is_loop)

    def test_stuck_task_detection(self):
        conn = doctor.connect(self.db_path)
        conn.execute("INSERT INTO tasks (agent, kind, status, attempts) VALUES ('Draft Worker', 'draft', 'running', 2)")
        conn.commit()
        conn.close()

        stuck = doctor.detect_stuck_tasks(db_path=self.db_path)
        self.assertEqual(len(stuck), 1)
        self.assertEqual(stuck[0]["agent"], "Draft Worker")
        self.assertEqual(stuck[0]["attempts"], 2)

    def test_format_last_cases_returns_formatted_log(self):
        doctor.record_case(
            {"agent": "Test Agent", "step": "test_step", "error_class": "ValueError",
             "error_message": "test msg", "last_5_actions": []},
            "rate_limit", "Rate limit hit", "Switched provider", "resolved", db_path=self.db_path
        )

        output = doctor.format_last_cases(limit=10, db_path=self.db_path)
        self.assertIn("Doctor Cases", output)
        self.assertIn("Test Agent", output)
        self.assertIn("Rate limit", output)

    def test_handle_callback_retry_skip_autofix(self):
        conn = doctor.connect(self.db_path)
        conn.execute("INSERT INTO tasks (kind, status) VALUES ('draft', 'failed')")
        task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        case_id = doctor.record_case(
            {"agent": "Test Agent", "step": "draft", "error_class": "TimeoutError",
             "error_message": "timeout", "last_5_actions": [], "task_id": task_id},
            "timeout", "Request timed out", "Marked needs owner", "needs_owner", db_path=self.db_path
        )

        # Retry callback
        with mock.patch("telegram_inbox.send_text"):
            reply = doctor.handle_callback("tok", "chat", f"doc:retry:{case_id}", db_path=self.db_path)
            self.assertIn("re-queued for immediate retry", reply)

        conn = doctor.connect(self.db_path)
        task = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        self.assertEqual(task["status"], "queued")

        # Skip callback
        with mock.patch("telegram_inbox.send_text"):
            reply = doctor.handle_callback("tok", "chat", f"doc:skip:{case_id}", db_path=self.db_path)
            self.assertIn("skipped for 24 hours", reply)

        conn = doctor.connect(self.db_path)
        task = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        self.assertEqual(task["status"], "deferred")

        # Autofix callback
        with mock.patch("autofix.dispatch_fix", return_value=True), \
             mock.patch("telegram_inbox.send_text"):
            reply = doctor.handle_callback("tok", "chat", f"doc:autofix:{case_id}", db_path=self.db_path)
            self.assertIn("Auto-fix workflow dispatched", reply)


if __name__ == "__main__":
    unittest.main()
