"""Offline public health export from the router's existing daily observations."""
import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import export_status
import llm_router
import workforce


class AIHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "test.db")
        workforce.connect(self.db).close()
        self.network = self.enterContext(mock.patch("socket.socket.connect", side_effect=AssertionError("offline test")))
        self.enterContext(mock.patch.object(export_status, "_llm_providers", return_value=[]))
        self.enterContext(mock.patch.object(export_status.guardrails, "is_killed", return_value=False))
        self.enterContext(mock.patch.object(export_status.guardrails, "CONFIG_PATH", os.path.join(self.tmp.name, "config.json")))
        self.enterContext(mock.patch.object(workforce, "CONFIG_PATH", os.path.join(self.tmp.name, "workforce.json")))

    def tearDown(self):
        self.network.assert_not_called()

    def record(self, rows):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE provider_log (day TEXT, provider TEXT, ok INTEGER, fail INTEGER, PRIMARY KEY(day, provider))")
            conn.executemany("INSERT INTO provider_log VALUES (?, ?, ?, ?)", rows)

    def health(self):
        return {item["provider"]: item for item in export_status._ai_health(self.db, "2026-10-23T12:00:00Z")}

    def test_router_records_success_failure_and_mixed_outcomes(self):
        llm_router.log_provider("Groq", True, self.db)
        llm_router.log_provider("Gemini", False, self.db)
        llm_router.log_provider("OpenRouter", True, self.db)
        llm_router.log_provider("OpenRouter", False, self.db)
        rows = {item["provider"]: item for item in export_status._ai_health(self.db)}
        self.assertEqual(rows["Groq"]["state"], "up")
        self.assertEqual(rows["Gemini"]["state"], "down")
        self.assertEqual(rows["OpenRouter"]["state"], "unknown")
        for row in rows.values():
            self.assertIsNone(row["model"])
            self.assertEqual(set(row), {"provider", "model", "state", "reason", "checked_at"})
            stamp = datetime.datetime.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
            self.assertEqual(stamp.utcoffset(), datetime.timedelta(0))

    def test_latest_recorded_day_wins_not_lifetime_totals(self):
        self.record([("2026-10-20", "Groq", 100, 0), ("2026-10-22", "Groq", 0, 1),
                     ("2026-10-20", "Gemini", 0, 100), ("2026-10-22", "Gemini", 1, 0),
                     ("2026-10-20", "Cloudflare", 1, 0), ("2026-10-22", "Cloudflare", 2, 1)])
        rows = self.health()
        self.assertEqual(rows["Groq"]["state"], "down")
        self.assertEqual(rows["Gemini"]["state"], "up")
        self.assertEqual(rows["Cloudflare"]["state"], "unknown")

    def test_missing_table_database_and_invalid_counts_stay_unknown(self):
        self.assertTrue(all(row["state"] == "unknown" for row in self.health().values()))
        missing = os.path.join(self.tmp.name, "missing.db")
        self.assertTrue(all(row["state"] == "unknown" for row in export_status._ai_health(missing)))
        self.assertFalse(os.path.exists(missing))
        self.record([("2026-10-22", "Groq", -1, 0), ("2026-10-22", "Gemini", "bad", 0),
                     ("2026-10-22", "Cloudflare", 0, 0), ("invalid", "OpenRouter", 1, 0),
                     ("2099-01-01", "GitHubModels", 1, 0)])
        self.assertTrue(all(row["state"] == "unknown" for row in self.health().values()))

    def test_public_file_adds_health_without_changing_legacy_fields(self):
        llm_router.log_provider("Groq", True, self.db)
        target = os.path.join(self.tmp.name, "web", "data", "status.json")
        legacy = export_status.build_status(self.db)
        with mock.patch.object(export_status, "build_status", return_value=legacy):
            export_status.write_status(target, self.db)
        with open(target, encoding="utf-8") as handle:
            status = json.load(handle)
        self.assertEqual({key: status[key] for key in legacy}, legacy)
        self.assertNotIn("ai_health", legacy)
        self.assertEqual(len(status["ai_health"]), len(export_status.PUBLIC_AI_PROVIDERS))
        self.assertTrue(all(row["checked_at"] == status["generated_at"] for row in status["ai_health"]))


if __name__ == "__main__":
    unittest.main()
