"""Tests for safe public export fields in public_export.py and export_status.py."""
import datetime
import json
import os
import sqlite3
import tempfile
import unittest

import export_status
import guardrails
import public_export
import workforce


class TestPublicExportFields(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "test.db")
        self.out_dir = os.path.join(self.tmp.name, "exported_content", "products")
        os.makedirs(self.out_dir, exist_ok=True)
        workforce.connect(self.db).close()
        self.today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.today_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    def test_products_export_owner_approved_only(self):
        conn = sqlite3.connect(self.db)
        # Pending draft (unapproved idea - MUST NOT be in products)
        conn.execute("INSERT INTO approvals (id, ts, kind, title, payload, status) "
                     "VALUES (1, ?, 'public_draft', 'Pending Secret Idea', 'Secret payload', 'pending')", (self.today,))
        # Approved draft
        conn.execute("INSERT INTO approvals (id, ts, kind, title, payload, status) "
                     "VALUES (2, ?, 'public_draft', 'Draft: [Budget Sheets Agent] Approved Budget Sheet', "
                     "?, 'approved')", (self.today, json.dumps({"store_url": "https://gumroad.com/l/budget-sheet"})))
        # Rejected draft
        conn.execute("INSERT INTO approvals (id, ts, kind, title, payload, status) "
                     "VALUES (3, ?, 'public_draft', 'Draft: Rejected Idea', 'Payload', 'rejected')", (self.today,))

        conn.execute("CREATE TABLE IF NOT EXISTS product_builds "
                     "(approval_id INTEGER PRIMARY KEY, status TEXT, folder TEXT, note TEXT, ts TEXT)")
        conn.execute("INSERT INTO product_builds (approval_id, status, folder, ts) VALUES (2, 'built', '2-approved-budget-sheet', ?)", (self.today,))

        conn.execute("CREATE TABLE IF NOT EXISTS truth_checks (approval_id INTEGER PRIMARY KEY, verdict TEXT, score INTEGER)")
        conn.execute("INSERT INTO truth_checks (approval_id, verdict, score) VALUES (2, 'ready', 100)")

        conn.commit()
        conn.close()

        # Create folder with image on disk
        p_folder = os.path.join(self.out_dir, "2-approved-budget-sheet")
        os.makedirs(p_folder, exist_ok=True)
        with open(os.path.join(p_folder, "cover.png"), "wb") as f:
            f.write(b"fake image")

        products = public_export.get_public_products(self.db, out_dir=self.out_dir)
        self.assertEqual(len(products), 1)
        p = products[0]
        self.assertEqual(p["id"], 2)
        self.assertEqual(p["short_title"], "Approved Budget Sheet")
        self.assertTrue(p["has_image"])
        self.assertEqual(p["listing_status"], "listed")
        self.assertEqual(p["store_url"], "https://gumroad.com/l/budget-sheet")
        self.assertEqual(p["truth_check"]["verdict"], "READY")
        self.assertEqual(p["truth_check"]["reasons"], [])

    def test_reject_reasons_top_5_counts_last_30_days(self):
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO approvals (ts, status, reason, review, decided_ts) "
                     "VALUES (?, 'rejected', 'Contains risky earnings promise', 'score 40/100 | issues: Contains prohibited financial or earnings promises', ?)",
                     (self.today, self.today))
        conn.execute("INSERT INTO approvals (ts, status, reason, review, decided_ts) "
                     "VALUES (?, 'rejected', 'Missing buyer section', 'score 50/100 | issues: Target audience is unclear', ?)",
                     (self.today, self.today))

        conn.execute("CREATE TABLE IF NOT EXISTS council_debates "
                     "(id INTEGER PRIMARY KEY, approval_id INTEGER, verdict TEXT, reasons TEXT, ts TEXT)")
        conn.execute("INSERT INTO council_debates (approval_id, verdict, reasons, ts) "
                     "VALUES (10, 'RETRY', 'Target audience is unclear; Product structure incomplete', ?)", (self.today,))

        conn.commit()
        conn.close()

        reasons = public_export.get_public_reject_reasons(self.db)
        self.assertLessEqual(len(reasons), 5)
        self.assertTrue(all("reason" in r and "count" in r for r in reasons))
        top_cat_names = [r["reason"] for r in reasons]
        self.assertIn("Risky or prohibited claim", top_cat_names)
        self.assertIn("Target audience unclear", top_cat_names)

    def test_report_today_counts(self):
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO approvals (ts, status) VALUES (?, 'pending')", (self.today,))
        conn.execute("INSERT INTO approvals (ts, status, decided_ts) VALUES (?, 'approved', ?)", (self.today, self.today))

        conn.execute("CREATE TABLE IF NOT EXISTS product_builds "
                     "(approval_id INTEGER PRIMARY KEY, status TEXT, ts TEXT)")
        conn.execute("INSERT INTO product_builds (approval_id, status, ts) VALUES (100, 'built', ?)", (self.today,))

        conn.execute("INSERT INTO tasks (ts, kind, status, finished_ts) VALUES (?, 'draft', 'failed', ?)", (self.today, self.today))

        conn.commit()
        conn.close()

        rep = public_export.get_public_report(self.db)
        self.assertEqual(set(rep), {"drafts_made", "drafts_waiting", "drafts_approved", "products_built", "errors"})
        self.assertEqual(rep["drafts_made"], 2)
        self.assertEqual(rep["drafts_waiting"], 1)
        self.assertEqual(rep["drafts_approved"], 1)
        self.assertEqual(rep["products_built"], 1)
        self.assertEqual(rep["errors"], 1)

    def test_workers_export_per_agent_state(self):
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO tasks (ts, kind, status, agent, started_ts) "
                     "VALUES (?, 'draft', 'running', 'Draft Worker', ?)", (self.today, self.today))
        conn.commit()
        conn.close()

        workers = public_export.get_public_workers(self.db)
        self.assertLessEqual(len(workers), 20)
        draft_w = next((w for w in workers if w["name"] == "Draft Worker"), None)
        self.assertIsNotNone(draft_w)
        self.assertEqual(draft_w["state"], "working")
        self.assertIn("Writing product spec", draft_w["last_action"])
        self.assertEqual(draft_w["last_seen"], self.today)

    def test_pipeline_counts_only(self):
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO tasks (ts, kind, status, finished_ts) VALUES (?, 'research', 'done', ?)", (self.today, self.today))
        conn.commit()
        conn.close()

        pipe = public_export.get_public_pipeline(self.db)
        self.assertIn("research", pipe)
        self.assertIn("plan", pipe["research"])
        self.assertIn("done", pipe["research"])
        self.assertGreaterEqual(pipe["research"]["done"], 1)

    def test_ai_health_reason_and_minutes_local_only(self):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE IF NOT EXISTS provider_log (day TEXT, provider TEXT, ok INTEGER, fail INTEGER)")
        conn.execute("INSERT INTO provider_log VALUES (?, 'Groq', 5, 0)", (self.today_date,))
        conn.execute("CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO control VALUES ('month_minutes', '450')")
        conn.execute("INSERT INTO control VALUES ('minutes_limit', '2000')")
        conn.commit()
        conn.close()

        health = public_export.get_public_ai_health(self.db)
        groq = next((h for h in health if h["provider"] == "Groq"), None)
        self.assertIsNotNone(groq)
        self.assertEqual(groq["state"], "up")
        self.assertIn("reason", groq)
        self.assertEqual(groq["reason"], "All recent calls succeeded")

        mins = public_export.get_public_minutes(self.db)
        self.assertEqual(mins["used_this_month"], 450)
        self.assertEqual(mins["limit"], 2000)

    def test_minutes_null_when_no_local_record(self):
        tmp_db = os.path.join(self.tmp.name, "empty.db")
        mins = public_export.get_public_minutes(tmp_db)
        self.assertIsNone(mins["used_this_month"])
        self.assertIsNone(mins["limit"])


if __name__ == "__main__":
    unittest.main()
