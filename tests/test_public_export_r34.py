"""Public export contract, privacy and old-reader compatibility (temporary DB only)."""
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import export_status
import llm_router
import workforce


LEGACY_FIELDS = {"generated_at", "kill_switch", "llm", "agents", "task_counts",
                 "recent_tasks", "pending_approvals", "worker", "handoffs"}
NEW_PUBLIC_FIELDS = {"products", "reject_reasons", "report", "workers", "pipeline"}
PRIVATE_MARKERS = ("PRIVATE_FIXTURE_CREDENTIAL", "PRIVATE_FIXTURE_PROMPT",
                   "PRIVATE_FIXTURE_ERROR", "PRIVATE_FIXTURE_MODEL", "PRIVATE_FIXTURE_TITLE")


class PublicExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "public-test.db")
        self.output = os.path.join(self.tmp.name, "web", "data", "status.json")
        workforce.connect(self.db).close()
        self.network = self.enterContext(mock.patch("socket.socket.connect", side_effect=AssertionError("offline test")))
        self.remote = self.enterContext(mock.patch("always_on.status", side_effect=AssertionError("no remote lookup")))
        self.enterContext(mock.patch.object(export_status, "_llm_providers", return_value=[]))
        self.enterContext(mock.patch.object(export_status.guardrails, "is_killed", return_value=False))
        self.enterContext(mock.patch.object(export_status.guardrails, "CONFIG_PATH", os.path.join(self.tmp.name, "config.json")))
        self.enterContext(mock.patch.object(workforce, "CONFIG_PATH", os.path.join(self.tmp.name, "workforce.json")))
        self.today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE provider_log (day TEXT, provider TEXT, ok INTEGER, fail INTEGER, "
                         "model TEXT, credential TEXT, prompt TEXT, error TEXT, cost REAL, PRIMARY KEY(day, provider))")
            conn.execute("INSERT INTO provider_log VALUES (?, 'Groq', 1, 0, ?, ?, ?, ?, 98765.43)",
                         (self.today, PRIVATE_MARKERS[3], PRIVATE_MARKERS[0], PRIVATE_MARKERS[1], PRIVATE_MARKERS[2]))
            conn.execute("INSERT INTO provider_log VALUES (?, ?, 1, 0, NULL, NULL, NULL, NULL, NULL)",
                         (self.today, PRIVATE_MARKERS[0]))
            conn.execute("CREATE TABLE agent_logs (id INTEGER PRIMARY KEY, timestamp TEXT, agent_name TEXT, status TEXT, message TEXT)")
            conn.execute("INSERT INTO agent_logs VALUES (1, ?, 'test', 'failed', ?)",
                         (self.today + 'T12:00:00Z', PRIVATE_MARKERS[2]))
            conn.execute("INSERT INTO prompt_versions (name, text) VALUES ('private_prompt', ?)", (PRIVATE_MARKERS[1],))
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, review, status) VALUES (?, 'public_draft', ?, ?, ?, 'pending')",
                         (self.today + 'T12:00:00Z', PRIVATE_MARKERS[4], PRIVATE_MARKERS[1], PRIVATE_MARKERS[2]))
            conn.execute("CREATE TABLE campaigns (earnings REAL, source TEXT, status TEXT)")
            conn.execute("INSERT INTO campaigns VALUES (98765.43, 'real', 'DONE')")

    def tearDown(self):
        self.network.assert_not_called()
        self.remote.assert_not_called()

    def read_export(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            export_status.write_status(self.output, self.db)
        self.assertEqual(output.getvalue(), "")
        with open(self.output, encoding="utf-8") as handle:
            return json.load(handle)

    def test_exact_public_fields_and_utc_health_schema(self):
        status = self.read_export()
        expected = LEGACY_FIELDS | NEW_PUBLIC_FIELDS | {"ai_health", "minutes", "scout_ideas_count", "trend_items_count", "media_items_count", "cubes", "marketing_kits_count"}
        self.assertEqual(set(status), expected)
        self.assertEqual(set(status["minutes"]), {"used_this_month", "limit"})
        self.assertEqual({row["provider"] for row in status["ai_health"]}, {name for name, _ in llm_router._PROVIDERS})
        for row in status["ai_health"]:
            self.assertEqual(set(row), {"provider", "model", "state", "reason", "checked_at"})
            self.assertIn(row["state"], {"up", "down", "unknown"})
            self.assertIsNone(row["model"])
            self.assertEqual(row["checked_at"], status["generated_at"])
            self.assertRegex(row["checked_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(next(row["state"] for row in status["ai_health"] if row["provider"] == "Groq"), "up")

    def test_private_columns_logs_drafts_prompts_and_amounts_do_not_escape(self):
        status = self.read_export()
        serialized = json.dumps(status)
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, serialized)
        self.assertNotIn("98765.43", serialized)
        metadata = json.dumps({key: status[key] for key in ("ai_health", "minutes")}).lower()
        for forbidden in ("secret", "prompt", "error", "money", "cost", "api_key", "bearer", "credential"):
            self.assertNotIn(forbidden, metadata)
        for forbidden in ("money", "prompts", "logs", "validation", "service_requests", "built_tools", "llm_providers"):
            self.assertNotIn(forbidden, status)
        self.assertTrue(all(item["title"].startswith("Draft #") for item in status["pending_approvals"]))

    def test_old_readers_get_identical_original_values_with_extra_fields_only(self):
        legacy = export_status.build_status(self.db)
        before = json.loads(json.dumps(legacy))
        self.assertEqual(set(legacy), LEGACY_FIELDS)
        with mock.patch.object(export_status, "build_status", return_value=legacy):
            extended = self.read_export()
        self.assertEqual(legacy, before)
        self.assertEqual({key: extended[key] for key in LEGACY_FIELDS}, before)
        self.assertEqual(set(extended) - set(legacy), NEW_PUBLIC_FIELDS | {"ai_health", "minutes", "scout_ideas_count", "trend_items_count", "media_items_count", "cubes", "marketing_kits_count"})
        # Existing consumers can still use the agent list, counts and approval queue.
        self.assertEqual([agent["name"] for agent in extended["agents"]],
                         [agent["name"] for agent in legacy["agents"]])
        self.assertEqual(len(extended["pending_approvals"]), len(legacy["pending_approvals"]))

    def test_health_read_is_read_only_and_missing_columns_are_unknown(self):
        before = Path(self.db).read_bytes()
        export_status._ai_health(self.db)
        self.assertEqual(Path(self.db).read_bytes(), before)
        with sqlite3.connect(self.db) as conn:
            conn.execute("ALTER TABLE provider_log RENAME COLUMN fail TO old_fail")
        rows = export_status._ai_health(self.db)
        self.assertEqual(len(rows), len(export_status.PUBLIC_AI_PROVIDERS))
        self.assertTrue(all(row["state"] == "unknown" for row in rows))


if __name__ == "__main__":
    unittest.main()
