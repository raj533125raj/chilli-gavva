"""Auto-fix requests contain only the robot's safe failure vocabulary."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.request
import urllib.error
import urllib.parse

import run_failure


class ApiError(Exception):
    """A fixed public error, never the request URL or response body."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


class GitHub:
    def __init__(self, env=None):
        self.env = os.environ if env is None else env
        self.repository = self.env.get("GITHUB_REPOSITORY", "")
        self.token = self.env.get("GITHUB_TOKEN", "")
        if not self.token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise ApiError("Auto-fix GitHub access is unavailable.")

    def call(self, method, path, data=None):
        request = urllib.request.Request(
            "https://api.github.com/repos/" + self.repository + path,
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except Exception:
            raise ApiError("The GitHub request did not complete.") from None

    def items(self, path, key=None):
        result = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 11):
            response = self.call("GET", f"{path}{separator}per_page=100&page={page}")
            rows = response.get(key) if key and isinstance(response, dict) else response
            if not isinstance(rows, list):
                raise ApiError("The GitHub response could not be checked.")
            if not all(isinstance(row, dict) for row in rows):
                raise ApiError("The GitHub response could not be checked.")
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise ApiError("The GitHub response is too large to approve safely.")

    def download(self, path):
        request = urllib.request.Request("https://api.github.com/repos/" + self.repository + path,
                                         headers={"Authorization": "Bearer " + self.token})
        try:
            opener = urllib.request.build_opener(NoRedirect())
            try:
                response = opener.open(request, timeout=15)
            except urllib.error.HTTPError as error:
                if error.code != 302:
                    raise
                location = error.headers.get("Location", "")
                error.close()
                parsed = urllib.parse.urlsplit(location)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError
                # The presigned artifact URL needs no credential header.
                response = urllib.request.urlopen(urllib.request.Request(location), timeout=15)
            with response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError
            return raw
        except Exception:
            raise ApiError("The auto-fix status download did not complete.") from None


def dispatch_fix(summary, env=None, problem="", attempt=1):
    try:
        inputs = {"summary": safe_summary(summary)}
        if problem:
            if not re.fullmatch(r"[0-9]{1,40}", problem) or type(attempt) is not int or not 1 <= attempt <= 3:
                raise ApiError("The auto-fix retry request could not be verified.")
            inputs.update(problem=problem, attempt=str(attempt))
        GitHub(env).call("POST", "/actions/workflows/autofix.yml/dispatches",
                         {"ref": "main", "inputs": inputs})
        return True
    except ApiError:
        print("Auto-fix could not start; use the robot-fix-runbook.")
        return False


QUOTA_DAILY = "quota used up (429 daily)"
RATE_MINUTE = "rate limit (429 per minute)"
SERVER_BUSY = "server busy (503)"
PROVIDER_TIMEOUT = "timeout"
REQUEST_LARGE = "request too large (413)"
AUTH_PROBLEM = "auth problem (401/403)"
NO_CHANGE = "no change made"
# Confirmed with `opencode models google`; all three use GEMINI_API_KEY.
GEMINI_MODELS = (
    "google/gemini-3.8-flash",
    "google/gemini-3.5-flash-lite",
    "google/gemini-flash-latest",
)
GEMINI_BUSY_WAIT = 20
PROVIDER_ATTEMPT_SECONDS = 90
GEMINI_ATTEMPT_SECONDS = 70
GEMINI_RESULT_SECONDS = 10
PROVIDER_TERM_SECONDS = 5
PROVIDER_KILL_SECONDS = 1


