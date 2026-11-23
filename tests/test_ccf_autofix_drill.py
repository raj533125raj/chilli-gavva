"""CCF-2: one test class per auto-fix drill (tools/autofix_drill.py).

Each class shows the part of the auto-fix flow its drill found broken, on the robot's real code
(run_failure.py, autofix.py, autofix_retry.py, telegram_inbox.py, tools/run_tests_chunked.py), with
small fake inputs so it is fast. No network, nothing written into the repo.

Real drills (temp git copy, real workflow commands, recorded AI answer) run in RealDrillTests at the
end: three of them on every test run (one per route: fixed alone / needs the owner / quiet retry),
all ten with CHILLI_DRILL_ALL=1 (about two minutes).
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for folder in (ROOT, ROOT / "tools"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import autofix            # noqa: E402
import autofix_retry      # noqa: E402
import run_failure        # noqa: E402
import run_tests_chunked  # noqa: E402

try:
    import PIL  # noqa: F401
    HAS_PILLOW = True
except Exception:
    HAS_PILLOW = False

REPO = "sim/robot"
SHA = "ab12" * 10
STEP = run_failure.STEPS["tests"]
KNOWN_TEST = "test_cce3_bad_day.ClockJumpTests.test_jump_back_gives_one_line_then_quiet"
MONEY_TEST = "test_r22_markdown_currency.MarkdownCurrencyTests.test_public_markdown_has_no_currency_amounts"
COPY_TEST = ("test_autofix_control_copy.AutofixControlCopyTests."
             "test_every_load_time_import_of_the_copied_files_is_copied")
T0 = 1_800_000_000.0
PRINT_AND_FAIL = "import sys; print(sys.argv[1]); sys.exit(1)"


def frame(name, line=12, where="build"):
    return f'  File "{ROOT / name}", line {line}, in {where}'


class Flow(unittest.TestCase):
    """A failing robot step -> the saved summary -> the owner alert and the auto-fix request."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.summary = self.dir / "failure.txt"
        self.state = self.dir / "robot-failure-state.json"
        self.sent = []          # what the owner gets (after the Tenglish line is added)
        self.requests = []      # auto-fix workflow requests
        self.env = {"GITHUB_TOKEN": "fake-token-for-tests", "GITHUB_REPOSITORY": REPO,
                    "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "7002"}

    def send(self, text):
        self.sent.append(run_failure.owner_text(text))
        return True

    def dispatch(self, summary, env=None, problem="", attempt=1):
        self.requests.append(autofix.safe_summary(summary))
        return True

    def failing_step(self, lines, step="tests"):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_failure.run_step(step, [sys.executable, "-c", PRINT_AND_FAIL, "\n".join(lines)],
                                        self.summary)

    def finish(self, status="failure", now=T0):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_failure.finish(status, self.state, self.summary, send=self.send, env=self.env,
                                      dispatch=self.dispatch, now=now)

    def two_failed_runs(self, lines, step="tests"):
        self.assertEqual(self.failing_step(lines, step), 1)
        self.finish(now=T0)
        self.assertEqual(self.sent, [], "one failure must stay quiet")
        self.finish(now=T0 + 3600)
        self.assertEqual(len(self.sent), 1)
        return self.sent[0]

    def problem(self):
        return run_failure.read_summary(self.summary).splitlines()[1]


# ---- 1. ImportError ------------------------------------------------------------------------------
class ImportErrorDrillTests(Flow):
    LINES = ["ERROR: test_ccb3_bundle (unittest.loader._FailedTest.test_ccb3_bundle)",
             "ImportError: Failed to import test module: test_ccb3_bundle",
             "Traceback (most recent call last):",
             f'  File "{ROOT / "tests" / "test_ccb3_bundle.py"}", line 20, in <module>',
             "    import tg_bundle",
             frame("tg_bundle.py", 27, "<module>"),
             "    from notify import send_bundle_ready_line",
             "ImportError: cannot import name 'send_bundle_ready_line' from 'notify' (/somewhere/notify.py)",
             "FAILED (errors=1)",
             "ERROR: test_ccb3_bundle (unittest.loader._FailedTest.test_ccb3_bundle)"]

    def test_alert_names_the_test_file_that_could_not_load_and_the_file_to_look_at(self):
        alert = self.two_failed_runs(self.LINES)
        self.assertIn("Problem: Failed tests: test_ccb3_bundle (could not load)", alert)
        self.assertIn("Likely file: tg_bundle.py", alert)
        self.assertNotIn("/somewhere", alert)
        self.assertNotIn(str(ROOT), alert)

    def test_auto_fix_starts_and_the_ai_is_told_which_file_cannot_load(self):
        alert = self.two_failed_runs(self.LINES)
        self.assertIn(run_failure.AUTOFIX_STARTED, alert)
        self.assertEqual(self.requests, [f"Step: {STEP}\nProblem: Failed tests: test_ccb3_bundle (could not load)"])
        prompt = autofix.build_prompt(self.requests[0], "7003")
        self.assertIn("test_ccb3_bundle (could not load)", prompt)

    def test_a_made_up_test_file_name_never_reaches_the_summary(self):
        self.failing_step(["ERROR: test_not_a_file (unittest.loader._FailedTest.test_not_a_file)",
                           "ImportError: boom"])
        self.assertEqual(self.problem(), "Problem: " + run_failure.ERRORS["ImportError"])
        self.assertEqual(run_failure.safe_detail("Failed tests: test_not_a_file (could not load)"),
                         run_failure.UNKNOWN)
        self.assertEqual(run_failure.safe_detail("Failed tests: test_ccb3_bundle (could not load) extra"),
                         run_failure.UNKNOWN)


# ---- 2. A failing test ---------------------------------------------------------------------------
class FailingTestDrillTests(Flow):
    def lines(self, name=KNOWN_TEST):
        return [f"FAIL: {name.rsplit('.', 1)[1]} (tests.{name})", "AssertionError: 1 != 0",
                "FAILED (failures=1)"]

    def test_runner_style_name_with_tests_prefix_is_named(self):
        alert = self.two_failed_runs(self.lines())
        self.assertIn(f"Problem: Failed tests: {KNOWN_TEST}", alert)
        self.assertIn(f"Failing tests: {KNOWN_TEST}", alert)
        self.assertIn(run_failure.AUTOFIX_STARTED, alert)
        self.assertIn(KNOWN_TEST, self.requests[0])

    def test_plain_discover_style_name_still_works(self):
        method = KNOWN_TEST.rsplit(".", 1)[1]
        self.failing_step([f"FAIL: {method} ({KNOWN_TEST})", "AssertionError: x"])
        self.assertEqual(self.problem(), f"Problem: Failed tests: {KNOWN_TEST}")

    def test_unknown_test_name_falls_back_to_the_fixed_sentence(self):
        self.failing_step(["FAIL: test_nope (tests.test_nothing.Nope.test_nope)", "AssertionError: x"])
        self.assertEqual(self.problem(), "Problem: " + run_failure.ERRORS["AssertionError"])


# ---- 3. A timeout --------------------------------------------------------------------------------
HANGING_TEST = '''import time
import unittest


class Hang(unittest.TestCase):
    def test_never_ends(self):
        while True:
            time.sleep(0.2)
'''
QUICK_TEST = "import unittest\n\n\nclass Quick(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"


class TimeoutDrillTests(Flow):
    def test_runner_names_the_test_file_that_was_running_at_the_limit(self):
        """Real chunk process: the hanging file is the SECOND one of the chunk, and it is the one named."""
        with tempfile.TemporaryDirectory() as folder:
            tests = Path(folder) / "tests"
            tests.mkdir()
            (tests / "test_a_quick.py").write_text(QUICK_TEST, encoding="utf-8")
            (tests / "test_b_hangs.py").write_text(HANGING_TEST, encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=folder, PYTHONDONTWRITEBYTECODE="1")
            logged = []
            with mock.patch.object(run_tests_chunked, "STACK_WAIT", 5):
                old = os.getcwd()
                os.chdir(folder)
                try:
                    result = run_tests_chunked.run_chunk(["tests.test_a_quick", "tests.test_b_hangs"], timeout=3,
                                                         env=env, log_func=logged.append)
                finally:
                    os.chdir(old)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["returncode"], 124)
        self.assertEqual(result["parsed"]["failing_tests"],
                         ["ERROR: test_chunk_timeout (tests.test_b_hangs.TimeoutError)"])
        self.assertEqual(run_tests_chunked.stuck_test(result["output"]), ("test_b_hangs", "test_never_ends"))
        self.assertTrue(any("tests/test_b_hangs.py" in line for line in logged))

    def test_time_limit_without_a_stack_names_the_first_file_and_bytes_do_not_crash(self):
        def runner(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 5, output=b"..")
        result = run_tests_chunked.run_chunk(["tests.test_one", "tests.test_two"], timeout=5, runner=runner,
                                             log_func=lambda line: None)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["parsed"]["failing_tests"],
                         ["ERROR: test_chunk_timeout (tests.test_one.TimeoutError)"])

    def test_chunk_time_limit_can_come_from_the_environment(self):
        default = run_tests_chunked.DEFAULT_TIMEOUT_PER_CHUNK
        self.assertEqual(run_tests_chunked.timeout_from_env({"CHILLI_TEST_CHUNK_TIMEOUT": "6"}), 6)
        for bad in ("", "0", "-3", "abc", "99999", "6.5"):
            self.assertEqual(run_tests_chunked.timeout_from_env({"CHILLI_TEST_CHUNK_TIMEOUT": bad}), default)
        self.assertEqual(run_tests_chunked.timeout_from_env({}), default)

    def test_alert_says_which_test_file_timed_out(self):
        alert = self.two_failed_runs([
            "Fatal Python error: Aborted", "",
            "Current thread 0x0001 (most recent call first):",
            f'  File "{ROOT / "state_guard.py"}", line 61 in check_state_files',
            f'  File "{ROOT / "tests" / "test_cce3_bad_day.py"}", line 80 in test_missing_and_good_files',
            "ERROR: test_chunk_timeout (tests.test_cce3_bad_day.TimeoutError)"])
        self.assertIn("Problem: Failed tests: test_cce3_bad_day (timed out)", alert)
        self.assertIn("Likely file: state_guard.py", alert)
        self.assertIn(run_failure.AUTOFIX_STARTED, alert)
        self.assertIn("test_cce3_bad_day (timed out)", autofix.build_prompt(self.requests[0], "7003"))


