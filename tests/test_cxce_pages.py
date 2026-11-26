import contextlib
import io
from pathlib import Path
import tempfile
import unittest
import urllib.error
from scripts import pages_preflight as pages

ROOT=Path(__file__).resolve().parents[1]
ENV={'GITHUB_EVENT_NAME':'workflow_dispatch','PUBLISH_APPROVED':'true','DB_KEY':'fake-private-key',
     'GITHUB_TOKEN':'fake-token','GITHUB_REPOSITORY':'example/repo'}

class Pages(unittest.TestCase):
    def test_default_or_push_never_reads_network(self):
        for env in ({},dict(ENV,PUBLISH_APPROVED='false'),dict(ENV,GITHUB_EVENT_NAME='push')):
            ready,line=pages.check(env,lambda path:self.fail('No owner publication request'))
            self.assertFalse(ready);self.assertIn('owner',line)
    def test_missing_key_is_green_with_one_safe_line(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'output';stream=io.StringIO()
            with contextlib.redirect_stdout(stream):
                result=pages.main(dict(ENV,DB_KEY='',GITHUB_OUTPUT=str(output)),opener=lambda *a,**k:self.fail('No key'))
            self.assertEqual(result,0);self.assertEqual(output.read_text(),'ready=false\n')
            self.assertEqual(len(stream.getvalue().splitlines()),1)
            self.assertIn('DB_KEY setup cheyandi',stream.getvalue())
            self.assertNotIn('fake-token',stream.getvalue())
    def test_missing_pages_and_wrong_source(self):
        def missing(path):raise urllib.error.HTTPError('https://example.com',404,'missing',{},None)
        for fetch in (missing,lambda path:{'build_type':'legacy'}):
            ready,line=pages.check(ENV,fetch)
            self.assertFalse(ready);self.assertIn('GitHub Actions',line)
    def test_missing_environment_and_network_failure(self):
        responses=iter(({'build_type':'workflow'},{}))
        self.assertIn('Environments',pages.check(ENV,lambda path:next(responses))[1])
        def offline(path):raise OSError('network')
        ready,line=pages.check(ENV,offline)
        self.assertFalse(ready);self.assertIn('malli cheyandi',line)
    def test_good_setup_only_gets_configuration(self):
        seen=[]
        def fetch(path):
            seen.append(path)
            return {'build_type':'workflow'} if path.endswith('/pages') else {'name':'github-pages'}
        self.assertTrue(pages.check(ENV,fetch)[0])
        self.assertEqual(seen,['/repos/example/repo/pages','/repos/example/repo/environments/github-pages'])
    def test_workflow_is_manual_and_deploy_depends_on_setup(self):
        text=(ROOT/'.github/workflows/publish.yml').read_text()
        self.assertNotIn('  push:',text)
        self.assertIn('default: false',text)
        self.assertIn("if: needs.check-setup.outputs.ready == 'true'",text)
        self.assertIn('run: python3 scripts/pages_preflight.py',text)
        self.assertIn('PUBLISH_APPROVED: ${{ inputs.publish_approved }}',text)
        self.assertIn('Source', (ROOT/'docs/PAGES-TENGLISH.md').read_text())
