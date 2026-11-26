import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import guardrails
import mockups
import product_builder as pb
import selltest
import telegram_inbox as inbox
import tg_listnext

NOW=dt.datetime(2026,11,26,7,tzinfo=dt.timezone.utc)
CHAT=str(int('1234' '5678'))

@unittest.skipUnless(mockups.available(),'Pillow and Poppler required')
class ListingDay(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.products=Path(temp.name)/'products';self.db=str(Path(temp.name)/'robot.db')
        from tools.sell_proof import spec_for
        for aid,kind in ((51,'sheets'),(52,'resume')):
            spec=spec_for(kind)
            with guardrails.connect(self.db) as conn:
                conn.execute("INSERT INTO approvals(id,kind,title,payload,status) VALUES(?,'public_draft',?,?,'approved')",
                             (aid,'Draft: [Inside Agent] raw title (v4, llm)',json.dumps(spec)))
            folder=pb.build_product(spec,aid,out_dir=str(self.products))
            pb._record(self.db,aid,'built',folder)
            result=pb.truth_check_folder(aid,folder,db_path=self.db,out_dir=str(self.products),notify_fn=lambda text:True)
            self.assertEqual(result['verdict'],'ready',result)
        self.texts=[];self.photos=[];self.documents=[]
        self.ctx={'chat_id':CHAT,'db_path':self.db,'products_dir':str(self.products),'now':NOW,
                  'send_text':lambda text,*a,**k:self.texts.append(str(text)) or True,
                  'send_photo':lambda path,caption='':self.photos.append((path,caption)) or True,
                  'send_document':lambda path,caption='':self.documents.append((path,caption)) or True,
                  'send_media_group':lambda paths,caption='':True,
                  'state_get':lambda key,default=None:inbox.get_state(key,default,self.db),
                  'state_set':lambda key,value:inbox.set_state(key,value,self.db)}
        self.enterContext(patch.dict(os.environ,TELEGRAM_CHAT_ID=CHAT,CHILLI_PRODUCTS_DIR=str(self.products)))
        self.enterContext(patch.object(inbox,'build_plugin_ctx',return_value=self.ctx))
        self.enterContext(patch.object(inbox,'record_plugin_crash',side_effect=AssertionError('plugin crashed')))
    def call(self,text):
        self.texts.clear();self.photos.clear()
        handled,reply=inbox.call_plugins(text,'unused',CHAT,db_path=self.db,now=self.ctx['now'])
        self.assertTrue(handled,text)
        if reply.get('text'):self.texts.append(str(reply['text']))
        return reply
    def test_listnext_separate_fields_from_real_kit(self):
        best=tg_listnext.next_pack(self.ctx)
        self.call('/listnext')
        for number,label in ((1,'Title'),(2,'Short description'),(3,'Full description'),(4,'Tags')):
            self.assertTrue(any(text.startswith(f'{number}. {label}') for text in self.texts),self.texts)
        combined='\n'.join(self.texts)
        self.assertIn(f'5. Files: send /upload {best["id"]}',combined)
        self.assertIn(f'/listed {best["id"]} <link>',combined)
        self.assertEqual(len(self.photos),1);self.assertTrue(Path(self.photos[0][0]).is_file())
        self.assertNotRegex(combined,r'Inside Agent|v4|rewritten|Draft:')
        for text in self.texts+[self.photos[0][1]]:
            self.assertLessEqual(len(text.splitlines()),15,text)
            self.assertTrue(text.splitlines()[-1].startswith('Next:'),text)
    def test_non_owner_sends_nothing(self):
        self.assertIsNone(tg_listnext.handle('/listnext',dict(self.ctx,chat_id='stranger')))
        self.assertEqual(self.texts,[])
