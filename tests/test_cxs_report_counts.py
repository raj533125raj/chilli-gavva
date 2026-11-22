"""Database states, daily report and routed /sell share the same ready inventory."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock
try:
    from PIL import Image
except ImportError:
    Image=None
import checker
import daily_report
import guardrails
import product_builder as pb
import product_counts
import product_pipeline
import telegram_inbox
import tg_sell
from tools import sell_proof


@unittest.skipUnless(Image is not None,'needs Pillow')
class ReportCountTests(unittest.TestCase):
    def test_database_and_owner_views_agree_and_ignore_orphan_stale_and_missing_packs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=str(root/'queue.db');products=root/'products'
            aid=guardrails.request_approval('public_draft','Draft: [ADHD Planners Agent] Focus',
                                          'Printable planner',checker.format_review(90,[],'pass'),db)
            with guardrails.connect(db) as conn:conn.execute("UPDATE approvals SET status='approved'")
            folder=pb.build_product(sell_proof.spec_for('planner'),aid,str(products))
            product_pipeline.prepare_upload(folder,str(products))
            pb._record(db,aid,'built',folder)
            pb._record(db,99,'built','99-missing')
            now=dt.datetime.now(dt.timezone.utc)
            counts=product_counts.snapshot(db,products,now=now)
            self.assertEqual((counts['built'],counts['upload_ready']),(2,1))
            report=daily_report.daily_text(db,now=now,status_fn=lambda:{},products_dir=products)
            self.assertIn('Products built: 2',report);self.assertIn('Upload packs ready: 1',report)
            sent=[]
            ctx={'db_path':db,'products_dir':str(products),'now':now,
                 'send_text':lambda text,keyboard=None:sent.append(text) or True}
            with mock.patch.object(telegram_inbox,'build_plugin_ctx',return_value=ctx):
                handled,result=telegram_inbox.call_plugins('/sell',token='test',chat_id=int('1234' '5678'),db_path=db,plugins=[tg_sell])
            self.assertTrue(handled)
            text='\n'.join(sent)+str(result)
            self.assertIn('Products built: 2',text);self.assertIn('Upload packs ready: 1',text)
            pb._record(db,aid,'needs_fix',folder,'Owner review required')
            self.assertEqual(product_counts.snapshot(db,products)['upload_ready'],0)
            self.assertIn('Upload packs ready: 0',daily_report.daily_text(db,status_fn=lambda:{},products_dir=products))
