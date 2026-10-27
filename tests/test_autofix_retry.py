"""Patient retries use fake clocks, provider processes, GitHub, and Telegram."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.error
import zipfile

import autofix
import autofix_retry as retries
import run_failure

SUMMARY = "Step: Run tests first\nProblem: AssertionError: a check failed."
ENV = {"GITHUB_TOKEN": "fixture", "GITHUB_REPOSITORY": "example/robot", "GITHUB_RUN_ID": "10"}


class RetryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / "state.json"
        self.summary = Path(directory.name) / "failure.txt"
        self.summary.write_text(SUMMARY)
        run_failure.save_count(self.state, 2, True, 100)
        self.request = retries.start_request(SUMMARY, ENV)
        retries.save_retry(self.state, self.request)
        self.send, self.dispatch = mock.Mock(return_value=True), mock.Mock(return_value=True)

    def result(self, attempt=1, outcome="busy", run_id="20", now=200):
        record = dict(self.request, attempt=attempt, outcome=outcome, run_id=run_id)
        return retries.accept_result(self.state, record, 110, now, self.send)

    def finish(self, status, now):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_failure.finish(status, self.state, self.summary, self.send, ENV, self.dispatch, now)

    def test_first_busy_failure_is_silent_and_due_in_thirty_minutes(self):
        self.assertEqual(self.result(), "waiting")
        retry = retries.saved_retry(self.state)
        self.assertEqual(retry["attempt"], 1)
        self.assertEqual(retry["due_at"], 200 + 1800)
        self.send.assert_not_called()
        self.dispatch.assert_not_called()

    def test_due_retry_is_the_same_problem_and_does_not_consume_the_daily_allowance(self):
        self.result()
        self.finish("failure", 1999)
        self.dispatch.assert_not_called()
        self.finish("failure", 2000)
        self.dispatch.assert_called_once_with(SUMMARY, ENV, problem="10", attempt=2)
        self.assertEqual(run_failure.last_autofix_dispatch(self.state), 100)
        self.assertEqual(retries.saved_retry(self.state)["phase"], "running")
        self.finish("failure", 2100)
        self.dispatch.assert_called_once()

    def test_third_busy_failure_sends_exactly_one_message_and_never_dispatches_a_fourth(self):
        for attempt in (1, 2):
            self.assertEqual(self.result(attempt, run_id=str(20 + attempt), now=attempt * 2000), "waiting")
            self.send.assert_not_called()
            self.assertTrue(retries.dispatch_due(self.state, ENV, attempt * 2000 + 1800, self.dispatch))
        self.assertEqual(self.result(3, run_id="23", now=6000), "exhausted")
        self.send.assert_called_once_with("Auto-fix tried 3 times, AI servers were busy. Use the robot-fix-runbook.")
        self.result(3, run_id="23", now=6100)
        self.result(3, run_id="24", now=6200)
        self.finish("failure", 10000)
        self.send.assert_called_once()
        self.assertEqual(self.dispatch.call_count, 2)

    def test_passing_robot_cancels_waiting_retries_silently(self):
        self.result()
        self.summary.unlink()
        self.finish("success", 300)
        self.assertEqual(retries.saved_retry(self.state)["phase"], "cancelled")
        self.assertNotIn("due_at", retries.saved_retry(self.state))
        self.assertFalse(retries.dispatch_due(self.state, ENV, 10000, self.dispatch))
        self.result(now=10001)
        self.send.assert_not_called()
        self.dispatch.assert_not_called()
        self.assertEqual(run_failure.last_autofix_dispatch(self.state), 100)

    def test_passing_robot_cancels_an_inflight_result_before_it_can_queue_a_retry(self):
        self.summary.unlink()
        self.finish("success", 300)
        self.assertEqual(self.result(now=400), "cancelled")
        self.send.assert_not_called()
        self.assertEqual(retries.saved_retry(self.state)["phase"], "cancelled")

    def test_continued_step_failure_is_not_mistaken_for_a_healthy_robot(self):
        self.result()
        self.finish("success", 2000)
        self.dispatch.assert_called_once_with(SUMMARY, ENV, problem="10", attempt=2)

    def test_cloud_cycle_that_stands_down_for_the_mac_does_not_cancel_retries(self):
        self.result()
        state = self.state.parent / "robot-failure-state.json"
        state.write_text(self.state.read_text())
        env = {"RUNNER_TEMP": str(self.state.parent), "RUN_STATUS": "success", "ROBOT_RAN": "false"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(run_failure.main(["finish"]), 0)
        self.assertEqual(retries.saved_retry(state)["phase"], "waiting")
        self.assertEqual(run_failure.read_count(state), 2)

    def test_nonbusy_no_change_failure_gets_manual_help_immediately(self):
        self.assertEqual(self.result(outcome="manual"), "manual")
        self.send.assert_called_once_with(autofix.FAILED)
        self.assertNotIn("due_at", retries.saved_retry(self.state))
        self.result(outcome="manual")
        self.send.assert_called_once()

    def test_proposal_stops_the_retry_chain_without_a_failure_message(self):
        self.assertEqual(self.result(outcome="proposed"), "proposed")
        self.assertFalse(retries.dispatch_due(self.state, ENV, 10000, self.dispatch))
        self.result(outcome="busy", now=300)
        self.send.assert_not_called()
        self.assertEqual(retries.saved_retry(self.state)["phase"], "proposed")

    def test_duplicate_and_late_results_cannot_move_due_time_or_attempt_backwards(self):
        self.result()
        self.result(now=900)
        self.assertEqual(retries.saved_retry(self.state)["due_at"], 2000)
        retries.dispatch_due(self.state, ENV, 2000, self.dispatch)
        self.result(now=2200)
        self.assertEqual(retries.saved_retry(self.state)["attempt"], 2)
        self.assertEqual(retries.saved_retry(self.state)["phase"], "running")

    def test_failed_notification_can_be_delivered_on_next_cycle_without_a_duplicate(self):
        self.send.side_effect = RuntimeError("private-request")
        self.result(outcome="manual")
        self.assertFalse(retries.saved_retry(self.state)["notified"])
        self.send.side_effect = None
        self.finish("failure", 400)
        self.assertTrue(retries.saved_retry(self.state)["notified"])
        self.finish("failure", 500)
        self.assertEqual(self.send.call_count, 2)

    def test_no_notification_or_saved_summary_contains_untrusted_details(self):
        private = "fixture-" + "private-value"
        record = dict(self.request, summary="Step: private-request\nProblem: " + private,
                      outcome="manual", run_id="20")
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": private}):
            retries.accept_result(self.state, record, 110, 200, self.send)
        self.assertNotIn(private, json.dumps(self.send.call_args_list, default=str))
        self.assertNotIn(private, self.state.read_text())

    def test_dispatch_is_reserved_before_ambiguous_api_failure(self):
        self.result()
        def ambiguous(*args, **kwargs):
            self.assertEqual(retries.saved_retry(self.state)["attempt"], 2)
            self.assertEqual(retries.saved_retry(self.state)["phase"], "running")
            raise RuntimeError("private-request")
        with contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertTrue(retries.dispatch_due(self.state, ENV, 2000, ambiguous))
        self.assertNotIn("private-request", log.getvalue())
        self.assertFalse(retries.dispatch_due(self.state, ENV, 2100, self.dispatch))

    def test_cancellation_and_a_different_problem_do_not_dispatch_the_waiting_problem(self):
        self.result()
        self.finish("cancelled", 2000)
        self.dispatch.assert_not_called()
        self.summary.write_text("Step: Save state\nProblem: The step could not finish.")
        self.finish("failure", 2100)
        self.dispatch.assert_not_called()
        self.assertEqual(retries.saved_retry(self.state)["phase"], "cancelled")

    def test_malformed_retry_state_cannot_dispatch_or_exceed_three_attempts(self):
        for values in ({"attempt": 4}, {"attempt": True}, {"problem": "private-request"},
                       {"phase": "unknown"}, {"due_at": float("nan")}):
            with self.subTest(values=values):
                retry = dict(self.request, phase="waiting", due_at=2000)
                retry.update(values)
                retries.save_retry(self.state, retry)
                self.assertFalse(retries.dispatch_due(self.state, ENV, 3000, self.dispatch))
        self.dispatch.assert_not_called()


class ProviderTests(unittest.TestCase):
    def test_busy_signals_are_recognized_but_no_change_and_safe_robot_summaries_are_not(self):
        for output in ("Error: This model is currently experiencing high demand", "HTTP 429 rate limits",
                       '{"status":503}', "ProviderHeaderTimeoutError: Provider response headers timed out",
                       "tokens per minute (TPM): Limit 8000, Requested 8753", "APITimeoutError"):
            with self.subTest(output=output):
                self.assertTrue(autofix.busy_provider_output(output))
        for output in ("AI made no change", "test_rate_limit_backoff ... ok",
                       "Step: Run tests first\nProblem: TimeoutError: a request timed out."):
            with self.subTest(output=output):
                self.assertFalse(autofix.busy_provider_output(output))

    def test_provider_output_never_reaches_logs_or_status_files(self):
        private = "fixture-" + "private-value"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            process = mock.Mock(returncode=0)
            process.communicate.return_value = ("Error: high demand " + private, None)
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), \
                    mock.patch.object(autofix.subprocess, "Popen", return_value=process) as popen, \
                    contextlib.redirect_stdout(io.StringIO()) as log:
                self.assertEqual(autofix.provider_run(), 0)
            self.assertEqual(output.read_text(), "busy=true\n")
            self.assertEqual(log.getvalue(), "Auto-fix provider was busy.\n")
            self.assertNotIn(private, log.getvalue() + output.read_text())
            self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_hung_provider_is_killed_and_classified_as_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            process = mock.Mock(returncode=-15, pid=123)
            process.communicate.side_effect = [subprocess.TimeoutExpired("opencode", 300), ("", None)]
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), \
                    mock.patch.object(autofix.subprocess, "Popen", return_value=process), \
                    mock.patch.object(autofix.os, "killpg") as kill, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(autofix.provider_run(), 1)
            kill.assert_called_once_with(123, autofix.signal.SIGTERM)
            self.assertEqual(output.read_text(), "busy=true\n")

    def test_retry_dispatch_contains_only_safe_summary_and_validated_metadata(self):
        with mock.patch.object(autofix, "GitHub") as github:
            self.assertTrue(autofix.dispatch_fix("Step: Run tests first | Problem: private-request", ENV,
                                                problem="10", attempt=2))
        github.return_value.call.assert_called_once_with("POST", "/actions/workflows/autofix.yml/dispatches",
            {"ref": "main", "inputs": {"summary": "Step: Run tests first\nProblem: " + run_failure.UNKNOWN,
                                        "problem": "10", "attempt": "2"}})
        with mock.patch.object(autofix, "GitHub") as github, contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(autofix.dispatch_fix(SUMMARY, ENV, problem="10", attempt=4))
        github.assert_not_called()


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / "state.json"
        self.result = {"problem": "10", "summary": SUMMARY, "attempt": 1, "run_id": "20", "outcome": "busy"}
        self.api = mock.Mock(repository="example/robot")
        self.source = {"path": ".github/workflows/autofix.yml", "head_branch": "main",
                       "head_repository": {"full_name": "example/robot"}, "event": "workflow_dispatch",
                       "status": "completed", "created_at": "1970-01-01T00:01:50Z"}
        self.api.call.return_value = self.source
        self.api.items.return_value = [{"id": 1, "name": retries.ARTIFACT, "expired": False}]
        self.send = mock.Mock(return_value=True)

    def archive(self, result=None, filename=None):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr(filename or retries.FILENAME, json.dumps(self.result if result is None else result))
        return data.getvalue()

    def receive(self):
        return retries.receive_result(self.state, {"workflow_run": {"id": 20}}, ENV,
                                      now=200, api=self.api, send=self.send)

    def test_completed_trusted_run_queues_only_the_safe_artifact(self):
        self.api.download.return_value = self.archive()
        self.assertEqual(self.receive(), "waiting")
        self.api.call.assert_called_once_with("GET", "/actions/runs/20")
        self.api.download.assert_called_once_with("/actions/artifacts/1/zip")
        self.assertEqual(retries.saved_retry(self.state)["due_at"], 2000)
        self.send.assert_not_called()

    def test_record_contains_only_projected_summary_and_status_and_proposal_takes_priority(self):
        output = self.state.parent / "result.json"
        private = "fixture-" + "private-value"
        env = dict(ENV, AUTOFIX_PROBLEM="10", AUTOFIX_ATTEMPT="2",
                   FAILURE_SUMMARY="Step: Run tests first | Problem: " + private,
                   AUTOFIX_BUSY="true", AUTOFIX_PROPOSAL="true")
        retries.record_result(env, output)
        self.assertEqual(json.loads(output.read_text()),
            {"problem": "10", "attempt": 2, "run_id": "10", "outcome": "proposed",
             "summary": "Step: Run tests first\nProblem: " + run_failure.UNKNOWN})
        self.assertNotIn(private, output.read_text())

    def test_foreign_branch_workflow_or_repository_cannot_schedule_retries(self):
        for field, value in (("head_branch", "other"), ("path", ".github/workflows/other.yml"),
                             ("head_repository", {"full_name": "other/robot"}),
                             ("event", "pull_request"), ("status", "in_progress")):
            with self.subTest(field=field):
                self.api.call.return_value = dict(self.source, **{field: value})
                with self.assertRaises(autofix.ApiError):
                    self.receive()
        self.api.download.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_mismatched_run_and_unsafe_archive_name_are_rejected_without_extraction(self):
        for raw in (self.archive(dict(self.result, run_id="21")), self.archive(filename="../private.py")):
            self.api.download.return_value = raw
            with self.assertRaises(autofix.ApiError):
                self.receive()
        self.assertFalse(self.state.exists())
        self.send.assert_not_called()

    def test_success_after_source_run_started_suppresses_a_late_manual_result(self):
        run_failure.save_count(self.state, 0)
        retries.cancel(self.state, 150)
        self.api.download.return_value = self.archive(dict(self.result, outcome="manual"))
        self.assertEqual(self.receive(), "cancelled")
        self.send.assert_not_called()

    def test_expired_or_ambiguous_artifacts_do_not_produce_notifications(self):
        for artifacts in ([], [{"id": 1, "name": retries.ARTIFACT, "expired": True}],
                          [{"id": 1, "name": retries.ARTIFACT}, {"id": 2, "name": retries.ARTIFACT}]):
            self.api.items.return_value = artifacts
            with self.assertRaises(autofix.ApiError):
                self.receive()
        self.send.assert_not_called()

    def test_artifact_redirect_never_forwards_github_credentials(self):
        api = autofix.GitHub(ENV)
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://api.github.com/fixture", 302, "redirect",
                {"Location": "https://storage.example/archive?signature=fixture"}, None)
        response = mock.MagicMock()
        response.read.return_value = b"fixture"
        with mock.patch.object(autofix.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(autofix.urllib.request, "urlopen", return_value=response) as download:
            self.assertEqual(api.download("/actions/artifacts/1/zip"), b"fixture")
        self.assertEqual(opener.open.call_args.args[0].get_header("Authorization"), "Bearer fixture")
        self.assertIsNone(download.call_args.args[0].get_header("Authorization"))


class RetryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.agent = (run_failure.ROOT / ".github/workflows/agent.yml").read_text()
        self.workflow = (run_failure.ROOT / ".github/workflows/autofix.yml").read_text()

    def test_result_handler_shares_failure_cache_and_serializes_with_robot_cycles(self):
        self.assertIn("workflows: [Robot auto-fix checks, Robot auto-fix]", self.agent)
        self.assertIn("group: agent-cycle", self.agent)
        result = self.agent.split("\n  autofix_result:\n", 1)[1]
        self.assertIn("github.event.workflow_run.name == 'Robot auto-fix'", result)
        self.assertIn("ref: main", result)
        self.assertIn("persist-credentials: false", result)
        self.assertEqual(result.count("robot-failure-v1-${{ github.run_id }}-${{ github.run_attempt }}"), 2)
        self.assertIn("restore-keys: robot-failure-v1-", result)
        self.assertLess(result.index("actions/cache/restore@v4"), result.index("autofix_retry.py receive"))
        self.assertLess(result.index("autofix_retry.py receive"), result.index("actions/cache/save@v4"))
        self.assertNotIn("DB_KEY", result)
        self.assertNotIn("run_failure.py finish", result)
        self.assertIn("ROBOT_RAN: ${{ steps.gate.outputs.run }}", self.agent)

    def test_provider_attempts_use_captured_output_and_never_wait_for_the_retry_delay(self):
        self.assertEqual(self.workflow.count('run: python3 "$RUNNER_TEMP/autofix-control/autofix.py" provider-run'), 3)
        self.assertNotIn("sleep ", self.workflow)
        result = self.workflow.split("\n  result:\n", 1)[1]
        self.assertIn("robot-autofix-result", result)
        self.assertIn("retention-days: 3", result)
        self.assertNotIn("TELEGRAM", result)

    def test_busy_flag_requires_every_attempted_provider_to_be_busy(self):
        expression = next(line.split("busy: ", 1)[1] for line in self.workflow.splitlines()
                          if line.startswith("      busy: "))
        for values, expected in (({"gemini": "true", "nim": "true", "groq": "true"}, True),
                                 ({"gemini": "true", "nim": "false", "groq": "true"}, False),
                                 ({"gemini": "false", "nim": "false", "groq": "false"}, False),
                                 ({"gemini": "true", "nim": "skipped", "groq": "skipped"}, True)):
            with self.subTest(values=values):
                refs = {}
                for provider, value in values.items():
                    refs[f"steps.{provider}.outputs.busy"] = value
                    refs[f"steps.{provider}.outcome"] = "skipped" if value == "skipped" else "success"
                translated = expression.removeprefix("${{").removesuffix("}}").strip()
                translated = translated.replace("&&", " and ").replace("||", " or ")
                translated = re.sub(r"steps\.\w+\.(?:outputs\.\w+|outcome)",
                                    lambda match: repr(refs[match.group()]), translated)
                self.assertEqual(eval(translated, {"__builtins__": {}}, {}), expected)
