"""Tests for llm_router.py — all offline (no real API calls)."""
import importlib
import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Minimal stub for guardrails so we don't need a real DB
# ---------------------------------------------------------------------------
_guardrails_stub = types.ModuleType("guardrails")
_guardrails_stub.can_call_llm = lambda db_path=None: True
_guardrails_stub.record_call = lambda: None
# Capture real guardrails for restoration after each test
_real_guardrails = sys.modules.get("guardrails")
sys.modules.setdefault("guardrails", _guardrails_stub)

import llm_router  # noqa: E402 (import after stub)
importlib.reload(llm_router)  # pick up stub if module was already loaded


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_groq_response(text: str) -> bytes:
    return json.dumps({
        "choices": [{"message": {"content": text}}]
    }).encode()


def _make_gemini_response(text: str) -> bytes:
    return json.dumps({
        "candidates": [{"content": {"parts": [{"text": text}]}}]
    }).encode()


def _make_cf_response(text: str) -> bytes:
    return json.dumps({
        "result": {"response": text},
        "success": True,
    }).encode()


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class _HTTPError(Exception):
    """Minimal HTTPError stand-in."""
    def __init__(self, code, headers=None):
        self.code = code
        self.headers = headers or {}


# ---------------------------------------------------------------------------
# provider_status
# ---------------------------------------------------------------------------
class TestProviderStatus(unittest.TestCase):
    def test_all_missing(self):
        env = {}
        with patch.dict("os.environ", env, clear=True):
            importlib.reload(llm_router)
            status = llm_router.provider_status()
        names = [s["provider"] for s in status]
        self.assertEqual(names, ["Groq", "Gemini", "Cloudflare"])
        for s in status:
            self.assertFalse(s["configured"])

    def test_groq_configured(self):
        env = {"GROQ_API_KEY": "gsk_real_key"}
        with patch.dict("os.environ", env, clear=True):
            importlib.reload(llm_router)
            status = llm_router.provider_status()
        by_name = {s["provider"]: s for s in status}
        self.assertTrue(by_name["Groq"]["configured"])
        self.assertFalse(by_name["Gemini"]["configured"])
        self.assertFalse(by_name["Cloudflare"]["configured"])

    def test_cloudflare_needs_both_vars(self):
        # Only token, no account id
        env = {"CF_API_TOKEN": "tok", "GROQ_API_KEY": ""}
        with patch.dict("os.environ", env, clear=True):
            importlib.reload(llm_router)
            status = llm_router.provider_status()
        by_name = {s["provider"]: s for s in status}
        self.assertFalse(by_name["Cloudflare"]["configured"])

    def test_cloudflare_both_vars(self):
        env = {"CF_API_TOKEN": "tok", "CF_ACCOUNT_ID": "acc123"}
        with patch.dict("os.environ", env, clear=True):
            importlib.reload(llm_router)
            status = llm_router.provider_status()
        by_name = {s["provider"]: s for s in status}
        self.assertTrue(by_name["Cloudflare"]["configured"])


