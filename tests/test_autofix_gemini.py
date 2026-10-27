"""Gemini fallback, busy retries and budgets run offline with fake providers."""
import contextlib
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

import autofix
import run_failure

MODELS = ("google/gemini-3.8-flash", "google/gemini-3.5-flash-lite", "google/gemini-flash-latest")
BUSY = (1, autofix.SERVER_BUSY, True)
NO_RESULT = (0, autofix.NO_CHANGE, False)


class GeminiOrderTests(unittest.TestCase):
    def run_chain(self, results, checks=None):
        events = []
        checks = [False] * len(results) if checks is None else checks
        attempts, verified = iter(results), iter(checks)
        def attempt(model, **options):
            events.append(("attempt", model, options["timeout"]))
            return next(attempts)
        def check():
            events.append(("verify",))
            return next(verified)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}, clear=True), \
                    mock.patch.object(autofix, "provider_attempt", side_effect=attempt), \
                    mock.patch.object(autofix, "gemini_result", side_effect=check), \
                    mock.patch.object(autofix.time, "sleep", side_effect=lambda seconds: events.append(("wait", seconds))), \
                    contextlib.redirect_stdout(io.StringIO()) as log:
                code = autofix.gemini_run()
            return code, events, output.read_text(), log.getvalue()

    def test_supported_models_stay_in_the_discovered_order(self):
        self.assertEqual(autofix.GEMINI_MODELS, MODELS)

    def test_all_busy_attempts_retry_each_model_once_after_twenty_seconds_and_verification(self):
        code, events, output, _ = self.run_chain([BUSY] * 6)
        expected = []
        for model in MODELS:
            expected.extend([("attempt", model, 70), ("verify",), ("wait", 20),
                             ("attempt", model, 70), ("verify",)])
        self.assertEqual(events, expected)
        self.assertEqual(code, 1)
        self.assertEqual(output, "busy=true\nreason=server busy (503)\n")

    def test_zero_exit_without_a_remote_result_tries_all_models_once_and_fails(self):
        code, events, output, log = self.run_chain([NO_RESULT] * 3)
        self.assertEqual([row[1] for row in events if row[0] == "attempt"], list(MODELS))
        self.assertFalse(any(row[0] == "wait" for row in events))
        self.assertEqual(code, 1)
        self.assertEqual(output, "busy=false\nreason=no change made\n")
        self.assertEqual(log.count("Auto-fix provider: no change made."), 3)

    def test_only_a_503_busy_class_gets_the_immediate_same_model_retry(self):
        for reason, busy in ((autofix.QUOTA_DAILY, True), (autofix.RATE_MINUTE, True),
                             (autofix.PROVIDER_TIMEOUT, True), (autofix.REQUEST_LARGE, False)):
            with self.subTest(reason=reason):
                code, events, output, _ = self.run_chain([(1, reason, busy)] * 3)
                self.assertEqual([row[1] for row in events if row[0] == "attempt"], list(MODELS))
                self.assertFalse(any(row[0] == "wait" for row in events))
                self.assertEqual(code, 1)
                self.assertIn("busy=" + str(busy).lower(), output)

    def test_a_successful_retry_exit_without_a_result_still_moves_to_lite_then_alias(self):
        code, events, output, _ = self.run_chain([BUSY, NO_RESULT, NO_RESULT, NO_RESULT])
        self.assertEqual([row[1] for row in events if row[0] == "attempt"], [MODELS[0], *MODELS])
        self.assertEqual(events.count(("wait", 20)), 1)
        self.assertEqual(code, 1)
        self.assertIn("busy=false", output)

    def test_verified_result_stops_before_retry_or_model_fallback_even_after_nonzero_exit(self):
        for results, checks, count in (([BUSY], [True], 1), ([BUSY, BUSY], [False, True], 2),
                                      ([NO_RESULT, (1, autofix.NO_CHANGE, False)], [False, True], 2)):
            with self.subTest(checks=checks):
                code, events, output, log = self.run_chain(results, checks)
                self.assertEqual(code, 0)
                self.assertEqual(sum(row[0] == "attempt" for row in events), count)
                self.assertEqual(events.count(("wait", 20)), int(results[0] == BUSY and count == 2))
                self.assertEqual(output, "busy=false\nreason=no change made\n")
                self.assertIn("Auto-fix proposal verified.", log)

    def test_auth_failure_stops_the_same_key_chain_without_waiting(self):
        code, events, output, _ = self.run_chain([(1, autofix.AUTH_PROBLEM, False)])
        self.assertEqual(code, 1)
        self.assertEqual(events, [("attempt", MODELS[0], 70), ("verify",)])
        self.assertEqual(output, "busy=false\nreason=auth problem (401/403)\n")

    def test_unverified_check_preserves_model_order_and_the_single_503_retry(self):
        code, events, output, log = self.run_chain([BUSY, BUSY, NO_RESULT, NO_RESULT], [None, None, False, False])
        self.assertEqual(code, 1)
        self.assertEqual([row[1] for row in events if row[0] == "attempt"], [MODELS[0], *MODELS])
        self.assertEqual(events.count(("wait",20)), 1)
        self.assertEqual(output, "busy=false\nreason=no change made\n")
        self.assertEqual(log.count("No new auto-fix proposal was verified."), 2)
        self.assertNotIn("Auto-fix proposal verified.", log)

    def test_uncertain_zero_exit_never_claims_no_change_or_success(self):
        code, events, output, log = self.run_chain([NO_RESULT] * 3, [None] * 3)
        self.assertEqual(code, 1)
        self.assertEqual([row[1] for row in events if row[0] == "attempt"], list(MODELS))
        self.assertEqual(output, "busy=false\nreason=no change made\n")
        self.assertEqual(log.count("No new auto-fix proposal was verified."), 3)
        self.assertNotIn("Auto-fix provider: no change made.", log)
        self.assertNotIn("Auto-fix proposal verified.", log)

    def test_real_classifier_uses_the_same_key_for_all_models_and_never_prints_raw_messages(self):
        private = "fixture-dedicated-private-value"
        processes = [mock.Mock(returncode=1) for _ in range(6)]
        for process in processes:
            process.communicate.return_value = ("HTTP 503 server busy " + private + " https://example.invalid/private", None)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            env = {"GITHUB_OUTPUT": str(output), "GEMINI_API_KEY": private}
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(autofix.subprocess, "Popen", side_effect=processes) as popen, \
                    mock.patch.object(autofix, "gemini_result", return_value=False), \
                    mock.patch.object(autofix.time, "sleep") as sleep, \
                    contextlib.redirect_stdout(io.StringIO()) as log:
                self.assertEqual(autofix.main(["gemini-run"]), 1)
            calls = popen.call_args_list
            self.assertEqual([call.kwargs["env"]["MODEL"] for call in calls], [model for model in MODELS for _ in range(2)])
            for call in calls:
                self.assertEqual(call.kwargs["env"]["GEMINI_API_KEY"], private)
                self.assertTrue(call.kwargs["start_new_session"])
            self.assertEqual(sleep.call_args_list, [mock.call(20)] * 3)
            self.assertEqual(log.getvalue().count("Auto-fix provider: server busy (503)."), 6)
            for text in (private, "https://example.invalid/private", "HTTP 503"):
                self.assertNotIn(text, log.getvalue() + output.read_text())


