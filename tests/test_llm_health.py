"""Tests for the AI health check and the one-line failure logs in llm_router (offline)."""
import io
import os
import socket
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_router  # noqa: E402
from tests.router_test_support import RouterTestCase

SECRET = "gsk_SECRET_should_never_appear"
ENV = {"GROQ_API_KEY": SECRET, "GEMINI_API_KEY": "AIza_SECRET_too", "GEMINI_MODEL": "gem-a,gem-b",
       "GROQ_MODEL_SMALL": "small-m", "GROQ_MODEL_LARGE": "large-m"}


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError("https://example.invalid", code, "err", {}, io.BytesIO(body))


class HealthTests(RouterTestCase):
    def run_health(self, outcomes):
        """outcomes: model -> (ok, reason, secs) for tiny tests; 'long' key for the long test."""
        calls = []

        def probe(url, headers, body, timeout, parse):
            is_long = body["messages"][0]["content"] == llm_router.LONG_PROMPT
            calls.append((body["model"], is_long, timeout))
            return outcomes["long" if is_long else body["model"]]
        with patch.dict(os.environ, ENV, clear=True):
            return llm_router.health(probe=probe), calls

    def test_one_tiny_test_per_model_then_long_on_groq_large(self):
        results, calls = self.run_health({"small-m": (True, "", 0.4), "large-m": (True, "", 0.9),
                                          "gem-a": (False, "404", 0.2), "gem-b": (True, "", 0.5),
                                          "long": (True, "", 12.0)})
        self.assertEqual([(r["provider"], r["model"], r["test"]) for r in results],
                         [("Groq", "small-m", "tiny"), ("Groq", "large-m", "tiny"), ("Gemini", "gem-a", "tiny"),
                          ("Gemini", "gem-b", "tiny"), ("Groq", "large-m", "long")])
        self.assertEqual(calls[-1], ("large-m", True, 90))
        self.assertTrue(all(c[2] == llm_router._TIMEOUT for c in calls[:-1]))

    def test_long_test_falls_back_to_a_working_model_and_flags_slowness(self):
        results, _ = self.run_health({"small-m": (False, "401", 0.1), "large-m": (False, "401", 0.1),
                                      "gem-a": (True, "", 0.3), "gem-b": (True, "", 0.3),
                                      "long": (True, "", 40.0)})
        long = results[-1]
        self.assertEqual((long["provider"], long["model"], long["ok"]), ("Gemini", "gem-a", True))
        self.assertIn("slower than the old 25s limit", long["reason"])

    def test_long_test_moves_on_when_a_model_is_busy(self):
        seen = []

        def probe(url, headers, body, timeout, parse):
            is_long = body["messages"][0]["content"] == llm_router.LONG_PROMPT
            if is_long:
                seen.append(body["model"])
                return (False, "503 UNAVAILABLE", 1.5) if body["model"] == "gem-a" else (True, "", 20.0)
            return (body["model"].startswith("gem"), "" if body["model"].startswith("gem") else "401", 0.2)
        with patch.dict(os.environ, ENV, clear=True):
            results = llm_router.health(probe=probe)
        self.assertEqual(seen, ["gem-a", "gem-b"])
        self.assertEqual([(r["model"], r["ok"]) for r in results if r["test"] == "long"],
                         [("gem-a", False), ("gem-b", True)])

    def test_no_long_test_when_nothing_works(self):
        results, _ = self.run_health({"small-m": (False, "timeout", 25), "large-m": (False, "timeout", 25),
                                      "gem-a": (False, "404", 0.1), "gem-b": (False, "404", 0.1)})
        self.assertFalse(any(r["test"] == "long" for r in results))

    def test_report_has_no_secrets(self):
        results, _ = self.run_health({"small-m": (True, "", 0.4), "large-m": (False, "429", 0.2),
                                      "gem-a": (False, "403", 0.2), "gem-b": (False, "timeout", 25.0),
                                      "long": (False, "timeout", 90.0)})
        text = llm_router.format_health(results)
        self.assertIn("Groq        large-m", text)
        self.assertIn("FAIL 429", text)
        self.assertIn("FAIL timeout", text)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("AIza", text)

    def test_nothing_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm_router.health(probe=lambda *a: self.fail("no calls")), [])
        self.assertIn("No AI provider", llm_router.format_health([]))

    def test_real_probe_reports_http_status_and_timeout(self):
        with patch("urllib.request.urlopen", side_effect=http_error(404)):
            self.assertEqual(llm_router._probe("https://x.invalid", {}, {}, 1, lambda d: "")[:2], (False, "404"))
        with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            self.assertEqual(llm_router._probe("https://x.invalid", {}, {}, 1, lambda d: "")[:2], (False, "timeout"))
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError(socket.timeout())):
            self.assertEqual(llm_router._probe("https://x.invalid", {}, {}, 1, lambda d: "")[:2], (False, "timeout"))


