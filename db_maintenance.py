"""Database health, safe cleanup, backups and schema migrations for the robot DB.

Nothing here touches product files or leaves copies in the repo folder.  The
check copy is made with SQLite's backup API inside the system temp folder,
verified, and then deleted.

CLI (optional, same functions as the /dbcare plugin):
  python3 db_maintenance.py --report
  python3 db_maintenance.py --clean
  python3 db_maintenance.py --check-copy
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / "agent_data.db")

SCHEMA_TABLE = "schema_migrations"
LOG_RETENTION_DAYS = 90
TASK_RETENTION_DAYS = 60
VACUUM_FREE_RATIO = 0.20
LOCK_RETRIES = 5
LOCK_BASE_DELAY = 0.05

# Tables that are audit/history data.  Approvals and sales records are never
# in this list: an approval (approved, rejected or pending) stays forever.
DEFAULT_LOG_RULES: Tuple[Tuple[str, str], ...] = (
    ("agent_logs", "timestamp"),
    ("provider_log", "day"),
    ("usage_log", "day"),
    ("button_log", "ts"),
    ("maintenance_log", "ts"),
)

FINISHED_TASK_STATUSES = ("done", "failed", "cancelled", "canceled", "finished", "complete", "completed")

PROTECTED_TABLES = frozenset({"approvals"})

DbPath = Union[str, os.PathLike, sqlite3.Connection]


class DatabaseBusyError(RuntimeError):
    """The database stayed locked after all retries."""


class MigrationError(RuntimeError):
    """A registered schema migration failed and was rolled back."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


_MIGRATIONS: List[Migration] = []


# ---------------------------------------------------------------------------
# Registry / migrations
# ---------------------------------------------------------------------------
def register_migration(version: int, name: str, apply: Callable[[sqlite3.Connection], None]) -> Migration:
    """Register one migration.  No production table is changed by the registry itself."""
    if type(version) is not int or version < 1:
        raise ValueError("migration version must be an integer >= 1")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("migration name must be a non-empty string")
    if not callable(apply):
        raise ValueError("migration apply must be callable")
    if any(m.version == version for m in _MIGRATIONS):
        raise ValueError(f"migration version {version} is already registered")
    if any(m.name == name for m in _MIGRATIONS):
        raise ValueError(f"migration name {name!r} is already registered")
    migration = Migration(version=version, name=name.strip(), apply=apply)
    _MIGRATIONS.append(migration)
    _MIGRATIONS.sort(key=lambda m: m.version)
    return migration


def registered_migrations() -> List[Migration]:
    """Return a copy of the migration registry in version order."""
    return list(_MIGRATIONS)


def clear_migrations() -> None:
    """Remove every registered migration (tests and one-off tooling only)."""
    _MIGRATIONS.clear()


def _now_utc(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    if now is None:
        return _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=_dt.timezone.utc)
    return now.astimezone(_dt.timezone.utc)


