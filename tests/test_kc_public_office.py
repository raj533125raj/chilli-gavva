"""tests/test_kc_public_office.py — Public Office life data export tests for Binny Mills.

Verifies:
1. "office" key structure:
   - agents: list of {name, team, activity} (capped at 500)
   - meetings: list of {draft_number, agents} where agents is list of job names (capped at 20)
   - letters: list of {from_team, to_team, kind, at} (capped at 20)
2. Safety rules:
   - No product titles
   - No draft titles
   - No money amounts / currencies ($ / USD / INR / etc.)
   - No tokens, keys, URLs with secrets (e.g. gsk_, api_key)
   - No kill_switch
   - No worker field inside office
   - No free text messages
3. Error / fault tolerance:
   - If building office fails or raises, omit the "office" key without breaking the card.
4. GITHUB_* env cleared in setUp using mock.patch.dict(os.environ, {}) and deleting GITHUB_* keys.
"""

import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import cubes
import export_status
import guardrails
import workforce


class PublicOfficeExportTests(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* env in setUp: use mock.patch.dict(os.environ, {}) then delete GITHUB_* keys
        self.env_patcher = mock.patch.dict(os.environ, {})
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_"):
                del os.environ[k]

        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")

        # Initialize base DB schema
        conn = sqlite3.connect(self.db)
        cubes.ensure_tables(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, payload TEXT,
            status TEXT DEFAULT 'queued', attempts INTEGER DEFAULT 0, agent TEXT,
            result TEXT, error TEXT, priority INTEGER DEFAULT 5, finished_ts TEXT,
            started_ts TEXT, machine_id TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, title TEXT,
            payload TEXT, status TEXT DEFAULT 'pending', review TEXT, decided_ts TEXT, reason TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS doctor_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent TEXT, category TEXT,
            status TEXT, details TEXT)""")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.env_patcher.stop()
        self.tmp.cleanup()

    def test_office_key_structure_and_allowed_fields(self):
        """Test the exact shape of 'office' in build_status."""
        status = export_status.build_status(self.db)
        self.assertIn("office", status)
        office = status["office"]
        self.assertEqual(set(office.keys()), {"agents", "meetings", "letters"})

        # Agents check
        self.assertIsInstance(office["agents"], list)
        self.assertGreater(len(office["agents"]), 0)
        self.assertLessEqual(len(office["agents"]), 500)
        for a in office["agents"]:
            self.assertEqual(set(a.keys()), {"name", "team", "activity"})
            self.assertIsInstance(a["name"], str)
            self.assertIsInstance(a["team"], str)
            self.assertIn(a["activity"], {"working", "idle", "waiting", "needs_you", "error", "off_duty"})

        # Meetings check
        self.assertIsInstance(office["meetings"], list)
        self.assertLessEqual(len(office["meetings"]), 20)
        for m in office["meetings"]:
            self.assertEqual(set(m.keys()), {"draft_number", "agents"})
            self.assertIsInstance(m["draft_number"], int)
            self.assertIsInstance(m["agents"], list)

        # Letters check
        self.assertIsInstance(office["letters"], list)
        self.assertLessEqual(len(office["letters"]), 20)
        for ltr in office["letters"]:
            self.assertEqual(set(ltr.keys()), {"from_team", "to_team", "kind", "at"})
            self.assertIsInstance(ltr["from_team"], str)
            self.assertIsInstance(ltr["to_team"], str)
            self.assertIsInstance(ltr["kind"], str)
            self.assertIsInstance(ltr["at"], str)

    def test_meetings_and_letters_populated_and_capped(self):
        """Verify meetings and letters populate properly from tasks and are capped."""
        conn = sqlite3.connect(self.db)
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        recent_ts = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. Populate meetings (draft #101 worked by 2 agents)
        conn.execute(
            "INSERT INTO tasks (kind, status, agent, result, finished_ts, ts) VALUES ('draft', 'done', 'Draft Worker', ?, ?, ?)",
            (json.dumps({"approval_id": 101}), recent_ts, recent_ts)
        )
        conn.execute(
            "INSERT INTO tasks (kind, status, agent, payload, finished_ts, ts) VALUES ('review', 'done', 'QA Checker', ?, ?, ?)",
            (json.dumps({"approval_id": 101}), recent_ts, recent_ts)
        )

        # 2. Populate 25 letters (cross-team handoffs)
        for i in range(1, 26):
            res = json.dumps({"approval_id": 200 + i, "next": [{"kind": "review", "payload": {"approval_id": 200 + i}}]})
            conn.execute(
                "INSERT INTO tasks (kind, status, agent, result, finished_ts, ts) VALUES ('draft', 'done', 'Draft Worker', ?, ?, ?)",
                (res, recent_ts, recent_ts)
            )
        conn.commit()
        conn.close()

        status = export_status.build_status(self.db)
        office = status["office"]

        # Meetings check
        self.assertGreaterEqual(len(office["meetings"]), 1)
        self.assertLessEqual(len(office["meetings"]), 20)
        m101 = next((m for m in office["meetings"] if m["draft_number"] == 101), None)
        self.assertIsNotNone(m101)
        self.assertIn("Draft Worker", m101["agents"])
        self.assertIn("QA Checker", m101["agents"])

        # Letters check
        self.assertLessEqual(len(office["letters"]), 20)
        self.assertGreaterEqual(len(office["letters"]), 1)
        for ltr in office["letters"]:
            self.assertEqual(set(ltr.keys()), {"from_team", "to_team", "kind", "at"})

    def test_agent_cap_at_500(self):
        """Verify agents list caps at 500 even if projection had more."""
        fake_teams = []
        for i in range(600):
            fake_teams.append({
                "id": f"team_{i}",
                "name": f"Team {i}",
                "captain": {"name": f"Agent {i}", "activity": "working"},
                "agents": [],
            })
        with mock.patch("office_projection.project_office_snapshot", return_value={"teams": fake_teams}):
            status = export_status.build_status(self.db)
            self.assertIn("office", status)
            self.assertEqual(len(status["office"]["agents"]), 500)

    def test_safety_rules_no_money_secrets_titles_killswitch_or_worker(self):
        """Prove safety rules: no product/draft titles, money, secrets, kill_switch, worker, or free text."""
        conn = sqlite3.connect(self.db)
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        recent_ts = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Insert tasks and approvals with poison data (secrets, money, private product titles)
        conn.execute(
            "INSERT INTO approvals (id, ts, kind, title, review, status) VALUES (99, ?, 'post', 'Secret Fitness Book $99 USD', 'Review gsk_secret_token_12345', 'pending')",
            (recent_ts,)
        )
        conn.execute(
            "INSERT INTO tasks (kind, status, agent, result, payload, finished_ts, ts) VALUES ('draft', 'done', 'Draft Worker', ?, ?, ?, ?)",
            (
                json.dumps({"approval_id": 99, "free_text_msg": "Hello do not show me", "secret_key": "gsk_12345", "price": "$99"}),
                json.dumps({"title": "Super Private Title", "money": 500}),
                recent_ts,
                recent_ts
            )
        )
        conn.commit()
        conn.close()

        status = export_status.build_status(self.db)
        self.assertIn("office", status)
        office_blob = json.dumps(status["office"])

        # 1. No product/draft titles or free text message keys
        self.assertNotIn("Secret Fitness Book", office_blob)
        self.assertNotIn("Super Private Title", office_blob)
        self.assertNotIn("free_text_msg", office_blob)
        self.assertNotIn("Hello do not show me", office_blob)

        # 2. No money amounts or currency symbols
        self.assertNotIn("$99", office_blob)
        self.assertNotIn("USD", office_blob)
        self.assertNotIn("price", office_blob)
        self.assertNotIn("money", office_blob)

        # 3. No secrets or tokens
        self.assertNotIn("gsk_", office_blob)
        self.assertNotIn("secret", office_blob.lower())

        # 4. No kill_switch
        self.assertNotIn("kill_switch", office_blob)
        self.assertNotIn("kill", office_blob.lower())

        # 5. No worker field inside office
        self.assertNotIn("worker", status["office"])
        for a in status["office"]["agents"]:
            self.assertNotIn("worker", a)

    def test_failure_omits_office_key_safely_without_crashing(self):
        """If building office data fails, the office key is omitted and status still builds safely."""
        with mock.patch("office_projection.project_office_snapshot", side_effect=RuntimeError("Projection crashed")):
            status = export_status.build_status(self.db)
            self.assertNotIn("office", status)
            # Other essential top-level fields are untouched
            self.assertIn("generated_at", status)
            self.assertIn("agents", status)
            self.assertIn("task_counts", status)
            self.assertIn("recent_tasks", status)
            self.assertIn("pending_approvals", status)
            self.assertIn("worker", status)
            self.assertIn("handoffs", status)


if __name__ == "__main__":
    unittest.main()
