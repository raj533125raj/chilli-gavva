"""November-shaped resume backlog must not hide other approved builder niches."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import guardrails
import product_builder
import product_pipeline as pipeline


class BuildRotation(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.db = str(Path(temp.name)/'robot.db')

    def add(self, kind, ready=False, status='approved'):
        title = 'Draft: '+kind+' Pack'
        aid = guardrails.request_approval('public_draft',title,'Format: '+kind,db_path=self.db)
        with guardrails.connect(self.db) as conn:
            conn.execute('UPDATE approvals SET status=? WHERE id=?',(status,aid))
        if ready:
            product_builder._record(self.db,aid,'built',str(aid)+'-pack')
            with pipeline.connect(self.db) as conn:
                conn.execute("INSERT INTO product_deliveries(approval_id,status) VALUES(?,'ready')",(aid,))
        return aid

    def test_three_existing_resumes_then_first_eight_cover_four_or_more(self):
        for _ in range(3): self.add('resume',ready=True)
        for _ in range(15): self.add('resume')
        others = [self.add(k) for k in ('sheets','planner','notion','digital_planner','biz_sheets')]
        queue = pipeline.pending(self.db)
        self.assertEqual({p['id'] for p in queue[:5]},set(others))
        niches = {'resume'} | {pipeline.queue_niche(p['title'],p['payload']) for p in queue[:5]}
        self.assertGreaterEqual(len(niches),4)
        # After each single successful run, persisted built counts keep the rotation moving.
        chosen = []
        for _ in range(5):
            item = pipeline.pending(self.db)[0]; chosen.append(pipeline.queue_niche(item['title'],item['payload']))
            with pipeline.connect(self.db) as conn:
                conn.execute("INSERT INTO product_deliveries(approval_id,status) VALUES(?,'prepared')",(item['id'],))
        self.assertEqual(len(set(chosen)),5)

    def test_single_available_niche_stays_fifo_and_pending_or_held_never_enters(self):
        ids = [self.add('resume') for _ in range(4)]
        self.add('notion',status='pending')
        with patch.object(product_builder,'is_held',side_effect=lambda aid,db: aid==ids[1]):
            self.assertEqual([p['id'] for p in pipeline.pending(self.db)],[ids[0],ids[2],ids[3]])

    def test_full_mixed_backlog_rotates_six_top_niches_before_repeating(self):
        for kind in pipeline.BUILD_ROTATION:
            for _ in range(3): self.add('biz_sheets' if kind=='business_workbook' else kind)
        kinds = [pipeline.queue_niche(p['title'],p['payload']) for p in pipeline.pending(self.db)]
        self.assertEqual(tuple(kinds[:6]),pipeline.BUILD_ROTATION)
        self.assertGreaterEqual(len(set(kinds[:8])),4)


    def test_legacy_build_counts_without_approval_ids_do_not_break_queue_reads(self):
        with guardrails.connect(self.db) as conn:
            conn.execute('CREATE TABLE product_builds(id INTEGER PRIMARY KEY,status TEXT)')
            conn.execute("INSERT INTO product_builds VALUES(1,'built')")
        ids = [self.add('resume'),self.add('sheets')]
        self.assertEqual({p['id'] for p in pipeline.pending(self.db)},set(ids))
