"""Safety layer: kill switch, daily LLM call cap, and the human approval queue.

Nothing in this project publishes anything on its own. Any output meant for the
public goes into the approvals table and waits for a human decision.

CLI:
  python3 guardrails.py status
  python3 guardrails.py kill [reason]      # stop all agent work immediately
  python3 guardrails.py resume
  python3 guardrails.py approvals          # list pending items
  python3 guardrails.py show <id>
  python3 guardrails.py approve <id>
  python3 guardrails.py reject <id>
"""
import datetime
import json
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "agent_data.db")
KILL_FILE = os.path.join(BASE_DIR, "KILL_SWITCH")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DEFAULT_DAILY_CAP = 100


def connect(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS usage_log (
        day TEXT PRIMARY KEY, llm_calls INTEGER DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, title TEXT,
        payload TEXT, status TEXT DEFAULT 'pending', review TEXT, decided_ts TEXT)""")
    return conn


def today():
    return datetime.date.today().isoformat()


# ---- kill switch -----------------------------------------------------------
def is_killed():
    return os.path.exists(KILL_FILE) or os.environ.get("AGENT_PAUSED") == "1"


def kill(reason="manual"):
    with open(KILL_FILE, "w", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {reason}\n")


def resume():
    if os.path.exists(KILL_FILE):
        os.remove(KILL_FILE)


# ---- LLM call budget ---------------------------------------------------------
def daily_cap():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return int(json.load(f).get("daily_llm_call_cap", DEFAULT_DAILY_CAP))
    except Exception:
        return DEFAULT_DAILY_CAP


def calls_today(db_path=None):
    conn = connect(db_path)
    row = conn.execute("SELECT llm_calls FROM usage_log WHERE day = ?", (today(),)).fetchone()
    conn.close()
    return row[0] if row else 0


def can_call_llm(db_path=None):
    return (not is_killed()) and calls_today(db_path) < daily_cap()


def record_call(db_path=None):
    conn = connect(db_path)
    conn.execute("INSERT INTO usage_log (day, llm_calls) VALUES (?, 1) "
                 "ON CONFLICT(day) DO UPDATE SET llm_calls = llm_calls + 1", (today(),))
    conn.commit()
    conn.close()


# ---- approvals ---------------------------------------------------------------
def request_approval(kind, title, payload, review=None, db_path=None):
    conn = connect(db_path)
    cur = conn.execute(
        "INSERT INTO approvals (ts, kind, title, payload, review) VALUES (?, ?, ?, ?, ?)",
        (datetime.datetime.now().isoformat(), kind, title, payload, review))
    conn.commit()
    approval_id = cur.lastrowid
    conn.close()
    return approval_id


def set_review(approval_id, review, db_path=None):
    conn = connect(db_path)
    conn.execute("UPDATE approvals SET review = ? WHERE id = ?", (review, approval_id))
    conn.commit()
    conn.close()


def get_approval(approval_id, db_path=None):
    conn = connect(db_path)
    row = conn.execute("SELECT id, ts, kind, title, payload, status, review, decided_ts "
                       "FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    conn.close()
    if not row:
        return None
    keys = ("id", "ts", "kind", "title", "payload", "status", "review", "decided_ts")
    return dict(zip(keys, row))


def pending(db_path=None):
    conn = connect(db_path)
    rows = conn.execute("SELECT id, ts, kind, title, review FROM approvals "
                        "WHERE status = 'pending' ORDER BY id").fetchall()
    conn.close()
    return [dict(zip(("id", "ts", "kind", "title", "review"), r)) for r in rows]


def decide(approval_id, approved, db_path=None):
    status = "approved" if approved else "rejected"
    conn = connect(db_path)
    cur = conn.execute("UPDATE approvals SET status = ?, decided_ts = ? WHERE id = ? AND status = 'pending'",
                       (status, datetime.datetime.now().isoformat(), approval_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return bool(changed)


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        print(f"Kill switch : {'ON (agents stopped)' if is_killed() else 'off'}")
        print(f"LLM calls   : {calls_today()} / {daily_cap()} today")
        print(f"Pending approvals: {len(pending())}")
    elif cmd == "kill":
        kill(" ".join(argv[1:]) or "manual")
        print("Kill switch ON. All agent work stops until: python3 guardrails.py resume")
    elif cmd == "resume":
        resume()
        print("Kill switch off.")
    elif cmd == "approvals":
        items = pending()
        for it in items:
            print(f"#{it['id']} [{it['kind']}] {it['title']}  review: {it['review'] or '-'}")
        if not items:
            print("No pending approvals.")
    elif cmd == "show" and len(argv) == 2:
        item = get_approval(int(argv[1]))
        print(json.dumps(item, indent=2) if item else "not found")
    elif cmd in ("approve", "reject") and len(argv) == 2:
        ok = decide(int(argv[1]), cmd == "approve")
        print("done" if ok else "not found or already decided")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
