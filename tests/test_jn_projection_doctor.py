"""tests/test_jn_projection_doctor.py — Tests for office projection and doctor tell the truth fixes.

Checks:
1. Pending draft titles are anonymised exactly as Draft #<id> (export_status parity).
2. Doctor case with status='needs_owner' maps to 'needs you' status for the agent.
3. Revision increases when content changes, and is stable when nothing changes.
4. doctor.py handles bad JSON payload in tasks gracefully without crashing.
5. doctor.py heal_stuck_items never re-queues a task that is currently running.
"""

import datetime
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

import cubes
import doctor
import guardrails
import office_projection


class TestJNProjectionDoctor(unittest.TestCase):
    def setUp(self):
        # Jules rule: in unit test setUp methods, clear GITHUB_ACTIONS, RUNNER_ENVIRONMENT, GITHUB_RUN_ID
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "GITHUB_ACTIONS": "",
                "RUNNER_ENVIRONMENT": "",
                "GITHUB_RUN_ID": "",
                "CHILLI_TEST": "1",
            },
            clear=False,
        )
        self.env_patcher.start()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_agent_data.db")

    def tearDown(self):
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def test_pending_draft_titles_anonymised(self):
        """1. office_projection pending draft titles must be anonymised as Draft #<id>."""
        conn = sqlite3.connect(self.db_path)
        cubes.ensure_tables(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, title TEXT,
            payload TEXT, status TEXT DEFAULT 'pending', review TEXT, decided_ts TEXT, reason TEXT)""")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        real_title = "Secret Unapproved Product Blueprint v1.0"
        conn.execute(
            "INSERT INTO approvals (id, ts, kind, title, status) VALUES (42, ?, 'public_draft', ?, 'pending')",
            (now_iso, real_title)
        )
        conn.commit()
        conn.close()

        with mock.patch("cubes.load_registry", return_value=[]):
            snap = office_projection.project_office_snapshot(db_path=self.db_path)

        self.assertEqual(len(snap["approvals"]), 1)
        app = snap["approvals"][0]
        self.assertEqual(app["id"], 42)
        self.assertEqual(app["title"], "Draft #42")
        self.assertNotIn("Secret", app["title"])
        self.assertNotIn("Blueprint", app["title"])

    def test_doctor_status_needs_owner_maps_to_needs_you(self):
        """2. Doctor cases with status='needs_owner' map agent status to 'needs you'."""
        conn = sqlite3.connect(self.db_path)
        cubes.ensure_tables(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS doctor_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent TEXT, step TEXT,
            error_class TEXT, error_message TEXT, last_5_actions TEXT,
            category TEXT, diagnosis TEXT, remedy TEXT, status TEXT,
            actions_count INTEGER DEFAULT 1, task_id INTEGER)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS cube_state (
            id TEXT PRIMARY KEY, status TEXT DEFAULT 'on', updated_ts TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS heartbeat (
            name TEXT PRIMARY KEY, ts TEXT, info TEXT, machine_id TEXT)""")

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute("INSERT INTO cube_state (id, status, updated_ts) VALUES ('executive', 'on', ?)", (now_iso,))
        conn.execute("INSERT INTO heartbeat (name, ts, info) VALUES ('worker', ?, 'running')", (now_iso,))

        # Doctor case where status='needs_owner' and category='doctor_heal' (category is not 'needs_owner')
        conn.execute("""INSERT INTO doctor_cases
            (ts, agent, step, error_class, error_message, last_5_actions, category, diagnosis, remedy, status)
            VALUES (?, 'CEO', 'step1', 'Error', 'Msg', '[]', 'doctor_heal', 'diag', 'remedy', 'needs_owner')""",
            (now_iso,)
        )
        conn.commit()
        conn.close()

        custom_cubes = [
            {
                "id": "executive",
                "name": "Executive Office",
                "role": "Planning and direction",
                "captain": "CEO",
                "sub_rooms": [],
                "agents": [],
                "sub_agents": [],
                "right_hand": None,
                "left_hand": None,
                "kinds": ["plan"],
            }
        ]

        with mock.patch("cubes.load_registry", return_value=custom_cubes):
            snap = office_projection.project_office_snapshot(db_path=self.db_path)

        exec_team = snap["teams"][0]
        ceo = exec_team["captain"]
        self.assertEqual(ceo["status"], "needs you")
        self.assertEqual(ceo["current_action"], "Needs owner assistance")

    def test_revision_increases_on_change_and_stable_when_unchanged(self):
        """3. Revision increases when content changes and is stable when nothing changes."""
        conn = sqlite3.connect(self.db_path)
        cubes.ensure_tables(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, payload TEXT,
            status TEXT DEFAULT 'queued', attempts INTEGER DEFAULT 0, agent TEXT,
            result TEXT, error TEXT, priority INTEGER DEFAULT 5, finished_ts TEXT,
            started_ts TEXT, machine_id TEXT)""")
        conn.commit()
        conn.close()

        snap1 = office_projection.project_office_snapshot(db_path=self.db_path)
        rev1 = snap1["revision"]

        # Unchanged run
        snap2 = office_projection.project_office_snapshot(db_path=self.db_path)
        rev2 = snap2["revision"]
        self.assertEqual(rev1, rev2)

        # Change content (insert active task for CEO)
        conn = sqlite3.connect(self.db_path)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO tasks (ts, kind, status, agent, started_ts) VALUES (?, 'plan', 'running', 'CEO', ?)",
            (now_iso, now_iso)
        )
        conn.commit()
        conn.close()

        snap3 = office_projection.project_office_snapshot(db_path=self.db_path)
        rev3 = snap3["revision"]
        self.assertGreater(rev3, rev2)

        # Stable again
        snap4 = office_projection.project_office_snapshot(db_path=self.db_path)
        rev4 = snap4["revision"]
        self.assertEqual(rev3, rev4)

    def test_doctor_detect_stuck_handles_corrupt_task_payload_json(self):
        """4. doctor.py guard json.loads (bad JSON -> skip that item, never crash)."""
        conn = doctor.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS office_missions (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, agent_id TEXT NOT NULL, every TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active', next_run REAL NOT NULL, last_run REAL,
                updated REAL NOT NULL
            )
        """)
        # Insert overdue mission
        overdue_ts = time.time() - 7200
        conn.execute(
            "INSERT INTO office_missions VALUES ('m_corrupt', 'Mission Corrupt', 'agent_x', '1d', 'active', ?, 0, ?)",
            (overdue_ts, overdue_ts)
        )
        # Insert task with corrupt JSON payload
        conn.execute(
            "INSERT INTO tasks (kind, status, payload) VALUES ('office_task', 'running', 'NOT_VALID_JSON{:::')"
        )
        conn.commit()
        conn.close()

        # detect_all_stuck_items must not raise an unhandled exception
        stuck = doctor.detect_all_stuck_items(db_path=self.db_path)
        self.assertTrue(any("Corrupt" in s.get("reason", "") or "corrupt" in s.get("item_key", "") for s in stuck))

    def test_doctor_never_requeues_currently_running_task(self):
        """5. doctor.py heal_stuck_items never re-queues a task that is currently running."""
        conn = doctor.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, payload TEXT,
                status TEXT DEFAULT 'queued', attempts INTEGER DEFAULT 0, agent TEXT,
                result TEXT, error TEXT, priority INTEGER DEFAULT 5, finished_ts TEXT,
                started_ts TEXT, machine_id TEXT
            )
        """)
        # Insert running task with attempts >= 2
        conn.execute(
            "INSERT INTO tasks (id, kind, status, attempts, agent) VALUES (888, 'test_task', 'running', 2, 'Worker Agent')"
        )
        conn.commit()
        conn.close()

        res = doctor.heal_stuck_items(db_path=self.db_path)

        conn = doctor.connect(self.db_path)
        row = conn.execute("SELECT status FROM tasks WHERE id = 888").fetchone()
        conn.close()

        # Must remain 'running', NOT overwritten to 'queued'
        self.assertEqual(row[0], "running")


if __name__ == "__main__":
    unittest.main()
