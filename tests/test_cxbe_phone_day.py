"""Owner's actual first-day command sequence with real files and fake Telegram."""
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
import post_planner
import selltest
import telegram_inbox as inbox


@unittest.skipUnless(mockups.available(),'Pillow and Poppler required')
class PhoneDay(unittest.TestCase):
    def test_first_sell_day_real_loader_replies_are_compact_and_have_the_next_step(self):
        from tools import sell_proof
        import checker
        now = dt.datetime(2026,11,24,7,tzinfo=dt.timezone.utc)
        chat = str(int('1234' '5678'))
        with tempfile.TemporaryDirectory() as temp:
            products = Path(temp)/'products'; db = str(Path(temp)/'robot.db')
            spec = sell_proof.spec_for('resume')
            with guardrails.connect(db) as conn:
                conn.execute("INSERT INTO approvals(id,kind,title,payload,review,status) VALUES(56,'public_draft',?,?,?,'approved')",
                             ('ATS Resume Bundle',json.dumps(spec),checker.format_review(90,[])))
            folder = pb.build_product(spec,56,out_dir=str(products))
            pb._record(db,56,'built',folder)
            result = pb.truth_check_folder(56,folder,db_path=db,out_dir=str(products),notify_fn=lambda text:True)
            self.assertEqual(result['verdict'],'ready',result)
            texts = []; captions = []; files = []; state = {}
            def document(path,caption=''):
                self.assertTrue(Path(path).is_file(),path); files.append(Path(path).name)
                captions.append(caption); return True
            def group(paths,caption=''):
                for path in paths: self.assertTrue(Path(path).is_file(),path)
                captions.append(caption); return True
            ctx = {'db_path':db,'products_dir':str(products),'chat_id':chat,'now':now,
                   'send_text':lambda text,*a,**k:texts.append(str(text)) or True,
                   'send_document':document,'send_photo':document,'send_media_group':group,
                   'state_get':lambda key,default=None:state.get(key,default),
                   'state_set':lambda key,value:state.update({key:value})}
            steps = [('/storekit','/sell 56'),('/sell 56','/upload 56'),('/upload 56','/listed 56'),
                     ('/listed 56 https://example.gumroad.com/l/resume','/selltest start'),
                     ('/selltest start','/today'),('/today','/post today'),('/post today','/posted 1'),
                     ('/posted 1','/selltest stats 56'),('/selltest stats 56 views 120 sales 1','/doctor selltest'),
                     ('/doctor selltest','/doctor promises'),('/doctor promises','/today')]
            with patch.dict(os.environ,{'TELEGRAM_CHAT_ID':chat}), \
                    patch.object(inbox,'build_plugin_ctx',return_value=ctx), \
                    patch.object(inbox,'record_plugin_crash',side_effect=AssertionError('Plugin crashed')), \
                    patch.object(post_planner,'PLAN_PATH',str(post_planner.plan_path_for(products))):
                for command, expected in steps:
                    with self.subTest(command=command):
                        texts.clear(); captions.clear()
                        handled, reply = inbox.call_plugins(command,'unused',chat,db_path=db,now=now)
                        self.assertTrue(handled,command)
                        if reply.get('text'): texts.append(str(reply['text']))
                        self.assertTrue(texts,command)
                        for text in texts+captions:
                            self.assertLessEqual(len(text.splitlines()),15,text)
                            self.assertTrue(text.splitlines()[-1].startswith('Next:'),text)
                            self.assertRegex(text,r'cheyandi|pampandi|chudandi|chesanu|chesam|kavali')
                        self.assertIn(expected,texts[-1].splitlines()[-1],texts[-1])
                handled, reply = inbox.call_plugins('2+2','unused',chat,db_path=db,now=now)
                self.assertTrue(handled)
                self.assertLessEqual(len(reply['text'].splitlines()),8)
                for command in ('/storekit','/selltest start','/today'): self.assertIn(command,reply['text'])
                self.assertTrue(reply['text'].splitlines()[-1].startswith('Next:'))
            self.assertIn('product.zip',files)
            self.assertEqual(selltest.stats(db)[0]['sales'],1)
            plan = post_planner.load_plan(post_planner.plan_path_for(products))
            self.assertEqual(next(p for p in plan if p['date']=='2026-11-24')['status'],'done')
