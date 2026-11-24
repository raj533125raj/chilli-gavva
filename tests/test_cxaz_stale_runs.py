from tests.test_cxo_alarm_worker import AlarmButtonTests
import daily_report
import unittest

class StaleRunTests(AlarmButtonTests):
    def test_old_run_overrides_pacing_but_not_hard_cap_or_dispatch_cooldown(self):
        self.run_case(r"""
updates=[];
values.set('runs',JSON.stringify({month:'2026-11',minutes:1650,runStarted:now-720*60000,runEnded:now-710*60000}));
values.set('clock',JSON.stringify({lastDispatch:now-720*60000}));
assert.equal((await worker.tick(env,now,fetchFn)).action,'alarm');
assert.equal((await worker.tick(env,now+31*60000,fetchFn)).action,'budget-wait');
values.set('runs',JSON.stringify({month:'2026-11',minutes:1900,runEnded:now-720*60000}));
assert.equal((await worker.tick(env,now+211*60000,fetchFn)).action,'budget-wait');
""")

    def test_healthy_run_does_not_bypass_pacing(self):
        self.run_case(r"""
updates=[];
values.set('runs',JSON.stringify({month:'2026-11',minutes:1650,runEnded:now-60*60000}));
assert.equal((await worker.tick(env,now,fetchFn)).action,'budget-wait');
""")

    def test_daily_report_uses_actual_run_age(self):
        self.assertEqual(daily_report.last_run_line({'minutes_since_last_run':720}),'Last robot run: 12.0 h ago')
        self.assertIn('unknown',daily_report.last_run_line({'minutes_since_last_dispatch':1}))
