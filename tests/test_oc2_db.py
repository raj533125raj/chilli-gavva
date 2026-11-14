"""Offline tests for db_maintenance.py and tg_dbcare.py (OC2-1)."""

from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db_maintenance
import tg_dbcare


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)


def make_db(path, rows=False):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, title TEXT,
            payload TEXT, status TEXT DEFAULT 'pending', review TEXT, decided_ts TEXT);
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, payload TEXT,
            status TEXT DEFAULT 'queued', attempts INTEGER DEFAULT 0, agent TEXT,
            result TEXT, error TEXT, priority INTEGER DEFAULT 5, finished_ts TEXT);
        CREATE INDEX idx_tasks_status ON tasks(status, priority, id);
        CREATE TABLE agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, message TEXT);
        CREATE TABLE provider_log (day TEXT, provider TEXT, ok INTEGER DEFAULT 0, fail INTEGER DEFAULT 0, PRIMARY KEY(day, provider));
        CREATE TABLE usage_log (day TEXT PRIMARY KEY, llm_calls INTEGER DEFAULT 0);
        CREATE TABLE button_log (ts TEXT);
        CREATE TABLE maintenance_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, summary TEXT);
        CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));
        """
    )
    if rows:
        conn.executemany("INSERT INTO parent (id, name) VALUES (?, ?)", [(1, "a"), (2, "b")])
        conn.executemany("INSERT INTO child (id, parent_id) VALUES (?, ?)", [(1, 1), (2, 2)])
    conn.commit()
    conn.close()


def insert_logs(path, now=NOW, days=(10, 100), tables=("agent_logs", "provider_log", "usage_log", "button_log", "maintenance_log")):
    conn = sqlite3.connect(path)
    for offset, days_old in enumerate(days):
        stamp = (now - dt.timedelta(days=days_old)).isoformat()
        day = stamp.split("T")[0]
        if "agent_logs" in tables:
            conn.execute("INSERT INTO agent_logs (timestamp, message) VALUES (?, ?)", (stamp, f"log-{offset}"))
        if "provider_log" in tables:
            conn.execute("INSERT OR REPLACE INTO provider_log (day, provider, ok) VALUES (?, ?, ?)", (day, f"p{offset}", 1))
        if "usage_log" in tables:
            conn.execute("INSERT OR REPLACE INTO usage_log (day, llm_calls) VALUES (?, ?)", (day, 1))
        if "button_log" in tables:
            conn.execute("INSERT INTO button_log (ts) VALUES (?)", (stamp,))
        if "maintenance_log" in tables:
            conn.execute("INSERT INTO maintenance_log (ts, summary) VALUES (?, ?)", (stamp, "x"))
    conn.commit()
    conn.close()


def insert_task(path, status, finished_days, task_id=None):
    stamp = (NOW - dt.timedelta(days=finished_days)).isoformat()
    conn = sqlite3.connect(path)
    if task_id is None:
        conn.execute("INSERT INTO tasks (ts, status, finished_ts) VALUES (?, ?, ?)", (stamp, status, stamp))
    else:
        conn.execute("INSERT INTO tasks (id, ts, status, finished_ts) VALUES (?, ?, ?, ?)", (task_id, stamp, status, stamp))
    conn.commit()
    conn.close()


class DBTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="oc2-db-")
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "agent_data.db"
        make_db(self.db)

    def count(self, table):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        finally:
            conn.close()

    def rows(self, sql, params=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


class RetryTests(unittest.TestCase):
    def test_retry_returns_value(self):
        self.assertEqual(db_maintenance._retry(lambda: 7, sleep_fn=lambda _: None), 7)

    def test_retry_retries_locked_and_sleeps(self):
        calls = []
        def work():
            calls.append(1)
            if len(calls) < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"
        sleeps = []
        self.assertEqual(db_maintenance._retry(work, retries=5, base_delay=0.1, sleep_fn=sleeps.append), "ok")
        self.assertEqual(sleeps, [0.1, 0.2])

    def test_retry_stops_after_limit(self):
        def work():
            raise sqlite3.OperationalError("database is busy")
        with self.assertRaises(db_maintenance.DatabaseBusyError):
            db_maintenance._retry(work, retries=3, sleep_fn=lambda _: None)

    def test_retry_non_lock_error_raises_immediately(self):
        with self.assertRaises(sqlite3.OperationalError):
            db_maintenance._retry(lambda: (_ for _ in ()).throw(sqlite3.OperationalError("no such table")), sleep_fn=lambda _: None)

    def test_retry_one_attempt(self):
        with self.assertRaises(db_maintenance.DatabaseBusyError):
            db_maintenance._retry(lambda: (_ for _ in ()).throw(sqlite3.OperationalError("locked")), retries=1, sleep_fn=lambda _: None)

    def test_is_lock_error_locked(self):
        self.assertTrue(db_maintenance._is_lock_error(sqlite3.OperationalError("database is locked")))

    def test_is_lock_error_busy(self):
        self.assertTrue(db_maintenance._is_lock_error(sqlite3.OperationalError("database is busy")))

    def test_is_lock_error_other(self):
        self.assertFalse(db_maintenance._is_lock_error(sqlite3.OperationalError("syntax error")))

    def test_connection_sets_foreign_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.db"
            make_db(path)
            conn, owned = db_maintenance._connection(path)
            try:
                self.assertTrue(owned)
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            finally:
                conn.close()

    def test_connection_row_factory(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.db"
            make_db(path)
            conn, _ = db_maintenance._connection(path)
            try:
                row = conn.execute("SELECT 1 AS one").fetchone()
                self.assertEqual(row["one"], 1)
            finally:
                conn.close()

    def test_connection_reuses_connection(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        got, owned = db_maintenance._connection(conn)
        self.assertIs(got, conn)
        self.assertFalse(owned)

    def test_require_file_missing(self):
        with self.assertRaises(FileNotFoundError):
            db_maintenance._require_file(Path(tempfile.gettempdir()) / "no-such-oc2.db")

    def test_require_file_existing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.db"
            path.write_bytes(b"")
            db_maintenance._require_file(path)

    def test_quote_double_quote(self):
        self.assertEqual(db_maintenance._quote('a"b'), '"a""b"')

    def test_table_names_excludes_sqlite(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE t (a)")
        self.assertEqual(db_maintenance._table_names(conn), ["t"])

    def test_table_columns(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE t (a, b)")
        self.assertEqual(db_maintenance._table_columns(conn, "t"), ["a", "b"])

    def test_has_table(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE t (a)")
        self.assertTrue(db_maintenance._has_table(conn, "t"))
        self.assertFalse(db_maintenance._has_table(conn, "nope"))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        db_maintenance.clear_migrations()
        self.addCleanup(db_maintenance.clear_migrations)
        self.tmp = tempfile.TemporaryDirectory(prefix="oc2-mig-")
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "mig.db"
        make_db(self.db)

    def rows(self, sql, params=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_register_returns_migration(self):
        m = db_maintenance.register_migration(1, "one", lambda conn: None)
        self.assertEqual((m.version, m.name), (1, "one"))

    def test_register_sorted(self):
        db_maintenance.register_migration(2, "two", lambda conn: None)
        db_maintenance.register_migration(1, "one", lambda conn: None)
        self.assertEqual([m.version for m in db_maintenance.registered_migrations()], [1, 2])

    def test_register_rejects_zero(self):
        with self.assertRaises(ValueError):
            db_maintenance.register_migration(0, "bad", lambda conn: None)

    def test_register_rejects_negative(self):
        with self.assertRaises(ValueError):
            db_maintenance.register_migration(-1, "bad", lambda conn: None)

    def test_register_rejects_bool(self):
        with self.assertRaises(ValueError):
            db_maintenance.register_migration(True, "bad", lambda conn: None)

    def test_register_rejects_string_version(self):
        with self.assertRaises(ValueError):
            db_maintenance.register_migration("1", "bad", lambda conn: None)

    def test_register_rejects_empty_name(self):
        with self.assertRaises(ValueError):
            db_maintenance.register_migration(1, "", lambda conn: None)

    def test_register_rejects_blank_name(self):
        with self.assertRaises(ValueError):
            db_maintenance.register_migration(1, "   ", lambda conn: None)

    def test_register_rejects_non_callable(self):
        with self.assertRaises(ValueError):
            db_maintenance.register_migration(1, "x", None)

    def test_register_rejects_duplicate_version(self):
        db_maintenance.register_migration(1, "one", lambda conn: None)
        with self.assertRaises(ValueError):
            db_maintenance.register_migration(1, "other", lambda conn: None)

    def test_register_rejects_duplicate_name(self):
        db_maintenance.register_migration(1, "one", lambda conn: None)
        with self.assertRaises(ValueError):
            db_maintenance.register_migration(2, "one", lambda conn: None)

    def test_registered_returns_copy(self):
        db_maintenance.register_migration(1, "one", lambda conn: None)
        got = db_maintenance.registered_migrations()
        got.clear()
        self.assertEqual(len(db_maintenance.registered_migrations()), 1)

    def test_clear_migrations(self):
        db_maintenance.register_migration(1, "one", lambda conn: None)
        db_maintenance.clear_migrations()
        self.assertEqual(db_maintenance.registered_migrations(), [])

    def test_ensure_schema_table(self):
        conn = sqlite3.connect(self.db)
        try:
            db_maintenance.ensure_schema_table(conn)
            self.assertTrue(db_maintenance._has_table(conn, db_maintenance.SCHEMA_TABLE))
        finally:
            conn.close()

    def test_ensure_schema_does_not_touch_existing_tables(self):
        conn = sqlite3.connect(self.db)
        before = conn.execute("PRAGMA table_info(approvals)").fetchall()
        db_maintenance.ensure_schema_table(conn)
        after = conn.execute("PRAGMA table_info(approvals)").fetchall()
        conn.close()
        self.assertEqual(before, after)

    def test_current_version_zero(self):
        self.assertEqual(db_maintenance.current_version(self.db), 0)

    def test_current_version_after_apply(self):
        db_maintenance.register_migration(1, "one", lambda conn: conn.execute("CREATE TABLE extra (x)"))
        db_maintenance.apply_migrations(self.db, now=NOW)
        self.assertEqual(db_maintenance.current_version(self.db), 1)

    def test_apply_records_migration(self):
        db_maintenance.register_migration(1, "one", lambda conn: None)
        result = db_maintenance.apply_migrations(self.db, now=NOW)
        self.assertEqual(result["applied"], [{"version": 1, "name": "one"}])
        rows = self.rows("SELECT version, name, applied_ts FROM schema_migrations")
        self.assertEqual(rows[0][0], 1)
        self.assertEqual(rows[0][1], "one")
        self.assertTrue(rows[0][2].startswith("2026-11-14"))

    def test_apply_sets_user_version(self):
        db_maintenance.register_migration(3, "three", lambda conn: None)
        db_maintenance.apply_migrations(self.db)
        self.assertEqual(self.rows("PRAGMA user_version")[0][0], 3)

    def test_apply_is_idempotent(self):
        db_maintenance.register_migration(1, "one", lambda conn: conn.execute("CREATE TABLE extra (x)"))
        db_maintenance.apply_migrations(self.db)
        second = db_maintenance.apply_migrations(self.db)
        self.assertEqual(second["applied"], [])

    def test_apply_multiple_in_order(self):
        order = []
        db_maintenance.register_migration(1, "one", lambda conn: order.append(1))
        db_maintenance.register_migration(2, "two", lambda conn: order.append(2))
        db_maintenance.apply_migrations(self.db)
        self.assertEqual(order, [1, 2])

    def test_apply_only_newer(self):
        db_maintenance.register_migration(1, "one", lambda conn: None)
        db_maintenance.apply_migrations(self.db)
        calls = []
        db_maintenance.register_migration(2, "two", lambda conn: calls.append(2))
        result = db_maintenance.apply_migrations(self.db)
        self.assertEqual(result["applied"], [{"version": 2, "name": "two"}])
        self.assertEqual(calls, [2])

    def test_apply_failure_rolls_back(self):
        def bad(conn):
            conn.execute("CREATE TABLE rolled_back (x)")
            raise RuntimeError("boom")
        db_maintenance.register_migration(1, "bad", bad)
        with self.assertRaises(db_maintenance.MigrationError):
            db_maintenance.apply_migrations(self.db)
        self.assertEqual(db_maintenance.current_version(self.db), 0)
        self.assertEqual(self.rows("SELECT name FROM sqlite_master WHERE name='rolled_back'"), [])

    def test_apply_failure_stops_later(self):
        db_maintenance.register_migration(1, "one", lambda conn: None)
        db_maintenance.register_migration(2, "bad", lambda conn: (_ for _ in ()).throw(RuntimeError("x")))
        with self.assertRaises(db_maintenance.MigrationError):
            db_maintenance.apply_migrations(self.db)
        self.assertEqual(db_maintenance.current_version(self.db), 1)

    def test_apply_failure_message_has_version(self):
        db_maintenance.register_migration(2, "bad", lambda conn: (_ for _ in ()).throw(RuntimeError("x")))
        with self.assertRaisesRegex(db_maintenance.MigrationError, "2 .*bad"):
            db_maintenance.apply_migrations(self.db)

    def test_apply_accepts_connection(self):
        db_maintenance.register_migration(1, "one", lambda conn: conn.execute("CREATE TABLE extra (x)"))
        conn = sqlite3.connect(self.db)
        try:
            db_maintenance.apply_migrations(conn)
            self.assertTrue(db_maintenance._has_table(conn, "extra"))
        finally:
            conn.close()

    def test_current_version_accepts_connection(self):
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(db_maintenance.current_version(conn), 0)
        finally:
            conn.close()

    def test_pending_migrations_filters(self):
        db_maintenance.register_migration(1, "one", lambda conn: None)
        db_maintenance.register_migration(2, "two", lambda conn: None)
        pending = db_maintenance.pending_migrations(applied_version=1)
        self.assertEqual([m.version for m in pending], [2])

    def test_pending_migrations_explicit_list(self):
        m1 = db_maintenance.Migration(1, "one", lambda conn: None)
        m2 = db_maintenance.Migration(2, "two", lambda conn: None)
        self.assertEqual([m.version for m in db_maintenance.pending_migrations([m1, m2], 0)], [1, 2])

    def test_pending_migrations_default_zero(self):
        db_maintenance.register_migration(1, "one", lambda conn: None)
        self.assertEqual(len(db_maintenance.pending_migrations()), 1)

    def test_apply_migration_creates_only_registry_and_new_table(self):
        before = {r[0] for r in self.rows("SELECT name FROM sqlite_master WHERE type='table'")}
        db_maintenance.register_migration(1, "one", lambda conn: conn.execute("CREATE TABLE extra (x)"))
        db_maintenance.apply_migrations(self.db)
        after = {r[0] for r in self.rows("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(after - before, {"schema_migrations", "extra"})


class IntegrityTests(DBTestCase):
    def test_healthy_database(self):
        result = db_maintenance.integrity_check(self.db)
        self.assertTrue(result["ok"])
        self.assertEqual(result["integrity"], "ok")

    def test_foreign_key_clean(self):
        result = db_maintenance.integrity_check(self.db)
        self.assertEqual(result["foreign_key_count"], 0)

    def test_foreign_key_violation(self):
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("INSERT INTO child (id, parent_id) VALUES (9, 999)")
        conn.commit()
        conn.close()
        result = db_maintenance.integrity_check(self.db)
        self.assertFalse(result["ok"])
        self.assertEqual(result["foreign_key_count"], 1)
        self.assertEqual(result["foreign_key_errors"][0]["table"], "child")

    def test_foreign_key_violation_parent_name(self):
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("INSERT INTO child (id, parent_id) VALUES (9, 999)")
        conn.commit()
        conn.close()
        self.assertEqual(db_maintenance.integrity_check(self.db)["foreign_key_errors"][0]["parent"], "parent")

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            db_maintenance.integrity_check(Path(self.tmp.name) / "nope.db")

    def test_locked_returns_error(self):
        with mock.patch.object(db_maintenance, "_retry", side_effect=db_maintenance.DatabaseBusyError("locked")):
            result = db_maintenance.integrity_check(self.db)
        self.assertFalse(result["ok"])
        self.assertIn("locked", result["error"])

    def test_sqlite_error_returns_error(self):
        with mock.patch.object(db_maintenance, "_connection", side_effect=sqlite3.DatabaseError("broken")):
            result = db_maintenance.integrity_check(self.db)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "broken")

    def test_checked_at_is_utc(self):
        result = db_maintenance.integrity_check(self.db)
        self.assertTrue(result["checked_at"].endswith("+00:00"))

    def test_result_keys(self):
        result = db_maintenance.integrity_check(self.db)
        for key in ("ok", "integrity", "messages", "foreign_key_errors", "foreign_key_count", "checked_at"):
            self.assertIn(key, result)

    def test_messages_ok(self):
        self.assertEqual(db_maintenance.integrity_check(self.db)["messages"], ["ok"])

    def test_empty_database(self):
        empty = Path(self.tmp.name) / "empty.db"
        sqlite3.connect(empty).close()
        self.assertTrue(db_maintenance.integrity_check(empty)["ok"])

    def test_broken_schema_database(self):
        broken = Path(self.tmp.name) / "broken.db"
        broken.write_bytes(b"not a database")
        result = db_maintenance.integrity_check(broken)
        self.assertFalse(result["ok"])


class SizeTests(DBTestCase):
    def test_table_sizes_rows(self):
        insert_logs(self.db)
        sizes = {t["table"]: t for t in db_maintenance.table_sizes(self.db)}
        self.assertEqual(sizes["agent_logs"]["rows"], 2)

    def test_table_sizes_bytes_nonnegative(self):
        insert_logs(self.db)
        for item in db_maintenance.table_sizes(self.db):
            self.assertTrue(item["bytes"] is None or item["bytes"] >= 0)

    def test_table_sizes_excludes_sqlite(self):
        names = {t["table"] for t in db_maintenance.table_sizes(self.db)}
        self.assertFalse(any(n.startswith("sqlite_") for n in names))

    def test_table_sizes_sorted(self):
        insert_logs(self.db)
        items = db_maintenance.table_sizes(self.db)
        keys = [(i["bytes"] is None, -(i["bytes"] or 0), -i["rows"], i["table"]) for i in items]
        self.assertEqual(keys, sorted(keys))

    def test_table_sizes_fallback_without_dbstat(self):
        with mock.patch.object(db_maintenance, "_dbstat_sizes", return_value={}):
            items = db_maintenance.table_sizes(self.db)
        self.assertTrue(all(i["bytes"] is None for i in items))

    def test_table_sizes_empty_db(self):
        empty = Path(self.tmp.name) / "empty.db"
        sqlite3.connect(empty).close()
        self.assertEqual(db_maintenance.table_sizes(empty), [])

    def test_table_sizes_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            db_maintenance.table_sizes(Path(self.tmp.name) / "nope.db")

    def test_database_stats_file_size(self):
        stats = db_maintenance.database_stats(self.db)
        self.assertEqual(stats["file_bytes"], self.db.stat().st_size)

    def test_database_stats_page_size(self):
        self.assertGreater(db_maintenance.database_stats(self.db)["page_size"], 0)

    def test_database_stats_page_count(self):
        self.assertGreater(db_maintenance.database_stats(self.db)["page_count"], 0)

    def test_database_stats_free_ratio_range(self):
        stats = db_maintenance.database_stats(self.db)
        self.assertGreaterEqual(stats["free_ratio"], 0.0)
        self.assertLessEqual(stats["free_ratio"], 1.0)

    def test_database_stats_table_count(self):
        stats = db_maintenance.database_stats(self.db)
        self.assertEqual(stats["table_count"], len(stats["tables"]))

    def test_database_stats_total_bytes(self):
        stats = db_maintenance.database_stats(self.db)
        expected = sum(int(t["bytes"] or 0) for t in stats["tables"])
        self.assertEqual(stats["total_table_bytes"], expected)

    def test_database_stats_tables_have_rows(self):
        insert_logs(self.db)
        stats = db_maintenance.database_stats(self.db)
        self.assertTrue(any(t["rows"] for t in stats["tables"]))

    def test_database_stats_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            db_maintenance.database_stats(Path(self.tmp.name) / "nope.db")

    def test_should_vacuum_dict_true(self):
        self.assertTrue(db_maintenance.should_vacuum({"free_ratio": 0.25}))

    def test_should_vacuum_dict_false(self):
        self.assertFalse(db_maintenance.should_vacuum({"free_ratio": 0.19}))

    def test_should_vacuum_boundary_false(self):
        self.assertFalse(db_maintenance.should_vacuum({"free_ratio": 0.20}))

    def test_should_vacuum_path(self):
        self.assertFalse(db_maintenance.should_vacuum(self.db))

    def test_stats_after_many_rows(self):
        conn = sqlite3.connect(self.db)
        conn.executemany("INSERT INTO agent_logs (timestamp, message) VALUES (?, ?)",
                         [("2026-11-14T00:00:00+00:00", "x" * 100) for _ in range(500)])
        conn.commit()
        conn.close()
        self.assertGreater(db_maintenance.database_stats(self.db)["page_count"], 0)


class PruneTests(DBTestCase):
    def test_old_agent_logs_deleted(self):
        insert_logs(self.db)
        db_maintenance.prune(self.db, now=NOW)
        self.assertEqual(self.count("agent_logs"), 1)

    def test_recent_rows_kept(self):
        insert_logs(self.db)
        db_maintenance.prune(self.db, now=NOW)
        self.assertEqual(self.rows("SELECT message FROM agent_logs")[0][0], "log-0")

    def test_exact_cutoff_kept(self):
        insert_logs(self.db, days=(90,))
        db_maintenance.prune(self.db, now=NOW)
        self.assertEqual(self.count("agent_logs"), 1)

    def test_dry_run_deletes_nothing(self):
        insert_logs(self.db)
        result = db_maintenance.prune(self.db, now=NOW, dry_run=True)
        self.assertEqual(result["total"], 5)
        self.assertEqual(self.count("agent_logs"), 2)

    def test_counts_per_log_table(self):
        insert_logs(self.db)
        result = db_maintenance.prune(self.db, now=NOW, dry_run=True)
        self.assertEqual(result["logs"]["provider_log"], 1)
        self.assertEqual(result["logs"]["usage_log"], 1)

    def test_missing_tables_skipped(self):
        empty = Path(self.tmp.name) / "empty.db"
        sqlite3.connect(empty).close()
        result = db_maintenance.prune(empty, now=NOW)
        self.assertEqual(result["total"], 0)
        self.assertIn("agent_logs", result["skipped"])

    def test_missing_column_skipped(self):
        conn = sqlite3.connect(self.db)
        conn.execute("DROP TABLE agent_logs")
        conn.execute("CREATE TABLE agent_logs (id INTEGER)")
        conn.commit()
        conn.close()
        result = db_maintenance.prune(self.db, now=NOW)
        self.assertIn("timestamp", result["skipped"]["agent_logs"])

    def test_old_done_task_deleted(self):
        insert_task(self.db, "done", 70)
        db_maintenance.prune(self.db, now=NOW)
        self.assertEqual(self.count("tasks"), 0)

    def test_recent_done_task_kept(self):
        insert_task(self.db, "done", 10)
        db_maintenance.prune(self.db, now=NOW)
        self.assertEqual(self.count("tasks"), 1)

    def test_old_queued_task_kept(self):
        insert_task(self.db, "queued", 100)
        db_maintenance.prune(self.db, now=NOW)
        self.assertEqual(self.count("tasks"), 1)

    def test_old_running_task_kept(self):
        insert_task(self.db, "running", 100)
        db_maintenance.prune(self.db, now=NOW)
        self.assertEqual(self.count("tasks"), 1)

    def test_old_failed_task_deleted(self):
        insert_task(self.db, "failed", 100)
        db_maintenance.prune(self.db, now=NOW)
        self.assertEqual(self.count("tasks"), 0)

    def test_old_cancelled_task_deleted(self):
        insert_task(self.db, "cancelled", 100)
        db_maintenance.prune(self.db, now=NOW)
        self.assertEqual(self.count("tasks"), 0)

    def test_status_case_insensitive(self):
        insert_task(self.db, "DONE", 100)
        db_maintenance.prune(self.db, now=NOW)
        self.assertEqual(self.count("tasks"), 0)

    def test_null_finished_task_kept(self):
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO tasks (status, finished_ts) VALUES ('done', NULL)")
        conn.commit()
        conn.close()
        db_maintenance.prune(self.db, now=NOW)
        self.assertEqual(self.count("tasks"), 1)

    def test_approvals_kept_forever(self):
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO approvals (ts, status) VALUES (?, 'approved')", ((NOW - dt.timedelta(days=900)).isoformat(),))
        conn.commit()
        conn.close()
        db_maintenance.prune(self.db, now=NOW, log_rules=(("approvals", "ts"),))
        self.assertEqual(self.count("approvals"), 1)

    def test_approvals_rule_reports_skipped(self):
        result = db_maintenance.prune(self.db, now=NOW, log_rules=(("approvals", "ts"),))
        self.assertEqual(result["skipped"]["approvals"], "kept forever")

    def test_custom_log_days(self):
        insert_logs(self.db, days=(10, 40))
        result = db_maintenance.prune(self.db, now=NOW, log_days=30, dry_run=True)
        self.assertEqual(result["logs"]["agent_logs"], 1)

    def test_custom_task_days(self):
        insert_task(self.db, "done", 10)
        db_maintenance.prune(self.db, now=NOW, task_days=5)
        self.assertEqual(self.count("tasks"), 0)

    def test_negative_days_rejected(self):
        with self.assertRaises(ValueError):
            db_maintenance.prune(self.db, now=NOW, log_days=-1)

    def test_negative_task_days_rejected(self):
        with self.assertRaises(ValueError):
            db_maintenance.prune(self.db, now=NOW, task_days=-1)

    def test_now_naive(self):
        insert_logs(self.db)
        result = db_maintenance.prune(self.db, now=NOW.replace(tzinfo=None), dry_run=True)
        self.assertEqual(result["logs"]["agent_logs"], 1)

    def test_now_aware_other_zone(self):
        insert_logs(self.db)
        result = db_maintenance.prune(self.db, now=NOW.astimezone(dt.timezone(dt.timedelta(hours=5))), dry_run=True)
        self.assertEqual(result["logs"]["agent_logs"], 1)

    def test_result_cutoffs(self):
        result = db_maintenance.prune(self.db, now=NOW, dry_run=True)
        self.assertTrue(result["log_cutoff"].startswith("2026-08-16"))

    def test_dry_run_flag(self):
        self.assertTrue(db_maintenance.prune(self.db, now=NOW, dry_run=True)["dry_run"])

    def test_total_sum(self):
        insert_logs(self.db)
        insert_task(self.db, "done", 70)
        result = db_maintenance.prune(self.db, now=NOW, dry_run=True)
        self.assertEqual(result["total"], sum(result["logs"].values()) + result["tasks"])

    def test_empty_database_prune(self):
        empty = Path(self.tmp.name) / "empty.db"
        sqlite3.connect(empty).close()
        self.assertEqual(db_maintenance.prune(empty, now=NOW)["total"], 0)

    def test_lock_returns_error(self):
        with mock.patch.object(db_maintenance, "_retry", side_effect=db_maintenance.DatabaseBusyError("locked")):
            result = db_maintenance.prune(self.db, now=NOW)
        self.assertIn("locked", result["error"])


class VacuumTests(DBTestCase):
    def _bloat(self):
        conn = sqlite3.connect(self.db)
        conn.executemany("INSERT INTO agent_logs (timestamp, message) VALUES (?, ?)",
                         [("2026-11-14T00:00:00+00:00", "x" * 200) for _ in range(2000)])
        conn.commit()
        conn.execute("DELETE FROM agent_logs")
        conn.commit()
        conn.close()

    def test_not_needed(self):
        result = db_maintenance.vacuum_if_needed(self.db)
        self.assertFalse(result["needed"])
        self.assertFalse(result["vacuumed"])

    def test_dry_run(self):
        self._bloat()
        before = self.db.stat().st_size
        result = db_maintenance.vacuum_if_needed(self.db, dry_run=True)
        self.assertTrue(result["needed"])
        self.assertFalse(result["vacuumed"])
        self.assertEqual(self.db.stat().st_size, before)

    def test_vacuum_runs(self):
        self._bloat()
        result = db_maintenance.vacuum_if_needed(self.db)
        self.assertTrue(result["vacuumed"])

    def test_vacuum_reduces_free_ratio(self):
        self._bloat()
        result = db_maintenance.vacuum_if_needed(self.db)
        self.assertLessEqual(result["after"]["free_ratio"], result["before"]["free_ratio"])

    def test_vacuum_file_not_larger(self):
        self._bloat()
        before = self.db.stat().st_size
        db_maintenance.vacuum_if_needed(self.db)
        self.assertLessEqual(self.db.stat().st_size, before)

    def test_vacuum_threshold_custom(self):
        result = db_maintenance.vacuum_if_needed(self.db, threshold=1.0)
        self.assertFalse(result["needed"])

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            db_maintenance.vacuum_if_needed(Path(self.tmp.name) / "nope.db")

    def test_result_has_before(self):
        self.assertIn("before", db_maintenance.vacuum_if_needed(self.db))

    def test_not_needed_reason(self):
        result = db_maintenance.vacuum_if_needed(self.db)
        self.assertIn("below", result["reason"])

    def test_lock_raises_busy(self):
        with mock.patch.object(db_maintenance, "_retry", side_effect=db_maintenance.DatabaseBusyError("locked")):
            with self.assertRaises(db_maintenance.DatabaseBusyError):
                db_maintenance.vacuum_if_needed(self.db)


class IndexAdviceTests(DBTestCase):
    def test_tasks_known_index_no_advice(self):
        advice = db_maintenance.index_advice(self.db)
        self.assertFalse(any(a["table"] == "tasks" and a["columns"] == ["status", "priority", "id"] for a in advice))

    def test_approvals_index_advice(self):
        advice = db_maintenance.index_advice(self.db)
        self.assertIn("approvals", {a["table"] for a in advice})

    def test_advice_has_sql(self):
        advice = [a for a in db_maintenance.index_advice(self.db) if a["table"] == "approvals"][0]
        self.assertIn("CREATE INDEX", advice["sql"])

    def test_advice_columns(self):
        advice = [a for a in db_maintenance.index_advice(self.db) if a["table"] == "approvals"][0]
        self.assertEqual(advice["columns"], ["status", "id"])

    def test_advice_reason(self):
        advice = [a for a in db_maintenance.index_advice(self.db) if a["table"] == "approvals"][0]
        self.assertTrue(advice["reason"])

    def test_advice_does_not_create_index(self):
        before = self.rows("SELECT name FROM sqlite_master WHERE type='index'")
        db_maintenance.index_advice(self.db)
        after = self.rows("SELECT name FROM sqlite_master WHERE type='index'")
        self.assertEqual(before, after)

    def test_large_approvals_scan_advice(self):
        conn = sqlite3.connect(self.db)
        conn.executemany("INSERT INTO approvals (ts, status) VALUES ('2026-01-01', 'pending')", [()] * 300)
        conn.commit()
        conn.close()
        advice = db_maintenance.index_advice(self.db)
        self.assertTrue(any(a["source"] == "query-plan" for a in advice))

    def test_small_approvals_no_scan_advice(self):
        advice = db_maintenance.index_advice(self.db)
        self.assertFalse(any(a["source"] == "query-plan" for a in advice))

    def test_large_tasks_with_index_no_scan_advice(self):
        conn = sqlite3.connect(self.db)
        conn.executemany("INSERT INTO tasks (status, priority) VALUES ('queued', 5)", [()] * 300)
        conn.commit()
        conn.close()
        advice = db_maintenance.index_advice(self.db)
        self.assertFalse(any(a["table"] == "tasks" and a["source"] == "query-plan" for a in advice))

    def test_missing_tables_no_crash(self):
        empty = Path(self.tmp.name) / "empty.db"
        sqlite3.connect(empty).close()
        self.assertEqual(db_maintenance.index_advice(empty), [])

    def test_no_duplicate_advice(self):
        advice = db_maintenance.index_advice(self.db)
        keys = [(a["table"], tuple(a["columns"]), a["source"]) for a in advice]
        self.assertEqual(len(keys), len(set(keys)))

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            db_maintenance.index_advice(Path(self.tmp.name) / "nope.db")


class CopyTests(DBTestCase):
    def test_success(self):
        insert_logs(self.db)
        result = db_maintenance.safe_check_copy(self.db)
        self.assertTrue(result["ok"])

    def test_copy_deleted(self):
        self.assertTrue(db_maintenance.safe_check_copy(self.db)["deleted"])

    def test_no_leftovers(self):
        self.assertEqual(db_maintenance.safe_check_copy(self.db)["leftovers"], [])

    def test_temp_dir_empty_after(self):
        work = Path(self.tmp.name) / "copy-home"
        work.mkdir()
        db_maintenance.safe_check_copy(self.db, temp_dir=work)
        self.assertEqual(list(work.iterdir()), [])

    def test_row_counts_present(self):
        result = db_maintenance.safe_check_copy(self.db)
        self.assertIn("approvals", result["row_counts"])

    def test_row_counts_match(self):
        result = db_maintenance.safe_check_copy(self.db)
        self.assertTrue(all(src == dst for src, dst in result["row_counts"].values()))

    def test_integrity_ok(self):
        self.assertEqual(db_maintenance.safe_check_copy(self.db)["integrity"], "ok")

    def test_bytes_positive(self):
        self.assertGreater(db_maintenance.safe_check_copy(self.db)["bytes"], 0)

    def test_source_unchanged(self):
        insert_logs(self.db)
        before = self.count("agent_logs")
        db_maintenance.safe_check_copy(self.db)
        self.assertEqual(self.count("agent_logs"), before)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            db_maintenance.safe_check_copy(Path(self.tmp.name) / "nope.db")

    def test_repo_temp_dir_rejected(self):
        with self.assertRaises(ValueError):
            db_maintenance.safe_check_copy(self.db, temp_dir=db_maintenance.BASE_DIR)

    def test_temp_root_not_exposed(self):
        self.assertEqual(db_maintenance.safe_check_copy(self.db)["temp_root"], "system temp folder")

    def test_empty_database(self):
        empty = Path(self.tmp.name) / "empty.db"
        sqlite3.connect(empty).close()
        self.assertTrue(db_maintenance.safe_check_copy(empty)["ok"])

    def test_verify_copy_mismatch(self):
        copy = Path(self.tmp.name) / "copy.db"
        make_db(copy)
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO approvals (ts, status) VALUES ('2026-01-01', 'pending')")
        conn.commit()
        conn.close()
        result = db_maintenance.verify_copy(self.db, copy)
        self.assertFalse(result["ok"])
        self.assertIn("approvals", result["mismatches"])

    def test_verify_copy_match(self):
        copy = Path(self.tmp.name) / "copy2.db"
        make_db(copy)
        self.assertTrue(db_maintenance.verify_copy(self.db, copy)["ok"])

    def test_verify_copy_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            db_maintenance.verify_copy(self.db, Path(self.tmp.name) / "nope.db")

    def test_locked_returns_error(self):
        with mock.patch.object(db_maintenance, "_retry", side_effect=db_maintenance.DatabaseBusyError("locked")):
            result = db_maintenance.safe_check_copy(self.db)
        self.assertFalse(result["ok"])
        self.assertIn("locked", result["error"])


class ReportTests(DBTestCase):
    def test_report_ok(self):
        self.assertTrue(db_maintenance.build_report(self.db)["ok"])

    def test_report_keys(self):
        report = db_maintenance.build_report(self.db)
        for key in ("integrity", "stats", "schema", "prune_plan", "vacuum", "index_advice"):
            self.assertIn(key, report)

    def test_report_database_present(self):
        self.assertEqual(db_maintenance.build_report(self.db)["database"], "present")

    def test_report_schema_version(self):
        self.assertEqual(db_maintenance.build_report(self.db)["schema"]["version"], 0)

    def test_report_schema_pending(self):
        db_maintenance.clear_migrations()
        self.addCleanup(db_maintenance.clear_migrations)
        db_maintenance.register_migration(1, "one", lambda conn: None)
        self.assertEqual(db_maintenance.build_report(self.db)["schema"]["pending_count"], 1)

    def test_report_prune_does_not_delete(self):
        insert_logs(self.db)
        db_maintenance.build_report(self.db, now=NOW)
        self.assertEqual(self.count("agent_logs"), 2)

    def test_report_prune_plan_counts(self):
        insert_logs(self.db)
        plan = db_maintenance.build_report(self.db, now=NOW)["prune_plan"]
        self.assertEqual(plan["logs"]["agent_logs"], 1)

    def test_report_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            db_maintenance.build_report(Path(self.tmp.name) / "nope.db")

    def test_format_report_status(self):
        text = db_maintenance.format_report(db_maintenance.build_report(self.db))
        self.assertIn("✅ Healthy", text)

    def test_format_report_buttons(self):
        text = db_maintenance.format_report(db_maintenance.build_report(self.db))
        self.assertIn("[Clean now] [Check copy]", text)

    def test_format_report_no_money(self):
        text = db_maintenance.format_report(db_maintenance.build_report(self.db))
        self.assertIsNone(re.search(r"[₹$€£]|\b(?:USD|INR|EUR|GBP|Rs\.?)\b", text))

    def test_format_report_length(self):
        text = db_maintenance.format_report(db_maintenance.build_report(self.db))
        self.assertLessEqual(len(text), 3900)

    def test_format_unhealthy(self):
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("INSERT INTO child (id, parent_id) VALUES (9, 999)")
        conn.commit()
        conn.close()
        text = db_maintenance.format_report(db_maintenance.build_report(self.db))
        self.assertIn("❌ Problem", text)

    def test_cleanup_stops_on_unhealthy(self):
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("INSERT INTO child (id, parent_id) VALUES (9, 999)")
        conn.commit()
        conn.close()
        insert_logs(self.db)
        result = db_maintenance.run_cleanup(self.db, now=NOW)
        self.assertFalse(result["ok"])
        self.assertEqual(self.count("agent_logs"), 2)

    def test_cleanup_prunes(self):
        insert_logs(self.db)
        insert_task(self.db, "done", 70)
        db_maintenance.run_cleanup(self.db, now=NOW)
        self.assertEqual(self.count("agent_logs"), 1)
        self.assertEqual(self.count("tasks"), 0)

    def test_cleanup_before_after(self):
        result = db_maintenance.run_cleanup(self.db, now=NOW)
        self.assertIn("before", result)
        self.assertIn("after", result)

    def test_cleanup_format(self):
        text = db_maintenance.format_clean_result(db_maintenance.run_cleanup(self.db, now=NOW))
        self.assertIn("Clean done", text)

    def test_copy_format_ok(self):
        text = db_maintenance.format_copy_result(db_maintenance.safe_check_copy(self.db))
        self.assertIn("Check copy OK", text)

    def test_copy_format_fail(self):
        text = db_maintenance.format_copy_result({"ok": False, "error": "x"})
        self.assertIn("Check copy fail", text)

    def test_main_report(self):
        with mock.patch("builtins.print") as out:
            self.assertEqual(db_maintenance.main(["--db", str(self.db), "--report"]), 0)
        self.assertIn("DB Care", out.call_args[0][0])


class PluginTests(DBTestCase):
    def test_command_is_dbcare(self):
        self.assertEqual(tg_dbcare.COMMANDS[0]["cmd"], "/dbcare")

    def test_command_words(self):
        self.assertIn("db care", tg_dbcare.COMMANDS[0]["words"])

    def test_help_texts(self):
        self.assertTrue(tg_dbcare.COMMANDS[0]["help_en"])
        self.assertTrue(tg_dbcare.COMMANDS[0]["help_te"])

    def test_parse_slash_command(self):
        self.assertEqual(tg_dbcare.parse_action("/dbcare"), "report")

    def test_parse_bot_suffix(self):
        self.assertEqual(tg_dbcare.parse_action("/dbcare@chilli_bot"), "report")

    def test_parse_clean(self):
        self.assertEqual(tg_dbcare.parse_action("/dbcare clean"), "clean")

    def test_parse_cleanup(self):
        self.assertEqual(tg_dbcare.parse_action("/dbcare cleanup"), "clean")

    def test_parse_copy(self):
        self.assertEqual(tg_dbcare.parse_action("/dbcare copy"), "copy")

    def test_parse_plain_clean(self):
        self.assertEqual(tg_dbcare.parse_action("Clean now"), "clean")

    def test_parse_plain_copy(self):
        self.assertEqual(tg_dbcare.parse_action("Check copy"), "copy")

    def test_parse_plain_health(self):
        self.assertEqual(tg_dbcare.parse_action("db health"), "report")

    def test_parse_other_none(self):
        self.assertIsNone(tg_dbcare.parse_action("hello"))

    def test_parse_unknown_arg_none(self):
        self.assertIsNone(tg_dbcare.parse_action("/dbcare dance"))

    def test_handle_report(self):
        result = tg_dbcare.handle("/dbcare", {"db_path": self.db, "chat_id": "1"})
        self.assertIn("DB Care", result["text"])

    def test_handle_keyboard(self):
        result = tg_dbcare.handle("/dbcare", {"db_path": self.db, "chat_id": "1"})
        self.assertEqual(result["keyboard"], [["Clean now", "Check copy"]])

    def test_handle_clean(self):
        result = tg_dbcare.handle("Clean now", {"db_path": self.db, "chat_id": "1"})
        self.assertIn("Clean", result["text"])

    def test_handle_copy(self):
        result = tg_dbcare.handle("Check copy", {"db_path": self.db, "chat_id": "1"})
        self.assertIn("Check copy", result["text"])

    def test_handle_other_none(self):
        self.assertIsNone(tg_dbcare.handle("hello", {"db_path": self.db, "chat_id": "1"}))

    def test_owner_mismatch_silent(self):
        with mock.patch("notify._telegram_configured", return_value=(True, "t", "999")):
            self.assertIsNone(tg_dbcare.handle("/dbcare", {"db_path": self.db, "chat_id": "1"}))

    def test_owner_match(self):
        with mock.patch("notify._telegram_configured", return_value=(True, "t", "1")):
            self.assertIsNotNone(tg_dbcare.handle("/dbcare", {"db_path": self.db, "chat_id": "1"}))

    def test_unconfigured_allowed(self):
        with mock.patch("notify._telegram_configured", return_value=(False, None, None)):
            self.assertIsNotNone(tg_dbcare.handle("/dbcare", {"db_path": self.db, "chat_id": "1"}))

    def test_plugin_does_not_import_telegram_inbox(self):
        source = Path(tg_dbcare.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import telegram_inbox", source)

    def test_call_plugins_reachable(self):
        import telegram_inbox
        handled, result = telegram_inbox.call_plugins(
            "/dbcare", "token", "1", db_path=str(self.db), plugins=[tg_dbcare]
        )
        self.assertTrue(handled)
        self.assertIn("DB Care", result["text"])

    def test_call_plugins_clean_reachable(self):
        import telegram_inbox
        handled, result = telegram_inbox.call_plugins(
            "Clean now", "token", "1", db_path=str(self.db), plugins=[tg_dbcare]
        )
        self.assertTrue(handled)
        self.assertIn("Clean", result["text"])

    def test_call_plugins_not_handled(self):
        import telegram_inbox
        handled, result = telegram_inbox.call_plugins(
            "hello", "token", "1", db_path=str(self.db), plugins=[tg_dbcare]
        )
        self.assertFalse(handled)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
