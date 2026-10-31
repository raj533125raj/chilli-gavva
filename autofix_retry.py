"""Patient auto-fix retries: only safe status flags and summaries cross runs."""
import datetime
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid
import zipfile

import autofix
import notify
import run_failure

DELAY = 30 * 60
MAX_ATTEMPTS = 3
ARTIFACT = "robot-autofix-result"
FILENAME = "autofix-result.json"
PHASES = {"running", "waiting", "exhausted", "manual", "proposed", "cancelled"}
BUSY_FAILED = "Auto-fix tried 3 times, AI servers were busy. Use the robot-fix-runbook."


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9]{1,40}", value) is not None


def start_request(summary, env, problem=None, attempt=1):
    problem = problem or env.get("GITHUB_RUN_ID", "") or str(uuid.uuid4().int)
    if not valid_id(problem) or type(attempt) is not int or not 1 <= attempt <= MAX_ATTEMPTS:
        raise autofix.ApiError("The auto-fix retry request could not be verified.")
    return {"problem": problem, "summary": autofix.safe_summary(summary), "attempt": attempt,
            "phase": "running", "notified": False}


def saved_retry(path):
    retry = run_failure.state_document(path).get("autofix_retry")
    if not isinstance(retry, dict) or retry.get("phase") not in PHASES or not valid_id(retry.get("problem")):
        return None
    try:
        safe = start_request(retry.get("summary", ""), {}, retry.get("problem"), retry.get("attempt"))
    except autofix.ApiError:
        return None
    safe.update(phase=retry["phase"], notified=retry.get("notified") is True)
    if valid_id(retry.get("last_result")):
        safe["last_result"] = retry["last_result"]
    due = retry.get("due_at")
    if type(due) in (int, float) and math.isfinite(due) and due >= 0:
        safe["due_at"] = due
    return safe


def save_retry(path, retry):
    state = run_failure.state_document(path)
    state["autofix_retry"] = retry
    run_failure.write_state(path, state)


def cancel(path, now):
    state = run_failure.state_document(path)
    retry = saved_retry(path)
    if retry:
        retry.update(phase="cancelled", notified=True)
        retry.pop("due_at", None)
        state["autofix_retry"] = retry
    state["last_success_at"] = now
    run_failure.write_state(path, state)


def dispatch_due(path, env, now, dispatch=None, summary=None):
    retry = saved_retry(path)
    if not retry or retry["phase"] != "waiting" or retry["attempt"] >= MAX_ATTEMPTS:
        return False
    if run_failure.read_count(path) == 0 or (summary is not None and summary != retry["summary"]):
        cancel(path, now)
        return False
    if retry.get("due_at", float("inf")) > now or not env.get("GITHUB_TOKEN"):
        return False
    retry.update(attempt=retry["attempt"] + 1, phase="running")
    retry.pop("due_at", None)
    # Reserve before dispatch: an API timeout may still start the workflow.
    # This deliberately leaves the daily NEW-problem timestamp unchanged.
    save_retry(path, retry)
    try:
        (dispatch or autofix.dispatch_fix)(retry["summary"], env,
                                          problem=retry["problem"], attempt=retry["attempt"])
    except Exception:
        print("Auto-fix retry could not start.")
    return True


def terminal_message(retry):
    if not retry:
        return autofix.FAILED
    phase = retry.get("phase")
    reason = retry.get("reason", "")
    if phase == "exhausted":
        if reason:
            return f"Auto-fix tried 3 times, AI servers were busy ({reason}). Use the robot-fix-runbook."
        return BUSY_FAILED
    if phase == "manual" and reason:
        return f"Auto-fix gave up: {reason}. Use the robot-fix-runbook."
    return autofix.FAILED


def notify_terminal(path, send=None):
    retry = saved_retry(path)
    if not retry or retry["phase"] not in {"manual", "exhausted"} or retry["notified"]:
        return False
    message = terminal_message(retry)
    try:
        delivered = (send or notify.send)(message)
    except Exception:
        delivered = False
    if delivered:
        retry["notified"] = True
        save_retry(path, retry)
    return bool(delivered)


