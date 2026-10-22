"""Safe, owner-approved publishing queue.

Nothing in this module is enabled by default.  It deliberately keeps publish
records local and stores only a short, safe error label rather than provider
responses or credentials.
"""
import datetime
import sqlite3

import guardrails

PUBLISH_TYPES = ("gumroad", "pinterest", "telegram")
JOB_STATUSES = ("queued", "done", "failed", "skipped")


def utc_now(now=None):
    """UTC timestamp used by tests as well as normal runs."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS publish_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        publish_type TEXT NOT NULL,
        item_id INTEGER NOT NULL,
        payload_path TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_ts TEXT NOT NULL,
        finished_ts TEXT,
        UNIQUE(publish_type, item_id)
    )""")
    conn.execute("CREATE TABLE IF NOT EXISTS publish_state (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def jobs(db_path=None):
    conn = connect(db_path)
    rows = conn.execute("SELECT id, publish_type, item_id, payload_path, status, attempts, last_error, created_ts, "
                        "finished_ts FROM publish_jobs ORDER BY id").fetchall()
    conn.close()
    keys = ("id", "type", "item_id", "payload_path", "status", "attempts", "last_error", "created_ts",
            "finished_ts")
    return [dict(zip(keys, row)) for row in rows]


def get_state(key, default=None, db_path=None):
    conn = connect(db_path)
    row = conn.execute("SELECT value FROM publish_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_state(key, value, db_path=None):
    conn = connect(db_path)
    conn.execute("INSERT INTO publish_state (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
    conn.commit()
    conn.close()


def set_enabled(publish_type, enabled, db_path=None):
    if publish_type not in PUBLISH_TYPES:
        raise ValueError("unknown publish type")
    set_state(f"publish_enabled:{publish_type}", "1" if enabled else "0", db_path)


def enabled(publish_type, db_path=None):
    if publish_type not in PUBLISH_TYPES:
        return False
    return get_state(f"publish_enabled:{publish_type}", "0", db_path) == "1"


def set_global_stop(stopped, db_path=None):
    """The owner pause control for every publish type at once."""
    set_state("publish_global_stop", "1" if stopped else "0", db_path)


def globally_stopped(db_path=None):
    return get_state("publish_global_stop", "0", db_path) == "1"


def can_publish(publish_type, db_path=None):
    return enabled(publish_type, db_path) and not globally_stopped(db_path) and not guardrails.is_killed()


def queue_job(publish_type, item_id, payload_path, db_path=None, now=None):
    """Add exactly one publish job for an item/type pair; duplicate requests are harmless."""
    if publish_type not in PUBLISH_TYPES:
        raise ValueError("unknown publish type")
    if not isinstance(item_id, int) or item_id < 1:
        raise ValueError("invalid item id")
    if not isinstance(payload_path, str) or not payload_path:
        raise ValueError("payload path required")
    conn = connect(db_path)
    try:
        cur = conn.execute("INSERT INTO publish_jobs (publish_type, item_id, payload_path, status, created_ts) "
                           "VALUES (?, ?, ?, 'queued', ?)", (publish_type, item_id, payload_path, utc_now(now)))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        conn.rollback()
        return None
    finally:
        conn.close()


def finish_job(job_id, status, error=None, db_path=None, now=None):
    """Finish one job with a short caller-supplied safe error category."""
    if status not in JOB_STATUSES or status == "queued":
        raise ValueError("invalid final status")
    safe_error = (error or "")[:160]
    conn = connect(db_path)
    cur = conn.execute("UPDATE publish_jobs SET status = ?, attempts = attempts + 1, last_error = ?, finished_ts = ? "
                       "WHERE id = ? AND status = 'queued'",
                       (status, safe_error or None, utc_now(now), job_id))
    conn.commit()
    conn.close()
    return bool(cur.rowcount)
