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
        self.assertEqual(names, ["Groq", "Gemini", "Cloudflare", "OpenRouter", "FreeLLMAPI"])
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
        fake = _FakeResp(_make_groq_response("ocean ecosystem article"))
        with patch.dict("os.environ", {"GEMINI_API_KEY": "AIza_real"}):
            with patch("urllib.request.urlopen", return_value=fake):
                result = llm_router._gemini("write about fish")
        self.assertEqual(result, "ocean ecosystem article")

    def test_empty_response_returns_none(self):
        fake = _FakeResp(_make_groq_response(""))
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

    def test_groq_is_tried_first(self):
        with patch.object(llm_router, "_groq", return_value="groq result"):
            with patch.object(llm_router, "_gemini", return_value="gemini result"):
                result = llm_router.chat("hello")
        self.assertEqual(result, "groq result")

    def test_gemini_answers_when_groq_fails(self):
        with patch.object(llm_router, "_groq", return_value=None):
            with patch.object(llm_router, "_gemini", return_value="gemini result"):
                result = llm_router.chat("hello")
        self.assertEqual(result, "gemini result")

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


# ---------------------------------------------------------------------------
# Size routing (small = 8B, large = 70B with fallback to 8B)
# ---------------------------------------------------------------------------
class TestSizeRouting(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict("os.environ", {"GROQ_API_KEY": "gsk_test"}, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _models_called(self, mock_urlopen):
        return [json.loads(c.args[0].data.decode())["model"] for c in mock_urlopen.call_args_list]

    def test_small_uses_8b_model(self):
        with patch("urllib.request.urlopen", return_value=_FakeResp(_make_groq_response("ok"))) as m:
            self.assertEqual(llm_router._groq("hi", size="small"), "ok")
        self.assertEqual(self._models_called(m), ["openai/gpt-oss-20b"])

    def test_default_size_is_small(self):
        with patch("urllib.request.urlopen", return_value=_FakeResp(_make_groq_response("ok"))) as m:
            llm_router._groq("hi")
        self.assertEqual(self._models_called(m), ["openai/gpt-oss-20b"])

    def test_large_uses_70b_model(self):
        with patch("urllib.request.urlopen", return_value=_FakeResp(_make_groq_response("big"))) as m:
            self.assertEqual(llm_router._groq("hi", size="large"), "big")
        self.assertEqual(self._models_called(m), ["openai/gpt-oss-120b"])

    def test_large_falls_back_to_small_on_error(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 400, "bad", {}, None)
        with patch("urllib.request.urlopen", side_effect=[err, _FakeResp(_make_groq_response("small ok"))]) as m:
            self.assertEqual(llm_router._groq("hi", size="large"), "small ok")
        self.assertEqual(self._models_called(m), ["openai/gpt-oss-120b", "openai/gpt-oss-20b"])

    def test_env_overrides_models(self):
        with patch.dict("os.environ", {"GROQ_MODEL_LARGE": "my-large", "GROQ_MODEL_SMALL": "my-small"}):
            self.assertEqual(llm_router._groq_models("large"), ["my-large", "my-small"])
            self.assertEqual(llm_router._groq_models("small"), ["my-small"])

    def test_pick_size(self):
        self.assertEqual(llm_router.pick_size("short"), "small")
        self.assertEqual(llm_router.pick_size("x" * 5000), "large")
        self.assertEqual(llm_router.pick_size("x" * 5000, "small"), "small")
        self.assertEqual(llm_router.pick_size("short", "large"), "large")
        self.assertEqual(llm_router.pick_size("", "weird"), "small")

    def test_chat_passes_size_to_groq(self):
        with patch.object(llm_router, "_freellmapi", return_value=None):
            with patch.object(llm_router, "_groq", return_value="g") as mg:
                self.assertEqual(llm_router.chat("hi", size="large"), "g")
        mg.assert_called_once_with("hi", size="large")

    def test_chat_auto_size(self):
        with patch.object(llm_router, "_freellmapi", return_value=None):
            with patch.object(llm_router, "_groq", return_value="g") as mg:
                llm_router.chat("hi")
        mg.assert_called_once_with("hi", size="small")


# ---------------------------------------------------------------------------
# Auto-switch: Gemini model list, OpenRouter, cooldown after 429
# ---------------------------------------------------------------------------
import os as _os  # noqa: E402
import tempfile as _tempfile  # noqa: E402
import urllib.error as _uerr  # noqa: E402


def _http429():
    return _uerr.HTTPError("u", 429, "too many", {"retry-after": "0"}, None)


class TestAutoSwitch(unittest.TestCase):
    def setUp(self):
        sys.modules["guardrails"] = _guardrails_stub
        importlib.reload(llm_router)

    def test_gemini_tries_next_model_when_model_missing(self):
        err404 = _uerr.HTTPError("u", 404, "not found", {}, None)
        with patch.dict("os.environ", {"GEMINI_API_KEY": "k", "GEMINI_MODEL": "old-model,new-model"}, clear=True):
            with patch("urllib.request.urlopen", side_effect=[err404, _FakeResp(_make_groq_response("hi"))]) as m:
                self.assertEqual(llm_router._gemini("p"), "hi")
        models = [json.loads(c.args[0].data.decode())["model"] for c in m.call_args_list]
        self.assertEqual(models, ["old-model", "new-model"])
        self.assertIn("v1beta/openai/chat/completions", m.call_args_list[0].args[0].full_url)

    def test_gemini_429_tries_next_model(self):
        llm_router._RATE_LIMITED.discard("Gemini")
        with patch.dict("os.environ", {"GEMINI_API_KEY": "k", "GEMINI_MODEL": "a,b"}, clear=True):
            with patch("urllib.request.urlopen", side_effect=[_http429(), _FakeResp(_make_groq_response("hi"))]) as m:
                self.assertEqual(llm_router._gemini("p"), "hi")
        self.assertEqual(m.call_count, 2)
        self.assertNotIn("Gemini", llm_router._RATE_LIMITED)   # no 30-minute block: another model answered

    def test_gemini_cools_down_only_when_every_model_says_429(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "k", "GEMINI_MODEL": "a,b"}, clear=True):
            with patch("urllib.request.urlopen", side_effect=[_http429(), _http429()]):
                self.assertIsNone(llm_router._gemini("p"))
        self.assertIn("Gemini", llm_router._RATE_LIMITED)
        llm_router._RATE_LIMITED.discard("Gemini")
        err503 = _uerr.HTTPError("u", 503, "busy", {}, None)
        with patch.dict("os.environ", {"GEMINI_API_KEY": "k", "GEMINI_MODEL": "a,b"}, clear=True):
            with patch("urllib.request.urlopen", side_effect=[_http429(), err503]):
                self.assertIsNone(llm_router._gemini("p"))
        self.assertNotIn("Gemini", llm_router._RATE_LIMITED)

    def test_openrouter_needs_key_and_model(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}, clear=True):
            self.assertIsNone(llm_router._openrouter("p"))
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "k", "OPENROUTER_MODEL": "x/y:free"}, clear=True):
            with patch("urllib.request.urlopen", return_value=_FakeResp(_make_groq_response("ok"))) as m:
                self.assertEqual(llm_router._openrouter("p"), "ok")
        self.assertEqual(json.loads(m.call_args.args[0].data.decode())["model"], "x/y:free")

    def test_rate_limited_provider_is_skipped_next_time(self):
        calls = {"groq": 0}

        def fake_groq(prompt, size="small"):
            calls["groq"] += 1
            llm_router._note_429("Groq")
            return None
        with patch.object(llm_router, "_freellmapi", return_value=None), \
                patch.object(llm_router, "_groq", side_effect=fake_groq), \
                patch.object(llm_router, "_gemini", return_value=None), \
                patch.object(llm_router, "_cloudflare", return_value="backup answer"):
            self.assertEqual(llm_router.chat("p"), "backup answer")
            self.assertEqual(llm_router.chat("p"), "backup answer")
        self.assertEqual(calls["groq"], 1)  # second call skipped Groq (cooling down)

    def test_failure_without_429_does_not_cool_down(self):
        with patch.object(llm_router, "_freellmapi", return_value=None), \
                patch.object(llm_router, "_groq", return_value=None), \
                patch.object(llm_router, "_gemini", return_value=None), \
                patch.object(llm_router, "_openrouter", return_value=None), \
                patch.object(llm_router, "_cloudflare", return_value=None):
            self.assertIsNone(llm_router.chat("p"))
        self.assertFalse(llm_router.in_cooldown("Groq"))

    def test_cooldown_is_saved_in_database_and_expires(self):
        tmp = _tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = _os.path.join(tmp.name, "t.db")
        llm_router.set_cooldown("Gemini", minutes=30, db_path=db)
        llm_router._MEM_COOLDOWN.clear()          # simulate a new cloud run (fresh process)
        self.assertTrue(llm_router.in_cooldown("Gemini", db_path=db))
        llm_router.set_cooldown("Gemini", minutes=-1, db_path=db)
        llm_router._MEM_COOLDOWN.clear()
        self.assertFalse(llm_router.in_cooldown("Gemini", db_path=db))

    def test_groq_429_on_both_attempts_marks_rate_limited(self):
        with patch.dict("os.environ", {"GROQ_API_KEY": "gsk"}, clear=True):
            with patch("urllib.request.urlopen", side_effect=[_http429(), _http429()]), \
                    patch("time.sleep"):
                self.assertIsNone(llm_router._groq("p"))
        self.assertIn("Groq", llm_router._RATE_LIMITED)

    def test_openrouter_in_provider_status(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "k", "OPENROUTER_MODEL": "m:free"}, clear=True):
            st = {s["provider"]: s["configured"] for s in llm_router.provider_status()}
        self.assertTrue(st["OpenRouter"])
