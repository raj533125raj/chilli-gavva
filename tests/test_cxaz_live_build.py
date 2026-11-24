import datetime as dt
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
try:
    from PIL import Image
except ImportError:
    Image=None
import auto_approve
import daily_report
import guardrails
import product_builder as pb
import product_council
import product_pipeline as pipeline
from tools import robot_sim_world

class LiveCycleTests(unittest.TestCase):
    def test_workflow_build_is_after_inbox_approval_and_before_encryption(self):
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/agent.yml').read_text()
        self.assertLess(workflow.index('- name: Telegram inbox'),workflow.index('- name: Build product files'))
        self.assertLess(workflow.index('- name: Build product files'),workflow.index('- name: Pack private products'))

    @unittest.skipUnless(Image,'needs Pillow')
    def test_three_freelancer_approvals_reach_real_files_in_same_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'queue.db');out=Path(temp)/'products'
            for aid,title in zip(range(109,112),['Invoice tracker for designers','Client project tracker for writers','Income tracker for consultants']):
                with guardrails.connect(db) as conn:
                    conn.execute("INSERT INTO approvals(id,ts,kind,title,payload,review,status,decided_ts,reason) VALUES(?,?,'public_draft',?,?,?,'approved',?,'auto-approved')",
                                 (aid,'2026-11-22T00:00:00','Draft: [Freelancer Trackers Agent] '+title,'PRODUCT NAME: '+title+'\nBUYER: freelancers\nWHAT\'S INSIDE: Invoices and clients spreadsheet\nFormat: sheets','Checker: 90/100 | CHECKER: PASS','2026-11-23T12:00:00'))
                product_council.log_debate(aid,1,'council_summary','PASS','Reviewed draft',db_path=db)
            before=daily_report.product_path_lines(db,dt.datetime(2026,11,23,12,5))
            self.assertTrue(any('Approved build queue: 3' in line for line in before))
            with mock.patch.object(pipeline,'_allowed',return_value=True), mock.patch.dict(os.environ,{'DB_KEY':''}), mock.patch.object(pipeline,'_marketing_lines',return_value={}):
                results=pipeline.run_pending(db,str(out),lambda prompt,size=None:robot_sim_world.fake_llm_reply(prompt)[1],notify_fn=lambda text:True)
            self.assertEqual({r['id'] for r in results if r['status']=='prepared'},{109,110,111},results)
            for build in pb.builds(db):
                pack=out/'upload'/build['folder']
                self.assertTrue((pack/'listing.txt').is_file())
                with zipfile.ZipFile(pack/'product.zip') as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertTrue(archive.namelist())
