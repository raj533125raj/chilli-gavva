"""26 November queue regression, with genuine buyer files in nine packs."""
import datetime as dt
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import auto_approve
import daily_report
import guardrails
import workforce

NOW=dt.datetime(2026,11,26,7,tzinfo=dt.timezone.utc)

class Backlog(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.db=str(self.root/'robot.db')
        self.products=self.root/'products'
        with guardrails.connect(self.db) as conn:
            for aid in range(1,17):
                conn.execute("INSERT INTO approvals(id,ts,kind,title,payload,status) VALUES(?,?,'scout_idea',?,'Owner research proposal','pending')",
                             (aid,(NOW-dt.timedelta(hours=80)).isoformat(),f'[Scout Idea] research idea {aid}'))
    def test_big7_cap_and_structured_classification_already_work(self):
        self.assertEqual(workforce.active_draft_backlog(self.db),0)
        plan=workforce.step_plan({'payload':{'topic':'Budget worksheet'}},{'db_path':self.db})
        self.assertTrue(plan['next'])
        aid=guardrails.request_approval('scout_idea','[Scout Idea] Budget Sheet','Format: sheets',db_path=self.db)
        self.assertEqual(guardrails.get_approval(aid,self.db)['kind'],'public_draft')
        self.assertEqual(workforce.active_draft_backlog(self.db),1)
    def test_reports_expose_existing_contradiction(self):
        view=daily_report.product_path(self.db,NOW)
        self.assertEqual((view['drafts_waiting'],view['proposals_waiting']),(0,16))
        # CXCB-1 evidence: the existing morning digest falsely says drafts paused.
        self.assertTrue(auto_approve.queue_digest(self.db,NOW)['new_drafts_paused'])
    def test_ai_failure_is_explicit_and_allowed_niche_reaches_writer(self):
        ctx={'db_path':self.db,'llm':lambda prompt:None,'rng':random.Random(7)}
        with self.assertRaises(workforce.AIUnavailable):
            workforce.step_draft({'payload':{'topic':'[Budget Sheets Agent] Family Budget Sheet'}},ctx)
        self.assertIn('off-limits',workforce.step_draft({'payload':{'topic':'fish recipe'}},ctx)['summary'])
        ctx['llm']=lambda prompt:'Family budget sheet\nFORMAT: sheets\nTrack planned and actual household spending.'
        result=workforce.step_draft({'payload':{'topic':'[Budget Sheets Agent] Family Budget Sheet'}},ctx)
        self.assertTrue(result['approval_id'])
    def test_nine_genuine_ready_uploads_do_not_block_new_draft(self):
        import mockups
        if not mockups.available():self.skipTest('Pillow and Poppler required')
        import product_builder as pb
        from tools.sell_proof import spec_for
        folder=pb.build_product(spec_for('sheets'),901,out_dir=str(self.products))
        outcome=pb.truth_check_folder(901,folder,db_path=self.db,out_dir=str(self.products),notify_fn=lambda text:True)
        self.assertEqual(outcome['verdict'],'ready',outcome)
        base=self.products/'upload'/folder
        for aid in range(902,910):
            shutil.copytree(base,base.with_name(str(aid)+'-budget-sheet'))
        self.assertEqual(daily_report._count_ready_upload_packs(self.products),9)
        self.assertEqual(workforce.active_draft_backlog(self.db),0)
        result=workforce.step_draft({'payload':{'topic':'[Budget Sheets Agent] Savings Goal Worksheet'}},
                                   {'db_path':self.db,'llm':lambda prompt:'Savings goal workbook\nFORMAT: sheets',
                                    'rng':random.Random(1)})
        self.assertTrue(result['approval_id'])
