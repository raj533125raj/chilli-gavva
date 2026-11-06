"""tests/test_ka_office_events.py — Tests for Binny-KA Office Life Events:
Lounge activity, round table meetings, and post bird cross-team letters.
"""

import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import cubes
import office_projection
import office_protocol


class TestKAOfficeEvents(unittest.TestCase):

    def setUp(self):
        # Clear GITHUB_* env in setUp with mock.patch.dict
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
        patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "agent_data.db")

        # Initialize base tables
        conn = sqlite3.connect(self.db_path)
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

    def test_activity_mapping_and_30min_idle_threshold(self):
        """Test activity maps to: working | idle | waiting | needs_you | error | off_duty.

        Idle = no running task in the last 30 minutes.
        """
        now = datetime.datetime(2025, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        recent_started = (now - datetime.timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
        stale_started = (now - datetime.timedelta(minutes=45)).isoformat().replace("+00:00", "Z")

        conn = sqlite3.connect(self.db_path)
        # Draft Worker has a running task started 10m ago -> working
        conn.execute(
            "INSERT INTO tasks (kind, status, agent, started_ts, ts) VALUES ('draft', 'running', 'Draft Worker', ?, ?)",
            (recent_started, recent_started)
        )
        # Coder Worker has a running task started 45m ago -> idle (exceeded 30 min threshold)
        conn.execute(
            "INSERT INTO tasks (kind, status, agent, started_ts, ts) VALUES ('code_proposal', 'running', 'Coder Worker', ?, ?)",
            (stale_started, stale_started)
        )
        # QA Checker has a failed task -> error
        conn.execute(
            "INSERT INTO tasks (kind, status, agent, finished_ts, ts) VALUES ('review', 'failed', 'QA Checker', ?, ?)",
            (recent_started, recent_started)
        )
        # Research Worker has a queued task -> waiting
        conn.execute(
            "INSERT INTO tasks (kind, status, agent, ts) VALUES ('research', 'queued', 'Research Worker', ?)",
            (recent_started,)
        )
        # Content Manager has a doctor case stuck -> needs_you
        conn.execute(
            "INSERT INTO doctor_cases (agent, category, status, ts) VALUES ('Content Manager', 'needs_owner', 'needs_owner', ?)",
            (recent_started,)
        )
        conn.commit()
        conn.close()

        # Mock workforce status running = True so daemon is active
        with mock.patch("workforce.worker_status", return_value={"running": True}):
            snapshot = office_projection.project_office_snapshot(db_path=self.db_path, now=now)

        agent_activities = {}
        for team in snapshot["teams"]:
            if team["captain"]:
                agent_activities[team["captain"]["name"]] = team["captain"]["activity"]
            for a in team["agents"]:
                agent_activities[a["name"]] = a["activity"]

        # Draft Worker (running 10m ago) -> working
        self.assertEqual(agent_activities.get("Draft Worker"), "working")
        # Coder Worker (running 45m ago > 30m) -> idle
        self.assertEqual(agent_activities.get("Coder Worker"), "idle")
        # QA Checker (failed task) -> error
        self.assertEqual(agent_activities.get("QA Checker"), "error")
        # Research Worker (queued task) -> waiting
        self.assertEqual(agent_activities.get("Research Worker"), "waiting")
        # Content Manager (needs owner) -> needs_you
        self.assertEqual(agent_activities.get("Content Manager"), "needs_you")

        # Planned or paused cube agents -> off_duty
        # Instagram Captain is in planned cube instagram-team -> off_duty
        self.assertEqual(agent_activities.get("Instagram Captain"), "off_duty")

        # CEO (no tasks, daemon running) -> idle
        self.assertEqual(agent_activities.get("CEO"), "idle")

    def test_meetings_detected_only_with_two_or_more_agents_in_two_hours(self):
        """Meeting detected only when 2+ distinct agents have tasks for the same draft/approval in the last 2 hours."""
        now = datetime.datetime(2025, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        recent_ts = (now - datetime.timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        old_ts = (now - datetime.timedelta(hours=3)).isoformat().replace("+00:00", "Z")

        conn = sqlite3.connect(self.db_path)
        # Draft #101: worked on by Draft Worker and QA Checker in the last 2 hours -> Meeting!
        conn.execute(
            "INSERT INTO tasks (kind, status, agent, payload, started_ts, finished_ts, ts) "
            "VALUES ('draft', 'done', 'Draft Worker', ?, ?, ?, ?)",
            (json.dumps({"topic": "Finance tracker"}), recent_ts, recent_ts, recent_ts)
        )
        # Result has approval_id 101
        conn.execute(
            "UPDATE tasks SET result = ? WHERE id = last_insert_rowid()",
            (json.dumps({"approval_id": 101}),)
        )

        conn.execute(
            "INSERT INTO tasks (kind, status, agent, payload, started_ts, finished_ts, ts) "
            "VALUES ('review', 'done', 'QA Checker', ?, ?, ?, ?)",
            (json.dumps({"approval_id": 101}), recent_ts, recent_ts, recent_ts)
        )

        # Draft #202: worked on ONLY by Draft Worker in the last 2 hours -> No meeting (only 1 agent)
        conn.execute(
            "INSERT INTO tasks (kind, status, agent, payload, started_ts, finished_ts, ts) "
            "VALUES ('draft', 'done', 'Draft Worker', ?, ?, ?, ?)",
            (json.dumps({"approval_id": 202}), recent_ts, recent_ts, recent_ts)
        )

        # Draft #303: worked on by Draft Worker and QA Checker, but 3 hours ago (> 2 hours) -> No meeting!
        conn.execute(
            "INSERT INTO tasks (kind, status, agent, payload, started_ts, finished_ts, ts) "
            "VALUES ('draft', 'done', 'Draft Worker', ?, ?, ?, ?)",
            (json.dumps({"approval_id": 303}), old_ts, old_ts, old_ts)
        )
        conn.execute(
            "INSERT INTO tasks (kind, status, agent, payload, started_ts, finished_ts, ts) "
            "VALUES ('review', 'done', 'QA Checker', ?, ?, ?, ?)",
            (json.dumps({"approval_id": 303}), old_ts, old_ts, old_ts)
        )

        conn.commit()
        conn.close()

        snapshot = office_projection.project_office_snapshot(db_path=self.db_path, now=now)
        meetings = snapshot.get("meetings", [])

        # Should only have meeting for Draft #101
        self.assertEqual(len(meetings), 1)
        m = meetings[0]
        self.assertEqual(m["draft_number"], 101)
        self.assertEqual(sorted(m["agents"]), ["Draft Worker", "QA Checker"])

    def test_letters_capped_at_20_and_contain_no_text_or_title_or_money(self):
        """Letters: up to 20 recent cross-team handoffs {from_team, to_team, kind, at}.

        No message text, no titles, no money amounts.
        """
        now = datetime.datetime(2025, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        recent_ts = (now - datetime.timedelta(minutes=10)).isoformat().replace("+00:00", "Z")

        conn = sqlite3.connect(self.db_path)
        # Insert 30 done tasks that trigger cross-team handoffs:
        # e.g., Draft Worker (Content Dept) -> next task: 'review' -> QA Checker (Quality Assurance Dept)
        for i in range(30):
            ts = (now - datetime.timedelta(minutes=i + 1)).isoformat().replace("+00:00", "Z")
            conn.execute(
                "INSERT INTO tasks (kind, status, agent, payload, result, finished_ts, ts) "
                "VALUES ('draft', 'done', 'Draft Worker', ?, ?, ?, ?)",
                (
                    json.dumps({"secret_idea": "Super secret plan with $100 price tag"}),
                    json.dumps({
                        "approval_id": 500 + i,
                        "title": f"Secret Product {i} for $50",
                        "next": [{"kind": "review", "payload": {"approval_id": 500 + i}}],
                    }),
                    ts,
                    ts
                )
            )
        conn.commit()
        conn.close()

        snapshot = office_projection.project_office_snapshot(db_path=self.db_path, now=now)
        letters = snapshot.get("letters", [])

        self.assertLessEqual(len(letters), 20)
        self.assertGreater(len(letters), 0)

        for letter in letters:
            self.assertEqual(set(letter.keys()), {"from_team", "to_team", "kind", "at"})
            self.assertNotEqual(letter["from_team"], letter["to_team"])
            self.assertNotIn("text", letter)
            self.assertNotIn("title", letter)
            self.assertNotIn("money", letter)
            self.assertNotIn("$", str(letter))
            self.assertNotIn("Secret", str(letter))

    def test_validator_accepts_old_and_new_shapes(self):
        """Validator accepts legacy snapshots (without meetings/letters) and new shapes."""
        legacy_snapshot = {
            "version": office_protocol.PROTOCOL_VERSION,
            "office_id": "test-office",
            "revision": 1,
            "generated_at": "2025-01-15T12:00:00Z",
            "teams": [],
            "counters": {
                "agents": 0, "running": 0, "needs_you": 0, "drafts_waiting": 0,
                "products_ready": 0, "products_needs_fix": 0, "minutes_used": 0,
                "minutes_limit": 100, "ai_providers_up": 1, "ai_providers_total": 1,
            },
            "approvals": [],
        }

        # Validating legacy snapshot must succeed and keep it valid
        v_legacy = office_protocol.validate_snapshot(legacy_snapshot)
        self.assertEqual(v_legacy["office_id"], "test-office")
        self.assertNotIn("meetings", v_legacy)
        self.assertNotIn("letters", v_legacy)

        # New shape with meetings and letters
        new_snapshot = dict(legacy_snapshot)
        new_snapshot["meetings"] = [
            {"draft_number": 42, "agents": ["Draft Worker", "QA Checker"]}
        ]
        new_snapshot["letters"] = [
            {"from_team": "Content Department", "to_team": "Quality Assurance Department", "kind": "review", "at": "2025-01-15T12:00:00Z"}
        ]
        v_new = office_protocol.validate_snapshot(new_snapshot)
        self.assertIn("meetings", v_new)
        self.assertEqual(len(v_new["meetings"]), 1)
        self.assertEqual(v_new["meetings"][0]["draft_number"], 42)
        self.assertIn("letters", v_new)
        self.assertEqual(len(v_new["letters"]), 1)
        self.assertEqual(v_new["letters"][0]["kind"], "review")

    def test_projection_stays_public_safe(self):
        """Projection stays public-safe: no currency amounts, no private draft titles."""
        now = datetime.datetime(2025, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        conn = sqlite3.connect(self.db_path)
        # Approval with private title and price
        conn.execute(
            "INSERT INTO approvals (id, ts, kind, title, review, status) "
            "VALUES (77, ?, 'public_draft', 'Top Secret AI Fitness Guide $99 USD', 'score 80', 'pending')",
            (now.isoformat().replace("+00:00", "Z"),)
        )
        conn.commit()
        conn.close()

        snapshot = office_projection.project_office_snapshot(db_path=self.db_path, now=now)
        serialized = json.dumps(snapshot)

        # Check that private title is anonymised to Draft #77
        self.assertNotIn("Top Secret AI Fitness Guide", serialized)
        self.assertIn("Draft #77", serialized)

        # Check no money pattern or currency signs
        self.assertNotIn("$99", serialized)
        self.assertNotIn("USD", serialized)


if __name__ == "__main__":
    unittest.main()
