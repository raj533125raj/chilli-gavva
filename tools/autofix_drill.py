"""tools/autofix_drill.py — the auto-fix drill kit (CCF-1).

Breaks a TEMP git copy of the robot in 10 typical ways, one by one, and plays the whole auto-fix
story offline for each one, with the robot's REAL code:

  failing robot run (run_failure.py run / finish, twice)  ->  Telegram alert + auto-fix request
  ->  the "Robot auto-fix" workflow (autofix.py prompt / result-start / gemini-run / result-check /
      prepare-checks, autofix_retry.py record)  ->  agent.yml "autofix_result" (autofix_retry.py receive)
  ->  the "Robot auto-fix checks" workflow (autofix.py validate-checks + the tests on the fix commit)
  ->  Telegram inbox (Merge / Close buttons), the owner presses Merge  ->  next robot run is green.

What is real: every command comes from the `run:` lines of the copy's own workflow files
(.github/workflows/agent.yml, autofix.yml, autofix-check.yml) and runs as its own process, like a
GitHub step. This tool only plays GitHub Actions: it starts the jobs in order, passes step outputs
on, keeps the failure-counter cache and applies a merge the robot asked for.

What is fake (tools/robot_sim_world.py): Telegram, the GitHub REST API (pull requests are real
branches in a bare git repo inside the sandbox) and the clock. The AI is a small stand-in program
named `opencode` that gives a RECORDED answer: the known right fix for that failure plus a
regression test, pushed as a branch and opened as a pull request, the way the real tool does it.
No network, no real token, nothing written outside the temp folder.

Scaled down so a drill takes seconds: the copy holds only the test files a drill needs (--full
copies all tests), and the test runner's chunk limit is short (CHILLI_TEST_CHUNK_TIMEOUT: 3 s in
the drill whose test never ends, instead of 8 min). The state-restore lines of two steps (they need the encrypted
database) are left out; the drill database is a fresh one.

Report, one row per drill:
  found?        the owner alert names the step and the real cause (test / file)
  route         auto-fix started / handed to the owner / quiet retry — and is that the right one
  fix branch?   a pull request from the robot account, from the broken main, only the right files,
                with a regression test, accepted by the robot's own scope check
  tests green?  the checks on the fix commit pass and the next robot run after the merge is green
  owner msg?    the Telegram messages are clear (step, cause, what happens next, Tenglish, no paths)

Usage:
  python3 tools/autofix_drill.py                 # all 10 drills
  python3 tools/autofix_drill.py --only flaky_test,money_string
  python3 tools/autofix_drill.py --list
  python3 tools/autofix_drill.py --json
  python3 tools/autofix_drill.py --keep /tmp/drill   # keep the sandboxes (outside the repo)
Exit code: 0 = every drill ended as expected, 1 = at least one did not.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import robot_day_sim as sim   # noqa: E402  (sandbox helpers: fake secrets, jsonl reader)

REPO = "sim/robot"
START = "2026-11-16T06:05:00Z"          # a Monday morning (UTC)
NEVER_COPIED = {"agent_data.db", "agent_data.db.enc", "products.tar.gz.enc", "config.json", ".env"}
QUICK_STEP_TIMEOUT = 240                # seconds; the real "Run tests first" limit is 28 minutes
QUICK_CHUNK_TIMEOUT = 120               # seconds; the real chunk limit is 8 minutes
HANG_CHUNK_TIMEOUT = 3                 # the drill whose test never ends: do not wait 2 minutes for it
FULL_STEP_TIMEOUT = 28 * 60
TENGLISH = re.compile(r"(?i)\b(cheyandi|chestondi|chestundi|chesindi|ayyindi|kavali|meeru|vaddu|undi|chudandi|"
                      r"kaaledu|avutundi|cheyakkarledu|cheyali|ledu|vachindi|pampistundi)\b")
MONEY = chr(36) + "1" "9"               # built at run time: no money amount is written in this file

# What each agent.yml step is called (step key -> `- name:` in the workflow).
AGENT_STEPS = {
    "tests": "Run tests first",
    "inbox": "Telegram inbox (buttons, commands, new draft alerts, daily report)",
    "export": "Export approved content",
    "finish": "Update failure counter and alert on a repeated failure",
    "recovery": "Handle owner buttons even while the main tests are failing",
    "receive": "Schedule a patient retry or report a terminal failure",
}
FIX_STEPS = {
    "prompt": "Prepare safe request",
    "gemini": "Find the cause and propose a fix with Gemini",
    "verify_gemini": "Verify the Gemini proposal on GitHub",
    "verify_nim": "Verify the NVIDIA proposal on GitHub",
    "verify_groq": "Verify the final proposal on GitHub",
    "final": "Check that a provider completed the fix",
    "prepare": "Validate the proposed files and request tests",
    "record": "Record only the safe auto-fix outcome",
}
CHECK_STEPS = {
    "validate": "Check the request using trusted code",
    "tests": "Run the full suite without production secrets",
}


# ---- the 10 drills -----------------------------------------------------------------------------
def _regression(name: str, body: str) -> Dict[str, str]:
    return {f"tests/{name}.py": '"""Regression test added with the auto-fix."""\nimport unittest\n\n\n' + body}


FLAKY_TEST = '''"""A test that depends on something the run before it left behind (a typical flaky test)."""
import pathlib
import unittest


class LeftoverStateTests(unittest.TestCase):
    def test_passes_only_after_the_first_run(self):
        mark = pathlib.Path.home() / "drill-first-run-done"
        seen = mark.exists()
        mark.write_text("seen", encoding="utf-8")
        self.assertTrue(seen, "first run on a fresh machine: the leftover file is not there yet")
'''

SCENARIOS: List[Dict[str, Any]] = [
    {
        "key": "import_error",
        "title": "ImportError (a module imports a name that does not exist)",
        "inject": [("tg_bundle.py", "import zipfile\nfrom pathlib import Path\n",
                    "import zipfile\nfrom pathlib import Path\nfrom notify import send_bundle_ready_line\n")],
        "tests": ["test_ccb3_bundle"],
        "steps": ["tests"],
        "expect": "autofix",
        "found": r"test_ccb3_bundle",
        "answer": {
            "edits": [("tg_bundle.py", "from notify import send_bundle_ready_line\n", "")],
            "files": _regression("test_autofix_bundle_imports", (
                "class BundleImportTests(unittest.TestCase):\n"
                "    def test_bundle_plugin_loads(self):\n"
                "        import tg_bundle\n"
                "        self.assertTrue(callable(tg_bundle.handle))\n")),
        },
    },
    {
        "key": "failing_test",
        "title": "A failing test (a wrong number in product code)",
        "inject": [("state_guard.py", "BACK_SECONDS = 10 * 60 ", "BACK_SECONDS = 10 * 60 * 60 ")],
        "tests": ["test_cce3_bad_day"],
        "steps": ["tests"],
        "expect": "autofix",
        "found": r"test_cce3_bad_day\.\w+\.test_jump_back",
        "answer": {
            "edits": [("state_guard.py", "BACK_SECONDS = 10 * 60 * 60 ", "BACK_SECONDS = 10 * 60 ")],
            "files": _regression("test_autofix_clock_back_limit", (
                "class ClockBackLimitTests(unittest.TestCase):\n"
                "    def test_limit_is_ten_minutes(self):\n"
                "        import state_guard\n"
                "        self.assertEqual(state_guard.BACK_SECONDS, 600)\n")),
        },
    },
    {
        "key": "timeout",
        "title": "A timeout (product code hangs, the test never ends)",
        "inject": [("state_guard.py", "    removed = []\n    for rel in files:",
                    "    removed = []\n    while root:\n        time.sleep(0.5)\n    for rel in files:")],
        "tests": ["test_cce3_bad_day"],
        "steps": ["tests"],
        "chunk_timeout": HANG_CHUNK_TIMEOUT,
        "expect": "autofix",
        "found": r"test_cce3_bad_day \(timed out\)",
        "answer": {
            "edits": [("state_guard.py", "    while root:\n        time.sleep(0.5)\n", "")],
            "files": _regression("test_autofix_state_files_end", (
                "class StateFilesEndTests(unittest.TestCase):\n"
                "    def test_check_returns_for_an_empty_folder(self):\n"
                "        import tempfile\n"
                "        import state_guard\n"
                "        with tempfile.TemporaryDirectory() as folder:\n"
                "            self.assertEqual(state_guard.check_state_files(root=folder, send=lambda text: True), [])\n")),
        },
    },
    {
        "key": "syntax_error",
        "title": "A syntax error in a product file",
        "inject": [("state_guard.py", "def damaged_json(path):", "def damaged_json(path:")],
        "tests": ["test_cce3_bad_day"],
        "steps": ["tests"],
        "expect": "autofix",
        "found": r"state_guard\.py",
        "answer": {
            "edits": [("state_guard.py", "def damaged_json(path:", "def damaged_json(path):")],
            "files": _regression("test_autofix_state_guard_compiles", (
                "class StateGuardCompilesTests(unittest.TestCase):\n"
                "    def test_file_compiles(self):\n"
                "        import pathlib\n"
                "        source = pathlib.Path(__file__).resolve().parent.parent / 'state_guard.py'\n"
                "        compile(source.read_text(encoding='utf-8'), str(source), 'exec')\n")),
        },
    },
    {
        "key": "control_copy",
        "title": "A missing file in the auto-fix copy list (a copied file imports one that is not copied)",
        "inject": [("notify.py", "import telegram_diagnostics as diagnostics\n",
                    "import telegram_diagnostics as diagnostics\nimport state_guard\n")],
        "tests": ["test_autofix_control_copy"],
        "steps": ["tests"],
        "expect": "owner",
        "found": r"test_autofix_control_copy",
        "reason": r"setup/workflow problem needs a human",
        "answer": {"edits": [("notify.py", "import state_guard\n", "")], "files": {}},
    },
    {
        "key": "bad_workflow_step",
        "title": "A bad workflow step (agent.yml runs a file that does not exist)",
        "inject": [(".github/workflows/agent.yml", "run export -- python3 content_export.py",
                    "run export -- python3 content_exprot.py")],
        "tests": ["test_autofix_control_copy"],
        "steps": ["tests", "export"],
        "expect": "owner",
        "found": r"Export approved content|test_\w*(?:workflow|ci_parity)",
        "reason": r"setup/workflow problem needs a human",
        "answer": {"edits": [(".github/workflows/agent.yml", "python3 content_exprot.py",
                              "python3 content_export.py")], "files": {}},
    },
    {
        "key": "flaky_test",
        "title": "A flaky test (fails once, passes on the next run)",
        "inject": [],
        "plant": {"tests/test_drill_leftover_state.py": FLAKY_TEST},
        "tests": ["test_cce3_bad_day"],
        "steps": ["tests"],
        "expect": "retry",
        "found": r"",
        "answer": {"edits": [], "files": {}},
    },
    {
        "key": "money_string",
        "title": "A money string in a public file (README.md)",
        "inject": [],
        "append": {"README.md": "\nLaunch price for the first planner: " + MONEY + ".\n"},
        "tests": ["test_r22_markdown_currency"],
        "steps": ["tests"],
        "expect": "autofix",
        "found": r"test_r22_markdown_currency",
        "answer": {
            "edits": [("README.md", "Launch price for the first planner: " + MONEY + ".",
                       "Launch price for the first planner: [amount].")],
            "files": _regression("test_autofix_readme_amounts", (
                "class ReadmeAmountTests(unittest.TestCase):\n"
                "    def test_readme_uses_the_amount_placeholder(self):\n"
                "        import pathlib\n"
                "        text = (pathlib.Path(__file__).resolve().parent.parent / 'README.md').read_text(encoding='utf-8')\n"
                "        self.assertIn('first planner: [amount].', text)\n")),
        },
    },
    {
        "key": "broken_builder",
        "title": "A broken builder (the habit tracker builder crashes)",
        "inject": [("builder_habit.py", "    norm = normalize_spec(spec)\n    _use_theme(spec)\n",
                    "    norm = normalize_spec(spec)[\"habits\"]\n    _use_theme(spec)\n")],
        "tests": ["test_gm1_builders"],
        "steps": ["tests"],
        "expect": "autofix",
        "found": r"test_gm1_builders",
        "answer": {
            "edits": [("builder_habit.py", "    norm = normalize_spec(spec)[\"habits\"]\n",
                       "    norm = normalize_spec(spec)\n")],
            "files": _regression("test_autofix_habit_builder", (
                "class HabitBuilderTests(unittest.TestCase):\n"
                "    def test_builder_makes_both_paper_sizes(self):\n"
                "        import tempfile\n"
                "        import builder_habit\n"
                "        if not builder_habit.HAS_PILLOW:\n"
                "            self.skipTest('Pillow is not installed')\n"
                "        with tempfile.TemporaryDirectory() as folder:\n"
                "            names = builder_habit.build({}, folder)\n"
                "        self.assertIn('habit_tracker_a4.pdf', names)\n"
                "        self.assertIn('habit_tracker_letter.pdf', names)\n")),
        },
    },
    {
        "key": "plugin_none",
        "title": "A Telegram plugin returning None for its own command",
        "inject": [("tg_bundle.py", "        return {\"text\": help_text(ctx)}\n", "        return None\n")],
        "tests": ["test_ccb3_bundle"],
        "steps": ["tests"],
        "expect": "autofix",
        "found": r"test_ccb3_bundle\.HelpTests",
        "answer": {
            "edits": [("tg_bundle.py", "            return handle_build(ids, ctx)\n        return None\n",
                       "            return handle_build(ids, ctx)\n        return {\"text\": help_text(ctx)}\n")],
            "files": _regression("test_autofix_bundle_answers", (
                "class BundleAnswersTests(unittest.TestCase):\n"
                "    def test_bare_command_gets_a_text_reply(self):\n"
                "        import tempfile\n"
                "        import tg_bundle\n"
                "        with tempfile.TemporaryDirectory() as folder:\n"
                "            reply = tg_bundle.handle('/bundle', {'db_path': folder + '/a.db'})\n"
                "        self.assertIsInstance(reply, dict)\n"
                "        self.assertTrue(reply.get('text'))\n")),
        },
    },
]
SCENARIO_KEYS = [s["key"] for s in SCENARIOS]


# ---- reading workflow files --------------------------------------------------------------------
class DrillError(Exception):
    """The drill itself cannot run (for example a workflow step was renamed)."""


def workflow_step_script(text: str, name: str) -> str:
    """The `run:` script of the step called `name` in a workflow file (plain or `|` block)."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)- name: (.+?)\s*$", line)
        if not match or match.group(2).strip("'\"") != name:
            continue
        indent = len(match.group(1))
        block = []
        for nxt in lines[index + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            block.append(nxt)
        for pos, row in enumerate(block):
            run = re.match(r"^(\s*)run: ?(.*)$", row)
            if not run:
                continue
            value = run.group(2).strip()
            if value not in ("|", "|-", "|+", ">", ">-"):
                return value
            body = []
            for nxt in block[pos + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= len(run.group(1)):
                    break
                body.append(nxt)
            while body and not body[-1].strip():
                body.pop()
            pad = min((len(b) - len(b.lstrip()) for b in body if b.strip()), default=0)
            return "\n".join(b[pad:] for b in body) + "\n"
        raise DrillError(f"workflow step {name!r} has no run: line")
    raise DrillError(f"workflow step {name!r} was not found (was it renamed?)")


def keep_heredoc_lines(script: str, tag: str, prefixes: Tuple[str, ...]) -> str:
    """Inside the `<<'TAG'` here-document keep only the lines that start with one of `prefixes`
    (the lines dropped need the encrypted database, which a drill copy does not have)."""
    lines = script.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip().endswith(f"<<'{tag}'"))
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == tag)
    except StopIteration:
        raise DrillError(f"here-document {tag} was not found in the workflow step") from None
    kept = [line for line in lines[start + 1:end] if line.strip().startswith(prefixes)]
    if not kept:
        raise DrillError(f"no line starting with {prefixes} inside {tag}")
    return "\n".join(lines[:start + 1] + kept + lines[end:]) + "\n"


