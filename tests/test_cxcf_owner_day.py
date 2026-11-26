"""A whole owner day through process_updates; only network boundaries are fake."""
import datetime as dt
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import auto_approve
import guardrails
import media_brain
import scout
import telegram_inbox as inbox
import tg_listnext
from tests import test_cxcd_listing as fixture

class OwnerDay(fixture.ReadyPacksFixture):
    def setUp(self):
        super().setUp()
        self.update=500
        self.enterContext(patch.object(inbox,'send_text',side_effect=lambda token,chat,text,*a,**k:self.texts.append(str(text)) or True))
        self.enterContext(patch.object(inbox,'_call',return_value={'ok':True}))
        self.enterContext(patch('rest_window.in_rest_window',return_value=False))
        self.enterContext(patch('llm_router.chat',return_value='Summary: Undated pages ni review cheyandi.\n1. Daily task blocks unnayi.\n2. Notes section undi.\n3. Print layout chudandi.\nHow this helps Chilli Gavva: Planner layout ni compare cheyandi.'))
        self.table=[]
    def say(self,text=None,message=None):
        self.update+=1;self.texts.clear();self.photos.clear()
        payload={'chat':{'id':int(fixture.CHAT)},'message_id':self.update}
        if text is not None:payload['text']=text
        if message:payload.update(message)
        with patch.object(inbox,'fetch_updates',return_value=[{'update_id':self.update,'message':payload}]):
            self.assertEqual(inbox.process_updates('unused',fixture.CHAT,self.db),1)
        self.check_messages(text or 'photo')
    def check_messages(self,label):
        messages=self.texts+[caption for _,caption in self.photos]
        self.assertTrue(messages,label)
        for text in messages:
            self.assertLessEqual(len(text.splitlines()),15,(label,text))
            self.assertTrue(text.splitlines()[-1].startswith('Next:'),(label,text))
            self.assertRegex(text,r'cheyandi|pampandi|chudandi|nokkandi|chesanu|kavali')
            self.assertNotRegex(text,r'Draft:|Inside Agent|\(v4|rewritten|Daily P \(')
        self.table.append((label,len(messages),max(len(t.splitlines()) for t in messages)))
    def test_whole_day_real_inbox_and_media_storage(self):
        now=fixture.NOW
        with guardrails.connect(self.db) as conn:
            for aid in range(600,616):
                conn.execute("INSERT INTO approvals(id,ts,kind,title,payload,status) VALUES(?,?,'scout_idea','Research proposal','Buyer need check','pending')",
                             (aid,(now-dt.timedelta(hours=1)).isoformat()))
        with patch('tg_settings.is_quiet_hour',return_value=False),patch('tg_settings.get_morning_post_hour',return_value=8):
            self.assertTrue(inbox.send_morning_sell_card('unused',fixture.CHAT,self.db,now=now))
        self.check_messages('morning card')
        self.assertIn('/listnext',self.texts[0]);self.assertIn('Drafts waiting: 0',self.texts[0])
        for text in ('2+2','ee roju em cheyali','/listnext'):
            self.say(text)
        aid=tg_listnext.next_pack(self.ctx)['id']
        self.say(f'/upload {aid}')
        self.say(f'/listed {aid} https://example.gumroad.com/l/first')
        for text in ('/selltest stats','/list','/drafts','/buttontest','/doctor','/help','zzyyxx'):
            self.say(text)
            if text=='/list':
                self.assertIn('No drafts waiting',self.texts[0]);self.assertIn('Proposals waiting: 16',self.texts[0])
            if text=='/drafts':self.assertIn('Proposals waiting: 16','\n'.join(self.texts))
        image_bytes=next((self.products/'upload').glob('*/cover.png')).read_bytes()
        with patch('media_brain.download_telegram_file',return_value=(image_bytes,None)), \
             patch('llm_router.chat_vision',return_value='Summary: Cover lo readable title undi.\n1. Clear title.\n2. Calm colors.\n3. Margin chudandi.\nHow this helps Chilli Gavva: Cover layout ni review cheyandi.'):
            self.say(message={'photo':[{'file_id':'photo-fixture','file_size':18}]})
        class Response(io.BytesIO):
            headers={'Content-Type':'text/html'}
        with patch('media_brain.urllib.request.urlopen',return_value=Response(b'<html><title>Planner layout</title><body>Undated tasks and notes.</body></html>')):
            self.say('https://example.com/planner')
        with guardrails.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM media_items').fetchone()[0],2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM approvals WHERE status='pending' AND kind='scout_idea'").fetchone()[0],16)
        self.assertTrue(any(Path(path).name=='product.zip' for path,_ in self.documents))
        print('OWNER DAY:',json.dumps(self.table))
    def test_archived_scout_mirror_agrees_and_restore_reopens_it(self):
        with scout.connect(self.db) as conn:
            conn.execute("INSERT INTO approvals(id,ts,kind,title,payload,status) VALUES(700,?,'scout_idea','Research proposal','Owner research','pending')",
                         ((fixture.NOW-dt.timedelta(hours=80)).isoformat(),))
            conn.execute("INSERT INTO scout_ideas(idea,approval_id,status,synced) VALUES('Research proposal',700,'pending',0)")
        self.assertEqual(auto_approve.archive_scout_proposals(self.db,fixture.NOW),[700])
        with guardrails.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT status,synced FROM scout_ideas').fetchone(),('archived',1))
        self.call('/restore 700')
        with guardrails.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT status,synced FROM scout_ideas').fetchone(),('pending',0))
        self.assertEqual(guardrails.get_approval(700,self.db)['status'],'pending')

    def test_card_and_report_exclude_an_unapproved_pack_together(self):
        import daily_report
        import tg_talk
        with guardrails.connect(self.db) as conn:
            conn.execute("UPDATE approvals SET status='rejected' WHERE id=51")
        numbers=daily_report.owner_numbers(self.db,self.products,fixture.NOW)
        data=tg_talk.sell_card_data(self.ctx)
        self.assertEqual(numbers['ready_packs'],1)
        self.assertEqual([row['id'] for row in data['to_list']],[52])


class FreeActionSafety(unittest.TestCase):
    def test_real_inbox_never_executes_free_action_text(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'robot.db')
            aid=guardrails.request_approval('public_draft','Budget Sheet','Format: sheets',db_path=db)
            sent=[]
            with patch.dict(os.environ,TELEGRAM_CHAT_ID=fixture.CHAT), patch.object(inbox,'_call',return_value={'ok':True}), \
                 patch.object(inbox,'send_text',side_effect=lambda token,chat,text,*a,**k:sent.append(text) or True):
                for index,verb in enumerate(('approve','reject','pause','resume','kill','delete','sell','list','post'),1):
                    update={'update_id':index,'message':{'chat':{'id':int(fixture.CHAT)},'text':verb+' '+str(aid)}}
                    with patch.object(inbox,'fetch_updates',return_value=[update]):
                        self.assertEqual(inbox.process_updates('unused',fixture.CHAT,db),1)
                    self.assertTrue(sent[-1].splitlines()[-1].startswith('Next:'),sent[-1])
                    self.assertEqual(guardrails.get_approval(aid,db)['status'],'pending')
                    self.assertFalse(guardrails.is_paused(db))
