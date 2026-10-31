"""Office wizard/engine-picker envelopes, durable controls and public leak protection."""
import contextlib
import io
import json
import os
import unittest
from unittest import mock

import agent_engines
import chat_brain
import cubes
import export_status
import guardrails
import llm_router
import office_agents
import office_tasks
import public_export
import workforce
from test_office_controls import AGENT, ENV, NOW, OfficeBase, signed


def spec():
    return {"name": "Ideas Helper", "role": "Research assistant", "engine": "cloud", "voice_enabled": False,
            "workspace": {"cube_id": "research", "room_id": "Niche Mining"},
            "briefing": {"who": "Private helper instructions", "does": "Review ideas", "never": "Publish work"}}


class AgentControlsTests(OfficeBase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.dict(os.environ, {}, clear=True))
        self.enterContext(mock.patch.object(guardrails, 'CONFIG_PATH', str(self.root / 'no-config.json')))

    def submit(self, payload, ident='hire', nonce=None, target=AGENT, thread=None):
        # Exact output of office ui/agent-wizard.mjs buildAgentControl().
        text = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        self.run_request(signed(action='send_message', target=target,
                                value={'thread_id': thread or target, 'text': text}, ident=ident, nonce=nonce))
        return self.kv.receipt(ident)

    def hire(self, value=None, **kwargs):
        return self.submit({'kind': 'add_agent', 'spec': value or spec()}, **kwargs)

    def change(self, agent_id=AGENT, provider='Groq', model='openai/gpt-oss-20b', **kwargs):
        return self.submit({'kind': 'engine_change', 'agent_id': agent_id, 'provider': provider, 'model': model},
                           ident=kwargs.pop('ident', 'engine'), **kwargs)

    def test_valid_add_persists_paused_and_owner_switch_allows_existing_workforce_dispatch(self):
        with mock.patch.object(chat_brain, 'office_reply') as reply:
            receipt = self.hire()
        reply.assert_not_called()
        self.assertEqual((receipt['state'], receipt['reason']), ('applied', 'agent added paused'))
        ident = office_agents.agent_id(spec()['name'])
        agent = office_tasks.agent(ident, self.db)
        self.assertEqual(agent['briefing'], spec()['briefing'])
        self.assertFalse(cubes.is_agent_enabled(agent['name'], self.db))
        state = next(a for a in workforce.agent_states(self.db) if a['id'] == ident)
        self.assertEqual(state['state'], 'paused')
        cube = next(c for c in cubes.get_public_cubes_export(self.db) if c['id'] == 'research')
        self.assertIn(agent['name'], [a['name'] for a in cube['agents']])
        task_id = workforce.enqueue('office_task', {'agent_id': ident, 'text': 'Offer an idea'}, db_path=self.db)
        self.assertIsNone(workforce.claim_next(self.db))
        with mock.patch.object(llm_router, 'chat') as llm:
            self.assertEqual(chat_brain.office_reply(ident, 'Offer an idea', self.db)[0], 'held')
        llm.assert_not_called()
        # Turning the cube on alone cannot unpause a newly hired agent.
        cubes.turn_on('research', self.db)
        self.assertIsNone(workforce.claim_next(self.db))
        self.run_request(signed(action='set_switch', target=ident, value=True, ident='enable'))
        self.assertEqual(self.kv.receipt('enable')['state'], 'applied')
        cubes.turn_off('research', self.db)
        self.assertIsNone(workforce.claim_next(self.db))
        cubes.turn_on('research', self.db)
        task = workforce.claim_next(self.db)
        self.assertEqual((task['id'], task['agent']), (task_id, agent['name']))
        prompts = []
        result = office_tasks.execute(task, {'db_path': self.db, 'llm': lambda p: prompts.append(p) or 'Useful advice'})
        self.assertEqual(result['answer'], 'Useful advice')
        self.assertIn(spec()['briefing']['who'], prompts[0])
        self.assertEqual(result['next'], [])

    def test_duplicate_case_builtin_and_slug_collisions_are_rejected(self):
        self.assertEqual(self.hire()['state'], 'applied')
        for i, name in enumerate(('Ideas Helper', 'ideas helper', 'Ideas-Helper', 'CEO', 'Research Worker', 'Marketing Manager')):
            value = spec(); value['name'] = name
            self.assertEqual(self.hire(value, ident=f'dup:{i}')['state'], 'rejected')
        self.assertEqual(self.rows('SELECT COUNT(*) FROM workforce_agents')[0][0], 1)

    def test_strict_manifest_fields_types_lengths_names_workspace_and_engine(self):
        mutations = [
            ('name', ''), ('name', ' bad'), ('name', '../agent'), ('name', 'x' * 61),
            ('role', 'x' * 121), ('engine', 'paid'), ('engine', {}), ('voice_enabled', 1),
            ('workspace', {'cube_id': 'missing', 'room_id': 'Niche Mining'}),
            ('workspace', {'cube_id': 'research', 'room_id': 'missing'}),
            ('workspace', {'cube_id': 'marketing', 'room_id': 'Social Media'}),
            ('workspace', {'cube_id': 'research', 'room_id': 'Niche Mining', 'command': 'run'}),
            ('briefing', {'who': 'x', 'does': 'x', 'never': ''}),
            ('briefing', {'who': 'x' * 121, 'does': 'x', 'never': 'x'}),
            ('briefing', {'who': 'x', 'does': 'x', 'never': 'x', 'exec': 'run'}),
            ('extra', 'field'),
        ]
        for i, (key, value) in enumerate(mutations):
            with self.subTest(key=key, value=value):
                draft = spec(); draft[key] = value
                self.assertEqual(self.hire(draft, ident=f'bad:{i}')['state'], 'rejected')
        self.assertEqual(self.rows('SELECT COUNT(*) FROM workforce_agents')[0][0], 0)

    def test_credential_like_values_and_exact_environment_values_never_persist_or_echo(self):
        samples = ['sk-' + 'A1' * 15, 'ghp_' + 'a' * 32, 'xoxb-' + '123a-' * 8,
                   'password=huntervalue', 'Bearer privatevalue', 'gsk_' + 'b' * 25,
                   'https://person:pass@private.invalid', 'AKIA' + 'A' * 16, 'a' * 40,
                   'AZ12' * 12, 'configured-private-value']
        with mock.patch.dict(os.environ, {'CONTROL_SIGNING_KEY': samples[-1]}):
            for i, sample in enumerate(samples):
                draft = spec(); draft['briefing']['who'] = sample
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    receipt = self.hire(draft, ident=f'cred:{i}')
                self.assertEqual(receipt['state'], 'rejected')
                self.assertNotIn(sample, json.dumps(receipt) + output.getvalue())
        self.assertEqual(self.rows('SELECT COUNT(*) FROM workforce_agents')[0][0], 0)

    def test_engine_change_exact_fields_applies_to_existing_and_new_agents_without_enabling(self):
        self.assertEqual(self.change()['state'], 'applied')
        self.assertEqual(office_tasks.agent(AGENT, self.db)['model'], 'openai/gpt-oss-20b')
        self.hire()
        ident = office_agents.agent_id(spec()['name'])
        self.assertEqual(self.change(ident, ident='hire-engine')['state'], 'applied')
        self.assertFalse(cubes.is_agent_enabled(spec()['name'], self.db))
        self.assertEqual(self.change('unknown-agent', ident='unknown')['state'], 'rejected')
        for i, (provider, model) in enumerate((('fake', 'fake'), ('Groq', 'invented'),
                ('GitHubModels', 'openai/gpt-4o-mini'), ('Groq', 'sk-' + 'a' * 30))):
            self.assertEqual(self.change(provider=provider, model=model, ident=f'bad-engine:{i}')['state'], 'rejected')
        self.assertEqual(office_tasks.agent(AGENT, self.db)['model'], 'openai/gpt-oss-20b')

    def test_replays_bad_signatures_threads_and_malformed_structured_messages_do_not_mutate(self):
        self.hire(nonce='hire-once')
        self.assertEqual(self.run_request()['applied'], 0)
        other = spec(); other['name'] = 'Other Helper'
        replay = self.hire(other, ident='replay', nonce='hire-once')
        self.assertEqual((replay['state'], replay['reason']), ('rejected', 'replayed nonce or request id'))
        self.assertEqual(self.hire(other, ident='thread', thread='ceo')['state'], 'rejected')
        self.assertEqual(self.hire(other, ident='recipient', target='unknown')['state'], 'rejected')
        for i, text in enumerate(('{"kind":"add_agent",', '{"kind":"engine_change","kind":"add_agent"}',
                                  '{"kind":"shell","command":"run"}', 'x' * 501)):
            self.run_request(signed(action='send_message', target=AGENT, ident=f'json:{i}',
                                    value={'thread_id': AGENT, 'text': text}))
            self.assertEqual(self.kv.receipt(f'json:{i}')['state'], 'rejected')
        bad = signed(action='send_message', target=AGENT, ident='unsigned',
                     value={'thread_id': AGENT, 'text': json.dumps({'kind': 'add_agent', 'spec': other})})
        bad['signature'] = '0' * 64
        self.run_request(bad)
        self.assertEqual(self.kv.receipt('unsigned')['state'], 'rejected')
        self.assertEqual(self.rows('SELECT COUNT(*) FROM workforce_agents')[0][0], 1)

    def test_connection_and_status_export_only_names_and_selected_route_never_briefing_or_values(self):
        self.hire(); self.change()
        path = self.root / 'web' / 'status.json'
        secrets = {name: f'private-value-{i}' for i, name in enumerate(ENV)}
        with mock.patch.dict(os.environ, secrets), mock.patch.object(public_export, 'get_public_changelog', return_value=[]):
            export_status.write_status(str(path), self.db)
            documents = [json.loads(path.read_text()), public_export.get_full_public_export(self.db)]
        for document in documents:
            connection = document['agent_connection']
            self.assertEqual(set(connection['required_keys']), set(ENV))
            self.assertEqual(connection['keys'], connection['required_keys'])  # current connect-sheet consumer
            self.assertEqual(connection['engines'], ['local', 'cloud'])
            self.assertEqual(connection['inbox'], 'control:request:*')
            self.assertEqual(connection['outbox'], 'runtime:receipt:<id>')
            blob = json.dumps(document)
            for private in [*secrets.values(), *spec()['briefing'].values()]:
                self.assertNotIn(private, blob)
            self.assertNotIn('briefing', blob)
            worker = next(a for a in document['workers'] if a['name'] == spec()['name'])
            self.assertEqual(worker['state'], 'paused')
        agent = next(a for a in documents[0]['agents'] if a['name'] == spec()['name'])
        self.assertEqual(agent['state'], 'paused')
        changed = next(a for a in documents[0]['agents'] if a.get('id') == AGENT)
        self.assertEqual(changed['model'], 'openai/gpt-oss-20b')

    def test_engine_choice_is_used_by_direct_chat_and_workforce(self):
        self.change()
        with mock.patch.object(agent_engines, 'chat', return_value='Advisory answer') as dispatch:
            result = chat_brain.office_reply(AGENT, 'Suggest something original', self.db)
        self.assertEqual(result[0], 'applied')
        self.assertEqual(dispatch.call_args.kwargs['provider'], 'Groq')
        self.assertEqual(dispatch.call_args.kwargs['model'], 'openai/gpt-oss-20b')
        with mock.patch.object(agent_engines, 'chat', return_value='Task answer') as dispatch:
            self.assertEqual(workforce._ask({'llm': lambda p: self.fail('default route'), 'agent': 'Research Worker',
                'db_path': self.db, 'custom_llm': False}, 'A task prompt', size='large'), 'Task answer')
        self.assertEqual(dispatch.call_args.kwargs['size'], 'large')

    def test_local_hire_and_engine_change_update_mode_without_starting_work(self):
        draft = spec(); draft['engine'] = 'local'; draft['voice_enabled'] = True
        self.assertEqual(self.hire(draft)['state'], 'applied')
        ident = office_agents.agent_id(draft['name'])
        self.assertEqual(office_tasks.agent(ident, self.db)['engine'], 'local')
        self.assertEqual(self.change(ident)['state'], 'applied')
        self.assertEqual(office_tasks.agent(ident, self.db)['engine'], 'cloud')
        self.assertFalse(cubes.is_agent_enabled(draft['name'], self.db))


