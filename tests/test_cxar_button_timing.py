import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import button_timing
import daily_report
import guardrails
import telegram_inbox
from tests.test_cxo_alarm_worker import AlarmButtonTests

NOW=dt.datetime(2026,11,24,12,tzinfo=dt.timezone.utc)
CHAT=int('1234' '5678')

class ClockTimingTests(AlarmButtonTests):
    def test_first_observed_press_to_result_and_duplicate_result_are_real_and_private(self):
        self.run_case(r"""
await worker.tick(env,now,fetchFn);
assert.equal(values.get('button-seen:owner-press'),String(now));
env.WORKER_SECRET='fake-worker';
const request = id => new Request('https://clock.example/button-result',{method:'POST',headers:{Authorization:'Bearer fake-worker','Content-Type':'application/json'},body:JSON.stringify({query_id:id})});
let result=await (await worker.handle(request('owner-press'),env,now+300000,fetchFn)).json();
assert.equal(result.minutes,5);
result=await (await worker.handle(request('owner-press'),env,now+900000,fetchFn)).json();
assert.equal(result.minutes,5);
assert.equal((await (await worker.handle(request('unseen'),env,now+300000,fetchFn)).json()).minutes,null);
assert.equal((await worker.handle(new Request('https://clock.example/button-result',{method:'POST'}),env,now,fetchFn)).status,401);
""")

class MedianTimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=str(Path(self.tmp.name)/'queue.db');guardrails.connect(self.db).close()

    def test_median_uses_only_real_recent_test_results_not_old_message_dates(self):
        for aid,minutes in enumerate((1,2,30),1):self.assertTrue(button_timing.record('fake',aid,self.db,NOW,{'minutes':minutes}))
        button_timing.record('fake',9,self.db,NOW-dt.timedelta(days=2),{'minutes':90})
        button_timing.record('fake',1,self.db,NOW,{'minutes':90})
        self.assertIn('2.0 min (3 tests',button_timing.line(self.db,NOW))
        for value in (None,-1,float('nan'),float('inf'),'2'):
            self.assertFalse(button_timing.record('fake',99,self.db,NOW,{'minutes':value}))
        report=daily_report.daily_text(self.db,NOW,status_fn=lambda:{'minutes_since_last_run':60},products_dir=Path(self.tmp.name)/'products')
        self.assertIn('/buttontest median: 2.0 min',report)

    def test_actual_callback_records_only_delivered_test_results(self):
        update={'update_id':8,'callback_query':{'id':'fake-test-query','data':'t:ping','message':{'date':1,'chat':{'id':CHAT},'message_id':1}}}
        with mock.patch.object(telegram_inbox,'fetch_updates',return_value=[update]),mock.patch.object(telegram_inbox,'acknowledge_update',return_value=True),mock.patch.object(telegram_inbox,'_call',return_value={'ok':True}),mock.patch('always_on.call',return_value={'minutes':4.5}):
            telegram_inbox.process_updates('fake',CHAT,self.db)
        self.assertIn('4.5 min',button_timing.line(self.db))
        self.assertNotIn('1970',button_timing.line(self.db))

    def test_unavailable_observation_is_explicit(self):
        with mock.patch('always_on.call',return_value=None):self.assertFalse(button_timing.record('fake',1,self.db,NOW))
        self.assertIn('unknown',button_timing.line(self.db,NOW))
