"""A real buyer kit through /upload, with lossless dimensions and gated copy."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock
try:
    from PIL import Image
except ImportError:
    Image=None
import gumroad_handoff
import listing_fields
import product_builder as pb
import product_pipeline
import telegram_inbox
import tg_upload
from tools import sell_proof


@unittest.skipUnless(Image is not None,'needs Pillow')
class HandoffTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.products=self.root/'products'
        name=pb.build_product(sell_proof.spec_for('planner'),27,str(self.products))
        self.pack=product_pipeline.prepare_upload(name,str(self.products))
        self.sent=[];self.text=[]
        self.ctx={'products_dir':str(self.products),'db_path':None,'chat_id':int('1234' '5678'),
                  'send_document':self.record,'send_text':lambda text,keyboard=None:self.text.append(text) or True}

    def record(self,path,caption=''):
        p=Path(path);self.sent.append((p.name,p.read_bytes()));return True

    def call(self):
        with mock.patch.object(telegram_inbox,'build_plugin_ctx',return_value=self.ctx), \
                mock.patch.dict('os.environ',{'TELEGRAM_CHAT_ID':str(self.ctx['chat_id'])}):
            return telegram_inbox.call_plugins('/upload 27',token='test',chat_id=self.ctx['chat_id'],plugins=[tg_upload])

    def test_one_folder_exact_dimensions_and_identical_gated_listing_sent_through_loader(self):
        folder=self.pack/gumroad_handoff.FOLDER
        self.assertEqual({p.name for p in folder.iterdir()},set(gumroad_handoff.FILES))
        before=listing_fields.parse_fields((self.pack/'listing.txt').read_text())
        after=listing_fields.parse_fields((folder/'listing.txt').read_text())
        for field in ('title','description','tags'):self.assertEqual(before[field],after[field])
        for name,size in gumroad_handoff.SIZES.items():
            with Image.open(folder/name) as image:self.assertEqual(image.size,size)
        handled,result=self.call();self.assertTrue(handled)
        self.assertEqual([name for name,_ in self.sent],list(gumroad_handoff.FILES))
        self.assertEqual(self.sent[0][1],(self.pack/'product.zip').read_bytes())
        self.assertIn('Publish meere','\n'.join(self.text))

    def test_oversized_file_is_explained_before_any_transfer(self):
        with mock.patch.object(gumroad_handoff,'MAX_FILE_BYTES',1):self.call()
        self.assertEqual(self.sent,[])
        self.assertIn('Telegram file limit','\n'.join(self.text))

    def test_bad_listing_corrupt_archive_and_wrong_image_dimensions_refuse_the_kit(self):
        (self.pack/'listing.txt').write_text('Title: Pack\nDescription: A file.\n')
        with self.assertRaises(ValueError):gumroad_handoff.build(self.pack)
        self.assertEqual(self.call()[0],True);self.assertEqual(self.sent,[])

    def test_existing_ready_pack_gets_a_missing_kit_backfilled(self):
        import shutil
        shutil.rmtree(self.pack/gumroad_handoff.FOLDER)
        result=gumroad_handoff.build_missing(self.products)
        self.assertTrue((self.pack/gumroad_handoff.FOLDER/'product.zip').is_file(),result)

    def test_symlink_image_is_refused(self):
        cover=self.pack/'cover.png';cover.unlink();cover.symlink_to(self.pack/'thumbnail.png')
        with self.assertRaises(ValueError):gumroad_handoff.build(self.pack)

    def test_corrupt_zip_and_wrong_size_are_refused(self):
        data=(self.pack/'product.zip').read_bytes();(self.pack/'product.zip').write_bytes(b'broken')
        with self.assertRaises((ValueError,OSError)):gumroad_handoff.build(self.pack)
        (self.pack/'product.zip').write_bytes(data)
        Image.new('RGB',(100,100),'white').save(self.pack/'thumbnail.png')
        with self.assertRaises(ValueError):gumroad_handoff.build(self.pack)
