"""Tests for the weekly PDF report: chart maths, DB data, PDF output and the plugin."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import report_charts  # noqa: E402
import report_pdf  # noqa: E402
import tg_reportpdf  # noqa: E402

NOW = datetime.datetime(2026, 11, 12, 9, 30, tzinfo=datetime.timezone.utc)  # Thursday, ISO week 46
OWNER = "111222333"
MONEY = re.compile(r"[$₹]|\b\d[\d,.]*\s*(?:USD|INR|Rs\.?|dollars?)\b"
                   r"|\b(?:USD|INR|Rs\.?)\s*\d", re.IGNORECASE)
PIL_OK = report_charts.PIL_AVAILABLE
needs_pillow = unittest.skipUnless(PIL_OK, "Pillow is not installed")


def seed_db(path, rows=None):
    rows = rows or {}
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, "
                 "title TEXT, payload TEXT, status TEXT, review TEXT, decided_ts TEXT, reason TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS product_builds (approval_id INTEGER PRIMARY KEY, status TEXT, "
                 "tries INTEGER, folder TEXT, note TEXT, ts TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS product_deliveries (approval_id INTEGER PRIMARY KEY, status TEXT, "
                 "reason TEXT, folder TEXT, files TEXT, digest TEXT, notified INTEGER, lease_until REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, "
                 "payload TEXT, status TEXT, attempts INTEGER, agent TEXT, result TEXT, error TEXT, priority INTEGER, "
                 "finished_ts TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS provider_log (day TEXT, provider TEXT, ok INTEGER DEFAULT 0, "
                 "fail INTEGER DEFAULT 0, PRIMARY KEY (day, provider))")
    for sql, values in rows.get("sql", []):
        conn.execute(sql, values)
    conn.commit()
    conn.close()


def write_plan(root, posts):
    path = Path(root) / "post_plan.json"
    path.write_text(json.dumps(posts), encoding="utf-8")
    return path


class ChartMathTests(unittest.TestCase):
    def test_nice_max_zero_is_one(self):
        self.assertEqual(report_charts.nice_max(0), 1)

    def test_nice_max_one(self):
        self.assertEqual(report_charts.nice_max(1), 1)

    def test_nice_max_three(self):
        self.assertEqual(report_charts.nice_max(3), 3)

    def test_nice_max_ten(self):
        self.assertEqual(report_charts.nice_max(10), 10)

    def test_nice_max_eleven_rounds_up(self):
        self.assertEqual(report_charts.nice_max(11), 20)

    def test_nice_max_99(self):
        self.assertEqual(report_charts.nice_max(99), 100)

    def test_nice_max_101(self):
        self.assertEqual(report_charts.nice_max(101), 200)

    def test_nice_max_1500(self):
        self.assertEqual(report_charts.nice_max(1500), 2000)

    def test_nice_max_negative_is_minimum(self):
        self.assertEqual(report_charts.nice_max(-5), 1)

    def test_nice_max_none_is_minimum(self):
        self.assertEqual(report_charts.nice_max(None), 1)

    def test_bar_heights_empty(self):
        self.assertEqual(report_charts.bar_heights([], 100), [])

    def test_bar_heights_zero(self):
        self.assertEqual(report_charts.bar_heights([0, 0], 100), [0, 0])

    def test_bar_heights_max_is_full(self):
        self.assertEqual(report_charts.bar_heights([5], 100, top=5), [100])

    def test_bar_heights_proportional(self):
        self.assertEqual(report_charts.bar_heights([1, 2, 4], 100, top=4), [25, 50, 100])

    def test_bar_heights_negative_is_zero(self):
        self.assertEqual(report_charts.bar_heights([-3], 100, top=4), [0])

    def test_bar_heights_clamped_to_top(self):
        self.assertEqual(report_charts.bar_heights([20], 100, top=5), [100])

    def test_line_points_empty(self):
        self.assertEqual(report_charts.line_points([], 100, 100), [])

    def test_line_points_count(self):
        self.assertEqual(len(report_charts.line_points([1, 2, 3], 100, 100)), 3)

    def test_line_points_x_starts_at_pad(self):
        self.assertEqual(report_charts.line_points([1, 2], 100, 100, pad_x=10)[0][0], 10)

    def test_line_points_x_ends_at_edge(self):
        self.assertEqual(report_charts.line_points([1, 2], 100, 100, pad_x=10)[-1][0], 90)

    def test_line_points_top_is_pad(self):
        self.assertEqual(report_charts.line_points([10], 100, 100, pad_y=10, top=10)[0][1], 10)

    def test_line_points_zero_is_bottom(self):
        self.assertEqual(report_charts.line_points([0], 100, 100, pad_y=10, top=10)[0][1], 90)

    def test_line_points_one_value_is_centred(self):
        x = report_charts.line_points([1], 100, 100)[0][0]
        self.assertEqual(x, 50)

    def test_donut_empty(self):
        self.assertEqual(report_charts.donut_segments([]), [])

    def test_donut_zero_total(self):
        self.assertEqual(report_charts.donut_segments([0, 0]), [])

    def test_donut_starts_at_top(self):
        self.assertEqual(report_charts.donut_segments([1])[0][0], -90)

    def test_donut_single_is_full_circle(self):
        start, end, value, index = report_charts.donut_segments([2])[0]
        self.assertEqual((start, end, value, index), (-90, 270.0, 2.0, 0))

    def test_donut_two_is_half_each(self):
        segments = report_charts.donut_segments([1, 1])
        self.assertEqual(segments[0][:2], (-90, 90.0))
        self.assertEqual(segments[1][:2], (90.0, 270.0))

    def test_donut_skips_zero_values(self):
        segments = report_charts.donut_segments([1, 0, 1])
        self.assertEqual([item[3] for item in segments], [0, 2])

    def test_safe_values_replaces_bad_input(self):
        self.assertEqual(report_charts._safe_values(["x", None, -2, 3]), [0.0, 0.0, 0.0, 3.0])

    def test_palette_has_colours(self):
        self.assertGreaterEqual(len(report_charts.PALETTE), 6)


@needs_pillow
class ChartImageTests(unittest.TestCase):
    def test_bar_size(self):
        image = report_charts.bar_chart(["a", "b"], [1, 2], title="t", width=500, height=300)
        self.assertEqual(image.size, (500, 300))

    def test_bar_mode_is_rgb(self):
        self.assertEqual(report_charts.bar_chart(["a"], [1]).mode, "RGB")

    def test_bar_zero_data_does_not_crash(self):
        self.assertEqual(report_charts.bar_chart(["a", "b"], [0, 0]).size, (1000, 420))

    def test_bar_empty_uses_no_data(self):
        image = report_charts.bar_chart([], [])
        self.assertEqual(image.size, (1000, 420))

    def test_bar_long_label_does_not_crash(self):
        image = report_charts.bar_chart(["x" * 200], [3])
        self.assertEqual(image.size, (1000, 420))

    def test_bar_many_labels_does_not_crash(self):
        image = report_charts.bar_chart([f"l{i}" for i in range(30)], list(range(30)))
        self.assertEqual(image.size, (1000, 420))

    def test_line_size(self):
        self.assertEqual(report_charts.line_chart(["a", "b"], [1, 2], width=400, height=200).size, (400, 200))

    def test_line_zero_data_does_not_crash(self):
        self.assertEqual(report_charts.line_chart(["a", "b"], [0, 0]).size, (1000, 400))

    def test_line_empty_does_not_crash(self):
        self.assertEqual(report_charts.line_chart([], []).size, (1000, 400))

    def test_donut_size(self):
        self.assertEqual(report_charts.donut_chart(["a"], [1], width=500, height=300).size, (500, 300))

    def test_donut_zero_data_does_not_crash(self):
        self.assertEqual(report_charts.donut_chart(["a"], [0]).size, (860, 400))

    def test_donut_many_legend_items(self):
        image = report_charts.donut_chart([f"l{i}" for i in range(10)], list(range(1, 11)))
        self.assertEqual(image.size, (860, 400))

    def test_chart_bytes_png_signature(self):
        data = report_charts.chart_bytes(report_charts.bar_chart(["a"], [1]))
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_chart_bytes_are_deterministic(self):
        first = report_charts.chart_bytes(report_charts.bar_chart(["a", "b"], [1, 2]))
        second = report_charts.chart_bytes(report_charts.bar_chart(["a", "b"], [1, 2]))
        self.assertEqual(first, second)

    def test_zero_and_nonzero_charts_differ(self):
        zero = report_charts.chart_bytes(report_charts.bar_chart(["a"], [0]))
        data = report_charts.chart_bytes(report_charts.bar_chart(["a"], [5]))
        self.assertNotEqual(zero, data)

    def test_save_png_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = report_charts.save_png(report_charts.bar_chart(["a"], [1]), Path(tmp) / "a.png")
            self.assertTrue(path.is_file())
            self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))

    def test_save_png_creates_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = report_charts.save_png(report_charts.line_chart(["a"], [1]), Path(tmp) / "x" / "a.png")
            self.assertTrue(path.is_file())

    def test_bar_has_non_white_pixels(self):
        image = report_charts.bar_chart(["a"], [5])
        self.assertNotEqual(image.getpixel((500, 300)), (255, 255, 255))

    def test_donut_has_colour_pixels(self):
        image = report_charts.donut_chart(["a"], [5])
        colours = {image.getpixel((230 + 100, 225))}
        self.assertNotEqual(colours, {(255, 255, 255)})

    def test_chart_unavailable_raises(self):
        with mock.patch.object(report_charts, "PIL_AVAILABLE", False):
            with self.assertRaises(report_charts.ChartUnavailable):
                report_charts.bar_chart(["a"], [1])


class DataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state.db"

    def collect(self, now=NOW, plan=None):
        # Never read the robot's real post plan from the repo: default to a missing temp file.
        if plan is None:
            plan = os.path.join(tempfile.mkdtemp(), "no_post_plan.json")
        return report_pdf.collect_weekly_data(db_path=self.db, now=now, plan_path=plan)

    def test_week_starts_on_monday(self):
        self.assertEqual(report_pdf._week_bounds(NOW)["start"], "2026-11-09")

    def test_week_ends_on_sunday(self):
        self.assertEqual(report_pdf._week_bounds(NOW)["end"], "2026-11-15")

    def test_week_key(self):
        self.assertEqual(report_pdf._week_bounds(NOW)["week_key"], "2026-W46")

    def test_week_label_has_dates(self):
        self.assertIn("09 Nov 2026", report_pdf._week_bounds(NOW)["label"])

    def test_empty_missing_db_is_all_zero(self):
        data = self.collect()
        for key in ("drafts_made", "drafts_approved", "drafts_rejected", "packs_built",
                    "ready", "needs_fix", "posts_planned", "posts_done", "runs_ok", "runs_failed"):
            self.assertEqual(data[key], 0, key)

    def test_empty_provider_dict(self):
        self.assertEqual(self.collect()["providers"], {})

    def test_run_days_always_seven(self):
        self.assertEqual(len(self.collect()["run_days"]), 7)

    def test_run_values_always_seven(self):
        self.assertEqual(len(self.collect()["run_values"]), 7)

    def test_wins_never_empty(self):
        self.assertTrue(self.collect()["wins"])

    def test_problems_never_empty(self):
        self.assertTrue(self.collect()["problems"])

    def test_next_plan_never_empty(self):
        self.assertTrue(self.collect()["next_plan"])

    def test_approval_counts_in_window(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO approvals (ts, status, decided_ts) VALUES (?,?,?)",
             ("2026-11-10T10:00:00", "approved", "2026-11-10T11:00:00")),
            ("INSERT INTO approvals (ts, status, decided_ts) VALUES (?,?,?)",
             ("2026-11-11T10:00:00", "rejected", "2026-11-11T11:00:00")),
        ]})
        data = self.collect()
        self.assertEqual((data["drafts_made"], data["drafts_approved"], data["drafts_rejected"]), (2, 1, 1))

    def test_approval_outside_window_ignored(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO approvals (ts, status) VALUES (?,?)", ("2026-11-01T10:00:00", "approved")),
        ]})
        self.assertEqual(self.collect()["drafts_made"], 0)

    def test_pending_is_global(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO approvals (ts, status) VALUES (?,?)", ("2026-10-01T10:00:00", "pending")),
        ]})
        self.assertEqual(self.collect()["drafts_pending"], 1)

    def test_packs_built_in_window(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO product_builds (approval_id, status, ts) VALUES (?,?,?)",
             (1, "built", "2026-11-10T10:00:00")),
        ]})
        self.assertEqual(self.collect()["packs_built"], 1)

    def test_failed_build_is_needs_fix(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO product_builds (approval_id, status, ts) VALUES (?,?,?)",
             (1, "failed", "2026-11-10T10:00:00")),
        ]})
        self.assertEqual(self.collect()["needs_fix"], 1)

    def test_retry_delivery_is_needs_fix(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO product_deliveries (approval_id, status) VALUES (?,?)", (1, "retry")),
        ]})
        self.assertEqual(self.collect()["needs_fix"], 1)

    def test_ready_delivery_counted(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO product_deliveries (approval_id, status) VALUES (?,?)", (1, "ready")),
        ]})
        self.assertEqual(self.collect()["ready"], 1)

    def test_tasks_done_and_failed(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO tasks (ts, kind, status, finished_ts) VALUES (?,?,?,?)",
             ("2026-11-10T10:00:00", "draft", "done", "2026-11-10T10:01:00")),
            ("INSERT INTO tasks (ts, kind, status, finished_ts) VALUES (?,?,?,?)",
             ("2026-11-10T11:00:00", "build", "failed", "2026-11-10T11:01:00")),
        ]})
        data = self.collect()
        self.assertEqual((data["runs_ok"], data["runs_failed"]), (1, 1))

    def test_runs_per_day_counts(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO tasks (ts, kind, status, finished_ts) VALUES (?,?,?,?)",
             ("2026-11-09T10:00:00", "draft", "done", "2026-11-09T10:01:00")),
            ("INSERT INTO tasks (ts, kind, status, finished_ts) VALUES (?,?,?,?)",
             ("2026-11-09T11:00:00", "draft", "done", "2026-11-09T11:01:00")),
        ]})
        data = self.collect()
        self.assertEqual(data["run_values"], [2, 0, 0, 0, 0, 0, 0])

    def test_failures_by_step(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO tasks (ts, kind, status, finished_ts) VALUES (?,?,?,?)",
             ("2026-11-10T10:00:00", "research", "failed", "2026-11-10T10:01:00")),
        ]})
        self.assertEqual(self.collect()["failures_by_step"], [("research", 1)])

    def test_provider_totals(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO provider_log (day, provider, ok, fail) VALUES (?,?,?,?)",
             ("2026-11-10", "groq", 5, 1)),
            ("INSERT INTO provider_log (day, provider, ok, fail) VALUES (?,?,?,?)",
             ("2026-11-11", "groq", 2, 0)),
        ]})
        data = self.collect()
        self.assertEqual(data["providers"]["groq"], {"ok": 7, "fail": 1, "total": 8})
        self.assertEqual(data["provider_total"], 8)

    def test_provider_outside_week_ignored(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO provider_log (day, provider, ok, fail) VALUES (?,?,?,?)",
             ("2026-10-10", "groq", 5, 1)),
        ]})
        self.assertEqual(self.collect()["providers"], {})

    def test_posts_planned_and_done(self):
        plan = write_plan(self.tmp.name, [
            {"date": "2026-11-09", "status": "done"},
            {"date": "2026-11-10", "status": "todo"},
            {"date": "2026-11-20", "status": "done"},
        ])
        data = self.collect(plan=plan)
        self.assertEqual((data["posts_planned"], data["posts_done"]), (2, 1))

    def test_missing_plan_is_zero(self):
        data = self.collect(plan=Path(self.tmp.name) / "missing.json")
        self.assertEqual((data["posts_planned"], data["posts_done"]), (0, 0))

    def test_bad_plan_is_zero(self):
        path = Path(self.tmp.name) / "bad.json"
        path.write_text("{not json")
        self.assertEqual(self.collect(plan=path)["posts_planned"], 0)

    def test_non_list_plan_is_zero(self):
        path = Path(self.tmp.name) / "dict.json"
        path.write_text('{"date": "2026-11-09"}')
        self.assertEqual(self.collect(plan=path)["posts_planned"], 0)

    def test_missing_tables_are_safe(self):
        sqlite3.connect(str(self.db)).close()
        data = self.collect()
        self.assertEqual(data["drafts_made"], 0)

    def test_big_db_counts(self):
        rows = [("INSERT INTO approvals (ts, status) VALUES (?,?)", ("2026-11-10T10:00:00", "pending"))
                for _ in range(500)]
        seed_db(self.db, {"sql": rows})
        self.assertEqual(self.collect()["drafts_made"], 500)

    def test_money_in_provider_name_is_scrubbed(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO provider_log (day, provider, ok, fail) VALUES (?,?,?,?)",
             ("2026-11-10", "usd 5 provider", 1, 0)),
        ]})
        text = json.dumps(self.collect())
        self.assertNotRegex(text, MONEY)

    def test_money_in_step_name_is_scrubbed(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO tasks (ts, kind, status, finished_ts) VALUES (?,?,?,?)",
             ("2026-11-10T10:00:00", "INR 100 fix", "failed", "2026-11-10T10:01:00")),
        ]})
        text = json.dumps(self.collect())
        self.assertNotRegex(text, MONEY)

    def test_data_is_deterministic(self):
        seed_db(self.db, {"sql": [
            ("INSERT INTO approvals (ts, status) VALUES (?,?)", ("2026-11-10T10:00:00", "approved")),
        ]})
        self.assertEqual(self.collect(), self.collect())

    def test_sunday_belongs_to_same_week(self):
        sunday = datetime.datetime(2026, 11, 15, 23, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(report_pdf._week_bounds(sunday)["week_key"], "2026-W46")

    def test_next_monday_is_new_week(self):
        monday = datetime.datetime(2026, 11, 16, 0, 1, tzinfo=datetime.timezone.utc)
        self.assertEqual(report_pdf._week_bounds(monday)["week_key"], "2026-W47")

    def test_scrub_keeps_placeholder(self):
        self.assertEqual(report_pdf._scrub("price USD 25 now"), "price [amount] now")

    def test_week_bounds_accept_date(self):
        self.assertEqual(report_pdf._week_bounds(datetime.date(2026, 11, 12))["week_key"], "2026-W46")


@needs_pillow
class PdfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state.db"
        self.out = Path(self.tmp.name) / "out"

    def build(self, now=NOW, rows=None):
        if rows:
            seed_db(self.db, rows)
        result = report_pdf.build_weekly_report(db_path=self.db, now=now, out_dir=self.out)
        return result, Path(result["path"]).read_bytes()

    def test_build_returns_path(self):
        result, _ = self.build()
        self.assertTrue(Path(result["path"]).is_file())

    def test_filename_has_iso_week(self):
        result, _ = self.build()
        self.assertEqual(result["filename"], "weekly-report-2026-W46.pdf")

    def test_pdf_header(self):
        _, data = self.build()
        self.assertTrue(data.startswith(b"%PDF-1.4"))

    def test_pdf_eof(self):
        _, data = self.build()
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))

    def test_pdf_has_six_pages(self):
        _, data = self.build()
        self.assertEqual(data.count(b"/Type /Page "), 6)

    def test_pdf_has_pages_object(self):
        _, data = self.build()
        self.assertIn(b"/Type /Pages", data)

    def test_pdf_has_xref(self):
        _, data = self.build()
        self.assertIn(b"\nxref\n0 ", data)
        self.assertIn(b"startxref", data)

    def test_pdf_has_fonts(self):
        _, data = self.build()
        self.assertIn(b"/BaseFont /Helvetica", data)

    def test_pdf_has_embedded_images(self):
        _, data = self.build()
        self.assertGreaterEqual(data.count(b"/Subtype /Image"), 4)

    def test_pdf_images_are_flate(self):
        _, data = self.build()
        self.assertIn(b"/Filter /FlateDecode", data)

    def test_pdf_has_title_metadata(self):
        _, data = self.build()
        self.assertIn(b"/Title (Weekly report 2026-W46)", data)

    def test_pdf_contains_cover_text(self):
        _, data = self.build()
        self.assertIn(b"Weekly Report", data)

    def test_pdf_contains_numbers_page(self):
        _, data = self.build()
        self.assertIn(b"This week in numbers", data)

    def test_pdf_contains_wins_page(self):
        _, data = self.build()
        self.assertIn(b"Top 3 wins", data)

    def test_pdf_contains_next_plan(self):
        _, data = self.build()
        self.assertIn(b"Next week plan", data)

    def test_pdf_is_deterministic_for_fixed_clock(self):
        _, first = self.build()
        _, second = self.build()
        self.assertEqual(hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest())

    def test_pdf_has_no_money_amounts(self):
        _, data = self.build(rows={"sql": [
            ("INSERT INTO provider_log (day, provider, ok, fail) VALUES (?,?,?,?)",
             ("2026-11-10", "usd 5", 1, 0)),
        ]})
        streams = re.findall(rb"<< /Length \d+ >>\nstream\n(.*?)\nendstream", data, flags=re.DOTALL)
        text_ops = []
        for stream in streams:
            text_ops.extend(re.findall(rb"\((.*?)\) Tj", stream))
        visible = b" ".join(text_ops).decode("latin-1")
        self.assertNotRegex(visible, MONEY)

    def test_pdf_does_not_contain_none(self):
        _, data = self.build()
        self.assertNotIn(b"None", data)

    def test_empty_db_still_six_pages(self):
        _, data = self.build()
        self.assertEqual(data.count(b"/Type /Page "), 6)

    def test_big_db_still_six_pages(self):
        rows = [("INSERT INTO approvals (ts, status) VALUES (?,?)", ("2026-11-10T10:00:00", "pending"))
                for _ in range(300)]
        _, data = self.build(rows={"sql": rows})
        self.assertEqual(data.count(b"/Type /Page "), 6)

    def test_big_counts_render(self):
        rows = [("INSERT INTO approvals (ts, status) VALUES (?,?)", ("2026-11-10T10:00:00", "pending"))
                for _ in range(1200)]
        _, data = self.build(rows={"sql": rows})
        self.assertIn(b"1,200", data)

    def test_long_step_name_does_not_crash(self):
        _, data = self.build(rows={"sql": [
            ("INSERT INTO tasks (ts, kind, status, finished_ts) VALUES (?,?,?,?)",
             ("2026-11-10T10:00:00", "x" * 200, "failed", "2026-11-10T10:01:00")),
        ]})
        self.assertTrue(data.startswith(b"%PDF"))

    def test_out_dir_is_created(self):
        result = report_pdf.build_weekly_report(db_path=self.db, now=NOW, out_dir=self.out)
        self.assertTrue(self.out.is_dir())
        self.assertTrue(Path(result["path"]).is_file())

    def test_result_has_data(self):
        result, _ = self.build()
        self.assertEqual(result["data"]["week_key"], "2026-W46")

    def test_result_pages_is_six(self):
        result, _ = self.build()
        self.assertEqual(result["pages"], 6)

    def test_different_week_has_different_filename(self):
        other = report_pdf.report_filename(datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc))
        self.assertEqual(other, "weekly-report-2026-W47.pdf")

    def test_render_from_data_directly(self):
        data = report_pdf.collect_weekly_data(db_path=self.db, now=NOW)
        path = report_pdf.render_weekly_report(data, self.out / "direct.pdf")
        self.assertTrue(path.is_file())

    def test_six_media_boxes(self):
        _, data = self.build()
        self.assertEqual(data.count(b"/MediaBox"), 6)

    def test_report_filename_matches_result(self):
        result, _ = self.build()
        self.assertEqual(result["filename"], report_pdf.report_filename(NOW))

    def test_owner_checklist_present(self):
        _, data = self.build()
        self.assertIn(b"Owner checklist", data)

    def test_pdf_has_provider_page(self):
        _, data = self.build()
        self.assertIn(b"AI calls and failures", data)


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state.db"

    def test_commands_shape(self):
        self.assertIsInstance(tg_reportpdf.COMMANDS, list)
        command = tg_reportpdf.COMMANDS[0]
        self.assertEqual(command["cmd"], "/reportpdf")
        self.assertTrue(command["help_en"])
        self.assertTrue(command["help_te"])

    def test_slash_command_matches(self):
        self.assertTrue(tg_reportpdf.is_report_command("/reportpdf"))

    def test_alias_matches(self):
        self.assertTrue(tg_reportpdf.is_report_command("/weeklypdf"))

    def test_bot_suffix_matches(self):
        self.assertTrue(tg_reportpdf.is_report_command("/reportpdf@my_bot"))

    def test_plain_words_match(self):
        for text in ("report pdf", "weekly pdf", "weekly report", "report pampu"):
            self.assertTrue(tg_reportpdf.is_report_command(text), text)

    def test_case_insensitive(self):
        self.assertTrue(tg_reportpdf.is_report_command("WEEKLY REPORT"))

    def test_unrelated_text_does_not_match(self):
        for text in ("/status", "hello", "report", "/reportpdf now", ""):
            self.assertFalse(tg_reportpdf.is_report_command(text), text)

    def test_handle_returns_none_for_other_text(self):
        self.assertIsNone(tg_reportpdf.handle("hello", {}))

    def test_handle_sends_document(self):
        sent = []
        ctx = {"db_path": str(self.db), "now": NOW,
               "send_document": lambda path, caption="": sent.append((path, caption))}
        result = tg_reportpdf.handle("/reportpdf", ctx)
        self.assertEqual(result, {"handled": True})
        self.assertEqual(len(sent), 1)
        self.assertTrue(Path(sent[0][0]).is_file())

    def test_caption_has_week_and_no_money(self):
        sent = []
        ctx = {"db_path": str(self.db), "now": NOW,
               "send_document": lambda path, caption="": sent.append((path, caption))}
        tg_reportpdf.handle("weekly report", ctx)
        caption = sent[0][1]
        self.assertIn("2026-W46", caption)
        self.assertNotRegex(caption, MONEY)

    def test_missing_send_document_returns_text(self):
        result = tg_reportpdf.handle("/reportpdf", {"db_path": str(self.db), "now": NOW})
        self.assertIn("text", result)

    def test_build_failure_returns_text(self):
        ctx = {"db_path": str(self.db), "now": NOW, "send_document": lambda *a, **k: None}
        with mock.patch.object(tg_reportpdf.report_pdf, "build_weekly_report",
                               side_effect=RuntimeError("boom")):
            result = tg_reportpdf.handle("/reportpdf", ctx)
        self.assertIn("build fail", result["text"])

    def test_module_does_not_import_telegram_inbox(self):
        self.assertNotIn("telegram_inbox", tg_reportpdf.__dict__)

    def test_reachable_through_call_plugins(self):
        import telegram_inbox as ti
        sent = []
        with mock.patch.object(ti, "send_document",
                               side_effect=lambda token, chat, path, caption="": sent.append(path)), \
             mock.patch.object(ti, "send_text", return_value=True):
            handled, result = ti.call_plugins("/reportpdf", "tok", OWNER, db_path=str(self.db),
                                              now=NOW, plugins=[tg_reportpdf])
        self.assertTrue(handled)
        self.assertEqual(result, {"handled": True})
        self.assertEqual(len(sent), 1)
        self.assertTrue(Path(sent[0]).is_file())

    def test_discover_plugins_finds_module(self):
        import telegram_inbox as ti
        names = [module.__name__ for module in ti.discover_plugins()]
        self.assertIn("tg_reportpdf", names)


if __name__ == "__main__":
    unittest.main()
