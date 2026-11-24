"""Live-shaped twenty-approved backlog; outages wait, one improvement is durable."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import guardrails
import product_builder
import product_council as council
import product_pipeline as pipeline
import daily_report
from tests.test_product_pipeline import DRAFT


class CouncilRecovery(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.db = str(self.root / 'robot.db')
        for target, value in [('guardrails.is_killed', False), ('rest_window.in_rest_window', False),
                              ('guardrails.can_call_llm', True), ('cubes.is_kind_enabled', True)]:
            self.enterContext(patch(target, return_value=value))

    def approved(self):
        aid = guardrails.request_approval('public_draft', 'Resume Bundle', DRAFT, db_path=self.db)
        with guardrails.connect(self.db) as conn:
            conn.execute("UPDATE approvals SET status='approved' WHERE id=?", (aid,))
        return aid

    def model(self, pass_recheck=True, outage_recheck=False):
        counts = {'judge': 0, 'fixer': 0}
        def llm(prompt, **kwargs):
            if 'Critic agent' in prompt: return json.dumps({'weaknesses': [], 'verdict': 'OK'})
            if "Buyer's Voice agent" in prompt: return json.dumps({'conviction': 8, 'answer': 'Clear buyer value'})
            if 'Fixer agent' in prompt:
                counts['fixer'] += 1
                return DRAFT + '\nExtra instructions: Write your experience and proof clearly.'
            if 'Judge agent' in prompt:
                counts['judge'] += 1
                if outage_recheck and counts['judge'] > 1: return None
                return json.dumps({'verdict': 'PASS' if pass_recheck and counts['judge'] > 1 else 'RETRY',
                                   'reasons': ['Buyer instructions need detail']})
        return llm, counts

    def test_twenty_outages_wait_without_failure_and_retry_same_day(self):
        ids = [self.approved() for _ in range(20)]
        with patch.object(pipeline, '_allowed', return_value=True), \
                patch('product_repair.run_daily'), patch.object(pipeline, 'notify_ready'):
            first = pipeline.run_pending(self.db, str(self.root / 'products'), llm=lambda *a, **k: None)
            second = pipeline.run_pending(self.db, str(self.root / 'products'), llm=lambda *a, **k: None)
        self.assertEqual([r['status'] for r in first], ['waiting_ai'] * 20)
        self.assertEqual(len(second), 20)
        self.assertTrue(all(guardrails.get_approval(a, self.db)['status'] == 'approved' for a in ids))
        counts = daily_report.product_path(self.db)
        self.assertEqual(counts['needs_fix'], 0)
        self.assertEqual(counts['council_waiting'], 20)
        self.assertIn('AI kosam waiting 20', '\n'.join(daily_report.product_path_lines(self.db)))

    def test_real_no_improves_once_then_passes_and_clears_old_review(self):
        aid = self.approved(); llm, calls = self.model()
        result = council.review_approved(aid, DRAFT, 'Resume', {'llm': llm}, self.db)
        self.assertEqual(result['verdict'], 'PASS')
        self.assertEqual(calls, {'judge': 2, 'fixer': 1})
        self.assertIn('Extra instructions', guardrails.get_approval(aid, self.db)['payload'])
        self.assertEqual(daily_report.product_path(self.db)['council_improved'], 1)

    def test_recheck_outage_keeps_one_candidate_across_restart(self):
        aid = self.approved(); llm, calls = self.model(outage_recheck=True)
        self.assertEqual(council.review_approved(aid, DRAFT, 'Resume', {'llm': llm}, self.db)['status'], 'waiting_ai')
        self.assertEqual(guardrails.get_approval(aid, self.db)['payload'], DRAFT)
        def recovered(prompt, **kwargs):
            if 'Fixer agent' in prompt: self.fail('Must not improve twice')
            if 'Critic agent' in prompt: return '{"weaknesses": [], "verdict": "OK"}'
            if "Buyer's Voice agent" in prompt: return '{"conviction": 8}'
            return '{"verdict": "PASS"}'
        result = council.review_approved(aid, DRAFT, 'Resume', {'llm': recovered}, self.db)
        self.assertEqual(result['verdict'], 'PASS')
        self.assertEqual(calls['fixer'], 1)

    def test_still_no_rejects_with_visible_reason(self):
        aid = self.approved(); llm, calls = self.model(pass_recheck=False)
        with patch.object(pipeline, '_allowed', return_value=True), \
                patch('product_repair.run_daily'), patch.object(pipeline, 'notify_ready'):
            result = pipeline.run_pending(self.db, str(self.root / 'products'), llm=llm)
        self.assertEqual(result[0]['status'], 'council_rejected')
        self.assertEqual(calls['fixer'], 1)
        approval = guardrails.get_approval(aid, self.db)
        self.assertEqual(approval['status'], 'rejected')
        with guardrails.connect(self.db) as conn:
            reason = conn.execute('SELECT reason FROM approvals WHERE id=?', (aid,)).fetchone()[0]
        self.assertIn('Buyer instructions', reason)
        self.assertEqual(daily_report.product_path(self.db)['council_rejected'], 1)
        self.assertEqual(pipeline.pending(self.db), [])

    def test_owner_edit_during_recheck_is_never_replaced_or_rejected(self):
        for passes in (True,False):
            aid = self.approved(); model, calls = self.model(pass_recheck=passes)
            owner_text = DRAFT+'\nOwner changed the instructions.'
            def racing(prompt,**kwargs):
                answer = model(prompt,**kwargs)
                if calls['judge']==2:
                    with guardrails.connect(self.db) as conn:
                        conn.execute('UPDATE approvals SET payload=? WHERE id=?',(owner_text,aid))
                return answer
            result = council.review_approved(aid,DRAFT,'Resume',{'llm':racing},self.db)
            self.assertEqual(result['status'],'waiting_ai')
            saved = guardrails.get_approval(aid,self.db)
            self.assertEqual(saved['status'],'approved')
            self.assertEqual(saved['payload'],owner_text)