# ---- 4. A syntax error ---------------------------------------------------------------------------
class SyntaxErrorDrillTests(Flow):
    def test_alert_points_at_the_broken_file_not_at_a_guess(self):
        method = KNOWN_TEST.rsplit(".", 1)[1]
        alert = self.two_failed_runs([
            f"ERROR: {method} (tests.{KNOWN_TEST})",
            "Traceback (most recent call last):",
            f'  File "{ROOT / "tests" / "test_cce3_bad_day.py"}", line 30, in setUp',
            "    import state_guard",
            f'  File "{ROOT / "state_guard.py"}", line 41',
            "    def damaged_json(path:",
            "                    ^",
            "SyntaxError: '(' was never closed",
            "FAILED (errors=1)"])
        self.assertIn(f"Failed tests: {KNOWN_TEST}", alert)
        self.assertIn("Likely file: state_guard.py", alert)
        self.assertEqual(alert.count("Likely file: "), 1)
        self.assertIn(run_failure.AUTOFIX_STARTED, alert)

    def test_only_real_repo_code_files_can_be_the_likely_file(self):
        good = run_failure.repo_file
        self.assertEqual(good(str(ROOT / "state_guard.py")), "state_guard.py")
        self.assertEqual(good("tools/run_tests_chunked.py"), "tools/run_tests_chunked.py")
        for bad in (str(ROOT / "tests" / "test_cce3_bad_day.py"), "/usr/lib/python3.11/unittest/case.py",
                    str(ROOT / "no_such_file.py"), "../outside.py", str(ROOT / ".github" / "x.py"),
                    str(ROOT / "exported_content" / "products" / "x.py"), "config.json", ""):
            self.assertIsNone(good(bad), bad)

    def test_a_plain_failed_assertion_has_no_traceback_file(self):
        self.assertIsNone(run_failure.likely_file_from_output(
            f'Traceback (most recent call last):\n  File "{ROOT / "tests" / "test_x.py"}", line 3, in test_a\n'
            "AssertionError: 1 != 2\n"))

    def test_a_forged_likely_file_line_is_ignored(self):
        self.summary.write_text(f"Step: {STEP}\nProblem: {run_failure.UNKNOWN}\nLikely file: ../../etc/passwd.py\n",
                                encoding="utf-8")
        self.assertIsNone(run_failure.read_likely_file(self.summary))