def classify_provider_output(output):
    # Match provider failures, not the robot's safe summary (which may itself
    # mention a timeout). Raw provider output is never logged or saved.
    output = "\n".join(line for line in output.splitlines()
                       if not line.strip().startswith(("Step: ", "Problem: ")))
    def matches(pattern):
        return re.search(pattern, output, re.IGNORECASE) is not None

    minute = matches(r"per[ _-]*minute|\b[RT]PM\b|requests?/minute|tokens?/minute")
    limited = matches(r"\b429\b|rate[_ -]?limit(?:error|ed| reached| exceeded)|rate_limit_exceeded|"
                      r"resource[_ ]exhausted|quota.{0,60}(?:exceed|exhaust)|(?:exceed|exhaust).{0,60}quota|"
                      r"(?:\bTPM\b|tokens per minute).{0,100}(?:limit|exceed)")
    if matches(r"\b(?:401|403)\b|unauthenticated|unauthorized|permission[_ ]denied|"
               r"api[_ -]?key.{0,40}(?:invalid|not valid|missing|required)|invalid[_ -]api[_ -]key"):
        return AUTH_PROBLEM, False
    if matches(r"\b413\b|(?:request|payload|entity) too large|maximum context length|context length exceeded"):
        # A TPM-based 413 can recover later; a static oversized body cannot.
        return REQUEST_LARGE, limited and minute
    if limited and matches(r"per[ _-]*day|\bdaily\b|\b[RT]PD\b|requests?/day|tokens?/day"):
        return QUOTA_DAILY, True
    if matches(r"\b503\b|high demand|server.{0,30}(?:busy|overload)|service unavailable"):
        return SERVER_BUSY, True
    if matches(r"(?:provider|api|read|connect)\w*timeout|response headers? timed out|request timed out"):
        return PROVIDER_TIMEOUT, True
    if limited:
        # An unspecified 429 uses the rate-limit class; do not infer a daily
        # quota exhaustion without an explicit daily quota signal.
        return RATE_MINUTE, True
    return NO_CHANGE, False


def busy_provider_output(output):
    return classify_provider_output(output)[1]


def provider_attempt(model=None, timeout=PROVIDER_ATTEMPT_SECONDS):
    reason, busy = NO_CHANGE, False
    timed_out = False
    options = {"env": dict(os.environ, MODEL=model)} if model else {}
    process = subprocess.Popen(["opencode", "github", "run"], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True,
                               **options)
    try:
        output, _ = process.communicate(timeout=timeout)
        reason, busy = classify_provider_output(output)
    except subprocess.TimeoutExpired:
        timed_out = True
        reason, busy = PROVIDER_TIMEOUT, True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=PROVIDER_TERM_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=PROVIDER_KILL_SECONDS)
            except subprocess.TimeoutExpired:
                # A detached descendant may still hold a pipe. Never drain it
                # indefinitely after the provider's process group was killed.
                if process.stdout is not None:
                    process.stdout.close()
    # Only the remote result check can confirm the absence of a proposal.
    if reason != NO_CHANGE:
        print("Auto-fix provider: " + reason + ".")
    return (0 if not timed_out and process.returncode == 0 else 1), reason, busy


def provider_status(reason, busy):
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write("busy=" + ("true" if busy else "false") + "\n")
        output.write("reason=" + reason + "\n")


def provider_run():
    code, reason, busy = provider_attempt()
    provider_status(reason, busy)
    return code


