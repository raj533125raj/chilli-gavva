"""Proposal backlogs cannot consume the product-draft limit or hide top niches."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import guardrails
import scheduler
import workforce
import scout


class DraftCapacityTests(unittest.TestCase):
    def test_proposals_do_not_use_draft_slots_but_product_drafts_still_do(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'queue.db')
            for i in range(workforce.MAX_PENDING_APPROVALS):
                guardrails.request_approval('scout_idea','Proposal '+str(i),'Idea',db_path=db)
            self.assertFalse(scheduler._queue_full(db))
            task={'payload':{'topic':'Budget Spreadsheet'}}
            self.assertTrue(workforce.step_plan(task,{'db_path':db})['next'])
            for i in range(workforce.MAX_PENDING_APPROVALS):
                guardrails.request_approval('public_draft','Draft '+str(i),'Private pack',db_path=db)
            self.assertTrue(scheduler._queue_full(db))
            self.assertEqual(workforce.step_plan(task,{'db_path':db})['next'],[])
            self.assertEqual(len(guardrails.pending(db)),2*workforce.MAX_PENDING_APPROVALS)

    def test_every_requested_sell_test_format_has_priority_without_score_changes(self):
        import format_registry
        for kind in ('sheets','biz_sheets','planner','resume','notion','digital_planner'):
            candidate={'idea':'Private product','format':kind,'total_score':85}
            self.assertEqual(scout.candidate_priority(candidate),101)
            self.assertEqual(candidate['total_score'],85)
            self.assertTrue(format_registry.get_format_info(kind))
        self.assertGreater(scout.candidate_priority({'idea':'Budget sheet','format':'sheets','total_score':85}),
                           scout.candidate_priority({'idea':'Teacher planner','format':'teacher_planner','total_score':85}))

    def test_deferred_duplicates_and_revisions_keep_owner_control_without_starving_new_drafts(self):
        import datetime as dt
        import checker
        now=dt.datetime.now(dt.timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'queue.db')
            original=guardrails.request_approval('public_draft','Focus Planner','Unique focus writing page',checker.format_review(90,[],'pass'),db)
            guardrails.decide(original,True,db)
            for i in range(workforce.MAX_PENDING_APPROVALS):
                aid=guardrails.request_approval('public_draft','Focus Planner','Unique focus writing page',checker.format_review(90,[],'pass'),db)
                conn=guardrails.connect(db)
                conn.execute('UPDATE approvals SET ts=? WHERE id=?',((now-dt.timedelta(hours=30)).isoformat(),aid));conn.commit();conn.close()
            # Identical scores keep the broken-checker guard active first.
            self.assertEqual(workforce.active_draft_backlog(db),workforce.MAX_PENDING_APPROVALS)
            # A real revise signal is also left for the owner, never approved.
            conn=guardrails.connect(db)
            for i,aid in enumerate([p['id'] for p in guardrails.pending(db)]):
                conn.execute('UPDATE approvals SET review=? WHERE id=?',(checker.format_review(80+i,[],'pass'),aid))
            conn.commit();conn.close()
            self.assertEqual(workforce.active_draft_backlog(db),0)
            self.assertFalse(scheduler._queue_full(db))
            self.assertEqual(len(guardrails.pending(db)),workforce.MAX_PENDING_APPROVALS)
            self.assertEqual(__import__('auto_approve').auto_decide_drafts(db,now),[])

    def test_capacity_replay_uses_distinct_real_table_structures(self):
        from tools import robot_sim_world as world
        import auto_approve
        from itertools import combinations
        drafts=[world._capacity_draft(subject) for subject in world.CAPACITY_SUBJECTS]
        self.assertEqual(len(set(drafts)),6)
        for a,b in combinations(drafts,2):
            self.assertLessEqual(auto_approve.jaccard_similarity(auto_approve._shingles(a),auto_approve._shingles(b)),auto_approve.MAX_SHINGLE_SIMILARITY)
        with mock.patch.object(world,'REPO_MARK',str(Path(world.__file__).resolve().parents[1])):
            specs=[world._capacity_spec(subject) for subject in world.CAPACITY_SUBJECTS]
        self.assertEqual(len({tuple(tab['name'] for tab in spec['sheets']) for spec in specs}),6)
        self.assertTrue(all(len(spec['sheets'])==3 for spec in specs))

    def test_capacity_profile_never_reads_from_current_directory(self):
        from tools import robot_sim_world as world
        with mock.patch.object(world,'SIM_DIR',''),mock.patch.object(world,'_load',side_effect=AssertionError('No relative reads')):
            self.assertIsNone(world._capacity_subject('irregular-income'))
            self.assertEqual(__import__('json').loads(world.fake_llm_reply('You are the QA Checker')[1])['score'],88)
