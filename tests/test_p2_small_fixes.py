"""P2 regressions use only synthetic input and isolated temporary databases/files."""
import os
import tempfile
import unittest
from unittest import mock

import checker
import guardrails
import scout
import telegram_inbox


class CheckerScoreBoundsTests(unittest.TestCase):
    def test_legacy_and_new_review_scores_are_bounded_in_telegram(self):
        with tempfile.TemporaryDirectory() as folder:
            db = os.path.join(folder, 'scores.db')
            for raw, expected in ((102,100),(101,100),(100,100),(82,82),(0,0),(-1,0),(-99,0),(1000,100)):
                with self.subTest(raw=raw):
                    review = f'Checker: {raw}/100 | CHECKER: PASS | top: Clear audience'
                    self.assertEqual(checker.parse_review(review), (expected,['Clear audience']))
                    self.assertIn(f'Checker: {expected}/100', telegram_inbox.draft_card(
                        {'id':1,'title':'Synthetic draft','review':review}, db_path=db))
                    formatted = checker.format_review(raw, ['Clear audience'])
                    self.assertIn(f'Checker: {expected}/100',formatted)
                    if raw != expected:
                        self.assertNotIn(f'AI {raw}', formatted)
            self.assertEqual(checker.parse_review('no score'), (None,[]))

    def test_display_clamping_does_not_make_invalid_reviews_eligible_for_approval(self):
        for score in (-99,-1,101,102,1000):
            self.assertFalse(checker.review_passes(f'Checker: {score}/100 | CHECKER: PASS'))
        self.assertTrue(checker.review_passes('score 100/100 | Checker: 82/100 | CHECKER: PASS'))
        self.assertEqual(checker.parse_review('score 100/100 | Checker: 67/100')[0],67)

    def test_scout_clamps_after_ranking_and_persists_only_bounded_scores(self):
        # Both top candidates exceed the display limit, and the stronger one arrives second.
        candidates = [dict(category='Templates',idea='Synthetic weaker',niche='niche_budget_sheets',
                           demand_score=25,competition_score=25,fit_score=25,effort_score=25,total_score=100,reason='Synthetic'),
                      dict(category='Templates',idea='Synthetic stronger',niche='niche_budget_sheets',
                           demand_score=25,competition_score=25,fit_score=25,effort_score=25,total_score=100,reason='Synthetic')]
        candidates[0]['effort_score'] = 24
        with tempfile.TemporaryDirectory() as folder:
            db = os.path.join(folder, 'rank.db')
            with mock.patch('scout.get_candidate_ideas',return_value=candidates), \
                 mock.patch('scout.get_niche_track_record',return_value={'niche_budget_sheets':6}), \
                 mock.patch('scout.get_recent_approved_or_rejected_ideas',return_value=[]), \
                 mock.patch('scout.public_export.get_public_reject_reasons',return_value=[]):
                ideas = scout.run_scout(db_path=db, llm=lambda *_a,**_k: None)
            self.assertEqual([item['idea'] for item in ideas],['Synthetic stronger','Synthetic weaker'])
            self.assertEqual([item['total_score'] for item in ideas],[100,100])
            conn = scout.connect(db)
            scores = conn.execute('SELECT total_score FROM scout_ideas ORDER BY id').fetchall()
            conn.close()
            self.assertEqual(scores,[(100,),(100,)])
            approvals = guardrails.pending(db_path=db)
            for approval in approvals:
                self.assertIn('/100',approval['review'])
                self.assertEqual(checker.parse_review(approval['review'])[0],100)
                self.assertIn('Total: 100/100',guardrails.get_approval(approval['id'],db)['payload'])
