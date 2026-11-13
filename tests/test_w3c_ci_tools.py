"""tests/test_w3c_ci_tools.py — Unit tests for tools/ci_install.py and tools/run_tests_chunked.py.

Tests cover:
- ci_install: Pillow installation success on attempt 1, 2, and 3 (retries + backoff)
- ci_install: failure after max retries
- ci_install: requirements file vs fallback package string
- ci_install: pip cache directory resolution and usage
- ci_install: apt-get package installation, already-installed checks, skip-apt option
- ci_install: apt-get failure handling, missing apt-get executable
- ci_install: single-line log formatting and exit code
- run_tests_chunked: test module discovery and sorting
- run_tests_chunked: chunking with various chunk sizes and edge cases (empty, 1, large)
- run_tests_chunked: unittest output parsing (OK, skipped, FAILED with errors/failures)
- run_tests_chunked: chunk execution, timing, and slowest 10 modules ranking
- run_tests_chunked: timeout handling and synthetic failure generation for run_failure
- run_tests_chunked: FAIL / ERROR regex compatibility for run_failure.py
- run_tests_chunked: summary formatting, exit codes (0 for pass, 1 for fail)
- CLI argument parsing for both tools
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

import run_failure
import tools.ci_install as ci_install
import tools.run_tests_chunked as run_tests_chunked

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_EMAIL = "name@example.com"
SAMPLE_CHAT_ID = int("1234" "5678")


class TestCiInstall(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.req_path = Path(self.temp_dir.name) / ".agent-image-requirements.txt"
        self.req_path.write_text("Pillow==11.3.0\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_check_pillow_installed_true(self):
        with patch.dict(sys.modules, {"PIL": MagicMock()}):
            self.assertTrue(ci_install.check_pillow_installed())

    def test_02_check_pillow_installed_false(self):
        with patch.dict(sys.modules, {"PIL": None}):
            with patch("builtins.__import__", side_effect=ImportError("No module named 'PIL'")):
                self.assertFalse(ci_install.check_pillow_installed())

    def test_03_check_dpkg_installed_returns_true_when_installed(self):
        fake_runner = MagicMock(return_value=MagicMock(stdout="install ok installed\n", returncode=0))
        with patch("subprocess.run", fake_runner):
            self.assertTrue(ci_install.check_dpkg_installed("poppler-utils"))

    def test_04_check_dpkg_installed_returns_false_when_not_installed(self):
        fake_runner = MagicMock(return_value=MagicMock(stdout="deinstall ok config-files\n", returncode=0))
        with patch("subprocess.run", fake_runner):
            self.assertFalse(ci_install.check_dpkg_installed("poppler-utils"))

    def test_05_check_dpkg_installed_handles_oserror(self):
        fake_runner = MagicMock(side_effect=FileNotFoundError("dpkg-query missing"))
        with patch("subprocess.run", fake_runner):
            self.assertFalse(ci_install.check_dpkg_installed("poppler-utils"))

    def test_06_get_pip_cache_dir_returns_stdout_when_successful(self):
        fake_runner = MagicMock(return_value=MagicMock(stdout="/custom/cache/dir\n", returncode=0))
        with patch("subprocess.run", fake_runner):
            self.assertEqual(ci_install.get_pip_cache_dir(), "/custom/cache/dir")

    def test_07_get_pip_cache_dir_falls_back_on_nonzero_exit(self):
        fake_runner = MagicMock(return_value=MagicMock(stdout="", returncode=1))
        with patch("subprocess.run", fake_runner):
            self.assertTrue(ci_install.get_pip_cache_dir().endswith(".cache/pip"))

    def test_08_get_pip_cache_dir_falls_back_on_exception(self):
        fake_runner = MagicMock(side_effect=OSError("pipe error"))
        with patch("subprocess.run", fake_runner):
            self.assertTrue(ci_install.get_pip_cache_dir().endswith(".cache/pip"))

    def test_09_install_pillow_success_first_attempt(self):
        runner = MagicMock(return_value=MagicMock(returncode=0, stdout="Successfully installed Pillow"))
        sleeper = MagicMock()
        ok, attempts, msg = ci_install.install_pillow(
            requirements_file=self.req_path,
            retries=3,
            sleeper=sleeper,
            runner=runner,
        )
        self.assertTrue(ok)
        self.assertEqual(attempts, 1)
        self.assertIn("Installed -r .agent-image-requirements.txt on attempt 1", msg)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(sleeper.call_count, 0)

    def test_10_install_pillow_success_on_retry(self):
        runner = MagicMock(
            side_effect=[
                MagicMock(returncode=1, stdout="Connection reset by peer\n"),
                MagicMock(returncode=0, stdout="Successfully installed Pillow\n"),
            ]
        )
        sleeper = MagicMock()
        ok, attempts, msg = ci_install.install_pillow(
            requirements_file=self.req_path,
            retries=3,
            initial_delay=1.0,
            backoff_factor=2.0,
            sleeper=sleeper,
            runner=runner,
        )
        self.assertTrue(ok)
        self.assertEqual(attempts, 2)
        self.assertEqual(runner.call_count, 2)
        sleeper.assert_called_once_with(1.0)

    def test_11_install_pillow_exponential_backoff_delays(self):
        runner = MagicMock(return_value=MagicMock(returncode=1, stdout="Network timeout\n"))
        sleeper = MagicMock()
        ok, attempts, msg = ci_install.install_pillow(
            requirements_file=self.req_path,
            retries=3,
            initial_delay=2.0,
            backoff_factor=3.0,
            sleeper=sleeper,
            runner=runner,
        )
        self.assertFalse(ok)
        self.assertEqual(attempts, 3)
        self.assertEqual(sleeper.call_args_list, [call(2.0), call(6.0)])

    def test_12_install_pillow_fallback_package_when_no_req_file(self):
        runner = MagicMock(return_value=MagicMock(returncode=0, stdout="Successfully installed"))
        non_existent = Path(self.temp_dir.name) / "missing_file.txt"
        ok, attempts, msg = ci_install.install_pillow(
            requirements_file=non_existent,
            package="Pillow==11.3.0",
            retries=1,
            runner=runner,
        )
        self.assertTrue(ok)
        self.assertIn("Installed Pillow==11.3.0 on attempt 1", msg)
        cmd_run = runner.call_args[0][0]
        self.assertIn("Pillow==11.3.0", cmd_run)

    def test_13_install_pillow_catches_oserror(self):
        runner = MagicMock(side_effect=OSError("Exec format error"))
        sleeper = MagicMock()
        ok, attempts, msg = ci_install.install_pillow(
            requirements_file=self.req_path,
            retries=2,
            initial_delay=0.5,
            sleeper=sleeper,
            runner=runner,
        )
        self.assertFalse(ok)
        self.assertEqual(attempts, 2)
        self.assertIn("Exec format error", msg)

    def test_14_ensure_apt_packages_all_installed_does_no_apt_calls(self):
        runner = MagicMock()
        checker = MagicMock(return_value=True)
        ok, count, msg = ci_install.ensure_apt_packages(
            packages=("poppler-utils", "fonts-urw-base35"),
            runner=runner,
            checker=checker,
            prefer_none=True,
        )
        self.assertTrue(ok)
        self.assertEqual(count, 0)
        self.assertIn("already installed", msg)
        self.assertEqual(runner.call_count, 0)

    def test_15_ensure_apt_packages_installs_missing_only(self):
        checker = MagicMock(side_effect=lambda pkg: pkg == "fonts-urw-base35")
        runner = MagicMock(return_value=MagicMock(returncode=0))
        with patch("shutil.which", return_value="/usr/bin/apt-get"):
            ok, count, msg = ci_install.ensure_apt_packages(
                packages=("poppler-utils", "fonts-urw-base35"),
                runner=runner,
                checker=checker,
                prefer_none=True,
            )
        self.assertTrue(ok)
        self.assertEqual(count, 1)
        self.assertIn("poppler-utils", msg)
        install_call = runner.call_args_list[1][0][0]
        self.assertIn("poppler-utils", install_call)
        self.assertNotIn("fonts-urw-base35", install_call)

    def test_16_ensure_apt_packages_missing_apt_get(self):
        checker = MagicMock(return_value=False)
        with patch("shutil.which", return_value=None):
            ok, count, msg = ci_install.ensure_apt_packages(
                packages=("poppler-utils",),
                checker=checker,
                prefer_none=True,
            )
        self.assertFalse(ok)
        self.assertEqual(count, 0)
        self.assertIn("apt-get not available", msg)

    def test_17_ensure_apt_packages_handles_apt_install_failure(self):
        checker = MagicMock(return_value=False)
        runner = MagicMock(
            side_effect=[
                MagicMock(returncode=0),  # apt update
                MagicMock(returncode=100),  # apt install
            ]
        )
        with patch("shutil.which", return_value="/usr/bin/apt-get"):
            ok, count, msg = ci_install.ensure_apt_packages(
                packages=("poppler-utils",),
                runner=runner,
                checker=checker,
                prefer_none=True,
            )
        self.assertFalse(ok)
        self.assertIn("exit code 100", msg)

    def test_18_ensure_apt_packages_handles_oserror(self):
        checker = MagicMock(return_value=False)
        runner = MagicMock(side_effect=[MagicMock(returncode=0), OSError("Permission denied")])
        with patch("shutil.which", return_value="/usr/bin/apt-get"):
            ok, count, msg = ci_install.ensure_apt_packages(
                packages=("poppler-utils",),
                runner=runner,
                checker=checker,
                prefer_none=True,
            )
        self.assertFalse(ok)
        self.assertIn("Permission denied", msg)

    def test_19_run_install_full_success_logs_single_line(self):
        logs = []
        runner = MagicMock(return_value=MagicMock(returncode=0, stdout="OK"))
        with patch("tools.ci_install.check_dpkg_installed", return_value=True):
            with patch("mockups.available", return_value=True, create=True):
                rc = ci_install.run_install(
                    requirements_file=self.req_path,
                    skip_apt=False,
                    runner=runner,
                    log_func=logs.append,
                )
        self.assertEqual(rc, 0)
        self.assertEqual(len(logs), 1)
        self.assertTrue(logs[0].startswith("[ci_install] status=SUCCESS"))
        self.assertIn("pip_attempts=1", logs[0])
        self.assertIn("apt=0 new", logs[0])
        self.assertIn("image_tools_ready=True", logs[0])

    def test_20_run_install_skip_apt_flag(self):
        logs = []
        runner = MagicMock(return_value=MagicMock(returncode=0, stdout="OK"))
        with patch("mockups.available", return_value=False, create=True):
            rc = ci_install.run_install(
                requirements_file=self.req_path,
                skip_apt=True,
                runner=runner,
                log_func=logs.append,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(len(logs), 1)
        self.assertIn("apt skipped", logs[0])

    def test_21_run_install_failure_returns_exit_code_1(self):
        logs = []
        runner = MagicMock(return_value=MagicMock(returncode=1, stdout="Failed"))
        sleeper = MagicMock()
        rc = ci_install.run_install(
            requirements_file=self.req_path,
            retries=2,
            sleeper=sleeper,
            runner=runner,
            log_func=logs.append,
        )
        self.assertEqual(rc, 1)
        self.assertEqual(len(logs), 1)
        self.assertIn("status=FAILED", logs[0])

    def test_22_main_cli_args_parsing(self):
        with patch("tools.ci_install.run_install", return_value=0) as mock_run:
            rc = ci_install.main(["-r", "custom.txt", "--package", "Pillow==10.0.0", "--skip-apt", "--retries", "5", "--backoff", "1.5"])
            self.assertEqual(rc, 0)
            mock_run.assert_called_once_with(
                requirements_file="custom.txt",
                package="Pillow==10.0.0",
                skip_apt=True,
                retries=5,
                backoff=1.5,
            )

    def test_23_main_cli_default_args(self):
        with patch("tools.ci_install.run_install", return_value=0) as mock_run:
            rc = ci_install.main([])
            self.assertEqual(rc, 0)
            self.assertEqual(mock_run.call_args[1]["retries"], 3)
            self.assertEqual(mock_run.call_args[1]["backoff"], 2.0)
            self.assertFalse(mock_run.call_args[1]["skip_apt"])

    def test_24_ci_install_exponential_backoff_factor_custom(self):
        runner = MagicMock(return_value=MagicMock(returncode=1, stdout="Failed\n"))
        sleeper = MagicMock()
        ci_install.install_pillow(
            requirements_file=self.req_path,
            retries=4,
            initial_delay=1.0,
            backoff_factor=1.5,
            sleeper=sleeper,
            runner=runner,
        )
        self.assertEqual(sleeper.call_args_list, [call(1.0), call(1.5), call(2.25)])

    def test_25_ci_install_prefer_none_false_forces_reinstall(self):
        checker = MagicMock(return_value=True)  # Already installed
        runner = MagicMock(return_value=MagicMock(returncode=0))
        with patch("shutil.which", return_value="/usr/bin/apt-get"):
            ok, count, msg = ci_install.ensure_apt_packages(
                packages=("poppler-utils",),
                runner=runner,
                checker=checker,
                prefer_none=False,
            )
        self.assertTrue(ok)
        self.assertEqual(count, 1)
        self.assertEqual(runner.call_count, 2)  # update + install

    def test_26_ci_install_dpkg_handles_empty_output(self):
        fake_runner = MagicMock(return_value=MagicMock(stdout="", returncode=1))
        with patch("subprocess.run", fake_runner):
            self.assertFalse(ci_install.check_dpkg_installed("poppler-utils"))

    def test_27_ci_install_pip_cache_dir_exception_handled(self):
        fake_runner = MagicMock(side_effect=PermissionError("no access"))
        with patch("subprocess.run", fake_runner):
            cache_dir = ci_install.get_pip_cache_dir()
            self.assertTrue(cache_dir.endswith(".cache/pip"))


class TestRunTestsChunked(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tests_path = Path(self.temp_dir.name)
        # Create some fake test files
        (self.tests_path / "test_a.py").write_text("# test a\n", encoding="utf-8")
        (self.tests_path / "test_b.py").write_text("# test b\n", encoding="utf-8")
        (self.tests_path / "test_c.py").write_text("# test c\n", encoding="utf-8")
        (self.tests_path / "helper.py").write_text("# helper\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_28_discover_test_modules_finds_and_sorts(self):
        mods = run_tests_chunked.discover_test_modules(self.tests_path)
        self.assertEqual(mods, ["tests.test_a", "tests.test_b", "tests.test_c"])

    def test_29_discover_test_modules_missing_dir(self):
        mods = run_tests_chunked.discover_test_modules(Path(self.temp_dir.name) / "nonexistent")
        self.assertEqual(mods, [])

    def test_30_chunk_list_standard(self):
        items = ["a", "b", "c", "d", "e"]
        chunks = run_tests_chunked.chunk_list(items, 2)
        self.assertEqual(chunks, [["a", "b"], ["c", "d"], ["e"]])

    def test_31_chunk_list_empty(self):
        self.assertEqual(run_tests_chunked.chunk_list([], 5), [])

    def test_32_chunk_list_zero_or_negative_chunk_size(self):
        items = ["a", "b"]
        self.assertEqual(run_tests_chunked.chunk_list(items, 0), [["a"], ["b"]])
        self.assertEqual(run_tests_chunked.chunk_list(items, -1), [["a"], ["b"]])

    def test_33_chunk_list_larger_than_items(self):
        items = ["a", "b"]
        self.assertEqual(run_tests_chunked.chunk_list(items, 10), [["a", "b"]])

    def test_34_parse_unittest_output_all_pass(self):
        out = (
            "...\n"
            "----------------------------------------------------------------------\n"
            "Ran 3 tests in 0.050s\n\n"
            "OK\n"
        )
        parsed = run_tests_chunked.parse_unittest_output(out)
        self.assertEqual(parsed["run"], 3)
        self.assertEqual(parsed["failures"], 0)
        self.assertEqual(parsed["errors"], 0)
        self.assertEqual(parsed["skipped"], 0)
        self.assertEqual(parsed["failing_tests"], [])

    def test_35_parse_unittest_output_with_skips(self):
        out = (
            "...ssss\n"
            "----------------------------------------------------------------------\n"
            "Ran 7 tests in 0.120s\n\n"
            "OK (skipped=4)\n"
        )
        parsed = run_tests_chunked.parse_unittest_output(out)
        self.assertEqual(parsed["run"], 7)
        self.assertEqual(parsed["skipped"], 4)
        self.assertEqual(parsed["failures"], 0)
        self.assertEqual(parsed["errors"], 0)

    def test_36_parse_unittest_output_with_failures(self):
        out = (
            "..F.\n"
            "======================================================================\n"
            "FAIL: test_feature (tests.test_sample.SampleTest.test_feature)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            "AssertionError: True is not False\n\n"
            "----------------------------------------------------------------------\n"
            "Ran 4 tests in 0.200s\n\n"
            "FAILED (failures=1)\n"
        )
        parsed = run_tests_chunked.parse_unittest_output(out)
        self.assertEqual(parsed["run"], 4)
        self.assertEqual(parsed["failures"], 1)
        self.assertEqual(parsed["errors"], 0)
        self.assertEqual(len(parsed["failing_tests"]), 1)
        self.assertEqual(
            parsed["failing_tests"][0],
            "FAIL: test_feature (tests.test_sample.SampleTest.test_feature)",
        )

    def test_37_parse_unittest_output_with_errors_and_skips(self):
        out = (
            "..E.s\n"
            "======================================================================\n"
            "ERROR: test_crash (tests.test_mod.ModTest.test_crash)\n"
            "----------------------------------------------------------------------\n"
            "ValueError: boom\n\n"
            "----------------------------------------------------------------------\n"
            "Ran 5 tests in 0.300s\n\n"
            "FAILED (failures=0, errors=1, skipped=1)\n"
        )
        parsed = run_tests_chunked.parse_unittest_output(out)
        self.assertEqual(parsed["run"], 5)
        self.assertEqual(parsed["errors"], 1)
        self.assertEqual(parsed["skipped"], 1)
        self.assertEqual(len(parsed["failing_tests"]), 1)
        self.assertEqual(
            parsed["failing_tests"][0],
            "ERROR: test_crash (tests.test_mod.ModTest.test_crash)",
        )

    def test_38_parse_unittest_output_multiple_failures_and_errors(self):
        out = (
            "FAIL: test_1 (tests.test_one.OneTest)\n"
            "ERROR: test_2 (tests.test_two.TwoTest)\n"
            "Ran 10 tests in 0.4s\n"
            "FAILED (failures=1, errors=1)\n"
        )
        parsed = run_tests_chunked.parse_unittest_output(out)
        self.assertEqual(parsed["run"], 10)
        self.assertEqual(parsed["failures"], 1)
        self.assertEqual(parsed["errors"], 1)
        self.assertEqual(len(parsed["failing_tests"]), 2)

    def test_39_run_chunk_success(self):
        runner = MagicMock(return_value=MagicMock(returncode=0, stdout="Ran 5 tests in 0.1s\nOK\n"))
        res = run_tests_chunked.run_chunk(["tests.test_a"], timeout=10, runner=runner)
        self.assertEqual(res["returncode"], 0)
        self.assertFalse(res["timed_out"])
        self.assertEqual(res["parsed"]["run"], 5)

    def test_40_run_chunk_timeout_expired(self):
        runner = MagicMock(side_effect=subprocess.TimeoutExpired(cmd=["python3"], timeout=5))
        res = run_tests_chunked.run_chunk(["tests.test_a", "tests.test_b"], timeout=5, runner=runner)
        self.assertTrue(res["timed_out"])
        self.assertEqual(res["returncode"], 124)
        self.assertEqual(res["parsed"]["errors"], 1)
        self.assertTrue(any("TimeoutError" in f for f in res["parsed"]["failing_tests"]))

    def test_41_run_chunk_oserror(self):
        runner = MagicMock(side_effect=OSError("Process failed"))
        res = run_tests_chunked.run_chunk(["tests.test_a"], timeout=5, runner=runner)
        self.assertEqual(res["returncode"], 1)
        self.assertIn("OSError: Process failed", res["output"])

    def test_42_run_single_module_success(self):
        runner = MagicMock(return_value=MagicMock(returncode=0, stdout="OK"))
        rc, elapsed, out = run_tests_chunked.run_single_module("tests.test_a", timeout=10, runner=runner)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "OK")

    def test_43_run_single_module_timeout(self):
        runner = MagicMock(side_effect=subprocess.TimeoutExpired(cmd="test", timeout=2))
        rc, elapsed, out = run_tests_chunked.run_single_module("tests.test_a", timeout=2, runner=runner)
        self.assertEqual(rc, -1)
        self.assertIn("TIMEOUT", out)

    def test_44_run_single_module_oserror(self):
        runner = MagicMock(side_effect=OSError("cannot run"))
        rc, elapsed, out = run_tests_chunked.run_single_module("tests.test_a", runner=runner)
        self.assertEqual(rc, -1)
        self.assertIn("cannot run", out)

    def test_45_format_summary_passed(self):
        timings = {"chunk_1": 1.2, "chunk_2": 2.5}
        summary = run_tests_chunked.format_summary(
            total_tests=50,
            total_failures=0,
            total_errors=0,
            total_skipped=2,
            total_time=3.7,
            chunk_timings=timings,
            all_failing_tests=[],
        )
        self.assertIn("Total tests run: 50 | Failures: 0 | Errors: 0 | Skipped: 2 | Time: 3.70s", summary)
        self.assertIn("RESULT: PASSED", summary)
        self.assertIn("Slowest modules / chunks (top 10):", summary)
        self.assertIn("2.50s - chunk_2", summary)
        self.assertIn("1.20s - chunk_1", summary)

    def test_46_format_summary_failed(self):
        timings = {"chunk_1": 1.0}
        fails = ["FAIL: test_x (tests.test_a.ATest)"]
        summary = run_tests_chunked.format_summary(
            total_tests=10,
            total_failures=1,
            total_errors=0,
            total_skipped=0,
            total_time=1.0,
            chunk_timings=timings,
            all_failing_tests=fails,
        )
        self.assertIn("RESULT: FAILED", summary)
        self.assertIn("Failing tests (1):", summary)
        self.assertIn("FAIL: test_x (tests.test_a.ATest)", summary)

    def test_47_format_summary_ranks_top_10_slowest(self):
        timings = {f"mod_{i:02d}": float(i) for i in range(1, 20)}
        summary = run_tests_chunked.format_summary(
            total_tests=100,
            total_failures=0,
            total_errors=0,
            total_skipped=0,
            total_time=190.0,
            chunk_timings=timings,
            all_failing_tests=[],
        )
        # Should contain mod_19 down to mod_10, but not mod_01
        self.assertIn("19.00s - mod_19", summary)
        self.assertIn("10.00s - mod_10", summary)
        self.assertNotIn("1.00s - mod_01", summary)

    def test_48_run_all_chunked_no_tests_found(self):
        logs = []
        rc = run_tests_chunked.run_all_chunked(
            tests_dir=Path(self.temp_dir.name) / "empty",
            log_func=logs.append,
        )
        self.assertEqual(rc, 0)
        self.assertIn("No tests found to run.", logs[0])

    def test_49_run_all_chunked_success(self):
        logs = []
        runner = MagicMock(return_value=MagicMock(returncode=0, stdout="Ran 1 test in 0.01s\nOK\n"))
        rc = run_tests_chunked.run_all_chunked(
            tests_dir=self.tests_path,
            chunk_size=2,
            timeout_per_chunk=10,
            runner=runner,
            log_func=logs.append,
        )
        self.assertEqual(rc, 0)
        # 3 tests, chunk_size=2 -> 2 chunks
        self.assertEqual(runner.call_count, 2)
        full_log = "\n".join(logs)
        self.assertIn("RESULT: PASSED", full_log)
        self.assertIn("Total tests run: 2", full_log)

    def test_50_run_all_chunked_failure(self):
        logs = []
        runner = MagicMock(
            side_effect=[
                MagicMock(returncode=0, stdout="Ran 1 test in 0.01s\nOK\n"),
                MagicMock(
                    returncode=1,
                    stdout=(
                        "FAIL: test_b (tests.test_b.BTest)\n"
                        "Ran 1 test in 0.01s\n"
                        "FAILED (failures=1)\n"
                    ),
                ),
            ]
        )
        rc = run_tests_chunked.run_all_chunked(
            tests_dir=self.tests_path,
            chunk_size=2,
            timeout_per_chunk=10,
            runner=runner,
            log_func=logs.append,
        )
        self.assertEqual(rc, 1)
        full_log = "\n".join(logs)
        self.assertIn("RESULT: FAILED", full_log)
        self.assertIn("FAIL: test_b (tests.test_b.BTest)", full_log)

    def test_51_run_all_chunked_timeout_failure(self):
        logs = []
        runner = MagicMock(side_effect=subprocess.TimeoutExpired(cmd=["python3"], timeout=2))
        rc = run_tests_chunked.run_all_chunked(
            tests_dir=self.tests_path,
            chunk_size=5,
            timeout_per_chunk=2,
            runner=runner,
            log_func=logs.append,
        )
        self.assertEqual(rc, 1)
        full_log = "\n".join(logs)
        self.assertIn("RESULT: FAILED", full_log)
        self.assertIn("TimeoutError", full_log)

    def test_52_run_failure_compatibility_extracts_failed_tests(self):
        """run_failure.problem() must extract failed tests correctly from run_tests_chunked output."""
        output = (
            "[run_tests_chunked] Discovered 50 test modules...\n"
            "Chunk 1 failed:\n"
            "FAIL: test_first_failure_is_silent (test_run_failure.RunFailureTests.test_first_failure_is_silent)\n"
            "FAIL: test_missing_counter (test_run_failure.RunFailureTests.test_missing_counter)\n"
            "FAILED (failures=2)\n"
            "======================================================================\n"
            "CHUNKED TEST RUNNER SUMMARY\n"
            "RESULT: FAILED\n"
            "FAIL: test_first_failure_is_silent (test_run_failure.RunFailureTests.test_first_failure_is_silent)\n"
            "FAIL: test_missing_counter (test_run_failure.RunFailureTests.test_missing_counter)\n"
        )
        prob = run_failure.problem(output)
        self.assertIn("Failed tests:", prob)
        self.assertIn("test_run_failure.RunFailureTests.test_first_failure_is_silent", prob)

    def test_53_run_failure_compatibility_extracts_error_tests(self):
        output = (
            "ERROR: test_crash (tests.test_demo.DemoTest.test_crash)\n"
            "RESULT: FAILED\n"
        )
        import re
        names = []
        for method, location in re.findall(r"^(?:FAIL|ERROR): (test_\w+) \(([\w.]+)\)", output, re.MULTILINE):
            names.append(location if location.endswith("." + method) else location + "." + method)
        self.assertEqual(names, ["tests.test_demo.DemoTest.test_crash"])

    def test_54_run_tests_chunked_main_cli(self):
        with patch("tools.run_tests_chunked.run_all_chunked", return_value=0) as mock_run:
            rc = run_tests_chunked.main(["--tests-dir", "/tmp/tests", "--chunk-size", "15", "--timeout", "90"])
            self.assertEqual(rc, 0)
            mock_run.assert_called_once_with(
                tests_dir="/tmp/tests",
                chunk_size=15,
                timeout_per_chunk=90,
            )

    def test_55_run_tests_chunked_main_defaults(self):
        with patch("tools.run_tests_chunked.run_all_chunked", return_value=0) as mock_run:
            rc = run_tests_chunked.main([])
            self.assertEqual(rc, 0)
            self.assertIsNone(mock_run.call_args[1]["tests_dir"])
            self.assertEqual(mock_run.call_args[1]["chunk_size"], 20)
            self.assertEqual(mock_run.call_args[1]["timeout_per_chunk"], 300)

    def test_56_run_tests_chunked_environment_chilli_test_default(self):
        env_passed = {}

        def fake_runner(*args, **kwargs):
            nonlocal env_passed
            env_passed = kwargs.get("env", {})
            return MagicMock(returncode=0, stdout="Ran 1 test in 0.01s\nOK\n")

        run_tests_chunked.run_all_chunked(
            tests_dir=self.tests_path,
            chunk_size=10,
            runner=fake_runner,
            log_func=lambda x: None,
        )
        self.assertEqual(env_passed.get("CHILLI_TEST"), "1")

    def test_57_run_tests_chunked_environment_respects_passed_env(self):
        def fake_runner(*args, **kwargs):
            env_passed = kwargs.get("env", {})
            self.assertEqual(env_passed.get("CUSTOM_VAR"), "xyz")
            self.assertEqual(env_passed.get("CHILLI_TEST"), "1")
            return MagicMock(returncode=0, stdout="Ran 1 test in 0.01s\nOK\n")

        run_tests_chunked.run_all_chunked(
            tests_dir=self.tests_path,
            chunk_size=10,
            env={"CUSTOM_VAR": "xyz"},
            runner=fake_runner,
            log_func=lambda x: None,
        )

    def test_58_run_chunk_output_truncated_handling(self):
        res = run_tests_chunked.run_chunk(
            ["tests.test_a"],
            runner=MagicMock(return_value=MagicMock(returncode=0, stdout="Just some text")),
        )
        self.assertEqual(res["parsed"]["run"], 0)
        self.assertEqual(res["parsed"]["failures"], 0)

    def test_59_run_chunk_handles_multi_error_multiline(self):
        output = (
            "ERROR: test_x (pkg.mod.TestX.test_x)\n"
            "some traceback\n"
            "ERROR: test_y (pkg.mod.TestY.test_y)\n"
            "FAILED (errors=2)\n"
        )
        parsed = run_tests_chunked.parse_unittest_output(output)
        self.assertEqual(parsed["errors"], 2)
        self.assertEqual(len(parsed["failing_tests"]), 2)

    def test_60_format_summary_empty_timings(self):
        summary = run_tests_chunked.format_summary(
            total_tests=0,
            total_failures=0,
            total_errors=0,
            total_skipped=0,
            total_time=0.0,
            chunk_timings={},
            all_failing_tests=[],
        )
        self.assertIn("Total tests run: 0", summary)
        self.assertIn("RESULT: PASSED", summary)

    def test_61_run_tests_chunked_retains_failing_output_lines(self):
        logs = []
        runner = MagicMock(
            return_value=MagicMock(
                returncode=1,
                stdout="Traceback info\nFAIL: test_bad (tests.test_bad.BadTest.test_bad)\nAssertionError\nFAILED (failures=1)\n",
            )
        )
        rc = run_tests_chunked.run_all_chunked(
            tests_dir=self.tests_path,
            chunk_size=5,
            runner=runner,
            log_func=logs.append,
        )
        self.assertEqual(rc, 1)
        full_log = "\n".join(logs)
        self.assertIn("Traceback info", full_log)
        self.assertIn("FAIL: test_bad (tests.test_bad.BadTest.test_bad)", full_log)

    def test_62_workflow_file_contains_ci_install_and_run_tests_chunked(self):
        workflow = (ROOT / ".github/workflows/agent.yml").read_text(encoding="utf-8")
        self.assertIn("python3 tools/ci_install.py", workflow)
        self.assertIn("python3 tools/run_tests_chunked.py", workflow)
        self.assertIn("timeout-minutes: 9", workflow)
        self.assertIn('CHILLI_TEST: "1"', workflow)

    def test_63_ci_parity_tests_pass(self):
        from tests.test_ci_parity import image_dependencies_before_tests
        workflow = (ROOT / ".github/workflows/agent.yml").read_text(encoding="utf-8")
        self.assertTrue(image_dependencies_before_tests(workflow))


if __name__ == "__main__":
    unittest.main()


class ChunkedRunnerImportPathTest(unittest.TestCase):
    def test_tests_folder_is_on_pythonpath_like_discover(self):
        import os, importlib
        import importlib.util
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location("rtc_pp", os.path.join(root, "tools", "run_tests_chunked.py"))
        rtc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rtc)
        seen = {}

        def fake_runner(cmd, **kw):
            seen["pp"] = kw["env"]["PYTHONPATH"].split(os.pathsep)
            class R:
                returncode = 0
                stdout = "Ran 1 test in 0.1s\n\nOK\n"
            return R()

        rtc.run_all_chunked(chunk_size=500, runner=fake_runner, log_func=lambda *a: None)
        self.assertIn(os.path.join(root, "tests"), seen["pp"])
        self.assertIn(root, seen["pp"])
