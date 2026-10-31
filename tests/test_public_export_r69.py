"""Office export regressions exercise real writers and public JSON, offline."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import export_status
import guardrails
import public_export
import workforce


NOW = "2026-10-31T12:00:00Z"


class OfficeExportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "state.db")
        workforce.connect(self.db).close()
        self.enterContext(mock.patch.object(guardrails, "CONFIG_PATH", str(self.root / "no-config.json")))
        self.enterContext(mock.patch.object(workforce, "CONFIG_PATH", str(self.root / "no-workforce.json")))
        self.enterContext(mock.patch.object(public_export, "get_public_changelog", return_value=[]))
        self.enterContext(mock.patch("socket.socket.connect", side_effect=AssertionError("offline only")))

    def status(self):
        path = self.root / "web" / "status.json"
        export_status.write_status(str(path), self.db)
        return json.loads(path.read_text())

    def test_connection_names_survive_final_scrub_without_credential_values(self):
        secrets = {name: "private-connection-value-" + str(i)
                   for i, name in enumerate(public_export.PUBLIC_CONNECTION_KEYS)}
        with mock.patch.dict(os.environ, secrets):
            documents = [self.status(), public_export.get_full_public_export(self.db)]
            for document in documents:
                connection = document["agent_connection"]
                self.assertEqual(connection, public_export.get_public_agent_connection())
                self.assertEqual(connection["required_keys"], list(public_export.PUBLIC_CONNECTION_KEYS))
                self.assertEqual(connection["engines"], ["local", "cloud"])
                for secret in secrets.values():
                    self.assertNotIn(secret, json.dumps(document))
            clean = public_export.scrub_public_data({"agent_connection": {"token": secrets["CF_API_TOKEN"]}})
            self.assertEqual(clean["agent_connection"], public_export.get_public_agent_connection())

    def test_shared_budget_uses_guardrails_records_and_owner_cap_read_only(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO usage_log VALUES (?,17)", (NOW[:10],))
            conn.execute("INSERT INTO usage_log VALUES ('2026-10-30',99)")
            conn.execute("INSERT INTO control VALUES ('office_daily_call_cap','30')")
        before = Path(self.db).read_bytes()
        rows = public_export.get_public_agent_budgets(self.db, NOW)
        self.assertEqual(Path(self.db).read_bytes(), before)
        for row in rows:
            self.assertEqual((row["calls_used_today"], row["calls_limit_today"], row["scope"]), (17, 30, "shared"))
            self.assertIsNone(row["context_percent"])
        with mock.patch.object(guardrails, "configured_daily_cap", return_value=20):
            self.assertTrue(all(row["calls_limit_today"] == 20
                                for row in public_export.get_public_agent_budgets(self.db, NOW)))

    def test_empty_day_zero_bad_cap_fails_closed_and_missing_ledger_is_unknown(self):
        rows = public_export.get_public_agent_budgets(self.db, NOW)
        self.assertTrue(all(row["calls_used_today"] == 0 for row in rows))
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO control VALUES ('office_daily_call_cap','invalid')")
        self.assertTrue(all(row["calls_limit_today"] == 0
                            for row in public_export.get_public_agent_budgets(self.db, NOW)))
        missing = str(self.root / "missing.db")
        rows = public_export.get_public_agent_budgets(missing, NOW)
        self.assertTrue(all(row["calls_used_today"] is None and row["calls_limit_today"] is None for row in rows))
        self.assertFalse(Path(missing).exists())

    def test_recorded_agent_budget_overrides_shared_ledger(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO usage_log VALUES (?,100)", (NOW[:10],))
            conn.execute("CREATE TABLE agent_usage(day,agent,calls_used,calls_limit,context_percent)")
            conn.execute("INSERT INTO agent_usage VALUES (?, 'chilli-gavva-robot-draft-worker',4,12,42.5)", (NOW[:10],))
        row = next(row for row in public_export.get_public_agent_budgets(self.db, NOW) if row["agent"] == "Draft Worker")
        self.assertEqual((row["calls_used_today"], row["calls_limit_today"], row["context_percent"], row["scope"]),
                         (4, 12, 42.5, "agent"))
