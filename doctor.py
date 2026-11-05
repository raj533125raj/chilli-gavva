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

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

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
    "doctor_heal": "Doctor Heal Stuck Work",
    "weekly_summary": "Weekly Doctor Summary",
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
                actions_count: int = 1, db_path=None, ts: str = None) -> int:
    """Store case in doctor_cases SQLite table."""
    facts = secret_guard.scrub_data(facts)
    category, diagnosis, remedy, status = (scrub_text(v) for v in (category, diagnosis, remedy, status))
    conn = connect(db_path)
    if not ts:
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


def is_healed_today(item_key: str, db_path=None, now_dt=None) -> bool:
    """Return True if item_key has already received an automatic heal remedy today (UTC)."""
    if now_dt is None:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
    today_str = now_dt.strftime("%Y-%m-%d")
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM doctor_cases WHERE category = 'doctor_heal' AND step = ? AND ts LIKE ?",
            (item_key, f"{today_str}%")
        ).fetchone()
        return bool(row and row[0] > 0)
    finally:
        conn.close()


def get_heal_count(item_key: str, db_path=None) -> int:
    """Return total number of prior heal attempts for this item_key."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM doctor_cases WHERE category = 'doctor_heal' AND step = ?",
            (item_key,)
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def detect_all_stuck_items(db_path=None, now_ts=None) -> list[dict]:
    """Inspect the 4 stuck categories: pipeline stages, launch packs, office requests, scheduled missions."""
    now_ts = time.time() if now_ts is None else now_ts
    items = []
    conn = connect(db_path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

        # 1. Pipeline stages (product_deliveries)
        if "product_deliveries" in tables:
            rows = conn.execute("SELECT approval_id, status, reason, lease_until FROM product_deliveries").fetchall()
            for r in rows:
                aid = r["approval_id"]
                st = r["status"] or ""
                reason = r["reason"] or ""
                lease_until = float(r["lease_until"] or 0)
                item_key = f"pipeline_stage:{aid}"

                if st == "ready":
                    continue
                if lease_until > 0 and lease_until < now_ts:
                    items.append({
                        "category_type": "pipeline_stage",
                        "item_key": item_key,
                        "ident": aid,
                        "kind": "stale_lease",
                        "reason": f"Product #{aid} delivery lease expired at {lease_until:.0f}",
                        "raw": dict(r)
                    })
                elif st in ("retry", "needs_fix"):
                    items.append({
                        "category_type": "pipeline_stage",
                        "item_key": item_key,
                        "ident": aid,
                        "kind": "retry",
                        "reason": reason or f"Product #{aid} stuck in {st}",
                        "raw": dict(r)
                    })

        # 2. Launch packs (launch_packs)
        if "launch_packs" in tables:
            rows = conn.execute("SELECT approval_id, status, reasons, quality_score FROM launch_packs").fetchall()
            for r in rows:
                aid = r["approval_id"]
                st = r["status"] or ""
                reasons = r["reasons"] or ""
                item_key = f"launch_pack:{aid}"
                if st in ("needs_fix", "failed"):
                    items.append({
                        "category_type": "launch_pack",
                        "item_key": item_key,
                        "ident": aid,
                        "kind": "retry",
                        "reason": reasons or f"Launch pack #{aid} status {st}",
                        "raw": dict(r)
                    })

        # 3. Office requests (office_requests)
        if "office_requests" in tables:
            rows = conn.execute("SELECT id, receipt FROM office_requests").fetchall()
            for r in rows:
                req_id = r["id"]
                receipt_raw = r["receipt"]
                item_key = f"office_request:{req_id}"
                try:
                    data = json.loads(receipt_raw) if receipt_raw else {}
                except Exception:
                    data = {}
                state = data.get("state")
                reason = data.get("reason", "")
                if state == "held":
                    items.append({
                        "category_type": "office_request",
                        "item_key": item_key,
                        "ident": req_id,
                        "kind": "retry",
                        "reason": reason or f"Office request {req_id} held",
                        "raw": dict(r)
                    })

        # 4. Scheduled missions (office_missions & tasks)
        if "office_missions" in tables:
            rows = conn.execute("SELECT id, title, next_run, status FROM office_missions").fetchall()
            for r in rows:
                m_id = r["id"]
                st = r["status"] or ""
                next_run = float(r["next_run"] or 0)
                item_key = f"scheduled_mission:{m_id}"
                if st == "active" and next_run < (now_ts - 3600):
                    # Check if there is an active queued or running task for this mission
                    t_rows = conn.execute("SELECT payload FROM tasks WHERE kind='office_task' AND status IN ('queued', 'running')").fetchall()
                    has_active_task = False
                    for tr in t_rows:
                        if not tr[0]:
                            continue
                        try:
                            payload_data = json.loads(tr[0])
                            if isinstance(payload_data, dict) and payload_data.get("mission_id") == m_id:
                                has_active_task = True
                                break
                        except Exception as e:
                            # Bad JSON -> skip that item and record a short reason, never crash
                            items.append({
                                "category_type": "scheduled_mission",
                                "item_key": f"bad_payload_mission:{m_id}",
                                "ident": m_id,
                                "kind": "skip",
                                "reason": f"Corrupt task payload JSON: {type(e).__name__}",
                                "raw": dict(r)
                            })
                    if not has_active_task:
                        items.append({
                            "category_type": "scheduled_mission",
                            "item_key": item_key,
                            "ident": m_id,
                            "kind": "retry",
                            "reason": f"Mission {m_id} overdue by >1 hour without active task",
                            "raw": dict(r)
                        })

        if "tasks" in tables:
            rows = conn.execute("SELECT id, agent, kind, attempts, status, error FROM tasks WHERE status IN ('running', 'queued') AND attempts >= 2").fetchall()
            for r in rows:
                tid = r["id"]
                item_key = f"task:{tid}"
                items.append({
                    "category_type": "scheduled_mission",
                    "item_key": item_key,
                    "ident": tid,
                    "kind": "retry",
                    "reason": f"Task #{tid} ({r['kind']}) stuck (attempts={r['attempts']})",
                    "raw": dict(r)
                })

    finally:
        conn.close()

    return items


def heal_stuck_items(db_path=None, now_ts=None) -> dict:
    """Look at stuck items, applying ONE safe automatic remedy per item per day.

    Safe automatic remedies:
    1. Reset stale lease (e.g. expired lease_until in product_deliveries)
    2. Retry later (e.g. re-queue task, re-arm retry status)
    3. Mark "needs owner" with clear reason after 3 failed heals

    Safety guarantees:
    - Never deletes data
    - Never touches encrypted archives
    - Never calls AI more than once per item per day and never above daily cap
    """
    if is_paused(db_path):
        return {"healed": 0, "needs_owner": 0, "skipped": 0, "items_evaluated": 0, "status": "paused"}

    now_ts = time.time() if now_ts is None else now_ts
    stuck_items = detect_all_stuck_items(db_path=db_path, now_ts=now_ts)

    healed_count = 0
    needs_owner_count = 0
    skipped_count = 0

    conn = connect(db_path)
    try:
        for item in stuck_items:
            item_key = item["item_key"]
            cat_type = item["category_type"]
            ident = item["ident"]
            kind = item["kind"]
            reason = scrub_text(item["reason"])

            # 1. ONE safe remedy per item per day limit
            if is_healed_today(item_key, db_path=db_path):
                skipped_count += 1
                continue

            # Check daily AI cap if an AI call would be required (we never exceed daily cap)
            ai_calls_allowed = guardrails.can_call_llm(db_path)

            prior_heals = get_heal_count(item_key, db_path=db_path)

            # 2. Mark needs owner after 3 failed heals
            if prior_heals >= 3:
                owner_reason = f"Doctor: needs owner review after {prior_heals} failed heal attempts ({reason})"
                if cat_type == "pipeline_stage":
                    conn.execute("UPDATE product_deliveries SET status='needs_owner', reason=?, lease_until=0 WHERE approval_id=?",
                                 (owner_reason, ident))
                elif cat_type == "launch_pack":
                    conn.execute("UPDATE launch_packs SET status='needs_owner', reasons=? WHERE approval_id=?",
                                 (owner_reason, ident))
                elif cat_type == "office_request":
                    # Update receipt state to needs_owner
                    row = conn.execute("SELECT receipt FROM office_requests WHERE id=?", (ident,)).fetchone()
                    try:
                        rec = json.loads(row[0]) if row and row[0] else {}
                    except Exception:
                        rec = {}
                    rec["state"] = "needs_owner"
                    rec["reason"] = owner_reason
                    conn.execute("UPDATE office_requests SET receipt=? WHERE id=?", (json.dumps(rec), ident))
                elif cat_type == "scheduled_mission":
                    if isinstance(ident, int) or str(ident).isdigit():
                        conn.execute("UPDATE tasks SET status='needs_owner', error=? WHERE id=?", (owner_reason, int(ident)))
                    else:
                        conn.execute("UPDATE office_missions SET status='needs_owner' WHERE id=?", (str(ident),))

                conn.commit()

                facts = {
                    "agent": "Doctor",
                    "step": item_key,
                    "error_class": "StuckWorkError",
                    "error_message": owner_reason,
                    "last_5_actions": [f"Prior heal attempts: {prior_heals}"],
                    "task_id": ident if (cat_type == "scheduled_mission" and (isinstance(ident, int) or str(ident).isdigit())) else None
                }
                record_case(
                    facts=facts,
                    category="doctor_heal",
                    diagnosis=f"Stuck item {item_key} exceeded heal limit",
                    remedy=f"Marked 'needs owner': {owner_reason}",
                    status="needs_owner",
                    actions_count=prior_heals + 1,
                    db_path=db_path
                )
                needs_owner_count += 1
                continue

            # 3. Apply safe automatic remedy: reset stale lease or retry later
            if kind == "stale_lease" and cat_type == "pipeline_stage":
                conn.execute("UPDATE product_deliveries SET lease_until=0 WHERE approval_id=?", (ident,))
                conn.commit()
                remedy_desc = f"Reset stale lease for product #{ident} (was expired)"
                heal_status = "lease_reset"
            elif cat_type == "pipeline_stage":
                conn.execute("UPDATE product_deliveries SET status='building', lease_until=? WHERE approval_id=?",
                             (now_ts + 600, ident))
                conn.commit()
                remedy_desc = f"Scheduled retry for product #{ident} build"
                heal_status = "retry_scheduled"
            elif cat_type == "launch_pack":
                conn.execute("UPDATE launch_packs SET status='ready_for_retry' WHERE approval_id=?", (ident,))
                conn.commit()
                remedy_desc = f"Scheduled retry for launch pack #{ident}"
                heal_status = "retry_scheduled"
            elif cat_type == "office_request":
                # Re-arm office request for retry without deleting any records
                row = conn.execute("SELECT receipt FROM office_requests WHERE id=?", (ident,)).fetchone()
                try:
                    rec = json.loads(row[0]) if row and row[0] else {}
                except Exception:
                    rec = {}
                rec["state"] = "retrying"
                conn.execute("UPDATE office_requests SET receipt=? WHERE id=?", (json.dumps(rec), ident))
                conn.commit()
                remedy_desc = f"Scheduled retry for office request {ident}"
                heal_status = "retry_scheduled"
            elif cat_type == "scheduled_mission":
                if isinstance(ident, int) or str(ident).isdigit():
                    task_row = conn.execute("SELECT status FROM tasks WHERE id=?", (int(ident),)).fetchone()
                    curr_st = task_row[0] if task_row else None
                    if curr_st == "running":
                        # Never re-queue a task that is currently running
                        remedy_desc = f"Task #{ident} is currently running; skipped re-queue"
                        heal_status = "skipped_running"
                    else:
                        conn.execute("UPDATE tasks SET status='queued', attempts=0, error='Doctor: retry scheduled' WHERE id=?", (int(ident),))
                        conn.commit()
                        remedy_desc = f"Re-queued stuck task #{ident}"
                        heal_status = "retry_scheduled"
                else:
                    conn.execute("UPDATE office_missions SET next_run=? WHERE id=?", (now_ts, str(ident)))
                    conn.commit()
                    remedy_desc = f"Re-armed scheduled mission {ident} for execution"
                    heal_status = "retry_scheduled"
            else:
                remedy_desc = f"Retry scheduled for {item_key}"
                heal_status = "retry_scheduled"

            facts = {
                "agent": "Doctor",
                "step": item_key,
                "error_class": "StuckWorkRemedy",
                "error_message": reason,
                "last_5_actions": [f"Prior heal attempts: {prior_heals}"],
                "task_id": ident if (cat_type == "scheduled_mission" and (isinstance(ident, int) or str(ident).isdigit())) else None
            }
            record_case(
                facts=facts,
                category="doctor_heal",
                diagnosis=f"Stuck item detected: {reason}",
                remedy=remedy_desc,
                status=heal_status,
                actions_count=prior_heals + 1,
                db_path=db_path
            )
            healed_count += 1

    finally:
        conn.close()

    return {
        "healed": healed_count,
        "needs_owner": needs_owner_count,
        "skipped": skipped_count,
        "items_evaluated": len(stuck_items)
    }


def record_weekly_summary_if_sunday(db_path=None, now_dt=None) -> dict | None:
    """Generate and store weekly doctor summary if today is Sunday in IST."""
    if now_dt is None:
        now_dt = datetime.datetime.now(IST)
    elif now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=IST)
    else:
        now_dt = now_dt.astimezone(IST)

    # Sunday is weekday 6
    if now_dt.weekday() != 6:
        return None

    date_ist = now_dt.date().isoformat()
    conn = connect(db_path)
    try:
        # Check if weekly summary was already written today
        exists = conn.execute(
            "SELECT 1 FROM doctor_cases WHERE category='weekly_summary' AND (step = ? OR ts LIKE ?)",
            (f"weekly_summary_{date_ist}", f"{date_ist}%")
        ).fetchone()
        if exists:
            return None

        # Gather weekly statistics (past 7 days)
        start_date = (now_dt - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        heal_rows = conn.execute(
            "SELECT status, step, diagnosis, remedy FROM doctor_cases WHERE category='doctor_heal' AND ts >= ?",
            (start_date,)
        ).fetchall()

        healed_items = [r["step"] for r in heal_rows if r["status"] in ("lease_reset", "retry_scheduled")]
        stuck_items = [r["step"] for r in heal_rows if r["status"] == "needs_owner"]

        # Current remaining stuck items
        current_stuck = detect_all_stuck_items(db_path=db_path)
        for s in current_stuck:
            if s["item_key"] not in stuck_items:
                stuck_items.append(s["item_key"])

        summary_text = (
            f"Doctor Weekly Summary ({date_ist}):\n"
            f"• Items healed: {len(healed_items)}\n"
            f"• Items still stuck: {len(stuck_items)}\n"
        )
        if stuck_items:
            summary_text += "• Stuck details: " + ", ".join(scrub_text(k) for k in stuck_items[:5])

        facts = {
            "agent": "Doctor",
            "step": f"weekly_summary_{date_ist}",
            "error_class": "None",
            "error_message": "Clean routine report",
            "last_5_actions": [f"Healed: {len(healed_items)}", f"Stuck: {len(stuck_items)}"],
            "task_id": None
        }
        summary_ts = now_dt.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")
        case_id = record_case(
            facts=facts,
            category="weekly_summary",
            diagnosis=f"Healed {len(healed_items)}, stuck {len(stuck_items)}",
            remedy=summary_text,
            status="completed",
            actions_count=1,
            db_path=db_path,
            ts=summary_ts
        )
        return {
            "case_id": case_id,
            "date_ist": date_ist,
            "healed_count": len(healed_items),
            "stuck_count": len(stuck_items),
            "summary": summary_text
        }
    finally:
        conn.close()


def run_doctor_checks(db_path=None) -> dict:
    """Run routine scan for stuck tasks and repeat loops, and heal stuck work."""
    if is_paused(db_path):
        return {"stuck_found": 0, "handled": 0, "healed": 0, "status": "paused"}

    # 1. Standard task stuck check
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

    # 2. Heal stuck work across pipeline stages, launch packs, office requests, scheduled missions
    heal_res = heal_stuck_items(db_path=db_path)

    # 3. Weekly summary if Sunday IST
    record_weekly_summary_if_sunday(db_path=db_path)

    return {
        "stuck_found": len(stuck),
        "handled": handled_count,
        "healed": heal_res.get("healed", 0),
        "needs_owner": heal_res.get("needs_owner", 0),
        "skipped": heal_res.get("skipped", 0)
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(format_last_cases())
    else:
        print(run_doctor_checks())
