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
            "https://github.com/example/robot/actions/runs/1\n"
            "Auto-fix: not started — no GitHub token")
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

    def test_run_step_keeps_safe_summary_and_prints_scrubbed_command_failure(self):
        raw = "private-request\nFileNotFoundError: private-path\nValueError: private-value\n"
        result = subprocess.CompletedProcess([], 1, stdout=raw)
        with mock.patch.object(failures.subprocess, "run", return_value=result), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(failures.run_step("save", ["unused"], self.summary), 1)
        expected = "Step: Save state\nProblem: setup problem: required file or tool\n"
        self.assertEqual(self.summary.read_text(), expected)
        self.assertEqual(output.getvalue(), "".join("Step output: " + line + "\n"
                                                  for line in raw.splitlines()) + expected)

    def test_failure_output_scrubs_environment_keys_query_values_and_workflow_commands(self):
        from urllib.parse import quote
        private = "fixture/credential+only"
        google = "AIza" + "X" * 35
        raw = (f"fatal: permission denied; request https://example.com/models?key={google}&alt=json\n"
               f"ValueError: {private} encoded={quote(private, safe='')} ?key=unknown-query-value\n"
               "::warning::untrusted command\n")
        result = subprocess.CompletedProcess([], 1, stdout=raw)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY_2": private}), \
                mock.patch.object(failures.subprocess, "run", return_value=result), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(failures.run_step("save", ["unused"], self.summary), 1)
        logged = output.getvalue()
        for value in (private, quote(private, safe=""), google, "unknown-query-value"):
            self.assertNotIn(value, logged)
        self.assertIn("fatal: permission denied", logged)
        self.assertIn("&alt=json", logged)
        self.assertIn("Step output: ::warning::untrusted command", logged)
        self.assertEqual(self.summary.read_text(),
                         "Step: Save state\nProblem: ValueError: a value could not be read.\n")

    def test_launch_error_prints_scrubbed_detail(self):
        with mock.patch.object(failures.subprocess, "run", side_effect=FileNotFoundError("fixture-command")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(failures.run_step("save", ["unused"], self.summary), 1)
        self.assertIn("Step output: FileNotFoundError: fixture-command", output.getvalue())

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
        for operation in ("read", "write"):
            line = f"Control inbox: KV {operation} failed (HTTP 403 - check API token has Workers KV Storage Edit)"
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


class AutofixHonestyTests(unittest.TestCase):
    setUp = RunFailureTests.setUp

    def attempt(self, dispatch, **kwargs):
        env = dict(self.env, GITHUB_TOKEN="fixture")
        env.update(kwargs.pop("env", {}))
        with contextlib.redirect_stdout(io.StringIO()) as log:
            failures.finish("failure", self.state, self.summary, self.send, env,
                            dispatch, now=kwargs.pop("now", 100000))
        return log.getvalue()

    def prepare(self, detail=failures.UNKNOWN, **state):
        failures.write_summary(self.summary, "tests", detail)
        self.state.write_text(json.dumps(dict(consecutive_failures=1, alerted=False, **state)))

    def assert_outcome(self, log, outcome):
        message = self.send.call_args.args[0]
        self.assertEqual(message.splitlines()[-1], outcome)
        self.assertEqual(sum(line.startswith("Auto-fix:") for line in message.splitlines()), 1)
        self.assertIn(outcome + "\n", log)

    def test_31_oct_cached_first_failure_dispatches_despite_product_workflow_method_name(self):
        methods = (
            "test_approved_to_real_encrypted_pack_and_names_only_command",
            "test_changed_files_before_encryption_confirmation_are_not_announced",
            "test_encryption_outage_never_notifies_or_loses_approval_and_resumes_without_ai",
            "test_existing_core_files_finish_without_regenerating_the_product",
            "test_one_missing_or_placeholder_image_blocks_an_otherwise_ready_pack",
            "test_pack_cli_failure_goes_to_doctor_and_retries_without_rebuild",
            "test_split_workflow_confirms_encryption_then_inbox_retries_send",
        )
        names = ["test_launch_pack.TestLaunchPack.test_build_full_launch_pack"]
        names += ["test_product_pipeline.DeliveryTests." + method for method in methods]
        self.prepare("Failed tests: " + ", ".join(names))
        self.env["GITHUB_RUN_ID"] = "41199080601"
        dispatch = mock.Mock(return_value=True)
        log = self.attempt(dispatch, now=1793448040)
        self.assertFalse(failures.workflow_failure(self.summary, failures.read_summary(self.summary)))
        dispatch.assert_called_once_with(failures.read_summary(self.summary),
                                         dict(self.env, GITHUB_TOKEN="fixture"),
                                         problem="41199080601", attempt=1)
        self.assertEqual(failures.read_state(self.state), (2, True))
        self.assert_outcome(log, "Auto-fix: started")

    def test_every_skip_reason_is_in_the_alert_and_log(self):
        for detail, state, env, reason in (
            ("setup problem: pdftoppm", {}, {}, "setup/workflow problem needs a human"),
            (failures.UNKNOWN, {"last_autofix_dispatch": 99999}, {}, "already tried in the last 24 h"),
            (failures.UNKNOWN, {}, {"GITHUB_TOKEN": ""}, "no GitHub token"),
        ):
            with self.subTest(reason=reason):
                self.prepare(detail, **state)
                dispatch = mock.Mock(return_value=True)
                self.assert_outcome(self.attempt(dispatch, env=env), failures.autofix_not_started(reason))
                dispatch.assert_not_called()

    def test_false_or_exception_from_github_never_claims_started(self):
        import autofix_retry
        for raises in (False, True):
            with self.subTest(raises=raises):
                self.prepare()
                dispatch = mock.Mock(return_value=False,
                                     side_effect=RuntimeError("private-request") if raises else None)
                log = self.attempt(dispatch)
                self.assert_outcome(log, "Auto-fix: not started — GitHub refused the request")
                self.assertNotIn("private-request", log)
                self.assertEqual(failures.last_autofix_dispatch(self.state), 100000)
                self.assertEqual(autofix_retry.saved_retry(self.state)["phase"], "dispatch_failed")

    def test_late_verified_result_can_recover_an_ambiguous_dispatch(self):
        import autofix_retry
        self.prepare()
        self.attempt(mock.Mock(side_effect=TimeoutError("private-request")))
        request = autofix_retry.saved_retry(self.state)
        result = dict(request, run_id="900", outcome="busy")
        self.assertEqual(autofix_retry.accept_result(self.state, result, 100001, 100002, self.send), "waiting")
        self.assertEqual(autofix_retry.saved_retry(self.state)["due_at"], 101802)

    def test_telegram_failure_does_not_block_or_duplicate_dispatch(self):
        self.prepare()
        self.send.return_value = False
        dispatch = mock.Mock(return_value=True)
        self.assert_outcome(self.attempt(dispatch), "Auto-fix: started")
        self.send.return_value = True
        self.assert_outcome(self.attempt(dispatch, now=100001),
                            "Auto-fix: not started — already tried in the last 24 h")
        dispatch.assert_called_once()
        self.assertEqual(failures.read_state(self.state), (3, True))

    def test_retry_reports_boolean_failure_and_success_honestly(self):
        import autofix_retry
        for started in (False, True):
            with self.subTest(started=started):
                self.prepare()
                env = dict(self.env, GITHUB_TOKEN="fixture")
                request = autofix_retry.start_request(failures.read_summary(self.summary), env)
                request.update(phase="waiting", due_at=100)
                autofix_retry.save_retry(self.state, request)
                with contextlib.redirect_stdout(io.StringIO()) as log:
                    self.assertTrue(autofix_retry.dispatch_due(self.state, env, 100,
                                                             mock.Mock(return_value=started)))
                expected = ("Auto-fix: started" if started else
                            "Auto-fix: not started — GitHub refused the request")
                self.assertEqual(log.getvalue(), expected + "\n")

    def test_due_retry_result_is_in_the_second_failure_alert(self):
        import autofix_retry
        for started in (False, True):
            with self.subTest(started=started):
                self.prepare(last_autofix_dispatch=99900)
                request = autofix_retry.start_request(failures.read_summary(self.summary), self.env)
                request.update(phase="waiting", due_at=100000)
                autofix_retry.save_retry(self.state, request)
                dispatch = mock.Mock(return_value=started)
                log = self.attempt(dispatch)
                expected = ("Auto-fix: started" if started else
                            "Auto-fix: not started — GitHub refused the request")
                self.assert_outcome(log, expected)
                self.assertEqual(log.count("Auto-fix:"), 1)
                self.assertEqual(failures.last_autofix_dispatch(self.state), 99900)
                dispatch.assert_called_once_with(failures.read_summary(self.summary),
                                                 dict(self.env, GITHUB_TOKEN="fixture"),
                                                 problem=self.env["GITHUB_RUN_ID"], attempt=2)

    def test_setup_diagnostics_are_fixed_safe_and_take_priority_over_test_failures(self):
        import autofix
        failed = "FAIL: test_first_failure_is_silent (test_run_failure.RunFailureTests.test_first_failure_is_silent)\n"
        for raw, tool in (
            ("FileNotFoundError: [Errno 2] No such file or directory: 'pdftoppm'", "pdftoppm"),
            ("FileNotFoundError: [Errno 2] No such file or directory: '/private/account/data'", "required file or tool"),
            ("/bin/bash: pdftocairo: command not found", "pdftocairo"),
            ("Product Builder: listing images skipped for #1: FileNotFoundError", "pdftoppm"),
            ("ModuleNotFoundError: No module named 'PIL'", "Pillow"),
            ("E: Unable to locate package poppler-utils", "apt-get"),
            ("apt-get failed with exit status 100: private-request", "apt-get"),
        ):
            with self.subTest(raw=raw):
                detail = failures.problem(failed + raw, env={})
                self.assertEqual(detail, "setup problem: " + tool)
                failures.write_summary(self.summary, "tests", detail)
                safe = failures.read_summary(self.summary)
                self.assertEqual(autofix.safe_summary(safe), safe)
                self.assertNotIn("private", safe)
                self.assertTrue(failures.workflow_failure(self.summary, safe))
        self.summary.write_text("Step: Run tests first\nProblem: setup problem: private-account")
        self.assertEqual(failures.read_summary(self.summary), "Step: Run tests first\nProblem: " + failures.UNKNOWN)


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
        self.assertEqual(workflow.count("key: robot-failure-v1-${{ github.run_id }}-${{ github.run_attempt }}"), 4)
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
