import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import zipfile
try:
    from PIL import Image
except ImportError:
    Image=None
import buyer_check as buyer
import format_registry
import guardrails
import product_builder
import selltest
import selltest_listings
import telegram_inbox
import upload_pack
from tools import sell_proof

@unittest.skipUnless(Image,'needs Pillow')
class AllFormatBuyerTests(unittest.TestCase):
    def test_nineteen_actual_formats_private_fix_and_strict_evidence_rules(self):
        self.assertEqual(set(buyer.TITLES),set(format_registry.list_registered_formats()))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);products=root/'products';db=str(root/'queue.db')
            guardrails.connect(db).close()
            for aid,kind in enumerate(sell_proof.FORMAT_ORDER,1):
                with self.subTest(kind=kind):
                    name=product_builder.build_product(sell_proof.spec_for(kind),aid,str(products))
                    pack=upload_pack.make(products/name,products/'upload')
                    original=(pack/'product.zip').read_bytes();old=(pack/'listing.txt').read_text()
                    row=buyer.prepare(pack,kind,aid,db,products)
                    self.assertEqual(row['after']['score'],83,row)
                    self.assertEqual(row['after']['top_fixes'],['Free sample lo current paid Pack link pettandi: /listed N <link>; /upload N sample.'])
                    self.assertTrue(all(ok for key,ok in row['after']['checks'].items() if key!='sample'))
                    fields=__import__('listing_fields').parse_fields(Path(row['listing']).read_text())
                    self.assertEqual(len(fields['title'].split()),6)
                    import listing_lint
                    self.assertEqual([name for name,level,_ in listing_lint.quality_checks(Path(row['listing']).read_text(),pack,fields['main_keyword']) if level=='FAIL'],[])
                    with zipfile.ZipFile(pack/'product.zip') as archive:
                        self.assertEqual(sorted(line.removeprefix('- ') for line in fields['what_you_get'].splitlines()),sorted(archive.namelist()))
                    self.assertEqual((pack/'product.zip').read_bytes(),original)
                    self.assertEqual((pack/'listing.txt').read_text(),old)
                    with Image.open(row['image']) as image:self.assertEqual(image.size,(1280,720))
                    text=Path(row['listing']).read_text().replace(fields['what_you_get'],'- imaginary.pdf')
                    report=buyer.evaluate(pack,kind,text,pack/'cover.png',Path(row['image']).read_bytes())
                    self.assertFalse(report['checks']['files']);self.assertFalse(report['checks']['inside'])
                    self.assertLessEqual(len(report['top_fixes']),3)

    def test_freelancer_invoice_and_student_workspace_keep_the_real_buyer_and_topic(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);products=root/'products';db=str(root/'queue.db');guardrails.connect(db).close()
            for aid,kind,name,audience in [(1,'sheets','Invoice Tracker','freelancers'),(2,'notion','Student Workspace','students')]:
                spec=sell_proof.spec_for(kind);spec['name']=name;spec['listing']['buyer']=audience
                folder=product_builder.build_product(spec,aid,str(products))
                pack=upload_pack.make(products/folder,products/'upload')
                row=buyer.prepare(pack,kind,aid,db,products)
                title=__import__('listing_fields').parse_fields(Path(row['listing']).read_text())['title']
                self.assertIn('Invoice' if kind=='sheets' else 'Study',title)
                self.assertTrue(title.startswith(audience.title()))
                self.assertTrue(row['after']['checks']['title'])

    def test_current_sample_link_and_real_loader(self):
        # Build one genuine paid source and sample with an owner-saved paid link.
        import free_sample
        import tg_listed
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);products=root/'products';db=str(root/'queue.db');chat=int('1234' '5678')
            aid=guardrails.request_approval('public_draft','Draft: [ADHD Planners Agent] Focus Planner','Printable planner',db_path=db)
            guardrails.decide(aid,True,db)
            name=product_builder.build_product(sell_proof.spec_for('planner'),aid,str(products))
            pack=upload_pack.make(products/name,products/'upload')
            with selltest._connect(db) as conn:
                conn.execute("INSERT INTO sell_test VALUES(1,'2026-11-24')")
                conn.execute('INSERT INTO sell_test_packs VALUES(1,?,?,?,?,?)',(aid,'Focus Planner',name,'planner',90))
            with tg_listed._connect(db) as conn:
                conn.execute("INSERT INTO listed_packs VALUES(?,?,'2026-11-24')",(aid,'https://example.gumroad.com/l/focus'))
            free_sample.ensure_all(products,db)
            row=buyer.prepare(pack,'planner',aid,db,products)
            self.assertEqual(row['after']['score'],100,row)
            sent=[];ctx={'db_path':db,'products_dir':products,'chat_id':str(chat),'send_document':lambda p,caption='':sent.append(Path(p).name) or True}
            with mock.patch.dict(os.environ,{'TELEGRAM_CHAT_ID':str(chat)}),mock.patch.object(telegram_inbox,'build_plugin_ctx',return_value=ctx):
                handled,result=telegram_inbox.call_plugins('/buyercheck','fake',chat,db_path=db)
            self.assertTrue(handled);self.assertIn('100/100',result['text'])
            self.assertEqual(sent,['listing.txt','gallery-first.png'])
            sample=products/'samples'/name
            (sample/'README.txt').write_text('Wrong paid link')
            self.assertFalse(buyer.prepare(pack,'planner',aid,db,products)['after']['checks']['sample'])
