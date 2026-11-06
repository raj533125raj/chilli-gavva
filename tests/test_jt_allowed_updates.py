"""Tests for telegram allowed_updates diagnostic reporting and drop risk detection."""
import io
import os
import sys
import unittest
from unittest import mock

import telegram_diagnostics as diagnostics


class AllowedUpdatesDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.patcher = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
        })
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _render_summary(self, allowed_updates_value):
        state = {
            "http": {m: {} for m in diagnostics.METHODS},
            "errors": {},
            "sent": 0,
            "configured": "yes",
            "webhook": "unset",
            "allowed_updates": allowed_updates_value,
        }
        stats = {
            "received": 0,
            "button": 0,
            "commands": 0,
            "handled": 0,
            "skipped": {"not owner chat": 0},
        }
        out = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = out
        try:
            diagnostics.print_summary(state, stats)
        finally:
            sys.stdout = old_stdout
        return out.getvalue().splitlines()

    def test_list_with_callback_query_reports_no_risk_line(self):
        with diagnostics.session() as state:
            diagnostics.webhook({"url": "", "allowed_updates": ["message", "callback_query"]})
            self.assertEqual(state["allowed_updates"], ["message", "callback_query"])
            lines = self._render_summary(state["allowed_updates"])
            self.assertIn("Telegram diagnostic: allowed_updates=['message', 'callback_query']", lines)
            self.assertFalse(any("callback_dropped_risk" in line for line in lines))
            for line in lines:
                if line.startswith("Telegram diagnostic:"):
                    self.assertTrue(diagnostics.safe_line(line), line)

    def test_list_with_message_only_reports_risk_yes(self):
        with diagnostics.session() as state:
            diagnostics.webhook({"url": "", "allowed_updates": ["message"]})
            self.assertEqual(state["allowed_updates"], ["message"])
            lines = self._render_summary(state["allowed_updates"])
            self.assertIn("Telegram diagnostic: allowed_updates=['message']", lines)
            self.assertIn("Telegram diagnostic: callback_dropped_risk=yes", lines)
            for line in lines:
                if line.startswith("Telegram diagnostic:"):
                    self.assertTrue(diagnostics.safe_line(line), line)

    def test_missing_field_reports_default_and_no_risk_line(self):
        with diagnostics.session() as state:
            diagnostics.webhook({"url": ""})
            self.assertEqual(state["allowed_updates"], "default")
            lines = self._render_summary(state["allowed_updates"])
            self.assertIn("Telegram diagnostic: allowed_updates=default", lines)
            self.assertFalse(any("callback_dropped_risk" in line for line in lines))
            for line in lines:
                if line.startswith("Telegram diagnostic:"):
                    self.assertTrue(diagnostics.safe_line(line), line)

    def test_none_field_reports_default_and_no_risk_line(self):
        with diagnostics.session() as state:
            diagnostics.webhook({"url": "", "allowed_updates": None})
            self.assertEqual(state["allowed_updates"], "default")
            lines = self._render_summary(state["allowed_updates"])
            self.assertIn("Telegram diagnostic: allowed_updates=default", lines)
            self.assertFalse(any("callback_dropped_risk" in line for line in lines))
            for line in lines:
                if line.startswith("Telegram diagnostic:"):
                    self.assertTrue(diagnostics.safe_line(line), line)

    def test_token_and_url_never_appear_in_any_diagnostic_line(self):
        secret_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        secret_url = f"https://api.telegram.org/bot{secret_token}/customEndpoint"
        with diagnostics.session() as state:
            diagnostics.webhook({
                "url": secret_url,
                "allowed_updates": ["message", secret_token, secret_url, "callback_query"],
            })
            self.assertEqual(state["webhook"], "set")
            self.assertEqual(state["allowed_updates"], ["message", "callback_query"])
            lines = self._render_summary(state["allowed_updates"])
            rendered_text = "\n".join(lines)
            self.assertNotIn(secret_token, rendered_text)
            self.assertNotIn(secret_url, rendered_text)
            for line in lines:
                if line.startswith("Telegram diagnostic:"):
                    self.assertTrue(diagnostics.safe_line(line), line)


if __name__ == "__main__":
    unittest.main()
