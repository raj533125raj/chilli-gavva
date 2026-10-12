"""Housekeeping so the database stays healthy and small.

  python3 maintenance.py             # backup, prune, integrity check, compact
  python3 maintenance.py --dry-run   # show what would be removed, change nothing

Never pruned: approvals (any status), campaigns and real earnings, validation_log, prompt tables.
Pruned: agent_logs, finished tasks, and usage_log rows older than the retention windows below.
Backups use SQLite's safe backup API and are rotated (newest kept). They live in backups/,
which is git-ignored.
"""
import datetime
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "agent_data.db")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")

KEEP_BACKUPS = 7
LOG_DAYS = 30
TASK_DAYS = 30
USAGE_DAYS = 90
MIN_LOGS_KEPT = 200          # always keep the newest logs even if old


def _cutoff(days):
    return (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()


def _has(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def backup(db_path=None, backup_dir=None, keep=KEEP_BACKUPS):
    # Backups sit next to the database they protect (project backups/ for the real one).
    backup_dir = backup_dir or (os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups") if db_path else BACKUP_DIR)
    db_path = db_path or DB_PATH
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(backup_dir, f"agent_data-{stamp}.db")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    old = sorted(f for f in os.listdir(backup_dir) if f.startswith("agent_data-") and f.endswith(".db"))
    removed = []
    for name in old[:-keep] if keep > 0 else old:
        os.remove(os.path.join(backup_dir, name))
        removed.append(name)
    return target, removed


def prune(db_path=None, dry_run=False):
    conn = sqlite3.connect(db_path or DB_PATH)
    counts = {}
    try:
        if _has(conn, "agent_logs"):
            where = ("timestamp < ? AND id NOT IN (SELECT id FROM agent_logs ORDER BY id DESC LIMIT ?)",
                     (_cutoff(LOG_DAYS), MIN_LOGS_KEPT))
            counts["agent_logs"] = conn.execute(f"SELECT COUNT(*) FROM agent_logs WHERE {where[0]}", where[1]).fetchone()[0]
            if not dry_run:
                conn.execute(f"DELETE FROM agent_logs WHERE {where[0]}", where[1])
        if _has(conn, "tasks"):
            sql = "finished_ts IS NOT NULL AND finished_ts < ? AND status IN ('done', 'failed')"
            counts["tasks"] = conn.execute(f"SELECT COUNT(*) FROM tasks WHERE {sql}", (_cutoff(TASK_DAYS),)).fetchone()[0]
            if not dry_run:
                conn.execute(f"DELETE FROM tasks WHERE {sql}", (_cutoff(TASK_DAYS),))
        if _has(conn, "usage_log"):
            day = (datetime.date.today() - datetime.timedelta(days=USAGE_DAYS)).isoformat()
            counts["usage_log"] = conn.execute("SELECT COUNT(*) FROM usage_log WHERE day < ?", (day,)).fetchone()[0]
            if not dry_run:
                conn.execute("DELETE FROM usage_log WHERE day < ?", (day,))
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return counts


def integrity(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def compact(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()


def run(db_path=None, backup_dir=None, dry_run=False):
    """Backup first (only if the database is healthy), then prune and compact."""
    report = {"integrity": integrity(db_path), "dry_run": dry_run}
    if report["integrity"] != "ok":
        report["stopped"] = "database failed the integrity check: nothing was changed"
        return report
    if not dry_run:
        report["backup"], report["old_backups_removed"] = backup(db_path, backup_dir)
    report["pruned"] = prune(db_path, dry_run)
    if not dry_run:
        compact(db_path)
        report["size_bytes"] = os.path.getsize(db_path or DB_PATH)
    return report


def run_if_due(db_path=None, backup_dir=None, every_hours=24):
    """Used by the worker: runs at most once per `every_hours`. Returns the report or None."""
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS maintenance_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, summary TEXT)")
    last = conn.execute("SELECT ts FROM maintenance_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.commit()
    conn.close()
    if last and datetime.datetime.fromisoformat(last[0]) > datetime.datetime.now() - datetime.timedelta(hours=every_hours):
        return None
    report = run(db_path, backup_dir)
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.execute("INSERT INTO maintenance_log (ts, summary) VALUES (?, ?)",
                 (datetime.datetime.now().isoformat(), str(report.get("pruned", report.get("stopped")))[:300]))
    conn.commit()
    conn.close()
    return report


if __name__ == "__main__":
    out = run(dry_run="--dry-run" in sys.argv)
    for k, v in out.items():
        print(f"{k}: {v}")
