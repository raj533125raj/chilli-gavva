"""Time/state trigger requests exercise fake KV, scheduler ticks and workforce dispatch."""
import datetime
import json
from unittest import mock

import guardrails
import office_tasks
import office_triggers as triggers
import workforce
from test_office_controls import OfficeBase, signed, NOW, AGENT


def at_value(stamp=NOW+60):
    return {'label':'Summarize progress','agent_id':AGENT,'enabled':True,'kind':'at',
            'at':datetime.datetime.fromtimestamp(stamp,datetime.timezone.utc).isoformat(),
            'watch_id':None,'match_state':None}


def state_value():
    return {'label':'Send a waiting drafts summary','agent_id':AGENT,'enabled':True,'kind':'state',
            'condition':{'metric':'drafts_waiting','op':'>','value':3},'action':'summary'}


class TriggerTests(OfficeBase):
    def submit(self,value=None,action='set_trigger',ident='trigger:req',target='trigger:one'):
        self.run_request(signed(action,target,at_value() if value is None else value,ident))
        return self.kv.receipt(ident)

    def tick(self,now=NOW+60):
        return office_tasks.tick(self.db,now)['queued']

    def pending(self):
        return self.rows("SELECT id,payload FROM tasks WHERE kind='office_task' AND status='queued'")

    def test_time_trigger_fires_once_and_edit_cancels_superseded_work(self):
        self.assertEqual(self.submit()['state'],'applied')
        self.assertEqual(self.tick(NOW),0)
        self.assertEqual(self.tick(),1)
        self.assertEqual(self.tick(NOW+86400),0)
        receipt=self.submit({'at':datetime.datetime.fromtimestamp(NOW+86400,datetime.timezone.utc).isoformat()},
                            action='edit_trigger',ident='edit:req')
        self.assertEqual(receipt['state'],'applied')
        self.assertEqual(self.pending(),[])
        self.assertEqual(self.tick(NOW+86400),1)

    def test_state_threshold_uses_edges_and_sends_database_summary(self):
        self.assertEqual(self.submit(state_value())['state'],'applied')
        for _ in range(3): guardrails.request_approval('public_draft','Draft','body',db_path=self.db)
        self.assertEqual(self.tick(),0)
        guardrails.request_approval('public_draft','Fourth','body',db_path=self.db)
        self.assertEqual(self.tick(),1)
        task=workforce.claim_next(self.db)
        with mock.patch('notify.send',return_value=True) as send:
            result=workforce.STEPS['office_task'](task,{'db_path':self.db,'llm':mock.Mock(side_effect=AssertionError('summary is local'))})
        self.assertIn('4',result['answer']); send.assert_called_once()
        workforce.complete(task['id'],result,self.db)
        self.assertEqual(self.tick(NOW+7200),0)  # still true: do not flood owner
        conn=triggers.connect(self.db)
        with conn: conn.execute("UPDATE approvals SET status='rejected'")
        conn.close()
        self.assertEqual(self.tick(NOW+7201),0)
        for _ in range(4): guardrails.request_approval('public_draft','New','body',db_path=self.db)
        self.assertEqual(self.tick(NOW+7202),1)

    def test_agent_state_and_task_done_use_real_references(self):
        task=workforce.enqueue('research',{'topic':'x'},db_path=self.db)
        value={**at_value(),'kind':'task_done','at':None,'watch_id':f'task:{task}'}
        self.assertEqual(self.submit(value)['state'],'applied')
        self.assertEqual(self.tick(),0)
        workforce.complete(task,{},self.db)
        self.assertEqual(self.tick(),1)
        value={**at_value(),'kind':'agent_state','at':None,'watch_id':AGENT,'match_state':'idle'}
        self.assertEqual(self.submit(value,ident='agent:req',target='trigger:agent')['state'],'applied')
        self.assertEqual(self.tick(),1)

    def test_disable_delete_caps_pause_and_dry_run(self):
        self.submit()
        self.assertEqual(office_tasks.tick(self.db,NOW+60,dry_run=True)['queued'],1)
        self.assertEqual(self.pending(),[])
        self.submit({'enabled':False},'edit_trigger','off:req')
        self.assertEqual(self.tick(),0)
        self.submit({'enabled':True},'edit_trigger','on:req')
        guardrails.set_paused(True,self.db); self.assertEqual(self.tick(),0)
        guardrails.set_paused(False,self.db); self.assertEqual(self.tick(),1)
        self.run_request(signed('delete_trigger','trigger:one',ident='delete:req'))
        self.assertEqual(self.kv.receipt('delete:req')['state'],'applied')
        self.assertEqual(self.pending(),[])
        with mock.patch.object(triggers,'MAX_TRIGGERS',0):
            self.assertEqual(self.submit(target='trigger:two',ident='cap:req')['state'],'held')
            self.assertEqual(self.submit(ident='restore:req')['state'],'held')

    def test_invalid_conditions_extra_commands_and_unknown_references_are_rejected(self):
        values=[{**state_value(),'condition':{'metric':'arbitrary_sql','op':'>','value':1}},
                {**state_value(),'condition':{'metric':'drafts_waiting','op':'eval','value':1}},
                {**state_value(),'condition':{'metric':'drafts_waiting','op':'>','value':True}},
                {**at_value(),'command':'echo unsafe'}, {**at_value(),'at':'tomorrow'},
                {**at_value(),'kind':'task_done','at':None,'watch_id':'task:999999'},
                {**at_value(),'agent_id':'imaginary-agent'}]
        for i,value in enumerate(values):
            self.assertEqual(self.submit(value,ident=f'bad:{i}')['state'],'rejected')
        self.assertEqual(self.tick(NOW+99999),0)

    def test_missions_and_triggers_share_dispatch_and_pending_caps(self):
        self.run_request(signed())
        for i in range(12): self.submit(ident=f'req:{i}',target=f'trigger:{i}')
        with mock.patch.object(office_tasks,'MAX_DISPATCH',2):
            self.assertEqual(self.tick(NOW+3600),2)
        with mock.patch.object(office_tasks,'MAX_PENDING',2):
            self.assertEqual(self.tick(NOW+3601),0)

    def test_cube_off_holds_trigger_until_enabled(self):
        import cubes
        self.submit(); cubes.turn_off('research',self.db)
        self.assertEqual(self.tick(),0)
        cubes.turn_on('research',self.db)
        self.assertEqual(self.tick(),1)
