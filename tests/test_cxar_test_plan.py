import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from tools import ci_test_plan as planner

class TestPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.git('init','-q');self.git('config','user.name','Test');self.git('config','user.email','name@example.com')
        (self.root/'robot.py').write_text('original code\n');self.commit()
        self.base=self.git('rev-parse','HEAD').stdout.strip()

    def git(self,*args):return subprocess.run(['git',*args],cwd=self.root,capture_output=True,text=True,check=True)
    def commit(self):self.git('add','-A');self.git('commit','-qm','test fixture')
    def decision(self,event='workflow_dispatch',reason='manual',force='no',base=None):
        return planner.plan(self.root,self.base if base is None else base,event,reason,force)[1]

    def test_state_only_scheduled_manual_and_doorbell_skip_but_forced_runs_test(self):
        for path in ('agent_data.db.enc','products.tar.gz.enc','web/data/status.json','.scheduler_locks/slot','docs/guide.md'):
            target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('fake state\n')
        self.commit()
        for event,reason in [('schedule',''),('workflow_dispatch','doorbell'),('workflow_dispatch','manual')]:
            self.assertFalse(self.decision(event,reason))
            self.assertTrue(self.decision(event,reason,'yes'))
        self.assertTrue(self.decision('workflow_dispatch','unrecognized'))
        self.assertTrue(self.decision('push','manual'))

    def test_any_code_tests_configuration_workflow_unknown_files_and_deletion_run(self):
        for name in ('robot.py','tests/test_new.py','.github/workflows/agent.yml','config.json','new-extension.xyz','tools/new.py'):
            with self.subTest(name=name):
                target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('changed\n');self.commit()
                self.assertTrue(self.decision())
                self.base=self.git('rev-parse','HEAD').stdout.strip()
        (self.root/'robot.py').unlink();self.commit();self.assertTrue(self.decision())

    def test_missing_invalid_unrelated_baselines_and_git_failures_test(self):
        for base in ('','malformed','0'*40):self.assertTrue(self.decision(base=base))
        with mock.patch.object(planner.subprocess,'run',side_effect=OSError):
            out=self.root/'output';baseline=self.root/'base';baseline.write_text(self.base)
            with mock.patch.dict(os.environ,{'GITHUB_OUTPUT':str(out)}):
                self.assertEqual(planner.main(['--repo',str(self.root),'--baseline-file',str(baseline)]),0)
            self.assertIn('run=true',out.read_text())

    def test_workflow_input_and_real_decision_command(self):
        source=(Path(__file__).resolve().parents[1]/'.github/workflows/agent.yml').read_text()
        self.assertIn('      full_tests:',source)
        step=source.split('- name: Check whether the full suite is needed',1)[1].split('# Preserve image-test coverage',1)[0]
        self.assertIn('github.event.inputs.full_tests',step)
        self.assertIn('python3 tools/ci_test_plan.py',step)
        self.assertIn('--event "$EVENT" --reason "$REASON" --force "$FULL_TESTS"',step)
        baseline=self.root/'baseline';baseline.write_text(self.base);out=self.root/'output'
        with mock.patch.dict(os.environ,{'GITHUB_OUTPUT':str(out)}):
            self.assertEqual(planner.main(['--repo',str(self.root),'--baseline-file',str(baseline),'--event','workflow_dispatch','--reason','doorbell']),0)
        self.assertIn('run=false',out.read_text())
        # Actual workflow YAML is parsed when the optional parser is available.
        try:import yaml
        except ImportError:return
        document=yaml.safe_load(source)
        self.assertIn('cycle',document['jobs'])
