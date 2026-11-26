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
class ReadyPacksFixture(unittest.TestCase):
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

class ListingDay(ReadyPacksFixture):
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

    def test_two_pack_phone_listing_and_individual_days(self):
        import tg_listed
        import tg_talk
        first=tg_listnext.next_pack(self.ctx)['id']
        self.call('/listnext')
        self.call(f'/upload {first}')
        self.assertTrue(any(Path(path).name=='product.zip' for path,_ in self.documents))
        self.call(f'/listed {first} https://example.gumroad.com/l/first')
        self.assertIn('Next: /listnext',self.texts[-1])
        run=selltest.active(self.db)
        selected=next(p for p in run['packs'] if p['id']==first)
        self.assertEqual(selected['start_day'],'2026-11-26')
        second=tg_listnext.next_pack(self.ctx)['id'];self.assertNotEqual(first,second)
        self.ctx['now']=NOW+dt.timedelta(days=2)
        self.call('/listnext')
        self.call(f'/listed {second} https://gumroad.com/l/second')
        run=selltest.active(self.db);members={p['id']:p for p in run['packs']}
        self.assertEqual(selltest.day_number(run,self.ctx['now'],members[first]),3)
        self.assertEqual(selltest.day_number(run,self.ctx['now'],members[second]),1)
        self.call(f'/listed {first} https://example.gumroad.com/l/first')
        self.assertEqual(next(p for p in selltest.active(self.db)['packs'] if p['id']==first)['start_day'],'2026-11-26')
        self.assertEqual(set(tg_listed.listed_ids(self.db)),{first,second})
        self.assertIsNone(tg_listnext.next_pack(self.ctx))
        self.call('/listnext');self.assertIn('Kotha unlisted READY Pack ledu',self.texts[-1])
        card=tg_talk.sell_card_text({'to_list':[{'id':first,'title':'Budget Sheet','status':'SELLABLE'}],'listed':[],'drafts':0},self.ctx)
        self.assertLess(card.index('/listnext'),card.index('/sell '))
    def test_wrong_link_does_not_mark_or_start(self):
        import tg_listed
        for link in ('https://gumroad.com.evil.test/l/x','http://gumroad.com/l/x','https://gumroad.com/','https://example.com/l/x'):
            self.call('/listed 51 '+link)
            self.assertFalse(tg_listed.listed_ids(self.db))
            self.assertIsNone(selltest.active(self.db))
            self.assertIn('Link save cheyaledu',self.texts[-1])
