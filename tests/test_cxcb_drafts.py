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
        # CXCB-2: proposals never claim that product drafts are paused.
        self.assertFalse(auto_approve.queue_digest(self.db,NOW)['new_drafts_paused'])
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

    def test_archive_and_restore_without_approval(self):
        import os
        import telegram_inbox as inbox
        auto_approve.auto_decide_drafts(self.db,NOW)
        self.assertEqual(daily_report.product_path(self.db,NOW)['proposals_waiting'],0)
        self.assertEqual(guardrails.get_approval(1,self.db)['status'],'archived')
        chat=str(int('1234' '5678'))
        with patch.dict(os.environ,TELEGRAM_CHAT_ID=chat):
            handled,reply=inbox.call_plugins('/restore 1','unused',chat,db_path=self.db,now=NOW)
        self.assertTrue(handled);self.assertIn('waiting',reply['text'])
        self.assertEqual(guardrails.get_approval(1,self.db)['status'],'pending')
        self.assertEqual(auto_approve.archive_scout_proposals(self.db,NOW),[])
    def test_reports_and_reminder_use_product_split(self):
        import telegram_inbox as inbox
        import tg_talk
        self.assertIn('No drafts waiting',inbox.list_text(self.db))
        self.assertIn('Proposals waiting: 16',inbox.list_text(self.db))
        self.assertIn('Product drafts waiting: 0',auto_approve.queue_digest_line(self.db,NOW))
        self.assertEqual(tg_talk.sell_card_data({'db_path':self.db,'products_dir':str(self.products)})['drafts'],0)
        with patch.object(inbox,'send_text') as send:
            self.assertFalse(inbox.remind_waiting_drafts('unused','owner',self.db,NOW.timestamp()))
            send.assert_not_called()
    def test_one_real_run_makes_product_draft_with_working_ai(self):
        import rest_window
        workforce.enqueue('draft',{'topic':'[Budget Sheets Agent] Savings Goal Worksheet'},db_path=self.db)
        with patch.object(rest_window,'in_rest_window',return_value=False):
            result=workforce.run_once(max_tasks=1,db_path=self.db,llm=lambda prompt:'Savings goal worksheet\nFORMAT: sheets',rng=random.Random(2))
        self.assertEqual(result['ran'],1,result)
        self.assertEqual(len(auto_approve.split_waiting(self.db)[0]),1)

    def test_auto_decision_owner_copy_uses_the_same_buyer_title(self):
        aid=guardrails.request_approval('public_draft','Draft: [ADHD Planners Agent] Focus (v4, llm)',
                                       'FORMAT: adhd_planner',review='Checker: 89/100 | CHECKER: PASS',db_path=self.db)
        with guardrails.connect(self.db) as conn:
            conn.execute('UPDATE approvals SET ts=? WHERE id=?',((NOW-dt.timedelta(hours=30)).isoformat(),aid))
        decisions=auto_approve.auto_decide_drafts(self.db,NOW)
        line=next(row['line'] for row in decisions if row['id']==aid)
        self.assertIn('Adults ADHD Planner Organizing Daily Steps',line)
        self.assertNotIn('Agent',line)

    def test_report_and_writer_share_the_same_active_draft_limit(self):
        guardrails.request_approval('public_draft','Budget Sheet','FORMAT: sheets',db_path=self.db)
        with patch.object(workforce,'MAX_PENDING_APPROVALS',1):
            data=auto_approve.queue_digest(self.db,NOW)
            self.assertTrue(data['new_drafts_paused'])
            self.assertIn('product Drafts limit 1',auto_approve.queue_digest_line(self.db,NOW))
            self.assertEqual(workforce.step_plan({'payload':{'topic':'Budget worksheet'}},{'db_path':self.db})['next'],[])
