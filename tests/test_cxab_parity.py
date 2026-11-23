"""Parity exercises isolation, a hosted path and real UTC boundary clocks."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tools import ci_parity


class ParityTests(unittest.TestCase):
    def test_environment_has_no_owner_values_and_forces_ci(self):
        with tempfile.TemporaryDirectory() as temp:
            env = ci_parity.clean_env(temp, Path(temp) / 'bin/python3')
            self.assertEqual(env['TZ'], 'UTC')
            self.assertEqual(env['CHILLI_TEST_JOBS'], '4')
            self.assertEqual(env['GITHUB_ACTIONS'], 'true')
            self.assertNotIn('TELEGRAM_BOT_TOKEN', env)
            self.assertEqual(env['PYTHONNOUSERSITE'], '1')

    def test_clock_discovery_includes_calendar_and_owner_flows(self):
        modules = ci_parity.sensitive_modules(ci_parity.ROOT)
        for name in ('test_kh_post_telegram', 'test_daily_report', 'test_office_fixture', 'test_cxaa_clock_safety'):
            self.assertIn('tests.' + name, modules)

    def test_clock_preserves_timezones(self):
        for moment in ci_parity.CLOCKS:
            script = ('import datetime as d; from tools.robot_sim_world import install_clock; '
                      f'install_clock(d.datetime.fromisoformat({moment!r}).timestamp()); '
                      'from post_planner import owner_today; '
                      'print(d.datetime.now(d.timezone.utc).strftime("%H:%M"),owner_today())')
            result = subprocess.run([sys.executable, '-c', script], cwd=ci_parity.ROOT,
                                    capture_output=True, text=True, check=True)
            self.assertIn(moment[11:16], result.stdout)
            self.assertIn('2026-11-24' if moment[11:13] == '19' else '2026-11-23', result.stdout)

    def test_refuses_repository_as_workspace(self):
        with self.assertRaises(ValueError):
            ci_parity.sync_checkout(ci_parity.ROOT)

class CalendarRegressionTests(unittest.TestCase):
    def test_cli_defaults_to_owner_day(self):
        from unittest import mock
        import post_planner
        import datetime
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(post_planner, 'owner_today', return_value=datetime.date(2026,11,24)), \
                mock.patch.object(post_planner, 'today_text', return_value='Owner day') as today, \
                mock.patch.object(post_planner, 'load_plan', return_value=[]):
            self.assertEqual(post_planner.main(['--today','--plan-path',str(Path(temp)/'plan.json')]),0)
            self.assertEqual(today.call_args.args[1], '2026-11-24')

    def test_usage_day_is_utc_even_when_owner_day_has_changed(self):
        import datetime
        from unittest import mock
        import guardrails
        real = datetime.datetime
        class Clock(real):
            @classmethod
            def now(cls, tz=None):
                return real(2026,11,23,19,45,tzinfo=datetime.timezone.utc).astimezone(tz)
        with mock.patch.object(guardrails.datetime, 'datetime', Clock):
            self.assertEqual(guardrails.today(),'2026-11-23')
