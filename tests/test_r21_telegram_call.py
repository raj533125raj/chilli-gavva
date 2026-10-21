import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import telegram_inbox as inbox  # noqa: E402


class _Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode()


def _http_error(code, headers=None):
    return urllib.error.HTTPError("https://invalid.example/path", code, "test", headers or {}, io.BytesIO(b"{}"))


class TelegramCallTests(unittest.TestCase):
    def test_call_uses_15_second_timeout(self):
        calls = []

        def opener(request, timeout):
            calls.append(timeout)
            return _Response({"ok": True})

        self.assertEqual(inbox._call("fake-token", "getUpdates", {}, opener=opener), {"ok": True})
        self.assertEqual(calls, [15])

    def test_rate_limit_waits_at_most_30_seconds_and_retries_once(self):
        calls, waits = [], []

        def opener(request, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise _http_error(429, {"Retry-After": "45"})
            return _Response({"ok": True})

        result = inbox._call("fake-token", "getUpdates", {}, opener=opener, sleep_fn=waits.append)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(waits, [30])

    def test_server_error_waits_two_seconds_and_retries_once(self):
        calls, waits = [], []

        def opener(request, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise _http_error(503)
            return _Response({"ok": True})

        self.assertEqual(inbox._call("fake-token", "getUpdates", {}, opener=opener,
                                     sleep_fn=waits.append), {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(waits, [2])

    def test_retries_only_once_then_returns_none(self):
        calls, waits = [], []

        def opener(request, timeout):
            calls.append(timeout)
            raise _http_error(502)

        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = inbox._call("fake-token", "getUpdates", {}, opener=opener, sleep_fn=waits.append)
        self.assertIsNone(result)
        self.assertEqual((len(calls), waits), (2, [2]))
        self.assertNotIn("fake-token", stderr.getvalue())

    def test_network_error_logs_one_safe_line_and_returns_none(self):
        def opener(request, timeout):
            raise OSError("request failed with fake-token")

        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = inbox._call("fake-token", "getUpdates", {}, opener=opener, sleep_fn=lambda _: None)
        self.assertIsNone(result)
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)
        self.assertIn("network error: OSError", stderr.getvalue())
        self.assertNotIn("fake-token", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