# ---------------------------------------------------------------------------
# _groq
# ---------------------------------------------------------------------------
class TestGroqProvider(unittest.TestCase):
    def setUp(self):
        importlib.reload(llm_router)

    def test_returns_none_if_no_key(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(llm_router._groq("hello"))

    def test_returns_none_if_placeholder_key(self):
        with patch.dict("os.environ", {"GROQ_API_KEY": "YOUR_KEY_HERE"}):
            self.assertIsNone(llm_router._groq("hello"))

    def test_success(self):
        fake = _FakeResp(_make_groq_response("salmon headline"))
        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk_real"}):
            with patch("urllib.request.urlopen", return_value=fake):
                result = llm_router._groq("write a headline")
        self.assertEqual(result, "salmon headline")

    def test_429_retries_once(self):
        """On 429, should wait and retry; success on second attempt."""
        fake = _FakeResp(_make_groq_response("retry worked"))
        err = _HTTPError(429, {"retry-after": "0.01"})
        import urllib.error as ue
        real_err = ue.HTTPError("url", 429, "Too Many", {}, None)
        real_err.headers = {"retry-after": "0.01"}
        call_count = [0]

        def side_effect(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise real_err
            return fake

        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk_real"}):
            with patch("urllib.request.urlopen", side_effect=side_effect):
                with patch("time.sleep"):
                    result = llm_router._groq("hello")
        self.assertEqual(result, "retry worked")
        self.assertEqual(call_count[0], 2)

    def test_non_429_http_error_returns_none(self):
        import urllib.error as ue
        err = ue.HTTPError("url", 500, "Server Error", {}, None)
        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk_real"}):
            with patch("urllib.request.urlopen", side_effect=err):
                result = llm_router._groq("hello")
        self.assertIsNone(result)

    def test_network_exception_returns_none(self):
        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk_real"}):
            with patch("urllib.request.urlopen", side_effect=OSError("conn refused")):
                result = llm_router._groq("hello")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# _gemini
# ---------------------------------------------------------------------------
class TestGeminiProvider(unittest.TestCase):
    def setUp(self):
        importlib.reload(llm_router)

    def test_returns_none_if_no_key(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(llm_router._gemini("hello"))

    def test_success(self):
        fake = _FakeResp(_make_gemini_response("ocean ecosystem article"))
        with patch.dict("os.environ", {"GEMINI_API_KEY": "AIza_real"}):
            with patch("urllib.request.urlopen", return_value=fake):
                result = llm_router._gemini("write about fish")
        self.assertEqual(result, "ocean ecosystem article")

    def test_empty_response_returns_none(self):
        body = json.dumps({"candidates": [{"content": {"parts": [{"text": ""}]}}]}).encode()
        fake = _FakeResp(body)
        with patch.dict("os.environ", {"GEMINI_API_KEY": "AIza_real"}):
            with patch("urllib.request.urlopen", return_value=fake):
                result = llm_router._gemini("hello")
        self.assertIsNone(result)

    def test_exception_returns_none(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "AIza_real"}):
            with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
                result = llm_router._gemini("hello")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# _cloudflare
# ---------------------------------------------------------------------------
class TestCloudflareProvider(unittest.TestCase):
    def setUp(self):
        importlib.reload(llm_router)

    def test_returns_none_if_no_vars(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(llm_router._cloudflare("hello"))

    def test_returns_none_if_missing_account_id(self):
        with patch.dict("os.environ", {"CF_API_TOKEN": "tok"}, clear=True):
            self.assertIsNone(llm_router._cloudflare("hello"))

    def test_success(self):
        fake = _FakeResp(_make_cf_response("cloudflare fishery text"))
        env = {"CF_API_TOKEN": "tok_real", "CF_ACCOUNT_ID": "acc123"}
        with patch.dict("os.environ", env):
            with patch("urllib.request.urlopen", return_value=fake):
                result = llm_router._cloudflare("write something")
        self.assertEqual(result, "cloudflare fishery text")

    def test_exception_returns_none(self):
        env = {"CF_API_TOKEN": "tok", "CF_ACCOUNT_ID": "acc123"}
        with patch.dict("os.environ", env):
            with patch("urllib.request.urlopen", side_effect=OSError("nope")):
                result = llm_router._cloudflare("hello")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# chat() — the main public function
# ---------------------------------------------------------------------------
class TestChat(unittest.TestCase):
    def setUp(self):
        sys.modules["guardrails"] = _guardrails_stub  # ensure stub is active
        importlib.reload(llm_router)

    def tearDown(self):
        # Restore real guardrails so later test modules see the real one
        if _real_guardrails is not None:
            sys.modules["guardrails"] = _real_guardrails
        elif "guardrails" in sys.modules:
            del sys.modules["guardrails"]

    def test_returns_groq_when_available(self):
        with patch.object(llm_router, "_groq", return_value="groq result"):
            with patch.object(llm_router, "_gemini", return_value="gemini result"):
                result = llm_router.chat("hello")
        self.assertEqual(result, "groq result")

    def test_falls_back_to_gemini_when_groq_fails(self):
        with patch.object(llm_router, "_groq", return_value=None):
            with patch.object(llm_router, "_gemini", return_value="gemini result"):
                result = llm_router.chat("hello")
        self.assertEqual(result, "gemini result")

    def test_falls_back_to_cloudflare_when_groq_and_gemini_fail(self):
        with patch.object(llm_router, "_groq", return_value=None):
            with patch.object(llm_router, "_gemini", return_value=None):
                with patch.object(llm_router, "_cloudflare", return_value="cf result"):
                    result = llm_router.chat("hello")
        self.assertEqual(result, "cf result")

    def test_returns_none_when_all_fail(self):
        with patch.object(llm_router, "_groq", return_value=None):
            with patch.object(llm_router, "_gemini", return_value=None):
                with patch.object(llm_router, "_cloudflare", return_value=None):
                    result = llm_router.chat("hello")
        self.assertIsNone(result)

    def test_respects_daily_cap(self):
        """If guardrails says can't call, return None without hitting any provider."""
        with patch.object(llm_router.guardrails, "can_call_llm", return_value=False):
            with patch.object(llm_router, "_groq") as mock_groq:
                result = llm_router.chat("hello")
        mock_groq.assert_not_called()
        self.assertIsNone(result)

    def test_provider_exception_skips_to_next(self):
        """An uncaught exception from a provider should skip to the next, not crash."""
        def boom(_prompt):
            raise RuntimeError("unexpected crash")

        with patch.object(llm_router, "_groq", side_effect=boom):
            with patch.object(llm_router, "_gemini", return_value="safe"):
                result = llm_router.chat("hello")
        self.assertEqual(result, "safe")


if __name__ == "__main__":
    unittest.main()