# ---- 5. A missing file in the auto-fix copy list -------------------------------------------------
class ControlCopyDrillTests(Flow):
    LINES = [f"FAIL: {COPY_TEST.rsplit('.', 1)[1]} (tests.{COPY_TEST})",
             "AssertionError: Lists differ: ['state_guard.py'] != []",
             "These files are imported by the files autofix.yml copies but are not copied themselves.",
             "FAILED (failures=1)"]

    def test_goes_to_the_owner_with_the_test_name_and_a_tenglish_line(self):
        alert = self.two_failed_runs(self.LINES)
        self.assertIn(f"Failed tests: {COPY_TEST}", alert)
        self.assertIn(run_failure.autofix_not_started("setup/workflow problem needs a human"), alert)
        self.assertTrue(alert.endswith(run_failure.TENGLISH_SETUP))
        self.assertEqual(self.requests, [], "the AI may not change workflow files, so it is not asked")

    def test_the_owner_fix_is_noticed(self):
        self.two_failed_runs(self.LINES)
        self.summary.unlink()
        self.finish("success", now=T0 + 7200)
        self.assertEqual(self.sent[-1], "fixed ✓")


# ---- 6. A bad workflow step ----------------------------------------------------------------------
class BadWorkflowStepDrillTests(Flow):
    def bad_step(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_failure.run_step("export", [sys.executable, str(self.dir / "content_exprot.py")],
                                        self.summary)

    def test_a_command_that_names_a_missing_file_is_a_setup_problem(self):
        self.assertNotEqual(self.bad_step(), 0)
        self.assertEqual(self.problem(), "Problem: setup problem: required file or tool")
        self.assertEqual(run_failure.setup_problem(
            "python3: can't open file '/home/runner/work/x/content_exprot.py': [Errno 2] No such file or directory"),
            "setup problem: required file or tool")
        self.assertEqual(run_failure.setup_problem(
            "/opt/hostedtoolcache/Python/3.11.9/x64/bin/python3: can't open file '/x/y.py': [Errno 2] No such file "
            "or directory"), "setup problem: required file or tool")
        self.assertEqual(run_failure.setup_problem("bash: line 1: pdftoppm: command not found"),
                         "setup problem: pdftoppm")
        self.assertEqual(run_failure.setup_problem(
            "/opt/Python.framework/Versions/3.11/Python: can't open file '/x/y.py': [Errno 2] No such file or "
            "directory"), "setup problem: required file or tool")

    def test_goes_to_the_owner_and_no_ai_is_started(self):
        self.bad_step()
        self.finish(now=T0)
        self.bad_step()
        self.finish(now=T0 + 3600)
        self.assertEqual(len(self.sent), 1)
        alert = self.sent[0]
        self.assertIn("Step: " + run_failure.STEPS["export"], alert)
        self.assertIn(run_failure.autofix_not_started("setup/workflow problem needs a human"), alert)
        self.assertTrue(alert.endswith(run_failure.TENGLISH_SETUP))
        self.assertEqual(self.requests, [])
        self.assertNotIn(str(self.dir), alert)


# ---- 7. A flaky test -----------------------------------------------------------------------------
class FlakyTestDrillTests(Flow):
    def test_one_failure_then_green_stays_quiet_and_starts_nothing(self):
        method = KNOWN_TEST.rsplit(".", 1)[1]
        self.failing_step([f"FAIL: {method} (tests.{KNOWN_TEST})", "AssertionError: x"])
        self.assertEqual(self.finish(now=T0), 1)
        self.summary.unlink()
        self.assertEqual(self.finish("success", now=T0 + 1800), 0)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.requests, [])
        self.assertEqual(run_failure.read_count(self.state), 0)

    def test_the_same_flake_a_day_later_is_again_only_one_failure(self):
        for start in (T0, T0 + 86400):
            self.failing_step(["FAIL: test_x (tests.test_nothing.A.test_x)", "AssertionError: x"])
            self.finish(now=start)
            self.summary.unlink()
            self.finish("success", now=start + 1800)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.requests, [])


