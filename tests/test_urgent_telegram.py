"""Exercise real inbox/notification code through a fake Telegram HTTP server."""
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

import control_inbox
import daily_report
import guardrails
import notify
import run_failure
import telegram_diagnostics as diagnostics
import telegram_inbox as inbox
from scripts import telegram_probe


class Response:
    status = 200

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.data).encode()


class Telegram:
    def __init__(self, updates=()):
        self.pending = list(updates)
        self.sent = []
        self.calls = []
        self.fail = {}
        self.webhook = ""

    def open(self, request, timeout):
        method = request.full_url.rsplit("/", 1)[1]
        params = json.loads(request.data)
        self.calls.append((method, params))
        if method in self.fail:
            raise urllib.error.HTTPError(request.full_url, self.fail[method],
                                         "PRIVATE API DETAIL", {}, io.BytesIO(b"PRIVATE BODY"))
        result = {}
        if method == "getMe":
            result = {"username": "expected_bot"}
        elif method == "getUpdates":
            offset = params.get("offset", 0)
            self.pending = [u for u in self.pending if u["update_id"] >= offset]
            result = self.pending[:params["limit"]]
        elif method == "getWebhookInfo":
            result = {"url": self.webhook}
        elif method == "sendMessage":
            self.sent.append(params)
        return Response({"ok": True, "result": result})


class TelegramIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = str(self.root / "inbox.db")
        self.env = self.enterContext(mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "", "GITHUB_TOKEN": "", "CF_ACCOUNT_ID": "",
            "CF_KV_NAMESPACE_ID": "", "CF_API_TOKEN": "", "CONTROL_SIGNING_KEY": "",
        }))
        self.enterContext(mock.patch.object(notify, "_telegram_configured",
                                           return_value=(True, "PRIVATE-TOKEN", "42")))
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.output))

    def run_server(self, server, recovery=True):
        with mock.patch("urllib.request.urlopen", side_effect=server.open):
            return inbox.run(self.db, recovery=recovery)

    def test_button_approves_real_draft_and_sends_reply_through_http(self):
        approval = guardrails.request_approval("public_draft", "Private draft", "Private body", db_path=self.db)
        server = Telegram([{"update_id": 10, "callback_query": {
            "id": "PRIVATE-QUERY", "data": f"a:{approval}", "message": {"chat": {"id": 42}}}}])
        self.assertEqual(self.run_server(server)["commands"], 1)
        self.assertEqual(guardrails.get_approval(approval, self.db)["status"], "approved")
        self.assertEqual(len(server.sent), 1)
        self.assertIn(f"Approved ✓ #{approval}", server.sent[0]["text"])
        self.assertEqual(server.sent[0]["chat_id"], "42")
        self.assertEqual(self.run_server(server)["commands"], 0)
        self.assertEqual(len(server.sent), 1)
        self.assertIn("buttons=1 commands=0 handled=1 messages_sent=1", self.output.getvalue())

    def test_status_replies_and_reports_safe_counts_on_an_empty_second_run(self):
        server = Telegram([{"update_id": 20, "message": {"chat": {"id": 42}, "text": "/status"}}])
        self.run_server(server)
        self.assertIn("Chilli Gavva Status", server.sent[0]["text"])
        self.assertIn("updates=1 buttons=0 commands=1 handled=1 messages_sent=1", self.output.getvalue())
        self.output.seek(0)
        self.output.truncate()
        self.run_server(server)
        logged = self.output.getvalue()
        self.assertIn("sendMessage HTTP=not_called", logged)
        self.assertIn("updates=0 buttons=0 commands=0 handled=0 messages_sent=0", logged)
        for line in logged.splitlines():
            if line.startswith("Telegram diagnostic:"):
                self.assertTrue(diagnostics.safe_line(line), line)

    def test_setup_failures_do_not_acknowledge_or_lose_updates(self):
        for method, code in (("getMe", 401), ("getChat", 400), ("getChat", 403), ("getUpdates", 409)):
            server = Telegram([{"update_id": 30, "message": {"chat": {"id": 42}, "text": "/status"}}])
            server.fail[method] = code
            with self.subTest(method=method, code=code), self.assertRaises(RuntimeError):
                self.run_server(server)
            self.assertEqual(len(server.pending), 1)
            self.assertIsNone(inbox.get_state("update_offset", db_path=self.db))
            self.assertFalse(server.sent)
            self.assertIn(f"{method} HTTP={code}x1", self.output.getvalue())
        for value in ("PRIVATE-TOKEN", "PRIVATE API DETAIL", "PRIVATE BODY", "/status"):
            self.assertNotIn(value, self.output.getvalue())

    def test_webhook_is_identified_without_printing_address_or_consuming_updates(self):
        server = Telegram([{"update_id": 31}])
        server.webhook = "https://private.example/secret-path"
        with self.assertRaisesRegex(RuntimeError, "webhook blocks"):
            self.run_server(server)
        self.assertIn("webhook=set", self.output.getvalue())
        self.assertNotIn(server.webhook, self.output.getvalue())
        self.assertNotIn("getUpdates", [method for method, _ in server.calls])

    def test_send_failure_makes_inbox_fail_instead_of_green(self):
        server = Telegram([{"update_id": 40, "message": {"chat": {"id": 42}, "text": "/help"}}])
        server.fail["sendMessage"] = 400
        with self.assertRaisesRegex(RuntimeError, "message delivery failed"):
            self.run_server(server)
        self.assertIn("sendMessage HTTP=400x1", self.output.getvalue())
        self.assertIn("errors=invalid_requestx1", self.output.getvalue())
        self.assertIn("messages_sent=0", self.output.getvalue())

    def test_daily_report_uses_one_ist_receipt_across_utc_midnight_and_stale_database(self):
        server = Telegram()
        sent_days = []
        def claim(day, db):
            if day in sent_days:
                return False
            sent_days.append(day)
            return True
        # 01:00 IST and 09:30 IST are one local day, on different UTC dates.
        for now in (datetime.datetime(2026, 11, 3, 19, 30), datetime.datetime(2026, 11, 4, 4)):
            inbox.set_state("daily_report_day", "2026-11-02", self.db)
            with mock.patch("urllib.request.urlopen", side_effect=server.open), \
                    mock.patch.object(daily_report, "report_text", return_value="Daily health report"):
                daily_report.maybe_send(lambda text: inbox.send_text("PRIVATE-TOKEN", "42", text),
                                        inbox.get_state, inbox.set_state, self.db, now=now, claim=claim)
        self.assertEqual(sent_days, ["2026-11-04"])
        self.assertEqual(len(server.sent), 1)

    def test_daily_schedule_waits_until_nine_ist_and_does_not_reset_at_utc_midnight(self):
        last_day = "2026-11-04"
        self.assertFalse(daily_report.due(last_day, datetime.datetime(2026, 11, 4, 20)))
        self.assertFalse(daily_report.due(last_day, datetime.datetime(2026, 11, 5, 3, 29)))
        self.assertTrue(daily_report.due(last_day, datetime.datetime(2026, 11, 5, 3, 30)))
        self.assertEqual(daily_report.delivery_day(datetime.datetime(2026, 11, 4, 20)), "2026-11-05")
        self.assertFalse(daily_report.due("2026-11-05", datetime.datetime(2026, 11, 5, 4)))

    def test_normal_run_sends_daily_report_once_with_real_transport(self):
        server = Telegram()
        with mock.patch.object(daily_report, "_now", return_value=datetime.datetime(2026, 11, 4, 4)), \
                mock.patch.object(daily_report, "report_text", return_value="Daily health report"), \
                mock.patch("improver_loop.maybe_run_weekly", return_value=False), \
                mock.patch("autofix.poll"), mock.patch("product_pipeline.notify_ready"):
            self.assertTrue(self.run_server(server, recovery=False)["daily_report"])
            self.assertFalse(self.run_server(server, recovery=False)["daily_report"])
        self.assertEqual(len(server.sent), 1)
        self.assertTrue(Path(self.db + ".daily-receipts/2026-11-04").is_file())

    def test_failed_announcement_is_not_marked_announced(self):
        approval = guardrails.request_approval("public_draft", "Private draft", "Private body",
                                              review="score 80/100", db_path=self.db)
        server = Telegram()
        server.fail["sendMessage"] = 400
        with mock.patch("urllib.request.urlopen", side_effect=server.open):
            self.assertEqual(inbox.alert_new_drafts("PRIVATE-TOKEN", "42", self.db), 0)
        self.assertIsNone(inbox.get_state("last_alerted_id", db_path=self.db))
        server.fail.clear()
        with mock.patch("urllib.request.urlopen", side_effect=server.open):
            self.assertEqual(inbox.alert_new_drafts("PRIVATE-TOKEN", "42", self.db), 1)
        self.assertEqual(inbox.get_state("last_alerted_id", db_path=self.db), str(approval))

    def test_both_senders_reject_scrubbed_empty_text_without_http(self):
        with mock.patch("public_export.scrub_text", return_value=""), \
                mock.patch("urllib.request.urlopen") as transport, diagnostics.session() as state:
            self.assertFalse(inbox.send_text("PRIVATE-TOKEN", "42", "hidden"))
            self.assertFalse(notify.send("hidden"))
        transport.assert_not_called()
        self.assertEqual(state["errors"]["empty_text"], 2)

    def test_probe_compares_bot_and_peeks_without_offset_or_sending(self):
        server = Telegram([{"update_id": 70, "message": {"chat": {"id": 42}, "text": "/status"}}])
        with mock.patch("urllib.request.urlopen", side_effect=server.open):
            self.assertFalse(telegram_probe.probe("PRIVATE-TOKEN", "42", "different_bot"))
            self.assertNotIn("getUpdates", [method for method, _ in server.calls])
            self.assertTrue(telegram_probe.probe("PRIVATE-TOKEN", "42", "expected_bot"))
        self.assertEqual(len(server.pending), 1)
        polls = [params for method, params in server.calls if method == "getUpdates"]
        self.assertEqual(len(polls), 1)
        self.assertNotIn("offset", polls[0])
        self.assertFalse(server.sent)
        self.assertNotIn("PRIVATE-TOKEN", self.output.getvalue())
        self.assertNotIn("expected_bot", self.output.getvalue())
        self.assertNotIn("/status", self.output.getvalue())

    def test_failed_wrapper_emits_each_diagnostic_once(self):
        line = "Telegram diagnostic: sendMessage HTTP=400x1"
        result = subprocess.CompletedProcess([], 1, stdout=line + "\nRuntimeError: delivery failed\n")
        with mock.patch.object(run_failure.subprocess, "run", return_value=result), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_failure.run_step("inbox", ["unused"], self.root / "summary"), 1)
        self.assertEqual(output.getvalue().count(line), 1)

    def test_workflow_preserves_only_fixed_safe_diagnostic_lines_on_success(self):
        self.run_server(Telegram())
        safe = [line for line in self.output.getvalue().splitlines() if diagnostics.safe_line(line)]
        self.assertEqual(len(safe), 10)
        unsafe = ["Telegram diagnostic: getMe HTTP=200 PRIVATE-TOKEN", "Telegram diagnostic: errors=PRIVATE-TOKEN",
                  "Telegram diagnostic: getUpdates HTTP=200x1\r::warning::PRIVATE-TOKEN"]
        raw = "\n".join(safe + unsafe + ["PRIVATE MESSAGE TEXT", "::warning::PRIVATE-TOKEN"])
        result = subprocess.CompletedProcess([], 0, stdout=raw)
        with mock.patch.object(run_failure.subprocess, "run", return_value=result), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_failure.run_step("inbox", ["unused"], self.root / "summary"), 0)
        logged = output.getvalue()
        for line in safe:
            self.assertIn(line, logged)
        self.assertNotIn("PRIVATE", logged)
        self.assertNotIn("::warning::", logged)


