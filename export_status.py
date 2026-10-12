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
import workforce

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_PATH = os.path.join(BASE_DIR, "web", "data", "status.json")


def _money(db_path):
    conn = sqlite3.connect(db_path or guardrails.DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS campaigns (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
        niche TEXT, content_title TEXT, clicks INTEGER, earnings REAL, status TEXT, source TEXT DEFAULT 'simulated')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
        agent_name TEXT, department TEXT, status TEXT, message TEXT)""")
    # Older databases (created before the ledger) have no source column: add it, rows count as simulated.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(campaigns)").fetchall()]
    if "source" not in cols:
        conn.execute("ALTER TABLE campaigns ADD COLUMN source TEXT DEFAULT 'simulated'")
        conn.commit()
    real = conn.execute("SELECT COALESCE(SUM(earnings), 0) FROM campaigns WHERE source = 'real'").fetchone()[0]
    total = conn.execute("SELECT COALESCE(SUM(earnings), 0) FROM campaigns").fetchone()[0]
    logs = conn.execute("SELECT timestamp, agent_name, status, message FROM agent_logs "
                        "ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()
    return real, total - real, [{"ts": t[:19], "agent": a, "status": s, "message": m[:200]} for t, a, s, m in logs]


def build_status(db_path=None):
    real, simulated, logs = _money(db_path)
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
    return {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "mode": "mixed" if real > 0 else "simulation",
        "money": {"real_usd": round(real, 2), "simulated_usd": round(simulated, 2)},
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
    }


def write_status(path=None, db_path=None):
    path = path or STATUS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build_status(db_path), f, indent=1)
    return path


if __name__ == "__main__":
    print("wrote", write_status())
