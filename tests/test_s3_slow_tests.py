"""Unit tests for slow_tests.py — Running test modules with timing and reporting."""

import os
import tempfile
import unittest
from unittest import mock

import slow_tests


class TestSlowTests(unittest.TestCase):
    def setUp(self):
        env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in list(os.environ.keys()) if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT"},
        )
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.tmp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_normalize_module_name_variations(self):
        self.assertEqual(slow_tests.normalize_module_name("tests/test_foo.py"), "tests.test_foo")
        self.assertEqual(slow_tests.normalize_module_name("tests.test_bar"), "tests.test_bar")
        self.assertEqual(slow_tests.normalize_module_name("./tests/test_baz.py"), "tests.test_baz")
        self.assertEqual(slow_tests.normalize_module_name("tests\\test_win.py"), "tests.test_win")

    def test_parse_unittest_output_standard(self):
        sample_output = """test_one (tests.test_example.ExampleTest.test_one) ... ok
test_two (tests.test_example.ExampleTest.test_two) ... ok
test_three (tests.test_example.ExampleTest.test_three) ... FAIL

======================================================================
Ran 3 tests in 1.500s

FAILED (failures=1)
"""
        timed, count = slow_tests.parse_unittest_output(sample_output, 1.5)
        self.assertEqual(count, 3)
        self.assertEqual(len(timed), 3)
        self.assertEqual(timed[0]["name"], "tests.test_example.ExampleTest.test_one")
        self.assertEqual(timed[0]["status"], "ok")
        self.assertAlmostEqual(timed[0]["duration"], 0.5, places=2)
        self.assertEqual(timed[2]["status"], "FAIL")

    def test_parse_unittest_output_with_in_timing(self):
        sample_output = """test_fast (mytest.FastTest.test_fast) ... ok in 0.05s
test_slow (mytest.FastTest.test_slow) ... ok in 2.10s

Ran 2 tests in 2.150s
OK
"""
        timed, count = slow_tests.parse_unittest_output(sample_output, 2.15)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(timed[0]["duration"], 0.05)
        self.assertAlmostEqual(timed[1]["duration"], 2.10)

    def test_parse_unittest_output_empty(self):
        timed, count = slow_tests.parse_unittest_output("", 0.0)
        self.assertEqual(count, 0)
        self.assertEqual(timed, [])

    def test_run_test_module_success_mocked(self):
        with mock.patch("subprocess.run") as mock_sub:
            mock_sub.return_value = mock.Mock(
                returncode=0,
                stdout="test_alpha (tests.test_m.TestM.test_alpha) ... ok\nRan 1 test in 0.200s\nOK\n",
            )
            res = slow_tests.run_test_module("tests.test_m", timeout=10.0)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["test_count"], 1)
            self.assertGreaterEqual(res["duration"], 0.0)
            self.assertEqual(res["timed_tests"][0]["name"], "tests.test_m.TestM.test_alpha")

    def test_run_test_module_duration_measured_with_perf_counter(self):
        with mock.patch("time.perf_counter", side_effect=[100.0, 102.5]):
            with mock.patch("subprocess.run") as mock_sub:
                mock_sub.return_value = mock.Mock(returncode=0, stdout="")
                res = slow_tests.run_test_module("tests.test_m", timeout=10.0)
                self.assertEqual(res["duration"], 2.5)

    def test_run_test_module_failure_mocked(self):
        with mock.patch("subprocess.run") as mock_sub:
            mock_sub.return_value = mock.Mock(
                returncode=1,
                stdout="test_bad (tests.test_m.TestM.test_bad) ... FAIL\nRan 1 test in 0.100s\nFAILED\n",
            )
            res = slow_tests.run_test_module("tests.test_m", timeout=10.0)
            self.assertEqual(res["status"], "fail")
            self.assertEqual(res["returncode"], 1)

    def test_run_test_module_timeout_mocked(self):
        import subprocess
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="fake", timeout=5.0)):
            res = slow_tests.run_test_module("tests.test_hang", timeout=5.0)
            self.assertEqual(res["status"], "timeout")
            self.assertEqual(res["returncode"], -1)
            self.assertIn("Timed out", res["output"])

    def test_run_test_module_exception_mocked(self):
        with mock.patch("subprocess.run", side_effect=OSError("Command not found")):
            res = slow_tests.run_test_module("tests.test_err", timeout=5.0)
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["returncode"], -2)

    def test_run_all_test_modules_aggregation(self):
        dummy_results = [
            {
                "module": "tests.mod_a",
                "duration": 5.2,
                "status": "ok",
                "returncode": 0,
                "test_count": 2,
                "timed_tests": [
                    {"name": "ModA.test1", "duration": 2.6, "status": "ok"},
                    {"name": "ModA.test2", "duration": 2.6, "status": "ok"},
                ],
                "output": "",
            },
            {
                "module": "tests.mod_b",
                "duration": 22.5,
                "status": "ok",
                "returncode": 0,
                "test_count": 1,
                "timed_tests": [
                    {"name": "ModB.test_heavy", "duration": 22.5, "status": "ok"},
                ],
                "output": "",
            },
        ]

        with mock.patch("slow_tests.run_test_module", side_effect=dummy_results):
            agg = slow_tests.run_all_test_modules(["tests/mod_a.py", "tests/mod_b.py"])
            self.assertEqual(agg["modules_run"], 2)
            self.assertEqual(len(agg["slowest_modules"]), 2)
            self.assertEqual(agg["slowest_modules"][0]["module"], "tests.mod_b")
            self.assertEqual(len(agg["modules_over_20s"]), 1)
            self.assertEqual(agg["modules_over_20s"][0]["module"], "tests.mod_b")
            self.assertEqual(len(agg["slowest_15_tests"]), 3)
            self.assertEqual(agg["slowest_15_tests"][0]["name"], "ModB.test_heavy")

    def test_slowest_15_tests_capped_at_15(self):
        tests_list = [
            {"name": f"TestCls.test_{i}", "duration": float(i), "status": "ok"}
            for i in range(25)
        ]
        dummy_res = {
            "module": "tests.bulk",
            "duration": 100.0,
            "status": "ok",
            "returncode": 0,
            "test_count": 25,
            "timed_tests": tests_list,
            "output": "",
        }
        with mock.patch("slow_tests.run_test_module", return_value=dummy_res):
            agg = slow_tests.run_all_test_modules(["tests.bulk"])
            self.assertEqual(len(agg["slowest_15_tests"]), 15)
            self.assertEqual(agg["slowest_15_tests"][0]["duration"], 24.0)

    def test_generate_markdown_report_structure(self):
        report_data = {
            "modules_run": 2,
            "modules_over_20s": [{"module": "tests.mod_b", "duration": 25.1, "test_count": 1, "status": "ok"}],
            "module_results": [
                {"module": "tests.mod_a", "duration": 3.0, "test_count": 2, "status": "ok"},
                {"module": "tests.mod_b", "duration": 25.1, "test_count": 1, "status": "ok"},
            ],
            "slowest_modules": [
                {"module": "tests.mod_b", "duration": 25.1, "test_count": 1, "status": "ok"},
                {"module": "tests.mod_a", "duration": 3.0, "test_count": 2, "status": "ok"},
            ],
            "slowest_15_tests": [
                {"name": "ModB.test_b", "module": "tests.mod_b", "duration": 25.1, "status": "ok"},
                {"name": "ModA.test_a", "module": "tests.mod_a", "duration": 3.0, "status": "ok"},
            ],
        }
        md = slow_tests.generate_markdown_report(report_data, threshold_s=20.0)
        self.assertIn("# Test Suite Execution & Latency Report", md)
        self.assertIn("## Top 15 Slowest Tests", md)
        self.assertIn("## Modules Exceeding 20.0s", md)
        self.assertIn("`tests.mod_b`", md)
        self.assertIn("25.100s", md)

    def test_generate_markdown_report_empty(self):
        report_data = {
            "modules_run": 0,
            "modules_over_20s": [],
            "module_results": [],
            "slowest_modules": [],
            "slowest_15_tests": [],
        }
        md = slow_tests.generate_markdown_report(report_data)
        self.assertIn("No tests recorded", md)
        self.assertIn("None", md)

    def test_format_cli_summary_outputs_top_15_and_over_20s(self):
        report_data = {
            "modules_run": 2,
            "module_results": [
                {"module": "tests.mod_fast", "duration": 1.2, "test_count": 5, "status": "ok"},
                {"module": "tests.mod_heavy", "duration": 21.4, "test_count": 10, "status": "ok"},
            ],
            "slowest_15_tests": [
                {"name": "HeavyTest.test_heavy", "module": "tests.mod_heavy", "duration": 21.4, "status": "ok"},
            ],
        }
        cli_out = slow_tests.format_cli_summary(report_data, threshold_s=20.0)
        self.assertIn("TEST RUN TIME SUMMARY", cli_out)
        self.assertIn("HeavyTest.test_heavy", cli_out)
        self.assertIn("MODULES OVER 20.0s", cli_out)
        self.assertIn("21.40s - tests.mod_heavy", cli_out)

    def test_format_cli_summary_none_over_20s(self):
        report_data = {
            "modules_run": 1,
            "module_results": [
                {"module": "tests.mod_fast", "duration": 2.0, "test_count": 5, "status": "ok"},
            ],
            "slowest_15_tests": [
                {"name": "FastTest.test_1", "module": "tests.mod_fast", "duration": 2.0, "status": "ok"},
            ],
        }
        cli_out = slow_tests.format_cli_summary(report_data, threshold_s=20.0)
        self.assertIn("None (all modules completed under 20.0s)", cli_out)

    def test_report_file_atomic_write(self):
        report_data = {
            "modules_run": 1,
            "modules_over_20s": [],
            "module_results": [{"module": "tests.m", "duration": 1.0, "test_count": 1, "status": "ok"}],
            "slowest_modules": [{"module": "tests.m", "duration": 1.0, "test_count": 1, "status": "ok"}],
            "slowest_15_tests": [{"name": "M.t", "module": "tests.m", "duration": 1.0, "status": "ok"}],
        }
        out_file = os.path.join(self.tmp_dir.name, "subdir", "report.md")
        with mock.patch("slow_tests.run_all_test_modules", return_value=report_data):
            with mock.patch("sys.argv", ["slow_tests.py", "tests.m", "--report", out_file]):
                with mock.patch("builtins.print"):
                    slow_tests.main()
        self.assertTrue(os.path.isfile(out_file))
        with open(out_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("# Test Suite Execution & Latency Report", content)

    def test_main_cli_without_args_scans_tests(self):
        with mock.patch("slow_tests.run_all_test_modules") as mock_run:
            mock_run.return_value = {
                "modules_run": 0,
                "modules_over_20s": [],
                "module_results": [],
                "slowest_modules": [],
                "slowest_15_tests": [],
            }
            with mock.patch("sys.argv", ["slow_tests.py"]):
                with mock.patch("builtins.print"):
                    slow_tests.main()
            mock_run.assert_called_once()

    def test_run_test_module_custom_python_bin(self):
        with mock.patch("subprocess.run") as mock_sub:
            mock_sub.return_value = mock.Mock(returncode=0, stdout="")
            slow_tests.run_test_module("tests.test_x", python_bin="/custom/bin/python")
            cmd = mock_sub.call_args[0][0]
            self.assertEqual(cmd[0], "/custom/bin/python")

    def test_run_test_module_custom_cwd(self):
        with mock.patch("subprocess.run") as mock_sub:
            mock_sub.return_value = mock.Mock(returncode=0, stdout="")
            slow_tests.run_test_module("tests.test_x", cwd="/tmp/custom_cwd")
            cwd = mock_sub.call_args[1].get("cwd")
            self.assertEqual(cwd, "/tmp/custom_cwd")

    def test_empty_module_string_skipped(self):
        with mock.patch("slow_tests.run_test_module") as mock_run:
            slow_tests.run_all_test_modules(["", "   "])
            mock_run.assert_not_called()

    def test_multiple_tests_parse_accuracy(self):
        out = """test_a (pkg.clsA.test_a) ... ok
test_b (pkg.clsA.test_b) ... skipped 'reason'
test_c (pkg.clsA.test_c) ... expected failure
test_d (pkg.clsA.test_d) ... ERROR

Ran 4 tests in 4.000s
FAILED (errors=1)
"""
        timed, count = slow_tests.parse_unittest_output(out, 4.0)
        self.assertEqual(count, 4)
        statuses = [t["status"] for t in timed]
        self.assertEqual(statuses, ["ok", "skipped", "expected", "ERROR"])

    def test_markdown_report_formatting_safe_chars(self):
        report_data = {
            "modules_run": 1,
            "modules_over_20s": [],
            "module_results": [{"module": "tests.mod<name>", "duration": 1.0, "test_count": 1, "status": "ok"}],
            "slowest_modules": [{"module": "tests.mod<name>", "duration": 1.0, "test_count": 1, "status": "ok"}],
            "slowest_15_tests": [{"name": "Test<X>.fn", "module": "tests.mod<name>", "duration": 1.0, "status": "ok"}],
        }
        md = slow_tests.generate_markdown_report(report_data)
        self.assertIn("`tests.mod<name>`", md)

    def test_cli_summary_empty_tests_fallback(self):
        report_data = {
            "modules_run": 0,
            "module_results": [],
            "slowest_15_tests": [],
        }
        text = slow_tests.format_cli_summary(report_data)
        self.assertIn("No individual test details available", text)

    def test_threshold_parameter_respected(self):
        report_data = {
            "modules_run": 1,
            "module_results": [
                {"module": "tests.mod_10s", "duration": 12.0, "test_count": 1, "status": "ok"}
            ],
            "slowest_15_tests": [],
        }
        # With 10s threshold, 12s module should be flagged
        cli_out = slow_tests.format_cli_summary(report_data, threshold_s=10.0)
        self.assertIn("MODULES OVER 10.0s", cli_out)
        self.assertIn("tests.mod_10s", cli_out)

    def test_no_money_amounts_in_output(self):
        report_data = {
            "modules_run": 1,
            "module_results": [{"module": "tests.m", "duration": 1.0, "test_count": 1, "status": "ok"}],
            "slowest_modules": [{"module": "tests.m", "duration": 1.0, "test_count": 1, "status": "ok"}],
            "slowest_15_tests": [{"name": "M.t", "module": "tests.m", "duration": 1.0, "status": "ok"}],
        }
        md = slow_tests.generate_markdown_report(report_data)
        cli = slow_tests.format_cli_summary(report_data)
        for s in (md, cli):
            self.assertNotIn("$", s)
            self.assertNotIn("USD", s)
            self.assertNotIn("INR", s)
            self.assertNotIn("Rs", s)


if __name__ == "__main__":
    unittest.main()
