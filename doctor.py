"""doctor.py — DOCTOR agent for automated failure diagnosis, safe remedies, and auto-fix.

When any robot agent hits an error, a repeat loop (3+ times), or a stuck task (2 cycles),
doctor.py:
1. Collects safe facts (agent, step, scrubbed error class & message, last 5 actions).
2. Matches against a playbook of known problems:
   - Rate limits / 429
   - Quota exceeded
   - Timeouts
   - Empty AI answer
   - JSON parse errors
   - DB locked
   - Missing file
   - Telegram API errors
   - GitHub API errors
   - Code-related errors
3. Applies ONLY safe automatic remedies for non-code issues:
   - Backoff and retry later
   - Switch to next AI provider via llm_router
   - Skip item for 24h
   - Mark task 'needs owner'
4. For code-related issues:
   - Does NOT change code directly.
   - Writes short diagnosis and triggers existing auto-fix flow (run_failure/autofix)
     and/or sends a Telegram card with buttons 'Retry now' / 'Skip' / 'Send to auto-fix'.
5. Keeps a doctor log in the database (doctor_cases table); /doctor shows last 10 cases.
6. Prevents self-loops: max 3 actions per case per day.
"""

import datetime
import json
import os
import re
import sqlite3
import sys
import time

import autofix
import cubes
import guardrails
import llm_router
import notify
import rest_window
import secret_guard

MAX_DOCTOR_ACTIONS_PER_DAY = 3

PLAYBOOK = {
    "rate_limit": "Rate limit (429)",
    "quota": "Quota exceeded",
    "timeout": "Timeout",
    "empty_ai": "Empty AI answer",
    "json_parse": "JSON parse error",
    "db_locked": "Database locked",
    "missing_file": "Missing file",
    "telegram_api": "Telegram API error",
    "github_api": "GitHub API error",
    "code_error": "Code-related error",
}


def is_paused(db_path=None):
    if guardrails.is_killed() or rest_window.in_rest_window():
        return True
    return not cubes.is_kind_enabled("code_proposal", db_path=db_path)


