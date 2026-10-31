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
