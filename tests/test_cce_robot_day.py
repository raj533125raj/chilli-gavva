"""CCE-4: a short simulated robot day in CI (3 simulated hours), so a future crash is caught before the owner
sees it. The real robot code runs, step by step like .github/workflows/agent.yml, in a temp copy with a fake
Telegram, fake AI answers, fake Cloudflare and a fake clock (tools/robot_day_sim.py). Nothing is written into
the repo folder and no real network, secret or database is used.

Two days are simulated:
  - a normal day: drafts are made, the owner approves with the real buttons, products are built, 0 errors;
  - a bad day: state files cut in half and a clock that jumps back -> no crash, one Tenglish line each,
    and the run after it is clean.
"""
import importlib.util
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    import PIL  # noqa: F401  (the product builders need Pillow; CI installs it)
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False
HAVE_BASH = shutil.which("bash") is not None
CAN_RUN = HAVE_PIL and HAVE_BASH and sys.version_info >= (3, 10)
WHY = "needs Pillow, bash and Python 3.10+ (like CI)"


def _sim():
    spec = importlib.util.spec_from_file_location("_cce4_robot_day_sim", ROOT / "tools" / "robot_day_sim.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _repo_state():
    """What git sees in the repo folder (changed + new files). Empty text when git is not there."""
    try:
        return subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT), stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, check=True).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.CalledProcessError):
        return ""


def _errors_text(sim, report):
    return sim.text_report(report, verbose=True)


@unittest.skipUnless(CAN_RUN, WHY)
class ShortRobotDayTests(unittest.TestCase):
    """3 simulated hours, one run per hour, every step of the cloud cycle."""

    @classmethod
    def setUpClass(cls):
        cls.sim = _sim()
        cls.before = _repo_state()
        cls.report = cls.sim.simulate(hours=3, every=60)
        cls.after = _repo_state()

    def test_no_error_no_traceback_no_failed_run(self):
        self.assertEqual(self.report["errors"], [], _errors_text(self.sim, self.report))
        self.assertEqual(self.report["failed_runs"], 0)
        self.assertEqual(self.report["runs"], 3)

    def test_telegram_accepted_every_message(self):
        self.assertEqual(self.report["telegram_refused"], 0)
        self.assertGreater(self.report["telegram_sent"], 20)

    def test_owner_saw_no_problem_looking_message(self):
        self.assertEqual(self.report["owner_error_lines"], [])

    def test_only_fake_hosts_were_reached(self):
        # the trend feeds are refused by the sandbox and listed; nothing else may try to leave
        allowed = {"github.blog", "huggingface.co", "news.ycombinator.com", "techcrunch.com", "www.producthunt.com"}
        self.assertLessEqual(set(self.report["blocked_hosts"]), allowed)

    def test_the_day_really_happened(self):
        report = self.report
        self.assertGreater(report["llm_calls"], 10)                       # drafts were written by the (fake) AI
        self.assertGreaterEqual(report["drafts"].get("approved", 0), 2)   # owner pressed the real buttons
        self.assertGreaterEqual(report["drafts"].get("rejected", 0), 1)
        self.assertGreaterEqual(len(report["approved_niches"]), 2)
        built = [b for b in report["builds"] if b["status"] == "built"]
        self.assertGreaterEqual(len(built), 2, report["builds"])          # two build slots a day
        for build in built:
            self.assertIn("READY", build["note"])
        self.assertGreater(len(report["owner_commands"]), 40)             # every core + plugin command was sent

    def test_every_cycle_step_ran_in_every_run(self):
        names = {step["name"] for step in self.sim.cycle_steps(sys.executable)}
        self.assertGreaterEqual(self.report["steps"], 3 * len(names))

    def test_repo_folder_untouched(self):
        self.assertEqual(self.before, self.after)


@unittest.skipUnless(CAN_RUN, WHY)
class ShortBadDayTests(unittest.TestCase):
    """Run 2 starts with half-written state files and a clock that went 3 hours back; run 3 must be clean."""

    @classmethod
    def setUpClass(cls):
        cls.sim = sim = _sim()

        def trouble(paths, env):
            for rel in ("exported_content/products/post_plan.json", "web/data/status.json"):
                sim._half_write(paths["code"] / rel)
            return -3 * 3600.0

        cls.scenarios = [(1, 1, "half_state_and_clock_back", "state files cut in half + clock 3 h back")]
        cls.report = sim.simulate(hours=3, every=60, events={1: trouble}, scenarios=cls.scenarios)
        cls.scenario = cls.report["scenarios"][0]
        cls.texts = [m["text"] for m in cls.scenario["owner_messages"]]

    def test_no_crash_and_the_next_run_is_clean(self):
        self.assertEqual(self.report["errors"], [], _errors_text(self.sim, self.report))
        self.assertEqual(self.report["failed_runs"], 0)
        self.assertEqual(self.scenario["failed_steps"], [])
        self.assertTrue(self.scenario["next_run_clean"])

    def test_one_tenglish_line_for_the_damaged_state(self):
        lines = [t for t in self.texts if "sagam matrame raasi undi" in t]
        self.assertEqual(len(lines), 1, self.texts)
        self.assertIn("status.json", lines[0])

    def test_one_tenglish_line_for_the_clock(self):
        lines = [t for t in self.texts if "clock venakki velindi" in t]
        self.assertEqual(len(lines), 1, self.texts)

    def test_the_robot_kept_working(self):
        self.assertGreaterEqual(self.report["drafts"].get("approved", 0), 2)
        self.assertTrue(any(b["status"] == "built" for b in self.report["builds"]), self.report["builds"])


if __name__ == "__main__":
    unittest.main()