def _naive_utc(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    return _now_utc(now).replace(tzinfo=None)


def ensure_schema_table(conn: sqlite3.Connection) -> None:
    """Create the migration registry table only; existing tables are untouched."""
    conn.execute(
        f'CREATE TABLE IF NOT EXISTS "{SCHEMA_TABLE}" ('
        "version INTEGER PRIMARY KEY, "
        "name TEXT NOT NULL, "
        "applied_ts TEXT NOT NULL)"
    )


def current_version(path: DbPath, retries: int = LOCK_RETRIES,
                    sleep_fn: Callable[[float], None] = time.sleep) -> int:
    """Latest applied migration version, or 0 when nothing has been applied."""

    def _work() -> int:
        conn, owned = _connection(path)
        try:
            ensure_schema_table(conn)
            conn.commit()
            row = conn.execute(f'SELECT MAX(version) FROM "{SCHEMA_TABLE}"').fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            if owned:
                conn.close()

    return _retry(_work, retries=retries, sleep_fn=sleep_fn)


def pending_migrations(migrations: Optional[Sequence[Migration]] = None,
                       applied_version: Optional[int] = None) -> List[Migration]:
    """Migrations newer than ``applied_version`` (or the registry's stored version)."""
    registry = list(migrations if migrations is not None else _MIGRATIONS)
    if applied_version is None:
        applied_version = 0
    return sorted((m for m in registry if m.version > int(applied_version)), key=lambda m: m.version)


def apply_migrations(path: DbPath, migrations: Optional[Sequence[Migration]] = None,
                     now: Optional[_dt.datetime] = None, retries: int = LOCK_RETRIES,
                     sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Apply pending migrations, one transaction each.  Safe to run repeatedly."""
    stamp = _now_utc(now).isoformat()

    def _work() -> Dict[str, Any]:
        conn, owned = _connection(path)
        applied: List[Dict[str, Any]] = []
        try:
            ensure_schema_table(conn)
            conn.commit()
            row = conn.execute(f'SELECT MAX(version) FROM "{SCHEMA_TABLE}"').fetchone()
            version = int(row[0]) if row and row[0] is not None else 0
            registry = list(migrations if migrations is not None else _MIGRATIONS)
            for migration in sorted(registry, key=lambda m: m.version):
                if migration.version <= version:
                    continue
                savepoint = f"migration_{migration.version}"
                conn.execute(f"SAVEPOINT {savepoint}")
                try:
                    migration.apply(conn)
                    conn.execute(
                        f'INSERT INTO "{SCHEMA_TABLE}" (version, name, applied_ts) VALUES (?, ?, ?)',
                        (migration.version, migration.name, stamp),
                    )
                    conn.execute(f"PRAGMA user_version = {int(migration.version)}")
                    conn.execute(f"RELEASE {savepoint}")
                    conn.commit()
                except Exception as exc:  # noqa: BLE001 - report the exact migration
                    conn.execute(f"ROLLBACK TO {savepoint}")
                    conn.execute(f"RELEASE {savepoint}")
                    conn.commit()
                    raise MigrationError(f"migration {migration.version} ({migration.name}) failed: {exc}") from exc
                applied.append({"version": migration.version, "name": migration.name})
                version = migration.version
            return {"applied": applied, "version": version, "pending": 0}
        finally:
            if owned:
                conn.close()

    return _retry(_work, retries=retries, sleep_fn=sleep_fn)


# ---------------------------------------------------------------------------
# Connections and lock retry
# ---------------------------------------------------------------------------
def _is_lock_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _retry(fn: Callable[[], Any], retries: int = LOCK_RETRIES,
           base_delay: float = LOCK_BASE_DELAY,
           sleep_fn: Callable[[float], None] = time.sleep) -> Any:
    """Run ``fn``; on a locked database retry with exponential backoff."""
    attempts = max(1, int(retries))
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc):
                raise
            last = exc
            if attempt == attempts - 1:
                break
            sleep_fn(base_delay * (2 ** attempt))
    raise DatabaseBusyError(f"database stayed locked after {attempts} attempt(s): {last}")


def _connection(path: DbPath, timeout: float = 5.0) -> Tuple[sqlite3.Connection, bool]:
    if isinstance(path, sqlite3.Connection):
        return path, False
    conn = sqlite3.connect(str(path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn, True


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _table_names(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()]


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _require_file(path: DbPath) -> None:
    if not isinstance(path, sqlite3.Connection):
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"database not found: {p}")


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------
def integrity_check(path: DbPath, retries: int = LOCK_RETRIES,
                    sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Run PRAGMA integrity_check and PRAGMA foreign_key_check."""
    _require_file(path)

    def _work() -> Dict[str, Any]:
        conn, owned = _connection(path)
        try:
            messages = [str(r[0]) for r in conn.execute("PRAGMA integrity_check").fetchall()]
            fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
            foreign_keys = [
                {
                    "table": str(r[0]),
                    "rowid": r[1],
                    "parent": str(r[2]),
                    "fkid": r[3],
                }
                for r in fk_rows
            ]
            integrity_ok = bool(messages) and all(m == "ok" for m in messages)
            return {
                "ok": integrity_ok and not foreign_keys,
                "integrity": "ok" if integrity_ok else "; ".join(messages)[:500],
                "messages": messages,
                "foreign_key_errors": foreign_keys,
                "foreign_key_count": len(foreign_keys),
                "checked_at": _now_utc().isoformat(),
            }
        finally:
            if owned:
                conn.close()

    try:
        return _retry(_work, retries=retries, sleep_fn=sleep_fn)
    except FileNotFoundError:
        raise
    except DatabaseBusyError as exc:
        return {"ok": False, "integrity": "locked", "messages": [], "foreign_key_errors": [],
                "foreign_key_count": 0, "error": str(exc)}
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "integrity": "error", "messages": [], "foreign_key_errors": [],
                "foreign_key_count": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Size report
# ---------------------------------------------------------------------------
def _dbstat_sizes(conn: sqlite3.Connection) -> Dict[str, int]:
    try:
        rows = conn.execute(
            "SELECT name, SUM(pgsize) FROM dbstat GROUP BY name"
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    return {str(name): int(size or 0) for name, size in rows}


def table_sizes(path: DbPath, retries: int = LOCK_RETRIES,
                sleep_fn: Callable[[float], None] = time.sleep) -> List[Dict[str, Any]]:
    """Rows and on-disk bytes for each user table (bytes are None if dbstat is missing)."""
    _require_file(path)

    def _work() -> List[Dict[str, Any]]:
        conn, owned = _connection(path)
        try:
            sizes = _dbstat_sizes(conn)
            out: List[Dict[str, Any]] = []
            for table in _table_names(conn):
                rows = int(conn.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0])
                out.append({
                    "table": table,
                    "rows": rows,
                    "bytes": sizes.get(table),
                })
            out.sort(key=lambda item: (item["bytes"] is None, -(item["bytes"] or 0), -item["rows"], item["table"]))
            return out
        finally:
            if owned:
                conn.close()

    return _retry(_work, retries=retries, sleep_fn=sleep_fn)


def database_stats(path: DbPath, retries: int = LOCK_RETRIES,
                   sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """File size, page/free-page counts and per-table size report."""
    _require_file(path)

    def _work() -> Dict[str, Any]:
        conn, owned = _connection(path)
        try:
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
            freelist = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            tables = []
            sizes = _dbstat_sizes(conn)
            for table in _table_names(conn):
                rows = int(conn.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0])
                tables.append({"table": table, "rows": rows, "bytes": sizes.get(table)})
            tables.sort(key=lambda item: (item["bytes"] is None, -(item["bytes"] or 0), -item["rows"], item["table"]))
            file_bytes = Path(path).stat().st_size
            free_ratio = (freelist / page_count) if page_count else 0.0
            return {
                "file_bytes": file_bytes,
                "page_size": page_size,
                "page_count": page_count,
                "freelist_count": freelist,
                "free_ratio": round(free_ratio, 4),
                "table_count": len(tables),
                "tables": tables,
                "total_table_bytes": sum(int(t["bytes"] or 0) for t in tables),
            }
        finally:
            if owned:
                conn.close()

    return _retry(_work, retries=retries, sleep_fn=sleep_fn)


def should_vacuum(stats_or_path: Union[Dict[str, Any], DbPath],
                  threshold: float = VACUUM_FREE_RATIO) -> bool:
    """True when free pages are strictly above the threshold (default 20%)."""
    stats = stats_or_path if isinstance(stats_or_path, dict) else database_stats(stats_or_path)
    return float(stats.get("free_ratio", 0.0)) > float(threshold)


def vacuum_if_needed(path: DbPath, threshold: float = VACUUM_FREE_RATIO, dry_run: bool = False,
                     retries: int = LOCK_RETRIES,
                     sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """VACUUM only when free pages exceed the threshold; returns before/after stats."""
    _require_file(path)
    before = database_stats(path, retries=retries, sleep_fn=sleep_fn)
    needed = float(before.get("free_ratio", 0.0)) > float(threshold)
    result: Dict[str, Any] = {
        "needed": needed,
        "threshold": float(threshold),
        "before": before,
        "dry_run": bool(dry_run),
        "vacuumed": False,
    }
    if not needed:
        result["reason"] = "free pages are at or below the threshold"
        return result
    if dry_run:
        result["reason"] = "VACUUM would run"
        return result

    def _work() -> None:
        conn, owned = _connection(path)
        try:
            conn.execute("VACUUM")
        finally:
            if owned:
                conn.close()

    _retry(_work, retries=retries, sleep_fn=sleep_fn)
    after = database_stats(path, retries=retries, sleep_fn=sleep_fn)
    result.update({"vacuumed": True, "after": after, "reason": "free pages above threshold"})
    return result


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------
def prune(path: DbPath, now: Optional[_dt.datetime] = None, dry_run: bool = False,
          log_days: int = LOG_RETENTION_DAYS, task_days: int = TASK_RETENTION_DAYS,
          log_rules: Optional[Sequence[Tuple[str, str]]] = None,
          retries: int = LOCK_RETRIES,
          sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Delete old log rows and old finished tasks.  Approvals are kept forever."""
    _require_file(path)
    if log_days < 0 or task_days < 0:
        raise ValueError("retention days must be >= 0")
    stamp = _naive_utc(now)
    log_cutoff = (stamp - _dt.timedelta(days=int(log_days))).isoformat(timespec="seconds")
    task_cutoff = (stamp - _dt.timedelta(days=int(task_days))).isoformat(timespec="seconds")
    rules = list(DEFAULT_LOG_RULES if log_rules is None else log_rules)

    def _work() -> Dict[str, Any]:
        conn, owned = _connection(path)
        deleted: Dict[str, int] = {}
        skipped: Dict[str, str] = {}
        try:
            for table, column in rules:
                if table.lower() in PROTECTED_TABLES:
                    skipped[table] = "kept forever"
                    continue
                if not _has_table(conn, table):
                    skipped[table] = "table not found"
                    continue
                columns = _table_columns(conn, table)
                if column not in columns:
                    skipped[table] = f"column {column} not found"
                    continue
                where = f"{_quote(column)} IS NOT NULL AND datetime({_quote(column)}) < datetime(?)"
                count = int(conn.execute(
                    f"SELECT COUNT(*) FROM {_quote(table)} WHERE {where}", (log_cutoff,)
                ).fetchone()[0])
                deleted[table] = count
                if not dry_run and count:
                    conn.execute(f"DELETE FROM {_quote(table)} WHERE {where}", (log_cutoff,))

            task_count = 0
            if _has_table(conn, "tasks"):
                columns = _table_columns(conn, "tasks")
                if "finished_ts" in columns and "status" in columns:
                    placeholders = ", ".join("?" for _ in FINISHED_TASK_STATUSES)
                    where = (
                        "finished_ts IS NOT NULL AND datetime(finished_ts) < datetime(?) "
                        f"AND lower(COALESCE(status, '')) IN ({placeholders})"
                    )
                    params = (task_cutoff, *FINISHED_TASK_STATUSES)
                    task_count = int(conn.execute(
                        f"SELECT COUNT(*) FROM tasks WHERE {where}", params
                    ).fetchone()[0])
                    if not dry_run and task_count:
                        conn.execute(f"DELETE FROM tasks WHERE {where}", params)
                else:
                    skipped["tasks"] = "finished_ts/status column not found"
            else:
                skipped["tasks"] = "table not found"

            if not dry_run:
                conn.commit()
            total = sum(deleted.values()) + task_count
            return {
                "dry_run": bool(dry_run),
                "log_days": int(log_days),
                "task_days": int(task_days),
                "log_cutoff": log_cutoff,
                "task_cutoff": task_cutoff,
                "logs": deleted,
                "tasks": task_count,
                "total": total,
                "skipped": skipped,
            }
        finally:
            if owned:
                conn.close()

    try:
        return _retry(_work, retries=retries, sleep_fn=sleep_fn)
    except DatabaseBusyError as exc:
        return {"error": str(exc), "dry_run": bool(dry_run)}


# ---------------------------------------------------------------------------
# Index advice
# ---------------------------------------------------------------------------
INDEX_SPECS: Tuple[Dict[str, Any], ...] = (
    {"table": "tasks", "columns": ["status", "priority", "id"], "reason": "claim_next queue lookup"},
    {"table": "tasks", "columns": ["finished_ts"], "reason": "old finished task pruning"},
    {"table": "approvals", "columns": ["status", "id"], "reason": "pending approval list"},
    {"table": "provider_log", "columns": ["day"], "reason": "daily provider report"},
    {"table": "product_builds", "columns": ["status"], "reason": "build status lookups"},
    {"table": "product_deliveries", "columns": ["status"], "reason": "delivery status lookups"},
)

SLOW_QUERY_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("tasks claim", "SELECT id FROM tasks WHERE status = 'queued' ORDER BY priority, id LIMIT 1"),
    ("approvals pending", "SELECT id FROM approvals WHERE status = 'pending' ORDER BY id"),
)

SLOW_TABLE_ROWS = 200


def _indexed_columns(conn: sqlite3.Connection, table: str) -> List[List[str]]:
    out: List[List[str]] = []
    for idx in conn.execute(f"PRAGMA index_list({_quote(table)})").fetchall():
        name = idx[1]
        cols = [str(r[2]) for r in conn.execute(f"PRAGMA index_info({_quote(name)})").fetchall()]
        if cols:
            out.append(cols)
    return out


def index_advice(path: DbPath, retries: int = LOCK_RETRIES,
                 sleep_fn: Callable[[float], None] = time.sleep) -> List[Dict[str, Any]]:
    """Suggest indexes for known hot queries and for slow table scans.  Advisory only."""
    _require_file(path)

    def _work() -> List[Dict[str, Any]]:
        conn, owned = _connection(path)
        try:
            advice: List[Dict[str, Any]] = []
            seen: set = set()
            for spec in INDEX_SPECS:
                table = spec["table"]
                if not _has_table(conn, table):
                    continue
                columns = list(spec["columns"])
                if columns in _indexed_columns(conn, table):
                    continue
                key = (table, tuple(columns))
                if key in seen:
                    continue
                seen.add(key)
                index_name = f"idx_{table}_{'_'.join(columns)}"
                advice.append({
                    "table": table,
                    "columns": columns,
                    "index_name": index_name,
                    "sql": f"CREATE INDEX IF NOT EXISTS {index_name} ON {_quote(table)} ({', '.join(_quote(c) for c in columns)})",
                    "reason": spec["reason"],
                    "source": "known-hot-query",
                })

            for label, sql in SLOW_QUERY_PATTERNS:
                try:
                    plan = [str(r[3]) for r in conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()]
                except sqlite3.OperationalError:
                    continue
                if not any("SCAN" in step.upper() for step in plan):
                    continue
                table = ""
                for token in ("tasks", "approvals"):
                    if token in sql:
                        table = token
                        break
                if not table or not _has_table(conn, table):
                    continue
                rows = int(conn.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0])
                if rows < SLOW_TABLE_ROWS:
                    continue
                key = (table, ("scan",))
                if key in seen:
                    continue
                seen.add(key)
                advice.append({
                    "table": table,
                    "columns": [],
                    "index_name": "",
                    "sql": "",
                    "reason": f"slow full scan for {label} on {rows} rows",
                    "source": "query-plan",
                })
            return advice
        finally:
            if owned:
                conn.close()

    return _retry(_work, retries=retries, sleep_fn=sleep_fn)


# ---------------------------------------------------------------------------
# Safe check copy
# ---------------------------------------------------------------------------
def _cleanup_dir(folder: Path) -> List[str]:
    leftovers: List[str] = []
    try:
        for child in folder.iterdir():
            try:
                if child.is_dir():
                    for sub in child.iterdir():
                        sub.unlink(missing_ok=True)
                    child.rmdir()
                else:
                    child.unlink(missing_ok=True)
            except OSError:
                leftovers.append(child.name)
    except FileNotFoundError:
        pass
    try:
        folder.rmdir()
    except OSError:
        if folder.exists():
            leftovers.extend(p.name for p in folder.iterdir())
    return leftovers


def verify_copy(source_path: DbPath, copy_path: DbPath) -> Dict[str, Any]:
    """Open a copy, run integrity_check and compare every table's row count."""
    _require_file(copy_path)
    src, src_owned = _connection(source_path)
    dst, dst_owned = _connection(copy_path)
    try:
        integrity = [str(r[0]) for r in dst.execute("PRAGMA integrity_check").fetchall()]
        src_tables = _table_names(src)
        dst_tables = _table_names(dst)
        counts: Dict[str, Tuple[int, int]] = {}
        mismatches: List[str] = []
        for table in sorted(set(src_tables) | set(dst_tables)):
            src_rows = int(src.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0]) if table in src_tables else -1
            dst_rows = int(dst.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0]) if table in dst_tables else -1
            counts[table] = (src_rows, dst_rows)
            if src_rows != dst_rows:
                mismatches.append(table)
        ok = bool(integrity) and all(m == "ok" for m in integrity) and not mismatches
        return {
            "ok": ok,
            "integrity": "ok" if all(m == "ok" for m in integrity) else "; ".join(integrity)[:500],
            "tables": dst_tables,
            "row_counts": counts,
            "mismatches": mismatches,
        }
    finally:
        if src_owned:
            src.close()
        if dst_owned:
            dst.close()


def safe_check_copy(path: DbPath, temp_dir: Optional[Union[str, os.PathLike]] = None,
                    retries: int = LOCK_RETRIES,
                    sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Backup into the system temp folder, verify it, then delete it.

    The repo folder is never used for the copy.  Returns a report with no path.
    """
    _require_file(path)
    base = Path(temp_dir) if temp_dir is not None else Path(tempfile.gettempdir())
    base = base.resolve()
    try:
        base.relative_to(BASE_DIR.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("temp_dir must be outside the repo folder")
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="dbcare-check-", dir=str(base)))
    target = work / "check-copy.db"
    report: Dict[str, Any] = {"ok": False, "deleted": False, "leftovers": []}
    src: Optional[sqlite3.Connection] = None
    dst: Optional[sqlite3.Connection] = None
    try:
        def _backup() -> None:
            nonlocal src, dst
            if src is not None:
                src.close()
            if dst is not None:
                dst.close()
            target.unlink(missing_ok=True)
            src = sqlite3.connect(str(path), timeout=5.0)
            src.row_factory = sqlite3.Row
            dst = sqlite3.connect(str(target))
            src.backup(dst)
            dst.close()
            dst = None
            src.close()
            src = None

        _retry(_backup, retries=retries, sleep_fn=sleep_fn)
        report.update(verify_copy(path, target))
        report["bytes"] = target.stat().st_size
        report["temp_root"] = "system temp folder"
    except DatabaseBusyError as exc:
        report["error"] = str(exc)
    except sqlite3.DatabaseError as exc:
        report["error"] = str(exc)
    finally:
        if src is not None:
            src.close()
        if dst is not None:
            dst.close()
        report["leftovers"] = _cleanup_dir(work)
        report["deleted"] = not work.exists() and not report["leftovers"]
    return report


# ---------------------------------------------------------------------------
# Report and cleanup
# ---------------------------------------------------------------------------
def build_report(path: Optional[DbPath] = None, now: Optional[_dt.datetime] = None,
                 retries: int = LOCK_RETRIES,
                 sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Collect integrity, schema, size, prune plan, vacuum need and index advice."""
    db_path: DbPath = path if path is not None else DB_PATH
    report: Dict[str, Any] = {
        "database": "present" if not isinstance(db_path, sqlite3.Connection) and Path(db_path).is_file() else "missing",
        "generated_at": _now_utc(now).isoformat(),
    }
    _require_file(db_path)
    integrity = integrity_check(db_path, retries=retries, sleep_fn=sleep_fn)
    report["integrity"] = integrity
    try:
        stats = database_stats(db_path, retries=retries, sleep_fn=sleep_fn)
        report["stats"] = stats
    except Exception as exc:  # noqa: BLE001 - report, never crash the owner command
        report["stats"] = {"error": str(exc)}
    try:
        version = current_version(db_path, retries=retries, sleep_fn=sleep_fn)
        pending = pending_migrations(applied_version=version)
        report["schema"] = {
            "version": version,
            "pending": [{"version": m.version, "name": m.name} for m in pending],
            "pending_count": len(pending),
            "registered": len(registered_migrations()),
        }
    except Exception as exc:  # noqa: BLE001
        report["schema"] = {"error": str(exc), "version": 0, "pending_count": 0}
    try:
        plan = prune(db_path, now=now, dry_run=True, retries=retries, sleep_fn=sleep_fn)
        report["prune_plan"] = {
            "logs": plan.get("logs", {}),
            "tasks": plan.get("tasks", 0),
            "total": plan.get("total", 0),
            "log_days": plan.get("log_days"),
            "task_days": plan.get("task_days"),
            "skipped": plan.get("skipped", {}),
        }
    except Exception as exc:  # noqa: BLE001
        report["prune_plan"] = {"error": str(exc), "total": 0}
    try:
        stats_for_vacuum = report.get("stats") or {}
        report["vacuum"] = {
            "needed": should_vacuum(stats_for_vacuum) if "free_ratio" in stats_for_vacuum else False,
            "threshold": VACUUM_FREE_RATIO,
        }
    except Exception as exc:  # noqa: BLE001
        report["vacuum"] = {"needed": False, "error": str(exc)}
    try:
        report["index_advice"] = index_advice(db_path, retries=retries, sleep_fn=sleep_fn)
    except Exception as exc:  # noqa: BLE001
        report["index_advice"] = []
        report["index_advice_error"] = str(exc)
    report["ok"] = bool(integrity.get("ok"))
    return report


def run_cleanup(path: Optional[DbPath] = None, now: Optional[_dt.datetime] = None,
                vacuum_threshold: float = VACUUM_FREE_RATIO,
                retries: int = LOCK_RETRIES,
                sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Integrity gate, then prune and VACUUM when needed."""
    db_path: DbPath = path if path is not None else DB_PATH
    _require_file(db_path)
    integrity = integrity_check(db_path, retries=retries, sleep_fn=sleep_fn)
    result: Dict[str, Any] = {"ok": False, "integrity": integrity}
    if not integrity.get("ok"):
        result["stopped"] = "integrity check failed; nothing was changed"
        return result
    before = database_stats(db_path, retries=retries, sleep_fn=sleep_fn)
    result["before"] = before
    result["pruned"] = prune(db_path, now=now, dry_run=False, retries=retries, sleep_fn=sleep_fn)
    result["vacuum"] = vacuum_if_needed(
        db_path, threshold=vacuum_threshold, retries=retries, sleep_fn=sleep_fn
    )
    result["after"] = database_stats(db_path, retries=retries, sleep_fn=sleep_fn)
    result["ok"] = "error" not in result["pruned"]
    return result


# ---------------------------------------------------------------------------
# Tenglish formatting
# ---------------------------------------------------------------------------
def _human_bytes(size: Optional[int]) -> str:
    if size is None:
        return "unknown"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} TB"


def format_report(report: Dict[str, Any]) -> str:
    """Owner-facing Tenglish card; never includes currency or secrets."""
    integrity = report.get("integrity") or {}
    stats = report.get("stats") or {}
    schema = report.get("schema") or {}
    plan = report.get("prune_plan") or {}
    vacuum = report.get("vacuum") or {}
    advice = report.get("index_advice") or []
    status = "✅ Healthy" if report.get("ok") else "❌ Problem undi"
    lines = [
        "🩺 DB Care report",
        f"Status: {status}",
        f"Integrity: {integrity.get('integrity', integrity.get('error', 'unknown'))}",
        f"Foreign key errors: {integrity.get('foreign_key_count', 0)}",
    ]
    if integrity.get("error"):
        lines.append(f"Note: {integrity['error']}")
    if "file_bytes" in stats:
        lines.append(
            f"Size: {_human_bytes(stats.get('file_bytes'))} | "
            f"pages {stats.get('page_count', 0)} | free {stats.get('free_ratio', 0.0):.1%}"
        )
        lines.append(f"Tables: {stats.get('table_count', 0)}")
        top = [t for t in stats.get("tables", []) if t.get("rows") or t.get("bytes")]
        for table in top[:5]:
            lines.append(f"  • {table['table']}: {table['rows']} rows, {_human_bytes(table.get('bytes'))}")
    elif stats.get("error"):
        lines.append(f"Size report: {stats['error']}")
    lines.append(
        f"Schema: v{schema.get('version', 0)} | pending {schema.get('pending_count', 0)}"
    )
    if plan.get("total"):
        lines.append(
            f"Clean plan: logs {sum(plan.get('logs', {}).values())}, "
            f"old tasks {plan.get('tasks', 0)}"
        )
    else:
        lines.append("Clean plan: emi ledu (already clean)")
    lines.append(f"VACUUM: {'needed' if vacuum.get('needed') else 'not needed'}")
    lines.append(f"Index advice: {len(advice)} item(s)")
    lines.append("Buttons: [Clean now] [Check copy]")
    text = "\n".join(lines)
    return text[:3900]


def format_clean_result(result: Dict[str, Any]) -> str:
    if not result.get("ok"):
        return "❌ Clean cheyaledu: " + str(result.get("stopped") or result.get("error") or "integrity problem")
    pruned = result.get("pruned") or {}
    vacuum = result.get("vacuum") or {}
    before = result.get("before") or {}
    after = result.get("after") or {}
    lines = [
        "🧹 Clean done",
        f"Logs removed: {sum((pruned.get('logs') or {}).values())}",
        f"Old tasks removed: {pruned.get('tasks', 0)}",
        f"VACUUM: {'done' if vacuum.get('vacuumed') else 'not needed'}",
        f"Size: {_human_bytes(before.get('file_bytes'))} → {_human_bytes(after.get('file_bytes'))}",
    ]
    return "\n".join(lines)


def format_copy_result(result: Dict[str, Any]) -> str:
    if not result.get("ok"):
        return "❌ Check copy fail: " + str(result.get("error") or "row counts match avvaledu")
    return (
        "✅ Check copy OK\n"
        f"Integrity: {result.get('integrity')}\n"
        f"Tables checked: {len(result.get('tables') or [])}\n"
        f"Size: {_human_bytes(result.get('bytes'))}\n"
        "Copy verify chesi delete chesam (repo lo file ledu)."
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Database health and safe cleanup")
    parser.add_argument("--db", default=DB_PATH, help="database path")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--report", action="store_true", help="show the health report")
    group.add_argument("--clean", action="store_true", help="prune and VACUUM if needed")
    group.add_argument("--check-copy", action="store_true", help="make, verify and delete a temp copy")
    args = parser.parse_args(argv)
    if args.clean:
        print(format_clean_result(run_cleanup(args.db)))
    elif args.check_copy:
        print(format_copy_result(safe_check_copy(args.db)))
    else:
        print(format_report(build_report(args.db)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
