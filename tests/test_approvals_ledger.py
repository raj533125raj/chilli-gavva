"""Tests for approvals_ledger.py."""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import approvals_ledger  # noqa: E402
import guardrails  # noqa: E402


class TestApprovalsLedger(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* env in setUp with mock.patch.dict
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "GITHUB_ACTIONS": "",
                "RUNNER_ENVIRONMENT": "",
                "GITHUB_RUN_ID": "",
                "LEDGER_URL": "",
                "LEDGER_READ_TOKEN": "",
            },
            clear=False,
        )
        self.env_patcher.start()
        # Ensure temporary directory for test database
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test_agent_data.db")

    def tearDown(self):
        self.env_patcher.stop()
        self.tmp.cleanup()

    def _create_draft(self, title="Test Draft"):
        return guardrails.request_approval("product", title, "payload", review="score 80/100", db_path=self.db)

    def test_fetch_decisions_success(self):
        fake_payload = {
            "decisions": [
                {"seq": 1, "draft_id": "10", "action": "approve", "reason": None, "ts": "2025-01-01T00:00:00Z"}
            ],
            "count": 1,
            "since": 0,
        }

        def fake_http_get(url, headers):
            self.assertIn("/decisions?since=0", url)
            self.assertEqual(headers.get("Authorization"), "Bearer test-token-123")
            import json
            return json.dumps(fake_payload)

        decisions = approvals_ledger.fetch_decisions("https://example.workers.dev", "test-token-123", since=0, http_get=fake_http_get)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["draft_id"], "10")
        self.assertEqual(decisions[0]["action"], "approve")

    def test_fetch_decisions_bad_json_no_crash(self):
        def fake_http_get(url, headers):
            return "<html><body>Not JSON</body></html>"

        decisions = approvals_ledger.fetch_decisions("https://example.workers.dev", "test-token-123", since=0, http_get=fake_http_get)
        self.assertEqual(decisions, [])
        self.assertEqual(approvals_ledger.LAST_FETCH_ERROR, "invalid json")

    def test_fetch_decisions_network_error_no_token_leak(self):
        secret_token = "SUPER_SECRET_TOKEN_XYZ_999"

        def fake_http_get(url, headers):
            raise Exception("connection refused")

        decisions = approvals_ledger.fetch_decisions("https://example.workers.dev", secret_token, since=0, http_get=fake_http_get)
        self.assertEqual(decisions, [])
        self.assertNotIn(secret_token, approvals_ledger.LAST_FETCH_ERROR or "")

    def test_apply_decisions_approve(self):
        draft_id = self._create_draft("Draft to Approve")
        decisions = [
            {"seq": 1, "draft_id": str(draft_id), "action": "approve", "reason": None, "ts": "2025-01-01T00:00:00Z"}
        ]

        counts = approvals_ledger.apply_decisions(decisions, db_path=self.db)
        self.assertEqual(counts["applied"], 1)
        self.assertEqual(counts["skipped"], 0)
        self.assertEqual(counts["unknown"], 0)

        # Verify draft is approved
        app = guardrails.get_approval(draft_id, db_path=self.db)
        self.assertEqual(app["status"], "approved")
        self.assertEqual(approvals_ledger.get_last_seq(db_path=self.db), 1)

    def test_apply_decisions_reject_with_reason(self):
        draft_id = self._create_draft("Draft to Reject")
        decisions = [
            {"seq": 2, "draft_id": str(draft_id), "action": "reject", "reason": "Too generic", "ts": "2025-01-01T00:00:00Z"}
        ]

        counts = approvals_ledger.apply_decisions(decisions, db_path=self.db)
        self.assertEqual(counts["applied"], 1)
        self.assertEqual(counts["skipped"], 0)

        app = guardrails.get_approval(draft_id, db_path=self.db)
        self.assertEqual(app["status"], "rejected")
        # Check reason stored
        conn = guardrails.connect(self.db)
        row = conn.execute("SELECT reason FROM approvals WHERE id = ?", (draft_id,)).fetchone()
        conn.close()
        self.assertEqual(row[0], "Too generic")
        self.assertEqual(approvals_ledger.get_last_seq(db_path=self.db), 2)

    def test_apply_decisions_already_decided_skipped(self):
        draft_id = self._create_draft("Already decided draft")
        # Pre-approve the draft
        guardrails.decide(draft_id, True, db_path=self.db)

        decisions = [
            {"seq": 3, "draft_id": str(draft_id), "action": "reject", "reason": "Weak niche", "ts": "2025-01-01T00:00:00Z"}
        ]

        counts = approvals_ledger.apply_decisions(decisions, db_path=self.db)
        self.assertEqual(counts["applied"], 0)
        self.assertEqual(counts["skipped"], 1)

        # Status must remain approved (owner's first decision won)
        app = guardrails.get_approval(draft_id, db_path=self.db)
        self.assertEqual(app["status"], "approved")

    def test_apply_decisions_duplicate_seq_ignored(self):
        draft_id_1 = self._create_draft("Draft 1")
        draft_id_2 = self._create_draft("Draft 2")

        decisions_1 = [
            {"seq": 5, "draft_id": str(draft_id_1), "action": "approve", "reason": None, "ts": "2025-01-01T00:00:00Z"}
        ]
        counts_1 = approvals_ledger.apply_decisions(decisions_1, db_path=self.db)
        self.assertEqual(counts_1["applied"], 1)

        # Re-applying same or lower seq decisions
        decisions_repeat = [
            {"seq": 4, "draft_id": str(draft_id_2), "action": "approve", "reason": None, "ts": "2025-01-01T00:00:00Z"},
            {"seq": 5, "draft_id": str(draft_id_1), "action": "reject", "reason": None, "ts": "2025-01-01T00:00:00Z"},
        ]
        counts_2 = approvals_ledger.apply_decisions(decisions_repeat, db_path=self.db)
        self.assertEqual(counts_2["applied"], 0)
        self.assertEqual(counts_2["skipped"], 2)

        # Draft 2 should still be pending because its seq (4) was <= last_seq (5)
        app_2 = guardrails.get_approval(draft_id_2, db_path=self.db)
        self.assertEqual(app_2["status"], "pending")

    def test_apply_decisions_unknown_draft(self):
        decisions = [
            {"seq": 10, "draft_id": "999999", "action": "approve", "reason": None, "ts": "2025-01-01T00:00:00Z"}
        ]
        counts = approvals_ledger.apply_decisions(decisions, db_path=self.db)
        self.assertEqual(counts["unknown"], 1)
        self.assertEqual(counts["applied"], 0)
        self.assertEqual(approvals_ledger.get_last_seq(db_path=self.db), 10)

    def test_run_missing_env_prints_not_configured(self):
        with mock.patch.dict(os.environ, {"LEDGER_URL": "", "LEDGER_READ_TOKEN": ""}):
            f = io.StringIO()
            with redirect_stdout(f):
                result = approvals_ledger.run(db_path=self.db)
            out = f.getvalue()
            self.assertIn("Ledger: not configured", out)
            self.assertFalse(result["configured"])

    def test_run_token_never_printed(self):
        secret_token = "MY_ULTRA_SECRET_TOKEN_456"
        with mock.patch.dict(os.environ, {"LEDGER_URL": "https://example.workers.dev", "LEDGER_READ_TOKEN": secret_token}):
            def failing_http_get(url, headers):
                raise Exception("Network connection failed")

            f = io.StringIO()
            with redirect_stdout(f):
                result = approvals_ledger.run(db_path=self.db, http_get=failing_http_get)
            out = f.getvalue()
            self.assertNotIn(secret_token, out)
            self.assertTrue(result["configured"])
            self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