def step_outputs(path: Path) -> Dict[str, str]:
    """Parse a GITHUB_OUTPUT file (name=value and name<<DELIMITER blocks)."""
    out: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    index = 0
    while index < len(lines):
        line = lines[index]
        block = re.fullmatch(r"([A-Za-z_][\w-]*)<<(\S+)", line)
        if block:
            body = []
            index += 1
            while index < len(lines) and lines[index] != block.group(2):
                body.append(lines[index])
                index += 1
            out[block.group(1)] = "\n".join(body)
        elif "=" in line:
            name, value = line.split("=", 1)
            out[name] = value
        index += 1
    return out


def helper_test_modules(names: List[str], root: Path = ROOT) -> List[str]:
    """The test modules plus every test module they import by name (shared fixtures)."""
    todo, seen = list(names), []
    while todo:
        name = todo.pop()
        if name in seen or not (root / "tests" / f"{name}.py").is_file():
            continue
        seen.append(name)
        text = (root / "tests" / f"{name}.py").read_text(encoding="utf-8")
        todo.extend(re.findall(r"^\s*(?:import|from) (?:tests\.)?(test_\w+)", text, re.MULTILINE))
    return sorted(seen)


# ---- the stand-in AI (`opencode github run`) ---------------------------------------------------
def fake_opencode() -> int:
    """Started by autofix.py as `opencode github run` inside the fix job's checkout. Gives the recorded
    answer of the current drill: edits + a regression test on a new branch, pushed, pull request opened."""
    import urllib.request
    answer = json.loads(Path(os.environ["DRILL_ANSWER"]).read_text(encoding="utf-8"))
    prompt = os.environ.get("PROMPT", "")
    with open(Path(os.environ["DRILL_ANSWER"]).with_name("ai_calls.jsonl"), "a", encoding="utf-8") as log:
        log.write(json.dumps({"model": os.environ.get("MODEL", ""), "prompt": prompt}) + "\n")
    title = re.search(r'Open a PR titled "([^"]+)"', prompt)
    marker = re.search(r'Include this exact line in its body: "([^"]+)"', prompt)
    if not title or not marker:
        print("The request could not be understood; no change made.")
        return 0

    def git(*args):
        subprocess.run(["git", *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    branch = "autofix/run-" + (os.environ.get("GITHUB_RUN_ID") or "0")
    git("checkout", "-q", "-b", branch)
    for name, old, new in answer.get("edits", []):
        path = Path(name)
        text = path.read_text(encoding="utf-8")
        if old not in text:
            print("The recorded answer does not fit this code; no change made.")
            return 0
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    for name, content in answer.get("files", {}).items():
        Path(name).parent.mkdir(parents=True, exist_ok=True)
        Path(name).write_text(content, encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", title.group(1))
    git("push", "-q", "origin", branch)
    request = urllib.request.Request(
        "https://api.github.com/repos/" + os.environ.get("GITHUB_REPOSITORY", "") + "/pulls",
        data=json.dumps({"title": title.group(1), "head": branch, "base": "main",
                         "body": "Found the cause and fixed it; a regression test is included.\n\n"
                                 + marker.group(1) + "\n"}).encode("utf-8"),
        method="POST", headers={"Authorization": "Bearer " + os.environ.get("GITHUB_TOKEN", "")})
    with urllib.request.urlopen(request, timeout=15) as response:
        number = json.loads(response.read()).get("number")
    print(f"Opened pull request #{number}.")
    return 0


# ---- one drill ---------------------------------------------------------------------------------
class Proc:
    def __init__(self, code: int, output: str, timed_out: bool = False):
        self.code, self.output, self.timed_out = code, output, timed_out

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out


class Drill:
    def __init__(self, scenario: Dict[str, Any], base: Path, full: bool = False, root: Path = ROOT):
        self.s = scenario
        self.base = Path(os.path.realpath(str(base)))
        self.full = full
        self.root = root
        self.origin = self.base / "origin.git"
        self.seed = self.base / "seed"
        self.sim = self.base / "sim"
        self.hooks = self.base / "hooks"
        self.bin = self.base / "bin"
        self.home = self.base / "home"
        self.work = self.base / "work"
        self.cache = self.base / "cache"          # plays actions/cache (the failure counter)
        self.secrets = sim.fake_secrets()
        self.now = dt.datetime.strptime(START, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
        self.run_id = 7000
        self.log: List[str] = []                  # what happened, in order (for --json / debugging)
        self.runs: List[Dict[str, Any]] = []
        self.failed_steps: List[Dict[str, Any]] = []
        self.dispatch_seen = 0
        self.update_id = 5000
        self.pressed: set = set()
        self.bad_sha = ""
        self.step_timeout = FULL_STEP_TIMEOUT if full else QUICK_STEP_TIMEOUT

    # -- sandbox --
    def git(self, *args: str, cwd: Optional[Path] = None, check: bool = True) -> str:
        env = dict(os.environ, HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
                   GIT_AUTHOR_NAME="drill", GIT_AUTHOR_EMAIL="name@example.com",
                   GIT_COMMITTER_NAME="drill", GIT_COMMITTER_EMAIL="name@example.com")
        done = subprocess.run(["git", *args], cwd=str(cwd or self.seed), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors="replace", check=False)
        if check and done.returncode != 0:
            raise DrillError(f"git {' '.join(args[:2])} failed: {done.stdout.strip()[:300]}")
        return done.stdout

    def setup(self) -> None:
        for folder in (self.seed, self.sim, self.hooks, self.bin, self.home, self.work, self.cache):
            folder.mkdir(parents=True, exist_ok=True)
        keep_tests = None if self.full else set(helper_test_modules(list(self.s["tests"]), self.root))
        for name in subprocess.run(["git", "ls-files", "-z"], cwd=str(self.root), stdout=subprocess.PIPE,
                                   check=True).stdout.decode("utf-8", "replace").split("\0"):
            base = name.rsplit("/", 1)[-1]
            if (not name or base in NEVER_COPIED or base.startswith("agent_data.db") or base.endswith(".enc")
                    or name.startswith("products.tar.gz.enc.parts/")
                    or name.startswith(".jobs/")):
                continue
            if keep_tests is not None and name.startswith("tests/") and base.startswith("test_") \
                    and base[:-3] not in keep_tests:
                continue
            source = self.root / name
            if not source.is_file():
                continue
            target = self.seed / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
        self.git("init", "-q", "-b", "main", ".")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "robot code (good)")
        self.git("clone", "-q", "--bare", "--no-hardlinks", str(self.seed), str(self.origin), cwd=self.base)
        # no background repack while a step is cloning (it makes a local clone fail now and then)
        for folder in (self.origin, self.seed):
            self.git("config", "gc.auto", "0", cwd=folder)
            self.git("config", "gc.autoDetach", "false", cwd=folder)
        self.git("remote", "add", "origin", str(self.origin))
        shutil.copy2(str(sim.WORLD), str(self.hooks / "robot_sim_world.py"))
        (self.hooks / "sitecustomize.py").write_text(
            "import os\nif os.environ.get('ROBOT_SIM_DIR'):\n    import robot_sim_world\n"
            "    robot_sim_world.install()\n", encoding="utf-8")
        for name, target in (("python3", f'exec "{sys.executable}" "$@"'),
                             ("opencode", f'exec "{sys.executable}" "{Path(__file__).resolve()}" --fake-opencode "$@"')):
            script = self.bin / name
            script.write_text("#!/bin/sh\n" + target + "\n", encoding="utf-8")
            script.chmod(0o755)
        (self.sim / "answer.json").write_text(json.dumps(self.s["answer"]), encoding="utf-8")
        self.save_hub({"git_dir": str(self.origin), "pulls": {}, "next_pull": 1, "dispatches": [],
                       "check_runs": {}, "runs": {}, "artifacts": {}, "artifact_files": {}, "merged": []})

    def hub(self) -> Dict[str, Any]:
        return json.loads((self.sim / "github_hub.json").read_text(encoding="utf-8"))

    def save_hub(self, hub: Dict[str, Any]) -> None:
        (self.sim / "github_hub.json").write_text(json.dumps(hub), encoding="utf-8")

    def push_change(self, edits, files=None, append=None, message="change") -> str:
        """Commit a change on main in the seed clone and push it (a push to GitHub main)."""
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "-B", "main", "origin/main")
        for name, old, new in edits:
            path = self.seed / name
            text = path.read_text(encoding="utf-8")
            if text.count(old) < 1:
                raise DrillError(f"{name}: the text this drill changes was not found; update the drill")
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
        for name, content in (files or {}).items():
            (self.seed / name).parent.mkdir(parents=True, exist_ok=True)
            (self.seed / name).write_text(content, encoding="utf-8")
        for name, content in (append or {}).items():
            with open(self.seed / name, "a", encoding="utf-8") as handle:
                handle.write(content)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        self.git("push", "-q", "origin", "main")
        return self.git("rev-parse", "HEAD").strip()

    def checkout(self, name: str, ref: str = "main", fresh: bool = True) -> Path:
        """actions/checkout: a clone of the sandbox GitHub repo at `ref`."""
        folder = self.work / name
        if fresh and folder.exists():
            shutil.rmtree(folder)
        if not folder.exists():
            self.git("clone", "-q", "--no-hardlinks", str(self.origin), str(folder), cwd=self.base)
            self.git("config", "gc.auto", "0", cwd=folder)
        self.git("fetch", "-q", "origin", cwd=folder)
        if re.fullmatch(r"[0-9a-f]{40}", ref):
            self.git("checkout", "-q", "--detach", ref, cwd=folder)
        else:
            self.git("checkout", "-q", "-B", ref, "origin/" + ref, cwd=folder)
        return folder

    def runner_temp(self, label: str) -> Path:
        folder = self.base / "runner" / f"{self.run_id}-{label}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def env(self, temp: Path, world: bool = True, secrets: Tuple[str, ...] = (), extra=None) -> Dict[str, str]:
        import site
        env = {
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
            "TMPDIR": str(temp), "RUNNER_TEMP": str(temp),
            "AUTOFIX_DRILL": "1",          # a test that itself starts drills skips inside a drill
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": REPO,
            "GITHUB_SERVER_URL": "https://github.sim.invalid", "GITHUB_RUN_ID": str(self.run_id),
            "GITHUB_OUTPUT": str(temp / "github_output.txt"), "GITHUB_ENV": str(temp / "github_env.txt"),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        }
        user_base = getattr(site, "USER_BASE", None) or site.getuserbase()
        if user_base:
            env["PYTHONUSERBASE"] = user_base
        if world:
            env.update({"PYTHONPATH": str(self.hooks), "ROBOT_SIM_DIR": str(self.sim),
                        "ROBOT_SIM_NOW": repr(self.now), "ROBOT_SIM_RUN": str(self.run_id),
                        "DRILL_ANSWER": str(self.sim / "answer.json")})
        for name in secrets:
            env[name] = self.secrets[name]
        env.update(extra or {})
        return env

    def run_script(self, script: str, cwd: Path, env: Dict[str, str], label: str,
                   timeout: Optional[float] = None) -> Proc:
        """One workflow step: `bash -e -o pipefail` like GitHub; killed with its children at the limit."""
        if "${{" in script:
            raise DrillError(f"step {label!r} uses a workflow expression inside run:; the drill cannot play it")
        env = dict(env, ROBOT_SIM_STEP=label)
        Path(env["GITHUB_OUTPUT"]).unlink(missing_ok=True)
        process = subprocess.Popen(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                                   cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, text=True, errors="replace", start_new_session=True)
        try:
            output, _ = process.communicate(timeout=timeout or 600)
            result = Proc(process.returncode, output or "")
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                output, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                output = ""
            result = Proc(-9, output or "", timed_out=True)
        self.log.append(f"[run {self.run_id}] {label}: " + ("TIMEOUT (killed like a GitHub step limit)"
                                                           if result.timed_out else f"exit {result.code}"))
        if not result.ok:      # the end of a failed step's log goes into the --json result
            self.failed_steps.append({"run": self.run_id, "step": label, "exit": result.code,
                                      "timed_out": result.timed_out,
                                      "tail": result.output.replace(str(self.base), "<sandbox>").splitlines()[-25:]})
        (self.base / "steps").mkdir(exist_ok=True)
        (self.base / "steps" / f"{self.run_id}-{re.sub(r'[^a-z0-9]+', '_', label.lower())}.txt").write_text(
            result.output, encoding="utf-8")
        return result

    def workflow(self, name: str) -> str:
        """GitHub runs the workflow file that is on main."""
        return self.git("show", f"main:.github/workflows/{name}", cwd=self.origin)

    def cache_restore(self, temp: Path) -> None:
        saved = self.cache / "robot-failure-state.json"
        if saved.exists():
            shutil.copy2(str(saved), str(temp / "robot-failure-state.json"))

    def cache_save(self, temp: Path) -> None:
        state = temp / "robot-failure-state.json"
        if state.exists():
            shutil.copy2(str(state), str(self.cache / "robot-failure-state.json"))

    def state(self) -> Dict[str, Any]:
        try:
            return json.loads((self.cache / "robot-failure-state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def tick(self, seconds: float) -> None:
        self.now += seconds

    # -- Telegram (fake) --
    def messages(self) -> List[Dict[str, Any]]:
        return [m for m in sim.read_jsonl(self.sim / "telegram_out.jsonl")
                if m.get("ok") and m.get("method") in ("sendMessage", "editMessageText")]

    def owner_presses(self, prefix: str = "af:merge:") -> bool:
        """The owner taps the newest auto-fix Merge button the robot sent."""
        for sent in reversed(self.messages()):
            for button in sent.get("buttons") or []:
                data = str(button.get("data") or "")
                if data.startswith(prefix) and data not in self.pressed:
                    self.pressed.add(data)
                    self.update_id += 1
                    chat = int(sim.OWNER_CHAT)
                    update = {"update_id": self.update_id, "callback_query": {
                        "id": str(self.update_id), "data": data, "chat_instance": "1",
                        "from": {"id": chat, "is_bot": False, "first_name": "Owner"},
                        "message": {"message_id": int(sent.get("message_id") or 1), "date": int(self.now),
                                    "text": "", "chat": {"id": chat, "type": "private"}}}}
                    path = self.sim / "tg_updates.json"
                    try:
                        queue = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        queue = []
                    path.write_text(json.dumps(queue + [update]), encoding="utf-8")
                    self.log.append(f"owner presses the button {data.split(':')[1]} (pull request {data.split(':')[2]})")
                    return True
        return False

    # -- agent.yml --
    def tests_script(self, workflow: str) -> str:
        script = workflow_step_script(workflow, AGENT_STEPS["tests"])
        return keep_heredoc_lines(script, "ROBOT_STEP", ("python3 tools/run_tests_chunked.py",))

    def agent_run(self) -> Dict[str, Any]:
        """One run of the `cycle` job of agent.yml (only the steps this drill needs), then the
        failure-counter step, then the recovery inbox job when the workflow would start it."""
        self.run_id += 1
        self.tick(1800)
        temp = self.runner_temp("cycle")
        self.cache_restore(temp)
        code = self.checkout("cycle", fresh=False)
        workflow = self.workflow("agent.yml")
        status, failed_step, inbox_started = "success", "", False
        for step in self.s["steps"]:
            if step == "tests":
                extra = {"CHILLI_TEST": "1"}
                if not self.full:
                    extra["CHILLI_TEST_CHUNK_TIMEOUT"] = str(self.s.get("chunk_timeout", QUICK_CHUNK_TIMEOUT))
                proc = self.run_script(self.tests_script(workflow), code,
                                       self.env(temp, world=False, extra=extra), AGENT_STEPS[step],
                                       timeout=self.step_timeout)
            else:
                names = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GITHUB_TOKEN") if step == "inbox" else ()
                env = self.env(temp, secrets=names)
                proc = self.run_script(workflow_step_script(workflow, AGENT_STEPS[step]), code, env,
                                       AGENT_STEPS[step], timeout=300)
                if step == "inbox":
                    inbox_started = step_outputs(Path(env["GITHUB_OUTPUT"])).get("started") == "true"
            if not proc.ok:
                status, failed_step = "failure", step
                break
        try:
            summary = (temp / "failure.txt").read_text(encoding="utf-8")
        except OSError:
            summary = ""
        finish = self.run_script(
            workflow_step_script(workflow, AGENT_STEPS["finish"]), code,
            self.env(temp, secrets=("GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
                     extra={"RUN_STATUS": status, "ROBOT_RAN": "true"}), AGENT_STEPS["finish"], timeout=120)
        self.cache_save(temp)
        run = {"run": self.run_id, "status": status, "failed_step": failed_step, "summary": summary.strip(),
               "finish_output": finish.output.strip(), "finish_ok": finish.ok}
        self.runs.append(run)
        self.log.append(f"robot run {self.run_id}: {status}"
                        + (f" at step '{AGENT_STEPS[failed_step]}'" if failed_step else ""))
        if status == "failure" and not inbox_started:
            self.recovery_inbox()
        return run

    def recovery_inbox(self) -> Proc:
        """agent.yml job `recovery_inbox` (runs when the cycle failed before its inbox step, and after
        the 'Robot auto-fix checks' workflow)."""
        temp = self.runner_temp("recovery")
        code = self.checkout("cycle", fresh=False)
        script = keep_heredoc_lines(workflow_step_script(self.workflow("agent.yml"), AGENT_STEPS["recovery"]),
                                    "ROBOT_RECOVERY", ("python3 telegram_inbox.py",))
        self.tick(60)
        return self.run_script(script, code, self.env(
            temp, secrets=("GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")), AGENT_STEPS["recovery"],
            timeout=300)

    # -- autofix.yml --
    def autofix_run(self, inputs: Dict[str, str]) -> Dict[str, Any]:
        """The 'Robot auto-fix' workflow: jobs fix -> prepare -> result, then agent.yml `autofix_result`."""
        self.run_id += 1
        fix_run = self.run_id
        self.tick(120)
        created = dt.datetime.fromtimestamp(self.now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        workflow = self.workflow("autofix.yml")
        temp = self.runner_temp("fix")
        code = self.checkout("fix")
        state = str(temp / "autofix-control" / "state.json")
        out: Dict[str, Any] = {"run": fix_run, "proposal": False, "busy": False, "fix_job": "success",
                               "prepare_job": "skipped", "prompt": ""}

        def step(key, secrets=("GITHUB_TOKEN",), extra=None, timeout=300):
            env = self.env(temp, secrets=secrets, extra=dict({"AUTOFIX_STATE": state}, **(extra or {})))
            proc = self.run_script(workflow_step_script(workflow, FIX_STEPS[key]), code, env,
                                   "auto-fix: " + FIX_STEPS[key], timeout=timeout)
            return proc, step_outputs(Path(env["GITHUB_OUTPUT"]))

        prompt_proc, prompt_out = step("prompt", extra={"FAILURE_SUMMARY": inputs.get("summary", "")})
        out["prompt"] = prompt_out.get("prompt", "")
        if not prompt_proc.ok:
            out["fix_job"] = "failure"          # a failed step ends the job; later steps need prompt success
        else:
            ai = {"USE_GITHUB_TOKEN": "true", "SHARE": "false", "PROMPT": out["prompt"]}
            _, gemini = step("gemini", secrets=("GITHUB_TOKEN", "GEMINI_API_KEY"), extra=ai, timeout=600)
            proposal = False
            reason = gemini.get("reason", "")
            for key in ("verify_gemini", "verify_nim", "verify_groq"):   # NVIDIA / Groq keys: not configured
                _, verify = step(key, extra={"PROVIDER_REASON": reason})
                proposal = verify.get("proposal") == "true"
                reason = ""
                if proposal:
                    break
            out["proposal"] = proposal
            out["busy"] = gemini.get("busy") == "true"
            if not proposal:
                step("final")
                out["fix_job"] = "failure"
        if out["fix_job"] == "success":
            prepare_temp = self.runner_temp("prepare")
            proc = self.run_script(workflow_step_script(workflow, FIX_STEPS["prepare"]), self.checkout("prepare"),
                                   self.env(prepare_temp, secrets=("GITHUB_TOKEN",)),
                                   "auto-fix: " + FIX_STEPS["prepare"], timeout=120)
            out["prepare_job"] = "success" if proc.ok else "failure"
        # job `result` (always): only the safe outcome leaves the workflow, as one small artifact
        result_temp = self.runner_temp("result")
        accepted = out["proposal"] and out["prepare_job"] == "success"
        self.run_script(workflow_step_script(workflow, FIX_STEPS["record"]), self.checkout("result"),
                        self.env(result_temp, world=False, extra={
                            "FAILURE_SUMMARY": inputs.get("summary", ""), "AUTOFIX_PROBLEM": inputs.get("problem", ""),
                            "AUTOFIX_ATTEMPT": inputs.get("attempt", "1"),
                            "AUTOFIX_PROPOSAL": "true" if accepted else "false",
                            "AUTOFIX_BUSY": "true" if (not out["proposal"] and out["busy"]) else "false"}),
                        "auto-fix: " + FIX_STEPS["record"], timeout=120)
        hub = self.hub()
        hub["runs"][str(fix_run)] = {"id": fix_run, "path": ".github/workflows/autofix.yml", "head_branch": "main",
                                     "event": "workflow_dispatch", "status": "completed", "created_at": created,
                                     "conclusion": "success" if accepted else "failure",
                                     "head_repository": {"full_name": REPO}}
        record = result_temp / "autofix-result.json"
        if record.exists():
            packed = self.base / "artifacts" / f"{fix_run}.zip"
            packed.parent.mkdir(exist_ok=True)
            with zipfile.ZipFile(packed, "w") as archive:
                archive.write(record, "autofix-result.json")
            hub["artifacts"][str(fix_run)] = [{"id": fix_run + 100000, "name": "robot-autofix-result",
                                               "expired": False}]
            hub["artifact_files"][str(fix_run + 100000)] = str(packed)
        self.save_hub(hub)
        # agent.yml reacts to the finished workflow (job `autofix_result`)
        self.run_id += 1
        self.tick(60)
        receive_temp = self.runner_temp("receive")
        self.cache_restore(receive_temp)
        event = receive_temp / "event.json"
        event.write_text(json.dumps({"workflow_run": {"id": fix_run, "name": "Robot auto-fix",
                                                      "conclusion": hub["runs"][str(fix_run)]["conclusion"]}}),
                         encoding="utf-8")
        self.run_script(workflow_step_script(self.workflow("agent.yml"), AGENT_STEPS["receive"]),
                        self.checkout("cycle", fresh=False),
                        self.env(receive_temp, secrets=("GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
                                 extra={"GITHUB_EVENT_PATH": str(event)}), AGENT_STEPS["receive"], timeout=120)
        self.cache_save(receive_temp)
        self.log.append(f"auto-fix workflow {fix_run}: proposal={'yes' if out['proposal'] else 'no'}, "
                        f"fix job {out['fix_job']}, prepare job {out['prepare_job']}")
        return out

    # -- autofix-check.yml --
    def check_run(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """The 'Robot auto-fix checks' workflow on the fix branch, then agent.yml `recovery_inbox`."""
        self.run_id += 1
        self.tick(120)
        inputs = request.get("inputs") or {}
        number, sha = str(inputs.get("number", "")), str(inputs.get("sha", ""))
        workflow = self.workflow("autofix-check.yml")
        temp = self.runner_temp("check")
        head = self.git("rev-parse", "--verify", "-q", "refs/heads/" + str(request.get("ref", "")),
                        cwd=self.origin, check=False).strip()
        validate = self.run_script(
            workflow_step_script(workflow, CHECK_STEPS["validate"]), self.checkout("validate"),
            self.env(temp, secrets=("GITHUB_TOKEN",), extra={"PR_NUMBER": number, "PR_SHA": sha, "GITHUB_SHA": head}),
            "checks: " + CHECK_STEPS["validate"], timeout=120)
        runs = [{"name": "Validate auto-fix scope", "head_sha": sha, "status": "completed",
                 "conclusion": "success" if validate.ok else "failure"}]
        tests_ok = False
        if validate.ok:
            tests = self.run_script(workflow_step_script(workflow, CHECK_STEPS["tests"]), self.checkout("check", sha),
                                    self.env(self.runner_temp("check-tests"), world=False, extra={"CHILLI_TEST": "1"}),
                                    "checks: " + CHECK_STEPS["tests"], timeout=12 * 60)
            tests_ok = tests.ok
            runs.append({"name": "Auto-fix tests", "head_sha": sha, "status": "completed",
                         "conclusion": "success" if tests.ok else "failure"})
        hub = self.hub()
        hub["check_runs"][sha] = runs
        self.save_hub(hub)
        self.log.append(f"auto-fix checks on pull request {number}: scope "
                        f"{'ok' if validate.ok else 'REFUSED'}, tests {'green' if tests_ok else 'not green'}")
        self.run_id += 1
        self.recovery_inbox()
        return {"number": number, "sha": sha, "scope_ok": validate.ok, "tests_ok": tests_ok}

    def handle_dispatches(self) -> Dict[str, Any]:
        """Start every workflow the robot asked GitHub to start (auto-fix, then its checks)."""
        seen: Dict[str, Any] = {"fix": None, "check": None, "requests": []}
        for _ in range(6):
            pending = self.hub().get("dispatches", [])[self.dispatch_seen:]
            if not pending:
                break
            for request in pending:
                self.dispatch_seen += 1
                seen["requests"].append(request.get("workflow"))
                if request.get("workflow") == "autofix.yml":
                    seen["fix"] = self.autofix_run(request.get("inputs") or {})
                elif request.get("workflow") == "autofix-check.yml":
                    seen["check"] = self.check_run(request)
        return seen

    def apply_merges(self) -> List[int]:
        """GitHub's side of a merge the robot asked for: squash the fix branch onto main."""
        hub = self.hub()
        done = []
        for merge in hub.get("merged", []):
            if merge.get("done"):
                continue
            self.git("fetch", "-q", "origin")
            self.git("checkout", "-q", "-B", "main", "origin/main")
            self.git("merge", "-q", "--squash", "origin/" + merge["ref"])
            self.git("commit", "-q", "-m", f"Auto-fix (#{merge['number']})")
            self.git("push", "-q", "origin", "main")
            merge["done"] = True
            done.append(merge["number"])
            self.log.append(f"pull request {merge['number']} merged into main (squash)")
        self.save_hub(hub)
        return done

    # -- the story --
    def play(self) -> Dict[str, Any]:
        s = self.s
        self.setup()
        self.bad_sha = self.push_change(s.get("inject", []), s.get("plant"), s.get("append"),
                                        message="a change that breaks the robot")
        first = self.agent_run()
        second = self.agent_run()
        flow: Dict[str, Any] = {"fix": None, "check": None, "requests": []}
        pressed = merged = False
        after: Optional[Dict[str, Any]] = None
        if second["status"] == "failure":
            flow = self.handle_dispatches()
            pressed = self.owner_presses()
            if pressed:
                self.agent_run()                      # main is still broken; the recovery inbox takes the tap
                merged = bool(self.apply_merges())
            if s["expect"] == "owner" and not merged:
                # the owner repairs it by hand (the runbook way): the robot must notice and say so
                self.push_change(s["answer"].get("edits", []), s["answer"].get("files"),
                                 message="owner fixes it by hand")
                self.log.append("owner fixes main by hand")
            if merged or s["expect"] == "owner":
                after = self.agent_run()
        return self.judge(first, second, flow, pressed, merged, after)

    # -- the verdict --
    def judge(self, first, second, flow, pressed, merged, after) -> Dict[str, Any]:
        s = self.s
        expect = s["expect"]
        messages = self.messages()
        texts = [str(m.get("text") or "") for m in messages]
        alerts = [t for t in texts if "run FAILED" in t]
        alert = alerts[0] if alerts else ""
        hub = self.hub()
        notes: List[str] = []
        cells: Dict[str, str] = {}

        # found?
        if expect == "retry":
            found = first["status"] == "failure" and second["status"] == "success" and not alerts
            cells["found"] = "yes (one failure, kept quiet)" if found else "NO"
            if not found:
                notes.append(f"expected fail then pass with no alert; got {first['status']}, {second['status']}, "
                             f"{len(alerts)} alert(s)")
        else:
            failed_twice = first["status"] == "failure" and second["status"] == "failure"
            named = bool(alert) and re.search(s["found"], alert) is not None
            found = failed_twice and named
            cells["found"] = "yes" if found else ("alert, but cause not named" if alert else "NO alert")
            if not failed_twice:
                notes.append(f"the injected failure did not fail both runs ({first['status']}, {second['status']})")
            elif not alert:
                notes.append("no owner alert after the second failure")
            elif not named:
                problem = next((line for line in alert.splitlines() if line.startswith("Problem: ")), "")
                notes.append(f"the alert does not name the cause (wanted /{s['found']}/; it says '{problem}')")

        # route
        started = any("Auto-fix: started" in t for t in alerts)
        not_started = next((re.search(r"Auto-fix: not started — (.+)", t).group(1) for t in alerts
                            if "Auto-fix: not started — " in t), "")
        if expect == "autofix":
            route = started and "autofix.yml" in flow["requests"]
            cells["route"] = "auto-fix started" if route else ("NOT started: " + (not_started or "no request"))
        elif expect == "owner":
            route = bool(not_started) and re.search(s["reason"], not_started) is not None \
                and "autofix.yml" not in flow["requests"]
            cells["route"] = ("owner (" + not_started + ")") if route else (
                "auto-fix started (should go to the owner)" if started else "unclear: " + (not_started or "nothing"))
        else:
            route = not self.hub().get("dispatches") and self.state().get("consecutive_failures") == 0
            cells["route"] = "quiet retry, no auto-fix" if route else "NOT quiet"
        if not route:
            notes.append("route: " + cells["route"])

        # fix branch?
        branch = True
        if expect == "autofix":
            pulls = list(hub.get("pulls", {}).values())
            fix = flow.get("fix") or {}
            problems = []
            if not fix:
                problems.append("the auto-fix workflow was never started")
            elif not pulls:
                problems.append("no pull request was opened" + ("" if fix.get("prompt") else " (the prompt step failed)"))
            else:
                pr = pulls[0]
                ref = pr.get("head_ref", "")
                changed = set(self.git("diff", "--name-only", f"{self.bad_sha}...refs/heads/{ref}",
                                       cwd=self.origin, check=False).split())
                wanted = {e[0] for e in s["answer"]["edits"]} | set(s["answer"]["files"])
                parent = self.git("rev-parse", "--verify", "-q", f"refs/heads/{ref}~1", cwd=self.origin,
                                  check=False).strip()
                if changed != wanted:
                    problems.append(f"changed files {sorted(changed)} are not the fix {sorted(wanted)}")
                if parent != self.bad_sha:
                    problems.append("the branch does not start from the broken main")
                if ref == "main" or pr.get("base") != "main":
                    problems.append("wrong branch or base")
                if not pr.get("title", "").startswith("Auto-fix: "):
                    problems.append("wrong title")
                if not fix.get("proposal"):
                    problems.append("the robot's own result check did not accept the proposal")
                if fix.get("prepare_job") != "success":
                    problems.append("the scope check (safe files + regression test) refused it")
                check = flow.get("check") or {}
                if not check.get("scope_ok"):
                    problems.append("the checks workflow did not validate the request")
            branch = not problems
            cells["fix_branch"] = "yes" if branch else "NO"
            notes.extend("fix branch: " + p for p in problems)
        else:
            cells["fix_branch"] = "not needed" if not hub.get("pulls") else "UNEXPECTED pull request"
            branch = not hub.get("pulls")

        # tests green after the fix?
        green = True
        if expect == "autofix":
            check = flow.get("check") or {}
            green = bool(check.get("tests_ok")) and merged and bool(after) and after["status"] == "success"
            cells["tests_green"] = "yes" if green else "NO"
            if not check.get("tests_ok"):
                notes.append("tests green: the checks on the fix commit did not pass (or never ran)")
            elif not pressed:
                notes.append("tests green: the owner never got a Merge button, so nothing was merged")
            elif not merged:
                notes.append("tests green: the Merge tap did not merge the pull request")
            elif not after or after["status"] != "success":
                notes.append("tests green: the robot run after the merge is still failing")
        elif expect == "owner":
            green = bool(after) and after["status"] == "success"
            cells["tests_green"] = "yes (after the owner's fix)" if green else "NO"
            if not green:
                notes.append("tests green: the run after the owner's fix is still failing")
        else:
            cells["tests_green"] = "yes (second run)" if second["status"] == "success" else "NO"
            green = second["status"] == "success"

        # owner messages
        problems = self.message_problems(messages, alert, expect, started, not_started, merged, after)
        cells["owner_msg"] = "clear" if not problems else "NOT clear"
        notes.extend("owner message: " + p for p in problems)

        ok = found and route and branch and green and not problems
        return {"key": s["key"], "title": s["title"], "expect": expect, "verdict": "PASS" if ok else "FAIL",
                "cells": cells, "notes": notes, "messages": texts, "log": self.log,
                "failed_steps": self.failed_steps,
                "buttons": [b.get("data") for m in messages for b in (m.get("buttons") or [])],
                "ai_prompt": ((flow.get("fix") or {}).get("prompt") or ""),
                "runs": [{k: r[k] for k in ("run", "status", "failed_step", "summary")} for r in self.runs]}

    def message_problems(self, messages, alert, expect, started, not_started, merged, after) -> List[str]:
        texts = [str(m.get("text") or "") for m in messages]
        problems: List[str] = []
        if expect == "retry":
            return [f"a single failure must stay quiet, but the owner got: {texts[0][:80]!r}"] if texts else []
        private = [str(self.base), "/private/var/", "/var/folders/", "/tmp/"] + [
            v for v in self.secrets.values() if len(v) >= 12]
        for text in texts:
            if any(mark in text for mark in private):
                problems.append(f"a private path or secret value is in a message: {text[:60]!r}")
            if len(text) > 3500:
                problems.append("a message is longer than 3500 characters")
            if len(text.split()) >= 6 and not TENGLISH.search(text):
                problems.append(f"no Tenglish line in: {text.splitlines()[0][:70]!r}")
        repeated = sorted({t for t in texts if texts.count(t) > 1})
        for text in repeated:
            problems.append(f"the same message was sent {texts.count(text)} times: {text.splitlines()[0][:60]!r}")
        if not alert:
            return problems + ["no failure alert at all"]
        if not re.search(r"(?m)^Step: .+$", alert) or not re.search(r"(?m)^Problem: .+$", alert):
            problems.append("the alert has no Step / Problem lines")
        if not started and not not_started:
            problems.append("the alert does not say whether auto-fix started")
        if expect == "autofix":
            ready = [m for m in messages if "Auto-fix ready" in str(m.get("text") or "")]
            datas = [str(b.get("data") or "") for m in ready for b in (m.get("buttons") or [])]
            if not ready:
                problems.append("no 'Auto-fix ready' message with buttons reached the owner")
            elif not (any(d.startswith("af:merge:") for d in datas) and any(d.startswith("af:close:") for d in datas)):
                problems.append("the 'Auto-fix ready' message has no Merge / Close buttons")
            if merged and not any("Auto-fix merged" in t for t in texts):
                problems.append("no answer after the Merge tap")
            if any("didn't work" in t for t in texts):
                problems.append("the owner was told auto-fix did not work")
        if expect == "owner" and started:
            problems.append("the alert says auto-fix started for a problem only the owner can fix")
        if after and after["status"] == "success" and not any(t.startswith("fixed") for t in texts):
            problems.append("no 'fixed' message after the run went green again")
        return problems


# ---- running and reporting ---------------------------------------------------------------------
def run_drill(key: str, base: Optional[Path] = None, full: bool = False) -> Dict[str, Any]:
    scenario = next(s for s in SCENARIOS if s["key"] == key)
    own = base is None
    folder = Path(tempfile.mkdtemp(prefix=f"autofix_drill_{key}_")) if own else Path(base)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        drill = Drill(scenario, folder, full=full)
        try:
            return drill.play()
        except DrillError as error:
            return {"key": key, "title": scenario["title"], "expect": scenario["expect"], "verdict": "ERROR",
                    "cells": {}, "notes": ["the drill could not run: " + str(error)], "messages": [],
                    "log": drill.log, "failed_steps": drill.failed_steps, "buttons": [], "ai_prompt": "",
                    "runs": []}
    finally:
        if own:
            shutil.rmtree(folder, ignore_errors=True)


def run_all(keys: Optional[List[str]] = None, keep: Optional[Path] = None, full: bool = False,
            jobs: int = 4) -> List[Dict[str, Any]]:
    keys = list(keys or SCENARIO_KEYS)
    with ThreadPoolExecutor(max_workers=max(1, min(jobs, len(keys)))) as pool:
        futures = [pool.submit(run_drill, key, (keep / key) if keep else None, full) for key in keys]
        return [future.result() for future in futures]


COLUMNS = (("found", "found?"), ("route", "route"), ("fix_branch", "fix branch?"),
           ("tests_green", "tests green?"), ("owner_msg", "owner msg?"))


def format_report(rows: List[Dict[str, Any]]) -> str:
    lines = ["AUTO-FIX DRILL REPORT", "=" * 78]
    for number, row in enumerate(rows, 1):
        lines.append(f"{number:2d}. {row['key']} — {row['title']}")
        lines.append(f"    result: {row['verdict']}   (expected route: {row['expect']})")
        for key, label in COLUMNS:
            if key in row["cells"]:
                lines.append(f"    {label:<13} {row['cells'][key]}")
        for note in row["notes"]:
            lines.append(f"    ! {note}")
    passed = sum(1 for row in rows if row["verdict"] == "PASS")
    lines.append("=" * 78)
    lines.append(f"DRILLS: {len(rows)} | as expected: {passed} | not as expected: {len(rows) - passed}")
    return "\n".join(lines)


def inside_repo(path: Path) -> bool:
    try:
        Path(os.path.realpath(str(path))).relative_to(Path(os.path.realpath(str(ROOT))))
        return True
    except ValueError:
        return False


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--fake-opencode"]:
        return fake_opencode()
    parser = argparse.ArgumentParser(description="Auto-fix drill: 10 injected failures, real auto-fix flow, offline.")
    parser.add_argument("--only", default="", help="comma-separated drill names (see --list)")
    parser.add_argument("--list", action="store_true", help="list the drills and stop")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    parser.add_argument("--full", action="store_true", help="copy ALL tests and use the real time limits (slow)")
    parser.add_argument("--keep", default="", help="keep the sandboxes in this folder (outside the repo)")
    parser.add_argument("--jobs", type=int, default=4, help="drills side by side (default 4)")
    args = parser.parse_args(argv)
    if args.list:
        for scenario in SCENARIOS:
            print(f"{scenario['key']:<18} {scenario['title']}  [expected: {scenario['expect']}]")
        return 0
    keys = [k.strip() for k in args.only.split(",") if k.strip()] or SCENARIO_KEYS
    unknown = [k for k in keys if k not in SCENARIO_KEYS]
    if unknown:
        parser.error("unknown drill: " + ", ".join(unknown))
    keep = Path(args.keep) if args.keep else None
    if keep is not None and inside_repo(keep):
        parser.error("--keep must be a folder outside the repo")
    rows = run_all(keys, keep=keep, full=args.full, jobs=args.jobs)
    print(json.dumps(rows, indent=1) if args.json else format_report(rows))
    return 0 if all(row["verdict"] == "PASS" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
