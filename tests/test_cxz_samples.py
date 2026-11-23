"""Free samples contain actual first pages and pass the unchanged listing gate."""
import datetime as dt
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
try:
    from PIL import Image
except ImportError:
    Image=None
import free_sample
import sample_pdf
import selltest
import product_builder as pb
import guardrails
from tools import sell_proof
from tests.test_cxx_selltest import NOW, CHAT


@unittest.skipUnless(Image is not None,'Pillow required')
class SampleTests(unittest.TestCase):
    def test_all_nineteen_real_formats_have_gated_samples_with_only_sample_buyer_files(self):
        import listing_lint
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);products=root/'products';db=str(root/'test.db');packs=[]
            for kind in sell_proof.FORMAT_ORDER:
                aid=guardrails.request_approval('public_draft','Draft: '+kind,'Product',db_path=db)
                guardrails.decide(aid,True,db_path=db)
                folder=pb.build_product(sell_proof.spec_for(kind),aid,str(products))
                pb._record(db,aid,'built',folder)
                truth=pb.truth_check_folder(aid,folder,db,str(products),lambda text:True)
                self.assertEqual(truth['verdict'],'ready',truth)
                packs.append({'id':aid,'title':sell_proof.spec_for(kind)['listing']['title'],'folder':folder,'format':kind,'listing_score':100,'ready_score':95})
            with mock.patch.object(selltest,'candidates',return_value=packs):
                selltest.start(db,products,NOW)
                for pack in packs:
                    with self.subTest(kind=pack['format']):
                        run=selltest.active(db)
                        if pack['id'] not in {p['id'] for p in run['packs']}:
                            selltest.swap(run['packs'][-1]['id'],pack['id'],db,products,NOW)
                        # Exercise the required Pillow-only path, without system PDF renderers.
                        with mock.patch.object(free_sample.shutil,'which',return_value=None):
                            folder=free_sample.build(pack['id'],products,db)
                        self.assertTrue(listing_lint.quality_report(folder)['ok'])
                        self.assertTrue(json.loads((folder/'sample.json').read_text())['free_sample'])
                        with zipfile.ZipFile(folder/'product.zip') as archive:
                            self.assertEqual(set(archive.namelist()),{'sample.pdf','README.txt','LICENSE.txt'})
                            self.assertTrue(archive.read('sample.pdf').startswith(b'%PDF'))
                        sampled=sample_pdf.pdf_pages(folder/'sample.pdf');self.assertEqual(len(sampled),4)
                        self.assertTrue(any('FULL VERSION' in line for line in sampled[3][1]))
                        pdfs=sorted((products/pack['folder']).glob('*.pdf'),key=lambda p:(not p.name.endswith('-A4.pdf'),p.name))
                        if pdfs:self.assertEqual(sampled[:3],sample_pdf.pdf_pages(pdfs[0])[:3])
                        self.assertIsNone(__import__('sell_ready').check_money_text((folder/'listing.txt').read_text()))
                        self.assertEqual(free_sample.build(pack['id'],products,db),folder)

    def test_loader_delivers_sample_and_sample_link_is_separate_from_paid_link(self):
        import tg_upload
        import tg_listed
        import telegram_inbox
        with tempfile.TemporaryDirectory() as temp:
            products=Path(temp)/'products';db=str(Path(temp)/'test.db')
            aid=guardrails.request_approval('public_draft','Draft: Focus Planner','PDF',db_path=db);guardrails.decide(aid,True,db_path=db)
            name=pb.build_product(sell_proof.spec_for('planner'),aid,str(products));pb._record(db,aid,'built',name)
            pb.truth_check_folder(aid,name,db,str(products),lambda text:True)
            with selltest._connect(db) as conn:
                conn.execute('INSERT INTO sell_test VALUES(1,?)',('2026-11-23',))
                conn.execute('INSERT INTO sell_test_packs VALUES(1,?,?,?,?,?)',(aid,'Focus Planner',name,'planner',100))
            tg_listed.set_listed(aid,'https://example.gumroad.com/l/full-pack',db,NOW)
            sent=[];texts=[]
            ctx={'db_path':db,'products_dir':str(products),'chat_id':CHAT,'now':NOW,
                 'send_document':lambda p,caption='':sent.append((Path(p).name,Path(p).read_bytes())),
                 'send_text':lambda t,**k:texts.append(t)}
            with mock.patch.object(telegram_inbox,'build_plugin_ctx',return_value=ctx),mock.patch.dict('os.environ',{'TELEGRAM_CHAT_ID':CHAT}):
                handled,_=telegram_inbox.call_plugins(f'/upload {aid} sample','unused',CHAT,db_path=db,now=NOW,plugins=[tg_upload])
            self.assertTrue(handled);self.assertEqual([n for n,data in sent],list(free_sample.FILES)+['emails.txt'])
            free_sample.set_link(aid,'https://example.gumroad.com/l/free-sample',db)
            self.assertEqual(tg_listed.get_listed(aid,db)['url'],'https://example.gumroad.com/l/full-pack')
            import selltest_posts
            plan=selltest_posts.build(products,selltest.active(db),[],db,NOW)
            self.assertTrue(all(p.get('link')=='https://example.gumroad.com/l/free-sample'+('?utm_source=pinterest&utm_campaign=selltest' if p['channel']=='pinterest' else '') for p in plan))
            self.assertTrue(all(p.get('title','').startswith('Free sample:') for p in plan))
            with self.assertRaises(ValueError):free_sample.set_link(aid,'https://example.com/bad',db)

    def test_fourth_full_page_is_not_leaked(self):
        import pdf_design
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';source.mkdir()
            pdf=pdf_design.Pdf('A4')
            for i in range(5):
                pdf.new_page();pdf.text(50,700,'PaidOnlyFourthPage' if i==3 else 'Real page '+str(i),18)
            pdf.save(source/'source.pdf')
            sample_pdf.make(source,root/'sample.pdf','Complete Planner')
            self.assertNotIn(b'PaidOnlyFourthPage',(root/'sample.pdf').read_bytes())