def gemini_result():
    # Run the same trusted remote verifier in a bounded child. Only its status
    # flag is read; stdout/stderr (including exception details) stay private.
    with tempfile.TemporaryDirectory(prefix="autofix-check-") as directory:
        output = Path(directory) / "result"
        env = dict(os.environ, GITHUB_OUTPUT=str(output))
        for name in ("GEMINI_API_KEY", "GEMINI_AUTOFIX_KEY", "NVIDIA_API_KEY", "GROQ_API_KEY"):
            env.pop(name, None)
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "result-check"],
                                    env=env, capture_output=True, text=True,
                                    timeout=GEMINI_RESULT_SECONDS)
            if result.returncode == 0:
                lines = output.read_text(encoding="utf-8").splitlines()
                if lines in (["proposal=true"], ["proposal=false"]):
                    return lines == ["proposal=true"]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def gemini_run(time_budget=None):
    all_busy = True
    start_time = time.time()
    for model in GEMINI_MODELS:
        for attempt in range(2):
            if time_budget is not None and (time.time() - start_time) >= time_budget:
                print("Auto-fix provider: timeout.")
                provider_status(PROVIDER_TIMEOUT, True)
                return 1
            # Model IDs come exclusively from the fixed supported list above.
            print("Auto-fix model: " + model + ".", flush=True)
            _, reason, busy = provider_attempt(model, timeout=GEMINI_ATTEMPT_SECONDS)
            all_busy = all_busy and busy
            # A successful exit is not a fix; a verified remote proposal is.
            found = gemini_result()
            if found is None:
                print("No new auto-fix proposal was verified.")
                # Uncertainty is never success or proof of no change. Keep the
                # bounded model order, including the single 503 retry, intact.
                all_busy = False
            if found:
                print("Auto-fix proposal verified.")
                provider_status(NO_CHANGE, False)
                return 0
            if reason == NO_CHANGE and found is False:
                print("Auto-fix provider: " + NO_CHANGE + ".")
            if reason == AUTH_PROBLEM:
                provider_status(reason, False)
                return 1
            if reason != SERVER_BUSY or attempt == 1:
                break
            print("Auto-fix Gemini busy; waiting 20 seconds before one retry.", flush=True)
            time.sleep(GEMINI_BUSY_WAIT)
    provider_status(reason, all_busy)
    return 1


DRILL_FAILING_TEST_CONTENT = '''"""Drill test planted in temporary copy only."""
import unittest

class DrillProofTest(unittest.TestCase):
    def test_drill_known_failure(self):
        self.assertEqual(1 + 1, 3, "drill planted failure")
'''

DRILL_PASSING_TEST_CONTENT = '''"""Drill test planted in temporary copy only."""
import unittest

class DrillProofTest(unittest.TestCase):
    def test_drill_known_failure(self):
        self.assertEqual(1 + 1, 2, "drill fixed")
'''


def real_drill_fix_loop(temp_workspace, env=None, time_budget=120):
    """Executes the patient provider fix loop for the drill in temp_workspace."""
    env = dict(os.environ if env is None else env)
    # Rule 4: GEMINI_AUTOFIX_KEY is strictly reserved for auto-fix operations
    autofix_key = env.get("GEMINI_AUTOFIX_KEY") or env.get("GEMINI_API_KEY")
    if not autofix_key:
        print("Auto-fix drill: GEMINI_AUTOFIX_KEY is not configured.", flush=True)
        return False

    drill_env = dict(env)
    drill_env["GEMINI_API_KEY"] = autofix_key
    # Never leak other keys or expose secrets
    start_time = time.time()

    # Try Gemini models in order with backoff
    for model in GEMINI_MODELS:
        backoff = GEMINI_BUSY_WAIT
        for attempt in range(2):
            if time.time() - start_time >= time_budget:
                return False
            cmd = ["opencode", "github", "run"]
            call_env = dict(drill_env, MODEL=model)
            try:
                proc = subprocess.Popen(cmd, cwd=str(temp_workspace), stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, errors="replace",
                                        env=call_env)
                out, _ = proc.communicate(timeout=GEMINI_ATTEMPT_SECONDS)
                reason, busy = classify_provider_output(out)
            except Exception:
                reason, busy = PROVIDER_TIMEOUT, True

            # Verify if planted test now passes
            planted = Path(temp_workspace) / "tests" / "test_autofix_drill_proof.py"
            test_res = subprocess.run([sys.executable, "-m", "unittest", str(planted)],
                                      cwd=str(temp_workspace), capture_output=True, text=True)
            if test_res.returncode == 0:
                return True

            if reason != SERVER_BUSY or attempt == 1:
                break
            time.sleep(min(backoff, max(1, int(time_budget - (time.time() - start_time)))))
            backoff *= 2

    # Try NVIDIA if configured
    if drill_env.get("NVIDIA_API_KEY") and (time.time() - start_time < time_budget):
        try:
            proc = subprocess.Popen(["opencode", "github", "run"], cwd=str(temp_workspace),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    errors="replace",
                                    env=dict(drill_env, MODEL="nim/deepseek-ai/deepseek-v4.1-flash"))
            proc.communicate(timeout=PROVIDER_ATTEMPT_SECONDS)
        except Exception:
            pass
        planted = Path(temp_workspace) / "tests" / "test_autofix_drill_proof.py"
        test_res = subprocess.run([sys.executable, "-m", "unittest", str(planted)],
                                  cwd=str(temp_workspace), capture_output=True, text=True)
        if test_res.returncode == 0:
            return True

    # Try Groq if configured
    if drill_env.get("GROQ_API_KEY") and (time.time() - start_time < time_budget):
        try:
            proc = subprocess.Popen(["opencode", "github", "run"], cwd=str(temp_workspace),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    errors="replace",
                                    env=dict(drill_env, MODEL="groq/openai/gpt-oss-120b"))
            proc.communicate(timeout=PROVIDER_ATTEMPT_SECONDS)
        except Exception:
            pass
        planted = Path(temp_workspace) / "tests" / "test_autofix_drill_proof.py"
        test_res = subprocess.run([sys.executable, "-m", "unittest", str(planted)],
                                  cwd=str(temp_workspace), capture_output=True, text=True)
        if test_res.returncode == 0:
            return True

    return False


