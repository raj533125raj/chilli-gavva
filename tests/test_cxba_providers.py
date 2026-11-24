"""Safe, actionable owner diagnostics from real HTTP failure classification."""
import io
import json
import os
import socket
import urllib.error
from unittest.mock import patch

import daily_report
import llm_router as router
from tests.router_test_support import RouterTestCase


class ProviderReasons(RouterTestCase):
    def test_every_failure_has_safe_tenglish_reason_and_fix(self):
        token = router._ROUTER_DB.set(self.db)
        self.addCleanup(router._ROUTER_DB.reset, token)
        cases = [(401, 'key refused'), (403, 'key refused'), (404, 'model peru'),
                 (429, 'rate limit'), (socket.timeout(), 'timeout'), (None, 'bad answer')]
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'private-credential'}, clear=True), \
                patch.object(router.guardrails, 'record_call'):
            for index, (failure, expected) in enumerate(cases):
                error = urllib.error.HTTPError('https://example.invalid', failure, 'private', {},
                                               io.BytesIO(b'{}')) if isinstance(failure, int) else failure
                class Response(io.BytesIO):
                    def __enter__(self): return self
                    def __exit__(self, *args): return False
                effect = {'side_effect': error} if error else {'return_value': Response(b'{}')}
                router.set_cooldown('Gemini', minutes=-1, db_path=self.db)
                with patch('urllib.request.urlopen', **effect):
                    router._openai_style_call('https://example.invalid', 'private-credential',
                                             'model-' + str(index), 'private prompt', 'Gemini')
                lines = router.provider_reason_lines(self.db)
                line = next(s for s in lines if s.startswith('Gemini:'))
                self.assertIn(expected, line)
                self.assertNotIn('private', json.dumps(lines))
            self.assertEqual(len(lines), 6)
            self.assertIn('Cloudflare: key ledu', '\n'.join(lines))

    def test_daily_report_has_counters_and_each_provider_reason(self):
        router.log_provider('Gemini', False, self.db)
        router.provider_state.observe('Gemini', 'model', False, 'invalid_model', path=self.db)
        import sqlite3
        with sqlite3.connect(self.db) as conn:
            text = daily_report._providers(conn, '2000-01-01')
        self.assertIn('Gemini FAIL (0 ok, 1 failed)', text)
        self.assertIn('Gemini: model peru', text)
        self.assertIn('GitHubModels: OFF', text)

class ProviderHealing(RouterTestCase):
    def setUp(self):
        super().setUp()
        token = router._ROUTER_DB.set(self.db)
        self.addCleanup(router._ROUTER_DB.reset, token)
        self.enterContext(patch.object(router.guardrails, 'record_call'))
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def response(self):
        from tests.test_llm_router_resilience import answer
        return answer('real fallback answer')

    def test_retired_model_fallback_is_remembered_for_every_model_list(self):
        cases = [('Gemini', router._gemini, {'GEMINI_API_KEY': 'fake', 'GEMINI_MODEL': 'old,new'}),
                 ('Groq', router._groq, {'GROQ_API_KEY': 'fake', 'GROQ_MODEL_SMALL': 'old,new'}),
                 ('OpenRouter', router._openrouter, {'OPENROUTER_API_KEY': 'fake', 'OPENROUTER_MODEL': 'old:free,new:free'})]
        for name, call, env in cases:
            with self.subTest(name=name), patch.dict(os.environ, env, clear=True):
                error = urllib.error.HTTPError('https://example.invalid', 404, '', {}, io.BytesIO(b'{}'))
                with patch('urllib.request.urlopen', side_effect=[error, self.response(), self.response()]) as network:
                    self.assertEqual(call('draft'), 'real fallback answer')
                    # Open a new connection on every state operation, as after restart.
                    self.assertEqual(call('draft'), 'real fallback answer')
                models = [json.loads(c.args[0].data)['model'] for c in network.call_args_list]
                self.assertEqual(models, ['old:free', 'new:free', 'new:free'] if name == 'OpenRouter'
                                 else ['old', 'new', 'new'])

    def test_rate_limit_rests_until_real_reset_across_restart(self):
        import datetime as dt
        now = dt.datetime(2026, 11, 24, 10)
        for header in ({'retry-after': '7200'}, {'Retry-After': 'Tue, 24 Nov 2026 12:00:00 GMT'},
                       {'x-ratelimit-reset': str(now.replace(tzinfo=dt.timezone.utc).timestamp() + 7200)}):
            with self.subTest(header=header), patch.dict(os.environ, {'GROQ_API_KEY': 'fake'}, clear=True):
                error = urllib.error.HTTPError('https://example.invalid', 429, '', header, io.BytesIO(b'{}'))
                with patch.object(router, '_now', return_value=now), patch('time.sleep') as sleep, \
                        patch('urllib.request.urlopen', side_effect=error) as network:
                    router.set_cooldown('Groq', minutes=-1, db_path=self.db)
                    self.assertIsNone(router._groq('draft'))
                    self.assertEqual(network.call_count, 1)
                    sleep.assert_not_called()
                router._MEM_COOLDOWN.clear()
                with patch.object(router, '_now', return_value=now + dt.timedelta(seconds=7199)), \
                        patch('urllib.request.urlopen') as network:
                    self.assertIsNone(router._groq('draft'))
                    network.assert_not_called()
                with patch.object(router, '_now', return_value=now + dt.timedelta(hours=2)), \
                        patch('urllib.request.urlopen', return_value=self.response()):
                    self.assertEqual(router._groq('draft'), 'real fallback answer')