# ---- 8. A money string in a public file ----------------------------------------------------------
class MoneyStringDrillTests(Flow):
    def test_the_guard_test_is_named_and_no_amount_reaches_the_owner_or_the_ai(self):
        amount = chr(36) + "1" "9"
        alert = self.two_failed_runs([
            f"FAIL: {MONEY_TEST.rsplit('.', 1)[1]} (tests.{MONEY_TEST})",
            f"AssertionError: README.md: Launch price for the first planner: {amount}.",
            "FAILED (failures=1)"])
        self.assertIn(f"Failed tests: {MONEY_TEST}", alert)
        self.assertIn(run_failure.AUTOFIX_STARTED, alert)
        prompt = autofix.build_prompt(self.requests[0], "7003")
        for text in (alert, prompt, self.summary.read_text(encoding="utf-8")):
            self.assertNotIn(amount, text)
            self.assertNotIn("Launch price", text)
        self.assertIn("Do not include money amounts", prompt)


# ---- 9. A broken builder -------------------------------------------------------------------------
class BrokenBuilderDrillTests(Flow):
    LINES = ["ERROR: setUpClass (tests.test_gm1_builders.HabitTests)",
             "Traceback (most recent call last):",
             f'  File "{ROOT / "tests" / "test_gm1_builders.py"}", line 60, in setUpClass',
             "    cls.files = builder_habit.build({}, cls.tmp)",
             frame("builder_habit.py", 438, "build"),
             frame("builder_habit.py", 375, "_build_pdf"),
             "TypeError: list indices must be integers or slices, not str",
             "Ran 117 tests in 3.9s", "FAILED (errors=1)"]

    def test_a_crashed_class_setup_is_named(self):
        alert = self.two_failed_runs(self.LINES)
        self.assertIn("Problem: Failed tests: test_gm1_builders (setup failed)", alert)
        self.assertIn("Likely file: builder_habit.py", alert)
        self.assertIn(run_failure.AUTOFIX_STARTED, alert)
        self.assertIn("test_gm1_builders (setup failed)", self.requests[0])

    def test_runner_lists_the_crashed_setup_and_never_prints_passed(self):
        parsed = run_tests_chunked.parse_unittest_output("\n".join(self.LINES))
        self.assertEqual(parsed["failing_tests"], ["ERROR: setUpClass (tests.test_gm1_builders.HabitTests)"])
        self.assertEqual(parsed["errors"], 1)
        text = run_tests_chunked.format_summary(117, 0, 1, 0, 4.0, {"chunk_01": 4.0}, [])
        self.assertIn("RESULT: FAILED", text)
        self.assertNotIn("RESULT: PASSED", text)
        self.assertIn("RESULT: FAILED", run_tests_chunked.format_summary(0, 0, 0, 0, 1.0, {}, [], failed=True))
        self.assertIn("RESULT: PASSED", run_tests_chunked.format_summary(5, 0, 0, 0, 1.0, {}, []))