def run_drill(repo_root=None, fix_fn=None, timeout=120, env=None):
    """Run an isolated auto-fix drill in a temp copy. Never touches real repo files."""
    repo_root = Path(repo_root or run_failure.ROOT).resolve()
    steps_taken = []
    steps_taken.append("create_temp_workspace")
    with tempfile.TemporaryDirectory(prefix="autofix-drill-") as temp_dir:
        temp_path = Path(temp_dir)
        temp_tests = temp_path / "tests"
        temp_tests.mkdir(parents=True, exist_ok=True)
        planted_test = temp_tests / "test_autofix_drill_proof.py"
        planted_test.write_text(DRILL_FAILING_TEST_CONTENT, encoding="utf-8")
        steps_taken.append("plant_failing_test")

        # Verify failing test fails
        cmd = [sys.executable, "-m", "unittest", str(planted_test)]
        check_failing = subprocess.run(cmd, cwd=str(temp_path), capture_output=True, text=True)
        if check_failing.returncode == 0:
            steps_taken.append("failing_test_did_not_fail")
            return {
                "verdict": "FAIL",
                "steps": steps_taken,
                "summary": "Drill test was expected to fail initially but passed.",
            }
        steps_taken.append("verify_failing_test")

        # Run fix loop
        steps_taken.append("run_fix_loop")
        if fix_fn is not None:
            fix_ok = fix_fn(temp_path)
        else:
            fix_ok = real_drill_fix_loop(temp_path, env=env, time_budget=timeout)

        if not fix_ok:
            steps_taken.append("fix_loop_failed")
            return {
                "verdict": "FAIL",
                "steps": steps_taken,
                "summary": "Fix loop did not produce a fix.",
            }
        steps_taken.append("fix_applied")

        # Verify passing
        check_passing = subprocess.run(cmd, cwd=str(temp_path), capture_output=True, text=True)
        if check_passing.returncode != 0:
            steps_taken.append("test_still_failing")
            return {
                "verdict": "FAIL",
                "steps": steps_taken,
                "summary": "Drill test failed after fix was applied.",
            }
        steps_taken.append("verify_passing_test")
        steps_taken.append("cleanup_temp_workspace")

    return {
        "verdict": "PASS",
        "steps": steps_taken,
        "summary": "Auto-fix drill passed end-to-end: failing test planted in temp copy, fix loop ran, test verified green.",
    }


