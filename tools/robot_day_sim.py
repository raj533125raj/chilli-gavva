"""tools/robot_day_sim.py — a simulated robot day with no network (CCE-1).

Runs the robot's REAL daily cycle — the same commands .github/workflows/agent.yml runs, in the same
order, through run_failure.py like CI — again and again over a simulated day (default 24 h, one run
per hour like the alarm clock). Each step is its own `python3` process, exactly like a workflow step.

Where it runs: a fresh temporary copy of the code (git-tracked files only; never agent_data.db*,
products.tar.gz.enc, config.json or tests). The robot starts from an empty state there, like a new
install. The repo folder, the real database and real secrets are never read or written.

What is fake (see tools/robot_sim_world.py): the clock, Telegram, the free AI providers, Cloudflare KV,
the alarm clock Worker and the GitHub API. Every other host is refused. Secrets are random fake values
with the right shape, made fresh for each simulation.

What the simulated owner does during the day:
  * starts three niche agents by hand before the first run (budget sheets, ADHD planners, 2027 planners)
  * presses the Approve button the robot sent for one draft of each niche (so 3 product types build),
    and Reject on one other draft
  * sends every owner command (core + every tg_*.py plugin), some with a product number once
    products exist, and two Tenglish chat lines
The day also covers the morning post plan, inbox polls, the daily report (after 03:30 UTC),
auto-approve, the night niche slot (21:00 UTC) and maintenance (maintenance.run_if_due, the Mac mode
daily clean-up).

Output: every crash (uncaught traceback) and every caught crash (the robot printed
"... skipped: KeyError" inside an except block) with file:line, failed steps, Telegram 400 answers
(the robot sent something Telegram refuses), blocked network hosts, and the owner messages that
look like error lines.

Usage:
  python3 tools/robot_day_sim.py                     # 24 h, one run per hour
  python3 tools/robot_day_sim.py --hours 3 --every 60
  python3 tools/robot_day_sim.py --json              # machine-readable report
  python3 tools/robot_day_sim.py --keep /tmp/simday  # keep the sandbox (must be outside the repo)
Exit code: 0 = no errors, 1 = errors found.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import glob
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
WORLD = Path(__file__).resolve().with_name("robot_sim_world.py")

# Never copied into the sandbox: real state, encrypted secrets, local settings, tests, job notes.
EXCLUDE_NAMES = {"agent_data.db", "agent_data.db.enc", "products.tar.gz.enc", "config.json", ".env"}
EXCLUDE_PREFIXES = ("tests/", ".jobs/", ".git/", ".github/", "__pycache__/", "products.tar.gz.enc.parts/")
SEED_NICHES = ("niche_budget_sheets", "niche_adhd_planners", "niche_planners_2027")
OWNER_CHAT = str(int("1234" "5678"))
SKIP_COMMANDS = {"/pause", "/resume", "/undo", "/approve", "/reject", "/start"}
NEEDS_ID = {"/view", "/files", "/sell", "/kit", "/pack", "/covers", "/pins", "/carousel", "/reel", "/blog",
            "/landing", "/launch", "/seokit", "/asset", "/ready", "/listed", "/post", "/posted", "/bundle"}
CHAT_LINES = ("ivala em jarigindi?", "drafts enni unnayi?")
OWNER_ERROR_WORDS = re.compile(r"(?i:traceback|problem vachindi|(?<!\b0 )\bfailed\b|pani cheyaledu|exception)|[A-Za-z]+Error\b")
# Optional modules CI installs (Pillow) or never needs: missing here = this machine, not a robot bug.
OPTIONAL_MODULES = ("PIL", "fitz", "reportlab", "openpyxl", "docx")
# Sunday 12:05 UTC: the day crosses UTC midnight (two daily build slots each side), the Sunday night slot
# (21:00 UTC) and the Monday morning daily report (after 03:30 UTC) both happen.
DEFAULT_START = "2026-11-15T12:05:00Z"


# ---- sandbox -----------------------------------------------------------------------------------
def tracked_files(root: Path = ROOT) -> List[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=str(root), stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, check=True).stdout.decode("utf-8", "replace")
        names = [n for n in out.split("\0") if n]
    except (OSError, subprocess.CalledProcessError):
        names = []
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".jobs", "tests")]
            for name in files:
                names.append(os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/"))
    keep = []
    for name in names:
        base = name.rsplit("/", 1)[-1]
        if base in EXCLUDE_NAMES or base.startswith("agent_data.db") or name.startswith(EXCLUDE_PREFIXES):
            continue
        if base.endswith((".pyc", ".enc")):
            continue
        keep.append(name)
    return keep


def make_sandbox(base: Path, root: Path = ROOT) -> Dict[str, Path]:
    code = base / "robot"
    sim = base / "sim"
    hooks = base / "hooks"
    runner = base / "runner_temp"
    home = base / "home"
    for folder in (code, sim, hooks, runner, home):
        folder.mkdir(parents=True, exist_ok=True)
    for name in tracked_files(root):
        source = root / name
        if not source.is_file():
            continue
        target = code / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(target))
    shutil.copy2(str(WORLD), str(hooks / "robot_sim_world.py"))
    (hooks / "sitecustomize.py").write_text(
        "import os\n"
        "if os.environ.get('ROBOT_SIM_DIR'):\n"
        "    import robot_sim_world\n"
        "    robot_sim_world.install()\n", encoding="utf-8")
    return {"base": base, "code": code, "sim": sim, "hooks": hooks, "runner": runner, "home": home}


def fake_secrets() -> Dict[str, str]:
    """Random values with the right SHAPE (CCD-2 checks shapes). Never real, never reused."""
    return {
        "TELEGRAM_BOT_TOKEN": "7" + str(secrets.randbelow(10 ** 8)).zfill(8) + ":" + secrets.token_urlsafe(26)[:35],
        "TELEGRAM_CHAT_ID": OWNER_CHAT,
        "GROQ_API_KEY": "gsk_" + secrets.token_hex(24),
        "GEMINI_API_KEY": "AIza" + secrets.token_hex(17)[:35],
        "GEMINI_API_KEY_2": "AIza" + secrets.token_hex(17)[:35],
        "OPENROUTER_API_KEY": "sk-or-v1-" + secrets.token_hex(32),
        "CF_API_TOKEN": secrets.token_urlsafe(30)[:40],
        "CF_ACCOUNT_ID": secrets.token_hex(16),
        "CF_KV_NAMESPACE_ID": secrets.token_hex(16),
        "CONTROL_SIGNING_KEY": secrets.token_hex(32),
        # random host: a fixed URL in this (copied) file would trip the robot's own secret guard
        "ALWAYS_ON_URL": "https://alarm-" + secrets.token_hex(4) + ".sim.invalid",
        "ALWAYS_ON_SECRET": secrets.token_hex(16),
        "GITHUB_TOKEN": "ghs_" + secrets.token_hex(18),
        "GITHUB_MODELS_TOKEN": "ghs_" + secrets.token_hex(18),
    }


def base_env(paths: Dict[str, Path], secret_values: Dict[str, str]) -> Dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(paths["home"]),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "TZ": "UTC",   # GitHub runners run in UTC
        "PYTHONPATH": str(paths["hooks"]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "TMPDIR": str(paths["runner"]),
        "ROBOT_SIM_DIR": str(paths["sim"]),
        "ROBOT_SIM_SANDBOX": str(paths["code"]),
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "sim/robot",
        "GITHUB_SERVER_URL": "https://github.sim.invalid",
        "GITHUB_RUN_ID": "1",
        "GITHUB_OUTPUT": str(paths["runner"] / "github_output.txt"),
        "GITHUB_ENV": str(paths["runner"] / "github_env.txt"),
        "GITHUB_STEP_SUMMARY": str(paths["runner"] / "step_summary.md"),
        "RUNNER_TEMP": str(paths["runner"]),
        "GEMINI_MODEL": "", "OPENROUTER_MODEL": "", "GITHUB_MODELS": "",
    }
    import site
    user_base = getattr(site, "USER_BASE", None) or site.getuserbase()
    if user_base:   # HOME is a temp folder: keep pip --user packages (Pillow) visible
        env["PYTHONUSERBASE"] = user_base
    env.update(secret_values)
    return env


# ---- the CI steps (agent.yml order) -------------------------------------------------------------
SAVE_SCRIPT = ("set -e\n{py} sanitize_state.py\n{py} secret_guard.py --save\nset +e\n{py} save_check.py\nrc=$?\n"
               "set -e\nif [ \"$rc\" = 3 ] || [ \"$rc\" = 0 ]; then exit 0; fi\nexit \"$rc\"\n")
IMAGE_PROBE = ("import os\nimport cubes\nimport guardrails\nimport product_builder\n"
               "needed = (guardrails.automation_allowed() and cubes.get_cube_state('product') == 'on'\n"
               "          and any(product_builder.niche_of(item['title']) for item in product_builder._todo()))\n"
               "with open(os.environ['GITHUB_OUTPUT'], 'a') as output:\n"
               "    print('needed=' + str(needed).lower(), file=output)\n")
MAINTENANCE = "import maintenance\nprint('maintenance:', maintenance.run_if_due())\n"


def cycle_steps(py: str) -> List[Dict[str, Any]]:
    """name, command, continue_on_error, always (like `if: always()`)."""
    wrap = [py, "run_failure.py", "run"]
    return [
        {"name": "secret_guard", "cmd": [py, "secret_guard.py"], "coe": False},
        {"name": "gate", "cmd": [py, "always_on.py", "gate", "--reason", "alarm", "--force", "no"], "coe": False},
        {"name": "scheduler", "cmd": wrap + ["scheduler", "--", py, "scheduler.py"], "coe": False},
        {"name": "cycle", "cmd": wrap + ["cycle", "--", py, "cloud_runner.py"], "coe": False},
        {"name": "inbox", "cmd": wrap + ["inbox", "--", py, "telegram_inbox.py"], "coe": False},
        {"name": "export", "cmd": wrap + ["export", "--", py, "content_export.py"], "coe": False},
        {"name": "image_probe", "cmd": [py, "-c", IMAGE_PROBE], "coe": True},
        {"name": "build", "cmd": wrap + ["build", "--", py, "product_builder.py", "--max", "2"], "coe": True},
        {"name": "pack", "cmd": wrap + ["pack", "--", py, "scripts/db_crypt.py", "pack-products"], "coe": False,
         "env": {"DB_KEY": "sim-" + secrets.token_hex(24)}},
        {"name": "save", "cmd": wrap + ["save", "--", "bash", "-c", SAVE_SCRIPT.format(py=py)], "coe": False},
        {"name": "maintenance", "cmd": [py, "-c", MAINTENANCE], "coe": True},
        {"name": "report", "cmd": wrap + ["report", "--", py, "always_on.py", "report"], "coe": True,
         "always": True},
    ]


# ---- reading the simulated world --------------------------------------------------------------
def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def db_rows(code: Path, sql: str, args: tuple = ()) -> List[tuple]:
    path = code / "agent_data.db"
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def plugin_commands(code: Path) -> List[str]:
    cmds: List[str] = []
    text = (code / "telegram_inbox.py").read_text(encoding="utf-8")
    cmds.extend(sorted(set(re.findall(r'"(/[a-z_]+)', text))))
    for path in sorted(glob.glob(str(code / "tg_*.py"))):
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "COMMANDS" for t in node.targets):
                try:
                    cmds.extend(c.get("cmd") for c in ast.literal_eval(node.value) if isinstance(c, dict))
                except ValueError:
                    pass
    seen, out = set(), []
    for cmd in cmds:
        if cmd and cmd not in seen and cmd not in SKIP_COMMANDS:
            seen.add(cmd)
            out.append(cmd)
    # Commands that take a product number go last, when products exist.
    return [c for c in out if c not in NEEDS_ID] + [c for c in out if c in NEEDS_ID]


class Owner:
    """The simulated owner: reads what the robot sent and answers through the fake Telegram queue."""

    def __init__(self, paths: Dict[str, Path], runs: int):
        self.paths = paths
        self.next_update = 1000
        self.next_message = 1
        self.pressed: set = set()
        self.approved_niches: set = set()
        self.rejected = 0
        self.commands = ["/start"] + plugin_commands(paths["code"]) + list(CHAT_LINES)
        self.per_run = max(1, -(-len(self.commands) // max(1, runs - 1)))
        self.sent_commands: List[str] = []

    def _queue(self, updates: List[Dict[str, Any]]) -> None:
        path = self.paths["sim"] / "tg_updates.json"
        try:
            queue = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            queue = []
        queue.extend(updates)
        path.write_text(json.dumps(queue), encoding="utf-8")

    def _message(self, text: str, when: float) -> Dict[str, Any]:
        self.next_update += 1
        self.next_message += 1
        chat = int(OWNER_CHAT)
        return {"update_id": self.next_update, "message": {
            "message_id": self.next_message, "date": int(when), "text": text,
            "from": {"id": chat, "is_bot": False, "first_name": "Owner"},
            "chat": {"id": chat, "type": "private", "first_name": "Owner"}}}

    def _press(self, data: str, message_id: int, when: float) -> Dict[str, Any]:
        self.next_update += 1
        chat = int(OWNER_CHAT)
        return {"update_id": self.next_update, "callback_query": {
            "id": str(self.next_update), "data": data, "chat_instance": "1",
            "from": {"id": chat, "is_bot": False, "first_name": "Owner"},
            "message": {"message_id": message_id, "date": int(when), "text": "",
                        "chat": {"id": chat, "type": "private"}}}}

    def act(self, run_index: int, when: float) -> List[str]:
        updates, notes = [], []
        drafts = {row[0]: row[1] or "" for row in db_rows(
            self.paths["code"], "SELECT id, title FROM approvals WHERE status = 'pending'")}
        for sent in read_jsonl(self.paths["sim"] / "telegram_out.jsonl"):
            for button in sent.get("buttons") or []:
                data = str(button.get("data") or "")
                if data == "t:ping" and data not in self.pressed:   # the /buttontest button
                    self.pressed.add(data)
                    updates.append(self._press(data, int(sent.get("message_id") or 1), when))
                    notes.append("owner presses the /buttontest button")
                    continue
                match = re.fullmatch(r"a:(\d+)", data)
                if not match or data in self.pressed:
                    continue
                aid = int(match.group(1))
                title = drafts.get(aid)
                if title is None:
                    continue
                niche = re.search(r"\[([^\]]+ Agent)\]", title)
                niche_name = niche.group(1) if niche else ""
                if niche_name and niche_name not in self.approved_niches and len(self.approved_niches) < 3:
                    self.approved_niches.add(niche_name)
                    self.pressed.add(data)
                    updates.append(self._press(data, int(sent.get("message_id") or 1), when))
                    notes.append(f"owner presses Approve on draft {aid} ({niche_name})")
                elif self.rejected < 1 and niche_name in self.approved_niches:
                    self.rejected += 1
                    self.pressed.add(data)
                    updates.append(self._press(f"r:{aid}", int(sent.get("message_id") or 1), when))
                    notes.append(f"owner presses Reject on draft {aid}")
        built = [row[0] for row in db_rows(self.paths["code"],
                                           "SELECT approval_id FROM product_builds ORDER BY approval_id")]
        start = run_index * self.per_run
        extra_path = self.paths["sim"] / "owner_extra.json"
        try:
            extra = json.loads(extra_path.read_text(encoding="utf-8"))
            extra_path.unlink()
        except (OSError, ValueError):
            extra = []
        for text in extra + self.commands[start:start + self.per_run]:
            if text in NEEDS_ID and built:
                text = f"{text} {built[0]}"
            updates.append(self._message(text, when))
            self.sent_commands.append(text)
            notes.append(f"owner sends {text}")
        if updates:
            self._queue(updates)
        return notes


# ---- running -----------------------------------------------------------------------------------
def _iso(epoch: float) -> str:
    return dt.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_step(step: Dict[str, Any], paths: Dict[str, Path], env: Dict[str, str], sim_now: float,
             timeout: int, run: int = 0) -> Dict[str, Any]:
    child = dict(env, ROBOT_SIM_NOW=repr(sim_now), ROBOT_SIM_STEP=step["name"], ROBOT_SIM_RUN=str(run))
    child.update(step.get("env") or {})
    started = time.time()
    try:
        proc = subprocess.run(step["cmd"], cwd=str(paths["code"]), env=child, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout)
        output = proc.stdout.decode("utf-8", "replace")
        code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", "replace") + "\n[simulator] step timed out"
        code = 124
    logs = paths["sim"] / "steps"
    logs.mkdir(exist_ok=True)
    safe = re.sub(r"[^a-z0-9_.-]+", "_", step["name"].lower())
    (logs / f"run{run:02d}-{safe}.txt").write_text(output, encoding="utf-8")
    return {"name": step["name"], "code": code, "output": output, "seconds": round(time.time() - started, 1)}


def fresh_runner_temp(folder: Path, keep=("robot-failure-state.json",)) -> None:
    """A new GitHub job starts with an empty RUNNER_TEMP; agent.yml restores only the failure counter."""
    folder.mkdir(parents=True, exist_ok=True)
    for item in folder.iterdir():
        if item.name in keep:
            continue
        if item.is_dir():
            shutil.rmtree(str(item), ignore_errors=True)
        else:
            item.unlink()


def seed(paths: Dict[str, Path], env: Dict[str, str], py: str, sim_now: float, timeout: int) -> List[Dict]:
    results = []
    for niche in SEED_NICHES:
        step = {"name": f"seed:{niche}", "cmd": [py, "recipe_runner.py", f"recipes/{niche}.yaml"]}
        results.append(run_step(step, paths, env, sim_now, timeout))
    return results


# ---- bad day (CCE-3) ---------------------------------------------------------------------------
def _faults(paths: Dict[str, Path], rules: List[Dict[str, Any]]) -> None:
    (paths["sim"] / "faults.json").write_text(json.dumps(rules), encoding="utf-8")
    (paths["sim"] / "fault_counts.json").write_text("{}", encoding="utf-8")


def _half_write(path: Path) -> bool:
    """Cut a file in the middle, like a run killed while writing it."""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    path.write_bytes(data[: max(1, len(data) // 2)])
    return True


def _corrupt_pdf(paths: Dict[str, Path]) -> Optional[str]:
    pdfs = sorted(glob.glob(str(paths["code"] / "exported_content" / "products" / "*" / "*.pdf")))
    if not pdfs:
        return None
    Path(pdfs[0]).write_bytes(b"%PDF-1.4\n%garbage\x00\xff" + secrets.token_bytes(64))
    return os.path.relpath(pdfs[0], paths["code"])


# (first run index, last run index, name, what happens). The fault is active from `first` to `last`;
# the run after `last` must recover.
BAD_DAY = [
    (2, 2, "telegram_429_500", "Telegram answers 429 (3x) then 500 (2x)"),
    (5, 6, "ai_down", "every AI provider answers 503 for two runs"),
    (8, 9, "kv_wrong_id", "Cloudflare KV says namespace not found (404)"),
    (12, 12, "disk_full", "disk full while writing product files (first build slot of the new UTC day)"),
    (14, 14, "corrupt_pdf", "a built product PDF is garbage; owner asks /files and /sell for it"),
    (16, 16, "half_written_state", "state files cut in half (post plan, status.json, failure state, dup report)"),
    (18, 18, "clock_jump_forward", "clock jumps 2 days forward"),
    (20, 20, "clock_jump_back", "clock jumps 3 hours back"),
]


def bad_day_events(every: int = 60) -> Dict[int, Callable[[Dict[str, Path], Dict[str, str]], Optional[float]]]:
    events: Dict[int, Callable] = {}

    def add(index, fn):
        old = events.get(index)
        if old:
            def both(paths, env, a=old, b=fn):
                first, second = a(paths, env), b(paths, env)
                return second or first
            events[index] = both
        else:
            events[index] = fn

    def clear(paths, env):
        _faults(paths, [])
        return None

    add(2, lambda p, e: _faults(p, [{"service": "telegram", "status": 429, "times": 3, "retry_after": 0},
                                    {"service": "telegram", "status": 500, "times": 2}]))
    add(3, clear)
    add(5, lambda p, e: _faults(p, [{"service": "llm", "status": 503}]))
    add(7, clear)
    add(8, lambda p, e: _faults(p, [{"service": "kv", "status": 404}]))
    add(10, clear)
    add(12, lambda p, e: _faults(p, [{"service": "disk", "path": os.path.join("exported_content", "products")}]))
    add(13, clear)

    def corrupt(paths, env):
        rel = _corrupt_pdf(paths)
        match = re.search(r"products/(\d+)-", rel or "")
        if match:   # the owner then asks for exactly that product
            _owner_extra(paths, [f"/files {match.group(1)}", f"/sell {match.group(1)}", f"/kit {match.group(1)}"])
        return None
    add(14, corrupt)

    def half(paths, env):
        for rel in ("exported_content/products/post_plan.json", "web/data/status.json",
                    "exported_content/marketing/dup_report.json"):
            _half_write(paths["code"] / rel)
        _half_write(paths["runner"] / "robot-failure-state.json")
        return None
    add(16, half)

    add(18, lambda p, e: 2 * 24 * 3600.0)     # an event may return a clock jump in seconds
    add(20, lambda p, e: -3 * 3600.0)
    return events


def _owner_extra(paths: Dict[str, Path], commands: List[str]) -> None:
    path = paths["sim"] / "owner_extra.json"
    try:
        queued = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        queued = []
    path.write_text(json.dumps(queued + commands), encoding="utf-8")


def simulate(hours: float = 24, every: int = 60, start: str = DEFAULT_START, keep: Optional[str] = None,
             events: Optional[Dict[int, Callable[[Dict[str, Path], Dict[str, str]], Optional[float]]]] = None,
             step_timeout: int = 300, log: Callable[[str], None] = lambda line: None,
             python: Optional[str] = None, scenarios: Optional[List[tuple]] = None) -> Dict[str, Any]:
    """Run the simulated day. `events` (CCE-3): run index -> fn(paths, env) called before that run;
    it may change faults/files and may return a clock jump in seconds (all later runs move with it)."""
    py = python or sys.executable
    if keep:
        base = Path(keep).resolve()
        if base == ROOT or ROOT in base.parents:
            raise SystemExit("--keep must be outside the repo folder")
        base.mkdir(parents=True, exist_ok=True)
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory(prefix="robot_day_sim_")
        base = Path(cleanup.name)
    try:
        paths = make_sandbox(base)
        env = base_env(paths, fake_secrets())
        report = _simulate(paths, env, py, hours, every, start, events or {}, step_timeout, log, scenarios)
        report["sandbox"] = str(base) if keep else "(deleted)"
        return report
    finally:
        if cleanup is not None:
            cleanup.cleanup()


def _simulate(paths, env, py, hours, every, start, events, step_timeout, log, scenarios=None) -> Dict[str, Any]:
    epoch = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
    runs = max(1, int(hours * 60 // every))
    owner = Owner(paths, runs)
    steps_run: List[Dict[str, Any]] = []
    timeline: List[str] = []
    failed_runs = 0
    for result in seed(paths, env, py, epoch, step_timeout):
        steps_run.append(dict(result, run=0, t=_iso(epoch)))
        if result["code"]:
            timeline.append(f"{_iso(epoch)} seed {result['name']} exit {result['code']}")
    for index in range(runs):
        sim_now = epoch + index * every * 60
        event = events.get(index)
        if event:
            jump = event(paths, env)
            if jump:
                epoch += jump
                sim_now += jump
            timeline.append(f"{_iso(sim_now)} event before run {index + 1}")
        for note in owner.act(index, sim_now):
            timeline.append(f"{_iso(sim_now)} {note}")
        log(f"run {index + 1}/{runs} at {_iso(sim_now)}")
        fresh_runner_temp(paths["runner"])
        real_start = time.time()
        job_failed = False
        inbox_started = False
        for step in cycle_steps(py):
            if job_failed and not step.get("always"):
                continue
            now = sim_now + (time.time() - real_start)
            inbox_started = inbox_started or step["name"] == "inbox"
            result = run_step(step, paths, env, now, step_timeout, index + 1)
            steps_run.append(dict(result, run=index + 1, t=_iso(now), coe=bool(step["coe"])))
            if result["code"] and step["name"] == "gate":
                job_failed = True
            if result["code"] and not step["coe"]:
                job_failed = True
                timeline.append(f"{_iso(now)} run {index + 1}: step {step['name']} FAILED (exit {result['code']})")
            elif result["code"]:
                timeline.append(f"{_iso(now)} run {index + 1}: step {step['name']} failed (continue-on-error)")
        now = sim_now + (time.time() - real_start)
        finish_env = dict(env, RUN_STATUS="failure" if job_failed else "success", ROBOT_RAN="true")
        result = run_step({"name": "finish", "cmd": [py, "run_failure.py", "finish"]}, paths, finish_env,
                          now, step_timeout, index + 1)
        steps_run.append(dict(result, run=index + 1, t=_iso(now)))
        if job_failed:
            failed_runs += 1
        if job_failed and not inbox_started:   # agent.yml recovery_inbox: own job, own empty RUNNER_TEMP
            recovery_temp = paths["base"] / "recovery_temp"
            fresh_runner_temp(recovery_temp, keep=())
            recovery = {"name": "recovery", "cmd": [py, "run_failure.py", "run", "inbox", "--", "bash", "-c",
                                                    f"set -e\n{py} telegram_inbox.py --recovery\n"
                                                    f"{py} sanitize_state.py\n{py} secret_guard.py agent_data.db\n"]}
            result = run_step(recovery, paths, dict(env, RUNNER_TEMP=str(recovery_temp)), now, step_timeout,
                              index + 1)
            steps_run.append(dict(result, run=index + 1, t=_iso(now)))
    report = build_report(paths, steps_run, timeline, owner, runs, failed_runs)
    if scenarios:
        report["scenarios"] = scenario_report(paths, steps_run, scenarios)
    return report


def scenario_report(paths, steps_run, scenarios) -> List[Dict[str, Any]]:
    """Per injected failure: what the owner got on Telegram while it lasted and in the run after it, every error
    record from those runs, failed CI steps, and whether the run after the fault was clean (= recovered).
    Fault index i is run i + 1."""
    telegram = read_jsonl(paths["sim"] / "telegram_out.jsonl")
    raw_errors = read_jsonl(paths["sim"] / "errors.jsonl")
    hits = read_jsonl(paths["sim"] / "faults_hit.jsonl")
    out = []
    for first, last, name, what in scenarios:
        window = {str(r) for r in range(first + 1, last + 3)}
        in_window = lambda rec: str(rec.get("run")) in window   # by run number: clock jumps repeat times
        after = [s for s in steps_run if s.get("run") == last + 2 and s.get("name") != "finish"]
        out.append({
            "name": name, "what": what, "runs": f"{first + 1}-{last + 1}",
            "fault_hits": sum(1 for h in hits if in_window(h)),
            "errors": [f"[{e.get('kind')}] {e.get('type')} at {e.get('file')}:{e.get('line')} ({e.get('step')})"
                       for e in raw_errors if in_window(e)],
            "telegram_refused": [m.get("error") for m in telegram if m.get("ok") is False and in_window(m)],
            "failed_steps": [f"run {s['run']} {s['name']}" for s in steps_run if in_window(s)
                             and s["code"] and not s.get("coe")],
            "owner_messages": [{"t": m.get("t"), "step": m.get("step"), "run": m.get("run"),
                                "text": (m.get("text") or "")[:240]}
                               for m in telegram if m.get("ok") and str(m.get("method", "")).startswith("send")
                               and in_window(m)],
            "next_run_clean": bool(after) and not any(s["code"] and not s.get("coe") for s in after),
        })
    return out


# ---- report ------------------------------------------------------------------------------------
def _exit_without_trace(step: Dict[str, Any], errors: List[Dict[str, Any]]) -> bool:
    return step["code"] != 0 and not any(
        e.get("kind") == "crash" and e.get("step") == step["name"] and str(e.get("run")) == str(step["run"])
        for e in errors)


def build_report(paths, steps_run, timeline, owner, runs, failed_runs) -> Dict[str, Any]:
    sim = paths["sim"]
    raw_errors = read_jsonl(sim / "errors.jsonl")
    telegram = read_jsonl(sim / "telegram_out.jsonl")
    blocked = read_jsonl(sim / "blocked.jsonl")
    llm = read_jsonl(sim / "llm_log.jsonl")
    grouped: Dict[tuple, Dict[str, Any]] = {}
    for err in raw_errors:
        key = (err.get("kind"), err.get("type"), err.get("file"), err.get("line"))
        item = grouped.setdefault(key, dict(err, count=0, steps=set()))
        item["count"] += 1
        item["steps"].add(err.get("step"))
    errors, environment = [], []
    for item in grouped.values():
        item["steps"] = sorted(s for s in item["steps"] if s)
        missing = re.search(r"No module named '([^'.]+)", str(item.get("message") or ""))
        if item.get("type") == "ModuleNotFoundError" and missing and missing.group(1) in OPTIONAL_MODULES:
            environment.append(f"{missing.group(1)} not installed here (CI installs it): "
                               f"{item.get('file')}:{item.get('line')} x{item['count']}")
            continue
        errors.append(item)
    for step in steps_run:
        if step["code"] and _exit_without_trace(step, raw_errors):
            tail = [line for line in step["output"].splitlines() if line.strip()][-6:]
            errors.append({"kind": "exit", "type": f"exit {step['code']}", "file": step["name"], "line": 0,
                           "message": " | ".join(tail)[-600:], "count": 1, "steps": [step["name"]],
                           "t": step["t"], "traceback": step["output"][-3000:]})
    tg_bad = [m for m in telegram if m.get("ok") is False]
    for bad in tg_bad:
        errors.append({"kind": "telegram400", "type": bad.get("error"), "file": bad.get("step"), "line": 0,
                       "message": f"{bad.get('method')}: {bad.get('text', '')[:120]}", "count": 1,
                       "steps": [bad.get("step")], "t": bad.get("t")})
    owner_lines = [m for m in telegram if m.get("ok") and m.get("method") in ("sendMessage", "editMessageText")
                   and OWNER_ERROR_WORDS.search(m.get("text") or "")]
    kinds = [row[0] for row in db_rows(paths["code"], "SELECT folder FROM product_builds WHERE status = 'built'")]
    built = db_rows(paths["code"], "SELECT approval_id, status, note FROM product_builds ORDER BY approval_id")
    drafts = db_rows(paths["code"], "SELECT status, COUNT(*) FROM approvals GROUP BY status")
    return {
        "runs": runs, "failed_runs": failed_runs, "steps": len(steps_run),
        "errors": sorted(errors, key=lambda e: (e.get("kind") != "crash", str(e.get("file")), e.get("line") or 0)),
        "telegram_sent": sum(1 for m in telegram if m.get("ok") and m.get("method", "").startswith(("send", "edit"))),
        "telegram_refused": len(tg_bad),
        "owner_error_lines": [{"t": m.get("t"), "step": m.get("step"), "text": (m.get("text") or "")[:300]}
                              for m in owner_lines],
        "environment": environment,
        "blocked_hosts": sorted({b.get("host") for b in blocked if b.get("host")}),
        "llm_calls": len(llm), "llm_labels": _count(l.get("label") for l in llm),
        "drafts": {status: count for status, count in drafts},
        "builds": [{"approval_id": a, "status": s, "note": (n or "")[:120]} for a, s, n in built],
        "built_folders": kinds,
        "owner_commands": owner.sent_commands, "approved_niches": sorted(owner.approved_niches),
        "timeline": timeline,
        "slow_steps": sorted(({"name": s["name"], "run": s["run"], "seconds": s["seconds"]} for s in steps_run),
                             key=lambda s: -s["seconds"])[:5],
    }


def _count(items) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in items:
        out[str(item)] = out.get(str(item), 0) + 1
    return out


def text_report(report: Dict[str, Any], verbose: bool = False) -> str:
    lines = [f"ROBOT DAY SIMULATION: {report['runs']} run(s), {report['steps']} step(s), "
             f"{report['failed_runs']} failed run(s)",
             f"Telegram messages sent: {report['telegram_sent']}  refused by Telegram: {report['telegram_refused']}",
             f"AI calls (fake): {report['llm_calls']} {report['llm_labels']}",
             f"Drafts: {report['drafts']}  approved niches: {report['approved_niches']}",
             "Builds: " + (", ".join(f"#{b['approval_id']} {b['status']}" for b in report["builds"]) or "none")]
    for note in report.get("environment") or []:
        lines.append("Environment (not counted): " + note)
    if report["blocked_hosts"]:
        lines.append("Blocked network hosts (robot tried to reach them): " + ", ".join(report["blocked_hosts"]))
    lines.append("")
    lines.append(f"ERRORS: {len(report['errors'])}")
    for err in report["errors"]:
        where = f"{err.get('file')}:{err.get('line')}" if err.get("line") else str(err.get("file"))
        lines.append(f"- [{err.get('kind')}] {err.get('type')} at {where} x{err.get('count', 1)} "
                     f"(steps {', '.join(str(s) for s in err.get('steps') or [])}) first {err.get('t')}")
        if err.get("message"):
            lines.append(f"    {str(err['message'])[:300]}")
        if err.get("note"):
            lines.append(f"    robot printed: {err['note'][:200]}")
        if verbose and err.get("traceback"):
            lines.extend("    | " + l for l in str(err["traceback"]).rstrip().splitlines()[-14:])
    if report["owner_error_lines"]:
        lines.append("")
        lines.append(f"Owner saw {len(report['owner_error_lines'])} problem-looking message(s):")
        for item in report["owner_error_lines"][:20]:
            lines.append(f"- {item['t']} [{item['step']}] {item['text'][:160]!r}")
    if verbose:
        lines.append("")
        lines.append("Timeline:")
        lines.extend("  " + t for t in report["timeline"])
    for sc in report.get("scenarios") or []:
        lines.append("")
        lines.append(f"BAD DAY {sc['name']} (runs {sc['runs']}): {sc['what']} | fault hits {sc['fault_hits']} | "
                     f"next run clean: {'YES' if sc['next_run_clean'] else 'NO'}")
        for item in sc["failed_steps"]:
            lines.append(f"  failed step: {item}")
        for item in sc["errors"]:
            lines.append(f"  error: {item}")
        for item in sc["telegram_refused"]:
            lines.append(f"  telegram refused: {item}")
        for msg in sc["owner_messages"]:
            lines.append(f"  owner got [{msg['step']}] {msg['text'][:150]!r}")
    lines.append("")
    lines.append("Slowest steps: " + ", ".join(f"{s['name']}#{s['run']} {s['seconds']}s" for s in report["slow_steps"]))
    lines.append("RESULT: " + ("0 errors" if not report["errors"] else f"{len(report['errors'])} error(s)"))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--every", type=int, default=60, help="minutes between runs (alarm clock)")
    parser.add_argument("--start", default=DEFAULT_START, help="simulated UTC start, e.g. 2026-11-16T00:05:00Z")
    parser.add_argument("--keep", default=None, help="keep the sandbox in this folder (outside the repo)")
    parser.add_argument("--bad", action="store_true", help="bad day: inject the CCE-3 failures (see BAD_DAY)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true", help="tracebacks and timeline")
    args = parser.parse_args(argv)
    report = simulate(args.hours, args.every, args.start, args.keep,
                      events=bad_day_events() if args.bad else None, scenarios=BAD_DAY if args.bad else None,
                      log=lambda line: print(line, file=sys.stderr, flush=True))
    if args.json:
        print(json.dumps(report, indent=1, default=str))
    else:
        print(text_report(report, args.verbose))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
