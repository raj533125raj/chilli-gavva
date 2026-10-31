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
  python3 guardrails.py approve-above <score>  # bulk approve by score threshold
"""
import datetime
import json
import os
import re
import sqlite3
import sys
import contextvars
import functools
import tempfile
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Legacy callers without an explicit fixture must not open a decrypted runtime
# database merely because unittest was launched without CHILLI_TEST.
_TEST_DATABASE = (tempfile.TemporaryDirectory(prefix="robot-tests-")
                  if os.environ.get("CHILLI_TEST") == "1"
                  or "unittest" in sys.modules or "pytest" in sys.modules else None)
DB_PATH = os.path.join(_TEST_DATABASE.name if _TEST_DATABASE else BASE_DIR, "agent_data.db")
KILL_FILE = os.path.join(BASE_DIR, "KILL_SWITCH")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DEFAULT_DAILY_CAP = 500  # own safety cap on LLM calls per day (Groq free tier allows far more)

# When CHILLI_TEST=1, record_call() is a no-op so tests don't eat the real cap
_TESTING = os.environ.get("CHILLI_TEST") == "1"
_BUDGET_DB = contextvars.ContextVar("robot_budget_db", default=None)


class BudgetExceeded(RuntimeError):
    """The hard daily reservation limit was reached before a provider request."""


def _effective_db(db_path=None):
    return db_path if db_path is not None else (_BUDGET_DB.get() or DB_PATH)


@contextmanager
def budget_context(db_path=None):
    token = _BUDGET_DB.set(_effective_db(db_path))
    try:
        yield
    finally:
        _BUDGET_DB.reset(token)


def budget_scoped(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with budget_context(kwargs.get("db_path")):
            return function(*args, **kwargs)
    return wrapped


def connect(db_path=None):
    conn = sqlite3.connect(_effective_db(db_path))
    conn.execute("""CREATE TABLE IF NOT EXISTS usage_log (
        day TEXT PRIMARY KEY, llm_calls INTEGER DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, title TEXT,
        payload TEXT, status TEXT DEFAULT 'pending', review TEXT, decided_ts TEXT)""")
    conn.execute("CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT)")
    if "reason" not in [r[1] for r in conn.execute("PRAGMA table_info(approvals)").fetchall()]:
        conn.execute("ALTER TABLE approvals ADD COLUMN reason TEXT")  # why a human rejected a draft
    return conn


def today():
    return datetime.date.today().isoformat()


# ---- kill switch -----------------------------------------------------------
def is_killed():
    if os.path.exists(KILL_FILE) or os.environ.get("AGENT_PAUSED") == "1":
        return True
    # The /pause flag lives in the real database (saved by the cloud every run). Tests use their own
    # databases, so the real flag is ignored while tests run.
    return False if _TESTING else is_paused()


# ---- /pause and /resume (owner only, via Telegram) ---------------------------
def is_paused(db_path=None):
    try:
        conn = connect(db_path)
        row = conn.execute("SELECT value FROM control WHERE key = 'paused'").fetchone()
        conn.close()
        return bool(row and row[0] == "1")
    except sqlite3.Error:
        return False


def set_paused(paused, db_path=None):
    conn = connect(db_path)
    conn.execute("INSERT INTO control (key, value) VALUES ('paused', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", ("1" if paused else "0",))
    conn.commit()
    conn.close()


def kill(reason="manual"):
    with open(KILL_FILE, "w", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {reason}\n")


def resume():
    if os.path.exists(KILL_FILE):
        os.remove(KILL_FILE)


# ---- LLM call budget ---------------------------------------------------------
def configured_daily_cap():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            value = json.load(f).get("daily_llm_call_cap", DEFAULT_DAILY_CAP)
            if type(value) is not int:
                return DEFAULT_DAILY_CAP
            return min(DEFAULT_DAILY_CAP, max(0, value))
    except Exception:
        return DEFAULT_DAILY_CAP


def _cap(conn):
    row = conn.execute("SELECT value FROM control WHERE key='office_daily_call_cap'").fetchone()
    maximum = configured_daily_cap()
    if not row:
        return maximum
    try:
        return min(maximum, max(0, int(row[0])))
    except (ValueError, TypeError):
        return 0


def daily_cap(db_path=None):
    conn = connect(db_path)
    try:
        return _cap(conn)
    finally:
        conn.close()


def autonomy(db_path=None):
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT value FROM control WHERE key='office_autonomy'").fetchone()
        value = row[0] if row else "supervised"
        return value if value in ("manual", "supervised", "autonomous") else "manual"
    finally:
        conn.close()


def automation_allowed(db_path=None):
    return not is_killed() and not is_paused(db_path) and autonomy(db_path) != "manual"


def calls_today(db_path=None):
    conn = connect(db_path)
    row = conn.execute("SELECT llm_calls FROM usage_log WHERE day = ?", (today(),)).fetchone()
    conn.close()
    return row[0] if row else 0


def can_call_llm(db_path=None):
    return (not is_killed()) and not is_paused(db_path) and calls_today(db_path) < daily_cap(db_path)


def record_call(db_path=None):
    if _TESTING:
        return  # tests must not contaminate the real daily cap
    conn = connect(db_path)
    try:
        with conn:
            # Every retry/fallback reserves atomically before network IO, so parallel callers
            # cannot both consume the last call. A failed request still consumes its reservation.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT llm_calls FROM usage_log WHERE day=?", (today(),)).fetchone()
            if (row[0] if row else 0) >= _cap(conn):
                raise BudgetExceeded("Daily LLM call budget reached.")
            conn.execute("INSERT INTO usage_log (day, llm_calls) VALUES (?, 1) "
                         "ON CONFLICT(day) DO UPDATE SET llm_calls = llm_calls + 1", (today(),))
    finally:
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


def decide(approval_id, approved, db_path=None, reason=None):
    status = "approved" if approved else "rejected"
    conn = connect(db_path)
    cur = conn.execute("UPDATE approvals SET status = ?, decided_ts = ?, reason = ? WHERE id = ? AND status = 'pending'",
                       (status, datetime.datetime.now().isoformat(), reason, approval_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return bool(changed)


REJECT_REASONS = ("price", "too generic", "bad niche", "poor quality", "other")


def set_reason(approval_id, reason, db_path=None):
    """Save why a human rejected a draft (only for rejected drafts; known reasons only)."""
    if reason not in REJECT_REASONS:
        return False
    conn = connect(db_path)
    cur = conn.execute("UPDATE approvals SET reason = ? WHERE id = ? AND status = 'rejected'", (reason, approval_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return bool(changed)


def _parse_score(review_text):
    """Extract numeric score from review strings like 'score 78/100' or 'Good score 80/100'."""
    if not review_text:
        return None
    m = re.search(r'(\d+)\s*/\s*100', review_text)
    return int(m.group(1)) if m else None


def approve_above(threshold, db_path=None):
    """Bulk-approve all pending items whose review score is >= threshold. Returns list of approved ids."""
    items = pending(db_path)
    approved_ids = []
    for item in items:
        score = _parse_score(item.get("review") or "")
        if score is not None and score >= threshold:
            if decide(item["id"], True, db_path):
                approved_ids.append(item["id"])
    return approved_ids


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
            score = _parse_score(it.get("review") or "")
            score_str = f"  score={score}/100" if score is not None else ""
            print(f"#{it['id']} [{it['kind']}] {it['title']}{score_str}")
        if not items:
            print("No pending approvals.")
    elif cmd == "show" and len(argv) == 2:
        item = get_approval(int(argv[1]))
        print(json.dumps(item, indent=2) if item else "not found")
    elif cmd in ("approve", "reject") and len(argv) == 2:
        ok = decide(int(argv[1]), cmd == "approve")
        print("done" if ok else "not found or already decided")
    elif cmd == "approve-above" and len(argv) == 2:
        try:
            threshold = int(argv[1])
        except ValueError:
            print("Usage: python3 guardrails.py approve-above <score 0-100>")
            return
        ids = approve_above(threshold)
        if ids:
            print(f"Approved {len(ids)} item(s): {ids}")
        else:
            print(f"No pending items with score >= {threshold}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
