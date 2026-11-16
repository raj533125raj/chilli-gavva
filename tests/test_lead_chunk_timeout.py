"""Lead fix (2026-11-16): a timed-out test chunk must be reported, not crash the runner.

agent-cycle #951-#953 failed with "TypeError: cannot use a string pattern on a bytes-like object":
subprocess hands the partial output of a timed-out child over as BYTES even with text=True, and
parse_unittest_output() then crashed, hiding the real reason (one chunk took longer than its limit).
"""
import importlib.util
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("rtc_lead", ROOT / "tools" / "run_tests_chunked.py")
rtc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rtc)


def _timeout_runner(partial):
    def runner(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"), output=partial)
    return runner


class ChunkTimeoutTests(unittest.TestCase):
    def test_bytes_partial_output_is_reported_not_crashing(self):
        partial = b"test_a (tests.test_x.T.test_a) ... ok\nFAIL: test_b (tests.test_x.T.test_b)\n"
        res = rtc.run_chunk(["tests.test_x"], timeout=1, runner=_timeout_runner(partial), log_func=lambda *a: None)
        self.assertTrue(res["timed_out"])
        self.assertEqual(res["returncode"], 124)
        self.assertIsInstance(res["output"], str)
        self.assertIn("test_b", res["output"])

    def test_no_partial_output_still_reports_a_failure(self):
        res = rtc.run_chunk(["tests.test_y"], timeout=2, runner=_timeout_runner(None), log_func=lambda *a: None)
        self.assertTrue(res["timed_out"])
        self.assertTrue(res["parsed"]["failing_tests"], "a timeout must count as a failure")

    def test_chunk_limit_gives_github_room(self):
        self.assertGreaterEqual(rtc.DEFAULT_TIMEOUT_PER_CHUNK, 900)


if __name__ == "__main__":
    unittest.main()