class ErrorDetailTests(RouterTestCase):
    def test_public_error_codes_are_shown(self):
        groq = b'{"error": {"message": "Invalid API Key", "type": "invalid_request_error", "code": "invalid_api_key"}}'
        self.assertEqual(llm_router._describe(http_error(401, groq)), "401 invalid_api_key")
        google = b'[{"error": {"code": 404, "message": "models/x is not found", "status": "NOT_FOUND"}}]'
        self.assertEqual(llm_router._describe(http_error(404, google)), "404 NOT_FOUND")
        self.assertEqual(llm_router._describe(http_error(403, b"error code: 1010")), "403 cloudflare-1010")
        self.assertEqual(llm_router._describe(http_error(500, b"<html>oops</html>")), "500")

    def test_detail_never_echoes_odd_text(self):
        body = b'{"error": {"code": "key gsk_SECRET leaked here"}}'
        self.assertEqual(llm_router._describe(http_error(400, body)), "400")

    def test_requests_send_a_user_agent(self):
        seen = []

        def fake(req, timeout=None):
            seen.append(req.get_header("User-agent"))
            raise http_error(403)
        with patch.dict(os.environ, ENV, clear=True), redirect_stderr(io.StringIO()), \
                patch("urllib.request.urlopen", fake), \
                patch.object(llm_router.guardrails, "record_call", lambda *a, **k: None):
            llm_router._groq("hi")
            llm_router._gemini("hi")
        self.assertTrue(seen and all(ua == "chilli-gavva/1.0" for ua in seen))

    def test_gemini_model_list_is_filtered(self):
        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        data = b'{"data": [{"id": "models/gemini-2.5-flash"}, {"id": "models/gemini-2.5-flash-preview-tts"}, ' \
               b'{"id": "models/text-embedding-004"}, {"id": "models/gemini-2.0-flash"}]}'
        with patch.dict(os.environ, ENV, clear=True):
            names = llm_router.gemini_model_names(opener=lambda req, timeout=None: Resp(data))
        self.assertEqual(names, ["gemini-2.5-flash", "gemini-2.0-flash"])

    def test_groq_model_list_is_filtered_and_safe(self):
        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        data = b'{"data": [{"id": "llama-9-70b"}, {"id": "whisper-large-v3"}, {"id": "bad id with spaces"}, ' \
               b'{"id": "openai/gpt-oss-20b"}, {"id": "llama-guard-4"}]}'
        with patch.dict(os.environ, ENV, clear=True):
            names = llm_router.groq_model_names(opener=lambda req, timeout=None: Resp(data))
        self.assertEqual(names, ["llama-9-70b", "openai/gpt-oss-20b"])
        with patch.dict(os.environ, ENV, clear=True):
            out = llm_router.groq_model_names(opener=lambda req, timeout=None: (_ for _ in ()).throw(http_error(401)))
        self.assertEqual(out, ["(could not list models: 401)"])
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm_router.groq_model_names(), [])