class KvRequestTests(unittest.TestCase):
    def test_list_request_has_encoded_prefix_and_cursor_and_valid_limit(self):
        calls = []
        def http(url, token, method, data):
            calls.append(url)
            return json.dumps({"success": True, "result": [],
                               "result_info": {"cursor": "a+/=&" if len(calls) == 1 else ""}})
        env = {"CF_ACCOUNT_ID": " " + "a" * 32 + "\n", "CF_KV_NAMESPACE_ID": " " + "b" * 32 + "\n",
               "CF_API_TOKEN": " fixture-token\n", "CONTROL_SIGNING_KEY": "fixture-signing"}
        self.assertEqual(control_inbox.fetch(env, http), [])
        self.assertEqual(len(calls), 2)
        for url in calls:
            parsed = urllib.parse.urlsplit(url)
            self.assertIn("/accounts/" + "a" * 32 + "/storage/kv/namespaces/" + "b" * 32 + "/keys", parsed.path)
            self.assertEqual(urllib.parse.parse_qs(parsed.query)["prefix"], ["control:request:"])
            self.assertEqual(urllib.parse.parse_qs(parsed.query)["limit"], ["1000"])
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(calls[1]).query)["cursor"], ["a+/=&"])

    def test_http400_identifies_route_and_id_shape_without_private_details(self):
        for value_read in (False, True):
            calls = []
            def http(url, token, method, data):
                calls.append(url)
                if value_read and len(calls) == 1:
                    return json.dumps({"result": [{"name": "control:request:private"}]})
                raise urllib.error.HTTPError(url, 400, "PRIVATE ERROR", {}, io.BytesIO(b"PRIVATE BODY"))
            env = {"CF_ACCOUNT_ID": "a" * 32, "CF_KV_NAMESPACE_ID": "PRIVATE namespace name",
                   "CF_API_TOKEN": "PRIVATE TOKEN", "CONTROL_SIGNING_KEY": "PRIVATE SIGNING"}
            with contextlib.redirect_stdout(io.StringIO()) as output:
                control_inbox.fetch(env, http)
            logged = output.getvalue()
            kind = "read_request" if value_read else "list_keys"
            self.assertIn(f"request={kind} HTTP=400 account_id=valid namespace_id=invalid", logged)
            self.assertNotIn("PRIVATE", logged)
            self.assertNotIn("control:request:private", logged)


if __name__ == "__main__":
    unittest.main()