class GeminiVerificationTests(unittest.TestCase):
    def test_checker_reads_only_exact_flags_uses_trusted_code_and_gets_no_provider_keys(self):
        for flag, expected in (("proposal=true\n", True), ("proposal=false\n", False),
                               ("proposal=true\nunsafe=extra\n", None), ("not a flag", None)):
            with self.subTest(flag=flag):
                def check(command, **options):
                    self.assertEqual(command, [autofix.sys.executable, str(Path(autofix.__file__).resolve()), "result-check"])
                    self.assertEqual(options["timeout"], 10)
                    self.assertTrue(options["capture_output"])
                    for key in ("GEMINI_API_KEY", "GEMINI_AUTOFIX_KEY", "NVIDIA_API_KEY", "GROQ_API_KEY"):
                        self.assertNotIn(key, options["env"])
                    self.assertEqual(options["env"]["GITHUB_TOKEN"], "fixture")
                    self.assertEqual(options["env"]["AUTOFIX_STATE"], "/fixture/state.json")
                    Path(options["env"]["GITHUB_OUTPUT"]).write_text(flag)
                    return mock.Mock(returncode=0, stdout="private-output", stderr="private-error")
                env = {key:"fixture" for key in ("GEMINI_API_KEY", "GEMINI_AUTOFIX_KEY", "NVIDIA_API_KEY", "GROQ_API_KEY", "GITHUB_TOKEN")}
                env["AUTOFIX_STATE"] = "/fixture/state.json"
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(autofix.subprocess, "run", side_effect=check), \
                        contextlib.redirect_stdout(io.StringIO()) as log:
                    self.assertIs(autofix.gemini_result(), expected)
                self.assertEqual(log.getvalue(), "")

    def test_checker_timeout_error_or_nonzero_exit_never_counts_as_success(self):
        for result in (subprocess.TimeoutExpired("fixture", 10, output="private-output"),
                       OSError("private-error"), mock.Mock(returncode=1), mock.Mock(returncode=0)):
            with self.subTest(result_type=type(result).__name__):
                option = {"side_effect":result} if isinstance(result, Exception) else {"return_value":result}
                with mock.patch.object(autofix.subprocess, "run", **option), \
                        contextlib.redirect_stdout(io.StringIO()) as log:
                    self.assertIsNone(autofix.gemini_result())
                self.assertEqual(log.getvalue(), "")


