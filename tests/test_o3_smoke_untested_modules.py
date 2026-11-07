"""O3: smoke tests for repo-root modules no test imports.

Each module is imported, its pure entry points are exercised with simple
inputs, and everything that could reach the network, Telegram, the real
database, or git mutations is mocked, isolated to a temp directory, or
limited to read-only commands.
"""
import contextlib
import io
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

import guardrails

ROOT = pathlib.Path(__file__).resolve().parents[1]

PY310 = sys.version_info >= (3, 10)
REQUIRES_PY310 = unittest.skipUnless(PY310, "module uses py3.10+ union annotations")


class UntestedModuleSmokeContract(unittest.TestCase):
    UNTESTED_MODULES = ["log_validation", "office_cubes", "office_policy",
                        "start", "versions"]
    PY310_ONLY_MODULES = ["run_engine", "web_fetcher"]

    def test_modules_import_cleanly(self):
        names = [*self.UNTESTED_MODULES, *(self.PY310_ONLY_MODULES if PY310 else [])]
        for name in names:
            with self.subTest(module=name):
                module = __import__(name)
                self.assertTrue(hasattr(module, "__file__"), name)


class LogValidationSmoke(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = str(pathlib.Path(self.directory.name) / "smoke_validation.db")

    def test_add_then_report_round_trip(self):
        import log_validation
        self.assertEqual(log_validation.TARGET_VISITORS, 1000)
        with mock.patch.object(log_validation, "DB_PATH", self.db), \
                contextlib.redirect_stdout(io.StringIO()):
            log_validation.add("2026-11-01", 85, 4, 30)
        output = io.StringIO()
        with mock.patch.object(log_validation, "DB_PATH", self.db), \
                contextlib.redirect_stdout(output):
            log_validation.report()
        text = output.getvalue()
        self.assertIn("Days logged : 1", text)
        self.assertIn("2026-11-01", text)
        self.assertIn("35.3%", text)
        self.assertIn("13 days to go", text)

    def test_inconsistent_counts_are_rejected_without_touching_the_database(self):
        import log_validation
        self.assertEqual(log_validation.TARGET_SIGNUPS, 50)
        with mock.patch.object(log_validation, "DB_PATH", self.db), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                log_validation.add("2026-11-01", 10, 2, 11)


class OfficeCubesSmoke(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = str(pathlib.Path(self.directory.name) / "smoke_cubes.db")
        guardrails.connect(self.db).close()

    def test_unknown_cube_is_rejected(self):
        import office_cubes
        verdict, reason, _ = office_cubes.apply(
            {"target": "cube:no-such-cube-o3", "action": "cube_on", "value": None}, self.db)
        self.assertEqual((verdict, reason), ("rejected", "unknown cube"))

    def test_registered_cube_toggles_on_and_off(self):
        import cubes
        import office_cubes
        verdict, reason, _ = office_cubes.apply(
            {"target": "cube:executive", "action": "cube_on", "value": None}, self.db)
        self.assertEqual((verdict, reason), ("applied", "cube enabled"))
        self.assertEqual(cubes.get_cube("executive", self.db)["status"], "on")
        verdict, reason, _ = office_cubes.apply(
            {"target": "cube:executive", "action": "cube_off", "value": None}, self.db)
        self.assertEqual((verdict, reason), ("applied", "cube disabled"))
        self.assertEqual(cubes.get_cube("executive", self.db)["status"], "off")

    def test_non_boolean_values_are_rejected(self):
        import office_cubes
        with self.assertRaises(ValueError):
            office_cubes.apply({"target": "cube:executive", "action": "cube_on", "value": True}, self.db)

    def test_planned_cube_is_held_not_applied(self):
        import office_cubes
        verdict, reason, _ = office_cubes.apply(
            {"target": "cube:marketing", "action": "set_cube", "value": "on"}, self.db)
        self.assertEqual((verdict, reason), ("held", "cube has no implemented runtime"))


class OfficePolicySmoke(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = str(pathlib.Path(self.directory.name) / "smoke_policy.db")
        guardrails.connect(self.db).close()

    def test_invalid_target_is_rejected_by_validation(self):
        import office_policy
        with self.assertRaises(ValueError):
            office_policy.apply({"target": "not-robot", "action": "set_policy",
                                 "value": {"autonomy": "manual", "budget_limit": 3, "budget_unit": "calls"}}, self.db)

    def test_policy_update_within_guardrails_is_applied(self):
        import office_policy
        verdict, reason, _ = office_policy.apply(
            {"target": "robot", "action": "set_policy",
             "value": {"autonomy": "manual", "budget_limit": 3, "budget_unit": "calls"}}, self.db)
        self.assertEqual((verdict, reason), ("applied", "autonomy and call budget updated within guardrails"))
        conn = guardrails.connect(self.db)
        stored = dict(conn.execute("SELECT key, value FROM control").fetchall())
        conn.close()
        self.assertEqual(stored.get("office_autonomy"), "manual")
        self.assertEqual(stored.get("office_daily_call_cap"), "3")

    def test_token_budget_is_held_and_oversized_budget_is_rejected(self):
        import office_policy
        held = office_policy.apply({"target": "robot", "action": "set_policy",
                                    "value": {"autonomy": "manual", "budget_limit": 3, "budget_unit": "tokens"}}, self.db)
        self.assertEqual(held[0], "held")
        oversized = office_policy.apply({"target": "robot", "action": "set_policy",
                                         "value": {"autonomy": "manual", "budget_limit": 10 ** 6,
                                                   "budget_unit": "calls"}}, self.db)
        self.assertEqual(oversized[0], "rejected")
        self.assertEqual(oversized[1], "budget exceeds the configured guardrails cap")


class StartSmoke(unittest.TestCase):
    def test_import_and_entry_point_exist(self):
        import start
        self.assertTrue(callable(start.main))
        self.assertIn("--no-browser", start.__doc__)


class RunEngineSmoke(unittest.TestCase):
    @REQUIRES_PY310
    def test_engine_delivery_is_importable_and_exposes_the_orchestrator(self):
        import run_engine
        self.assertTrue(hasattr(run_engine.engine, "CEOOrchestrator"))


class VersionsSmoke(unittest.TestCase):
    def test_read_only_git_helpers_return_the_checked_out_commit(self):
        import versions
        head = versions.git("rev-parse", "HEAD")
        self.assertEqual(len(head), 40)
        self.assertRegex(head, r"^[0-9a-f]{40}$")
        self.assertEqual(versions.git("rev-parse", "HEAD"), versions.git("rev-parse", "HEAD"))

    def test_list_versions_prints_the_commit_history(self):
        import versions
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            versions.list_versions()
        self.assertGreater(len(output.getvalue().splitlines()), 0)

    def test_rollback_dry_run_refuses_an_unknown_target(self):
        import versions
        with self.assertRaises(SystemExit):
            versions.rollback("no-such-target-o3", apply=False)


class WebFetcherSmoke(unittest.TestCase):
    @REQUIRES_PY310
    def test_stripper_removes_hidden_markup_and_unescapes_entities(self):
        import web_fetcher
        stripper = web_fetcher._Stripper()
        stripper.feed(
            "<head><title>hidden</title></head>"
            "<p>Hello &amp; welcome</p>"
            "<script>alert(1)</script>"
            "<style>p { color: red }</style>"
            "<nav>menu items</nav>"
            "<p>Bold text</p>")
        self.assertEqual(stripper.get_text(), "Hello & welcome Bold text")

    def setUp(self):
        self.fake_response_headers = {"Content-Type": "text/html; charset=utf-8"}

        class FakeResponse:
            headers = self.fake_response_headers

            def __enter__(self):
                return self

            def __exit__(self, *exception):
                return False

            def read(self, max_bytes):
                return b"<h1>Hello &amp; welcome</h1><script>alert(1)</script><p>From the real fetcher</p>"

        self.fake_response = FakeResponse()

    @REQUIRES_PY310
    def test_fetch_text_turns_html_into_plain_text_without_network(self):
        import web_fetcher
        with mock.patch.object(web_fetcher, "_LAST_FETCH", -1e9), \
                mock.patch.object(web_fetcher.urllib.request, "urlopen", return_value=self.fake_response):
            text, error = web_fetcher.fetch_text("https://example.invalid/smoke")
        self.assertIsNone(error)
        self.assertEqual(text, "Hello & welcome From the real fetcher")

    @REQUIRES_PY310
    def test_non_text_content_type_reports_an_error(self):
        import web_fetcher
        self.fake_response_headers["Content-Type"] = "image/png"
        with mock.patch.object(web_fetcher, "_LAST_FETCH", -1e9), \
                mock.patch.object(web_fetcher.urllib.request, "urlopen", return_value=self.fake_response):
            text, error = web_fetcher.fetch_text("https://example.invalid/picture")
        self.assertEqual(text, "")
        self.assertIn("Non-text content-type", error)

    @REQUIRES_PY310
    def test_http_error_is_scrubbed_and_returns_empty_text(self):
        import web_fetcher
        failure = web_fetcher.urllib.error.HTTPError("https://example.invalid/missing", 404, "Not Found", None, None)
        with mock.patch.object(web_fetcher, "_LAST_FETCH", -1e9), \
                mock.patch.object(web_fetcher.urllib.request, "urlopen", side_effect=failure):
            text, error = web_fetcher.fetch_text("https://example.invalid/missing")
        self.assertEqual((text, error), ("", "HTTP 404: Not Found"))

    @REQUIRES_PY310
    def test_fetch_words_limits_the_snippet_to_the_requested_count(self):
        import web_fetcher
        self.fake_response.read = lambda max_bytes: b"<p>one two three four five</p>"
        with mock.patch.object(web_fetcher, "_LAST_FETCH", -1e9), \
                mock.patch.object(web_fetcher.urllib.request, "urlopen", return_value=self.fake_response):
            snippet, error = web_fetcher.fetch_words("https://example.invalid/feed", max_words=3)
        self.assertIsNone(error)
        self.assertEqual(snippet, "one two three")

    @REQUIRES_PY310
    def test_timeout_constant_keeps_fetches_bounded(self):
        import web_fetcher
        self.assertEqual(web_fetcher._TIMEOUT, 10)
        self.assertEqual(web_fetcher._MIN_GAP, 3.0)


if __name__ == "__main__":
    unittest.main()
