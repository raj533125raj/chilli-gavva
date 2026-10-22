"""Builds the public status snapshot used by the dashboard (web/index.html).

The snapshot contains counts, titles and short logs only. It never contains
API keys, config values, or the full text of drafts.
"""
import datetime
import json
import os
import sqlite3
import sys

import guardrails
import improver
import service_intake
import tool_builder
import workforce

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_PATH = os.path.join(BASE_DIR, "web", "data", "status.json")


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _money(db_path):
    """Real money only (rows recorded with proof by record_earning.py), plus the activity log."""
    conn = sqlite3.connect(db_path or guardrails.DB_PATH)
    real = 0.0
    if _has_table(conn, "campaigns") and "source" in _cols(conn, "campaigns"):
        real = conn.execute("SELECT COALESCE(SUM(earnings), 0) FROM campaigns "
                            "WHERE source = 'real' AND status != 'VOIDED'").fetchone()[0]
    logs = []
    if _has_table(conn, "agent_logs"):
        logs = conn.execute("SELECT timestamp, agent_name, status, message FROM agent_logs "
                            "ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()
    return real, [{"ts": t[:19], "agent": a, "status": s, "message": (m or "")[:200]} for t, a, s, m in logs]


def purge_simulated(db_path=None):
    """Delete the old engine.py simulation rows: fake money, its agent logs, fake discoveries and
    health reports. Real earnings (source = 'real'), drafts, approvals, tasks and Telegram state
    are never touched. Safe to run every cycle; returns the number of rows deleted per table."""
    conn = sqlite3.connect(db_path or guardrails.DB_PATH)
    deleted = {}
    try:
        if _has_table(conn, "campaigns"):
            where = "COALESCE(source, 'simulated') != 'real'" if "source" in _cols(conn, "campaigns") else "1"
            deleted["campaigns"] = conn.execute(f"DELETE FROM campaigns WHERE {where}").rowcount
        if _has_table(conn, "research_discoveries"):
            where = "COALESCE(source, 'simulated') != 'real'" if "source" in _cols(conn, "research_discoveries") else "1"
            deleted["research_discoveries"] = conn.execute(f"DELETE FROM research_discoveries WHERE {where}").rowcount
        for table in ("agent_logs", "health_reports"):  # only the old simulation (engine.py) writes these
            if _has_table(conn, table):
                deleted[table] = conn.execute(f"DELETE FROM {table}").rowcount
        conn.commit()
    finally:
        conn.close()
    return {t: n for t, n in deleted.items() if n}



def _llm_providers() -> list:
    """Return LLM provider status list; silently returns [] if llm_router unavailable."""
    try:
        import llm_router
        return llm_router.provider_status()
    except Exception:
        return []

def build_status_private(db_path=None):
    """The full local snapshot (money, prompts, logs, ...). Used by the local server only; never written to the repo."""
    real, logs = _money(db_path)
    conn = workforce.connect(db_path)
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall())
    recent = [{"id": r[0], "kind": r[1], "status": r[2], "agent": r[3], "attempts": r[4]} for r in
              conn.execute("SELECT id, kind, status, agent, attempts FROM tasks ORDER BY id DESC LIMIT 12")]
    prompts = [{"id": r[0], "name": r[1], "active": bool(r[2]), "trials": r[3],
                "quality": round(improver.quality((r[3], r[4], r[5], r[6])), 1)} for r in
               conn.execute("SELECT id, name, active, trials, score_sum, decisions, approvals "
                            "FROM prompt_versions ORDER BY name, id")]
    validation = None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'validation_log'").fetchone():
        v = conn.execute("SELECT COUNT(*), COALESCE(SUM(visitors),0), COALESCE(SUM(signups),0), "
                         "COALESCE(SUM(engaged),0) FROM validation_log").fetchone()
        validation = {"days": v[0], "visitors": v[1], "signups": v[2], "engaged": v[3],
                      "targets": {"visitors": 1000, "signups": 50, "engaged_pct": 30}}
    conn.close()
    # Service requests (last 10)
    svc_reqs = service_intake.list_requests(db_path)[:10]
    # Built tools
    tb_conn = tool_builder.connect(db_path)
    built_tools = [{"slug": r[0], "title": r[1], "built_ts": r[2]} for r in
                   tb_conn.execute("SELECT slug, title, built_ts FROM tool_pages ORDER BY approval_id DESC LIMIT 10").fetchall()]
    tb_conn.close()
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "money": {"real_usd": round(real, 2)},
        "kill_switch": guardrails.is_killed(),
        "worker": workforce.worker_status(db_path),
        "llm": {"calls_today": guardrails.calls_today(db_path), "cap": guardrails.daily_cap()},
        "agents": workforce.agent_states(db_path),
        "task_counts": counts,
        "recent_tasks": recent,
        "pending_approvals": guardrails.pending(db_path),
        "prompts": prompts,
        "validation": validation,
        "logs": logs,
        "service_requests": svc_reqs,
        "built_tools": built_tools,
        "llm_providers": _llm_providers(),
    }


# The public snapshot: only these fields. No money, prompts, logs, validation, service requests, built tools,
# worker info or approval review text. The robot's own files and the dashboard are the only readers.
PUBLIC_AGENT_KEYS = ("name", "level", "role", "kinds", "state", "tasks_done")
PUBLIC_TASK_KEYS = ("id", "kind", "status", "agent", "attempts")
PUBLIC_APPROVAL_KEYS = ("id", "ts", "kind", "title")


def _public_approval(approval):
    """Return the small, anonymous approval record safe for the public file.

    A pending approval is an idea the owner has not accepted yet.  Its real
    title is therefore private, even though the identifier and queue time are
    useful on the public dashboard.
    """
    item = {k: approval.get(k) for k in PUBLIC_APPROVAL_KEYS}
    if approval.get("status") != "approved":
        item["title"] = f"Draft #{approval.get('id')}"
    return item


def build_status(db_path=None):
    """The snapshot written to web/data/status.json (safe for a public repo)."""
    full = build_status_private(db_path)
    return {
        "generated_at": full["generated_at"],
        "kill_switch": full["kill_switch"],
        "llm": {"calls_today": full["llm"]["calls_today"], "cap": full["llm"]["cap"]},
        "agents": [{k: a.get(k) for k in PUBLIC_AGENT_KEYS} for a in full["agents"]],
        "task_counts": full["task_counts"],
        "recent_tasks": [{k: t.get(k) for k in PUBLIC_TASK_KEYS} for t in full["recent_tasks"]],
        "pending_approvals": [_public_approval(a) for a in full["pending_approvals"]],
        "worker": {"running": full["worker"].get("running"), "age_seconds": full["worker"].get("age_seconds")},
    }


def write_status(path=None, db_path=None):
    path = path or STATUS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build_status(db_path), f, indent=1)
    return path


if __name__ == "__main__":
    print("wrote", write_status())