class EngineRoutingTests(OfficeBase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.dict(os.environ, {'GROQ_API_KEY': 'fixture-key',
                                                      'FREELLMAPI_KEY': 'freellmapi-fixture'}, clear=True))
        self.enterContext(mock.patch.object(guardrails, 'CONFIG_PATH', str(self.root / 'no-config.json')))
        self.enterContext(mock.patch.object(guardrails, '_TESTING', False))

    def test_selected_model_reaches_existing_router_transport_and_budget(self):
        def transport(request, **kwargs):
            self.assertEqual(json.loads(request.data)['model'], 'openai/gpt-oss-120b')
            self.assertEqual(guardrails.calls_today(self.db), 1)
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{"choices":[{"message":{"content":"Answer"}}]}'
            return response
        with mock.patch('urllib.request.urlopen', side_effect=transport) as http:
            self.assertEqual(agent_engines.chat('A prompt', db_path=self.db, provider='Groq',
                                               model='openai/gpt-oss-120b'), 'Answer')
        self.assertEqual(http.call_count, 1)
        with mock.patch.object(guardrails, 'daily_cap', return_value=1), mock.patch('urllib.request.urlopen') as http:
            self.assertIsNone(agent_engines.chat('A prompt', db_path=self.db, provider='Groq', model='openai/gpt-oss-120b'))
        http.assert_not_called()

    def test_modes_and_selected_model_never_fall_back_across_engines(self):
        with mock.patch.object(llm_router, '_freellmapi', return_value=None) as local, \
             mock.patch.object(llm_router, '_openai_style_call', return_value=(None, 503)) as cloud:
            self.assertIsNone(agent_engines.chat('A prompt', db_path=self.db, mode='local'))
            local.assert_called_once(); cloud.assert_not_called()
            self.assertIsNone(agent_engines.chat('A prompt', db_path=self.db, provider='Groq', model='openai/gpt-oss-20b'))
            self.assertEqual(local.call_count, 1)
            self.assertEqual(cloud.call_count, 1)
        with mock.patch.object(llm_router, 'GUARD_ENABLED', True), \
             mock.patch.object(llm_router, '_high_guard_reasons', return_value=['injection']), \
             mock.patch('urllib.request.urlopen') as http:
            self.assertIn('blocked_by_guard', agent_engines.chat('A prompt', db_path=self.db))
        http.assert_not_called()

    def test_gemini_backup_key_supports_the_selected_model_when_primary_is_absent(self):
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY_2': 'fixture-backup'}, clear=True), \
             mock.patch.object(llm_router, '_openai_style_call', return_value=('Backup answer', 200)) as call:
            self.assertEqual(agent_engines.chat('A prompt', db_path=self.db, provider='Gemini',
                                               model='gemini-flash-latest'), 'Backup answer')
        self.assertEqual(call.call_args.args[1:3], ('fixture-backup', 'gemini-flash-latest'))

    def test_catalog_follows_router_and_omits_secret_looking_or_paid_model_config(self):
        with mock.patch.dict(os.environ, {'GROQ_MODEL_SMALL': 'sk-' + 'a' * 40,
                                        'OPENROUTER_MODEL': 'vendor/paid'}):
            catalog = agent_engines.choices()
        self.assertFalse(any(p == 'OpenRouter' or m.startswith('sk-') for p, m in catalog))
        with mock.patch.object(llm_router, '_PROVIDERS', [('FreeLLMAPI', llm_router._freellmapi)]):
            self.assertEqual(agent_engines.modes(), ['local'])
            self.assertEqual(agent_engines.choices(), [('FreeLLMAPI', 'auto')])


if __name__ == '__main__':
    unittest.main()
