"""CCE-1: the day simulator's fake world and sandbox (fast checks; the short real day is in test_cce_robot_day)."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = str(int("1234" "5678"))


def _load(name):
    spec = importlib.util.spec_from_file_location("_cce1_" + name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTelegramTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.world = _load("robot_sim_world")
        self.world.SIM_DIR = self.tmp.name
        self.world.OWNER_CHAT = OWNER

    def call(self, method, params):
        req = urllib.request.Request(f"https://api.telegram.org/bot1:abc/{method}",
                                     data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
        with self.world.fake_urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())

    def outbox(self):
        with open(os.path.join(self.tmp.name, "telegram_out.jsonl"), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def test_send_message_is_logged_without_token(self):
        reply = self.call("sendMessage", {"chat_id": OWNER, "text": "Namaste",
                                          "reply_markup": {"inline_keyboard": [[{"text": "OK", "callback_data": "a:5"}]]}})
        self.assertTrue(reply["ok"])
        sent = self.outbox()[-1]
        self.assertEqual((sent["method"], sent["text"], sent["buttons"][0]["data"]), ("sendMessage", "Namaste", "a:5"))
        self.assertNotIn("abc", json.dumps(sent))

    def test_real_telegram_limits_give_400(self):
        cases = [{"chat_id": OWNER, "text": "x" * 4097}, {"chat_id": OWNER, "text": "   "},
                 {"chat_id": "999", "text": "hi"},
                 {"chat_id": OWNER, "text": "hi",
                  "reply_markup": {"inline_keyboard": [[{"text": "B", "callback_data": "d" * 65}]]}}]
        for params in cases:
            with self.subTest(params=str(params)[:60]):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.call("sendMessage", params)
                self.assertEqual(caught.exception.code, 400)
        self.assertTrue(all(m["ok"] is False for m in self.outbox()))

    def test_get_updates_confirms_with_offset(self):
        queue = [{"update_id": 7, "message": {"text": "/list"}}, {"update_id": 8, "message": {"text": "/status"}}]
        Path(self.tmp.name, "tg_updates.json").write_text(json.dumps(queue), encoding="utf-8")
        self.assertEqual(len(self.call("getUpdates", {})["result"]), 2)
        self.assertEqual([u["update_id"] for u in self.call("getUpdates", {"offset": 8})["result"]], [8])
        self.assertEqual(self.call("getUpdates", {"offset": 9})["result"], [])

    def test_unknown_hosts_are_refused_and_logged(self):
        with self.assertRaises(urllib.error.URLError):
            self.world.fake_urlopen("https://example.com/page")
        with open(os.path.join(self.tmp.name, "blocked.jsonl"), encoding="utf-8") as handle:
            self.assertEqual(json.loads(handle.readline())["host"], "example.com")

    def test_fault_rules_count_down(self):
        Path(self.tmp.name, "faults.json").write_text(json.dumps(
            [{"service": "telegram", "status": 429, "times": 1, "retry_after": 0}]), encoding="utf-8")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call("getMe", {})
        self.assertEqual(caught.exception.code, 429)
        self.assertTrue(self.call("getMe", {})["ok"])   # used up: next call works

    def test_llm_answers_in_provider_shapes(self):
        body = json.dumps({"messages": [{"role": "user", "content": "You are the QA Checker ... Draft:"}]}).encode()
        req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=body)
        with self.world.fake_urlopen(req) as resp:
            text = json.loads(resp.read().decode())["choices"][0]["message"]["content"]
        self.assertEqual(json.loads(text)["verdict"], "pass")
        native = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key=k",
            data=json.dumps({"contents": [{"parts": [{"text": "PRODUCT NAME budget"}]}]}).encode())
        with self.world.fake_urlopen(native) as resp:
            self.assertIn("PRODUCT NAME", json.loads(resp.read().decode())["candidates"][0]["content"]["parts"][0]["text"])

    def test_daily_report_receipt_ref_only_once(self):
        def create():
            req = urllib.request.Request("https://api.github.com/repos/sim/robot/git/refs", method="POST",
                                         data=json.dumps({"ref": "refs/tags/robot-daily-report/2026-11-16"}).encode())
            with self.world.fake_urlopen(req) as resp:
                return json.loads(resp.read().decode())
        self.assertEqual(create()["ref"], "refs/tags/robot-daily-report/2026-11-16")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            create()
        self.assertEqual(caught.exception.code, 422)


class InstalledWorldTests(unittest.TestCase):
    """install() patches a whole process, so it is checked in a child process."""

    def test_clock_network_and_caught_errors_in_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim = Path(tmp, "sim")
            hooks = Path(tmp, "hooks")
            sim.mkdir()
            hooks.mkdir()
            hooks.joinpath("robot_sim_world.py").write_bytes((ROOT / "tools" / "robot_sim_world.py").read_bytes())
            hooks.joinpath("sitecustomize.py").write_text(
                "import os\nif os.environ.get('ROBOT_SIM_DIR'):\n    import robot_sim_world\n"
                "    robot_sim_world.install()\n", encoding="utf-8")
            script = ("import datetime, socket, time, urllib.request\n"
                      "print(datetime.datetime.now(datetime.timezone.utc).date(), datetime.date.today(),\n"
                      "      time.strftime('%Y'), isinstance(datetime.datetime(2020, 1, 1), datetime.datetime))\n"
                      "try:\n    {}['missing']\nexcept Exception as exc:\n    print('Thing skipped:', type(exc).__name__)\n"
                      "try:\n    socket.create_connection(('203.0.113.9', 80), timeout=1)\n"
                      "except OSError as exc:\n    print('socket refused', exc.errno)\n")
            env = dict(os.environ, ROBOT_SIM_DIR=str(sim), ROBOT_SIM_NOW="1794787200",   # 2026-11-16 00:00 UTC
                       PYTHONPATH=str(hooks), ROBOT_SIM_STEP="t", TZ="UTC")
            out = subprocess.run([sys.executable, "-c", script], env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, timeout=60).stdout.decode()
            self.assertIn("2026-11-16 2026-11-16 2026 True", out)
            self.assertIn("socket refused 101", out)
            errors = [json.loads(line) for line in sim.joinpath("errors.jsonl").read_text().splitlines()]
            self.assertEqual((errors[0]["kind"], errors[0]["type"]), ("caught", "KeyError"))
            self.assertTrue(list(sim.joinpath("proc").iterdir()))   # output copy for run_failure-hidden steps


class SandboxTests(unittest.TestCase):
    def test_sandbox_never_holds_state_secrets_or_tests(self):
        sim = _load("robot_day_sim")
        names = sim.tracked_files()
        self.assertIn("telegram_inbox.py", names)
        self.assertIn("tools/sell_proof.py", names)
        for name in names:
            base = name.rsplit("/", 1)[-1]
            self.assertFalse(base.startswith("agent_data.db") or base in ("config.json", "products.tar.gz.enc"), name)
            self.assertFalse(name.startswith(("tests/", ".jobs/", ".github/")), name)

    def test_fake_secrets_have_the_checked_shapes_and_change(self):
        sim = _load("robot_day_sim")
        import telegram_diagnostics
        values = sim.fake_secrets()
        self.assertEqual(telegram_diagnostics.secret_problems(values), [])
        self.assertNotEqual(values["CF_API_TOKEN"], sim.fake_secrets()["CF_API_TOKEN"])
        self.assertEqual(values["TELEGRAM_CHAT_ID"], OWNER)

    def test_keep_folder_inside_repo_is_refused(self):
        sim = _load("robot_day_sim")
        with self.assertRaises(SystemExit):
            sim.simulate(hours=1, keep=str(ROOT / "sim_out"))
        self.assertFalse((ROOT / "sim_out").exists())

    def test_report_text_lists_errors_with_file_and_line(self):
        sim = _load("robot_day_sim")
        report = {"runs": 1, "steps": 3, "failed_runs": 0, "telegram_sent": 2, "telegram_refused": 0,
                  "llm_calls": 1, "llm_labels": {}, "drafts": {}, "approved_niches": [], "builds": [],
                  "blocked_hosts": [], "environment": [], "owner_error_lines": [], "timeline": [],
                  "slow_steps": [], "errors": [{"kind": "crash", "type": "KeyError", "file": "x.py", "line": 7,
                                                "count": 2, "steps": ["inbox"], "t": "T", "message": "'k'"}]}
        text = sim.text_report(report)
        self.assertIn("[crash] KeyError at x.py:7 x2", text)
        self.assertIn("RESULT: 1 error(s)", text)


if __name__ == "__main__":
    unittest.main()
