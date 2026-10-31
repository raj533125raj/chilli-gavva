"""Safe workflow summaries and a failure streak saved independently of the database."""
import argparse
import ast
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

import notify
import secret_guard

ROOT = Path(__file__).resolve().parent
STEPS = {
    "key": "Check the database key",
    "decrypt": "Open the database",
    "unpack": "Unpack private products",
    "health": "AI health check",
    "tests": "Run tests first",
    "scheduler": "Run scheduled recipe",
    "cycle": "Run one agent cycle",
    "inbox": "Telegram inbox",
    "export": "Export approved content",
    "build": "Build product files from approved drafts",
    "pack": "Pack private products",
    "save": "Save state",
    "report": "Report minutes to the alarm clock",
    "workflow": "Workflow setup",
}
PENDING = "The step stopped before it finished (possibly a timeout)."
UNKNOWN = "The step could not finish."
KEY_MISSING = "The database key is not configured."
ERRORS = {
    "AssertionError": "AssertionError: a check failed.",
    "FileNotFoundError": "FileNotFoundError: a required file is missing.",
    "ModuleNotFoundError": "ModuleNotFoundError: a required Python module is missing.",
    "ImportError": "ImportError: a required Python module could not load.",
    "PermissionError": "PermissionError: access was refused.",
    "TimeoutError": "TimeoutError: a request timed out.",
    "CryptError": "CryptError: the encrypted file could not be opened.",
    "OperationalError": "OperationalError: a database operation failed.",
    "JSONDecodeError": "JSONDecodeError: a response could not be read.",
    "RuntimeError": "RuntimeError: the step could not finish.",
    "TypeError": "TypeError: a value had an unexpected type.",
    "ValueError": "ValueError: a value could not be read.",
    "OSError": "OSError: a file or connection could not be opened.",
}
SETUP_TOOLS = {"pdftoppm", "pdftocairo", "poppler-utils", "Pillow", "apt-get",
               "python3", "bash", "git", "openssl", "required file or tool", "Python module"}
SETUP_PROBLEMS = {"setup problem: " + tool for tool in SETUP_TOOLS}
AUTOFIX_STARTED = "Auto-fix: started"


def autofix_not_started(reason):
    return "Auto-fix: not started — " + reason


def setup_problem(output):
    # Only fixed tool names cross into alerts; paths and arbitrary module names stay private.
    for line in output.splitlines():
        if re.search(r"FileNotFoundError|command not found|not found|No such file|missing tool", line, re.I):
            for tool in ("pdftoppm", "pdftocairo", "poppler-utils", "python3", "bash", "git", "openssl"):
                if re.search(r"(?<![\w.-])" + re.escape(tool) + r"(?![\w.-])", line):
                    return "setup problem: " + tool
            if "listing images skipped" in line and "FileNotFoundError" in line:
                return "setup problem: pdftoppm"
        if re.search(r"(?:ModuleNotFoundError|ImportError):.*(?:'PIL'|'Pillow')", line):
            return "setup problem: Pillow"
        if re.search(r"(?:apt(?:-get)?:.*(?:fail|error)|E: (?:Unable to|Failed to|Could not)|"
                     r"(?:apt|apt-get).*?(?:failed|exit (?:code|status) [1-9]))", line, re.I):
            return "setup problem: apt-get"
    if re.search(r"FileNotFoundError:|command not found|missing tool", output, re.I):
        return "setup problem: required file or tool"
    if "ModuleNotFoundError:" in output:
        return "setup problem: Python module"
    return None


def safe_detail(detail, env=None):
    if detail.startswith("Failed tests: "):
        names = safe_test_names(detail[14:].split(", "), env)
        return "Failed tests: " + ", ".join(names) if names else UNKNOWN
    allowed = {*ERRORS.values(), *SETUP_PROBLEMS, PENDING, UNKNOWN, KEY_MISSING,
               "A command could not complete its work."}
    return detail if detail in allowed else UNKNOWN


def known_tests():
    names = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for method in node.body:
                    if isinstance(method, ast.FunctionDef) and method.name.startswith("test_"):
                        names.add(f"{path.stem}.{node.name}.{method.name}")
    return names


