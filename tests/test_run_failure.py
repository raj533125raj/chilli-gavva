"""Workflow failure handling uses temporary state and a mocked notification sender."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

import run_failure as failures


class RunFailureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.state = self.directory / "state.json"
        self.summary = self.directory / "failure.txt"
        self.send = mock.Mock(return_value=True)
        self.env = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "example/robot",
                    "GITHUB_RUN_ID": "1"}

    def finish(self, status="failure"):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            count = failures.finish(status, self.state, self.summary, self.send, self.env)
        return count, output.getvalue()

    def test_first_failure_is_silent(self):
        self.assertEqual(self.finish(), (1, "temporary problem, will retry\n"))
        self.send.assert_not_called()
        self.assertEqual(json.loads(self.state.read_text()), {"consecutive_failures": 1, "alerted": False})

    def test_second_failure_alerts_with_safe_reason_and_help(self):
        failures.write_summary(self.summary, "tests", failures.ERRORS["AssertionError"])
        self.finish()
        self.assertEqual(self.finish()[0], 2)
        self.send.assert_called_once_with(
            "Chilli Gavva: run FAILED 2 times in a row.\n"
            "Step: Run tests first\nProblem: AssertionError: a check failed.\n"
            "What to do: open the robot-fix-runbook, paste this message into Codex.\n"
            "robot-fix-runbook: https://github.com/example/robot/blob/main/docs/robot-fix-runbook.md\n"
            "https://github.com/example/robot/actions/runs/1")
        self.finish()
        self.send.assert_called_once()

    def test_success_resets_the_streak(self):
        self.finish()
        self.assertEqual(self.finish("success")[0], 0)
        self.assertEqual(failures.read_count(self.state), 0)
        self.assertEqual(self.finish()[0], 1)
        self.send.assert_not_called()

    def test_continued_step_failure_counts_even_if_job_status_is_success(self):
        failures.write_summary(self.summary, "build", failures.UNKNOWN)
        self.assertEqual(self.finish("success")[0], 1)

    def test_cancellation_does_not_reset_a_failure(self):
        self.finish()
        self.assertEqual(self.finish("cancelled")[0], 1)
        self.send.assert_not_called()

    def test_summary_has_no_secrets_or_request_details(self):
        private = "fixture-" + "private-value"
        env = {name: private + name for name in
               ("DB_KEY", "CF_API_TOKEN", "CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CONTROL_SIGNING_KEY")}
        raw = "\n".join(env.values()) + "\nValueError: " + " ".join(env.values())
        with mock.patch.dict(os.environ, env):
            detail = failures.problem(raw)
            failures.write_summary(self.summary, "inbox", detail)
            safe = failures.read_summary(self.summary)
        self.assertEqual(safe, "Step: Telegram inbox\nProblem: ValueError: a value could not be read.")
        self.assertTrue(all(value not in self.summary.read_text() for value in env.values()))

    def test_failed_test_names_are_verified_deduplicated_and_limited(self):
        names = sorted(failures.known_tests())[:15]
        output = "\n".join(f"FAIL: {name.rsplit('.', 1)[1]} ({name})" for name in names + names)
        output += "\nFAIL: test_private_request (private.account.test_private_request)"
        detail = failures.problem(output, env={})
        self.assertEqual(detail, "Failed tests: " + ", ".join(names[:10]))

    def test_sensitive_values_inside_known_test_names_are_excluded(self):
        name = "test_run_failure.RunFailureTests.test_first_failure_is_silent"
        output = f"FAIL: test_first_failure_is_silent ({name})"
        self.assertEqual(failures.problem(output, env={"DB_KEY": "first_failure"}), failures.UNKNOWN)

    def test_tampered_summary_is_not_forwarded(self):
        self.summary.write_text("Step: private-account\nProblem: private-request\n")
        self.assertEqual(failures.read_summary(self.summary),
                         "Step: Workflow setup\nProblem: The step could not finish.")

    def test_run_step_captures_last_error_without_logging_private_output(self):
        raw = "private-request\nFileNotFoundError: private-path\nValueError: private-value\n"
        result = subprocess.CompletedProcess([], 1, stdout=raw)
        with mock.patch.object(failures.subprocess, "run", return_value=result), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(failures.run_step("save", ["unused"], self.summary), 1)
        expected = "Step: Save state\nProblem: ValueError: a value could not be read.\n"
        self.assertEqual(self.summary.read_text(), expected)
        self.assertEqual(output.getvalue(), expected)

    def test_timeout_has_a_summary_before_the_command_finishes(self):
        def interrupted(*args, **kwargs):
            self.assertIn(failures.PENDING, self.summary.read_text())
            raise KeyboardInterrupt
        with mock.patch.object(failures.subprocess, "run", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                failures.run_step("cycle", ["unused"], self.summary)
        self.assertEqual(failures.read_summary(self.summary),
                         "Step: Run one agent cycle\nProblem: " + failures.PENDING)

    def test_successful_later_step_keeps_an_earlier_failure(self):
        failures.write_summary(self.summary, "build", failures.UNKNOWN)
        original = self.summary.read_text()
        with mock.patch.object(failures.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(failures.run_step("pack", ["unused"], self.summary), 0)
        self.assertEqual(self.summary.read_text(), original)

    def test_safe_inbox_diagnostic_is_still_visible(self):
        line = "Control inbox: KV read failed (HTTP 403 - check API token has Workers KV Storage Edit)"
        result = subprocess.CompletedProcess([], 0, stdout=line + "\nprivate-request\n")
        with mock.patch.object(failures.subprocess, "run", return_value=result), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(failures.run_step("inbox", ["unused"], self.summary), 0)
        self.assertEqual(output.getvalue(), line + "\nTelegram inbox: done\n")

    def test_notification_error_does_not_lose_the_saved_count(self):
        self.finish()
        self.send.side_effect = RuntimeError("private-request")
        self.assertEqual(self.finish()[0], 2)
        self.assertEqual(failures.read_count(self.state), 2)
        self.send.side_effect = None
        self.finish()
        self.assertTrue(failures.read_state(self.state)[1])
        self.assertEqual(self.send.call_count, 2)

    def test_successful_command_removes_its_pending_summary(self):
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": str(self.directory)}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(failures.main(["run", "export", "--", sys.executable, "-c", "pass"]), 0)
        self.assertFalse(self.summary.exists())

    def test_missing_or_invalid_counter_starts_at_zero(self):
        for data in ("invalid", "[]", '{"consecutive_failures": -1}', '{"consecutive_failures": true}'):
            self.state.write_text(data)
            self.assertEqual(failures.read_count(self.state), 0)


class FailureWorkflowTests(unittest.TestCase):
    def test_required_steps_are_wrapped_and_counter_is_saved_after_failures(self):
        workflow = (failures.ROOT / ".github/workflows/agent.yml").read_text()
        for step in ("tests", "cycle", "inbox", "export", "build", "pack", "save"):
            self.assertIn(f"python3 run_failure.py run {step} -- ", workflow)
        restore = workflow.index("uses: actions/cache/restore@")
        self.assertLess(restore, workflow.index("- name: Run tests first"))
        finish = workflow[workflow.index("- name: Update failure counter"):]
        self.assertIn("if: always()", finish)
        self.assertIn("RUN_STATUS: ${{ job.status }}", finish)
        self.assertIn("steps.failure_result.outcome == 'success'", finish)
        self.assertIn("uses: actions/cache/save@", finish)
        self.assertEqual(workflow.count("key: robot-failure-v1-${{ github.run_id }}-${{ github.run_attempt }}"), 2)
        self.assertIn("restore-keys: robot-failure-v1-", workflow)
        self.assertNotIn("notify.send", workflow)

    def test_multiline_wrappers_have_valid_shell_syntax(self):
        workflow = (failures.ROOT / ".github/workflows/agent.yml").read_text()
        for block in workflow.split("      - "):
            if "        run: |\n" not in block:
                continue
            script = block.split("        run: |\n", 1)[1]
            script = "\n".join(line for line in script.splitlines() if not line.startswith("      #"))
            script = re.sub(r"\$\{\{.*?\}\}", "", textwrap.dedent(script))
            subprocess.run(["bash", "-n"], input=script, text=True, check=True, capture_output=True)