class GeminiFallbackTests(RouterTestCase):
    def test_busy_or_retired_model_moves_to_next(self):
        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        answers = [http_error(503, b'[{"error": {"status": "UNAVAILABLE"}}]'), http_error(404),
                   Resp(b'{"choices": [{"message": {"content": "draft"}}]}')]
        seen = []

        def fake(req, timeout=None):
            import json as _json
            seen.append((_json.loads(req.data)["model"], timeout))
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        env = dict(ENV, GEMINI_MODEL="m1,m2,m3")
        with patch.dict(os.environ, env, clear=True), redirect_stderr(io.StringIO()), \
                patch("urllib.request.urlopen", fake), \
                patch.object(llm_router.guardrails, "record_call", lambda *a, **k: None):
            self.assertEqual(llm_router._gemini("p", timeout=60), "draft")
        self.assertEqual(seen, [("m1", 60), ("m2", 60), ("m3", 60)])

    def test_auth_problem_stops(self):
        with patch.dict(os.environ, dict(ENV, GEMINI_MODEL="m1,m2"), clear=True), redirect_stderr(io.StringIO()), \
                patch("urllib.request.urlopen", side_effect=[http_error(403)]) as m, \
                patch.object(llm_router.guardrails, "record_call", lambda *a, **k: None):
            self.assertIsNone(llm_router._gemini("p"))
        self.assertEqual(m.call_count, 1)

    def test_chat_gives_gemini_the_long_timeout_for_large_drafts(self):
        calls = []
        with patch.object(llm_router.guardrails, "can_call_llm", lambda *a, **k: True), \
                patch.object(llm_router, "in_cooldown", lambda *a, **k: False), \
                patch.object(llm_router, "_freellmapi", lambda p: None), \
                patch.object(llm_router, "_groq", lambda p, size=None: None), \
                patch.object(llm_router, "_gemini", lambda p, timeout=None: calls.append(timeout) or "ok"):
            self.assertEqual(llm_router.chat("p", size="large"), "ok")
            self.assertEqual(llm_router.chat("p", size="small"), "ok")
        self.assertEqual(calls, [60, None])

    def test_default_models_skip_the_retired_one(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("gemini-2.5-flash", llm_router._gemini_models())

    def test_default_groq_models_are_current(self):
        from pathlib import Path
        router_text = Path(llm_router.__file__).read_text(encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm_router._groq_models(llm_router.LARGE)[0], "openai/gpt-oss-120b")
            self.assertEqual(llm_router._groq_models(llm_router.SMALL)[0], "openai/gpt-oss-20b")
        for name in ("llama-3.1-8b-instant", "llama-3.3-70b-versatile"):  # retired by Groq (404)
            self.assertNotIn(name, router_text)


class PrivacyTests(RouterTestCase):
    def test_scrub_hides_secrets_and_chat_id(self):
        env = {"TELEGRAM_CHAT_ID": "123456789", "GROQ_API_KEY": "gsk_abcdefghijklmnop1234"}
        text = ("chat 123456789 key gsk_abcdefghijklmnop1234 other gsk_ZZZZZZZZZZZZZZZZZZZZ "
                "bot 1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA ghp_aaaaaaaaaaaaaaaaaaaaaaaa")
        with patch.dict(os.environ, env, clear=True):
            out = llm_router.scrub(text)
        for secret in ("123456789", "gsk_", "AAAAAAAAAA", "ghp_"):
            self.assertNotIn(secret, out)
        self.assertIn("chat [hidden]", out)

    def test_chat_sends_scrubbed_prompt(self):
        sent = []
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "987654321"}, clear=True), \
                patch.object(llm_router.guardrails, "can_call_llm", lambda *a, **k: True), \
                patch.object(llm_router, "in_cooldown", lambda *a, **k: False), \
                patch.object(llm_router, "_gemini", lambda p, timeout=None: sent.append(p) or "ok"):
            llm_router.chat("my chat id is 987654321")
        self.assertEqual(sent, ["my chat id is [hidden]"])

    def test_dashboard_chat_prompt_has_no_earnings(self):
        import chat
        seen = []
        status = {"money": {"real_usd": 42.5}, "pending_approvals": [], "agents": [], "task_counts": {},
                  "kill_switch": False}
        chat.reply("tell me a joke", status, llm=lambda p: seen.append(p) or "ha")
        self.assertNotIn("42.5", seen[0])


class FailureLogTests(RouterTestCase):
    def test_groq_failure_prints_one_line_without_key(self):
        err = io.StringIO()
        with patch.dict(os.environ, ENV, clear=True), redirect_stderr(err), \
                patch("urllib.request.urlopen", side_effect=http_error(401)), \
                patch.object(llm_router.guardrails, "record_call", lambda *a, **k: None):
            self.assertIsNone(llm_router._groq("hi", size=llm_router.LARGE))
        lines = err.getvalue().strip().splitlines()
        self.assertEqual(lines, ["LLM Groq large-m: FAIL 401", "LLM Groq small-m: FAIL 401"])
        self.assertNotIn(SECRET, err.getvalue())

    def test_gemini_timeout_is_named(self):
        err = io.StringIO()
        with patch.dict(os.environ, ENV, clear=True), redirect_stderr(err), \
                patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")), \
                patch.object(llm_router.guardrails, "record_call", lambda *a, **k: None):
            self.assertIsNone(llm_router._gemini("hi"))
        # a timed-out model is skipped and the next one is tried
        self.assertEqual(err.getvalue().strip().splitlines(),
                         ["LLM Gemini gem-a: FAIL timeout", "LLM Gemini gem-b: FAIL timeout"])

    def test_large_requests_get_the_long_timeout(self):
        seen = []

        def fake_call(prompt, key, model, timeout=llm_router._TIMEOUT):
            seen.append(timeout)
            return None
        with patch.dict(os.environ, ENV, clear=True), patch.object(llm_router, "_groq_call", fake_call):
            llm_router._groq("hi", size=llm_router.LARGE)
            llm_router._groq("hi", size=llm_router.SMALL)
        self.assertEqual(seen, [llm_router._TIMEOUT_LARGE, llm_router._TIMEOUT_LARGE, llm_router._TIMEOUT])
        self.assertEqual(llm_router._TIMEOUT_LARGE, 60)


if __name__ == "__main__":
    unittest.main()
