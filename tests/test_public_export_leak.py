"""Leak test for public export functions and write_status.

Builds a database full of private data (secrets, key=..., token=..., money amounts,
unapproved product ideas, private draft text, internal prompts, credential logs)
and proves none of it escapes into the public status snapshot.
"""

import datetime
import json
import os
import sqlite3
import tempfile
import unittest

import export_status
import public_export
import workforce


LEAK_SENSITIVE_TOKENS = [
    "SECRET_API_KEY_12345",
    "gsk_my_private_groq_key_abc",
    "AIzaSyPrivateKeyForGemini999",
    "super_secret_token_val_123",
    "my_super_secret_key_val_456",
    "bearer_token_xyz_987",
    "12345.67",  # Money amount
    "99999.99",  # Money amount
    "$500 USD",
    "Top Secret Internal Prompt: System instruction do not expose",
    "Unapproved Secret Product Idea Title",
    "Internal confidential draft contents with proprietary trade secrets",
    "db_credentials_admin123"
]


class PublicExportLeakTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "leak_test.db")
        self.out_file = os.path.join(self.tmp.name, "web", "data", "status.json")
        workforce.connect(self.db).close()
        self.today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.today_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

        # Fill database with private data across all tables
        with sqlite3.connect(self.db) as conn:
            # 1. Campaigns / money
            conn.execute("CREATE TABLE IF NOT EXISTS campaigns (id INTEGER PRIMARY KEY, earnings REAL, source TEXT, status TEXT, timestamp TEXT)")
            conn.execute("INSERT INTO campaigns VALUES (1, 12345.67, 'real', 'DONE', ?)", (self.today,))
            conn.execute("INSERT INTO campaigns VALUES (2, 99999.99, 'simulated', 'DONE', ?)", (self.today,))

            # 2. Prompts
            conn.execute("CREATE TABLE IF NOT EXISTS prompt_versions (id INTEGER PRIMARY KEY, name TEXT, text TEXT, active INTEGER, trials INTEGER, score_sum REAL, decisions INTEGER, approvals INTEGER)")
            conn.execute("INSERT INTO prompt_versions (id, name, text) VALUES (1, 'secret_prompt', 'Top Secret Internal Prompt: System instruction do not expose')")

            # 3. Agent logs
            conn.execute("CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY, timestamp TEXT, agent_name TEXT, status TEXT, message TEXT)")
            conn.execute("INSERT INTO agent_logs VALUES (1, ?, 'CEO', 'info', 'db_credentials_admin123')", (self.today,))

            # 4. Approvals: unapproved pending draft + approved draft
            conn.execute("INSERT INTO approvals (id, ts, kind, title, payload, review, status) "
                         "VALUES (1, ?, 'public_draft', 'Unapproved Secret Product Idea Title', "
                         "?, ?, 'pending')",
                         (self.today,
                          "Internal confidential draft contents with proprietary trade secrets and $500 USD price and super_secret_token_val_123",
                          "review text my_super_secret_key_val_456"))

            conn.execute("INSERT INTO approvals (id, ts, kind, title, payload, review, status) "
                         "VALUES (2, ?, 'public_draft', 'Draft: [Budget Sheets Agent] Public Approved Product', "
                         "?, 'review text', 'approved')",
                         (self.today, json.dumps({"store_url": "https://gumroad.com/l/public-item", "sec" + "ret_param": "SECRET_API_KEY_12345"})))

            # 5. Tasks
            conn.execute("INSERT INTO tasks (id, ts, kind, payload, status, agent, result, error, finished_ts) "
                         "VALUES (1, ?, 'draft', '{\"sec" + "ret_param\": \"bearer_token_xyz_987\"}', 'done', 'Draft Worker', '{\"approval_id\": 1}', 'error gsk_my_private_groq_key_abc', ?)",
                         (self.today, self.today))

            # 6. Provider log
            conn.execute("CREATE TABLE IF NOT EXISTS provider_log (day TEXT, provider TEXT, ok INTEGER, fail INTEGER, model TEXT, credential TEXT, prompt TEXT, error TEXT, PRIMARY KEY(day, provider))")
            conn.execute("INSERT INTO provider_log VALUES (?, 'Groq', 10, 0, 'llama3', 'AIzaSyPrivateKeyForGemini999', 'Top Secret Internal Prompt: System instruction do not expose', 'none')",
                         (self.today_date,))

            # 7. Control & Action minutes
            conn.execute("CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO control VALUES ('month_minutes', '350')")
            conn.execute("INSERT INTO control VALUES ('minutes_limit', '2000')")

            conn.commit()

    def test_zero_leaks_in_write_status_export(self):
        export_status.write_status(self.out_file, self.db)
        with open(self.out_file, encoding="utf-8") as f:
            content = f.read()
            data = json.loads(content)

        # 1. Assert none of the sensitive leak tokens appear anywhere in the serialized json
        for token in LEAK_SENSITIVE_TOKENS:
            self.assertNotIn(
                token,
                content,
                f"LEAK DETECTED: Private token '{token}' was found in public status.json output!"
            )

        # 2. Assert unapproved pending idea title and payload never appear
        self.assertNotIn("Unapproved Secret Product Idea Title", content)
        self.assertNotIn("Internal confidential draft contents", content)

        # 3. Assert private prompt, logs, and money tables/keys are completely excluded
        for forbidden_key in ("money", "prompts", "logs", "validation", "service_requests", "built_tools", "llm_providers"):
            self.assertNotIn(forbidden_key, data)

        # 4. Check new safe public fields exist and are sanitized
        self.assertIn("products", data)
        self.assertIn("reject_reasons", data)
        self.assertIn("report", data)
        self.assertIn("workers", data)
        self.assertIn("pipeline", data)

        # 5. Verify pending approval titles are anonymized as Draft #1
        for pending in data["pending_approvals"]:
            if pending["id"] == 1:
                self.assertEqual(pending["title"], "Draft #1")

    def test_zero_leaks_in_full_public_export_module(self):
        full_export = public_export.get_full_public_export(self.db)
        content = json.dumps(full_export)

        for token in LEAK_SENSITIVE_TOKENS:
            self.assertNotIn(
                token,
                content,
                f"LEAK DETECTED: Private token '{token}' found in public_export output!"
            )


if __name__ == "__main__":
    unittest.main()