class AttemptBudgetTests(unittest.TestCase):
    def test_step_limits_and_all_six_attempts_fit_below_the_job_limit(self):
        workflow = (run_failure.ROOT / ".github/workflows/autofix.yml").read_text()
        fix = workflow.split("  fix:\n", 1)[1].split("\n  prepare:", 1)[0]
        job = int(re.search(r"^    timeout-minutes: (\d+)$", fix, re.M)[1])
        limits = [int(value) for value in re.findall(r"^        timeout-minutes: (\d+)$", fix, re.M)]
        self.assertLess(job, 25)
        self.assertEqual(sum(limits), 24)
        self.assertLessEqual(sum(limits), job)
        attempts = len(MODELS) * 2
        worst_gemini = attempts * (autofix.GEMINI_ATTEMPT_SECONDS + autofix.PROVIDER_TERM_SECONDS + autofix.PROVIDER_KILL_SECONDS + autofix.GEMINI_RESULT_SECONDS) + len(MODELS) * autofix.GEMINI_BUSY_WAIT
        self.assertEqual(worst_gemini, 576)
        self.assertLess(worst_gemini, 10 * 60)
        self.assertLess(autofix.PROVIDER_ATTEMPT_SECONDS + autofix.PROVIDER_TERM_SECONDS + autofix.PROVIDER_KILL_SECONDS, 2 * 60)
        gemini = fix.split("        id: gemini\n", 1)[1].split("      - name:", 1)[0]
        self.assertIn("timeout-minutes: 10", gemini)
        self.assertIn('" gemini-run', gemini)
        self.assertLess(fix.index('" gemini-run'), fix.index('MODEL: nim/'))
        self.assertIn("steps.verify_gemini.outputs.proposal != 'true'", fix.split("        id: nim\n", 1)[1])

    def test_attempt_cleanup_has_no_unbounded_wait_even_when_killed_descendants_hold_a_pipe(self):
        process = mock.Mock(returncode=None, pid=123)
        process.communicate.side_effect = [subprocess.TimeoutExpired("fixture", seconds) for seconds in (70,5,1)]
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(autofix.subprocess, "Popen", return_value=process), \
                mock.patch.object(autofix.os, "killpg") as kill, \
                contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertEqual(autofix.provider_attempt(MODELS[0], timeout=70), (1, "timeout", True))
        self.assertEqual(process.communicate.call_args_list, [mock.call(timeout=70),mock.call(timeout=5),mock.call(timeout=1)])
        self.assertEqual(kill.call_args_list,[mock.call(123,autofix.signal.SIGTERM),mock.call(123,autofix.signal.SIGKILL)])
        process.stdout.close.assert_called_once_with()
        self.assertEqual(log.getvalue(),"Auto-fix provider: timeout.\n")

    def test_default_provider_attempt_is_capped_even_on_a_normal_exit(self):
        process = mock.Mock(returncode=0)
        process.communicate.return_value = ("",None)
        with mock.patch.object(autofix.subprocess,"Popen",return_value=process), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(autofix.provider_attempt(),(0,"no change made",False))
        process.communicate.assert_called_once_with(timeout=90)
