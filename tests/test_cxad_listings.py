"""A/B copy comes from every real format and reaches the owner through plugins."""
import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile
try:
    from PIL import Image
except ImportError:
    Image=None
import selltest
import selltest_listings as listings
from tests.test_cxx_selltest import NOW,CHAT


@unittest.skipUnless(Image is not None,'Pillow required')
class EveryFormatCopyTests(unittest.TestCase):
    def test_nineteen_real_buyer_archives_have_two_keyword_titles_and_exact_file_lists(self):
        import product_builder,upload_pack,format_registry
        from tools import sell_proof
        self.assertEqual(set(listings.PROFILES),set(format_registry.list_registered_formats()))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for i,kind in enumerate(sell_proof.FORMAT_ORDER,1):
                with self.subTest(kind=kind):
                    name=product_builder.build_product(sell_proof.spec_for(kind),i,str(root/'products'))
                    pack=upload_pack.make(root/'products'/name,root/'upload')
                    copy=listings.make(pack,kind)
                    self.assertNotEqual(copy['A'],copy['B'])
                    for title in (copy['A'],copy['B']):
                        self.assertTrue(title.startswith(copy['keyword']))
                        self.assertTrue(40<=len(title)<=80)
                        self.assertNotRegex(title,r'(?i)chilli|gavva|gumroad|notion|excel|canva|goodnotes')
                    self.assertTrue(copy['description'].startswith(listings.PROFILES[kind][3]))
                    with zipfile.ZipFile(pack/'product.zip') as archive:
                        self.assertEqual(copy['files'],sorted(n for n in archive.namelist() if not n.endswith('/')))
                    self.assertTrue(3<=len(copy['tags'])<=10)
                    import listing_lint
                    for variant in ('A','B'):
                        self.assertEqual([name for name,level,_ in listing_lint.quality_checks(listings.render(copy,variant),pack,copy['keyword']) if level=='FAIL'],[])
                    self.assertIsNone(__import__('sell_ready').check_money_text(listings.render(copy)))


@unittest.skipUnless(Image is not None,'Pillow required')
class OwnerCopyFlowTests(unittest.TestCase):
    def setUp(self):
        from tests.test_cxt_handoff import HandoffTests
        self.fixture=HandoffTests('test_one_folder_exact_dimensions_and_identical_gated_listing_sent_through_loader')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root;self.products=self.fixture.products
        self.pack=self.fixture.pack;self.db=str(self.root/'owner.db')
        with selltest._connect(self.db) as conn:
            conn.execute('INSERT INTO sell_test VALUES(1,?)',(NOW.date().isoformat(),))
            conn.execute('INSERT INTO sell_test_packs VALUES(1,?,?,?,?,?)',(27,'Focus Planner',self.pack.name,'planner',100))
        self.sent=[];self.text=[]
        self.ctx=dict(self.fixture.ctx,db_path=self.db,chat_id=CHAT,now=NOW,
                      send_document=lambda p,caption='':self.sent.append((Path(p).name,Path(p).read_bytes())) or True,
                      send_text=lambda t,**k:self.text.append(t) or True)
        self.enterContext(mock.patch.dict(os.environ,{'TELEGRAM_CHAT_ID':CHAT}))

    def exchange(self,text,now=NOW):
        import telegram_inbox,tg_upload,tg_selltest
        with mock.patch.object(telegram_inbox,'build_plugin_ctx',return_value=dict(self.ctx,now=now)):
            handled,result=telegram_inbox.call_plugins(text,'unused',CHAT,db_path=self.db,now=now,plugins=[tg_upload,tg_selltest])
        self.assertTrue(handled)
        return result

    def test_upload_sends_both_titles_and_all_real_copy_in_owner_document(self):
        import gumroad_handoff
        self.exchange('/upload 27')
        self.assertEqual([n for n,_ in self.sent],list(gumroad_handoff.FILES)+[listings.FILE])
        copy=listings.make(self.pack,'planner')
        text=self.sent[-1][1].decode()
        self.assertIn('Title A: '+copy['A'],text);self.assertIn('Title B: '+copy['B'],text)
        self.assertTrue(all(name in text for name in copy['files']))
        self.assertIn(copy['B'],'\n'.join(self.text))

    def test_day_seven_uses_b_persistently_and_never_changes_buyer_zip(self):
        original=(self.pack/'product.zip').read_bytes()
        reply=self.exchange('/selltest swap-title 27')
        self.assertIn('Day 7',reply['text']);self.assertEqual(selltest.title_variant(27,self.db),'A')
        reply=self.exchange('/selltest swap-title 27',NOW+dt.timedelta(days=6))
        self.assertIn(listings.make(self.pack,'planner')['B'],reply['text'])
        self.assertEqual(selltest.title_variant(27,self.db),'B')
        self.exchange('/upload 27',NOW+dt.timedelta(days=7))
        self.assertIn('Active title: B',self.sent[-1][1].decode())
        self.assertEqual((self.pack/'product.zip').read_bytes(),original)
        with self.assertRaises(ValueError):selltest.swap_title(999,self.db,self.products,NOW+dt.timedelta(days=6))

    def test_wrong_owner_cannot_select_title(self):
        import tg_selltest
        self.assertIsNone(tg_selltest.handle('/selltest swap-title 27',dict(self.ctx,chat_id='other',now=NOW+dt.timedelta(days=6))))
        self.assertEqual(selltest.title_variant(27,self.db),'A')