# ---- 10. A plugin returning None -----------------------------------------------------------------
class PluginNoneDrillTests(Flow):
    def test_many_failing_tests_of_one_plugin_are_named_and_point_at_the_plugin(self):
        names = sorted(name for name in run_failure.known_tests() if name.startswith("test_ccb3_bundle.HelpTests."))
        self.assertGreaterEqual(len(names), 2)
        lines = []
        for name in names:
            lines += [f"FAIL: {name.rsplit('.', 1)[1]} (tests.{name})", "Traceback (most recent call last):",
                      f'  File "{ROOT / "tests" / "test_ccb3_bundle.py"}", line 90, in test',
                      "AssertionError: None is not an instance of <class 'dict'>"]
        alert = self.two_failed_runs(lines + ["FAILED (failures=%d)" % len(names)])
        self.assertIn("Failed tests: " + ", ".join(names[:10]), alert)
        self.assertIn("Failing tests: " + ", ".join(names[:3]), alert)
        self.assertIn("Likely file: tg_bundle.py", alert)
        self.assertIn(run_failure.AUTOFIX_STARTED, alert)


# ---- all drills: the owner gets the Merge button while main is still broken ------------------------
class FakeTelegram:
    def __init__(self):
        self.updates = []
        self.calls = []

    def __call__(self, token, method, params, *a, **k):
        self.calls.append((method, params))
        if method == "getMe":
            return {"ok": True, "result": {"id": 777, "is_bot": True}}
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": "", "allowed_updates": ["message", "callback_query"]}}
        if method == "getUpdates":
            if params.get("limit") == 1:
                return {"ok": True, "result": []}
            offset = params.get("offset") or 0
            return {"ok": True, "result": [u for u in self.updates if u["update_id"] >= offset]}
        return {"ok": True, "result": True}

    def messages(self):
        return [p for m, p in self.calls if m == "sendMessage"]


class FakeGitHub:
    """One open auto-fix pull request whose checks are green."""
    repository = REPO

    def __init__(self, env=None):
        pass

    pr = {"number": 5, "title": autofix.TITLE_PREFIX + STEP, "state": "open", "draft": False,
          "user": {"login": "github-actions[bot]"}, "base": {"ref": "main"}, "mergeable_state": "clean",
          "body": "Fixed.\n\nRobot auto-fix run: 7003\n",
          "head": {"ref": "autofix/run-7003", "sha": SHA, "repo": {"full_name": REPO}}}
    merged = []

    def items(self, path, key=None):
        if path.startswith("/pulls?"):
            return [dict(self.pr)] if self.pr["state"] == "open" else []
        if path.endswith("/files"):
            return [{"filename": "state_guard.py", "patch": "+BACK_SECONDS = 600"},
                    {"filename": "tests/test_autofix_clock.py", "patch": "+import unittest"}]
        if "/check-runs" in path:
            return [{"name": autofix.TEST_CHECK, "head_sha": SHA, "status": "completed", "conclusion": "success"}]
        return []

    def call(self, method, path, data=None):
        if method == "PUT" and path.endswith("/merge"):
            type(self).merged.append(data)
            return {"merged": True}
        return dict(self.pr)


