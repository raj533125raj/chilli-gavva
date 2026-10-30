"""Agent messages return scrubbed replies to signed KV receipts without Telegram or tools."""
import json
from unittest import mock

import chat_brain
import cubes
import guardrails
from test_office_controls import OfficeBase, signed, AGENT


class OfficeChatTests(OfficeBase):
    def submit(self,action='send_message',target=AGENT,value=None,ident='chat:one'):
        value={'thread_id':target,'text':'Suggest a useful product idea'} if value is None else value
        self.run_request(signed(action,target,value,ident))
        return self.kv.receipt(ident)

    def test_each_chat_alias_returns_agent_reply_in_receipt_without_telegram(self):
        for index,action in enumerate(('send_message','chat','ask')):
            with mock.patch('llm_router.chat',return_value='Try a printable checklist') as llm, \
                    mock.patch('telegram_inbox.send_text') as telegram:
                value='Suggest a useful product idea' if action!='send_message' else None
                receipt=self.submit(action,value=value,ident=f'chat:{index}')
            self.assertEqual(receipt['state'],'applied')
            self.assertEqual(receipt['reply'],'Try a printable checklist')
            self.assertIn('Research Worker',llm.call_args.args[0])
            telegram.assert_not_called()
        self.assertEqual(len(chat_brain.get_chat_memory('office:'+AGENT,db_path=self.db)),6)

    def test_local_queries_need_no_llm_and_pause_action_requires_explicit_signed_control(self):
        with mock.patch('llm_router.chat',side_effect=AssertionError('local query must not call AI')):
            receipt=self.submit(value={'thread_id':AGENT,'text':'status ela undhi'})
        self.assertEqual(receipt['state'],'applied'); self.assertIn('State:',receipt['reply'])
        receipt=self.submit(value={'thread_id':AGENT,'text':'pause the robot'},ident='action:one')
        self.assertEqual(receipt['state'],'held')
        self.assertIn('signed control',receipt['reason'])
        self.assertFalse(guardrails.is_paused(self.db))

    def test_mismatched_thread_unknown_agent_oversize_and_extra_commands_rejected(self):
        cases=[(AGENT,{'thread_id':'ceo','text':'hello'}),('unknown',{'thread_id':'unknown','text':'hello'}),
               (AGENT,{'thread_id':AGENT,'text':'x'*501}),
               (AGENT,{'thread_id':AGENT,'text':'hello','command':'whoami'})]
        for index,(target,value) in enumerate(cases):
            self.assertEqual(self.submit(target=target,value=value,ident=f'bad:{index}')['state'],'rejected')

    def test_cube_off_pause_and_empty_provider_answer_are_held(self):
        cubes.turn_off('research',self.db)
        with mock.patch('llm_router.chat') as llm:
            self.assertEqual(self.submit()['state'],'held'); llm.assert_not_called()
        cubes.turn_on('research',self.db); guardrails.set_paused(True,self.db)
        self.assertEqual(self.submit(ident='paused')['state'],'held')
        guardrails.set_paused(False,self.db)
        with mock.patch('llm_router.chat',return_value=None):
            self.assertEqual(self.submit(ident='empty')['state'],'held')

    def test_replies_and_chat_memory_are_scrubbed_and_replay_never_calls_agent_twice(self):
        private='AIza'+'X'*35
        value={'thread_id':AGENT,'text':'Think about this URL https://example.invalid/?key=fixture-query'}
        with mock.patch('llm_router.chat',return_value='Reply '+private+' ?key=fixture-query') as llm:
            receipt=self.submit(value=value)
            self.run_request()
        llm.assert_called_once()
        raw=json.dumps(receipt)+json.dumps(chat_brain.get_chat_memory('office:'+AGENT,db_path=self.db))
        self.assertNotIn(private,raw); self.assertNotIn('fixture-query',raw)
        self.assertNotIn('fixture-query',llm.call_args.args[0])

    def test_role_routes_require_the_current_real_agent_role(self):
        value={'thread_id':AGENT,'text':'Suggest a useful product idea','role':'Finds and lists ideas','cube_id':'chilli-gavva-robot'}
        with mock.patch('llm_router.chat',return_value='Idea'):
            self.assertEqual(self.submit('route_request','orchestrator',value)['state'],'applied')
        value['role']='Invented role'
        self.assertEqual(self.submit('route_request','orchestrator',value,ident='bad:route')['state'],'held')

    def test_budget_denial_prevents_freeform_agent_call(self):
        with mock.patch.object(guardrails,'can_call_llm',return_value=False), mock.patch('llm_router.chat') as llm:
            self.assertEqual(self.submit()['state'],'held')
            llm.assert_not_called()
