"""Telegram and reservation servers are simulated; no messages or GitHub refs are created."""
import concurrent.futures
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest import mock

import daily_receipts
import daily_report
import telegram_inbox as inbox

DAY = datetime.datetime(2026, 10, 30, 4)
ROOT = Path(__file__).resolve().parents[1]


def message(uid, text="/help"):
    return {"update_id": uid, "message": {"chat": {"id": 42}, "text": text}}


class Queue:
    def __init__(self, updates):
        self.pending = list(updates)
        self.calls = []
        self.confirmed = -1

    def call(self, token, method, params):
        self.calls.append((method, dict(params)))
        if method == "getUpdates":
            offset = params.get("offset")
            if offset is not None:
                self.confirmed = max(self.confirmed, int(offset) - 1)
                self.pending = [u for u in self.pending if u["update_id"] >= int(offset)]
            return {"ok": True, "result": self.pending[:params.get("limit", 100)]}
        return {"ok": True}


class UpdateDeliveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = str(Path(directory.name) / "inbox.db")
        inbox.set_state("update_offset", 0, self.db)
        self.before = Path(self.db).read_bytes()

    def test_acknowledges_each_update_before_replies_and_survives_failed_save(self):
        button = {"update_id": 11, "callback_query": {"id": "q", "data": "t:ping",
                                                         "message": {"chat": {"id": 42}}}}
        queue = Queue([message(10), button])
        sent = []

        def send(token, chat, text, buttons=None):
            sent.append(queue.confirmed)
            self.assertEqual(inbox.get_state("update_offset", db_path=self.db), str(queue.confirmed + 1))
            return True

        with mock.patch.object(inbox, "_call", side_effect=queue.call), \
                mock.patch.object(inbox, "send_text", side_effect=send):
            self.assertEqual(inbox.process_updates("fixture", "42", self.db), 2)
            self.assertEqual(sent, [10, 11])
            # The encrypted state commit failed: the next runner opens the OLD database.
            Path(self.db).write_bytes(self.before)
            self.assertEqual(inbox.process_updates("fixture", "42", self.db), 0)
        self.assertEqual(len(sent), 2)
        acknowledgements = [p for method, p in queue.calls if method == "getUpdates" and p.get("limit") == 1]
        self.assertEqual([p["offset"] for p in acknowledgements], [11, 12])
        self.assertTrue(all(p["allowed_updates"] == ["message", "callback_query"] for p in acknowledgements))

    def test_crash_after_first_ack_never_replies_again_but_keeps_later_updates(self):
        queue = Queue([message(20), message(21)])
        with mock.patch.object(inbox, "_call", side_effect=queue.call), \
                mock.patch.object(inbox, "send_text", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                inbox.process_updates("fixture", "42", self.db)
        self.assertEqual([u["update_id"] for u in queue.pending], [21])
        Path(self.db).write_bytes(self.before)
        with mock.patch.object(inbox, "_call", side_effect=queue.call), \
                mock.patch.object(inbox, "send_text", return_value=True) as send:
            self.assertEqual(inbox.process_updates("fixture", "42", self.db), 1)
        send.assert_called_once()
        self.assertEqual(queue.confirmed, 21)

    def test_failed_acknowledgement_has_no_effects_and_no_local_offset(self):
        for result in (None, {"ok": False}, OSError("fixture-private-request")):
            with self.subTest(result=type(result).__name__), \
                    mock.patch.object(inbox, "fetch_updates", return_value=[message(1)]), \
                    mock.patch.object(inbox, "_call", side_effect=result if isinstance(result, Exception) else None,
                                      return_value=result), \
                    mock.patch.object(inbox, "handle_command") as command, \
                    mock.patch.object(inbox, "send_text") as send:
                with self.assertRaisesRegex(RuntimeError, "acknowledgement failed") as error:
                    inbox.process_updates("fixture", "42", self.db)
                self.assertNotIn("fixture-private-request", str(error.exception))
                command.assert_not_called()
                send.assert_not_called()
                self.assertEqual(inbox.get_state("update_offset", db_path=self.db), "0")

    def test_failed_poll_stops_scheduled_sends(self):
        with mock.patch.object(inbox.notify, "_telegram_configured", return_value=(True, "fixture", "42")), \
                mock.patch.object(inbox, "_call", return_value=None), \
                mock.patch.object(inbox, "alert_new_drafts") as alerts, \
                mock.patch.object(daily_report, "maybe_send") as report:
            with self.assertRaisesRegex(RuntimeError, "could not be read"):
                inbox.run(self.db)
        alerts.assert_not_called()
        report.assert_not_called()

    def test_new_updates_returned_by_ack_are_left_pending_until_next_poll(self):
        queue = Queue([message(30)])
        original = queue.call

        def call(token, method, params):
            if params.get("limit") == 1 and params.get("offset") == 31:
                queue.pending.append(message(31))
            return original(token, method, params)

        with mock.patch.object(inbox, "_call", side_effect=call), \
                mock.patch.object(inbox, "send_text", return_value=True):
            self.assertEqual(inbox.process_updates("fixture", "42", self.db), 1)
            self.assertEqual([u["update_id"] for u in queue.pending], [31])
            self.assertEqual(inbox.process_updates("fixture", "42", self.db), 1)

    def test_send_message_never_retries_an_ambiguous_server_failure(self):
        opener = mock.Mock(side_effect=urllib.error.HTTPError("https://example.invalid", 503, "fixture", {}, io.BytesIO()))
        wait = mock.Mock()
        with contextlib.redirect_stderr(io.StringIO()):
            result = inbox._call("fixture", "sendMessage", {}, opener=opener, sleep_fn=wait)
        self.assertIsNone(result)
        opener.assert_called_once()
        wait.assert_not_called()


class ReceiptServer:
    def __init__(self, uncertain=False):
        self.refs = set()
        self.uncertain = uncertain

    def call(self, method, path, data=None):
        if method == "GET":
            return {"object": {"sha": "a" * 40}}
        ref = data["ref"]
        if ref in self.refs:
            raise RuntimeError("ref already exists")
        self.refs.add(ref)
        if self.uncertain:
            raise TimeoutError("request timed out after creating the receipt")
        return {"ref": ref}


class DailyDeliveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = str(Path(directory.name) / "daily.db")

    def send(self, sender, claim):
        return daily_report.maybe_send(sender, inbox.get_state, inbox.set_state, self.db,
                                       now=DAY, status_fn=lambda: {}, claim=claim)

    def test_remote_daily_receipt_survives_a_database_save_failure(self):
        server = ReceiptServer()
        claim = lambda day, db: daily_receipts.claim(day, db, env={}, api=server)
        sent = []

        def send(text):
            self.assertEqual(inbox.get_state("daily_report_day", db_path=self.db), "2026-10-30")
            self.assertTrue(server.refs)
            sent.append(text)
            return True

        self.assertTrue(self.send(send, claim))
        inbox.set_state("daily_report_day", "2026-10-29", self.db)  # simulate stale restored state
        self.assertFalse(self.send(send, claim))
        self.assertEqual(len(sent), 1)
        self.assertEqual(server.refs, {"refs/tags/robot-daily-report/2026-10-30"})

    def test_local_receipt_survives_database_rollback_and_has_one_concurrent_winner(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            wins = list(pool.map(lambda _: daily_receipts.claim("2026-10-30", self.db, env={}), range(8)))
        self.assertEqual(wins.count(True), 1)
        self.assertFalse(daily_receipts.claim("2026-10-30", self.db, env={}))
        self.assertTrue(daily_receipts.claim("2026-10-31", self.db, env={}))

    def test_uncertain_reservation_or_missing_cloud_access_never_sends(self):
        server = ReceiptServer(uncertain=True)
        send = mock.Mock(return_value=True)
        claim = lambda day, db: daily_receipts.claim(day, db, env={}, api=server)
        self.assertFalse(self.send(send, claim))
        self.assertFalse(self.send(send, claim))
        send.assert_not_called()
        self.assertFalse(daily_receipts.claim("2026-10-30", self.db, env={"GITHUB_ACTIONS": "true"}))

    def test_uncertain_send_consumes_reservation_even_after_date_rollback(self):
        server = ReceiptServer()
        claim = lambda day, db: daily_receipts.claim(day, db, env={}, api=server)
        send = mock.Mock(side_effect=TimeoutError("simulated lost response"))
        with self.assertRaises(TimeoutError):
            self.send(send, claim)
        inbox.set_state("daily_report_day", "2026-10-29", self.db)
        self.assertFalse(self.send(send, claim))
        send.assert_called_once()


class SingleInboxJobTests(unittest.TestCase):
    def test_recovery_only_processes_updates_and_never_sends_automatic_reports(self):
        with mock.patch.object(inbox.notify, "_telegram_configured", return_value=(True, "fixture", "42")), \
                mock.patch.object(inbox, "process_updates", return_value=1), \
                mock.patch.object(inbox, "alert_new_drafts") as alerts, \
                mock.patch.object(inbox, "remind_waiting_drafts") as reminder, \
                mock.patch.object(daily_report, "maybe_send") as report, \
                contextlib.redirect_stdout(io.StringIO()):
            result = inbox.run(recovery=True)
        self.assertEqual(result["commands"], 1)
        alerts.assert_not_called()
        reminder.assert_not_called()
        report.assert_not_called()

    def test_workflow_allows_only_one_inbox_job_even_when_save_or_inbox_fails(self):
        workflow = (ROOT / ".github/workflows/agent.yml").read_text()
        main = workflow.split("      - name: Telegram inbox", 1)[1].split("      - name:", 1)[0]
        self.assertIn("id: inbox", main)
        self.assertLess(main.index('echo "started=true"'), main.index("python3 run_failure.py"))
        self.assertIn("inbox_started: ${{ steps.inbox.outputs.started }}", workflow)
        recovery = workflow.split("  recovery_inbox:\n", 1)[1].split("  autofix_result:", 1)[0]
        condition = next(line.strip()[4:] for line in recovery.splitlines() if line.strip().startswith("if: "))
        for started, result, event, name, expected in [
            ("true", "failure", "schedule", "", False),  # failed Save state / failed inbox
            ("true", "success", "schedule", "", False),
            ("", "failure", "schedule", "", True),       # tests failed before inbox
            ("", "skipped", "workflow_run", "Robot auto-fix checks", True),
            ("", "skipped", "workflow_run", "Robot auto-fix", False),
            ("", "success", "schedule", "", False),
        ]:
            expression = condition
            for key, value in {"needs.cycle.outputs.inbox_started": started, "needs.cycle.result": result,
                               "github.event_name": event, "github.event.workflow_run.name": name}.items():
                expression = expression.replace(key, repr(value))
            expression = expression.replace("always()", "True").replace("&&", " and ").replace("||", " or ")
            self.assertEqual(eval(expression, {"__builtins__": {}}, {}), expected)
        self.assertIn("python3 telegram_inbox.py --recovery", recovery)


if __name__ == "__main__":
    unittest.main()
