"""Cube controls and cross-type authentication/receipt failure checks with fake KV."""
import contextlib
import io
import json
from unittest import mock
import urllib.error

import control_inbox as ci
import cubes
import guardrails
import office_tasks
import workforce
from test_office_controls import OfficeBase, signed, NOW, AGENT


class CubeControlTests(OfficeBase):
    def toggle(self,action='set_cube',value=False,target='research',ident='cube:request'):
        self.run_request(signed(action,target,value,ident))
        return self.kv.receipt(ident)

    def test_each_on_off_form_persists_and_governs_workforce_claims(self):
        task=workforce.enqueue('research',{'topic':'Ideas'},db_path=self.db)
        self.assertEqual(self.toggle()['state'],'applied')
        self.assertEqual(cubes.get_cube_state('research',self.db),'off')
        self.assertIsNone(workforce.claim_next(self.db))
        self.assertEqual(self.toggle('cube_on',None,ident='on')['state'],'applied')
        self.assertEqual(workforce.claim_next(self.db)['id'],task)
        for index,(action,value) in enumerate((('cube_off',None),('set_switch',False),('set_cube','Off'),('set_cube',{'enabled':False}))):
            self.assertEqual(self.toggle(action,value,target='cube:research',ident=f'off:{index}')['state'],'applied')
            self.assertEqual(cubes.get_cube_state('research',self.db),'off')

    def test_cube_off_suspends_missions_and_does_not_clear_pause_on_enable(self):
        self.run_request(signed()); self.toggle()
        self.assertEqual(office_tasks.tick(self.db,NOW+3600)['queued'],0)
        guardrails.set_paused(True,self.db)
        self.toggle(value=True,ident='enable')
        self.assertTrue(guardrails.is_paused(self.db))
        self.assertEqual(office_tasks.tick(self.db,NOW+3600)['queued'],0)

    def test_unknown_planned_and_malformed_toggles_have_explicit_receipts(self):
        self.assertEqual(self.toggle(target='missing')['state'],'rejected')
        self.assertEqual(self.toggle(value=True,target='marketing',ident='planned')['state'],'held')
        for i,value in enumerate((1,'maybe',{'enabled':True,'command':'whoami'})):
            self.assertEqual(self.toggle(value=value,ident=f'bad:{i}')['state'],'rejected')
        self.assertEqual(self.toggle('cube_on',True,ident='bad:null')['state'],'rejected')

    def test_product_cube_off_blocks_automatic_builders(self):
        import product_builder
        self.toggle(target='product')
        with mock.patch.object(product_builder,'_todo',side_effect=AssertionError('disabled builder must not inspect work')):
            self.assertEqual(product_builder.build_pending(db_path=self.db),[])
        with mock.patch.object(product_builder,'build_pending') as build:
            product_builder.main(['--db',self.db])
        build.assert_not_called()


class AllTypeSafetyTests(OfficeBase):
    def test_every_dispatch_type_verifies_signature_before_handler_or_commands(self):
        with mock.patch.object(ci,'_apply',side_effect=AssertionError('unauthenticated handler reached')) as apply, \
                mock.patch('subprocess.run',side_effect=AssertionError('no shell')), \
                mock.patch('os.system',side_effect=AssertionError('no shell')):
            for index,action in enumerate(sorted(ci.ACTION_NAMES)):
                record=signed(action,ident=f'auth:{index}'); record['signature']='0'*64
                self.run_request(record)
                self.assertEqual(self.kv.receipt(f'auth:{index}')['state'],'rejected')
        apply.assert_not_called()

    def test_write_failure_after_success_retains_reply_and_retries_only_receipt(self):
        import chat_brain
        record=signed('send_message',AGENT,{'text':'Suggest an idea','thread_id':AGENT})
        self.kv.add(record)
        original=self.kv
        def transport(url,token,method='GET',data=None):
            if method=='PUT' and json.loads(data).get('state')=='applied':
                raise OSError('fixture-private-network-detail')
            return original(url,token,method,data)
        with mock.patch.object(chat_brain,'office_reply',return_value=('applied','agent reply ready','Answer')) as reply, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(ci.run(self.db,__import__('test_office_controls').ENV,transport,NOW)['applied'],1)
            self.assertEqual(self.kv.receipt()['state'],'held')
            self.run_request()
        reply.assert_called_once()
        self.assertEqual(self.kv.receipt()['reply'],'Answer')
        self.assertEqual(len(output.getvalue().splitlines()),1)
        self.assertNotIn('fixture-private-network-detail',output.getvalue())

    def test_completed_history_does_not_starve_new_requests_or_change_old_outcomes(self):
        self.kv.add(signed('pause','robot',ident='old:pause'))
        self.run_request()
        original=self.kv.receipt('old:pause')
        # More completed source keys than the per-run work batch are retained by the office.
        with ci._connect(self.db) as conn:
            for i in range(75):
                rec=signed('pause','robot',ident=f'history:{i}')
                self.kv.add(rec)
                conn.execute('INSERT INTO office_requests VALUES (?,?,?,?)',
                             (rec['id'],rec['nonce'],ci._digest(ci._signed(rec),'fixture-control-signing-value'),json.dumps(original)))
        self.kv.add(signed('resume','robot',ident='new:resume'))
        self.assertEqual(self.run_request()['applied'],1)
        self.assertFalse(guardrails.is_paused(self.db))
        self.run_request(now=NOW+90000)
        self.assertEqual(self.kv.receipt('old:pause'),original)

    def test_office_runs_without_telegram_and_before_a_telegram_outage(self):
        import telegram_inbox
        with mock.patch('notify._telegram_configured',return_value=(False,'','')), \
                mock.patch.object(ci,'run',return_value={'applied':1,'refused':0}) as office:
            self.assertFalse(telegram_inbox.run(self.db)['configured'])
        office.assert_called_once_with(self.db)
        events=[]
        def poll(*args,**kwargs):
            events.append('telegram'); raise RuntimeError('fixture outage')
        with mock.patch('notify._telegram_configured',return_value=(True,'fixture','42')), \
                mock.patch.object(ci,'run',side_effect=lambda *a:events.append('office')), \
                mock.patch.object(telegram_inbox,'process_updates',side_effect=poll):
            with self.assertRaises(RuntimeError): telegram_inbox.run(self.db)
        self.assertEqual(events,['office','telegram'])

    def test_workflow_supplies_existing_provider_configuration_to_office_agent_questions(self):
        from pathlib import Path
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/agent.yml').read_text()
        block=workflow.split('      - name: Telegram inbox',1)[1].split('      - name: Export approved content',1)[0]
        for variable in ('GROQ_API_KEY','GEMINI_API_KEY','GEMINI_API_KEY_2','GITHUB_MODELS_TOKEN','CF_KV_NAMESPACE_ID','CONTROL_SIGNING_KEY'):
            self.assertIn(variable+':',block)

    def test_kv_receipts_after_a_failed_save_do_not_consume_new_dispatch_budget(self):
        from test_office_controls import KEY
        guardrails.set_paused(True,self.db)
        for index in range(ci.MAX_REQUESTS+5):
            rec=signed('pause','robot',ident=f'old:{index:03}')
            self.kv.add(rec)
            body=ci._receipt(rec['id'],'applied','fixture completed',NOW,source_signature=rec['signature'])
            self.kv.values[ci.RECEIPT_PREFIX+rec['id']]=json.dumps({**body,'signature':ci._digest(body,KEY)})
        self.kv.add(signed('resume','robot',ident='zz:new'))
        self.assertEqual(self.run_request()['applied'],1)
        self.assertFalse(guardrails.is_paused(self.db))
