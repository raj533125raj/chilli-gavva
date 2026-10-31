"""Offline router resilience tests: real transports with fake HTTP responses, temp state."""
import concurrent.futures
import contextvars
import io
import json
import os
import socket
import sqlite3
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr
from unittest.mock import patch

import llm_router as router
from tests.router_test_support import RouterTestCase


class Response(io.BytesIO):
    def __exit__(self, *args):
        self.close()


def answer(text="answer"):
    return Response(json.dumps({"choices": [{"message": {"content": text}}]}).encode())


def cf_answer(text="answer"):
    return Response(json.dumps({"success": True, "result": {"response": text}}).encode())


def http_error(status, code=None, message="private request text"):
    data = {"errors": [{"code": code, "message": message}]} if code else {}
    return urllib.error.HTTPError("https://example.invalid", status, "private error",
                                  {"retry-after": "0"}, io.BytesIO(json.dumps(data).encode()))


class RouterResilienceTests(RouterTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.enterContext(patch.object(router.guardrails, "_TESTING", True, create=True))
        self.enterContext(patch.object(router.guardrails, "can_call_llm", return_value=True))
        self.record = self.enterContext(patch.object(router.guardrails, "record_call"))
        self.enterContext(patch.object(router, "GUARD_ENABLED", False))
        self.enterContext(patch.object(router, "_MEM_COOLDOWN", {}))
        self.enterContext(patch.object(router, "_RATE_LIMITED", set()))
        token = router._ROUTER_DB.set(self.db)
        self.addCleanup(router._ROUTER_DB.reset, token)
        self.errors = io.StringIO()
        self.enterContext(redirect_stderr(self.errors))
        # Any accidental unmocked network access is a test failure.
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("offline")))

    def call(self, model="model-a", provider="Gemini"):
        return router._openai_style_call("https://example.invalid", "fake-key", model,
                                         "private request text", provider)[0]

    def health(self, model="model-a", provider="Gemini"):
        return router.provider_state.health(provider, model, router._model_cap(provider, model), self.db)

    def test_breaker_opens_after_three_matching_failures_skips_then_closes(self):
        with patch("urllib.request.urlopen", side_effect=socket.timeout("private request text")) as network:
            for _ in range(3):
                self.assertIsNone(self.call())
            self.assertEqual(self.health()["breaker"], "open")
            self.assertIsNone(self.call())
            self.assertEqual(network.call_count, 3)
        with patch.object(router.provider_state.time, "time", return_value=router.time.time() + 301), \
                patch("urllib.request.urlopen", return_value=answer()) as network:
            self.assertEqual(self.health()["breaker"], "half_open")
            self.assertEqual(self.call(), "answer")
            self.assertEqual(network.call_count, 1)
            self.assertEqual(self.health()["breaker"], "closed")
            self.assertEqual(self.health()["consecutive_failures"], 0)

    def test_different_failure_kind_and_success_reset_streak(self):
        responses = [socket.timeout(), socket.timeout(), http_error(503), socket.timeout(), answer(), socket.timeout()]
        with patch("urllib.request.urlopen", side_effect=responses):
            for _ in range(6):
                self.call()
        self.assertEqual(self.health()["breaker"], "closed")
        self.assertEqual(self.health()["consecutive_failures"], 1)
        self.assertEqual(self.health()["failure_reasons"]["timeout"]["count"], 4)

    def test_failed_half_open_probe_reopens_and_only_one_probe_is_reserved(self):
        with patch.object(router.provider_state.time, "time", return_value=1000), \
                patch("urllib.request.urlopen", side_effect=socket.timeout()):
            for _ in range(3):
                self.call()
        with patch.object(router.provider_state.time, "time", return_value=1301):
            self.assertTrue(router.provider_state.reserve("Gemini", "model-a", 100, self.db))
            self.assertFalse(router.provider_state.reserve("Gemini", "model-a", 100, self.db))
            router.provider_state.observe("Gemini", "model-a", False, "network", path=self.db)
            self.assertEqual(self.health()["breaker"], "open")

    def test_breaker_and_caps_survive_a_fresh_database_connection(self):
        with patch("urllib.request.urlopen", side_effect=socket.timeout()):
            for _ in range(3):
                self.call()
        with sqlite3.connect(self.db) as conn:
            raw = conn.execute("SELECT data FROM llm_model_health").fetchone()[0]
            self.assertNotIn("private request text", raw)
        self.assertFalse(router.provider_state.reserve("Gemini", "model-a", 100, self.db))
        self.assertEqual(self.health()["calls_today"], 3)

    def test_model_breaker_does_not_block_other_model_or_provider(self):
        with patch("urllib.request.urlopen", side_effect=socket.timeout()):
            for _ in range(3):
                self.call()
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: answer()) as network:
            self.assertIsNone(self.call())
            self.assertEqual(self.call("model-b"), "answer")
            self.assertEqual(self.call(provider="Groq"), "answer")
        self.assertEqual(network.call_count, 2)

    def test_zero_and_per_model_caps_skip_before_network_and_global_budget(self):
        os.environ["LLM_MODEL_DAILY_CAPS"] = json.dumps({"Gemini": {"model-a": 1, "model-b": 0}})
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: answer()) as network:
            self.assertEqual(self.call(), "answer")
            self.assertIsNone(self.call())
            self.assertIsNone(self.call("model-b"))
            self.assertEqual(self.call("model-c"), "answer")
        self.assertEqual(network.call_count, 2)
        self.assertEqual(self.record.call_count, 2)
        self.assertTrue(self.health()["capped"])

    def test_failed_attempts_consume_cap_and_backup_key_cannot_bypass_it(self):
        os.environ.update(GEMINI_API_KEY="first-credential", GEMINI_API_KEY_2="second-credential",
                          GEMINI_MODEL="model-a", LLM_MODEL_DAILY_CAPS='{"Gemini":{"model-a":1}}')
        with patch("urllib.request.urlopen", side_effect=socket.timeout()) as network:
            self.assertIsNone(router._gemini("private request text"))
        self.assertEqual(network.call_count, 1)
        self.assertEqual(self.health()["calls_today"], 1)

    def test_groq_retry_cannot_exceed_model_cap(self):
        os.environ.update(GROQ_API_KEY="fake-groq-credential", GROQ_MODEL_SMALL="model-a",
                          LLM_MODEL_DAILY_CAPS='{"Groq":{"model-a":1}}')
        with patch("urllib.request.urlopen", side_effect=http_error(429)), patch("time.sleep"):
            self.assertIsNone(router._groq("private request text"))
        self.assertEqual(self.record.call_count, 1)
        self.assertTrue(self.health(provider="Groq")["capped"])

    def test_caps_reset_on_utc_day_boundary(self):
        os.environ["LLM_MODEL_DAILY_CAPS"] = '{"Gemini":{"model-a":1}}'
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: answer()) as network:
            with patch.object(router.provider_state.time, "time", return_value=1700000000):
                self.assertEqual(self.call(), "answer")
                self.assertIsNone(self.call())
            with patch.object(router.provider_state.time, "time", return_value=1700086400):
                self.assertEqual(self.call(), "answer")
        self.assertEqual(network.call_count, 2)

    def test_concurrent_workers_cannot_exceed_last_model_reservations(self):
        def reserve(_):
            return router.provider_state.reserve("Gemini", "model-a", 3, self.db)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(reserve, range(16))).count(True), 3)

    def test_invalid_cap_configuration_fails_closed(self):
        for raw in ("broken", "[]", '{"Gemini":[]}', '{"Gemini":{"model-a":true}}'):
            with self.subTest(raw=raw):
                os.environ["LLM_MODEL_DAILY_CAPS"] = raw
                with patch("urllib.request.urlopen") as network:
                    self.assertIsNone(self.call())
                network.assert_not_called()

    def test_broken_state_database_fails_closed(self):
        token = router._ROUTER_DB.set(os.path.join(self.db, "missing", "state.db"))
        try:
            with patch("urllib.request.urlopen") as network:
                self.assertIsNone(self.call())
            network.assert_not_called()
        finally:
            router._ROUTER_DB.reset(token)

    def test_implicit_global_budget_path_cannot_make_test_calls_write_real_state(self):
        implicit = os.path.join(os.path.dirname(self.db), "implicit.db")
        budget = contextvars.ContextVar("fake_budget", default=implicit)
        token = router._ROUTER_DB.set(None)
        try:
            with patch.object(router.guardrails, "DB_PATH", implicit, create=True), \
                    patch.object(router.guardrails, "_BUDGET_DB", budget, create=True), \
                    patch("urllib.request.urlopen", return_value=answer()):
                self.assertIs(router._state_path(), router._MEMORY_STATE)
                self.assertEqual(self.call("test-isolation-model"), "answer")
            self.assertFalse(os.path.exists(implicit))
        finally:
            router._ROUTER_DB.reset(token)

    def test_global_budget_rejection_is_not_provider_failure(self):
        self.record.side_effect = RuntimeError("private global budget detail")
        with patch("urllib.request.urlopen") as network:
            self.assertIsNone(self.call())
        network.assert_not_called()
        self.assertEqual(self.health()["failure_reasons"], {})
        self.assertNotIn("private global budget detail", self.errors.getvalue())

    def enable_cf(self):
        os.environ.update(CF_API_TOKEN="fake-cloudflare-credential", CF_ACCOUNT_ID="fake-account-id")

    def test_cloudflare_uses_current_native_model_and_long_timeout(self):
        self.enable_cf()
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: cf_answer()) as network:
            self.assertEqual(router.chat("private request text", size="large", db_path=self.db), "answer")
        request = network.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/ai/run/" + router._CF_MODEL))
        self.assertEqual(network.call_args.kwargs["timeout"], router._TIMEOUT_LARGE)
        body = json.loads(request.data)
        self.assertNotIn("model", body)
        self.assertEqual(body["max_tokens"], 4096)
        self.assertIn(("Cloudflare", router._CF_MODEL), [(p, m) for p, m, *_ in router._targets()])

    def test_cloudflare_oversized_unicode_prompt_skips_without_network(self):
        self.enable_cf()
        with patch("urllib.request.urlopen") as network:
            self.assertIsNone(router._cloudflare("界" * 11000))
        network.assert_not_called()
        self.record.assert_not_called()

    def test_cloudflare_http_numeric_error_codes_are_classified_safely(self):
        self.enable_cf()
        cases = [(400, 5007, "invalid_model"), (404, 3042, "invalid_model"),
                 (413, 3006, "request_too_large"), (408, 3007, "timeout"),
                 (429, 3040, "capacity"), (403, 5035, "authentication"), (429, 3036, "quota")]
        for status, code, kind in cases:
            with self.subTest(code=code), patch("urllib.request.urlopen", side_effect=http_error(status, code)):
                self.assertIsNone(router._cloudflare("private request text"))
                self.assertEqual(self.health(router._CF_MODEL, "Cloudflare")["failure_kind"], kind)
        self.assertNotIn("private request text", self.errors.getvalue())
        self.assertIn("Cloudflare", router._RATE_LIMITED)

    def test_cloudflare_error_envelope_is_failure_even_on_http_200(self):
        self.enable_cf()
        raw = {"success": False, "result": {"response": "must not accept"},
               "errors": [{"code": 3036, "message": "private request text"}]}
        with patch("urllib.request.urlopen", return_value=Response(json.dumps(raw).encode())):
            self.assertIsNone(router._cloudflare("private request text"))
        self.assertEqual(self.health(router._CF_MODEL, "Cloudflare")["failure_kind"], "quota")

    def test_cloudflare_invalid_model_falls_back_to_configured_next_model(self):
        self.enable_cf()
        os.environ["CF_MODEL"] = "@cf/meta/old-model," + router._CF_MODEL
        with patch("urllib.request.urlopen", side_effect=[http_error(400, 5007), cf_answer()]) as network:
            self.assertEqual(router._cloudflare("private request text"), "answer")
        self.assertEqual(network.call_count, 2)

    def test_cloudflare_429_activates_provider_cooldown_and_fallback(self):
        self.enable_cf()
        os.environ.update(OPENROUTER_API_KEY="fake-openrouter-credential", OPENROUTER_MODEL="model-b:free")
        with patch("urllib.request.urlopen", side_effect=[http_error(429, 3036), answer(), answer()]) as network:
            self.assertEqual(router.chat("private request text", db_path=self.db), "answer")
            self.assertTrue(router.in_cooldown("Cloudflare", self.db))
            self.assertEqual(router.chat("private request text", db_path=self.db), "answer")
        self.assertEqual(network.call_count, 3)
        self.assertIn("openrouter", network.call_args.args[0].full_url)

    def test_response_parse_empty_timeout_network_and_auth_have_fixed_reasons(self):
        cases = [(Response(b"not JSON"), "response_parse"), (answer({"unexpected": "object"}), "response_parse"),
                 (answer(""), "empty_response"), (socket.timeout("private"), "timeout"),
                 (urllib.error.URLError("private"), "network"), (http_error(401), "authentication")]
        for index, (result, kind) in enumerate(cases):
            effects = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            model = "model-" + str(index)
            with self.subTest(kind=kind), patch("urllib.request.urlopen", **effects):
                self.assertIsNone(self.call(model))
                health = self.health(model)
                self.assertEqual(health["failure_kind"], kind)
                self.assertEqual(health["reason"], router.provider_state.REASONS[kind])

    def test_recent_ranking_is_task_specific_stable_and_expires(self):
        order = [(name, fn.__name__) for name, fn in router._PROVIDERS]
        self.assertEqual(router.provider_state.order(order, "draft", self.db), order)
        for provider, task, ok in [("Groq", "draft", False), ("Gemini", "draft", True),
                                   ("Groq", "review", True), ("Gemini", "review", False)]:
            router.provider_state.observe(provider, "model-a", ok, "timeout", task, self.db)
        self.assertEqual(router.provider_state.order(order, "draft", self.db)[0][0], "Gemini")
        self.assertEqual(router.provider_state.order(order, "review", self.db)[0][0], "Groq")
        with patch.object(router.provider_state.time, "time", return_value=router.time.time() + 86401):
            self.assertEqual(router.provider_state.order(order, "draft", self.db), order)

    def test_chat_uses_learned_task_order_then_falls_back(self):
        os.environ.update(GROQ_API_KEY="fake-groq-credential", GROQ_MODEL_SMALL="model-a",
                          GEMINI_API_KEY="fake-gemini-credential", GEMINI_MODEL="model-b")
        router.provider_state.observe("Gemini", "model-b", True, task="draft", path=self.db)
        with patch("urllib.request.urlopen", side_effect=[socket.timeout(), answer()]) as network:
            self.assertEqual(router.chat("private request text", db_path=self.db, task_kind="draft"), "answer")
        urls = [item.args[0].full_url for item in network.call_args_list]
        self.assertIn("googleapis", urls[0])
        self.assertIn("groq", urls[1])

    def test_ranking_uses_bounded_recent_window(self):
        for _ in range(25):
            router.provider_state.observe("Groq", "model-a", False, "timeout", "review", self.db)
        for _ in range(20):
            router.provider_state.observe("Groq", "model-a", True, task="review", path=self.db)
        order = [("Groq", "g"), ("Gemini", "m")]
        self.assertEqual(router.provider_state.order(order, "review", self.db)[0][0], "Groq")
        state = router.provider_state.snapshots(self.db)[0][2]
        self.assertEqual(len(state["recent"]["review"]), 20)

    def test_no_prompt_secret_or_arbitrary_error_code_in_logs_or_health(self):
        private_value = "sensitive-placeholder-credential"
        os.environ.update(GEMINI_API_KEY=private_value, GEMINI_MODEL="model-a")
        raw = json.dumps({"error": {"code": "arbitrary_private_string", "message": private_value + " private request text"}}).encode()
        err = urllib.error.HTTPError("https://example.invalid/" + private_value, 400, private_value, {}, io.BytesIO(raw))
        with patch("urllib.request.urlopen", side_effect=err):
            router.chat("private request text", db_path=self.db, task_kind="private request text")
        public = json.dumps(router.provider_status(self.db)) + self.errors.getvalue()
        stored = json.dumps(router.provider_state.snapshots(self.db))
        for forbidden in (private_value, "private request text", "arbitrary_private_string"):
            self.assertNotIn(forbidden, public)
            self.assertNotIn(forbidden, stored)
        os.environ["GEMINI_MODEL"] = private_value
        with patch("urllib.request.urlopen") as network:
            self.assertIsNone(router._gemini("private request text"))
        network.assert_not_called()
        self.assertNotIn(private_value, json.dumps(router.provider_status(self.db)))

    def test_autofix_key_is_never_a_router_credential_including_media(self):
        os.environ["GEMINI_AUTOFIX_KEY"] = "autofix-placeholder-credential"
        with patch("urllib.request.urlopen") as network:
            self.assertIsNone(router.chat("private request text", db_path=self.db))
            self.assertIsNone(router.chat_audio(b"audio", "audio/wav", db_path=self.db))
            self.assertIsNone(router.chat_vision(b"image", "image/png", db_path=self.db))
        network.assert_not_called()

    def test_media_native_fallback_obeys_same_model_cap(self):
        os.environ.update(GEMINI_API_KEY="fake-gemini-credential", GEMINI_MODEL="model-a",
                          LLM_MODEL_DAILY_CAPS='{"Gemini":{"model-a":1}}')
        with patch("urllib.request.urlopen", side_effect=http_error(503)) as network:
            self.assertIsNone(router.chat_vision(b"image", "image/png", db_path=self.db))
            self.assertIsNone(router.chat_audio(b"audio", "audio/wav", db_path=self.db))
        self.assertEqual(network.call_count, 1)

    def test_real_health_probes_share_runtime_model_cap_and_native_payload(self):
        self.enable_cf()
        os.environ["LLM_MODEL_DAILY_CAPS"] = json.dumps({"Cloudflare": {router._CF_MODEL: 1}})
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: cf_answer()) as network:
            results = router.health(db_path=self.db)
            self.assertIsNone(router._cloudflare("private request text"))
        self.assertEqual(network.call_count, 1)
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        self.assertIn("cap", results[1]["reason"])
        self.assertNotIn("model", json.loads(network.call_args.args[0].data))
        self.assertEqual(self.record.call_count, 1)

    def test_public_health_fixed_reasons_and_staleness_without_read_side_effects(self):
        missing = os.path.join(os.path.dirname(self.db), "not-created.db")
        self.assertTrue(all(row["state"] == "unknown" for row in router.get_provider_health(missing)))
        self.assertFalse(os.path.exists(missing))
        with patch("urllib.request.urlopen", side_effect=socket.timeout("private request text")):
            self.call()
        rows = {row["provider"]: row for row in router.get_provider_health(self.db)}
        self.assertEqual(rows["Gemini"]["state"], "down")
        self.assertEqual(rows["Gemini"]["reason"], "Provider request timed out")
        self.assertEqual(rows["Gemini"]["failure_reasons"]["timeout"]["count"], 1)
        with patch("urllib.request.urlopen", return_value=answer()):
            self.call()
        self.assertEqual(next(row for row in router.get_provider_health(self.db)
                              if row["provider"] == "Gemini")["state"], "up")
        with patch.object(router.provider_state.time, "time", return_value=router.time.time() + 86401):
            row = next(row for row in router.get_provider_health(self.db) if row["provider"] == "Gemini")
            self.assertEqual(row["state"], "unknown")
        self.assertNotIn("private request text", json.dumps(rows))

    def test_model_cap_skip_is_not_counted_as_a_provider_outage(self):
        os.environ.update(GEMINI_API_KEY="fake-gemini-credential", GEMINI_MODEL="model-a",
                          LLM_MODEL_DAILY_CAPS='{"Gemini":{"model-a":0}}')
        with patch.object(router, "log_provider") as log, patch("urllib.request.urlopen") as network:
            self.assertIsNone(router.chat("private request text", db_path=self.db))
        log.assert_not_called()
        network.assert_not_called()

    def test_cloudflare_capacity_tries_other_model_but_quota_stops_model_chain(self):
        self.enable_cf()
        os.environ["CF_MODEL"] = "@cf/meta/model-a,@cf/meta/model-b"
        with patch("urllib.request.urlopen", side_effect=[http_error(429, 3040), cf_answer()]) as network:
            self.assertEqual(router._cloudflare("private request text"), "answer")
        self.assertEqual(network.call_count, 2)
        self.assertNotIn("Cloudflare", router._RATE_LIMITED)
        with patch("urllib.request.urlopen", side_effect=http_error(429, 3036)) as network:
            self.assertIsNone(router._cloudflare("private request text"))
        self.assertEqual(network.call_count, 1)

    def test_cloudflare_daily_quota_waits_until_utc_reset_across_restart(self):
        self.enable_cf()
        noon = router.datetime.datetime(2026, 10, 31, 12)
        with patch.object(router, "_now", return_value=noon), \
                patch("urllib.request.urlopen", side_effect=http_error(429, 3036)) as network:
            self.assertIsNone(router.chat("private request text", db_path=self.db))
        router._MEM_COOLDOWN.clear()
        with patch.object(router, "_now", return_value=noon + router.datetime.timedelta(hours=11)), \
                patch("urllib.request.urlopen") as network:
            self.assertIsNone(router._cloudflare("private request text"))
            network.assert_not_called()
        with patch.object(router, "_now", return_value=router.datetime.datetime(2026, 11, 1)), \
                patch("urllib.request.urlopen", return_value=cf_answer()) as network:
            self.assertEqual(router._cloudflare("private request text"), "answer")
            self.assertEqual(network.call_count, 1)


if __name__ == "__main__":
    unittest.main()