def accept_result(path, result, created_at, now, send=None):
    if not valid_id(result.get("problem")):
        raise autofix.ApiError("The auto-fix problem could not be verified.")
    request = start_request(result.get("summary", ""), {}, result.get("problem"), result.get("attempt"))
    run_id, outcome = result.get("run_id"), result.get("outcome")
    if not valid_id(run_id) or outcome not in {"busy", "manual", "proposed"}:
        raise autofix.ApiError("The auto-fix result could not be verified.")
    state = run_failure.state_document(path)
    success = state.get("last_success_at")
    if type(success) in (int, float) and math.isfinite(success) and success >= created_at:
        return "cancelled"
    retry = saved_retry(path)
    if retry and retry["problem"] == request["problem"]:
        if retry["phase"] in {"cancelled", "proposed"} or retry["attempt"] != request["attempt"]:
            return retry["phase"]
        if retry["phase"] in {"manual", "exhausted"} or retry.get("last_result") == run_id:
            notify_terminal(path, send)
            return retry["phase"]
    elif request["attempt"] != 1 or (retry and retry["phase"] in {"running", "waiting"}):
        return "ignored"
    else:
        retry = request
        state["last_autofix_dispatch"] = max(run_failure.last_autofix_dispatch(path) or 0, created_at)
        # An owner-started drill may precede the first saved robot failure.
        state["consecutive_failures"] = max(run_failure.read_count(path), 1)
        run_failure.write_state(path, state)
    retry.update(last_result=run_id, notified=False)
    retry.pop("due_at", None)
    if "reason" in result and isinstance(result["reason"], str):
        retry["reason"] = result["reason"]
    if outcome == "busy":
        if retry["attempt"] < MAX_ATTEMPTS:
            retry.update(phase="waiting", due_at=now + DELAY)
        else:
            retry["phase"] = "exhausted"
    else:
        retry["phase"] = outcome
    save_retry(path, retry)
    notify_terminal(path, send)
    return retry["phase"]


def record_result(env, path):
    attempt = env.get("AUTOFIX_ATTEMPT", "1")
    if attempt not in {"1", "2", "3"}:
        raise autofix.ApiError("The auto-fix attempt could not be verified.")
    request = start_request(env.get("FAILURE_SUMMARY", ""), env,
                            env.get("AUTOFIX_PROBLEM") or None, int(attempt))
    run_id = env.get("GITHUB_RUN_ID", "")
    if not valid_id(run_id):
        raise autofix.ApiError("The auto-fix run could not be verified.")
    outcome = ("proposed" if env.get("AUTOFIX_PROPOSAL") == "true" else
               "busy" if env.get("AUTOFIX_BUSY") == "true" else "manual")
    result = {key: request[key] for key in ("problem", "summary", "attempt")}
    result.update(run_id=run_id, outcome=outcome)
    Path(path).write_text(json.dumps(result), encoding="utf-8")


def receive_result(path, event, env, now=None, api=None, send=None):
    source = event.get("workflow_run") or {}
    run_id = str(source.get("id", ""))
    if not valid_id(run_id):
        raise autofix.ApiError("The completed auto-fix run could not be verified.")
    api = api or autofix.GitHub(env)
    source = api.call("GET", f"/actions/runs/{run_id}")
    if (source.get("path") != ".github/workflows/autofix.yml" or source.get("head_branch") != "main"
            or source.get("event") != "workflow_dispatch" or source.get("status") != "completed"
            or (source.get("head_repository") or {}).get("full_name") != api.repository):
        raise autofix.ApiError("The completed auto-fix run is not trusted.")
    created = datetime.datetime.fromisoformat(source["created_at"].replace("Z", "+00:00")).timestamp()
    artifacts = api.items(f"/actions/runs/{run_id}/artifacts", "artifacts")
    matches = [row for row in artifacts if row.get("name") == ARTIFACT and not row.get("expired")]
    if len(matches) != 1 or type(matches[0].get("id")) is not int:
        raise autofix.ApiError("The auto-fix status artifact could not be verified.")
    raw = api.download(f"/actions/artifacts/{matches[0]['id']}/zip")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.namelist() != [FILENAME] or archive.getinfo(FILENAME).file_size > 16384:
            raise autofix.ApiError("The auto-fix status artifact is not safe.")
        result = json.loads(archive.read(FILENAME))
    if not isinstance(result, dict) or result.get("run_id") != run_id:
        raise autofix.ApiError("The auto-fix status run does not match.")
    return accept_result(path, result, created, time.time() if now is None else now, send)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv == ["record"]:
            record_result(os.environ, Path(os.environ["RUNNER_TEMP"]) / FILENAME)
        elif argv == ["receive"]:
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
            receive_result(Path(os.environ["RUNNER_TEMP"]) / "robot-failure-state.json", event, os.environ)
        else:
            return 2
        return 0
    except (autofix.ApiError, KeyError, OSError, TypeError, ValueError, zipfile.BadZipFile):
        print("Auto-fix retry status could not be checked.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
