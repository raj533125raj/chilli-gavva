"""Policies cannot lift hard caps or approval rules; every provider attempt reserves atomically."""
import concurrent.futures
import json
from unittest import mock

import guardrails
import llm_router
import office_tasks
import scheduler
import workforce
from test_office_controls import OfficeBase, signed, NOW


class PolicyTests(OfficeBase):
    def policy(self,mode='supervised',limit=10,unit='calls',ident='policy:one'):
        self.run_request(signed('set_policy','robot',{'autonomy':mode,'budget_limit':limit,'budget_unit':unit},ident))
        return self.kv.receipt(ident)

    def test_each_autonomy_mode_is_applied_without_enabling_auto_approval(self):
        for mode in ('manual','supervised','autonomous'):
            self.assertEqual(self.policy(mode,ident=mode)['state'],'applied')
            self.assertEqual(guardrails.autonomy(self.db),mode)
            self.assertEqual(guardrails.automation_allowed(self.db),mode!='manual')
        approval=guardrails.request_approval('public_draft','Draft','body',db_path=self.db)
        self.assertEqual(guardrails.get_approval(approval,self.db)['status'],'pending')

    def test_manual_suspends_scheduler_workforce_and_recurring_tasks(self):
        self.run_request(signed()); self.policy('manual')
        self.assertEqual(office_tasks.tick(self.db,NOW+3600)['queued'],0)
        self.assertTrue(scheduler.run_now(db_path=self.db)['skipped'])
        workforce.enqueue('research',{'topic':'new'},db_path=self.db)
        self.assertEqual(workforce.run_once(db_path=self.db)['status'],'manual')
        self.assertIsNone(workforce.claim_next(self.db))
        self.policy('supervised',ident='resume:policy')
        self.assertEqual(office_tasks.tick(self.db,NOW+3600)['queued'],1)

    def test_manual_suspends_worker_side_work_and_product_cli(self):
        import worker_daemon
        import product_builder
        self.policy('manual')
        with mock.patch('improver.evolve',side_effect=AssertionError('manual cannot evolve')):
            result,_=worker_daemon.one_pass(self.db,write_snapshot=False)
        self.assertEqual(result['status'],'manual')
        with mock.patch.object(product_builder,'build_pending') as build:
            product_builder.main(['--db',self.db])
        build.assert_not_called()

    def test_call_budget_is_effective_in_all_readers_and_never_exceeds_configured_cap(self):
        self.assertEqual(self.policy(limit=2)['state'],'applied')
        self.assertEqual(guardrails.daily_cap(self.db),2)
        with mock.patch.object(guardrails,'_TESTING',False):
            guardrails.record_call(self.db); guardrails.record_call(self.db)
            self.assertFalse(guardrails.can_call_llm(self.db))
            with self.assertRaises(guardrails.BudgetExceeded): guardrails.record_call(self.db)
        self.assertEqual(guardrails.calls_today(self.db),2)
        self.assertEqual(self.policy(limit=guardrails.configured_daily_cap()+1,ident='too:high')['state'],'rejected')
        self.assertEqual(guardrails.daily_cap(self.db),2)
        self.assertEqual(self.policy(limit=1,ident='below:usage')['state'],'held')
        self.assertEqual(self.policy('autonomous',limit=3,unit='tokens',ident='tokens')['state'],'held')
        self.assertEqual(guardrails.autonomy(self.db),'supervised')

    def test_zero_budget_and_separate_budget_autonomy_edits(self):
        self.run_request(signed('set_budget','robot',{'limit':0,'unit':'calls'},ident='zero'))
        self.assertEqual(self.kv.receipt('zero')['state'],'applied')
        self.assertFalse(guardrails.can_call_llm(self.db))
        self.run_request(signed('set_autonomy','robot','manual',ident='manual'))
        self.assertEqual(self.kv.receipt('manual')['state'],'applied')
        self.assertEqual(guardrails.daily_cap(self.db),0)
        self.run_request(signed('set_budget','robot',5,ident='raise:within:cap'))
        self.assertEqual(guardrails.daily_cap(self.db),5)
        self.assertEqual(guardrails.autonomy(self.db),'manual')

    def test_invalid_policy_values_are_rejected_without_partial_updates(self):
        values=[{'autonomy':'supervised','budget_limit':True,'budget_unit':'calls'},
                {'autonomy':'godmode','budget_limit':1,'budget_unit':'calls'},
                {'autonomy':'manual','budget_limit':-1,'budget_unit':'calls'},
                {'autonomy':'manual','budget_limit':1,'budget_unit':'dollars'},
                {'autonomy':'autonomous','budget_limit':1,'budget_unit':'calls','auto_approve':True}]
        for i,value in enumerate(values):
            self.run_request(signed('set_policy','robot',value,ident=f'bad:{i}'))
            self.assertEqual(self.kv.receipt(f'bad:{i}')['state'],'rejected')
        self.assertEqual(guardrails.autonomy(self.db),'supervised')

    def test_concurrent_reservations_never_overshoot_a_shared_budget(self):
        self.policy(limit=3)
        def reserve(_):
            try: guardrails.record_call(self.db); return True
            except guardrails.BudgetExceeded: return False
        with mock.patch.object(guardrails,'_TESTING',False), concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            accepted=list(pool.map(reserve,range(16)))
        self.assertEqual(accepted.count(True),3)
        self.assertEqual(guardrails.calls_today(self.db),3)

    def test_fallback_and_retries_cannot_overshoot_and_use_the_requested_database(self):
        self.policy(limit=1)
        calls=[]
        def provider(prompt,**kwargs):
            for _ in range(2):
                try:
                    guardrails.record_call()
                    calls.append('attempt')
                except guardrails.BudgetExceeded:
                    pass
            return None
        with mock.patch.object(guardrails,'_TESTING',False), \
                mock.patch.object(llm_router,'in_cooldown',return_value=False), \
                mock.patch.object(llm_router,'provider_status',return_value=[]), \
                mock.patch.object(llm_router,'_groq',side_effect=provider), \
                mock.patch.object(llm_router,'_gemini',side_effect=provider), \
                mock.patch.object(llm_router,'_cloudflare',return_value=None), \
                mock.patch.object(llm_router,'_openrouter',return_value=None), \
                mock.patch.object(llm_router,'_freellmapi',return_value=None):
            self.assertIsNone(llm_router.chat('Hello',db_path=self.db))
        self.assertEqual(calls,['attempt'])
        self.assertEqual(guardrails.calls_today(self.db),1)

    def test_bad_stored_budget_fails_closed_and_policy_uses_lower_configured_ceiling(self):
        self.policy(limit=20)
        with mock.patch.object(guardrails,'configured_daily_cap',return_value=4):
            self.assertEqual(guardrails.daily_cap(self.db),4)
            self.assertEqual(self.policy(limit=5,ident='above:config')['state'],'rejected')
        conn=guardrails.connect(self.db)
        with conn: conn.execute("UPDATE control SET value='invalid' WHERE key='office_daily_call_cap'")
        conn.close()
        self.assertEqual(guardrails.daily_cap(self.db),0)
