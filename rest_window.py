"""rest_window.py — two short daily rest windows (12 minutes a day).

6:00-6:06 AM IST (00:30-00:36 UTC) and 6:00-6:06 PM IST (12:30-12:36 UTC):
  - no new AI work, no new drafts, no product building, no recipes
  - only health checks and cleanup: AI providers reachable?, tidy old lock files and old log rows
  - Telegram commands and buttons are still answered (that is a separate step of the run)
The night niche slot (about 20:00-22:00 UTC) and the door bell are never inside a rest window.
"""
import datetime
import os
import sqlite3

import guardrails

WINDOWS_UTC = [((0, 30), (0, 36)), ((12, 30), (12, 36))]   # 6:00-6:06 AM and PM in IST (UTC+5:30)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_DIR = os.path.join(BASE_DIR, ".scheduler_locks")
KEEP_LOCK_DAYS = 14
KEEP_LOG_DAYS = 60


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def in_rest_window(now=None):
    if now is None and getattr(guardrails, "_TESTING", False):
        return False   # tests never depend on the real clock; they pass a time on purpose
    now = now or _utc_now()
    t = (now.hour, now.minute)
    return any(start <= t < end for start, end in WINDOWS_UTC)


def cleanup(now=None, lock_dir=LOCK_DIR, db_path=None):
    """Remove scheduler lock files older than 14 days and log rows older than 60 days. Returns counts."""
    now = now or _utc_now()
    removed = {"lock files": 0, "log rows": 0}
    cutoff = now - datetime.timedelta(days=KEEP_LOCK_DAYS)
    if os.path.isdir(lock_dir):
        for name in os.listdir(lock_dir):
            try:
                day = datetime.datetime.strptime(name[:10], "%Y-%m-%d")
            except ValueError:
                continue
            if day < cutoff and name.endswith(".lock"):
                os.remove(os.path.join(lock_dir, name))
                removed["lock files"] += 1
    old_day = (now - datetime.timedelta(days=KEEP_LOG_DAYS)).date().isoformat()
    conn = guardrails.connect(db_path)
    for table, column in (("provider_log", "day"), ("usage_log", "day")):
        try:
            removed["log rows"] += conn.execute(f"DELETE FROM {table} WHERE {column} < ?", (old_day,)).rowcount
        except sqlite3.Error:
            pass
    conn.commit()
    conn.close()
    return removed


def ai_reachable(probe=None):
    """One tiny test per AI provider (first model only). Returns {provider: "OK" | "FAIL <reason>"}."""
    import llm_router
    probe = probe or llm_router._probe
    out = {}
    for provider, model, url, headers, parse in llm_router._targets():
        if provider in out:
            continue
        body = {"model": model, "messages": [{"role": "user", "content": llm_router.HEALTH_PROMPT}]}
        ok, reason, _ = probe(url, headers, body, llm_router._TIMEOUT, parse)
        out[provider] = "OK" if ok else f"FAIL {reason}"
    return out


def rest_tasks(now=None, db_path=None, probe=None, lock_dir=LOCK_DIR):
    """What a run does inside the rest window. Never raises. Returns a short safe summary line."""
    parts = []
    try:
        removed = cleanup(now, lock_dir, db_path)
        parts.append(f"cleanup: {removed['lock files']} old lock file(s), {removed['log rows']} old log row(s)")
    except Exception as exc:
        parts.append(f"cleanup failed: {type(exc).__name__}")
    try:
        ai = ai_reachable(probe)
        parts.append("AI: " + (", ".join(f"{p} {s}" for p, s in ai.items()) or "no provider configured"))
    except Exception as exc:
        parts.append(f"AI check failed: {type(exc).__name__}")
    parts.append("tests: passed at the start of this run" if os.environ.get("GITHUB_ACTIONS")
                 else "tests: run 'python3 -m unittest discover -s tests'")
    return "Rest window (no AI work, no drafts): " + "; ".join(parts) + "."
