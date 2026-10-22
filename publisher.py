"""Safe, owner-approved publishing queue.

Nothing in this module is enabled by default.  It deliberately keeps publish
records local and stores only a short, safe error label rather than provider
responses or credentials.
"""
import datetime
import json
import os
import sqlite3
import urllib.error
import urllib.request

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
    conn.execute("""CREATE TABLE IF NOT EXISTS publish_report_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT, publish_type TEXT NOT NULL,
        status TEXT NOT NULL, ts TEXT NOT NULL
    )""")
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
    """Add exactly one job for an already owner-approved item/type pair."""
    if publish_type not in PUBLISH_TYPES:
        raise ValueError("unknown publish type")
    if not isinstance(item_id, int) or item_id < 1:
        raise ValueError("invalid item id")
    if not isinstance(payload_path, str) or not payload_path:
        raise ValueError("payload path required")
    conn = connect(db_path)
    try:
        approved = conn.execute("SELECT 1 FROM approvals WHERE id = ? AND status = 'approved'", (item_id,)).fetchone()
        if not approved:
            return None
        cur = conn.execute("INSERT INTO publish_jobs (publish_type, item_id, payload_path, status, created_ts) "
                           "VALUES (?, ?, ?, 'queued', ?)", (publish_type, item_id, payload_path, utc_now(now)))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        conn.rollback()
        return None
    finally:
        conn.close()


def approved_product_pins(item_id, products_dir, db_path=None):
    """Return the approved product's pin folder, or None for any other item."""
    conn = connect(db_path)
    row = conn.execute("SELECT a.status, b.status, b.folder FROM approvals a "
                       "LEFT JOIN product_builds b ON b.approval_id = a.id WHERE a.id = ?", (item_id,)).fetchone()
    conn.close()
    if not row or row[0] != "approved" or row[1] != "built" or not row[2]:
        return None
    pins = os.path.join(products_dir, row[2], "pins")
    return pins if os.path.isdir(pins) else None


def queue_product_pins(item_id, products_dir, db_path=None, now=None):
    """Queue one Pinterest job using only pins belonging to an approved built product."""
    pins = approved_product_pins(item_id, products_dir, db_path)
    return queue_job("pinterest", item_id, pins, db_path, now) if pins else None


def finish_job(job_id, status, error=None, db_path=None, now=None, attempts=1):
    """Finish one job with a short caller-supplied safe error category."""
    if status not in JOB_STATUSES or status == "queued":
        raise ValueError("invalid final status")
    safe_error = (error or "")[:160]
    conn = connect(db_path)
    cur = conn.execute("UPDATE publish_jobs SET status = ?, attempts = attempts + ?, last_error = ?, finished_ts = ? "
                       "WHERE id = ? AND status = 'queued'",
                       (status, attempts, safe_error or None, utc_now(now), job_id))
    if cur.rowcount:
        publish_type = conn.execute("SELECT publish_type FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()[0]
        conn.execute("INSERT INTO publish_report_lines (publish_type, status, ts) VALUES (?, ?, ?)",
                     (publish_type, status, utc_now(now)))
    conn.commit()
    conn.close()
    return bool(cur.rowcount)


def daily_report_lines(db_path=None, day=None):
    """Short owner-report lines for every publishing result recorded on one UTC day."""
    day = day or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    conn = connect(db_path)
    rows = conn.execute("SELECT publish_type, status, COUNT(*) FROM publish_report_lines WHERE ts LIKE ? "
                        "GROUP BY publish_type, status", (f"{day}%",)).fetchall()
    conn.close()
    counts = {(kind, status): count for kind, status, count in rows}
    labels = {"gumroad": "Gumroad", "pinterest": "Pinterest", "telegram": "Telegram"}
    lines = []
    for kind in PUBLISH_TYPES:
        total = sum(counts.get((kind, status), 0) for status in ("done", "failed", "skipped"))
        if total:
            line = f"{labels[kind]}: {counts.get((kind, 'done'), 0)} posted, {counts.get((kind, 'failed'), 0)} failed"
            skipped = counts.get((kind, "skipped"), 0)
            lines.append(line + (f", {skipped} skipped" if skipped else ""))
    return lines


def _http_request(method, url, headers, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=15) as response:
        return {"status": response.getcode()}


class PublishAdapter:
    """Provider adapter with a single injectable request function and no credential logging."""
    publish_type = None
    token_env = None
    required_env = ()
    endpoint = ""

    def __init__(self, env=None):
        self.env = env if env is not None else os.environ
        self.dry_run_log = []

    def configured(self):
        return all(self.env.get(name, "").strip() for name in self.required_env)

    def request_data(self, job):
        return "POST", self.endpoint, {"Authorization": "Bearer " + self.env[self.token_env]}, {
            "item_id": job["item_id"], "payload_path": job["payload_path"]}

    def publish(self, job, http_request=None, dry_run=False):
        """Return (status, safe error label, attempts)."""
        if not self.configured():
            return "skipped", "not configured", 0
        if dry_run:
            self.dry_run_log.append({"type": self.publish_type, "item_id": job["item_id"],
                                     "payload_path": job["payload_path"]})
            return "done", None, 0
        request = http_request or _http_request
        method, url, headers, body = self.request_data(job)
        for attempt in (1, 2):
            try:
                reply = request(method, url, headers, body) or {}
                status_code = int(reply.get("status", 0))
            except (OSError, TimeoutError, urllib.error.URLError):
                status_code = 599
            if 200 <= status_code < 300:
                return "done", None, attempt
            if status_code < 500:
                return "failed", "permanent error", attempt
        return "failed", "temporary error", 2


class GumroadAdapter(PublishAdapter):
    publish_type = "gumroad"
    token_env = "GUMROAD_TOKEN"
    required_env = ("GUMROAD_TOKEN",)
    endpoint = "https://api.gumroad.com/v2/products"


class PinterestAdapter(PublishAdapter):
    publish_type = "pinterest"
    token_env = "PINTEREST_TOKEN"
    required_env = ("PINTEREST_TOKEN",)
    endpoint = "https://api.pinterest.com/v5/pins"


class TelegramChannelAdapter(PublishAdapter):
    publish_type = "telegram"
    token_env = "TELEGRAM_BOT_TOKEN"
    required_env = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID")

    @property
    def endpoint(self):
        return "https://api.telegram.org/bot" + self.env[self.token_env] + "/sendMessage"

    def request_data(self, job):
        # The endpoint necessarily carries the bot credential, but errors and logs never do.
        return "POST", self.endpoint, {"Content-Type": "application/json"}, {
            "chat_id": self.env["TELEGRAM_CHANNEL_ID"], "item_id": job["item_id"],
            "payload_path": job["payload_path"]}


ADAPTERS = {"gumroad": GumroadAdapter, "pinterest": PinterestAdapter, "telegram": TelegramChannelAdapter}


def attempt_job(job, db_path=None, http_request=None, dry_run=False, now=None):
    """Perform one queued job only when its own switch and the global stop allow it."""
    if not can_publish(job["type"], db_path):
        return None
    outcome, error, attempts = ADAPTERS[job["type"]]().publish(job, http_request, dry_run)
    finish_job(job["id"], outcome, error, db_path, now, attempts)
    return outcome
