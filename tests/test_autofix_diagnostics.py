"""Dedicated-key routing and provider diagnostics are checked without real keys."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

import autofix
import run_failure
import secret_guard


class DedicatedKeyTests(unittest.TestCase):
    def test_gate_and_provider_use_the_same_dedicated_first_expression(self):
        workflow = (run_failure.ROOT / ".github/workflows/autofix.yml").read_text()
        references = re.findall(r"^          GEMINI_API_KEY: (.+)$", workflow, re.M)
        self.assertEqual(references, ["${{ secrets.GEMINI_AUTOFIX_KEY || secrets.GEMINI_API_KEY }}"] * 2)
        for dedicated, shared, expected in (("dedicated-fixture", "shared-fixture", "dedicated-fixture"),
                                            ("dedicated-fixture", "", "dedicated-fixture"),
                                            ("", "shared-fixture", "shared-fixture"), ("", "", ""),
                                            ("invalid-fixture", "shared-fixture", "invalid-fixture")):
            with self.subTest(dedicated=bool(dedicated), shared=bool(shared)):
                for reference in references:
                    expression = reference.removeprefix("${{").removesuffix("}}").strip()
                    expression = expression.replace("secrets.GEMINI_AUTOFIX_KEY", repr(dedicated))
                    expression = expression.replace("secrets.GEMINI_API_KEY", repr(shared)).replace("||", " or ")
                    self.assertEqual(eval(expression, {"__builtins__": {}}, {}), expected)

    def test_dedicated_key_is_not_routed_to_the_robot_cycle(self):
        workflows = run_failure.ROOT / ".github/workflows"
        for path in workflows.glob("*.yml"):
            if path.name != "autofix.yml":
                with self.subTest(workflow=path.name):
                    self.assertNotIn("GEMINI_AUTOFIX_KEY", path.read_text())
        router = (run_failure.ROOT / "llm_router.py").read_text()
        self.assertIn("GEMINI_AUTOFIX_KEY", router)
        robot = (workflows / "agent.yml").read_text()
        self.assertIn("GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}", robot)
        config = json.loads((run_failure.ROOT / "opencode.json").read_text())
        self.assertEqual(config["provider"]["google"]["options"]["apiKey"], "{env:GEMINI_API_KEY}")

    def test_secret_guard_protects_an_exact_dedicated_key_value(self):
        private = "fixture-dedicated-" + "private-value"
        with mock.patch.dict(os.environ, {"GEMINI_AUTOFIX_KEY": private}, clear=True):
            self.assertTrue(secret_guard.scan_text(private))


class DiagnosticTests(unittest.TestCase):
    CASES = (
        ('HTTP 429 RESOURCE_EXHAUSTED {"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}',
         "quota used up (429 daily)", True),
        ("429 quota exceeded: tokens per day", "quota used up (429 daily)", True),
        ("You exceeded your current quota. quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
         "quota used up (429 daily)", True),
        ('HTTP 429 RESOURCE_EXHAUSTED {"quotaId":"GenerateContentInputTokensPerModelPerMinute-FreeTier"}',
         "rate limit (429 per minute)", True),
        ("HTTP 429", "rate limit (429 per minute)", True),
        ("HTTP 429 quota exceeded", "rate limit (429 per minute)", True),
        ("HTTP 503 UNAVAILABLE", "server busy (503)", True),
        ("This model is currently experiencing high demand", "server busy (503)", True),
        ("ProviderHeaderTimeoutError: Provider response headers timed out after 90000ms", "timeout", True),
        ("APITimeoutError", "timeout", True),
        ("HTTP 413 payload too large", "request too large (413)", False),
        ("413 tokens per minute (TPM): Limit 8000, Requested 8753", "request too large (413)", True),
        ("HTTP 401 unauthorized", "auth problem (401/403)", False),
        ("HTTP 403 PERMISSION_DENIED", "auth problem (401/403)", False),
        ("API_KEY_INVALID: API key not valid", "auth problem (401/403)", False),
        ("AI made no change", "no change made", False),
        ("unknown failure details", "no change made", False),
    )

    def test_each_provider_failure_maps_to_the_exact_safe_reason_and_retry_flag(self):
        reasons = set()
        for raw, reason, busy in self.CASES:
            with self.subTest(reason=reason, raw=raw):
                self.assertEqual(autofix.classify_provider_output(raw), (reason, busy))
                self.assertEqual(autofix.busy_provider_output(raw), busy)
                reasons.add(reason)
        self.assertEqual(reasons, {"quota used up (429 daily)", "rate limit (429 per minute)",
                                   "server busy (503)", "timeout", "request too large (413)",
                                   "auth problem (401/403)", "no change made"})

    def test_auth_failure_is_not_retried_even_if_the_output_mentions_earlier_rate_limits(self):
        raw = "HTTP 429 per minute\nHTTP 401 API key invalid"
        self.assertEqual(autofix.classify_provider_output(raw), ("auth problem (401/403)", False))

    def test_safe_robot_summary_and_test_names_do_not_become_provider_failures(self):
        for raw in ("Step: Run tests first\nProblem: TimeoutError: a request timed out.",
                    "test_rate_limit_backoff ... ok", "private identifier 123429456"):
            with self.subTest(raw=raw):
                self.assertEqual(autofix.classify_provider_output(raw), ("no change made", False))

    def test_logs_and_outputs_never_echo_keys_identifiers_urls_or_raw_errors(self):
        private = "fixture-dedicated-" + "private-value"
        identifier, url = "private-project-981235", "https://example.invalid/private"
        for raw, reason, busy in self.CASES:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "outputs"
                process = mock.Mock(returncode=1)
                process.communicate.return_value = (raw + "\n" + private + " " + identifier + " " + url, None)
                env = {"GITHUB_OUTPUT": str(output), "GEMINI_API_KEY": private}
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(autofix.subprocess, "Popen", return_value=process), \
                        contextlib.redirect_stdout(io.StringIO()) as log:
                    self.assertEqual(autofix.provider_run(), 1)
                expected = "" if reason == "no change made" else "Auto-fix provider: " + reason + ".\n"
                self.assertEqual(log.getvalue(), expected)
                self.assertEqual(output.read_text(), f"busy={'true' if busy else 'false'}\nreason={reason}\n")
                for value in (private, identifier, url):
                    self.assertNotIn(value, log.getvalue() + output.read_text())

    def test_no_change_is_confirmed_by_remote_result_and_never_printed_for_a_proposal(self):
        for found in (False, True):
            with self.subTest(proposal=found), tempfile.TemporaryDirectory() as directory:
                state, output = Path(directory) / "state.json", Path(directory) / "outputs"
                state.write_text("{}")
                env = {"AUTOFIX_STATE": str(state), "GITHUB_OUTPUT": str(output),
                       "GITHUB_RUN_ID": "1", "PROVIDER_REASON": "no change made"}
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(autofix, "new_proposal_exists", return_value=found), \
                        contextlib.redirect_stdout(io.StringIO()) as log:
                    self.assertEqual(autofix.main(["result-check"]), 0)
                self.assertEqual(log.getvalue(), "Auto-fix proposal verified.\n" if found else
                                 "Auto-fix provider: no change made.\n")

    def test_reason_metadata_cannot_inject_text_into_the_result_log(self):
        with tempfile.TemporaryDirectory() as directory:
            state, output = Path(directory) / "state.json", Path(directory) / "outputs"
            state.write_text("{}")
            env = {"AUTOFIX_STATE": str(state), "GITHUB_OUTPUT": str(output),
                   "GITHUB_RUN_ID": "1", "PROVIDER_REASON": "private-request"}
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(autofix, "new_proposal_exists", return_value=False), \
                    contextlib.redirect_stdout(io.StringIO()) as log:
                self.assertEqual(autofix.main(["result-check"]), 0)
            self.assertEqual(log.getvalue(), "No new auto-fix proposal was verified.\n")

    def test_verifiers_receive_the_correct_provider_reason(self):
        workflow = (run_failure.ROOT / ".github/workflows/autofix.yml").read_text()
        for provider in ("gemini", "nim", "groq"):
            self.assertIn("PROVIDER_REASON: ${{ steps." + provider + ".outputs.reason }}", workflow)