def connect(db_path=None):
    import workforce
    workforce.connect(db_path)
    conn = guardrails.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS doctor_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            agent TEXT NOT NULL,
            step TEXT NOT NULL,
            error_class TEXT NOT NULL,
            error_message TEXT NOT NULL,
            last_5_actions TEXT NOT NULL,
            category TEXT NOT NULL,
            diagnosis TEXT NOT NULL,
            remedy TEXT NOT NULL,
            status TEXT NOT NULL,
            actions_count INTEGER DEFAULT 1,
            task_id INTEGER
        )
    """)
    conn.commit()
    return conn


def scrub_text(text: str) -> str:
    """Use the shared storage/diagnostic scrubber."""
    return secret_guard.scrub_text(text, replacement="[REDACTED]")


def get_last_5_actions(agent: str, task_id: int = None, db_path=None) -> list[str]:
    """Retrieve last 5 task action descriptions for an agent or task from DB."""
    try:
        conn = connect(db_path)
        if task_id:
            rows = conn.execute(
                "SELECT id, kind, status, error FROM tasks WHERE id <= ? ORDER BY id DESC LIMIT 5",
                (task_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, kind, status, error FROM tasks WHERE agent = ? ORDER BY id DESC LIMIT 5",
                (agent,)
            ).fetchall()
        conn.close()
        return [f"task#{r['id']} ({r['kind']}, status={r['status']})" for r in rows]
    except Exception:
        return []


def collect_facts(agent: str, step: str, error: Exception | str, last_5_actions: list = None,
                  task_id: int = None, db_path=None) -> dict:
    """Collect safe facts with all secrets scrubbed."""
    if isinstance(error, Exception):
        err_cls = type(error).__name__
        err_msg = str(error)
    else:
        err_msg = str(error or "Unknown error")
        if ":" in err_msg:
            parts = err_msg.split(":", 1)
            cls_candidate = parts[0].strip()
            if cls_candidate.endswith("Error") or cls_candidate in ("Exception", "ApiError", "AIUnavailable"):
                err_cls = cls_candidate
                err_msg = parts[1].strip()
            else:
                err_cls = "AgentExecutionError"
        else:
            err_cls = "AgentExecutionError"

    scrubbed_msg = scrub_text(err_msg)
    scrubbed_agent = scrub_text(agent or "Unknown Agent")
    scrubbed_step = scrub_text(step or "unknown_step")

    actions = last_5_actions
    if not actions:
        actions = get_last_5_actions(scrubbed_agent, task_id=task_id, db_path=db_path)

    scrubbed_actions = [scrub_text(str(a)) for a in actions[:5]]

    return {
        "agent": scrubbed_agent,
        "step": scrubbed_step,
        "error_class": err_cls,
        "error_message": scrubbed_msg,
        "last_5_actions": scrubbed_actions,
        "task_id": task_id
    }


def match_playbook(facts: dict) -> tuple[str, str]:
    """Match facts against playbook. Returns (category_key, diagnosis)."""
    err_cls = facts.get("error_class", "")
    err_msg = facts.get("error_message", "").lower()
    step = facts.get("step", "").lower()

    # 1. Quota
    if "quota" in err_msg or "daily limit" in err_msg or "429 daily" in err_msg or "rpd" in err_msg or "tpd" in err_msg:
        return "quota", "LLM provider daily quota or call cap reached."

    # 2. Rate limits / 429
    if "429" in err_msg or "rate limit" in err_msg or "too many requests" in err_msg or "rpm" in err_msg or "tpm" in err_msg:
        return "rate_limit", "LLM/API rate limit (429 per minute) exceeded."

    # 3. Timeouts
    if err_cls in ("TimeoutError", "socket.timeout") or any(t in err_msg for t in ("timeout", "timed out", "504", "gateway timeout", "read timeout", "connect timeout")):
        return "timeout", "Network or service request timed out."

    # 4. Empty AI answer
    if "empty answer" in err_msg or "ai response was empty" in err_msg or "no answer from any ai" in err_msg or "aiunavailable" in err_msg:
        return "empty_ai", "AI provider returned an empty answer."

    # 5. JSON parse errors
    if err_cls in ("JSONDecodeError", "ValueError") and "json" in err_msg or ("json" in err_msg and ("parse" in err_msg or "decode" in err_msg or "expecting value" in err_msg)):
        return "json_parse", "JSON response could not be parsed."

    # 6. DB locked
    if "database is locked" in err_msg or "db locked" in err_msg or (err_cls == "OperationalError" and "locked" in err_msg):
        return "db_locked", "SQLite database file is locked by another write."

    # 7. Missing file
    if err_cls in ("FileNotFoundError", "ModuleNotFoundError") or "no such file or directory" in err_msg or "missing file" in err_msg:
        return "missing_file", "Required system file or module missing."

    # 8. Telegram API errors
    if "telegram" in err_msg or "bot token" in err_msg or "getupdates" in err_msg or "sendmessage" in err_msg or "409 (another program" in err_msg:
        return "telegram_api", "Telegram API communication or token error."

    # 9. GitHub API errors
    if err_cls == "ApiError" or "github" in err_msg or "autofix" in err_msg or "workflow dispatch" in err_msg:
        return "github_api", "GitHub API or workflow dispatch failed."

    # 10. Code errors
    code_err_classes = ("SyntaxError", "NameError", "AttributeError", "TypeError", "IndexError", "KeyError",
                        "AssertionError", "ImportError", "UnboundLocalError", "IndentationError")
    if err_cls in code_err_classes or "code_proposal" in step or "traceback" in err_msg or "exception" in err_msg:
        return "code_error", f"Code error detected ({err_cls})."

    return ("code_error" if err_cls.endswith("Error") else ("unknown", f"Error: {err_cls}"))


def get_actions_today(agent: str, step: str, db_path=None) -> int:
    """Return count of doctor actions taken today for given agent and step/case."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    conn = connect(db_path)
    row = conn.execute(
        "SELECT COUNT(*) FROM doctor_cases WHERE agent = ? AND step = ? AND ts LIKE ?",
        (agent, step, f"{today}%")
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def detect_repeat_loop(agent: str, step: str, error_msg: str = "", db_path=None) -> bool:
    """True if same agent and step hit failures 3+ times."""
    try:
        conn = connect(db_path)
        rows = conn.execute(
            "SELECT status, attempts, error FROM tasks WHERE agent = ? AND kind = ? ORDER BY id DESC LIMIT 3",
            (agent, step)
        ).fetchall()
        conn.close()
        if len(rows) >= 3 and all(r["status"] == "failed" or r["attempts"] >= 3 for r in rows):
            return True
        if len(rows) >= 1 and rows[0]["attempts"] >= 3:
            return True
        return False
    except Exception:
        return False


def detect_stuck_tasks(db_path=None) -> list[dict]:
    """Return tasks that have made no progress for 2+ cycles (attempts >= 2 and running/queued)."""
    stuck = []
    try:
        conn = connect(db_path)
        rows = conn.execute(
            "SELECT id, agent, kind, attempts, status, ts, started_ts FROM tasks "
            "WHERE status IN ('running', 'queued') AND attempts >= 2"
        ).fetchall()
        conn.close()
        for r in rows:
            stuck.append({
                "task_id": r["id"],
                "agent": r["agent"] or "Unknown Agent",
                "step": r["kind"],
                "attempts": r["attempts"],
                "status": r["status"]
            })
    except Exception:
        pass
    return stuck


def doctor_buttons(case_id: int):
    """Inline buttons for Doctor Telegram card."""
    return [
        [("🔄 Retry now", f"doc:retry:{case_id}"),
         ("⏭ Skip 24h", f"doc:skip:{case_id}"),
         ("🛠 Send to auto-fix", f"doc:autofix:{case_id}")]
    ]


def send_doctor_card(case_id: int, facts: dict, category: str, diagnosis: str, db_path=None):
    """Send Doctor Telegram card with diagnosis and action buttons."""
    title = PLAYBOOK.get(category, "Doctor Notice")
    msg = (f"🩺 Doctor Diagnosis:\n\n"
           f"Agent: {facts['agent']}\n"
           f"Step: {facts['step']}\n"
           f"Problem: {title}\n"
           f"Diagnosis: {diagnosis}\n"
           f"Error: {facts['error_message'][:200]}\n\n"
           f"Pick an action below:")
    configured, token, chat_id = notify._telegram_configured()
    if configured:
        import telegram_inbox
        telegram_inbox.send_text(token, chat_id, msg, doctor_buttons(case_id))


def apply_remedy(category: str, facts: dict, db_path=None) -> tuple[str, str]:
    """Apply safe automatic remedy. Returns (remedy_name, status)."""
    task_id = facts.get("task_id")

    if category == "rate_limit":
        # Cool down current provider in llm_router and backoff task
        llm_router.set_cooldown("Groq", minutes=30, db_path=db_path)
        llm_router.set_cooldown("Gemini", minutes=15, db_path=db_path)
        if task_id:
            _backoff_task(task_id, db_path=db_path)
        return "Switched AI provider via llm_router & backed off retry", "resolved"

    if category == "quota":
        # Switch provider and defer item
        llm_router.set_cooldown("Groq", minutes=120, db_path=db_path)
        if task_id:
            _defer_task_24h(task_id, db_path=db_path)
        return "Switched provider & skipped item for 24h", "skipped"

    if category in ("timeout", "db_locked", "empty_ai"):
        # Backoff and retry later
        if task_id:
            _backoff_task(task_id, db_path=db_path)
        return "Backoff and retry scheduled", "resolved"

    if category in ("missing_file", "telegram_api", "github_api"):
        # Mark task needs owner or backoff
        if task_id:
            _mark_needs_owner(task_id, db_path=db_path)
        return "Marked task 'needs owner'", "needs_owner"

    if category == "json_parse":
        if task_id:
            _backoff_task(task_id, db_path=db_path)
        return "Backoff and retry task", "resolved"

    # Default / code_error
    if task_id:
        _mark_needs_owner(task_id, db_path=db_path)
    return "Marked task 'needs owner'", "needs_owner"


def _backoff_task(task_id: int, db_path=None):
    try:
        import workforce
        conn = connect(db_path)
        row = conn.execute("SELECT attempts FROM tasks WHERE id = ?", (task_id,)).fetchone()
        attempts = row[0] if row else 0
        if attempts < workforce.MAX_ATTEMPTS:
            conn.execute("UPDATE tasks SET status = 'queued', error = 'Doctor: backoff retry' WHERE id = ?", (task_id,))
            conn.commit()
        conn.close()
    except Exception:
        pass


def _defer_task_24h(task_id: int, db_path=None):
    try:
        conn = connect(db_path)
        conn.execute("UPDATE tasks SET status = 'deferred', error = 'Doctor: deferred 24h' WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _mark_needs_owner(task_id: int, db_path=None):
    try:
        conn = connect(db_path)
        conn.execute("UPDATE tasks SET status = 'needs_owner', error = 'Doctor: marked needs owner' WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def diagnose_and_remedy(agent: str, step: str, error: Exception | str,
                         last_5_actions: list = None, task_id: int = None,
                         db_path=None) -> dict:
    """Main entrypoint: collects facts, matches playbook, checks limits, applies remedies."""
    if is_paused(db_path):
        return {"case_id": None, "category": "paused", "status": "skipped",
                "diagnosis": "Doctor paused", "remedy": "Skipped due to kill switch, rest window, or cube off"}
    facts = collect_facts(agent, step, error, last_5_actions=last_5_actions, task_id=task_id, db_path=db_path)

    # Check loop limit for Doctor itself (max 3 actions per case per day)
    actions_today = get_actions_today(facts["agent"], facts["step"], db_path=db_path)
    if actions_today >= MAX_DOCTOR_ACTIONS_PER_DAY:
        case_id = record_case(facts, "loop_limit", "Doctor loop prevention (max 3 actions/day reached)",
                              "Doctor limit reached; task set to needs_owner", "needs_owner",
                              actions_count=actions_today + 1, db_path=db_path)
        if task_id:
            _mark_needs_owner(task_id, db_path=db_path)
        return {"case_id": case_id, "category": "loop_limit", "status": "needs_owner",
                "diagnosis": "Doctor limit reached", "remedy": "Task marked needs owner"}

    category, diagnosis = match_playbook(facts)

    if category == "code_error":
        remedy_desc = "Triggered auto-fix & sent Telegram card"
        status = "autofix_triggered"
        case_id = record_case(facts, category, diagnosis, remedy_desc, status,
                              actions_count=actions_today + 1, db_path=db_path)

        # Write failure summary and trigger auto-fix flow
        summary = f"Step: {facts['step']}\nProblem: {facts['error_class']}: {facts['error_message']}"
        try:
            autofix.dispatch_fix(summary)
        except Exception:
            pass

        # Send Telegram card with action buttons
        send_doctor_card(case_id, facts, category, diagnosis, db_path=db_path)

        return {"case_id": case_id, "category": category, "diagnosis": diagnosis,
                "remedy": remedy_desc, "status": status}

    # Non-code errors: apply safe remedy
    remedy_desc, status = apply_remedy(category, facts, db_path=db_path)
    case_id = record_case(facts, category, diagnosis, remedy_desc, status,
                          actions_count=actions_today + 1, db_path=db_path)

    # Send card if required
    send_doctor_card(case_id, facts, category, diagnosis, db_path=db_path)

    return {"case_id": case_id, "category": category, "diagnosis": diagnosis,
            "remedy": remedy_desc, "status": status}


def record_case(facts: dict, category: str, diagnosis: str, remedy: str, status: str,
                actions_count: int = 1, db_path=None) -> int:
    """Store case in doctor_cases SQLite table."""
    facts = secret_guard.scrub_data(facts)
    category, diagnosis, remedy, status = (scrub_text(v) for v in (category, diagnosis, remedy, status))
    conn = connect(db_path)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO doctor_cases
           (ts, agent, step, error_class, error_message, last_5_actions, category, diagnosis, remedy, status, actions_count, task_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ts, facts["agent"], facts["step"], facts["error_class"], facts["error_message"],
         json.dumps(facts["last_5_actions"]), category, diagnosis, remedy, status, actions_count, facts.get("task_id"))
    )
    conn.commit()
    case_id = cur.lastrowid
    conn.close()
    return case_id


def format_last_cases(limit: int = 10, db_path=None) -> str:
    """Format last N cases from doctor_cases table for Telegram /doctor command."""
    try:
        conn = connect(db_path)
        rows = conn.execute(
            "SELECT * FROM doctor_cases ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
    except Exception:
        rows = []

    if not rows:
        return "🩺 Doctor Log: No cases recorded yet. All robot agents healthy! ✅"

    lines = [f"🩺 Doctor Cases (Last {len(rows)}):\n"]
    for r in rows:
        ts_str = r["ts"][:16].replace("T", " ")
        cat_title = PLAYBOOK.get(r["category"], r["category"])
        lines.append(
            f"#{r['id']} [{ts_str}] {r['agent']} ({r['step']})\n"
            f"• Problem: {cat_title}\n"
            f"• Diagnosis: {r['diagnosis']}\n"
            f"• Action: {r['remedy']} [{r['status']}]\n"
        )
    return "\n".join(lines)


def handle_callback(token: str, owner_chat_id: str, data: str, db_path=None) -> str | None:
    """Handle doc:retry:ID, doc:skip:ID, doc:autofix:ID callback button presses."""
    match = re.fullmatch(r"^doc:(retry|skip|autofix):(\d+)$", data or "")
    if not match:
        return None

    action, case_id = match.group(1), int(match.group(2))
    conn = connect(db_path)
    case = conn.execute("SELECT * FROM doctor_cases WHERE id = ?", (case_id,)).fetchone()
    if not case:
        conn.close()
        reply = f"Doctor case #{case_id} not found."
        notify.send(reply)
        return reply

    task_id = case["task_id"]

    if action == "retry":
        if task_id:
            conn.execute("UPDATE tasks SET status = 'queued', attempts = 0 WHERE id = ?", (task_id,))
        conn.execute("UPDATE doctor_cases SET status = 'retry_requested' WHERE id = ?", (case_id,))
        conn.commit()
        conn.close()
        reply = f"Doctor: Case #{case_id} re-queued for immediate retry. 🔄"

    elif action == "skip":
        if task_id:
            conn.execute("UPDATE tasks SET status = 'deferred' WHERE id = ?", (task_id,))
        conn.execute("UPDATE doctor_cases SET status = 'skipped_24h' WHERE id = ?", (case_id,))
        conn.commit()
        conn.close()
        reply = f"Doctor: Case #{case_id} skipped for 24 hours. ⏭"

    elif action == "autofix":
        conn.execute("UPDATE doctor_cases SET status = 'autofix_dispatched' WHERE id = ?", (case_id,))
        conn.commit()
        conn.close()
        summary = f"Step: {case['step']}\nProblem: {case['error_class']}: {case['error_message']}"
        try:
            autofix.dispatch_fix(summary)
            reply = f"Doctor: Auto-fix workflow dispatched for Case #{case_id}. 🛠"
        except Exception:
            reply = f"Doctor: Auto-fix could not be dispatched for Case #{case_id}."

    if token and owner_chat_id:
        import telegram_inbox
        telegram_inbox.send_text(token, owner_chat_id, reply)
    return reply


def run_doctor_checks(db_path=None) -> dict:
    """Run routine scan for stuck tasks and repeat loops, applying remedies."""
    if is_paused(db_path):
        return {"stuck_found": 0, "handled": 0, "status": "paused"}
    stuck = detect_stuck_tasks(db_path=db_path)
    handled_count = 0
    for s in stuck:
        diagnose_and_remedy(
            agent=s["agent"],
            step=s["step"],
            error=f"Task stuck for 2 cycles (attempts={s['attempts']})",
            task_id=s["task_id"],
            db_path=db_path
        )
        handled_count += 1

    return {"stuck_found": len(stuck), "handled": handled_count}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(format_last_cases())
    else:
        print(run_doctor_checks())