def media_brain_patch():
    try:
        import media_brain  # noqa: F401
        return []
    except Exception:
        return [mock.patch.dict(sys.modules, {"media_brain": types.ModuleType("media_brain")})]


class RecoveryInboxTests(unittest.TestCase):
    """While main is broken only the recovery inbox runs. It must bring the result and take the tap."""

    def setUp(self):
        import telegram_inbox
        self.inbox = telegram_inbox
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = str(Path(temp.name) / "robot.db")
        self.tg = FakeTelegram()
        self.owner = int("1234" "5678")
        FakeGitHub.merged = []
        FakeGitHub.pr = dict(FakeGitHub.pr, state="open")
        patches = [mock.patch.object(telegram_inbox, "_call", side_effect=self.tg),
                   mock.patch("notify._telegram_configured", return_value=(True, "123:abc", str(self.owner))),
                   mock.patch.object(autofix, "GitHub", FakeGitHub),
                   mock.patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token-for-tests", "GITHUB_REPOSITORY": REPO,
                                                "CF_KV_NAMESPACE_ID": "", "CF_ACCOUNT_ID": "", "CF_API_TOKEN": "",
                                                "ALWAYS_ON_URL": "", "ALWAYS_ON_SECRET": ""})]
        for patch in patches + media_brain_patch():
            patch.start()
            self.addCleanup(patch.stop)

    def recovery_run(self, updates=()):
        self.tg.updates = list(updates)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            return self.inbox._run(self.db, recovery=True)

    def test_recovery_run_sends_auto_fix_ready_with_buttons_and_tenglish(self):
        result = self.recovery_run()
        self.assertEqual(result["alerts"], 1)
        ready = [m for m in self.tg.messages() if m["text"].startswith("Auto-fix ready: ")]
        self.assertEqual(len(ready), 1)
        self.assertIn(f"Auto-fix ready: {autofix.TITLE_PREFIX}{STEP}. Tests passed.", ready[0]["text"])
        self.assertIn("Merge tap cheste", ready[0]["text"])
        markup = json.dumps(ready[0]["reply_markup"]) if not isinstance(ready[0]["reply_markup"], str) \
            else ready[0]["reply_markup"]
        self.assertIn(f"af:merge:5:{SHA}", markup)
        self.assertIn(f"af:close:5:{SHA}", markup)

    def test_the_same_result_is_not_sent_twice(self):
        self.recovery_run()
        self.recovery_run()
        self.assertEqual(len([m for m in self.tg.messages() if m["text"].startswith("Auto-fix ready: ")]), 1)

    def test_recovery_run_takes_the_merge_tap(self):
        self.recovery_run()
        tap = {"update_id": 9, "callback_query": {"id": "q9", "data": f"af:merge:5:{SHA}",
                                                  "from": {"id": self.owner},
                                                  "message": {"message_id": 5, "chat": {"id": self.owner}}}}
        self.recovery_run([tap])
        self.assertEqual(FakeGitHub.merged, [{"merge_method": "squash", "sha": SHA}])
        self.assertIn("Auto-fix merged.", [m["text"] for m in self.tg.messages()])

    def test_recovery_run_still_sends_no_other_automatic_message(self):
        with mock.patch.object(self.inbox, "alert_new_drafts") as drafts, \
                mock.patch.object(self.inbox, "remind_waiting_drafts") as reminder:
            self.recovery_run()
        drafts.assert_not_called()
        reminder.assert_not_called()

    def test_a_github_problem_does_not_break_the_recovery_run(self):
        with mock.patch.object(autofix, "poll", side_effect=RuntimeError("boom")):
            result = self.recovery_run()
        self.assertEqual(result["alerts"], 0)
        self.assertEqual(result["configured"], True)


