"""Natural titles, registered builder feasibility and preferred niches before draft creation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import guardrails
import scout


class ScoutDraftTests(unittest.TestCase):
    def test_singular_audience_suffix_is_replaced_and_never_duplicated(self):
        self.assertEqual(scout.natural_title('Festival Planner for exam taker'),'Festival Planner for Students')
        self.assertEqual(scout.natural_title('Telugu Cultural Event and Festival Planner for exam taker'),
                         'Telugu Festival Planner for Students')
        self.assertEqual(scout.natural_title('Festival Planner for Students','teachers',True),'Festival Planner for Students')
        item=scout.score_candidate({'idea':'Festival Planner for exam taker','audience':'exam takers'})
        self.assertEqual(item['idea'],'Festival Planner for Students')

    def test_app_physical_and_course_ideas_never_enter_the_database(self):
        bad=('Budget Planner App','Physical Product: Printed Planner Shipped to Buyers','Resume Video Course','Subscription Audio Course','Physical Planner')
        good=('Budget Sheet for Students','Small Business Log for Home Bakers','ADHD Focus Planner',
              'Notion Workspace Template','Resume Bundle for Job Seekers','Teacher Planner','Fitness Planner',
              'Baby Tracker','Kids Chore Chart and Reward Pack')
        for title in bad:self.assertFalse(scout.is_buildable_idea(title,'printable planner'))
        for title in good:self.assertTrue(scout.is_buildable_idea(title),title)
        reply=json.dumps([{'category':'Templates','idea':title,'demand_score':24,'competition_score':24,
                           'fit_score':24,'effort_score':24,'audience':'students','format':'printable planner'}
                          for title in bad]+[{'category':'Planners','idea':'Festival Planner for exam taker',
                                              'audience':'exam takers','format':'printable planner'}])
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'queue.db')
            rows=scout.run_scout(db_path=db,llm=lambda *a,**k:reply)
            self.assertEqual([r['idea'] for r in rows],['Festival Planner for Students'])
            pending=guardrails.pending(db)
            self.assertEqual(len(pending),1)
            self.assertNotIn('exam taker',pending[0]['title'])

    def test_real_fallback_queue_uses_new_registered_types(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch('scout.guardrails.can_call_llm',return_value=False):
            rows=scout.run_scout(db_path=str(Path(temp)/'queue.db'))
            self.assertEqual(len(rows),5)
            self.assertTrue({'teacher_planner','fitness_planner','baby_tracker','small_business_log','kids_chore_reward'}
                            & {row['format'] for row in rows},rows)

    def test_unknown_delivery_is_not_buildable_and_preferred_format_wins_equal_scores(self):
        self.assertFalse(scout.is_buildable_idea('Custom Handcrafted Object'))
        self.assertGreater(scout.candidate_priority({'idea':'Budget Sheet','total_score':80}),
                           scout.candidate_priority({'idea':'Wedding Event Planner','total_score':80}))
        registered={'teacher_planner','fitness_planner','baby_tracker','small_business_log','kids_chore_reward'}
        self.assertTrue(registered.issubset(scout.get_supported_formats()))
        self.assertTrue(registered.issubset({r.get('format') for r in scout.EXTENDED_FALLBACK_CANDIDATES}))