def safe_summary(raw):
    # workflow_dispatch's input is one line; normalize only its field separator.
    lines = str(raw).replace(" | Problem: ", "\nProblem: ").splitlines()
    step = next((line[6:] for line in lines if line.startswith("Step: ")
                 and line[6:] in run_failure.STEPS.values()), run_failure.STEPS["workflow"])
    detail = next((line[9:] for line in lines if line.startswith("Problem: ")), run_failure.UNKNOWN)
    if detail.startswith("Failed tests: "):
        names = run_failure.safe_test_names(detail[14:].split(", "))
        detail = "Failed tests: " + ", ".join(names) if names else run_failure.UNKNOWN
    elif detail not in {*run_failure.ERRORS.values(), run_failure.PENDING, run_failure.UNKNOWN,
                        run_failure.KEY_MISSING, "A command could not complete its work."}:
        detail = run_failure.UNKNOWN
    return f"Step: {step}\nProblem: {detail}"


def build_prompt(raw, run_id=""):
    summary = safe_summary(raw)
    step = summary.splitlines()[0][6:]
    marker = f"Robot auto-fix run: {run_id}" if re.fullmatch(r"[0-9]+", run_id) else "Robot auto-fix"
    return (summary + "\n\nFind the real cause, fix it, and add a regression test. "
            "Run python3 -m unittest discover -s tests until it ends OK. "
            f'Open a PR titled "Auto-fix: {step}" targeting main. '
            f'Include this exact line in its body: "{marker}". '
            "Never touch .github/workflows, secrets, encrypted files, or private products. "
            "Do not read credentials or print environment variables. Never print or log secrets. "
            "Do not include money amounts or product ideas in code, comments, messages, or PR text. "
            "If a password, token, key, or login is requested, stop. "
            "Do not merge the PR; the owner must approve it in Telegram.")


FAILED = "Auto-fix didn't work - use the robot-fix-runbook."
TEST_CHECK = "Auto-fix tests"
TITLE_PREFIX = "Auto-fix: "
CALLBACK = re.compile(r"^af:(merge|close):([1-9][0-9]{0,8}):([0-9a-f]{40})$")


def safe_title(pr):
    title = pr.get("title", "")
    return title if title in {TITLE_PREFIX + step for step in run_failure.STEPS.values()} else None


def eligible(pr, repository):
    return (isinstance(pr, dict) and safe_title(pr) is not None
            and pr.get("state") == "open" and not pr.get("draft")
            and (pr.get("user") or {}).get("login") == "github-actions[bot]"
            and (pr.get("base") or {}).get("ref") == "main"
            and ((pr.get("head") or {}).get("repo") or {}).get("full_name") == repository
            and (pr.get("head") or {}).get("ref") != "main"
            and re.search(r"(?m)^Robot auto-fix run: [0-9]+\s*$", pr.get("body") or "") is not None
            and re.fullmatch(r"[0-9a-f]{40}", (pr.get("head") or {}).get("sha", "")) is not None)


def protected_path(name):
    name = str(name).lower()
    parts = name.split("/")
    return (not name or "\\" in name or ".." in parts or name.startswith("/")
            or name.startswith((".github/workflows/", "exported_content/products/", ".aws/", ".git/"))
            or name.endswith((".enc", ".db", ".sqlite"))
            or any(part.startswith(".env") or part == "config.json" for part in parts)
            or name == "opencode.json")


def scope_allowed(api, number):
    files = api.items(f"/pulls/{number}/files")
    return (bool(files) and any(row.get("filename", "").startswith("tests/") for row in files)
            and all(not protected_path(row.get("filename", ""))
                    and not (row.get("previous_filename") and protected_path(row["previous_filename"]))
                    and not run_failure.secret_guard.scan_text(row.get("patch") or "")
                    for row in files))


def check_state(api, sha):
    runs = api.items(f"/commits/{sha}/check-runs?filter=latest", "check_runs")
    statuses = api.items(f"/commits/{sha}/status", "statuses")
    if any(row.get("status") == "completed" and row.get("conclusion") not in ("success",)
           for row in runs) or any(row.get("state") in ("failure", "error") for row in statuses):
        return "failed"
    if (not any(row.get("name") == TEST_CHECK for row in runs)
            or any(row.get("head_sha") != sha or row.get("status") != "completed"
                   or row.get("conclusion") != "success" for row in runs)
            or any(row.get("state") != "success" for row in statuses)):
        return "pending"
    return "green"