# ---- all drills: every auto-fix message has a Tenglish line ----------------------------------------
class OwnerWordingTests(unittest.TestCase):
    ALERT = "Chilli Gavva: run FAILED 2 times in a row.\nStep: Run tests first\nProblem: x\n"

    def test_alert_line_matches_what_happens_next(self):
        text = run_failure.owner_text
        self.assertTrue(text(self.ALERT + run_failure.AUTOFIX_STARTED).endswith(run_failure.TENGLISH_STARTED))
        self.assertTrue(text(self.ALERT + run_failure.autofix_not_started("setup/workflow problem needs a human"))
                        .endswith(run_failure.TENGLISH_SETUP))
        self.assertTrue(text(self.ALERT + run_failure.autofix_not_started("already tried in the last 24 h"))
                        .endswith(run_failure.TENGLISH_TRIED))
        for reason in ("no GitHub token", "GitHub refused the request"):
            self.assertTrue(text(self.ALERT + run_failure.autofix_not_started(reason))
                            .endswith(run_failure.TENGLISH_NOT_STARTED))

    def test_every_auto_fix_message_gets_one_line_once(self):
        messages = ["Auto-fix ready: Auto-fix: Run tests first. Tests passed.", autofix.FAILED,
                    autofix_retry.BUSY_FAILED,
                    autofix_retry.terminal_message({"phase": "manual", "reason": autofix.AUTH_PROBLEM}),
                    autofix_retry.terminal_message({"phase": "exhausted", "reason": autofix.SERVER_BUSY}),
                    "This auto-fix button is no longer current. Wait for the next test result.",
                    "This auto-fix request is no longer open."]
        for message in messages:
            with self.subTest(message=message):
                once = run_failure.owner_text(message)
                self.assertTrue(once.startswith(message + "\n"))
                self.assertEqual(len(once.splitlines()), 2)
                self.assertEqual(run_failure.owner_text(once), once)
                self.assertRegex(once.splitlines()[1], r"\b(cheyandi|chudandi|cheyakkarledu)\b")

    def test_short_and_unrelated_messages_are_left_alone(self):
        for message in ("fixed ✓", "Auto-fix merged.", "Auto-fix closed.", "Pack 3 ready ✓", "", None):
            self.assertEqual(run_failure.owner_text(message), message or "")

    def test_workflow_commands_send_with_the_tenglish_line(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(run_failure.notify, "send",
                                                                         return_value=True) as send:
            folder = Path(folder)
            run_failure.write_summary(folder / "failure.txt", "tests", f"Failed tests: {KNOWN_TEST}")
            env = {"RUNNER_TEMP": str(folder), "RUN_STATUS": "failure", "ROBOT_RAN": "true"}
            with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_failure.main(["finish"]), 0)
                self.assertEqual(run_failure.main(["finish"]), 0)
                self.assertEqual(autofix.main(["failed"]), 0)
        texts = [call.args[0] for call in send.call_args_list]
        self.assertEqual(len(texts), 2)
        self.assertIn(f"Failing tests: {KNOWN_TEST}", texts[0])
        self.assertTrue(texts[0].endswith(run_failure.TENGLISH_NOT_STARTED))   # no GitHub token in this test
        self.assertEqual(texts[1], run_failure.owner_text(autofix.FAILED))


# ---- the owner guide (docs/AUTOFIX-TENGLISH.md) cannot drift from the drill kit --------------------
class OwnerGuideDrillTableTests(unittest.TestCase):
    WHO = {"autofix": "**Robot**", "owner": "**Meeru**", "retry": "**Evaru vaddu**"}

    @classmethod
    def setUpClass(cls):
        import autofix_drill
        cls.scenarios = autofix_drill.SCENARIOS
        cls.text = (ROOT / "docs" / "AUTOFIX-TENGLISH.md").read_text(encoding="utf-8")

    def test_every_drill_has_a_row_with_the_right_fixer(self):
        self.assertEqual(len(self.scenarios), 10)
        for number, scenario in enumerate(self.scenarios, 1):
            with self.subTest(drill=scenario["key"]):
                rows = [line for line in self.text.splitlines()
                        if line.startswith(f"| {number} | `{scenario['key']}` |")]
                self.assertEqual(len(rows), 1)
                self.assertIn(self.WHO[scenario["expect"]], rows[0])
                for other in set(self.WHO.values()) - {self.WHO[scenario["expect"]]}:
                    self.assertNotIn(other, rows[0])

    def test_guide_quotes_the_real_tenglish_lines(self):
        lines = [run_failure.TENGLISH_STARTED, run_failure.TENGLISH_SETUP, run_failure.TENGLISH_TRIED,
                 run_failure.TENGLISH_NOT_STARTED]
        lines += [tenglish for start, tenglish in run_failure.TENGLISH_LINES
                  if start.startswith(("Auto-fix ready", "Auto-fix didn't", "Auto-fix tried",
                                       "This auto-fix button"))]
        self.assertEqual(len(lines), 8)
        for line in lines:
            with self.subTest(line=line):
                self.assertIn(line, self.text)

    def test_guide_no_longer_calls_the_test_name_gap_open(self):
        self.assertNotIn("known gap", self.text)
        self.assertIn("python3.11 tools/autofix_drill.py", self.text)
        self.assertIn("Robot tanantata merge cheyadu", self.text)
        self.assertIsNone(re.search(r"[$₹€£]\s?\d", self.text))


