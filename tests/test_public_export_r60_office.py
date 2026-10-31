"""Office projections, caps and privacy using temporary databases and git repos."""
import datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

import export_status
import public_export
import workforce


FIELDS = {"agent_budgets", "loop_events", "activity_events", "schedules", "receipts",
          "workspaces", "cloud_heartbeat", "changelog"}
NOW = "2026-10-31T12:00:00Z"
PRIVATE = "PRIVATE_R60_INSTRUCTIONS_AND_UNAPPROVED_IDEA"


class OfficeExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "office.db")
        workforce.connect(self.db).close()
        self.enterContext(mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}))
        self.enterContext(mock.patch("socket.socket.connect", side_effect=AssertionError("offline only")))
        self.enterContext(mock.patch.object(public_export.guardrails, "CONFIG_PATH", str(Path(self.tmp.name) / "config.json")))
        self.enterContext(mock.patch.object(workforce, "CONFIG_PATH", str(Path(self.tmp.name) / "workforce.json")))
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE agent_usage(day TEXT, agent TEXT, calls_used, calls_limit, context_percent);
                CREATE TABLE doctor_cases(id INTEGER PRIMARY KEY, agent TEXT, category TEXT, ts TEXT,
                                          error_message TEXT, diagnosis TEXT);
                CREATE TABLE office_missions(id TEXT PRIMARY KEY, title TEXT, every TEXT,
                                             next_run REAL, status TEXT, updated REAL);
                CREATE TABLE office_triggers(id TEXT PRIMARY KEY, definition TEXT, archived INTEGER,
                                             last_fired REAL, updated REAL);
                CREATE TABLE office_requests(id TEXT PRIMARY KEY, receipt TEXT, nonce TEXT, digest TEXT);
            """)

    def task(self, ident, status="done", attempts=1, stamp=NOW, agent="Draft Worker"):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO tasks(id,ts,kind,status,agent,attempts,payload,result,error) "
                         "VALUES (?,?,'draft',?,?,?,?,?,?)",
                         (ident, stamp, status, agent, attempts, PRIVATE, PRIVATE, PRIVATE))

    def test_agent_budget_today_observed_and_unknown_context(self):
        with sqlite3.connect(self.db) as conn:
            conn.executemany("INSERT INTO agent_usage VALUES (?,?,?,?,?)", [
                ("2026-10-30", "Draft Worker", 999, 999, 90),
                (NOW[:10], "Draft Worker", 7, 25, 37.5),
                (NOW[:10], "CEO", -1, "private", 101)])
            conn.execute("INSERT INTO usage_log VALUES (?,999)", (NOW[:10],))
        rows = public_export.get_public_agent_budgets(self.db, NOW)
        writer = next(row for row in rows if row["agent"] == "Draft Worker")
        self.assertEqual(writer, {"agent": "Draft Worker", "calls_used_today": 7,
                                  "calls_limit_today": 25, "context_percent": 37.5})
        ceo = next(row for row in rows if row["agent"] == "CEO")
        self.assertEqual([ceo[key] for key in ("calls_used_today", "calls_limit_today", "context_percent")], [None]*3)
        self.assertTrue(all(row["calls_used_today"] is None for row in rows if row["agent"] != "Draft Worker"))
        self.assertLessEqual(len(rows), 20)

    def test_loop_and_activity_caps_latest_first_fixed_labels_and_ids(self):
        base = datetime.datetime.fromisoformat(NOW.replace("Z", "+00:00"))
        for ident in range(1, 251):
            self.task(ident, "failed", 3 if ident % 2 else 1,
                      (base + datetime.timedelta(seconds=ident)).isoformat())
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO doctor_cases VALUES (1, 'CEO', 'loop_limit', ?, ?, ?)",
                         ((base + datetime.timedelta(seconds=300)).isoformat(), PRIVATE, PRIVATE))
        loops = public_export.get_public_loop_events(self.db)
        self.assertEqual(len(loops), 50)
        self.assertEqual(loops[0]["label"], "Loop prevention check")
        self.assertEqual({row["kind"] for row in loops}, {"loop", "error"})
        self.assertTrue(all(set(row) == {"agent", "kind", "label", "at"} for row in loops))
        events = public_export.get_public_activity_events(self.db)
        self.assertEqual(len(events), 200)
        self.assertEqual(events[0]["id"], "task:250")
        self.assertEqual(events[-1]["id"], "task:51")
        self.assertTrue(all(set(row) == {"id", "agent", "label", "at"} for row in events))
        self.assertNotIn(PRIVATE, json.dumps([loops, events]))
        self.assertEqual(events, public_export.get_public_activity_events(self.db))

    def test_schedule_definitions_no_titles_labels_or_prompts(self):
        due = datetime.datetime.fromisoformat(NOW.replace("Z", "+00:00")).timestamp()
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO office_missions VALUES ('mission-1',?,'1d',?,'active',1)", (PRIVATE, due))
            definitions = [
                {"kind": "at", "at": NOW},
                {"kind": "task_done", "watch_id": "task:3"},
                {"kind": "agent_state", "watch_id": "chilli-gavva-robot-ceo", "match_state": "idle"},
                {"kind": "state", "condition": {"metric": "failed_tasks", "op": ">=", "value": 3}}]
            for ident, definition in enumerate(definitions):
                definition.update(label=PRIVATE, prompt=PRIVATE, enabled=True)
                conn.execute("INSERT INTO office_triggers VALUES (?,?,0,NULL,?)", (f"trigger-{ident}", json.dumps(definition), ident+2))
        rows = public_export.get_public_schedules(self.db)
        mission = next(row for row in rows if row["id"] == "mission-1")
        self.assertEqual(mission, {"id": "mission-1", "name": "Recurring mission", "cadence": "1d",
                                   "next_run": NOW, "state": "active"})
        triggers = {row["id"]: row for row in rows if "condition" in row}
        self.assertEqual(triggers["trigger-0"]["next_run"], NOW)
        self.assertEqual(triggers["trigger-1"]["condition"], "task:3 completed")
        self.assertEqual(triggers["trigger-2"]["condition"], "CEO is idle")
        self.assertEqual(triggers["trigger-3"]["condition"], "failed_tasks >= 3")
        self.assertIsNone(triggers["trigger-3"]["next_run"])
        self.assertNotIn(PRIVATE, json.dumps(rows))

    def test_combined_schedule_cap_and_inactive_or_fired_times(self):
        with sqlite3.connect(self.db) as conn:
            for ident in range(120):
                conn.execute("INSERT INTO office_missions VALUES (?,?,'1h',0,'archived',?)", (f"m{ident}", PRIVATE, ident))
                conn.execute("INSERT INTO office_triggers VALUES (?,?,0,1,?)",
                             (f"t{ident}", json.dumps({"kind": "at", "at": NOW, "enabled": True}), ident+200))
        rows = public_export.get_public_schedules(self.db)
        self.assertEqual(len(rows), 100)
        self.assertTrue(all(row["id"].startswith("t") and row["next_run"] is None for row in rows))

    def test_receipts_newest_cap_allowlisted_outcomes_without_reply_signature(self):
        with sqlite3.connect(self.db) as conn:
            for ident in range(70):
                value = {"state": "applied", "at": f"2026-10-31T12:00:{ident % 60:02d}Z",
                         "reason": PRIVATE, "reply": PRIVATE, "signature": PRIVATE, "request_signature": PRIVATE}
                if ident == 59:
                    value["type"] = "add_mission"
                conn.execute("INSERT INTO office_requests VALUES (?,?,?,?)", (f"r{ident}", json.dumps(value), PRIVATE, PRIVATE))
        rows = public_export.get_public_receipts(self.db)
        self.assertEqual(len(rows), 50)
        self.assertEqual(rows[0], {"id": "r59", "type": "add_mission", "state": "applied",
                                  "safe_reason": "Request applied", "at": "2026-10-31T12:00:59Z"})
        self.assertTrue(any(row["type"] == "unknown" for row in rows))
        self.assertNotIn(PRIVATE, json.dumps(rows))

    def test_workspaces_projection_cap_and_unknown_floor(self):
        registry = [{"id": f"cube-{i}", "name": "Department", "floor": "Main", "prompt": PRIVATE} for i in range(60)]
        registry[0].pop("floor")
        with mock.patch("cubes.load_registry", return_value=registry):
            rows = public_export.get_public_workspaces()
        self.assertEqual(len(rows), 50)
        self.assertEqual(rows[0], {"id": "cube-0", "name": "Department", "floor": None})
        self.assertEqual(rows[1]["floor"], "Main")
        self.assertNotIn(PRIVATE, json.dumps(rows))

    def test_cloud_heartbeat_requires_hosted_actions_and_valid_run_id(self):
        empty = {"last_run_start": None, "last_run_end": None, "runner": None, "run_id": None}
        self.assertEqual(public_export.get_public_cloud_heartbeat(NOW), empty)
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "GITHUB_RUN_ID": "12345"}), mock.patch.object(public_export, "_EXPORT_PROCESS_STARTED_AT", "2026-10-31T11:59:00Z"):
            self.assertEqual(public_export.get_public_cloud_heartbeat(NOW),
                             {"last_run_start": "2026-10-31T11:59:00Z", "last_run_end": NOW,
                              "runner": "github-actions", "run_id": "12345"})
            with mock.patch.dict(os.environ, {"RUNNER_ENVIRONMENT": "self-hosted"}):
                self.assertEqual(public_export.get_public_cloud_heartbeat(NOW), empty)
            with mock.patch.dict(os.environ, {"GITHUB_RUN_ID": PRIVATE}):
                self.assertEqual(public_export.get_public_cloud_heartbeat(NOW), empty)

    def test_changelog_uses_local_git_subjects_only_newest_20(self):
        repo = Path(self.tmp.name) / "repo"
        repo.mkdir()
        def git(*args):
            return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
        git("init", "-q")
        for ident in range(23):
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", f"Change {ident}\n\n{PRIVATE}")
        rows = public_export.get_public_changelog(repo)
        self.assertEqual(rows, [f"Change {i}" for i in range(22, 2, -1)])
        self.assertNotIn(PRIVATE, json.dumps(rows))
        self.assertNotIn("@", json.dumps(rows))
        self.assertEqual(public_export.get_public_changelog(Path(self.tmp.name) / "missing"), [])

    def test_all_office_readers_are_read_only_and_missing_db_stays_missing(self):
        self.task(1)
        before = Path(self.db).read_bytes()
        public_export.get_public_office_export(self.db, NOW)
        self.assertEqual(Path(self.db).read_bytes(), before)
        missing = str(Path(self.tmp.name) / "missing.db")
        rows = public_export.get_public_office_export(missing, NOW)
        self.assertFalse(Path(missing).exists())
        self.assertEqual(rows["activity_events"], [])
        self.assertEqual(rows["schedules"], [])
        self.assertTrue(all(row["context_percent"] is None for row in rows["agent_budgets"]))

    def test_event_times_normalize_offsets_and_sort_fractional_seconds(self):
        self.task(1, "failed", stamp="2026-10-31T12:00:00Z")
        self.task(2, "failed", stamp="2026-10-31T17:30:00.500+05:30")
        rows = public_export.get_public_activity_events(self.db)
        self.assertEqual([row["id"] for row in rows], ["task:2", "task:1"])
        self.assertEqual(rows[0]["at"], "2026-10-31T12:00:00.500000Z")
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO doctor_cases VALUES (1,'CEO','error',?,NULL,NULL)", (NOW,))
        loops = public_export.get_public_loop_events(self.db)
        self.assertEqual(loops[0]["at"], "2026-10-31T12:00:00.500000Z")

    def test_final_projection_scrubs_all_nested_strings_and_caps_legacy_lists(self):
        raw = "key=fixture-final-value " + "fixture-owner@private.invalid $123"
        with mock.patch.dict(os.environ, {"R60_FIXTURE_SECRET": "fixture-exact-value"}):
            clean = public_export.scrub_public_data({"nested": [[raw, "fixture-exact-value"]] * 250})
        self.assertEqual(len(clean["nested"]), 200)
        serialized = json.dumps(clean)
        for private in ("fixture-final-value", "fixture-owner@", "$123", "fixture-exact-value"):
            self.assertNotIn(private, serialized)
        with mock.patch.object(public_export, "get_public_products", return_value=[{"store_url": raw}]):
            full = public_export.get_full_public_export(self.db, NOW)
        self.assertNotIn("fixture-final-value", json.dumps(full))

    def test_scrubs_credentials_emails_currency_before_truncating_every_string(self):
        sensitive = 'key=fixture-key token="fixture token value" Bearer fixture-bearer '
        sensitive += 'writer@private.invalid USD 1,234.50 42 INR $9.99 ₹800 €2 £4'
        clean = public_export.scrub_text(sensitive)
        for marker in ("fixture-key", "fixture token value", "fixture-bearer", "writer@", "1,234.50", "42", "9.99", "800", "€2", "£4"):
            self.assertNotIn(marker, clean)
        registry = [{"id": sensitive, "name": sensitive, "floor": sensitive}]
        with mock.patch("cubes.load_registry", return_value=registry), mock.patch.object(public_export.subprocess, "run", return_value=mock.Mock(stdout=sensitive)):
            serialized = json.dumps(public_export.get_public_office_export(self.db, NOW))
        self.assertNotIn("fixture-key", serialized)
        self.assertNotIn("fixture-bearer", serialized)
        self.assertNotIn("writer@", serialized)

    def test_database_full_of_private_content_never_leaks_to_status_or_full_export(self):
        self.task(1, "failed", 4)
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO approvals(ts,kind,title,payload,review,status) VALUES (?,'public_draft',?,?,?,'pending')", (NOW, PRIVATE, PRIVATE, PRIVATE))
            conn.execute("INSERT INTO prompt_versions(name,text) VALUES (?,?)", (PRIVATE, PRIVATE))
            conn.execute("INSERT INTO office_missions VALUES ('m1',?,'1d',0,'active',0)", (PRIVATE,))
            conn.execute("INSERT INTO office_triggers VALUES ('t1',?,0,NULL,0)",
                         (json.dumps({"kind": "at", "at": NOW, "label": PRIVATE, "prompt": PRIVATE, "enabled": True}),))
            conn.execute("INSERT INTO office_requests VALUES ('r1',?,?,?)", (json.dumps({"at": NOW, "state": "held", "reason": PRIVATE, "reply": PRIVATE, "signature": PRIVATE}), PRIVATE, PRIVATE))
            conn.execute("INSERT INTO doctor_cases VALUES (1,'CEO','error',?,?,?)", (NOW, PRIVATE, PRIVATE))
        output = str(Path(self.tmp.name) / "web" / "status.json")
        with mock.patch.object(export_status, "_llm_providers", return_value=[]):
            export_status.write_status(output, self.db)
            status = json.loads(Path(output).read_text())
            full = public_export.get_full_public_export(self.db, NOW)
        self.assertTrue(FIELDS <= set(status))
        self.assertTrue(FIELDS <= set(full))
        for document in (status, full):
            serialized = json.dumps(document)
            self.assertNotIn(PRIVATE, serialized)
            for forbidden in ("prompt", "reply", "signature", "nonce", "digest", "error_message", "payload"):
                self.assertNotIn('"' + forbidden + '"', serialized)

    def test_malformed_optional_rows_fail_closed(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO office_requests VALUES ('r1','not json','x','x')")
            conn.execute("INSERT INTO office_requests VALUES ('r2',?,'x','x')", (json.dumps({"at": NOW, "state": [], "type": {}}),))
            conn.execute("INSERT INTO office_triggers VALUES ('t1','[]',0,NULL,0)")
            conn.execute("INSERT INTO office_triggers VALUES ('t2','broken',0,NULL,0)")
        self.assertEqual(public_export.get_public_schedules(self.db), [])
        self.assertEqual(public_export.get_public_receipts(self.db)[0]["state"], "unknown")
        self.task(1, stamp=PRIVATE, agent=PRIVATE)
        self.assertEqual(public_export.get_public_activity_events(self.db), [])
        self.assertIsNone(public_export._iso(float("inf")))


if __name__ == "__main__":
    unittest.main()
