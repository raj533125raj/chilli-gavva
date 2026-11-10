"""Tests for BINNY-V5C: Quiet, correct failure alerts and fresh status."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock
import urllib.error

import control_inbox
import export_status
import run_failure


class V5CRunFailureAlertsTests(unittest.TestCase):
    """60+ unit tests covering run_failure, control_inbox, and export_status."""

    def setUp(self):
        self.env_patcher = mock.patch.dict(os.environ, {"CHILLI_TEST": "1"}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") and k != "GITHUB_ACTIONS":
                os.environ.pop(k, None)

        self.tmp = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.tmp.name)
        self.state_file = self.temp_dir / "robot-failure-state.json"
        self.summary_file = self.temp_dir / "failure.txt"
        self.send = mock.Mock(return_value=True)
        self.env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "chilli/robot",
            "GITHUB_RUN_ID": "12345",
        }

    def tearDown(self):
        self.env_patcher.stop()
        self.tmp.cleanup()

    # --- PART 1: likely_file_for_test and parsing helpers ---

    def test_01_likely_file_direct_mapping(self):
        self.assertEqual(run_failure.likely_file_for_test("test_run_failure.RunFailureTests.test_foo"), "run_failure.py")

    def test_02_likely_file_control_inbox(self):
        self.assertEqual(run_failure.likely_file_for_test("test_r26_control_inbox.ControlInboxTests.test_kv_down"), "control_inbox.py")

    def test_03_likely_file_export_status(self):
        self.assertEqual(run_failure.likely_file_for_test("test_public_status.PublicStatusTests.test_safe"), "export_status.py")

    def test_04_likely_file_prefix_map_v5c(self):
        self.assertEqual(run_failure.likely_file_for_test("test_v5c_alerts.AlertTests.test_one"), "run_failure.py")

    def test_05_likely_file_autofix(self):
        self.assertEqual(run_failure.likely_file_for_test("test_autofix.AutofixTests.test_dispatch"), "autofix.py")

    def test_06_likely_file_none_on_invalid_input(self):
        self.assertIsNone(run_failure.likely_file_for_test(""))
        self.assertIsNone(run_failure.likely_file_for_test(None))
        self.assertIsNone(run_failure.likely_file_for_test(123))

    def test_07_likely_file_unknown_module_handled_gracefully(self):
        res = run_failure.likely_file_for_test("test_nonexistent_xyz_999.SomeTest.test_bar")
        self.assertTrue(res is None or isinstance(res, str))

    def test_08_parse_failed_tests_single(self):
        summary = "Step: Run tests first\nProblem: Failed tests: test_run_failure.RunFailureTests.test_one"
        self.assertEqual(run_failure.parse_failed_tests(summary), ["test_run_failure.RunFailureTests.test_one"])

    def test_09_parse_failed_tests_multiple(self):
        summary = "Step: Run tests first\nProblem: Failed tests: test_a.TestA.test_1, test_b.TestB.test_2, test_c.TestC.test_3"
        self.assertEqual(run_failure.parse_failed_tests(summary), ["test_a.TestA.test_1", "test_b.TestB.test_2", "test_c.TestC.test_3"])

    def test_10_parse_failed_tests_empty_when_non_test_failure(self):
        summary = "Step: Open the database\nProblem: CryptError: the encrypted file could not be opened."
        self.assertEqual(run_failure.parse_failed_tests(summary), [])

    def test_11_extract_step_name_tests(self):
        summary = "Step: Run tests first\nProblem: AssertionError: a check failed."
        self.assertEqual(run_failure.extract_step_name(summary), "Run tests first")

    def test_12_extract_step_name_workflow_fallback(self):
        self.assertEqual(run_failure.extract_step_name("Invalid summary with no step"), "workflow")

    # --- PART 2: run_failure failure grouping, 6h throttling, count & naming ---

    def test_13_first_failure_is_silent(self):
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        count = run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env)
        self.assertEqual(count, 1)
        self.send.assert_not_called()

    def test_14_second_failure_sends_alert(self):
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env)
        count = run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env)
        self.assertEqual(count, 2)
        self.send.assert_called_once()
        msg = self.send.call_args.args[0]
        self.assertIn("Chilli Gavva: run FAILED 2 times in a row.", msg)
        self.assertIn("Step: Run tests first", msg)

    def test_15_third_failure_within_6h_is_throttled(self):
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        t0 = 1000000.0
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 10)
        self.assertEqual(self.send.call_count, 1)
        # Third failure 1 hour later (less than 6 hours)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 3600)
        self.assertEqual(self.send.call_count, 1)

    def test_16_repeated_failure_after_6h_sends_alert_with_updated_count(self):
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        t0 = 1000000.0
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 10)
        self.assertEqual(self.send.call_count, 1)

        # 4th run, 6.1 hours later (22000 seconds later)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 22000)
        self.assertEqual(self.send.call_count, 2)
        second_msg = self.send.call_args.args[0]
        self.assertIn("FAILED 3 times in a row.", second_msg)

    def test_17_alert_names_first_three_failing_tests(self):
        failing_tests = [
            "test_run_failure.RunFailureTests.test_first_failure_is_silent",
            "test_run_failure.RunFailureTests.test_missing_or_invalid_counter_starts_at_zero",
            "test_run_failure.RunFailureTests.test_safe_inbox_diagnostic_is_still_visible",
            "test_run_failure.RunFailureTests.test_successful_command_removes_its_pending_summary",
        ]
        summary_detail = "Failed tests: " + ", ".join(failing_tests)
        run_failure.write_summary(self.summary_file, "tests", summary_detail)
        t0 = 1000000.0
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 10)

        self.assertEqual(self.send.call_count, 1)
        msg = self.send.call_args.args[0]
        self.assertIn("Failing tests:", msg)
        failing_line = [line for line in msg.splitlines() if line.startswith("Failing tests:")][0]
        self.assertIn(failing_tests[0], failing_line)
        self.assertIn(failing_tests[1], failing_line)
        self.assertIn(failing_tests[2], failing_line)
        self.assertNotIn(failing_tests[3], failing_line)

    def test_18_alert_names_likely_file(self):
        failing_tests = ["test_r26_control_inbox.ControlInboxTests.test_kv_down"]
        summary_detail = "Failed tests: " + ", ".join(failing_tests)
        run_failure.write_summary(self.summary_file, "tests", summary_detail)
        t0 = 1000000.0
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 10)

        msg = self.send.call_args.args[0]
        self.assertIn("Likely file: control_inbox.py", msg)

    def test_19_different_failing_tests_form_different_group(self):
        t0 = 1000000.0
        t1 = ["test_run_failure.RunFailureTests.test_first_failure_is_silent"]
        run_failure.write_summary(self.summary_file, "tests", "Failed tests: " + ", ".join(t1))
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 10)
        self.assertEqual(self.send.call_count, 1)

        t2 = ["test_r26_control_inbox.ControlInboxTests.test_kv_down"]
        run_failure.write_summary(self.summary_file, "tests", "Failed tests: " + ", ".join(t2))
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 20)
        self.assertEqual(self.send.call_count, 2)
        msg2 = self.send.call_args.args[0]
        self.assertIn("Likely file: control_inbox.py", msg2)

    def test_20_different_step_forms_different_group(self):
        t0 = 1000000.0
        run_failure.write_summary(self.summary_file, "decrypt", "CryptError: the encrypted file could not be opened.")
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 10)
        self.assertEqual(self.send.call_count, 1)

        run_failure.write_summary(self.summary_file, "build", "RuntimeError: the step could not finish.")
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 20)
        self.assertEqual(self.send.call_count, 2)

    # --- PART 3: 'fixed ✓' notification on recovery ---

    def test_21_fixed_sent_once_when_next_run_passes_after_alert(self):
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        t0 = 1000000.0
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 10)
        self.assertEqual(self.send.call_count, 1)

        self.summary_file.unlink(missing_ok=True)
        count = run_failure.finish("success", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 20)
        self.assertEqual(count, 0)
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.send.call_args.args[0], "fixed ✓")

    def test_22_fixed_not_sent_again_on_subsequent_green_runs(self):
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        t0 = 1000000.0
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 10)
        self.assertEqual(self.send.call_count, 1)

        self.summary_file.unlink(missing_ok=True)
        run_failure.finish("success", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 20)
        self.assertEqual(self.send.call_count, 2)

        run_failure.finish("success", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 40)
        self.assertEqual(self.send.call_count, 2)

    def test_23_fixed_not_sent_if_single_failure_never_alerted(self):
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        t0 = 1000000.0
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        self.assertEqual(self.send.call_count, 0)

        self.summary_file.unlink(missing_ok=True)
        run_failure.finish("success", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 20)
        self.assertEqual(self.send.call_count, 0)

    def test_24_fixed_handles_send_exception_gracefully(self):
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        t0 = 1000000.0
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 10)

        self.summary_file.unlink(missing_ok=True)
        failing_send = mock.Mock(side_effect=RuntimeError("telegram down"))
        count = run_failure.finish("success", self.state_file, self.summary_file, send=failing_send, env=self.env, now=t0 + 20)
        self.assertEqual(count, 0)

    def test_25_last_successful_run_saved_in_state_on_success(self):
        self.summary_file.unlink(missing_ok=True)
        t0 = 1792656000.0
        run_failure.finish("success", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("last_successful_run"), "2026-10-22T08:00:00Z")

    def test_26_last_run_failed_step_cleared_on_success(self):
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        t0 = 1792656000.0
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 10)
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("last_run_failed_step"), "Run tests first")

        self.summary_file.unlink(missing_ok=True)
        run_failure.finish("success", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 20)
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertIsNone(data.get("last_run_failed_step"))

    def test_27_cancellation_preserves_streak_and_state(self):
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env)
        count = run_failure.finish("cancelled", self.state_file, self.summary_file, send=self.send, env=self.env)
        self.assertEqual(count, 1)

    def test_28_repeat_count_increments_for_same_group(self):
        t0 = 1000000.0
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env, now=t0 + 10)
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        groups = data.get("failure_groups", {})
        self.assertIn("Run tests first:", groups)
        self.assertEqual(groups["Run tests first:"]["count"], 2)

    def test_29_send_failure_leaves_alerted_false_so_it_retries_next_time(self):
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        failing_send = mock.Mock(return_value=False)
        t0 = 1000000.0
        run_failure.finish("failure", self.state_file, self.summary_file, send=failing_send, env=self.env, now=t0)
        run_failure.finish("failure", self.state_file, self.summary_file, send=failing_send, env=self.env, now=t0 + 10)
        self.assertEqual(run_failure.read_state(self.state_file), (2, False))

    def test_30_links_empty_when_env_invalid(self):
        self.assertEqual(run_failure.links({}), "")

    # --- PART 4: control_inbox KV read retry, quiet log, daily owner alert ---

    def test_31_control_inbox_retry_once_on_oserror_succeeds(self):
        env = {"CF_ACCOUNT_ID": "a", "CF_KV_NAMESPACE_ID": "n", "CF_API_TOKEN": "t", "CONTROL_SIGNING_KEY": "k"}
        calls = 0

        def flaky_http(url, token, method="GET", data=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("connection reset by peer")
            return '{"result": []}'

        with mock.patch("builtins.print") as output:
            res = control_inbox.fetch(env, http_request=flaky_http)
        self.assertEqual(res, [])
        self.assertEqual(calls, 2)
        output.assert_not_called()

    def test_32_control_inbox_retry_once_on_oserror_fails_quietly(self):
        env = {"CF_ACCOUNT_ID": "a", "CF_KV_NAMESPACE_ID": "n", "CF_API_TOKEN": "t", "CONTROL_SIGNING_KEY": "k"}
        calls = 0

        def always_fail(url, token, method="GET", data=None):
            nonlocal calls
            calls += 1
            raise OSError("network is unreachable")

        with mock.patch("builtins.print") as output:
            res = control_inbox.fetch(env, http_request=always_fail)
        self.assertEqual(res, [])
        self.assertEqual(calls, 2)
        output.assert_called_once_with("Control inbox: KV read failed (OSError)")

    def test_33_control_inbox_oserror_sends_owner_alert(self):
        env = {"CF_ACCOUNT_ID": "a", "CF_KV_NAMESPACE_ID": "n", "CF_API_TOKEN": "t", "CONTROL_SIGNING_KEY": "k"}
        sent_messages = []

        def always_fail(url, token, method="GET", data=None):
            raise OSError("socket down")

        control_inbox._last_oserror_alert_time = 0
        with mock.patch("builtins.print"):
            control_inbox.fetch(env, http_request=always_fail, send=lambda m: sent_messages.append(m))
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("Control inbox: KV read failed (OSError)", sent_messages[0])
        self.assertIn("CF_KV_NAMESPACE_ID secret check cheyandi", sent_messages[0])

    def test_34_control_inbox_oserror_alert_throttled_to_once_per_day(self):
        env = {"CF_ACCOUNT_ID": "a", "CF_KV_NAMESPACE_ID": "n", "CF_API_TOKEN": "t", "CONTROL_SIGNING_KEY": "k"}
        sent_messages = []
        t0 = 1000000.0

        def always_fail(url, token, method="GET", data=None):
            raise OSError("socket down")

        control_inbox._last_oserror_alert_time = 0
        with mock.patch("builtins.print"):
            control_inbox.fetch(env, http_request=always_fail, now=t0, send=lambda m: sent_messages.append(m))
            control_inbox.fetch(env, http_request=always_fail, now=t0 + 7200, send=lambda m: sent_messages.append(m))
        self.assertEqual(len(sent_messages), 1)

    def test_35_control_inbox_oserror_alert_allowed_after_24_hours(self):
        env = {"CF_ACCOUNT_ID": "a", "CF_KV_NAMESPACE_ID": "n", "CF_API_TOKEN": "t", "CONTROL_SIGNING_KEY": "k"}
        sent_messages = []
        t0 = 1000000.0

        def always_fail(url, token, method="GET", data=None):
            raise OSError("socket down")

        control_inbox._last_oserror_alert_time = 0
        with mock.patch("builtins.print"):
            control_inbox.fetch(env, http_request=always_fail, now=t0, send=lambda m: sent_messages.append(m))
            control_inbox.fetch(env, http_request=always_fail, now=t0 + 90000, send=lambda m: sent_messages.append(m))
        self.assertEqual(len(sent_messages), 2)

    def test_36_control_inbox_alert_kv_oserror_persists_in_db(self):
        db_path = str(self.temp_dir / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

        sent = []
        t0 = 1000000.0
        control_inbox._last_oserror_alert_time = 0
        res = control_inbox.alert_kv_oserror(db_path, now=t0, send=lambda m: sent.append(m))
        self.assertTrue(res)
        self.assertEqual(len(sent), 1)

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT value FROM telegram_state WHERE key = 'kv_oserror_alert_ts'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], str(t0))

    def test_37_control_inbox_alert_kv_oserror_honors_persisted_ts(self):
        db_path = str(self.temp_dir / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO telegram_state VALUES ('kv_oserror_alert_ts', '1000000.0')")
        conn.commit()
        conn.close()

        sent = []
        control_inbox._last_oserror_alert_time = 0
        res = control_inbox.alert_kv_oserror(db_path, now=1003600.0, send=lambda m: sent.append(m))
        self.assertFalse(res)
        self.assertEqual(len(sent), 0)

    def test_38_control_inbox_run_passes_db_and_send_to_fetch(self):
        env = {"CF_ACCOUNT_ID": "a", "CF_KV_NAMESPACE_ID": "n", "CF_API_TOKEN": "t", "CONTROL_SIGNING_KEY": "k"}
        sent = []
        control_inbox._last_oserror_alert_time = 0

        def always_fail(url, token, method="GET", data=None):
            raise OSError("down")

        with mock.patch("builtins.print"):
            result = control_inbox.run(
                db_path=str(self.temp_dir / "test.db"),
                env=env,
                http_request=always_fail,
                now=1000000.0,
                send=lambda m: sent.append(m)
            )
        self.assertEqual(result, {"applied": 0, "refused": 0})
        self.assertEqual(len(sent), 1)

    def test_39_control_inbox_non_oserror_does_not_send_alert(self):
        env = {"CF_ACCOUNT_ID": "a", "CF_KV_NAMESPACE_ID": "n", "CF_API_TOKEN": "t", "CONTROL_SIGNING_KEY": "k"}
        sent = []

        def value_error_call(url, token, method="GET", data=None):
            raise ValueError("bad json")

        with mock.patch("builtins.print"):
            control_inbox.fetch(env, http_request=value_error_call, send=lambda m: sent.append(m))
        self.assertEqual(len(sent), 0)

    def test_40_control_inbox_alert_content_tenglish(self):
        sent = []
        control_inbox._last_oserror_alert_time = 0
        control_inbox.alert_kv_oserror(now=1000000.0, send=lambda m: sent.append(m))
        self.assertEqual(len(sent), 1)
        self.assertIn("CF_KV_NAMESPACE_ID secret check cheyandi", sent[0])
        self.assertIn("Control inbox: KV read failed (OSError)", sent[0])

    # --- PART 5: export_status freshness fields ---

    def test_41_get_freshness_fields_default_none(self):
        freshness = export_status.get_freshness_fields()
        self.assertIn("last_successful_run", freshness)
        self.assertIn("last_run_failed_step", freshness)

    def test_42_get_freshness_fields_reads_from_state_file(self):
        state_file = self.temp_dir / "robot-failure-state.json"
        state_file.write_text(json.dumps({
            "last_successful_run": "2026-11-10T00:00:00Z",
            "last_run_failed_step": "Run tests first"
        }))
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": str(self.temp_dir)}):
            freshness = export_status.get_freshness_fields()
        self.assertEqual(freshness["last_successful_run"], "2026-11-10T00:00:00Z")
        self.assertEqual(freshness["last_run_failed_step"], "Run tests first")

    def test_43_get_freshness_fields_handles_corrupt_state_file(self):
        state_file = self.temp_dir / "robot-failure-state.json"
        state_file.write_text("corrupted json content")
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": str(self.temp_dir)}):
            freshness = export_status.get_freshness_fields()
        self.assertIsNone(freshness["last_successful_run"])
        self.assertIsNone(freshness["last_run_failed_step"])

    def test_44_get_freshness_fields_reads_from_db_fallback(self):
        db_path = str(self.temp_dir / "fresh.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO telegram_state VALUES ('last_successful_run', '2026-10-25T12:00:00Z')")
        conn.execute("INSERT INTO telegram_state VALUES ('last_run_failed_step', 'Telegram inbox')")
        conn.commit()
        conn.close()

        with mock.patch.dict(os.environ, {"RUNNER_TEMP": str(self.temp_dir / "nonexistent")}):
            freshness = export_status.get_freshness_fields(db_path=db_path)
        self.assertEqual(freshness["last_successful_run"], "2026-10-25T12:00:00Z")
        self.assertEqual(freshness["last_run_failed_step"], "Telegram inbox")

    def test_45_build_status_private_contains_freshness_keys(self):
        db_path = str(self.temp_dir / "priv.db")
        status = export_status.build_status_private(db_path=db_path)
        self.assertIn("last_successful_run", status)
        self.assertIn("last_run_failed_step", status)

    def test_46_build_status_preserves_allowed_keys_for_public(self):
        db_path = str(self.temp_dir / "pub.db")
        status = export_status.build_status(db_path=db_path)
        self.assertIn("generated_at", status)
        self.assertIn("agents", status)
        self.assertNotIn("kill_switch", status)
        self.assertNotIn("money", status)

    def test_47_no_secrets_in_freshness_values(self):
        state_file = self.temp_dir / "robot-failure-state.json"
        state_file.write_text(json.dumps({
            "last_successful_run": "2026-11-10T00:00:00Z",
            "last_run_failed_step": "Run tests first"
        }))
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": str(self.temp_dir)}):
            freshness = export_status.get_freshness_fields()
        blob = json.dumps(freshness).lower()
        for s in ("secret", "token", "key", "password", "cf_"):
            self.assertNotIn(s, blob)

    def test_48_freshness_timestamps_are_iso8601_utc(self):
        state_file = self.temp_dir / "robot-failure-state.json"
        state_file.write_text(json.dumps({
            "last_successful_run": "2026-11-10T00:00:00Z",
            "last_run_failed_step": None
        }))
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": str(self.temp_dir)}):
            freshness = export_status.get_freshness_fields()
        self.assertTrue(freshness["last_successful_run"].endswith("Z"))

    # --- PART 6: edge cases & integrity checks ---

    def test_49_no_money_amounts_in_alerts(self):
        failing_tests = ["test_r26_control_inbox.ControlInboxTests.test_kv_down"]
        run_failure.write_summary(self.summary_file, "tests", "Failed tests: " + ", ".join(failing_tests))
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env)
        msg = self.send.call_args.args[0]
        for currency in ("$", "€", "₹", "rs.", "usd", "inr"):
            self.assertNotIn(currency, msg.lower())

    def test_50_no_secrets_in_failure_alert(self):
        failing_tests = ["test_run_failure.RunFailureTests.test_first_failure_is_silent"]
        run_failure.write_summary(self.summary_file, "tests", "Failed tests: " + ", ".join(failing_tests))
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env)
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env)
        msg = self.send.call_args.args[0]
        for term in ("secret", "password", "gsk_", "cf_api_token", "db_key"):
            self.assertNotIn(term, msg.lower())

    def test_51_state_atomic_write_leaves_no_temp_files(self):
        run_failure.write_summary(self.summary_file, "tests", "AssertionError: a check failed.")
        run_failure.finish("failure", self.state_file, self.summary_file, send=self.send, env=self.env)
        tmp_files = list(self.temp_dir.glob("tmp*"))
        self.assertEqual(tmp_files, [])

    def test_52_safe_detail_preserves_failed_tests(self):
        detail = "Failed tests: test_run_failure.RunFailureTests.test_first_failure_is_silent"
        self.assertEqual(run_failure.safe_detail(detail), detail)

    def test_53_safe_detail_rejects_arbitrary_string(self):
        self.assertEqual(run_failure.safe_detail("private exception details"), run_failure.UNKNOWN)

    def test_54_problem_recognizes_assertion_error(self):
        output = "Traceback:\nAssertionError: something failed\n"
        self.assertEqual(run_failure.problem(output), run_failure.ERRORS["AssertionError"])

    def test_55_problem_recognizes_file_not_found(self):
        output = "FileNotFoundError: [Errno 2] No such file\n"
        self.assertEqual(run_failure.problem(output), "setup problem: required file or tool")

    def test_56_problem_recognizes_module_not_found(self):
        output = "ModuleNotFoundError: No module named 'xyz'\n"
        self.assertEqual(run_failure.problem(output), "setup problem: Python module")

    def test_57_problem_recognizes_failed_test_pattern(self):
        output = "FAIL: test_first_failure_is_silent (test_run_failure.RunFailureTests.test_first_failure_is_silent)\n"
        prob = run_failure.problem(output)
        self.assertIn("Failed tests: test_run_failure.RunFailureTests.test_first_failure_is_silent", prob)

    def test_58_write_and_read_summary_roundtrip(self):
        run_failure.write_summary(self.summary_file, "tests", run_failure.ERRORS["AssertionError"])
        summary = run_failure.read_summary(self.summary_file)
        self.assertIn("Step: Run tests first", summary)
        self.assertIn("Problem: AssertionError: a check failed.", summary)

    def test_59_read_summary_nonexistent_returns_defaults(self):
        summary = run_failure.read_summary(self.temp_dir / "nonexistent.txt")
        self.assertIn("Step: Workflow setup", summary)
        self.assertIn(f"Problem: {run_failure.UNKNOWN}", summary)

    def test_60_control_inbox_fetch_empty_when_no_config(self):
        with mock.patch("builtins.print"):
            self.assertEqual(control_inbox.fetch(env={}), [])

    def test_61_control_inbox_reason_validation_valid(self):
        now = 1792656000
        rec = {
            "id": "r1",
            "action": "approve",
            "target": "1",
            "value": None,
            "created_at": "2026-10-22T08:00:00.000Z",
            "nonce": "n1"
        }
        rec["signature"] = control_inbox._digest(control_inbox._signed(rec), "test-key")
        self.assertIsNone(control_inbox._reason(rec, "test-key", now))

    def test_62_control_inbox_reason_validation_bad_sig(self):
        now = 1792656000
        rec = {
            "id": "r1",
            "action": "approve",
            "target": "1",
            "value": None,
            "created_at": "2026-10-22T08:00:00.000Z",
            "nonce": "n1",
            "signature": "bad"
        }
        self.assertEqual(control_inbox._reason(rec, "test-key", now), "bad signature")

    def test_63_control_inbox_reason_validation_expired(self):
        now = 1792656000
        rec = {
            "id": "r1",
            "action": "approve",
            "target": "1",
            "value": None,
            "created_at": "2026-10-20T08:00:00.000Z",
            "nonce": "n1"
        }
        rec["signature"] = control_inbox._digest(control_inbox._signed(rec), "test-key")
        self.assertEqual(control_inbox._reason(rec, "test-key", now), "expired")

    def test_64_control_inbox_reason_validation_unknown_action(self):
        now = 1792656000
        rec = {
            "id": "r1",
            "action": "destroy_everything",
            "target": "1",
            "value": None,
            "created_at": "2026-10-22T08:00:00.000Z",
            "nonce": "n1"
        }
        rec["signature"] = control_inbox._digest(control_inbox._signed(rec), "test-key")
        self.assertEqual(control_inbox._reason(rec, "test-key", now), "unknown action")

    def test_65_export_status_purge_simulated(self):
        db_path = str(self.temp_dir / "purge.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE campaigns (source TEXT, status TEXT, earnings REAL)")
        conn.execute("INSERT INTO campaigns VALUES ('simulated', 'DONE', 10.0)")
        conn.commit()
        conn.close()

        deleted = export_status.purge_simulated(db_path)
        self.assertEqual(deleted.get("campaigns"), 1)


if __name__ == "__main__":
    unittest.main()
