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



    def test_daily_report_uses_ist_date_capped_generic_labels_and_no_private_prose(self):
        private = "PRIVATE_IDEA prompt key=fixture-report-value $123"
        with sqlite3.connect(self.db) as conn:
            # 18:30 UTC is the start of the following IST day.
            for i in range(12):
                conn.execute("INSERT INTO tasks(ts,finished_ts,kind,status,payload,result) VALUES (?,?,?,'done',?,?)",
                             ("2026-10-31T18:30:00Z", "2026-10-31T18:30:00Z", private if i == 0 else "draft", private, private))
            for stamp in ("2026-10-31T18:29:59Z", "2026-11-01T01:00:00Z"):
                conn.execute("INSERT INTO tasks(ts,finished_ts,kind,status) VALUES (?,?,'draft','done')", (stamp, stamp))
        report = public_export.get_public_office_reports(self.db, "2026-10-31T18:30:01Z")
        self.assertEqual(report["daily_report"]["date_ist"], "2026-11-01")
        self.assertEqual(report["daily_report"]["summary"], "12 tasks completed; 0 approvals reviewed; 0 products built.")
        self.assertEqual(len(report["daily_report"]["items_done"]), 10)
        self.assertTrue(all(label in ("Draft completed", "Task completed") for label in report["daily_report"]["items_done"]))
        self.assertEqual(report["sunday_letter"]["date_ist"], "2026-11-01")
        self.assertNotIn(private, json.dumps(report))
        self.assertNotIn("fixture-report-value", json.dumps(report))
        self.assertNotIn("$", json.dumps(report))
        self.assertEqual(public_export.get_public_office_reports(self.db, NOW)["daily_report"]["items_done"], [])
        self.assertIsNone(public_export.get_public_office_reports(self.db, NOW)["sunday_letter"])

    def test_reports_count_reviews_and_builds_and_sunday_week_read_only(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO approvals(ts,kind,title,payload,status,decided_ts) "
                         "VALUES ('2026-10-31','public_draft','private idea','private prompt','approved','2026-11-01T00:00:00Z')")
            conn.execute("CREATE TABLE product_builds(id INTEGER PRIMARY KEY, status, ts, folder)")
            conn.execute("INSERT INTO product_builds VALUES (1,'built','2026-11-01T00:00:00Z','private folder')")
            conn.execute("INSERT INTO tasks(ts,finished_ts,kind,status) VALUES ('2026-10-29','2026-10-29','research','done')")
        before = Path(self.db).read_bytes()
        reports = public_export.get_public_office_reports(self.db, "2026-11-01T12:00:00Z")
        self.assertEqual(Path(self.db).read_bytes(), before)
        self.assertEqual(reports["daily_report"]["items_done"], ["Approval reviewed", "Product built"])
        self.assertEqual(reports["sunday_letter"]["summary"], "1 tasks completed; 1 approvals reviewed; 1 products built.")
        with mock.patch.object(export_status, "build_status", return_value={"generated_at": "2026-11-01T12:00:00Z"}):
            document = self.status()
        self.assertEqual({key: document[key] for key in reports}, reports)
        missing = str(self.root / "missing.db")
        self.assertEqual(public_export.get_public_office_reports(missing, NOW)["daily_report"]["items_done"], [])
        self.assertFalse(Path(missing).exists())

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

    def test_model_health_uses_latest_real_observation_without_daily_tally(self):
        import llm_provider_state
        at = 1793448000  # NOW, 2026-10-31 12:00 UTC
        with mock.patch.object(llm_provider_state.time, "time", return_value=at - 120):
            llm_provider_state.observe("Groq", "openai/gpt-oss-120b", True, path=self.db)
        with mock.patch.object(llm_provider_state.time, "time", return_value=at - 60):
            llm_provider_state.observe("Groq", "openai/gpt-oss-20b", False, "rate_limit", path=self.db)
            llm_provider_state.observe("Gemini", "gemini-3.8-flash", True, path=self.db)
        before = Path(self.db).read_bytes()
        rows = {row["provider"]: row for row in public_export.get_public_ai_health(self.db, NOW)}
        self.assertEqual(Path(self.db).read_bytes(), before)
        self.assertEqual((rows["Groq"]["model"], rows["Groq"]["state"]), ("openai/gpt-oss-20b", "down"))
        self.assertEqual(rows["Groq"]["reason"], "Provider request rate exceeded")
        self.assertEqual(rows["Gemini"]["model"], "gemini-3.8-flash")
        self.assertIsNone(rows["Cloudflare"]["model"])
        with mock.patch.object(export_status, "build_status", return_value={"generated_at": NOW}):
            self.assertEqual(self.status()["ai_health"], list(rows.values()))

    def test_health_rejects_private_malformed_future_models_and_marks_stale_observations(self):
        import llm_provider_state
        at = 1793448000
        private_model = "fixture-private-model-value"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE llm_model_health(provider,model,data)")
            conn.executemany("INSERT INTO llm_model_health VALUES (?,?,?)", [
                ("Groq", "openai/gpt-oss-20b", json.dumps({"observed_at": at - 90000, "last_ok": True})),
                ("Groq", private_model, json.dumps({"observed_at": at - 1, "last_ok": True})),
                ("Groq", "future", json.dumps({"observed_at": at + 1, "last_ok": True})),
                ("Gemini", "private prompt with spaces", json.dumps({"observed_at": at, "last_ok": True})),
                ("Gemini", "broken", "[]"), ("Gemini", "broken", "not JSON"),
                ("Gemini", "broken", json.dumps({"observed_at": "private", "last_ok": True})),
                ("Cloudflare", "broken", json.dumps({"observed_at": at, "last_ok": "private"})),
                ("unknown", "private", json.dumps({"observed_at": at, "last_ok": True})),
            ])
        with mock.patch.dict(os.environ, {"R69_MODEL_SECRET": private_model}):
            rows = {row["provider"]: row for row in public_export.get_public_ai_health(self.db, NOW)}
        self.assertEqual(rows["Groq"]["model"], "openai/gpt-oss-20b")
        self.assertEqual(rows["Groq"]["state"], "unknown")
        self.assertIsNone(rows["Gemini"]["model"])
        self.assertNotIn(private_model, json.dumps(rows))
