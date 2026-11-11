"""tests/test_w4a_health.py — Comprehensive unit tests for /health, /logs, and /undo (BINNY-W4A).

60+ unit tests covering:
  - 10 health checks (database, last run, doorbell, telegram errors, ai quota,
    github minutes, packs quality, products disk, plugins, autofix state)
  - Color status rules (green, amber, red)
  - Exactly one actionable Tenglish fix hint per red line
  - Safe run log summaries with secret_guard scrubbing and length limits
  - Owner-only undo actions (approvals, rejections, settings, office requests)
  - Undo safety rules (10-minute expiry, confirmation keyboard, cancellation)
  - tg_health Telegram plugin commands and callbacks
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import guardrails
import health_check
import secret_guard
import tg_health


class FakeContext:
    def __init__(self, db_path=None, now=None):
        self.db_path = db_path
        self.chat_id = str(int("1234" "5678"))
        self.now = now or datetime.datetime.now(datetime.timezone.utc)
        self.sent_texts: list[tuple[str, Any]] = []
        self.sent_docs: list[tuple[str, str]] = []
        self.state_store: dict[str, str] = {}

    def send_text(self, text, keyboard=None):
        self.sent_texts.append((text, keyboard))
        return True

    def send_document(self, path, caption=""):
        self.sent_docs.append((path, caption))
        return True

    def state_get(self, key, default=None):
        return self.state_store.get(key, default)

    def state_set(self, key, value):
        self.state_store[key] = str(value)

    def as_dict(self):
        return {
            "db_path": self.db_path,
            "chat_id": self.chat_id,
            "now": self.now,
            "send_text": self.send_text,
            "send_document": self.send_document,
            "send_photo": lambda *a, **k: True,
            "send_media_group": lambda *a, **k: True,
            "state_get": self.state_get,
            "state_set": self.state_set,
        }


class TestHealthCheckPart1to5(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        conn = guardrails.connect(self.db_path)
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 1. Database check
    def test_01_db_check_green_when_healthy(self):
        res = health_check.check_database(self.db_path)
        self.assertEqual(res["status"], health_check.GREEN)
        self.assertIn("DB opens", res["label"])
        self.assertIsNone(res["fix"])

    def test_02_db_check_red_when_invalid_path(self):
        invalid_path = os.path.join(self.temp_dir, "nonexistent", "sub", "fake.db")
        res = health_check.check_database(invalid_path)
        self.assertEqual(res["status"], health_check.RED)
        self.assertIsNotNone(res["fix"])
        self.assertIn("DB open avvaledu", res["fix"])

    def test_03_db_check_red_when_corrupted(self):
        corrupt_db = os.path.join(self.temp_dir, "corrupt.db")
        with open(corrupt_db, "wb") as f:
            f.write(b"not a valid sqlite file content at all")
        res = health_check.check_database(corrupt_db)
        self.assertEqual(res["status"], health_check.RED)
        self.assertIsNotNone(res["fix"])

    # 2. Last run check
    def test_04_last_run_green_when_recent(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        recent = (now - datetime.timedelta(minutes=30)).isoformat()
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO telegram_state VALUES ('last_cloud_run_ts', ?)", (recent,))
        conn.commit()
        conn.close()

        res = health_check.check_last_run(self.db_path, now=now)
        self.assertEqual(res["status"], health_check.GREEN)
        self.assertIn("ago", res["label"])
        self.assertIsNone(res["fix"])

    def test_05_last_run_amber_when_delayed(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        delayed = (now - datetime.timedelta(hours=3)).isoformat()
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO telegram_state VALUES ('last_cloud_run_ts', ?)", (delayed,))
        conn.commit()
        conn.close()

        res = health_check.check_last_run(self.db_path, now=now)
        self.assertEqual(res["status"], health_check.AMBER)
        self.assertIn("delayed", res["label"])
        self.assertIsNone(res["fix"])

    def test_06_last_run_red_when_stale_over_6h(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        stale = (now - datetime.timedelta(hours=8)).isoformat()
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO telegram_state VALUES ('last_cloud_run_ts', ?)", (stale,))
        conn.commit()
        conn.close()

        res = health_check.check_last_run(self.db_path, now=now)
        self.assertEqual(res["status"], health_check.RED)
        self.assertIn("stale > 6h", res["label"])
        self.assertIsNotNone(res["fix"])
        self.assertIn("Robot run aagi", res["fix"])

    def test_07_last_run_amber_when_missing(self):
        res = health_check.check_last_run(self.db_path)
        self.assertEqual(res["status"], health_check.AMBER)
        self.assertIn("unknown", res["label"])

    # 3. Door bell check
    def test_08_doorbell_green_when_active_and_token_ok(self):
        fake_status = lambda: {"door_bell": True, "telegram_token_ok": "yes"}
        res = health_check.check_doorbell(status_fn=fake_status)
        self.assertEqual(res["status"], health_check.GREEN)
        self.assertIn("ready", res["label"])
        self.assertIsNone(res["fix"])

    def test_09_doorbell_red_when_token_rejected(self):
        fake_status = lambda: {"door_bell": True, "telegram_token_ok": "no"}
        res = health_check.check_doorbell(status_fn=fake_status)
        self.assertEqual(res["status"], health_check.RED)
        self.assertIn("rejected", res["label"])
        self.assertIsNotNone(res["fix"])
        self.assertIn("TELEGRAM_BOT_TOKEN", res["fix"])

    def test_10_doorbell_amber_when_off(self):
        fake_status = lambda: {"door_bell": False}
        res = health_check.check_doorbell(status_fn=fake_status)
        self.assertEqual(res["status"], health_check.AMBER)
        self.assertIn("off", res["label"])
        self.assertIsNone(res["fix"])

    def test_11_doorbell_amber_when_unreachable(self):
        fake_status = lambda: {}
        res = health_check.check_doorbell(status_fn=fake_status)
        self.assertEqual(res["status"], health_check.AMBER)
        self.assertIn("unknown", res["label"])

    # 4. Telegram read errors
    def test_12_telegram_errors_green_when_clean(self):
        with patch("telegram_inbox.LAST_FETCH_ERROR", None):
            res = health_check.check_telegram_errors(self.db_path)
            self.assertEqual(res["status"], health_check.GREEN)
            self.assertIn("no read errors", res["label"])

    def test_13_telegram_errors_red_on_401(self):
        with patch("telegram_inbox.LAST_FETCH_ERROR", "401 (bot token not accepted)"):
            res = health_check.check_telegram_errors(self.db_path)
            self.assertEqual(res["status"], health_check.RED)
            self.assertIsNotNone(res["fix"])
            self.assertIn("TELEGRAM_BOT_TOKEN", res["fix"])

    def test_14_telegram_errors_red_on_409_webhook_conflict(self):
        with patch("telegram_inbox.LAST_FETCH_ERROR", "409 (another program or a webhook is reading this bot)"):
            res = health_check.check_telegram_errors(self.db_path)
            self.assertEqual(res["status"], health_check.RED)
            self.assertIsNotNone(res["fix"])
            self.assertIn("webhook", res["fix"])

    def test_15_telegram_errors_amber_on_generic_fetch_error(self):
        with patch("telegram_inbox.LAST_FETCH_ERROR", "network timeout"):
            res = health_check.check_telegram_errors(self.db_path)
            self.assertEqual(res["status"], health_check.AMBER)

    # 5. AI quota check
    def test_16_ai_quota_green_when_calls_remain(self):
        fake_status = [
            {
                "provider": "Groq",
                "configured": True,
                "models": [{"model": "m1", "calls_today": 10, "daily_cap": 100, "capped": False}],
            }
        ]
        with patch("llm_router.provider_status", return_value=fake_status):
            res = health_check.check_ai_quota(self.db_path)
            self.assertEqual(res["status"], health_check.GREEN)
            self.assertIn("90 left", res["label"])
            self.assertIsNone(res["fix"])

    def test_17_ai_quota_red_when_exhausted(self):
        fake_status = [
            {
                "provider": "Groq",
                "configured": True,
                "models": [{"model": "m1", "calls_today": 100, "daily_cap": 100, "capped": True}],
            }
        ]
        with patch("llm_router.provider_status", return_value=fake_status):
            res = health_check.check_ai_quota(self.db_path)
            self.assertEqual(res["status"], health_check.RED)
            self.assertIn("0 left", res["label"])
            self.assertIsNotNone(res["fix"])
            self.assertIn("quota aypoyindi", res["fix"])

    def test_18_ai_quota_fallback_usage_log(self):
        conn = guardrails.connect(self.db_path)
        conn.execute("INSERT OR REPLACE INTO usage_log (day, llm_calls) VALUES (?, ?)", (guardrails.today(), 40))
        conn.commit()
        conn.close()

        with patch("llm_router.provider_status", side_effect=Exception("router down")):
            res = health_check.check_ai_quota(self.db_path)
            self.assertEqual(res["status"], health_check.GREEN)
            self.assertIn("460/500 left", res["label"])


class TestHealthCheckPart6to10(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        conn = guardrails.connect(self.db_path)
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 6. GitHub minutes check
    def test_19_github_minutes_green_when_low(self):
        fake_status = lambda: {"month_minutes": 250}
        res = health_check.check_github_minutes(status_fn=fake_status, db_path=self.db_path)
        self.assertEqual(res["status"], health_check.GREEN)
        self.assertIn("250/2000", res["label"])
        self.assertIsNone(res["fix"])

    def test_20_github_minutes_amber_when_above_80_percent(self):
        fake_status = lambda: {"month_minutes": 1700}
        res = health_check.check_github_minutes(status_fn=fake_status, db_path=self.db_path)
        self.assertEqual(res["status"], health_check.AMBER)
        self.assertIn("85%", res["label"])

    def test_21_github_minutes_red_when_near_limit(self):
        fake_status = lambda: {"month_minutes": 1950}
        res = health_check.check_github_minutes(status_fn=fake_status, db_path=self.db_path)
        self.assertEqual(res["status"], health_check.RED)
        self.assertIn("98%", res["label"])
        self.assertIsNotNone(res["fix"])
        self.assertIn("free minutes aypothunnai", res["fix"])

    def test_22_github_minutes_amber_when_unknown(self):
        fake_status = lambda: {}
        with patch("public_export.get_public_minutes", return_value={}):
            res = health_check.check_github_minutes(status_fn=fake_status, db_path=self.db_path)
            self.assertEqual(res["status"], health_check.AMBER)
            self.assertIn("unknown", res["label"])

    # 7. Packs quality check
    def test_23_packs_quality_green_when_all_ready(self):
        fake_prods = [
            {"truth_check": {"verdict": "READY"}},
            {"truth_check": {"verdict": "READY"}},
        ]
        with patch("public_export.get_public_products", return_value=fake_prods):
            res = health_check.check_packs_quality(self.db_path)
            self.assertEqual(res["status"], health_check.GREEN)
            self.assertIn("2 READY, 0 NEEDS FIX", res["label"])

    def test_24_packs_quality_amber_when_some_need_fix(self):
        fake_prods = [
            {"truth_check": {"verdict": "READY"}},
            {"truth_check": {"verdict": "NEEDS FIX"}},
        ]
        with patch("public_export.get_public_products", return_value=fake_prods):
            res = health_check.check_packs_quality(self.db_path)
            self.assertEqual(res["status"], health_check.AMBER)
            self.assertIn("1 READY, 1 NEEDS FIX", res["label"])

    def test_25_packs_quality_red_when_only_needs_fix(self):
        fake_prods = [
            {"truth_check": {"verdict": "NEEDS FIX"}},
        ]
        with patch("public_export.get_public_products", return_value=fake_prods):
            res = health_check.check_packs_quality(self.db_path)
            self.assertEqual(res["status"], health_check.RED)
            self.assertIn("0 READY, 1 NEEDS FIX", res["label"])
            self.assertIsNotNone(res["fix"])
            self.assertIn("defects unnai", res["fix"])

    # 8. Products disk check
    def test_26_products_disk_green_under_1gb(self):
        p_dir = os.path.join(self.temp_dir, "prods")
        os.makedirs(p_dir, exist_ok=True)
        with open(os.path.join(p_dir, "dummy.txt"), "w") as f:
            f.write("hello" * 100)
        res = health_check.check_products_disk(p_dir)
        self.assertEqual(res["status"], health_check.GREEN)
        self.assertIn("MB", res["label"])

    def test_27_products_disk_amber_over_1gb(self):
        with patch("os.walk", return_value=[(self.temp_dir, [], ["big.bin"])]), \
             patch("os.path.getsize", return_value=1200 * 1024 * 1024):
            res = health_check.check_products_disk(self.temp_dir)
            self.assertEqual(res["status"], health_check.AMBER)

    def test_28_products_disk_red_over_2gb(self):
        with patch("os.walk", return_value=[(self.temp_dir, [], ["massive.bin"])]), \
             patch("os.path.getsize", return_value=2500 * 1024 * 1024):
            res = health_check.check_products_disk(self.temp_dir)
            self.assertEqual(res["status"], health_check.RED)
            self.assertIsNotNone(res["fix"])
            self.assertIn("2GB daatindi", res["fix"])

    # 9. Telegram plugins health
    def test_29_plugins_health_green_when_all_load(self):
        res = health_check.check_plugins_health()
        self.assertEqual(res["status"], health_check.GREEN)
        self.assertIn("loaded ok", res["label"])

    def test_30_plugins_health_red_when_a_plugin_fails(self):
        dummy_dir = os.path.join(self.temp_dir, "plugins")
        os.makedirs(dummy_dir, exist_ok=True)
        with open(os.path.join(dummy_dir, "tg_broken.py"), "w") as f:
            f.write("import non_existent_library_abc_xyz_123\n")
        res = health_check.check_plugins_health(repo_root=dummy_dir)
        self.assertEqual(res["status"], health_check.RED)
        self.assertIn("tg_broken", res["label"])
        self.assertIsNotNone(res["fix"])

    # 10. Auto-fix state check
    def test_31_autofix_green_when_idle(self):
        res = health_check.check_autofix_state(self.db_path)
        self.assertEqual(res["status"], health_check.GREEN)
        self.assertIn("ready (idle)", res["label"])

    def test_32_autofix_green_when_offer_active(self):
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO telegram_state VALUES ('autofix_offer:42', 'commit_sha')")
        conn.commit()
        conn.close()
        res = health_check.check_autofix_state(self.db_path)
        self.assertEqual(res["status"], health_check.GREEN)
        self.assertIn("1 offer waiting", res["label"])

    def test_33_autofix_amber_when_retry_pending(self):
        with patch("run_failure.state_document", return_value={"autofix_retry": {"problem": "AssertionError"}}):
            with patch("pathlib.Path.is_file", return_value=True):
                res = health_check.check_autofix_state(self.db_path)
                self.assertEqual(res["status"], health_check.AMBER)
                self.assertIn("retry pending", res["label"])


class TestHealthCardFormatting(unittest.TestCase):
    def test_34_health_card_contains_all_10_parts(self):
        checks = [
            {"part": "db", "status": health_check.GREEN, "label": "DB opens", "fix": None},
            {"part": "last_run", "status": health_check.GREEN, "label": "Last run: 1h ago", "fix": None},
            {"part": "doorbell", "status": health_check.GREEN, "label": "Door bell: on", "fix": None},
            {"part": "telegram_errors", "status": health_check.GREEN, "label": "Telegram: ok", "fix": None},
            {"part": "ai_quota", "status": health_check.GREEN, "label": "AI quota: 100 left", "fix": None},
            {"part": "github_minutes", "status": health_check.GREEN, "label": "GitHub: 200/2000", "fix": None},
            {"part": "packs_quality", "status": health_check.GREEN, "label": "Packs: 2 READY", "fix": None},
            {"part": "products_disk", "status": health_check.GREEN, "label": "Products: 10 MB", "fix": None},
            {"part": "plugins", "status": health_check.GREEN, "label": "Plugins: 8 loaded", "fix": None},
            {"part": "autofix", "status": health_check.GREEN, "label": "Auto-fix: ready", "fix": None},
        ]
        card = health_check.format_health_card(checks)
        self.assertIn("Robot Health Card", card)
        for c in checks:
            self.assertIn(c["label"], card)
        self.assertIn("🟢", card)
        self.assertIn("Anni systems baagunnai", card)

    def test_35_health_card_shows_tenglish_fix_for_red(self):
        checks = [
            {"part": "db", "status": health_check.RED, "label": "DB fail", "fix": "DB backup check cheyandi."},
            {"part": "doorbell", "status": health_check.GREEN, "label": "Door bell: on", "fix": None},
        ]
        card = health_check.format_health_card(checks)
        self.assertIn("🔴 DB fail", card)
        self.assertIn("👉 Fix: DB backup check cheyandi.", card)

    def test_36_health_card_strictly_no_currency(self):
        checks = [
            {"part": "db", "status": health_check.GREEN, "label": "DB check", "fix": None},
        ]
        card = health_check.format_health_card(checks)
        self.assertNotRegex(card, r"[$₹€£]")
        self.assertNotIn("USD", card)
        self.assertNotIn("INR", card)

    def test_37_health_card_length_under_3500(self):
        checks = [
            {"part": f"p{i}", "status": health_check.GREEN, "label": f"Part {i}: " + ("x" * 100), "fix": None}
            for i in range(10)
        ]
        card = health_check.format_health_card(checks)
        self.assertLessEqual(len(card), 3500)


class TestLastRunLogs(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.summary_file = os.path.join(self.temp_dir, "summary.txt")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_38_logs_reads_from_summary_file(self):
        with open(self.summary_file, "w") as f:
            f.write("Line 1: step started\nLine 2: step completed\n")
        logs = health_check.get_last_run_summary(summary_path=self.summary_file)
        self.assertIn("Line 1: step started", logs)
        self.assertIn("Line 2: step completed", logs)

    def test_39_logs_scrubs_secrets(self):
        with open(self.summary_file, "w") as f:
            f.write("Sample secret token: bot123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11\n")
        logs = health_check.get_last_run_summary(summary_path=self.summary_file)
        self.assertNotIn("ABC-DEF1234ghIkl-zyx57W2v1u123ew11", logs)

    def test_40_logs_scrubs_currency_amounts(self):
        with open(self.summary_file, "w") as f:
            f.write("Price test: $49 or Rs. 1500 or 25 USD.\n")
        logs = health_check.get_last_run_summary(summary_path=self.summary_file)
        self.assertNotIn("$49", logs)
        self.assertNotIn("1500", logs)
        self.assertIn("[hidden]", logs)

    def test_41_logs_caps_at_3500_chars(self):
        with open(self.summary_file, "w") as f:
            for i in range(200):
                f.write(f"Line {i}: " + ("data " * 10) + "\n")
        logs = health_check.get_last_run_summary(summary_path=self.summary_file)
        self.assertLessEqual(len(logs), 3500)

    def test_42_logs_fallback_when_file_missing(self):
        logs = health_check.get_last_run_summary(summary_path=os.path.join(self.temp_dir, "missing.txt"))
        self.assertIn("Last Run Summary", logs)


class TestUndoManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        conn = guardrails.connect(self.db_path)
        conn.execute(
            "INSERT INTO approvals (id, status, decided_ts, reason, title, payload, kind) "
            "VALUES (10, 'approved', '2026-11-11T10:00:00Z', '', 'Title 10', 'Body', 'draft')"
        )
        conn.execute(
            "INSERT INTO approvals (id, status, decided_ts, reason, title, payload, kind) "
            "VALUES (20, 'rejected', '2026-11-11T10:00:00Z', 'Weak niche', 'Title 20', 'Body', 'draft')"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_43_undo_recent_approval_reverts_to_pending(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        health_check.record_owner_action("approval", {"id": 10}, db_path=self.db_path, now=now)

        rec, err = health_check.get_last_undoable_action(db_path=self.db_path, now=now)
        self.assertIsNone(err)
        self.assertEqual(rec["action_type"], "approval")

        ok, msg = health_check.apply_undo(rec, db_path=self.db_path)
        self.assertTrue(ok)
        self.assertIn("pending", msg)

        # Verify DB row
        conn = guardrails.connect(self.db_path)
        row = conn.execute("SELECT status, decided_ts, reason FROM approvals WHERE id = 10").fetchone()
        conn.close()
        self.assertEqual(row[0], "pending")
        self.assertIsNone(row[1])

    def test_44_undo_recent_rejection_reverts_to_pending(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        health_check.record_owner_action("rejection", {"id": 20}, db_path=self.db_path, now=now)

        rec, err = health_check.get_last_undoable_action(db_path=self.db_path, now=now)
        self.assertIsNone(err)
        self.assertEqual(rec["action_type"], "rejection")

        ok, msg = health_check.apply_undo(rec, db_path=self.db_path)
        self.assertTrue(ok)

        conn = guardrails.connect(self.db_path)
        row = conn.execute("SELECT status, reason FROM approvals WHERE id = 20").fetchone()
        conn.close()
        self.assertEqual(row[0], "pending")
        self.assertIsNone(row[1])

    def test_45_undo_older_than_10_minutes_fails(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        past = now - datetime.timedelta(minutes=15)
        health_check.record_owner_action("approval", {"id": 10}, db_path=self.db_path, now=past)

        rec, err = health_check.get_last_undoable_action(db_path=self.db_path, now=now)
        self.assertIsNone(rec)
        self.assertIsNotNone(err)
        self.assertIn("10 minutes daatindi", err)

    def test_46_undo_settings_change(self):
        prev = {"quiet_hours": {"enabled": False}}
        now = datetime.datetime.now(datetime.timezone.utc)
        health_check.record_owner_action("settings", {"previous_settings": prev}, db_path=self.db_path, now=now)

        rec, err = health_check.get_last_undoable_action(db_path=self.db_path, now=now)
        self.assertIsNone(err)
        with patch("tg_settings.save_settings") as mock_save:
            ok, msg = health_check.apply_undo(rec, db_path=self.db_path)
            self.assertTrue(ok)
            mock_save.assert_called_once_with(prev, db_path=self.db_path)

    def test_47_undo_office_request(self):
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS office_requests (id TEXT PRIMARY KEY, intent TEXT, args TEXT, ts TEXT)")
        conn.execute("INSERT INTO office_requests VALUES ('req_1', 'theme', '{}', '2026-11-11T12:00:00Z')")
        conn.commit()
        conn.close()

        now = datetime.datetime.now(datetime.timezone.utc)
        health_check.record_owner_action("office_request", {"request_id": "req_1", "intent": "theme"}, db_path=self.db_path, now=now)

        rec, err = health_check.get_last_undoable_action(db_path=self.db_path, now=now)
        self.assertIsNone(err)

        ok, msg = health_check.apply_undo(rec, db_path=self.db_path)
        self.assertTrue(ok)
        self.assertIn("cancelled", msg)

        conn = guardrails.connect(self.db_path)
        cnt = conn.execute("SELECT count(*) FROM office_requests WHERE id = 'req_1'").fetchone()[0]
        conn.close()
        self.assertEqual(cnt, 0)

    def test_48_cannot_undo_twice(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        health_check.record_owner_action("approval", {"id": 10}, db_path=self.db_path, now=now)

        rec, _ = health_check.get_last_undoable_action(db_path=self.db_path, now=now)
        ok1, _ = health_check.apply_undo(rec, db_path=self.db_path)
        self.assertTrue(ok1)

        rec2, err2 = health_check.get_last_undoable_action(db_path=self.db_path, now=now)
        self.assertIsNone(rec2)
        self.assertIn("no recent action found", err2)


class TestTgHealthPlugin(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        conn = guardrails.connect(self.db_path)
        conn.close()
        self.ctx = FakeContext(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_49_plugin_commands_structure(self):
        self.assertIsInstance(tg_health.COMMANDS, list)
        cmds = [c["cmd"] for c in tg_health.COMMANDS]
        self.assertIn("/health", cmds)
        self.assertIn("/logs", cmds)
        self.assertIn("/undo", cmds)

    def test_50_plugin_ignores_unrelated_text(self):
        res = tg_health.handle("hello random message", self.ctx.as_dict())
        self.assertIsNone(res)

    def test_51_handle_health_slash_command(self):
        res = tg_health.handle("/health", self.ctx.as_dict())
        self.assertTrue(res.get("handled") or "text" in res)
        self.assertEqual(len(self.ctx.sent_texts), 1)
        text, _ = self.ctx.sent_texts[0]
        self.assertIn("Health Card", text)

    def test_52_handle_health_plain_words(self):
        res = tg_health.handle("system health", self.ctx.as_dict())
        self.assertTrue(res.get("handled") or "text" in res)
        self.assertEqual(len(self.ctx.sent_texts), 1)
        text, _ = self.ctx.sent_texts[0]
        self.assertIn("Health Card", text)

    def test_53_handle_health_tenglish_word(self):
        res = tg_health.handle("arogyam", self.ctx.as_dict())
        self.assertTrue(res.get("handled") or "text" in res)
        self.assertEqual(len(self.ctx.sent_texts), 1)

    def test_54_handle_logs_command(self):
        res = tg_health.handle("/logs", self.ctx.as_dict())
        self.assertTrue(res.get("handled") or "text" in res)
        self.assertEqual(len(self.ctx.sent_texts), 1)
        text, _ = self.ctx.sent_texts[0]
        self.assertIn("Last Run Summary", text)

    def test_55_handle_logs_plain_words(self):
        res = tg_health.handle("last run logs", self.ctx.as_dict())
        self.assertTrue(res.get("handled") or "text" in res)
        self.assertEqual(len(self.ctx.sent_texts), 1)

    def test_56_handle_undo_when_no_recent_action(self):
        res = tg_health.handle("/undo", self.ctx.as_dict())
        self.assertTrue(res.get("handled") or "text" in res)
        text, _ = self.ctx.sent_texts[0]
        self.assertIn("no recent action found", text)

    def test_57_handle_undo_presents_confirm_keyboard(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        health_check.record_owner_action("approval", {"id": 42}, db_path=self.db_path, now=now)

        res = tg_health.handle("/undo", self.ctx.as_dict())
        self.assertTrue(res.get("handled") or "text" in res)
        text, kb = self.ctx.sent_texts[0]
        self.assertIn("Undo Confirmation", text)
        self.assertIn("Draft #42 approval", text)
        self.assertEqual(kb, [["Yes", "No"]])
        self.assertIn("owner_undo_pending_confirm", self.ctx.state_store)

    def test_58_handle_undo_confirm_yes(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        conn = guardrails.connect(self.db_path)
        conn.execute(
            "INSERT INTO approvals (id, status, decided_ts, reason, title, payload, kind) "
            "VALUES (42, 'approved', '2026-11-11T10:00:00Z', '', 'Draft 42', 'Payload', 'draft')"
        )
        conn.commit()
        conn.close()

        health_check.record_owner_action("approval", {"id": 42}, db_path=self.db_path, now=now)
        tg_health.handle("/undo", self.ctx.as_dict())

        # Now send "Yes"
        res = tg_health.handle("Yes", self.ctx.as_dict())
        self.assertTrue(res.get("handled") or "text" in res)
        reply, _ = self.ctx.sent_texts[-1]
        self.assertIn("revert ayindi", reply)

        conn = guardrails.connect(self.db_path)
        row = conn.execute("SELECT status FROM approvals WHERE id = 42").fetchone()
        conn.close()
        self.assertEqual(row[0], "pending")

    def test_59_handle_undo_confirm_no(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        health_check.record_owner_action("approval", {"id": 42}, db_path=self.db_path, now=now)
        tg_health.handle("/undo", self.ctx.as_dict())

        res = tg_health.handle("No", self.ctx.as_dict())
        self.assertTrue(res.get("handled") or "text" in res)
        reply, _ = self.ctx.sent_texts[-1]
        self.assertIn("cancel chesam", reply)
        self.assertEqual(self.ctx.state_store.get("owner_undo_pending_confirm"), "")

    def test_60_handle_undo_expired_prompt(self):
        past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=15)).isoformat()
        pending = {
            "ts": past,
            "record": {"action_type": "approval", "details": {"id": 42}},
            "description": "Draft #42 approval",
        }
        self.ctx.state_set("owner_undo_pending_confirm", json.dumps(pending))

        res = tg_health.handle("Yes", self.ctx.as_dict())
        self.assertIsNone(res)  # Expired confirmation ignored for Yes
        self.assertEqual(self.ctx.state_store.get("owner_undo_pending_confirm"), "")

    def test_61_tenglish_confirmation_words(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        health_check.record_owner_action("approval", {"id": 42}, db_path=self.db_path, now=now)
        tg_health.handle("/undo", self.ctx.as_dict())

        res = tg_health.handle("avunu", self.ctx.as_dict())
        self.assertTrue(res.get("handled") or "text" in res)
        reply, _ = self.ctx.sent_texts[-1]
        self.assertIn("undo chesam", reply)

    def test_62_tenglish_cancel_words(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        health_check.record_owner_action("approval", {"id": 42}, db_path=self.db_path, now=now)
        tg_health.handle("/undo", self.ctx.as_dict())

        res = tg_health.handle("vaddu", self.ctx.as_dict())
        self.assertTrue(res.get("handled") or "text" in res)
        reply, _ = self.ctx.sent_texts[-1]
        self.assertIn("cancel chesam", reply)


if __name__ == "__main__":
    unittest.main()
