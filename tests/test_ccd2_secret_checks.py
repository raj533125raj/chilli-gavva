"""CCD-2: clear secret checks at the start of a run, never printing a secret value.

Fake values only. A wrongly shaped CF_KV_NAMESPACE_ID gives one Tenglish Telegram message (once a day, CCD-1)
and the KV (office control inbox) step is skipped quietly instead of failing on every run.
"""
import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import telegram_diagnostics as diagnostics
import telegram_inbox

OWNER = int("1234" "5678")
HEX_A = "0123456789abcdef" * 2
HEX_B = "fedcba9876543210" * 2
TOKEN = "Abc_def-" + "x" * 32
KV_MESSAGE = ("GitHub Secret CF_KV_NAMESPACE_ID tappu ga undi: Cloudflare > Storage > KV lo namespace ID "
              "(32 letters/numbers) copy chesi pettandi.")
GOOD = {"CF_ACCOUNT_ID": HEX_A, "CF_KV_NAMESPACE_ID": HEX_B, "CF_API_TOKEN": TOKEN,
        "CONTROL_SIGNING_KEY": "office-signing-key-fake", "ALWAYS_ON_URL": "https://bell.example.workers.dev",
        "ALWAYS_ON_SECRET": "fake-worker-secret-1"}


class SecretShapeTests(unittest.TestCase):
    def test_good_values_have_no_problem(self):
        self.assertEqual(diagnostics.secret_problems(GOOD), [])
        self.assertEqual(diagnostics.secret_problems(dict(GOOD, CF_KV_NAMESPACE_ID="  " + HEX_B + "\n")), [])
        self.assertEqual(diagnostics.secret_problems(dict(GOOD, CF_ACCOUNT_ID=HEX_A.upper())), [])

    def test_missing_optional_secrets_are_quiet(self):
        self.assertEqual(diagnostics.secret_problems({}), [])
        self.assertEqual(diagnostics.secret_problems({"CF_KV_NAMESPACE_ID": "   ", "ALWAYS_ON_URL": ""}), [])

    def test_wrong_namespace_shapes(self):
        for bad in ("my-namespace", HEX_B[:31], HEX_B + "0", "g" * 32,
                    "https://dash.cloudflare.com/x/workers/kv/namespaces/" + HEX_B, '"' + HEX_B + '"'):
            problems = diagnostics.secret_problems(dict(GOOD, CF_KV_NAMESPACE_ID=bad))
            self.assertEqual(problems, [("CF_KV_NAMESPACE_ID", KV_MESSAGE)], bad)
            self.assertFalse(diagnostics.kv_secrets_ok(problems))

    def test_other_optional_secrets(self):
        cases = {"CF_ACCOUNT_ID": "acct-123", "CF_API_TOKEN": "Bearer " + TOKEN,
                 "ALWAYS_ON_URL": "bell.example.workers.dev", "ALWAYS_ON_SECRET": "two words"}
        for name, bad in cases.items():
            problems = diagnostics.secret_problems(dict(GOOD, **{name: bad}))
            self.assertEqual([p[0] for p in problems], [name])
            self.assertTrue(problems[0][1].startswith(f"GitHub Secret {name} tappu ga undi: "))
            self.assertNotIn(bad, problems[0][1])
        self.assertTrue(diagnostics.kv_secrets_ok(
            diagnostics.secret_problems(dict(GOOD, ALWAYS_ON_URL="nope"))))

    def test_message_is_an_error_so_it_goes_once_a_day(self):
        self.assertTrue(diagnostics.looks_like_error(KV_MESSAGE))


class RunStartTests(unittest.TestCase):
    """The real telegram_inbox._run start, with fake Telegram and a fake control inbox."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = str(Path(temp.name) / "robot.db")
        self.sent = []
        self.kv_runs = []
        fake_control = types.ModuleType("control_inbox")
        fake_control.run = lambda db_path=None: self.kv_runs.append(db_path)

        class Stop(Exception):
            pass
        self.Stop = Stop

        def stop(*a, **k):
            raise Stop()

        patches = [
            mock.patch.dict(sys.modules, {"control_inbox": fake_control}),
            mock.patch("notify._telegram_configured", return_value=(True, "123:abc", str(OWNER))),
            mock.patch.object(telegram_inbox, "_call", return_value={"ok": True, "result": {"url": ""}}),
            mock.patch.object(telegram_inbox, "send_text",
                              side_effect=lambda t, c, text, buttons=None, keyboard=None:
                              self.sent.append(text) or True),
            mock.patch.object(telegram_inbox, "process_updates", side_effect=stop),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_start(self, env, recovery=False):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            with self.assertRaises(self.Stop):
                telegram_inbox._run(self.db, recovery=recovery)
        return out.getvalue()

    def test_wrong_namespace_skips_kv_and_tells_owner_once(self):
        bad = "not-a-namespace-" + "9" * 7
        env = dict(GOOD, CF_KV_NAMESPACE_ID=bad)
        log1 = self.run_start(env)
        log2 = self.run_start(env)
        self.assertEqual(self.kv_runs, [])
        self.assertEqual(self.sent, [KV_MESSAGE])
        for text in (log1, log2, " ".join(self.sent)):
            self.assertNotIn(bad, text)
            self.assertNotIn(HEX_A, text)
            self.assertNotIn(TOKEN, text)
        self.assertIn("Control inbox skipped", log1)
        self.assertIn("CF_KV_NAMESPACE_ID has the wrong format (value not shown)", log1)

    def test_good_secrets_run_kv_and_say_nothing(self):
        log = self.run_start(GOOD)
        self.assertEqual(len(self.kv_runs), 1)
        self.assertEqual(self.sent, [])
        self.assertNotIn("Secret check", log)

    def test_wrong_optional_non_kv_secret_still_runs_kv(self):
        self.run_start(dict(GOOD, ALWAYS_ON_URL="bell"))
        self.assertEqual(len(self.kv_runs), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].startswith("GitHub Secret ALWAYS_ON_URL tappu ga undi"))

    def test_two_wrong_secrets_one_message(self):
        self.run_start(dict(GOOD, CF_KV_NAMESPACE_ID="x", CF_ACCOUNT_ID="y"))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("CF_KV_NAMESPACE_ID", self.sent[0])
        self.assertIn("CF_ACCOUNT_ID", self.sent[0])

    def test_recovery_run_sends_no_secret_message(self):
        self.run_start(dict(GOOD, CF_KV_NAMESPACE_ID="x"), recovery=True)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.kv_runs, [])


if __name__ == "__main__":
    unittest.main()
