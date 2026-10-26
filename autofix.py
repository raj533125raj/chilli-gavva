"""Auto-fix requests contain only the robot's safe failure vocabulary."""
import json
import os
from pathlib import Path
import re
import sys
import uuid
import urllib.request

import run_failure


class ApiError(Exception):
    """A fixed public error, never the request URL or response body."""


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


def dispatch_fix(summary, env=None):
    try:
        GitHub(env).call("POST", "/actions/workflows/autofix.yml/dispatches",
                         {"ref": "main", "inputs": {"summary": safe_summary(summary)}})
        return True
    except ApiError:
        print("Auto-fix could not start; use the robot-fix-runbook.")
        return False


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
        elif argv == ["result-check"]:
            baseline = json.loads(Path(os.environ["AUTOFIX_STATE"]).read_text(encoding="utf-8"))
            found = new_proposal_exists(os.environ.get("GITHUB_RUN_ID", ""), baseline)
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write("proposal=" + ("true" if found else "false") + "\n")
            print("Auto-fix proposal verified." if found else "No new auto-fix proposal was verified.")
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
