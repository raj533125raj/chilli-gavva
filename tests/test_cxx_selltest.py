"""Owner-only experiment selection and durable membership."""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import guardrails
import selltest
import tg_selltest

NOW = dt.datetime(2026,11,23,7,tzinfo=dt.timezone.utc)
CHAT = str(int('1234' '5678'))


def choices(n=10):
    return [{'id':i,'title':'Focus Planner Pack '+str(i),'folder':str(i)+'-focus','format':'planner',
             'listing_score':100,'ready_score':95} for i in range(1,n+1)]


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.db = str(self.root/'robot.db')
        self.ctx = {'db_path':self.db,'chat_id':CHAT,'now':NOW,'products_dir':str(self.root/'products')}
        self.enterContext(mock.patch.dict(os.environ,{'TELEGRAM_CHAT_ID':CHAT}))
        self.enterContext(mock.patch.object(selltest,'candidates',return_value=choices()))

    def test_eight_selected_and_idempotent_start_keeps_date(self):
        reply = tg_selltest.handle('/selltest',self.ctx)['text']
        self.assertLessEqual(len(reply.splitlines()),15)
        self.assertIn('Next:',reply); self.assertIn('/upload 8',reply)
        self.assertEqual(len(selltest.active(self.db)['packs']),8)
        future = dict(self.ctx, now=NOW+dt.timedelta(days=3))
        self.assertIn('start 2026-11-23',tg_selltest.handle('/selltest',future)['text'])
        self.assertIn('day 4/14',tg_selltest.handle('/selltest list',future)['text'])

    def test_swap_is_durable_and_requires_ready_replacement(self):
        selltest.start(self.db,now=NOW)
        tg_selltest.handle('/selltest swap 1 9',self.ctx)
        self.assertEqual(selltest.active(self.db)['packs'][0]['id'],9)
        self.assertIn('READY kaadu',tg_selltest.handle('/selltest swap 2 90',self.ctx)['text'])
        self.assertIn('vere READY',tg_selltest.handle('/selltest swap 2 9',self.ctx)['text'])

    def test_insufficient_packs_do_not_start_and_wrong_owner_cannot_write(self):
        with mock.patch.object(selltest,'candidates',return_value=choices(4)):
            self.assertIn('Kanisam 5',tg_selltest.handle('/selltest',self.ctx)['text'])
        self.assertIsNone(selltest.active(self.db))
        self.assertIsNone(tg_selltest.handle('/selltest',dict(self.ctx,chat_id='other')))
        self.assertIsNone(tg_selltest.handle('/stats',self.ctx))

    def test_real_loader_reaches_command_with_fake_senders(self):
        import telegram_inbox
        with mock.patch.object(telegram_inbox,'send_text',return_value=True), mock.patch.object(telegram_inbox,'build_plugin_ctx',return_value=self.ctx):
            handled, reply = telegram_inbox.call_plugins('/selltest','unused',CHAT,db_path=self.db,now=NOW)
        self.assertTrue(handled); self.assertIn('/upload 1',reply['text'])


class CandidateTests(unittest.TestCase):
    def test_listing_score_then_niche_preference_and_failed_gate_excluded(self):
        rows = [(p['id'],p['title'],Path('/private/tmp')/p['folder'],{'score':p['ready_score']}) for p in choices(4)]
        scores = {1:100,2:100,3:80,4:100}
        with mock.patch('product_counts.snapshot',return_value={'packs':rows}), \
             mock.patch('listing_lint.quality_report',side_effect=lambda p: {'ok':int(p.name.split('-')[0])!=4,'score':scores[int(p.name.split('-')[0])]}), \
             mock.patch('store_kits.parse_listing_file',return_value={}), \
             mock.patch('store_kits.gumroad_product_type',side_effect=['wall_art','biz_sheets','sheets']):
            result = selltest.candidates('/private/tmp/unused.db','/private/tmp/unused-products',NOW)
        self.assertEqual([p['id'] for p in result],[2,1,3])


class RealCandidateTests(unittest.TestCase):
    def test_five_real_built_packs_start_and_failed_database_status_is_excluded(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow required')
        import product_builder as pb
        from tools import sell_proof
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); db=str(root/'robot.db'); products=root/'products'
            for index in range(5):
                aid=guardrails.request_approval('public_draft','Draft: Focus Planner '+str(index),'PDF planner',db_path=db)
                guardrails.decide(aid,True,db_path=db)
                spec=sell_proof.spec_for('planner');spec['short_name']='focus-'+str(index)
                folder=pb.build_product(spec,aid,out_dir=str(products))
                pb._record(db,aid,'built',folder)
                result=pb.truth_check_folder(aid,folder,db_path=db,out_dir=str(products),notify_fn=lambda text:True)
                self.assertEqual(result['verdict'],'ready',result)
            run=selltest.start(db,products,NOW)
            self.assertEqual(len(run['packs']),5)
            pb._record(db,aid,'needs_fix',folder)
            self.assertNotIn(aid,[p['id'] for p in selltest.candidates(db,products,NOW)])
