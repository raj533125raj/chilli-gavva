"""CCI-3: the normal run builds one buyer per product type; CHILLI_SLOW_TESTS=1 builds all three.

The helpers are checked on their own. The slow path itself is run (one product type, all three buyers,
18 tests) only when CHILLI_SLOW_TESTS=1 is set, so the nightly / manual run proves it still works.
"""
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_och_niche_sweep as sweep  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
import run_tests_chunked  # noqa: E402


class SlowFlagHelpersTests(unittest.TestCase):
    def test_normal_run_builds_one_buyer(self):
        self.assertEqual(sweep.buyers_for({}), (sweep.FAST_BUYER,))
        self.assertEqual(sweep.buyers_for({"CHILLI_SLOW_TESTS": "0"}), (sweep.FAST_BUYER,))

    def test_slow_flag_builds_all_three_buyers(self):
        self.assertEqual(sweep.buyers_for({"CHILLI_SLOW_TESTS": "1"}), sweep.BUYER_KEYS)
        self.assertTrue(sweep.slow_mode({"CHILLI_SLOW_TESTS": " 1 "}))
        self.assertFalse(sweep.slow_mode({"CHILLI_SLOW_TESTS": "yes"}))

    def test_fast_run_has_one_method_per_check_per_kind(self):
        cls = sweep._make_kind_class(sweep.FORMAT_ORDER[0]) if not sweep.slow_mode() else None
        if cls is None:
            self.skipTest("fast-mode shape only applies without CHILLI_SLOW_TESTS=1")
        tests = [name for name in dir(cls) if name.startswith("test_")]
        self.assertEqual(len(tests), len(sweep.CHECKS) * len(sweep.BUYER_KEYS[:1]))


@unittest.skipUnless(sweep.slow_mode(), "slow path: set CHILLI_SLOW_TESTS=1 (nightly / manual run)")
class ChunkSizeTests(unittest.TestCase):
    def test_default_chunk_keeps_heavy_modules_apart(self):
        # the slow real-pack / simulation modules must not all land in one 300 s chunk
        self.assertLessEqual(run_tests_chunked.DEFAULT_CHUNK_SIZE, 10)


class SlowPathTests(unittest.TestCase):
    def test_full_three_buyer_sweep_runs_for_one_kind(self):
        env = dict(os.environ, CHILLI_SLOW_TESTS="1", CHILLI_TEST="1")
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_och_niche_sweep.TestOch1Sheets"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=900)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output[-2000:])
        self.assertIsNotNone(re.search(r"^Ran 18 tests", output, re.M), output[-500:])


if __name__ == "__main__":
    unittest.main()