def run_marker(run_id):
    if not re.fullmatch(r"[0-9]+", str(run_id)):
        raise ApiError("The auto-fix request could not be verified.")
    return "Robot auto-fix run: " + str(run_id)


def proposals_for_run(run_id, api):
    marker = run_marker(run_id)
    return [pr for pr in api.items("/pulls?state=open")
            if eligible(pr, api.repository) and marker in (pr.get("body") or "").splitlines()]


def snapshot_proposals(run_id, env=None, api=None):
    run_marker(run_id)
    api = api or GitHub(env)
    numbers = [pr.get("number") for pr in api.items("/pulls?state=open")]
    if not all(type(number) is int and 0 < number < 10**9 for number in numbers):
        raise ApiError("The existing auto-fix proposals could not be checked.")
    return {"run_id": str(run_id), "numbers": numbers}


def new_proposal_exists(run_id, baseline, env=None, api=None):
    marker = run_marker(run_id)
    if (not isinstance(baseline, dict) or baseline.get("run_id") != str(run_id)
            or not isinstance(baseline.get("numbers"), list)
            or not all(type(number) is int and 0 < number < 10**9 for number in baseline["numbers"])):
        raise ApiError("The auto-fix result baseline could not be verified.")
    api = api or GitHub(env)
    for candidate in proposals_for_run(run_id, api):
        number = candidate.get("number")
        if type(number) is not int or not 0 < number < 10**9 or number in baseline["numbers"]:
            continue
        # Re-read from GitHub: a local commit, branch, or successful process exit
        # is insufficient. The new remote PR must still match and have safe files.
        pr = api.call("GET", f"/pulls/{number}")
        if (eligible(pr, api.repository) and pr.get("number") == number
                and marker in (pr.get("body") or "").splitlines() and scope_allowed(api, number)):
            return True
    return False


def prepare_checks(run_id, env=None, api=None):
    run_marker(run_id)
    api = api or GitHub(env)
    prepared = 0
    for pr in proposals_for_run(run_id, api):
        number = int(pr["number"])
        if not scope_allowed(api, number):
            raise ApiError("The proposed auto-fix changes protected files or has no regression test.")
        api.call("POST", "/actions/workflows/autofix-check.yml/dispatches", {
            "ref": pr["head"]["ref"],
            "inputs": {"number": str(number), "sha": pr["head"]["sha"]}})
        prepared += 1
    if not prepared:
        raise ApiError("No safe auto-fix proposal was found.")
    return prepared


def validate_check_request(number, sha, env=None, api=None):
    env = os.environ if env is None else env
    if not re.fullmatch(r"[1-9][0-9]{0,8}", str(number)) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ApiError("The auto-fix request could not be verified.")
    api = api or GitHub(env)
    pr = api.call("GET", f"/pulls/{number}")
    if (not eligible(pr, api.repository) or pr["head"]["sha"] != sha
            or env.get("GITHUB_SHA") != sha or not scope_allowed(api, int(number))):
        raise ApiError("The auto-fix changed or its scope is not allowed.")


def poll(get_state, set_state, send, db_path=None, env=None, api=None):
    env = os.environ if env is None else env
    if not env.get("GITHUB_TOKEN") and api is None:
        return 0
    sent = 0
    try:
        api = api or GitHub(env)
        for candidate in api.items("/pulls?state=open"):
            if not eligible(candidate, api.repository):
                continue
            number = int(candidate["number"])
            pr = api.call("GET", f"/pulls/{number}")
            if not eligible(pr, api.repository):
                continue
            sha = pr["head"]["sha"]
            state = check_state(api, sha)
            if not scope_allowed(api, number):
                state = "failed"
            key = f"autofix_notice:{number}"
            notice = f"{sha}:{state}"
            if state == "pending" or get_state(key, None, db_path) == notice:
                continue
            if state == "failed":
                delivered = send(FAILED, None)
            elif pr.get("mergeable_state") != "clean":
                continue
            else:
                buttons = [[("✅ Merge", f"af:merge:{number}:{sha}"),
                            ("❌ Close", f"af:close:{number}:{sha}")]]
                delivered = send(f"Auto-fix ready: {safe_title(pr)}. Tests passed.", buttons)
                if delivered:
                    set_state(f"autofix_offer:{number}", sha, db_path)
            if delivered:
                set_state(key, notice, db_path)
                sent += 1
    except (ApiError, KeyError, TypeError, ValueError):
        print("Auto-fix status is temporarily unavailable.")
    return sent


