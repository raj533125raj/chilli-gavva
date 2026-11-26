import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import owner_talk
import telegram_inbox as inbox

class Talk(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.chat = str(int('1234' '5678'))
        self.ctx = {'chat_id':self.chat,'db_path':str(Path(self.temp.name)/'robot.db'),
                    'now':dt.datetime(2026,11,26,7,tzinfo=dt.timezone.utc)}
        self.env = patch.dict(os.environ,TELEGRAM_CHAT_ID=self.chat); self.env.start(); self.addCleanup(self.env.stop)
    def test_local(self):
        for text, expected in [('2+2','4'),('3*(4+2)','18'),('1 km to m','1000 m'),('ee roju date','26 November 2026, Thursday')]:
            with self.subTest(text=text), patch('llm_router.chat',side_effect=AssertionError('local')):
                self.assertEqual(owner_talk.answer(text,self.ctx)['text'].splitlines()[0],expected)
    def test_router_and_long_question(self):
        with patch('llm_router.chat',return_value='Mee prashna ki answer idi.') as chat:
            result=owner_talk.answer('enduku ila avutundi? '*500,self.ctx)
            self.assertLessEqual(len(result['text'].splitlines()),8)
            self.assertLess(len(chat.call_args.args[0]),6500)
            self.assertEqual(chat.call_args.kwargs['db_path'],self.ctx['db_path'])
    def test_down(self):
        with patch('llm_router.chat',return_value=None):
            reply=owner_talk.answer('something unknown',self.ctx)['text']
            self.assertIn('AI ippudu',reply);self.assertEqual(reply.count('•'),3)
    def test_stranger(self):
        with patch('llm_router.chat',side_effect=AssertionError('stranger')):
            self.assertIsNone(owner_talk.answer('question',dict(self.ctx,chat_id='other')))
    def test_real_discovery(self):
        with patch.object(inbox,'build_plugin_ctx',return_value=self.ctx), patch('llm_router.chat',return_value='Ee roju /today chudandi.'):
            handled,reply=inbox.call_plugins('2+2','unused',self.chat,db_path=self.ctx['db_path'])
            self.assertTrue(handled);self.assertEqual(reply['text'].splitlines()[0],'4')
            for text in ('hi','ee roju em cheyali?'):
                handled,reply=inbox.call_plugins(text,'unused',self.chat,db_path=self.ctx['db_path'])
                self.assertTrue(handled);self.assertTrue(reply.get('text') or reply.get('handled'))
