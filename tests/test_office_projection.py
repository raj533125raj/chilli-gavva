"""Unit tests for office protocol and projection (read by Binny Mills)."""

import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import cubes
import guardrails
import office_protocol
import office_projection


class TestOfficeProjection(unittest.TestCase):
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

    def test_empty_database_returns_honest_empty_snapshot(self):
        # Empty DB (file does not even exist)
        non_existent_db = os.path.join(self.temp_dir.name, "missing.db")
        snapshot = office_projection.project_office_snapshot(db_path=non_existent_db)

        self.assertEqual(snapshot["version"], office_protocol.PROTOCOL_VERSION)
        self.assertEqual(snapshot["office_id"], office_protocol.DEFAULT_OFFICE_ID)
        self.assertIsInstance(snapshot["teams"], list)
        self.assertIsInstance(snapshot["counters"], dict)
        self.assertIsInstance(snapshot["approvals"], list)
        self.assertEqual(len(snapshot["approvals"]), 0)

        # Counters should be zeroes / honest nulls
        counters = snapshot["counters"]
        self.assertEqual(counters["running"], 0)
        self.assertEqual(counters["drafts_waiting"], 0)
        self.assertEqual(counters["products_ready"], 0)
        self.assertEqual(counters["products_needs_fix"], 0)
        self.assertIsNone(counters["minutes_used"])

    def test_sample_db_with_two_teams(self):
        # Create a database with 2 cubes, tasks, approvals, and doctor cases
        conn = sqlite3.connect(self.db_path)
        cubes.ensure_tables(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, title TEXT,
            payload TEXT, status TEXT DEFAULT 'pending', review TEXT, decided_ts TEXT, reason TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, payload TEXT,
            status TEXT DEFAULT 'queued', attempts INTEGER DEFAULT 0, agent TEXT,
            result TEXT, error TEXT, priority INTEGER DEFAULT 5, finished_ts TEXT,
            started_ts TEXT, machine_id TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS doctor_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent TEXT, step TEXT,
            category TEXT, error TEXT, remedy TEXT, status TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS heartbeat (
            name TEXT PRIMARY KEY, ts TEXT, info TEXT, machine_id TEXT)""")

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Set executive cube to on, and content cube to off
        conn.execute("INSERT INTO cube_state (id, status, updated_ts) VALUES ('executive', 'on', ?)", (now_iso,))
        conn.execute("INSERT INTO cube_state (id, status, updated_ts) VALUES ('content', 'off', ?)", (now_iso,))

        # Heartbeat active
        conn.execute("INSERT INTO heartbeat (name, ts, info) VALUES ('worker', ?, 'running')", (now_iso,))

        # Active task for CEO
        conn.execute(
            "INSERT INTO tasks (ts, kind, status, agent, started_ts) VALUES (?, 'plan', 'running', 'CEO', ?)",
            (now_iso, now_iso)
        )

        conn.commit()
        conn.close()

        custom_cubes = [
            {
                "id": "content",
                "name": "Content Department",
                "role": "Drafting articles and digital assets",
                "captain": "Content Manager",
                "sub_rooms": ["Drafting Room"],
                "agents": ["Draft Worker"],
                "sub_agents": [],
                "right_hand": None,
                "left_hand": None,
                "kinds": ["draft", "rewrite"],
            },
            {
                "id": "executive",
                "name": "Executive Office",
                "role": "Planning and direction",
                "captain": "CEO",
                "sub_rooms": ["Boardroom"],
                "agents": [],
                "sub_agents": [],
                "right_hand": None,
                "left_hand": None,
                "kinds": ["plan"],
            },
        ]

        with mock.patch("cubes.load_registry", return_value=custom_cubes):
            snapshot = office_projection.project_office_snapshot(db_path=self.db_path)

        self.assertEqual(len(snapshot["teams"]), 2)
        # Check deterministic sorting: 'content' should come before 'executive' by id
        self.assertEqual(snapshot["teams"][0]["id"], "content")
        self.assertEqual(snapshot["teams"][1]["id"], "executive")

        # Check content team is paused because cube_state is off
        content_team = snapshot["teams"][0]
        self.assertEqual(content_team["status"], "off")
        self.assertEqual(content_team["captain"]["status"], "paused")
        self.assertEqual(content_team["agents"][0]["status"], "paused")

        # Check executive team CEO is thinking/working on plan
        exec_team = snapshot["teams"][1]
        self.assertEqual(exec_team["status"], "on")
        self.assertEqual(exec_team["captain"]["status"], "thinking")
        self.assertEqual(exec_team["captain"]["current_action"], "Planning daily tasks")
        self.assertEqual(exec_team["captain"]["progress"], 50)

    def test_secret_looking_text_scrubbed(self):
        conn = sqlite3.connect(self.db_path)
        cubes.ensure_tables(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, title TEXT,
            payload TEXT, status TEXT DEFAULT 'pending', review TEXT, decided_ts TEXT, reason TEXT)""")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # Title with secret key and email and money
        dirty_title = "Secret API key: token=abcdef123456 contact test-lead@test.com for $500"
        conn.execute(
            "INSERT INTO approvals (ts, kind, title, status) VALUES (?, 'public_draft', ?, 'pending')",
            (now_iso, dirty_title)
        )
        conn.commit()
        conn.close()

        custom_cubes = [
            {
                "id": "executive",
                "name": "Secret api_key=xyz Team",
                "role": "Planning with secret=1234 and boss@test.org and $100",
                "captain": "CEO",
                "sub_rooms": ["Room"],
                "agents": [],
                "sub_agents": [],
                "right_hand": None,
                "left_hand": None,
                "kinds": ["plan"],
            }
        ]

        with mock.patch("cubes.load_registry", return_value=custom_cubes):
            snapshot = office_projection.project_office_snapshot(db_path=self.db_path)

        # Team name and role scrubbed
        team = snapshot["teams"][0]
        self.assertNotIn("api_key=xyz", team["name"])
        self.assertNotIn("secret=1234", team["role"])
        self.assertNotIn("boss@test.org", team["role"])
        self.assertNotIn("$100", team["role"])

        # Approval scrubbed
        self.assertEqual(len(snapshot["approvals"]), 1)
        app = snapshot["approvals"][0]
        self.assertNotIn("token=abcdef123456", app["title"])
        self.assertNotIn("test-lead@test.com", app["title"])
        self.assertNotIn("$500", app["title"])

    def test_sizes_capped(self):
        # Test validation bounds on excessive lengths and counts
        long_name = "A" * 500
        validated_agent = office_protocol.validate_agent({
            "id": "id1",
            "name": long_name,
            "level": "main",
            "status": "idle",
            "current_action": "B" * 500,
        })
        self.assertLessEqual(len(validated_agent["name"]), office_protocol.MAX_NAME_LEN)
        self.assertLessEqual(len(validated_agent["current_action"]), office_protocol.MAX_ACTION_LEN)

        # Test snapshot collection caps
        huge_teams = [
            {
                "id": f"team_{i}",
                "name": f"Team {i}",
                "role": "Role",
                "status": "on",
                "captain": None,
                "sub_rooms": [],
                "agents": [],
            }
            for i in range(150)
        ]
        snapshot = office_protocol.validate_snapshot({
            "version": office_protocol.PROTOCOL_VERSION,
            "teams": huge_teams,
            "counters": {},
            "approvals": [],
        })
        self.assertLessEqual(len(snapshot["teams"]), office_protocol.MAX_TEAMS_COUNT)

    def test_schema_version_check(self):
        invalid_snapshot = {
            "version": "99.0.0",
            "teams": [],
            "counters": {},
            "approvals": [],
        }
        with self.assertRaises(office_protocol.ProtocolValidationError):
            office_protocol.validate_snapshot(invalid_snapshot)

    def test_24h_due_time_correct(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, title TEXT,
            payload TEXT, status TEXT DEFAULT 'pending', review TEXT, decided_ts TEXT, reason TEXT)""")

        # Created 4 hours ago
        base_time = datetime.datetime(2025, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        created_time = base_time - datetime.timedelta(hours=4)
        created_iso = created_time.isoformat().replace("+00:00", "Z")

        conn.execute(
            "INSERT INTO approvals (id, ts, kind, title, review, status) VALUES (10, ?, 'public_draft', 'Test Draft', 'score 85/100', 'pending')",
            (created_iso,)
        )
        conn.commit()
        conn.close()

        with mock.patch("cubes.load_registry", return_value=[]):
            snapshot = office_projection.project_office_snapshot(db_path=self.db_path, now=base_time)

        self.assertEqual(len(snapshot["approvals"]), 1)
        app = snapshot["approvals"][0]
        self.assertEqual(app["id"], 10)
        self.assertEqual(app["score"], 85)
        self.assertEqual(app["waited_hours"], 4.0)

        # 24h due time should be created_time + 24 hours: 2025-01-16T08:00:00Z
        expected_due = (created_time + datetime.timedelta(hours=24)).isoformat().replace("+00:00", "Z")
        self.assertEqual(app["auto_decision_due"], expected_due)

    def test_deterministic_output_order(self):
        # Check that multiple runs on same DB yield identical team, sub_room, agent, and approval orders
        conn = sqlite3.connect(self.db_path)
        cubes.ensure_tables(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, title TEXT,
            payload TEXT, status TEXT DEFAULT 'pending', review TEXT, decided_ts TEXT, reason TEXT)""")
        now_iso = "2025-01-15T12:00:00Z"
        conn.execute("INSERT INTO approvals (id, ts, kind, title, status) VALUES (5, ?, 'public_draft', 'Five', 'pending')", (now_iso,))
        conn.execute("INSERT INTO approvals (id, ts, kind, title, status) VALUES (2, ?, 'public_draft', 'Two', 'pending')", (now_iso,))
        conn.execute("INSERT INTO approvals (id, ts, kind, title, status) VALUES (9, ?, 'public_draft', 'Nine', 'pending')", (now_iso,))
        conn.commit()
        conn.close()

        custom_cubes = [
            {
                "id": "zebra",
                "name": "Zebra Team",
                "role": "Zebra",
                "captain": "Draft Worker",
                "sub_rooms": ["Zebra Room B", "Zebra Room A"],
                "agents": ["Coder Worker", "Research Worker"],
                "sub_agents": [],
                "right_hand": None,
                "left_hand": None,
                "kinds": [],
            },
            {
                "id": "alpha",
                "name": "Alpha Team",
                "role": "Alpha",
                "captain": "CEO",
                "sub_rooms": ["Room 2", "Room 1"],
                "agents": [],
                "sub_agents": [],
                "right_hand": None,
                "left_hand": None,
                "kinds": [],
            },
        ]

        with mock.patch("cubes.load_registry", return_value=custom_cubes):
            s1 = office_projection.project_office_snapshot(db_path=self.db_path, now=datetime.datetime(2025, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc))
            s2 = office_projection.project_office_snapshot(db_path=self.db_path, now=datetime.datetime(2025, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc))

        self.assertEqual(json.dumps(s1, sort_keys=True), json.dumps(s2, sort_keys=True))

        # Check teams ordered alpha then zebra
        self.assertEqual([t["id"] for t in s1["teams"]], ["alpha", "zebra"])

        # Check approvals ordered 2, 5, 9
        self.assertEqual([a["id"] for a in s1["approvals"]], [2, 5, 9])

        # Check sub rooms ordered Room 1, Room 2
        self.assertEqual([r["name"] for r in s1["teams"][0]["sub_rooms"]], ["Room 1", "Room 2"])


if __name__ == "__main__":
    unittest.main()