def decide(data, get_state, set_state, db_path=None, env=None, api=None):
    match = CALLBACK.fullmatch(data or "")
    if not match:
        return None
    action, number, expected = match.groups()
    key = f"autofix_offer:{number}"
    if get_state(key, None, db_path) != expected:
        return "This auto-fix button is no longer current. Wait for the next test result."
    try:
        api = api or GitHub(env)
        pr = api.call("GET", f"/pulls/{number}")
        if not eligible(pr, api.repository):
            return "This auto-fix request is no longer open."
        if action == "close":
            result = api.call("PATCH", f"/pulls/{number}", {"state": "closed"})
            if result.get("state") != "closed":
                return FAILED
            reply = "Auto-fix closed."
        else:
            if (pr["head"]["sha"] != expected or pr.get("mergeable_state") != "clean"
                    or not scope_allowed(api, number) or check_state(api, expected) != "green"):
                return FAILED
            # Re-read the head after checking; the merge API also compares the exact SHA.
            current = api.call("GET", f"/pulls/{number}")
            if (not eligible(current, api.repository) or current["head"]["sha"] != expected
                    or current.get("mergeable_state") != "clean"):
                return FAILED
            result = api.call("PUT", f"/pulls/{number}/merge", {"merge_method": "squash", "sha": expected})
            if result.get("merged") is not True:
                return FAILED
            reply = "Auto-fix merged."
        set_state(key, "done", db_path)
        return reply
    except (ApiError, KeyError, TypeError, ValueError):
        return FAILED


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv == ["prompt"]:
            prompt = build_prompt(os.environ.get("FAILURE_SUMMARY", ""), os.environ.get("GITHUB_RUN_ID", ""))
            delimiter = "robot_" + uuid.uuid4().hex
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write(f"prompt<<{delimiter}\n{prompt}\n{delimiter}\n")
                # The full repository agent's tools already exceed Groq's free
                # request budget, even with a short summary. No small mode yet.
                output.write("groq_small=false\n")
        elif argv == ["result-start"]:
            baseline = snapshot_proposals(os.environ.get("GITHUB_RUN_ID", ""))
            Path(os.environ["AUTOFIX_STATE"]).write_text(json.dumps(baseline), encoding="utf-8")
        elif argv == ["provider-run"]:
            return provider_run()
        elif argv == ["gemini-run"]:
            return gemini_run()
        elif argv == ["result-check"]:
            baseline = json.loads(Path(os.environ["AUTOFIX_STATE"]).read_text(encoding="utf-8"))
            found = new_proposal_exists(os.environ.get("GITHUB_RUN_ID", ""), baseline)
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write("proposal=" + ("true" if found else "false") + "\n")
            if found:
                print("Auto-fix proposal verified.")
            elif os.environ.get("PROVIDER_REASON", NO_CHANGE) == NO_CHANGE:
                print("Auto-fix provider: " + NO_CHANGE + ".")
            else:
                print("No new auto-fix proposal was verified.")
        elif argv == ["prepare-checks"]:
            prepare_checks(os.environ.get("GITHUB_RUN_ID", ""))
        elif argv == ["validate-checks"]:
            validate_check_request(os.environ.get("PR_NUMBER", ""), os.environ.get("PR_SHA", ""))
        elif argv == ["failed"]:
            import notify
            notify.send(FAILED)
        else:
            return 2
        return 0
    except (ApiError, KeyError, OSError, TypeError, ValueError):
        print("Auto-fix could not complete; use the robot-fix-runbook.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