# ---- the ten real drills ---------------------------------------------------------------------------
def drills_can_run():
    if os.environ.get("AUTOFIX_DRILL"):
        return False, "already inside a drill"
    if sys.version_info < (3, 10):
        return False, "the robot needs Python 3.10+"
    if not HAS_PILLOW:
        return False, "Pillow is not installed"
    if not shutil.which("git") or not shutil.which("bash"):
        return False, "git and bash are needed"
    return True, ""


@unittest.skipUnless(*drills_can_run())
class RealDrillTests(unittest.TestCase):
    """tools/autofix_drill.py, drills side by side, exactly as the tool runs them."""
    EVERY_RUN = ("failing_test", "bad_workflow_step", "flaky_test")

    @classmethod
    def setUpClass(cls):
        import autofix_drill
        cls.drill = autofix_drill
        before = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT), stdout=subprocess.PIPE,
                                text=True, check=False).stdout
        cls.keys = (list(autofix_drill.SCENARIO_KEYS) if os.environ.get("CHILLI_DRILL_ALL") == "1"
                    else list(cls.EVERY_RUN))
        cls.rows = {row["key"]: row for row in autofix_drill.run_all(cls.keys)}
        after = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT), stdout=subprocess.PIPE,
                               text=True, check=False).stdout
        cls.repo_unchanged = before == after

    def check(self, key, **cells):
        if key not in self.rows:
            self.skipTest("this real drill runs with CHILLI_DRILL_ALL=1 (its fast test is above)")
        row = self.rows[key]
        self.assertEqual(row["verdict"], "PASS", self.drill.format_report([row]))
        for name, value in cells.items():
            self.assertEqual(row["cells"][name], value)
        return row

    def fixed_alone(self, key, problem):
        row = self.check(key, found="yes", route="auto-fix started", fix_branch="yes", tests_green="yes",
                         owner_msg="clear")
        self.assertIn(problem, row["messages"][0])
        self.assertTrue(row["messages"][0].endswith(run_failure.TENGLISH_STARTED))
        self.assertTrue(row["messages"][1].startswith("Auto-fix ready: "))
        self.assertEqual(row["messages"][2:], ["Auto-fix merged.", "fixed ✓"])
        self.assertTrue(any(str(data).startswith("af:merge:") for data in row["buttons"]))
        self.assertIn(problem, row["ai_prompt"])
        return row

    def needs_owner(self, key):
        row = self.check(key, found="yes", fix_branch="not needed", tests_green="yes (after the owner's fix)",
                         owner_msg="clear")
        self.assertEqual(len(row["messages"]), 2)
        self.assertTrue(row["messages"][0].endswith(run_failure.TENGLISH_SETUP))
        self.assertEqual(row["messages"][1], "fixed ✓")
        self.assertEqual(row["buttons"], [])
        self.assertEqual(row["ai_prompt"], "")
        return row

    def test_the_drills_ran_and_the_repo_is_untouched(self):
        self.assertEqual(sorted(self.rows), sorted(self.keys))
        self.assertEqual(len(self.drill.SCENARIO_KEYS), 10)
        self.assertTrue(self.repo_unchanged)

    def test_01_import_error(self):
        self.fixed_alone("import_error", "test_ccb3_bundle (could not load)")

    def test_02_failing_test(self):
        self.fixed_alone("failing_test", KNOWN_TEST)

    def test_03_timeout(self):
        self.fixed_alone("timeout", "test_cce3_bad_day (timed out)")

    def test_04_syntax_error(self):
        row = self.fixed_alone("syntax_error", "test_cce3_bad_day.")
        self.assertIn("Likely file: state_guard.py", row["messages"][0])

    def test_05_control_copy(self):
        row = self.needs_owner("control_copy")
        self.assertIn("test_autofix_control_copy.", row["messages"][0])

    def test_06_bad_workflow_step(self):
        row = self.needs_owner("bad_workflow_step")
        self.assertIn("Step: " + run_failure.STEPS["export"], row["messages"][0])

    def test_07_flaky_test(self):
        row = self.check("flaky_test", route="quiet retry, no auto-fix", owner_msg="clear")
        self.assertEqual(row["messages"], [])

    def test_08_money_string(self):
        row = self.fixed_alone("money_string", MONEY_TEST)
        for text in row["messages"] + [row["ai_prompt"]]:
            self.assertNotRegex(text, r"[$₹€£]\s?\d")

    def test_09_broken_builder(self):
        row = self.fixed_alone("broken_builder", "test_gm1_builders (setup failed)")
        self.assertIn("Likely file: builder_habit.py", row["messages"][0])

    def test_10_plugin_none(self):
        row = self.fixed_alone("plugin_none", "test_ccb3_bundle.HelpTests.")
        self.assertIn("Likely file: tg_bundle.py", row["messages"][0])


if __name__ == "__main__":
    unittest.main()