def safe_test_names(names, env=None):
    env = os.environ if env is None else env
    secrets = [value for name, value in env.items() if value and
               re.search(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|ACCOUNT|NAMESPACE|CHAT_ID", name)]
    allowed = known_tests()
    result = []
    for name in names:
        if (name in allowed and len(name) <= 180 and name not in result
                and not any(value in name for value in secrets)
                and not secret_guard.scan_text(name, env_secrets=[])):
            result.append(name)
        if len(result) == 10:
            break
    return result


def problem(output, env=None):
    setup = setup_problem(output)
    if setup:
        return setup
    names = []
    for method, location in re.findall(r"^(?:FAIL|ERROR): (test_\w+) \(([\w.]+)\)", output, re.MULTILINE):
        names.append(location if location.endswith("." + method) else location + "." + method)
    names = safe_test_names(names, env)
    if names:
        return "Failed tests: " + ", ".join(names)
    # Project the last recognized error onto fixed wording; never copy its private details.
    for line in reversed(output.splitlines()):
        match = re.match(r"^(?:[\w.]+\.)?([A-Za-z]+Error|CryptError):", line)
        if match:
            return ERRORS.get(match.group(1), UNKNOWN)
        if line.startswith("db_crypt:"):
            return ERRORS["CryptError"]
        if line.startswith(("fatal:", "error:")):
            return "A command could not complete its work."
    return UNKNOWN


def write_summary(path, step, detail, workflow_file=False):
    text = f"Step: {STEPS.get(step, STEPS['workflow'])}\nProblem: {detail}\n"
    if workflow_file:
        text += "Workflow-file failure: yes\n"
    Path(path).write_text(text, encoding="utf-8")


def read_summary(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        lines = []
    step = next((line[6:] for line in lines if line.startswith("Step: ") and line[6:] in STEPS.values()),
                STEPS["workflow"])
    detail = next((line[9:] for line in lines if line.startswith("Problem: ")), UNKNOWN)
    detail = safe_detail(detail)
    return f"Step: {step}\nProblem: {detail}"


def run_step(step, command, summary_path):
    pending = not Path(summary_path).exists()
    if pending:
        write_summary(summary_path, step, PENDING)
    output = ""
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", check=False)
        output = result.stdout or ""
        # Keep the existing safe inbox diagnostic visible even on a successful command.
        for line in output.splitlines():
            match = re.fullmatch(r"Control inbox: KV (?:read|write) failed \(([^()]+)\)", line)
            if match and (match.group(1) in ERRORS or match.group(1) == "URLError"
                          or re.fullmatch(r"HTTP [1-5][0-9]{2}", match.group(1))
                          or match.group(1) == "HTTP 403 - check API token has Workers KV Storage Edit"):
                print(line)
        status, detail = result.returncode, problem(output) if result.returncode else None
    except OSError as error:
        output = f"{type(error).__name__}: {error}"
        status, detail = 1, problem(output)
    if status:
        workflow_file = any(name in output.lower() for name in
                            (".github/workflows/", "agent.yml", "autofix.yml"))
        write_summary(summary_path, step, detail, workflow_file)
        # Keep the fixed-wording summary for alerts, but expose the real command failure in CI.
        # Prefix each line so untrusted output cannot inject GitHub workflow commands.
        for line in secret_guard.scrub_text(output).splitlines():
            print("Step output: " + line)
        print(read_summary(summary_path))
    else:
        if pending:
            Path(summary_path).unlink(missing_ok=True)
        print(f"{STEPS[step]}: done")
    return status if status > 0 else (1 if status else 0)


def read_state(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        count = data.get("consecutive_failures")
        return (count, data.get("alerted") is True) if type(count) is int and count >= 0 else (0, False)
    except (OSError, ValueError, AttributeError):
        return 0, False


def read_count(path):
    return read_state(path)[0]


def last_autofix_dispatch(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8")).get("last_autofix_dispatch")
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    except (OSError, ValueError, AttributeError):
        return None


def workflow_failure(path, summary):
    try:
        raw = Path(path).read_text(encoding="utf-8").lower()
    except (OSError, UnicodeError):
        raw = ""
    # A product test can mention "workflow" in its method name without testing a workflow file.
    modules = re.findall(r"\b(test_\w+)\.\w+\.test_\w+", summary)
    return ("Workflow-file failure: yes".lower() in raw
            or any(name in raw for name in (".github/workflows/", "agent.yml", "autofix.yml"))
            or any("workflow" in module or module == "test_ci_parity" for module in modules)
            or summary.startswith(("Step: " + STEPS["key"], "Step: " + STEPS["workflow"]))
            or "\nProblem: setup problem: " in summary)


def state_document(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as handle:
        temporary = Path(handle.name)
        json.dump(data, handle)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_count(path, count, alerted=False, autofix_dispatch=None):
    old = state_document(path)
    data = {"consecutive_failures": count, "alerted": alerted}
    last_dispatch = last_autofix_dispatch(path) if autofix_dispatch is None else autofix_dispatch
    if last_dispatch is not None:
        data["last_autofix_dispatch"] = last_dispatch
    for name in ("autofix_retry", "last_success_at"):
        if name in old:
            data[name] = old[name]
    write_state(path, data)


def links(env):
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    repository = env.get("GITHUB_REPOSITORY", "")
    run = env.get("GITHUB_RUN_ID", "")
    if not (re.fullmatch(r"https://[A-Za-z0-9.-]+", server)
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            and re.fullmatch(r"[0-9]+", run)):
        return ""
    base = f"{server}/{repository}"
    return (f"robot-fix-runbook: {base}/blob/main/docs/robot-fix-runbook.md\n"
            f"{base}/actions/runs/{run}")


def start_autofix(state_path, summary_path, summary, count, alerted, env, current, dispatch):
    if workflow_failure(summary_path, summary):
        return autofix_not_started("setup/workflow problem needs a human")
    last = last_autofix_dispatch(state_path)
    if last is not None and current - last < 24 * 3600:
        return autofix_not_started("already tried in the last 24 h")
    if not env.get("GITHUB_TOKEN"):
        return autofix_not_started("no GitHub token")
    # Reserve before the API call: a timeout may still have started the workflow.
    save_count(state_path, count, alerted, autofix_dispatch=current)
    import autofix_retry
    request = autofix_retry.start_request(summary, env)
    autofix_retry.save_retry(state_path, request)
    return autofix_retry.dispatch_request(state_path, request, env, dispatch)


def finish(status, state_path, summary_path, send=None, env=None, dispatch=None, now=None):
    env = os.environ if env is None else env
    current = time.time() if now is None else now
    previous, alerted = read_state(state_path)
    if status == "cancelled":
        save_count(state_path, previous, alerted)
        return previous
    failed = status == "failure" or Path(summary_path).exists()
    count = previous + 1 if failed else 0
    retry_reported = False
    save_count(state_path, count, alerted if failed else False)
    if not failed:
        import autofix_retry
        autofix_retry.cancel(state_path, current)
    if count == 1:
        print("temporary problem, will retry")
    elif count >= 2 and not alerted:
        summary = read_summary(summary_path)
        import autofix_retry
        outcomes = []
        # A due retry can coincide with the first repeated-failure alert (for example after a drill).
        # Include that actual result instead of claiming the daily guard blocked all dispatches.
        autofix_retry.dispatch_due(state_path, env, current, dispatch, summary, report=outcomes.append)
        retry_reported = bool(outcomes)
        outcome = (outcomes[0] if outcomes else
                   start_autofix(state_path, summary_path, summary, count, alerted, env, current, dispatch))
        print(outcome)
        message = ("Chilli Gavva: run FAILED 2 times in a row.\n" + summary
                   + "\nWhat to do: open the robot-fix-runbook, paste this message into Codex.\n"
                   + links(env) + "\n" + outcome)
        try:
            delivered = (send or notify.send)(message)
        except Exception:
            delivered = False
        if delivered:
            save_count(state_path, count, True)
        else:
            print("The repeated failure alert could not be delivered.")
    elif count >= 2:
        print("Repeated failure alert already delivered.")
    if failed:
        import autofix_retry
        if not retry_reported:
            autofix_retry.dispatch_due(state_path, env, current, dispatch, read_summary(summary_path))
        autofix_retry.notify_terminal(state_path, send)
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser("run")
    run.add_argument("step", choices=STEPS)
    run.add_argument("command", nargs=argparse.REMAINDER)
    commands.add_parser("finish")
    args = parser.parse_args(argv)
    directory = Path(os.environ["RUNNER_TEMP"])
    summary = directory / "failure.txt"
    if args.action == "run":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            parser.error("a command is required")
        return run_step(args.step, command, summary)
    status = os.environ.get("RUN_STATUS", "failure")
    if status == "success" and os.environ.get("ROBOT_RAN") == "false":
        status = "cancelled"
    finish(status, directory / "robot-failure-state.json", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
