"""tests/test_t4b_ops_dashboard.py — Unit tests for ops_dashboard.py.

Verifies:
  - Rendering ops_report.py JSON summary to HTML
  - Output path defaults to exported_content/ops/index.html
  - All 6 cards present (runs, failures, providers, drafts, packs, marketing)
  - Tiny inline SVG bar charts (valid SVG tags, viewBox, rects, text)
  - Self-contained HTML (no external JS, no external CSS, no remote fonts/trackers)
  - Zero currency symbols or money amounts (USD, INR, Rs, $, ₹)
  - Atomic writing via temporary file replacement
  - Tenglish house style phrases
  - Dynamic content escaping and resiliency
  - CLI execution and flags
"""

import os
import pathlib
import re
import shutil
import tempfile
import unittest
from unittest import mock

import ops_dashboard
import ops_report

CURRENCY_PATTERN = re.compile(
    r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
    re.IGNORECASE,
)


class TestOpsDashboard(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* and RUNNER_ENVIRONMENT environment variables
        self.env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in list(os.environ.keys()) if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT"},
        )
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.tmp_dir = tempfile.mkdtemp(prefix="test_ops_dash_")
        self.out_file = os.path.join(self.tmp_dir, "exported_content", "ops", "index.html")

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _sample_data(self):
        return {
            "period_days": 7,
            "generated_at": "2026-11-09T10:00:00.000000",
            "runs_per_day": {
                "2026-11-03": 5,
                "2026-11-04": 12,
                "2026-11-05": 8,
                "2026-11-06": 15,
                "2026-11-07": 20,
            },
            "failures": {
                "total": 3,
                "by_step": {"draft": 2, "review": 1},
            },
            "retries_queued": 1,
            "providers": {
                "Groq": {"ok": 85, "fail": 2, "status": "UP"},
                "Gemini": {"ok": 10, "fail": 15, "status": "DOWN"},
            },
            "drafts": {
                "made": 14,
                "approved": 6,
                "rejected": 4,
                "pending": 4,
            },
            "packs_ready": 5,
            "marketing_files": {
                "total_files": 48,
                "carousels": 18,
                "pins": 15,
                "reels": 8,
                "blog_posts": 3,
                "seo_meta": 3,
                "keywords": 1,
                "by_pack": {
                    "pack-budget-planner": 24,
                    "pack-habit-tracker": 24,
                },
            },
        }

    def _empty_data(self):
        return {
            "period_days": 7,
            "generated_at": "2026-11-09T10:00:00.000000",
            "runs_per_day": {},
            "failures": {"total": 0, "by_step": {}},
            "retries_queued": 0,
            "providers": {},
            "drafts": {"made": 0, "approved": 0, "rejected": 0, "pending": 0},
            "packs_ready": 0,
            "marketing_files": {
                "total_files": 0,
                "carousels": 0,
                "pins": 0,
                "reels": 0,
                "blog_posts": 0,
                "seo_meta": 0,
                "keywords": 0,
                "by_pack": {},
            },
        }

    # Test 1: Generate HTML from sample data returns valid non-empty string
    def test_01_render_dashboard_html_returns_string(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        self.assertIsInstance(html_out, str)
        self.assertTrue(html_out.startswith("<!DOCTYPE html>"))
        self.assertTrue(html_out.strip().endswith("</html>"))

    # Test 2: Card 1 - Runs per day exists and contains correct metrics
    def test_02_card_runs_per_day(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        self.assertIn("Runs (Rojuki runs)", html_out)
        self.assertIn("Total: 60", html_out)
        self.assertIn("total task executions", html_out)

    # Test 3: Card 2 - Failures & retries exists and contains failure breakdown
    def test_03_card_failures_and_retries(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        self.assertIn("Failures &amp; Retries", html_out)
        self.assertIn("3 fails", html_out)
        self.assertIn("Failed tasks", html_out)
        self.assertIn("Retries queued", html_out)

    # Test 4: Card 3 - AI Providers displays providers and status badges
    def test_04_card_ai_providers(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        self.assertIn("AI Providers", html_out)
        self.assertIn("Groq", html_out)
        self.assertIn("Gemini", html_out)
        self.assertIn("badge-up", html_out)
        self.assertIn("badge-down", html_out)
        self.assertIn("85 ok", html_out)
        self.assertIn("15 fail", html_out)

    # Test 5: Card 4 - Drafts pipeline shows made, approved, rejected, pending
    def test_05_card_drafts_pipeline(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        self.assertIn("Drafts Pipeline", html_out)
        self.assertIn("14", html_out)
        self.assertIn("Made", html_out)
        self.assertIn("Approved", html_out)
        self.assertIn("Rejected", html_out)
        self.assertIn("Pending review", html_out)

    # Test 6: Card 5 - Upload packs ready shows ready count
    def test_06_card_upload_packs_ready(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        self.assertIn("Upload Packs (Ready packs)", html_out)
        self.assertIn("5 ready", html_out)
        self.assertIn("product.zip + listing.txt ✓", html_out)

    # Test 7: Card 6 - Marketing assets breakdown
    def test_07_card_marketing_assets(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        self.assertIn("Marketing Assets (Promotions)", html_out)
        self.assertIn("Carousel slides", html_out)
        self.assertIn("Pinterest pins", html_out)
        self.assertIn("Reel frames", html_out)
        self.assertIn("Blog posts", html_out)
        self.assertIn("pack-budget-planner", html_out)
        self.assertIn("pack-habit-tracker", html_out)

    # Test 8: Empty data renders gracefully without crashing
    def test_08_empty_data_renders_cleanly(self):
        html_out = ops_dashboard.render_dashboard_html(self._empty_data())
        self.assertIn("Zero failures ✓", html_out)
        self.assertIn("Runs record avvaledu", html_out)
        self.assertIn("direct provider calls em record avvaledu", html_out.lower())

    # Test 9: Zero money amounts or currency symbols anywhere in rendered HTML
    def test_09_no_money_amounts_in_rendered_html(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        match = CURRENCY_PATTERN.search(html_out)
        self.assertIsNone(match, f"Found currency amount in rendered HTML: {match}")

    # Test 10: Zero money amounts in empty data HTML
    def test_10_no_money_amounts_in_empty_html(self):
        html_out = ops_dashboard.render_dashboard_html(self._empty_data())
        match = CURRENCY_PATTERN.search(html_out)
        self.assertIsNone(match, f"Found currency amount in empty HTML: {match}")

    # Test 11: Bar chart SVG generation produces valid SVG markup
    def test_11_bar_chart_svg_markup(self):
        items = [("Mon", 10), ("Tue", 25), ("Wed", 15)]
        svg = ops_dashboard.generate_bar_chart_svg(items, width=300, height=120)
        self.assertTrue(svg.startswith('<svg viewBox="0 0 300 120"'))
        self.assertTrue(svg.endswith("</svg>"))
        self.assertIn('<rect x=', svg)
        self.assertIn('<text x=', svg)
        self.assertIn('Mon', svg)
        self.assertIn('Tue', svg)
        self.assertIn('Wed', svg)

    # Test 12: Bar chart SVG with empty items produces placeholder SVG
    def test_12_bar_chart_svg_empty(self):
        svg = ops_dashboard.generate_bar_chart_svg([], empty_label="No tasks")
        self.assertIn("<svg", svg)
        self.assertIn("No tasks", svg)
        self.assertIn("</svg>", svg)

    # Test 13: Self-contained: No external <script src="...">
    def test_13_no_external_scripts(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        self.assertNotIn("<script", html_out.lower())

    # Test 14: Self-contained: No external CSS link stylesheets
    def test_14_no_external_stylesheets(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        self.assertNotIn("<link", html_out.lower())
        self.assertNotIn("@import", html_out.lower())

    # Test 15: Self-contained: No external remote asset links (ignoring standard SVG xmlns)
    def test_15_no_external_media_urls(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        # Strip allowed XML namespace for SVG
        cleaned = html_out.replace("http://www.w3.org/2000/svg", "")
        self.assertNotIn("http://", cleaned)
        self.assertNotIn("https://", cleaned)
        self.assertNotIn("<img", html_out.lower())

    # Test 16: Atomic write creates destination file
    def test_16_atomic_write_creates_file(self):
        target = ops_dashboard.render_dashboard_to_file(self.out_file, data=self._sample_data())
        self.assertEqual(pathlib.Path(self.out_file), target)
        self.assertTrue(os.path.isfile(self.out_file))
        with open(self.out_file, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Chilli Gavva Robot — Ops Dashboard", content)

    # Test 17: Atomic write leaves no temporary files in directory
    def test_17_atomic_write_no_temp_files(self):
        ops_dashboard.render_dashboard_to_file(self.out_file, data=self._sample_data())
        ops_dir = os.path.dirname(self.out_file)
        files = os.listdir(ops_dir)
        self.assertEqual(files, ["index.html"])
        for fname in files:
            self.assertFalse(fname.startswith(".tmp_ops_dash_"))

    # Test 18: Atomic overwrite updates content cleanly
    def test_18_atomic_write_overwrites_existing(self):
        ops_dashboard.render_dashboard_to_file(self.out_file, data=self._sample_data())
        with open(self.out_file, encoding="utf-8") as f:
            c1 = f.read()
        self.assertIn("Total: 60", c1)

        empty = self._empty_data()
        ops_dashboard.render_dashboard_to_file(self.out_file, data=empty)
        with open(self.out_file, encoding="utf-8") as f:
            c2 = f.read()
        self.assertIn("Total: 0", c2)

    # Test 19: HTML escaping handles special characters safely in step names
    def test_19_html_escaping_steps(self):
        data = self._sample_data()
        data["failures"]["by_step"]["step<x>&alert('bad')"] = 4
        html_out = ops_dashboard.render_dashboard_html(data)
        self.assertNotIn("step<x>", html_out)
        self.assertIn("step&lt;x&gt;&amp;alert(&#x27;bad&#x27;)", html_out)

    # Test 20: HTML escaping handles special characters in provider names
    def test_20_html_escaping_providers(self):
        data = self._sample_data()
        data["providers"]["<script>bad</script>"] = {"ok": 1, "fail": 0, "status": "UP"}
        html_out = ops_dashboard.render_dashboard_html(data)
        self.assertNotIn("<script>bad</script>", html_out)
        self.assertIn("&lt;script&gt;bad&lt;/script&gt;", html_out)

    # Test 21: HTML escaping handles pack IDs in marketing breakdown
    def test_21_html_escaping_packs(self):
        data = self._sample_data()
        data["marketing_files"]["by_pack"]["<img onerror=hack>"] = 3
        html_out = ops_dashboard.render_dashboard_html(data)
        self.assertNotIn("<img onerror", html_out)
        self.assertIn("&lt;img onerror=hack&gt;", html_out)

    # Test 22: Tenglish house style phrases present
    def test_22_tenglish_labels_present(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        self.assertIn("Rojuki runs", html_out)
        self.assertIn("Lopalu", html_out)
        self.assertIn("Promotions", html_out)
        self.assertIn("kosam full ready", html_out)

    # Test 23: Period days parameter reflected in header and labels
    def test_23_period_days_custom(self):
        data = self._sample_data()
        data["period_days"] = 14
        html_out = ops_dashboard.render_dashboard_html(data)
        self.assertIn("Last 14 days summary", html_out)
        self.assertIn("last 14 days lo", html_out)

    # Test 24: Generated at timestamp formatted cleanly
    def test_24_generated_at_timestamp(self):
        html_out = ops_dashboard.render_dashboard_html(self._sample_data())
        self.assertIn("Generated at: 2026-11-09 10:00:00 UTC", html_out)

    # Test 25: Integrates directly with ops_report.generate_ops_summary
    def test_25_integrate_with_ops_report(self):
        db_path = os.path.join(self.tmp_dir, "agent.db")
        summary = ops_report.generate_ops_summary(
            db_path=db_path,
            products_dir=self.tmp_dir,
            marketing_dir=self.tmp_dir,
        )
        html_out = ops_dashboard.render_dashboard_html(summary)
        self.assertIn("Chilli Gavva Robot — Ops Dashboard", html_out)
        self.assertIn("Upload Packs (Ready packs)", html_out)
        self.assertIn("0 ready", html_out)

    # Test 26: render_dashboard_to_file calls ops_report when data is None
    def test_26_render_to_file_fetches_summary_if_none(self):
        db_path = os.path.join(self.tmp_dir, "agent_empty.db")
        target = ops_dashboard.render_dashboard_to_file(
            out_path=self.out_file,
            data=None,
            db_path=db_path,
            days=3,
            products_dir=self.tmp_dir,
            marketing_dir=self.tmp_dir,
        )
        self.assertTrue(target.is_file())
        with open(target, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Last 3 days summary", content)

    # Test 27: Bar chart scaling handles all zeroes without division error
    def test_27_bar_chart_all_zeroes(self):
        items = [("A", 0), ("B", 0)]
        svg = ops_dashboard.generate_bar_chart_svg(items)
        self.assertIn("<svg", svg)
        self.assertIn("A", svg)
        self.assertIn("B", svg)

    # Test 28: Bar chart scaling handles large numbers
    def test_28_bar_chart_large_numbers(self):
        items = [("Day 1", 10000), ("Day 2", 25000)]
        svg = ops_dashboard.generate_bar_chart_svg(items)
        self.assertIn("10000", svg)
        self.assertIn("25000", svg)

    # Test 29: CLI run with arguments
    def test_29_cli_main_execution(self):
        custom_out = os.path.join(self.tmp_dir, "custom_dashboard.html")
        with mock.patch("sys.argv", ["ops_dashboard.py", "--out-path", custom_out, "--days", "5"]):
            with mock.patch("builtins.print") as mock_print:
                ops_dashboard.main()
                self.assertTrue(os.path.isfile(custom_out))
                mock_print.assert_called_once()
                printed = mock_print.call_args[0][0]
                self.assertIn("successfully rendered", printed)

    # Test 30: CLI default output path constant
    def test_30_default_constants(self):
        self.assertEqual(ops_dashboard.DEFAULT_OUT_DIR, "exported_content/ops")
        self.assertEqual(ops_dashboard.DEFAULT_OUT_FILE, "exported_content/ops/index.html")

    # Test 31: Provider chart handles DOWN status correctly
    def test_31_provider_status_down(self):
        data = self._empty_data()
        data["providers"]["Ollama"] = {"ok": 0, "fail": 10, "status": "DOWN"}
        html_out = ops_dashboard.render_dashboard_html(data)
        self.assertIn("Ollama", html_out)
        self.assertIn("badge-down", html_out)
        self.assertIn("DOWN", html_out)

    # Test 32: Pack breakdown handles empty by_pack
    def test_32_marketing_empty_by_pack(self):
        data = self._empty_data()
        html_out = ops_dashboard.render_dashboard_html(data)
        self.assertIn("Files em levu", html_out)


if __name__ == "__main__":
    unittest.main()
