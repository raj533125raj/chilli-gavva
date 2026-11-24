import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
try:
    from PIL import Image
except ImportError:
    Image=None
import guardrails
import selltest
import store_front_kit as kit
import telegram_inbox
import tg_storekit

@unittest.skipUnless(Image,'needs Pillow')
class StoreKitTests(unittest.TestCase):
    def test_actual_loader_sends_copy_and_valid_house_art_with_saved_pack_order(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'queue.db');chat=int('1234' '5678')
            with selltest._connect(db) as conn:
                conn.execute("INSERT INTO sell_test VALUES(1,'2026-11-24')")
                for slot in range(1,9):
                    conn.execute('INSERT INTO sell_test_packs VALUES(?,?,?,?,?,?)',(slot,slot,'Budget Sheet '+str(slot),str(slot)+'-budget','sheets',90))
            got={}
            def document(path,caption=''):
                got[Path(path).name]=Path(path).read_bytes()
                if path.endswith('.png'):
                    with Image.open(path) as image:image.load();self.assertGreater(image.width,500)
                return True
            ctx={'chat_id':str(chat),'db_path':db,'now':dt.datetime(2026,11,24,12,tzinfo=dt.timezone.utc),'send_document':document}
            with mock.patch.dict(os.environ,{'TELEGRAM_CHAT_ID':str(chat)}),mock.patch.object(telegram_inbox,'build_plugin_ctx',return_value=ctx):
                handled,result=telegram_inbox.call_plugins('/storekit','fake',chat,db_path=db)
            self.assertTrue(handled)
            self.assertEqual(set(got),{'store-kit.txt','profile.png','store-cover.png'})
            text=got['store-kit.txt'].decode()
            for value in [*kit.NAMES,kit.BIO,kit.ABOUT,kit.REFUND,kit.FAQ,'8/8','Gumroad settings lo ekkada em pettali']:
                self.assertIn(value,text)
            self.assertLess(text.index('Pack #1:'),text.index('Pack #8:'))
            self.assertNotRegex(text,r'[$₹€£]\s*\d')
            self.assertEqual(selltest.active(db)['packs'][0]['id'],1)

    def test_empty_inventory_and_failed_delivery_are_honest(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'queue.db');guardrails.connect(db).close()
            self.assertIn('0/8',kit.copy(db,Path(temp)/'products'))
            ctx={'db_path':db,'send_document':lambda *a,**k:False}
            with mock.patch.dict(os.environ,{'TELEGRAM_CHAT_ID':''}):
                self.assertIn('complete kaaledu',tg_storekit.handle('/storekit',ctx)['text'])

    def test_stranger_cannot_generate_store_kit(self):
        with mock.patch.dict(os.environ,{'TELEGRAM_CHAT_ID':'owner'}):
            self.assertIsNone(tg_storekit.handle('/storekit',{'chat_id':'stranger'}))
