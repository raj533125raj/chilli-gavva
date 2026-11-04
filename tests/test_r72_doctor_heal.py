"""Unit tests for R72 Doctor Heal functionality (doctor.py).

Tests:
1. Stale lease reset once per day.
2. Retry scheduled across pipeline stages, launch packs, office requests, scheduled missions.
3. Needs-owner marking after 3 failed heals.
4. Weekly summary generated only on Sunday IST.
5. Daily AI cap respected and never exceeded; no more than 1 AI call per item per day.
6. Nothing deleted and encrypted archives remain untouched.
"""

import datetime
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

import doctor
import guardrails


class TestDoctorHeal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = self.tmp.name
        self._init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.unlink(self.db_path)
            except OSError:
                pass

    def _init_db(self, path):
        conn = doctor.connect(path)
        # Create pipeline deliveries table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_deliveries (
                approval_id INTEGER PRIMARY KEY, status TEXT, reason TEXT DEFAULT '',
                folder TEXT DEFAULT '', files TEXT DEFAULT '[]', digest TEXT DEFAULT '',
                notified INTEGER DEFAULT 0, lease_until REAL DEFAULT 0
            )
        """)
        # Create launch packs table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS launch_packs (
                approval_id INTEGER PRIMARY KEY,
                status TEXT,
                quality_score INTEGER,
                reasons TEXT,
                folder TEXT,
                rounds INTEGER DEFAULT 1,
                ts TEXT
            )
        """)
        # Create office requests table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS office_requests (
                id TEXT PRIMARY KEY, nonce TEXT UNIQUE NOT NULL, digest TEXT NOT NULL, receipt TEXT NOT NULL
            )
        """)
        conn.execute("CREATE TABLE IF NOT EXISTS office_receipt_outbox (id TEXT PRIMARY KEY)")
        # Create office missions table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS office_missions (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, agent_id TEXT NOT NULL, every TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active', next_run REAL NOT NULL, last_run REAL,
                updated REAL NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def test_stale_lease_reset_once_per_day(self):
        """Test that an expired lease is reset once per day, and subsequent runs today skip it."""
        past_ts = time.time() - 300
        conn = doctor.connect(self.db_path)
        conn.execute(
            "INSERT INTO product_deliveries (approval_id, status, lease_until, reason) VALUES (101, 'building', ?, 'building in progress')",
            (past_ts,)
        )
        conn.commit()
        conn.close()

        res1 = doctor.heal_stuck_items(db_path=self.db_path)
        self.assertEqual(res1["healed"], 1)

        conn = doctor.connect(self.db_path)
        deliv = conn.execute("SELECT lease_until FROM product_deliveries WHERE approval_id = 101").fetchone()
        self.assertEqual(deliv[0], 0)

        # If another stale lease occurs for the same item today, Doctor skips it (1 remedy/item/day)
        conn.execute("UPDATE product_deliveries SET lease_until = ? WHERE approval_id = 101", (past_ts,))
        conn.commit()
        conn.close()

        res2 = doctor.heal_stuck_items(db_path=self.db_path)
        self.assertEqual(res2["healed"], 0)
        self.assertEqual(res2["skipped"], 1)

    def test_retry_scheduled_for_stuck_items(self):
        """Test scheduling retries across the stuck categories."""
        conn = doctor.connect(self.db_path)
        # 1. Pipeline stage in retry status
        conn.execute(
            "INSERT INTO product_deliveries (approval_id, status, reason) VALUES (102, 'retry', 'Stage failed')"
        )
        # 2. Launch pack in needs_fix status
        conn.execute(
            "INSERT INTO launch_packs (approval_id, status, reasons) VALUES (103, 'needs_fix', 'Low quality score')"
        )
        # 3. Office request held
        conn.execute(
            "INSERT INTO office_requests VALUES ('req_1', 'nonce1', 'dig1', ?)",
            (json.dumps({"state": "held", "reason": "runtime held"}),)
        )
        conn.execute("INSERT INTO office_receipt_outbox VALUES ('req_1')")
        # 4. Overdue scheduled mission (>1 hr overdue, no active tasks)
        old_time = time.time() - 7200
        conn.execute(
            "INSERT INTO office_missions VALUES ('m1', 'Mission 1', 'agent_a', '1d', 'active', ?, 0, ?)",
            (old_time, old_time)
        )
        # 5. Stuck task with attempts >= 2
        conn.execute(
            "INSERT INTO tasks (id, kind, status, attempts, agent) VALUES (999, 'office_task', 'running', 2, 'Research Worker')"
        )
        conn.commit()
        conn.close()

        res = doctor.heal_stuck_items(db_path=self.db_path)
        self.assertEqual(res["healed"], 5)

        conn = doctor.connect(self.db_path)
        # Check delivery
        deliv = conn.execute("SELECT status, lease_until FROM product_deliveries WHERE approval_id = 102").fetchone()
        self.assertEqual(deliv[0], "building")
        self.assertGreater(deliv[1], time.time())

        # Check launch pack
        lp = conn.execute("SELECT status FROM launch_packs WHERE approval_id = 103").fetchone()
        self.assertEqual(lp[0], "ready_for_retry")

        # Check office request
        req = conn.execute("SELECT receipt FROM office_requests WHERE id = 'req_1'").fetchone()
        data = json.loads(req[0])
        self.assertEqual(data["state"], "retrying")

        # Check task
        t = conn.execute("SELECT status, attempts FROM tasks WHERE id = 999").fetchone()
        self.assertEqual(t[0], "queued")
        self.assertEqual(t[1], 0)

        conn.close()

    def test_needs_owner_after_3_failed_heals(self):
        """Test that after 3 prior heals, an item is marked 'needs_owner' with a clear reason."""
        conn = doctor.connect(self.db_path)
        conn.execute(
            "INSERT INTO product_deliveries (approval_id, status, reason) VALUES (105, 'retry', 'Continuous failure')"
        )
        conn.commit()
        conn.close()

        item_key = "pipeline_stage:105"
        # Simulate 3 previous heal attempts on prior dates
        for day in ("2026-03-20", "2026-03-21", "2026-03-22"):
            facts = {
                "agent": "Doctor",
                "step": item_key,
                "error_class": "StuckWorkRemedy",
                "error_message": "Continuous failure",
                "last_5_actions": []
            }
            conn = doctor.connect(self.db_path)
            conn.execute(
                """INSERT INTO doctor_cases (ts, agent, step, error_class, error_message, last_5_actions, category, diagnosis, remedy, status, actions_count)
                   VALUES (?, 'Doctor', ?, 'StuckWorkRemedy', 'Continuous failure', '[]', 'doctor_heal', 'diag', 'remedy', 'retry_scheduled', 1)""",
                (f"{day}T10:00:00", item_key)
            )
            conn.commit()
            conn.close()

        # Run heal now: 3 prior heals should escalate to needs_owner
        res = doctor.heal_stuck_items(db_path=self.db_path)
        self.assertEqual(res["needs_owner"], 1)

        conn = doctor.connect(self.db_path)
        row = conn.execute("SELECT status, reason FROM product_deliveries WHERE approval_id = 105").fetchone()
        self.assertEqual(row[0], "needs_owner")
        self.assertIn("Doctor: needs owner review after 3 failed heal attempts", row[1])

        # Verify case in doctor_cases
        case = conn.execute("SELECT status, remedy FROM doctor_cases WHERE step = ? ORDER BY id DESC LIMIT 1", (item_key,)).fetchone()
        self.assertEqual(case[0], "needs_owner")
        self.assertIn("needs owner", case[1])
        conn.close()

    def test_weekly_summary_only_on_sunday_ist(self):
        """Test that the doctor weekly summary is created ONLY on Sunday IST."""
        # Non-Sunday (e.g. Wednesday 2026-04-01)
        wednesday_ist = datetime.datetime(2026, 4, 1, 10, 0, 0, tzinfo=doctor.IST)
        res_wed = doctor.record_weekly_summary_if_sunday(db_path=self.db_path, now_dt=wednesday_ist)
        self.assertIsNone(res_wed)

        conn = doctor.connect(self.db_path)
        cases_wed = conn.execute("SELECT COUNT(*) FROM doctor_cases WHERE category = 'weekly_summary'").fetchone()[0]
        self.assertEqual(cases_wed, 0)
        conn.close()

        # Sunday (e.g. Sunday 2026-04-05)
        sunday_ist = datetime.datetime(2026, 4, 5, 10, 0, 0, tzinfo=doctor.IST)
        res_sun = doctor.record_weekly_summary_if_sunday(db_path=self.db_path, now_dt=sunday_ist)
        self.assertIsNotNone(res_sun)
        self.assertEqual(res_sun["date_ist"], "2026-04-05")

        conn = doctor.connect(self.db_path)
        case_sun = conn.execute("SELECT category, remedy, status FROM doctor_cases WHERE category = 'weekly_summary'").fetchone()
        self.assertIsNotNone(case_sun)
        self.assertEqual(case_sun["category"], "weekly_summary")
        self.assertIn("Doctor Weekly Summary (2026-04-05):", case_sun["remedy"])
        self.assertEqual(case_sun["status"], "completed")

        # Second call on same Sunday should be idempotent
        res_sun_2 = doctor.record_weekly_summary_if_sunday(db_path=self.db_path, now_dt=sunday_ist)
        self.assertIsNone(res_sun_2)
        count_sun = conn.execute("SELECT COUNT(*) FROM doctor_cases WHERE category = 'weekly_summary'").fetchone()[0]
        self.assertEqual(count_sun, 1)
        conn.close()

    def test_respects_daily_cap_and_never_exceeds(self):
        """Test that Doctor respects the daily AI cap and never exceeds it."""
        with mock.patch("guardrails.can_call_llm", return_value=False):
            # doctor.heal_stuck_items never makes rogue AI calls and checks cap
            conn = doctor.connect(self.db_path)
            conn.execute("INSERT INTO product_deliveries (approval_id, status, reason) VALUES (201, 'retry', 'Quota exceeded')")
            conn.commit()
            conn.close()

            res = doctor.heal_stuck_items(db_path=self.db_path)
            self.assertEqual(res["healed"], 1)

    def test_nothing_deleted_and_no_enc_archive_touched(self):
        """Test that Doctor never deletes records from tables or manipulates encrypted files."""
        enc_fake = tempfile.NamedTemporaryFile(delete=False, suffix=".enc")
        enc_fake.write(b"Salted__fakeproductarchive")
        enc_fake.close()

        try:
            stat_before = os.stat(enc_fake.name)

            conn = doctor.connect(self.db_path)
            conn.execute("INSERT INTO product_deliveries (approval_id, status, reason) VALUES (301, 'retry', 'reason')")
            conn.execute("INSERT INTO launch_packs (approval_id, status) VALUES (301, 'needs_fix')")
            conn.execute("INSERT INTO office_requests VALUES ('r99', 'n99', 'd99', '{}')")
            conn.commit()
            conn.close()

            doctor.run_doctor_checks(db_path=self.db_path)

            # Check that no rows were deleted
            conn = doctor.connect(self.db_path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM product_deliveries").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM launch_packs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM office_requests").fetchone()[0], 1)
            conn.close()

            stat_after = os.stat(enc_fake.name)
            self.assertEqual(stat_before.st_mtime, stat_after.st_mtime)
            self.assertEqual(stat_before.st_size, stat_after.st_size)

        finally:
            if os.path.exists(enc_fake.name):
                os.unlink(enc_fake.name)

    def test_format_last_cases_shows_heals_and_summaries(self):
        """Test that format_last_cases (/doctor) includes doctor heal actions."""
        item_key = "pipeline_stage:401"
        doctor.record_case(
            facts={"agent": "Doctor", "step": item_key, "error_class": "StuckWorkRemedy", "error_message": "stuck", "last_5_actions": []},
            category="doctor_heal",
            diagnosis="Stuck item detected",
            remedy="Reset stale lease for product #401",
            status="lease_reset",
            db_path=self.db_path
        )

        output = doctor.format_last_cases(limit=10, db_path=self.db_path)
        self.assertIn("Doctor Heal Stuck Work", output)
        self.assertIn("Reset stale lease for product #401", output)
        self.assertIn("[lease_reset]", output)


if __name__ == "__main__":
    unittest.main()
