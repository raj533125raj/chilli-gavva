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
