"""Tests for always_on.py (offline: the Worker is a fake opener)."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import always_on  # noqa: E402


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.requests = reply, error, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.error:
            raise self.error
        return FakeResp(json.dumps(self.reply).encode())


class SettingsTests(unittest.TestCase):
    def test_env_wins_over_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w") as f:
                json.dump({"always_on_url": "https://cfg.example/", "always_on_secret": "cfg"}, f)
            self.assertEqual(always_on.settings(path, env={}), ("https://cfg.example", "cfg"))
            self.assertEqual(always_on.settings(path, env={"ALWAYS_ON_URL": "https://env.example",
                                                           "ALWAYS_ON_SECRET": "env"}),
                             ("https://env.example", "env"))

    def test_missing_settings(self):
        self.assertEqual(always_on.settings("/nonexistent.json", env={}), ("", ""))


class CallTests(unittest.TestCase):
    def test_not_set_up_is_a_no_op(self):
        opener = FakeOpener({"ok": True})
        self.assertIsNone(always_on.call("/status", url="", secret="", opener=opener))
        self.assertEqual(opener.requests, [])

    def test_post_sends_secret_in_header_only(self):
        opener = FakeOpener({"mac_alive": False})
        reply = always_on.run_started("alarm", url="https://clock.example", secret="s3cret", opener=opener)
        self.assertEqual(reply, {"mac_alive": False})
        req = opener.requests[0]
        self.assertEqual(req.full_url, "https://clock.example/run")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.headers["Authorization"], "Bearer s3cret")
        self.assertNotIn("s3cret", req.full_url)
        self.assertEqual(json.loads(req.data), {"phase": "start", "reason": "alarm"})

    def test_errors_and_bad_replies_become_none(self):
        kw = {"url": "https://clock.example", "secret": "x"}
        self.assertIsNone(always_on.status(opener=FakeOpener(error=OSError("offline")), **kw))
        self.assertIsNone(always_on.status(opener=FakeOpener(reply=[1, 2]), **kw))

    def test_heartbeat_and_finish_payloads(self):
        opener = FakeOpener({"ok": True})
        kw = {"url": "https://c.example", "secret": "x", "opener": opener}
        always_on.heartbeat(stopping=True, **kw)
        always_on.run_finished(3, **kw)
        self.assertEqual([json.loads(r.data) for r in opener.requests],
                         [{"stopping": True}, {"phase": "end", "minutes": 3}])


class GateTests(unittest.TestCase):
    def test_gate_decisions(self):
        self.assertEqual(always_on.gate("alarm", start=lambda r: None)[0], True)        # fail open
        self.assertEqual(always_on.gate("alarm", start=lambda r: {"mac_alive": False})[0], True)
        self.assertEqual(always_on.gate("alarm", start=lambda r: {"mac_alive": True})[0], False)
        self.assertEqual(always_on.gate("manual", force=True,
                                        start=lambda r: {"mac_alive": True})[0], True)

    def test_billed_minutes(self):
        self.assertEqual(always_on.billed_minutes(1000, now=1000), 1)
        self.assertEqual(always_on.billed_minutes(1000, now=1035), 1)   # 35 s + 20 s set-up
        self.assertEqual(always_on.billed_minutes(1000, now=1045), 2)
        self.assertEqual(always_on.billed_minutes(1000, now=900), 1)    # clock skew never goes below 1

    def test_cli_writes_github_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, envf = os.path.join(tmp, "out"), os.path.join(tmp, "env")
            original = always_on.run_started
            always_on.run_started = lambda reason, **kw: {"mac_alive": True}
            try:
                with redirect_stdout(io.StringIO()) as printed:
                    always_on.main(["gate", "--reason", "alarm"], env={"GITHUB_OUTPUT": out, "GITHUB_ENV": envf})
            finally:
                always_on.run_started = original
            with open(out) as f:
                self.assertEqual(f.read(), "run=false\n")
            with open(envf) as f:
                self.assertRegex(f.read(), r"^RUN_STARTED=\d+\n$")
            self.assertIn("SKIP", printed.getvalue())

    def test_cli_report_without_clock(self):
        original = always_on.settings
        always_on.settings = lambda *a, **k: ("", "")
        try:
            with redirect_stdout(io.StringIO()) as printed:
                always_on.main(["report"], env={"RUN_STARTED": "not-a-number"})
        finally:
            always_on.settings = original
        self.assertIn("not set up", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
