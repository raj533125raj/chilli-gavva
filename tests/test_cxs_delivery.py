"""Owner approvals drain in one cycle; upload retries preserve real buyer files."""
import datetime as dt
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

try:
    from PIL import Image
except ImportError:
    Image = None

import guardrails
import checker
import product_builder as pb
import product_council
import product_pipeline as pipeline
from tools import approval_probe, robot_sim_world, sell_proof


class RegisteredDraftTests(unittest.TestCase):
    def test_previously_approved_automatic_backlog_is_not_capped_again(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'queue.db')
            for i in range(4):
                aid=guardrails.request_approval('public_draft','Draft: [ADHD Planners Agent] Daily Planner',
                                              'Printable planner PDF',checker.format_review(90,[], 'pass'),db)
                with guardrails.connect(db) as conn:
                    conn.execute("UPDATE approvals SET status='approved',reason='auto-approved' WHERE id=?",(aid,))
                product_council.log_debate(aid,1,'council_summary','PASS','Reviewed',db_path=db)
            with mock.patch.object(pipeline,'_allowed',return_value=True), mock.patch.dict(os.environ,{'DB_KEY':''}), \
                    mock.patch.object(pipeline,'_marketing_lines',return_value={}), \
                    mock.patch('launch_pack.build_launch_pack',side_effect=OSError):
                for _ in range(2):
                    pipeline.run_pending(db,str(Path(temp)/'products'),lambda *a,**k:'',max_builds=2)
            with pipeline.connect(db) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM product_delivery_attempts').fetchone()[0],4)

    def test_each_registered_format_resolves_and_parses_its_own_spec(self):
        for kind in sell_proof.FORMAT_ORDER:
            with self.subTest(kind=kind):
                niche=pb.niche_for_draft('Draft: format: '+kind)
                self.assertIsNotNone(niche)
                self.assertIn('Product type: '+kind, pb.spec_prompt(niche,'Approved private draft'))
                raw=sell_proof.builtin_specs()[kind][0] if kind in sell_proof.builtin_specs() else sell_proof.spec_for(kind)
                parsed,reason=pb.parse_spec(json.dumps(raw),niche)
                self.assertIsNone(reason)
                self.assertEqual(parsed['kind'],kind)


@unittest.skipUnless(Image is not None,'needs Pillow')
class OwnerDeliveryTests(unittest.TestCase):
    def test_four_approved_automatic_backlog_builds_in_one_cycle_and_upload_retry_is_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=str(root/'queue.db');out=root/'products'
            guardrails.connect(db).close()
            now=dt.datetime(2026,11,22,12,5,tzinfo=dt.timezone.utc)
            approval_probe.seed_queue(Path(db),now,live=True)
            ids=list(range(9012,9016))
            with guardrails.connect(db) as conn:
                conn.execute("UPDATE approvals SET reason='auto-approved' WHERE status='approved'")
            for aid in ids:
                product_council.log_debate(aid,1,'council_summary','PASS','Reviewed draft',db_path=db)
            with mock.patch.object(pipeline,'_allowed',return_value=True), mock.patch.dict(os.environ,{'DB_KEY':''}), \
                    mock.patch.object(pipeline,'_marketing_lines',return_value={}):
                result=pipeline.run_pending(db,str(out),lambda prompt,size=None: robot_sim_world.fake_llm_reply(prompt)[1],
                                            max_builds=2,notify_fn=lambda text:True)
            self.assertEqual({row['id'] for row in result if row['status']=='prepared'},set(ids),result)
            for build in pb.builds(db):
                pack=out/'upload'/build['folder']
                self.assertTrue((pack/'listing.txt').is_file())
                with zipfile.ZipFile(pack/'product.zip') as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertTrue(archive.namelist())
            first=pb.builds(db)[0]['folder']
            pack=out/'upload'/first
            before=(pack/'product.zip').read_bytes()
            pipeline.prepare_upload(first,str(out))
            with zipfile.ZipFile(pack/'product.zip') as archive:self.assertIsNone(archive.testzip())
            self.assertEqual((pack/'product.zip').read_bytes(),before)
