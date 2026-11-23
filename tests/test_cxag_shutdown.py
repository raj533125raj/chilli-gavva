"""Queue inspection must not pull the UI stack into reloadable workers."""
from pathlib import Path
import os
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WorkerShutdownTests(unittest.TestCase):
    def test_reloaded_memory_workers_exit_cleanly(self):
        env = dict(os.environ, CHILLI_TEST="1", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_memory_web_wire"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Ran 16 tests", result.stderr)

    def test_queue_policy_import_does_not_load_telegram(self):
        result = subprocess.run(
            [sys.executable, "-c", "import auto_approve, sys; "
             "assert 'telegram_inbox' not in sys.modules"],
            cwd=ROOT, env=dict(os.environ, CHILLI_TEST="1"),
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
