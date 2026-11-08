"""Unit tests for ops_report.py — Weekly ops summary from robot's state."""

import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import guardrails
import ops_report


class TestOpsReport(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* environment variables
        env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in list(os.environ.keys()) if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT"},
        )
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_agent_data.db")
        self.products_dir = os.path.join(self.tmp_dir.name, "exported_content", "products")
        self.marketing_dir = os.path.join(self.tmp_dir.name, "exported_content", "marketing")
        os.makedirs(self.products_dir, exist_ok=True)
        os.makedirs(self.marketing_dir, exist_ok=True)

        self.conn = guardrails.connect(self.db_path)
        self._init_extra_tables()

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass
        self.tmp_dir.cleanup()

    def _init_extra_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, payload TEXT,
                status TEXT DEFAULT 'queued', attempts INTEGER DEFAULT 0, agent TEXT,
                result TEXT, error TEXT, priority INTEGER DEFAULT 5, finished_ts TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS provider_log (
                day TEXT, provider TEXT, ok INTEGER DEFAULT 0, fail INTEGER DEFAULT 0,
                PRIMARY KEY (day, provider)
            )
        """)
        self.conn.commit()

    def test_empty_db_generation(self):
        summary = ops_report.generate_ops_summary(
            db_path=self.db_path,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        self.assertEqual(summary["period_days"], 7)
        self.assertEqual(summary["failures"]["total"], 0)
        self.assertEqual(summary["retries_queued"], 0)
        self.assertEqual(summary["packs_ready"], 0)
        self.assertEqual(summary["marketing_files"]["total_files"], 0)
        self.assertEqual(summary["drafts"]["made"], 0)

    def test_format_ops_text_empty(self):
        summary = ops_report.generate_ops_summary(
            db_path=self.db_path,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        text = ops_report.format_ops_text(summary)
        self.assertIn("Chilli Gavva Weekly Ops Summary", text)
        self.assertIn("zero failures", text)
        self.assertLessEqual(len(text), 3000)

    def test_runs_per_day_from_tasks(self):
        now = datetime.datetime(2026, 11, 8, 12, 0, 0)
        self.conn.execute(
            "INSERT INTO tasks (ts, kind, status) VALUES ('2026-11-07T10:00:00', 'draft', 'finished')"
        )
        self.conn.execute(
            "INSERT INTO tasks (ts, kind, status) VALUES ('2026-11-07T11:00:00', 'review', 'finished')"
        )
        self.conn.execute(
            "INSERT INTO tasks (ts, kind, status) VALUES ('2026-11-06T09:00:00', 'plan', 'finished')"
        )
        self.conn.commit()

        summary = ops_report.generate_ops_summary(
            db_path=self.db_path,
            now=now,
            days=7,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        rpd = summary["runs_per_day"]
        self.assertEqual(rpd.get("2026-11-07"), 2)
        self.assertEqual(rpd.get("2026-11-06"), 1)

    def test_runs_per_day_fallback_usage_log(self):
        now = datetime.datetime(2026, 11, 8, 12, 0, 0)
        self.conn.execute("INSERT OR REPLACE INTO usage_log (day, llm_calls) VALUES ('2026-11-05', 42)")
        self.conn.commit()

        summary = ops_report.generate_ops_summary(
            db_path=self.db_path,
            now=now,
            days=7,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        self.assertEqual(summary["runs_per_day"].get("2026-11-05"), 42)

    def test_failures_by_step_calculation(self):
        now = datetime.datetime(2026, 11, 8, 12, 0, 0)
        self.conn.execute(
            "INSERT INTO tasks (ts, finished_ts, kind, status, error) VALUES ('2026-11-07T10:00:00', '2026-11-07T10:01:00', 'draft', 'failed', 'Timeout')"
        )
        self.conn.execute(
            "INSERT INTO tasks (ts, finished_ts, kind, status, error) VALUES ('2026-11-07T11:00:00', '2026-11-07T11:01:00', 'draft', 'failed', 'Rate limit')"
        )
        self.conn.execute(
            "INSERT INTO tasks (ts, finished_ts, kind, status, error) VALUES ('2026-11-07T12:00:00', '2026-11-07T12:01:00', 'review', 'failed', 'JSON parse error')"
        )
        self.conn.commit()

        summary = ops_report.generate_ops_summary(
            db_path=self.db_path,
            now=now,
            days=7,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        fails = summary["failures"]
        self.assertEqual(fails["total"], 3)
        self.assertEqual(fails["by_step"].get("draft"), 2)
        self.assertEqual(fails["by_step"].get("review"), 1)

    def test_failures_excluded_if_outside_window(self):
        now = datetime.datetime(2026, 11, 8, 12, 0, 0)
        self.conn.execute(
            "INSERT INTO tasks (ts, finished_ts, kind, status, error) VALUES ('2026-10-01T10:00:00', '2026-10-01T10:01:00', 'draft', 'failed', 'Old failure')"
        )
        self.conn.commit()

        summary = ops_report.generate_ops_summary(
            db_path=self.db_path,
            now=now,
            days=7,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        self.assertEqual(summary["failures"]["total"], 0)

    def test_retries_count(self):
        self.conn.execute("INSERT INTO tasks (kind, status, error) VALUES ('draft', 'queued', 'Temporary glitch')")
        self.conn.execute("INSERT INTO tasks (kind, status, attempts) VALUES ('plan', 'queued', 1)")
        self.conn.execute("INSERT INTO tasks (kind, status, attempts) VALUES ('review', 'queued', 0)")
        self.conn.execute("INSERT INTO tasks (kind, status, error) VALUES ('code', 'running', 'Error in progress')")
        self.conn.commit()

        summary = ops_report.generate_ops_summary(
            db_path=self.db_path,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        self.assertEqual(summary["retries_queued"], 2)

    def test_provider_up_down_counts(self):
        self.conn.execute("INSERT INTO provider_log (day, provider, ok, fail) VALUES ('2026-11-07', 'Groq', 50, 2)")
        self.conn.execute("INSERT INTO provider_log (day, provider, ok, fail) VALUES ('2026-11-06', 'Gemini', 1, 10)")
        self.conn.commit()

        summary = ops_report.generate_ops_summary(
            db_path=self.db_path,
            now=datetime.datetime(2026, 11, 8, 12, 0, 0),
            days=7,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        provs = summary["providers"]
        self.assertIn("Groq", provs)
        self.assertEqual(provs["Groq"]["status"], "UP")
        self.assertEqual(provs["Groq"]["ok"], 50)
        self.assertEqual(provs["Groq"]["fail"], 2)

        self.assertIn("Gemini", provs)
        self.assertEqual(provs["Gemini"]["status"], "DOWN")
        self.assertEqual(provs["Gemini"]["fail"], 10)

    def test_draft_counts_made_approved_rejected(self):
        now = datetime.datetime(2026, 11, 8, 12, 0, 0)
        self.conn.execute("INSERT INTO approvals (ts, title, status) VALUES ('2026-11-07T10:00:00', 'Draft 1', 'pending')")
        self.conn.execute("INSERT INTO approvals (ts, decided_ts, title, status) VALUES ('2026-11-06T10:00:00', '2026-11-06T12:00:00', 'Draft 2', 'approved')")
        self.conn.execute("INSERT INTO approvals (ts, decided_ts, title, status) VALUES ('2026-11-05T10:00:00', '2026-11-05T12:00:00', 'Draft 3', 'rejected')")
        self.conn.execute("INSERT INTO approvals (ts, decided_ts, title, status) VALUES ('2026-10-01T10:00:00', '2026-10-01T12:00:00', 'Draft Old', 'approved')")
        self.conn.commit()

        summary = ops_report.generate_ops_summary(
            db_path=self.db_path,
            now=now,
            days=7,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        d = summary["drafts"]
        self.assertEqual(d["made"], 3)
        self.assertEqual(d["approved"], 1)
        self.assertEqual(d["rejected"], 1)
        self.assertEqual(d["pending"], 1)

    def test_ready_upload_packs_count(self):
        upload_dir = Path(self.products_dir) / "upload"
        p1 = upload_dir / "pack-1"
        p1.mkdir(parents=True)
        (p1 / "product.zip").write_text("dummy")
        (p1 / "listing.txt").write_text("title: Pack 1")

        p2 = upload_dir / "pack-2"
        p2.mkdir(parents=True)
        (p2 / "product.zip").write_text("dummy")
        # missing listing.txt

        p3 = upload_dir / "pack-3"
        p3.mkdir(parents=True)
        (p3 / "product.zip").write_text("dummy")
        (p3 / "listing.txt").write_text("title: Pack 3")

        count = ops_report.count_ready_upload_packs(self.products_dir)
        self.assertEqual(count, 2)

    def test_count_marketing_files_breakdown(self):
        mkt = Path(self.marketing_dir)
        pack_car = mkt / "pack-1" / "carousel"
        pack_car.mkdir(parents=True)
        for i in range(6):
            (pack_car / f"slide_{i+1}.png").write_text("fake png")
        (pack_car / "caption.txt").write_text("caption")

        pack_pins = mkt / "pack-1" / "pins"
        pack_pins.mkdir(parents=True)
        for i in range(5):
            (pack_pins / f"pin_{i+1}.png").write_text("fake png")
        (pack_pins / "pins.json").write_text("{}")

        pack_reel = mkt / "pack-1" / "reel"
        pack_reel.mkdir(parents=True)
        (pack_reel / "frame_1.png").write_text("frame")

        (mkt / "pack-1" / "seo_meta.json").write_text("{}")
        (mkt / "pack-1" / "blog.md").write_text("# Blog")
        (mkt / "keywords.json").write_text("[]")

        mkt_counts = ops_report.count_marketing_files(self.marketing_dir)
        self.assertEqual(mkt_counts["carousels"], 7)
        self.assertEqual(mkt_counts["pins"], 6)
        self.assertEqual(mkt_counts["reels"], 1)
        self.assertEqual(mkt_counts["seo_meta"], 1)
        self.assertEqual(mkt_counts["blog_posts"], 1)
        self.assertEqual(mkt_counts["keywords"], 1)
        self.assertEqual(mkt_counts["total_files"], 17)
        self.assertEqual(mkt_counts["by_pack"].get("pack-1"), 16)

    def test_count_marketing_files_nonexistent_dir(self):
        res = ops_report.count_marketing_files("/non/existent/dir/path")
        self.assertEqual(res["total_files"], 0)
        self.assertEqual(res["carousels"], 0)

    def test_count_ready_upload_packs_nonexistent_dir(self):
        res = ops_report.count_ready_upload_packs("/non/existent/dir/path")
        self.assertEqual(res, 0)

    def test_format_ops_text_length_limit(self):
        # Create a large payload with many failures to test truncation
        many_fails = {f"step_{i}": i * 10 for i in range(200)}
        data = {
            "period_days": 7,
            "generated_at": "2026-11-08T12:00:00",
            "runs_per_day": {"2026-11-08": 50},
            "failures": {"total": 5000, "by_step": many_fails},
            "retries_queued": 12,
            "providers": {"Groq": {"ok": 100, "fail": 2, "status": "UP"}},
            "drafts": {"made": 10, "approved": 5, "rejected": 2, "pending": 3},
            "packs_ready": 4,
            "marketing_files": {"total_files": 25, "carousels": 6, "pins": 5, "reels": 8, "blog_posts": 1},
        }
        text = ops_report.format_ops_text(data)
        self.assertLessEqual(len(text), 3000)

    def test_format_ops_text_content_includes_key_sections(self):
        data = {
            "period_days": 7,
            "generated_at": "2026-11-08T12:00:00",
            "runs_per_day": {"2026-11-07": 10, "2026-11-08": 15},
            "failures": {"total": 2, "by_step": {"draft": 1, "review": 1}},
            "retries_queued": 1,
            "providers": {"Groq": {"ok": 20, "fail": 1, "status": "UP"}},
            "drafts": {"made": 4, "approved": 2, "rejected": 1, "pending": 1},
            "packs_ready": 3,
            "marketing_files": {"total_files": 12, "carousels": 6, "pins": 5, "reels": 0, "blog_posts": 1},
        }
        text = ops_report.format_ops_text(data)
        self.assertIn("Chilli Gavva Weekly Ops Summary", text)
        self.assertIn("Runs per day", text)
        self.assertIn("Failures (2)", text)
        self.assertIn("Retries queued: 1", text)
        self.assertIn("Groq UP", text)
        self.assertIn("4 made, 2 approved, 1 rejected", text)
        self.assertIn("3 packs", text)
        self.assertIn("12 files", text)

    def test_no_money_amounts_in_ops_text(self):
        summary = ops_report.generate_ops_summary(
            db_path=self.db_path,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        text = ops_report.format_ops_text(summary)
        self.assertNotIn("$", text)
        self.assertNotIn("USD", text)
        self.assertNotIn("INR", text)
        self.assertNotIn("Rs", text)

    def test_json_output_serializable(self):
        summary = ops_report.generate_ops_summary(
            db_path=self.db_path,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        dumped = json.dumps(summary)
        reloaded = json.loads(dumped)
        self.assertEqual(reloaded["period_days"], 7)

    def test_main_cli_text_run(self):
        with mock.patch("sys.argv", ["ops_report.py", "--db-path", self.db_path, "--products-dir", self.products_dir, "--marketing-dir", self.marketing_dir]):
            with mock.patch("builtins.print") as mock_print:
                ops_report.main()
                mock_print.assert_called_once()
                printed = mock_print.call_args[0][0]
                self.assertIn("Chilli Gavva Weekly Ops Summary", printed)

    def test_main_cli_json_run(self):
        with mock.patch("sys.argv", ["ops_report.py", "--json", "--db-path", self.db_path, "--products-dir", self.products_dir, "--marketing-dir", self.marketing_dir]):
            with mock.patch("builtins.print") as mock_print:
                ops_report.main()
                mock_print.assert_called_once()
                printed = mock_print.call_args[0][0]
                data = json.loads(printed)
                self.assertEqual(data["period_days"], 7)

    def test_query_error_resilience(self):
        # Corrupt DB or table missing
        bad_conn = sqlite3.connect(":memory:")
        res = ops_report.get_failures_by_step(bad_conn, "2026-11-01")
        self.assertEqual(res, {})
        bad_conn.close()

    def test_retries_count_resilience(self):
        bad_conn = sqlite3.connect(":memory:")
        res = ops_report.get_retries_count(bad_conn)
        self.assertEqual(res, 0)
        bad_conn.close()

    def test_provider_stats_resilience(self):
        bad_conn = sqlite3.connect(":memory:")
        res = ops_report.get_provider_stats(bad_conn, "2026-11-01")
        self.assertEqual(res, {})
        bad_conn.close()

    def test_draft_stats_resilience(self):
        bad_conn = sqlite3.connect(":memory:")
        res = ops_report.get_draft_stats(bad_conn, "2026-11-01")
        self.assertEqual(res["made"], 0)
        self.assertEqual(res["approved"], 0)
        bad_conn.close()

    def test_custom_days_parameter(self):
        now = datetime.datetime(2026, 11, 8, 12, 0, 0)
        # Event 10 days ago
        self.conn.execute("INSERT INTO approvals (ts, title, status) VALUES ('2026-10-29T10:00:00', 'Draft 10d', 'pending')")
        # Event 5 days ago
        self.conn.execute("INSERT INTO approvals (ts, title, status) VALUES ('2026-11-03T10:00:00', 'Draft 5d', 'pending')")
        self.conn.commit()

        # 7-day window
        summary_7 = ops_report.generate_ops_summary(db_path=self.db_path, now=now, days=7)
        self.assertEqual(summary_7["drafts"]["made"], 1)

        # 14-day window
        summary_14 = ops_report.generate_ops_summary(db_path=self.db_path, now=now, days=14)
        self.assertEqual(summary_14["drafts"]["made"], 2)

    def test_runs_per_day_multiple_days_sorting(self):
        now = datetime.datetime(2026, 11, 8, 12, 0, 0)
        for day in ["2026-11-02", "2026-11-05", "2026-11-07"]:
            self.conn.execute(f"INSERT INTO tasks (ts, kind, status) VALUES ('{day}T12:00:00', 'draft', 'finished')")
        self.conn.commit()

        summary = ops_report.generate_ops_summary(db_path=self.db_path, now=now, days=7)
        rpd = summary["runs_per_day"]
        self.assertEqual(len(rpd), 3)
        self.assertEqual(rpd["2026-11-02"], 1)
        self.assertEqual(rpd["2026-11-05"], 1)
        self.assertEqual(rpd["2026-11-07"], 1)


if __name__ == "__main__":
    unittest.main()
