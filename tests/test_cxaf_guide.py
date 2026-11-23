"""Every guide command is declared and the documented plugin forms really route."""
from pathlib import Path
import re
import unittest
from unittest import mock
import tempfile
import os
import selltest
from tests.test_cxx_selltest import NOW,CHAT

ROOT=Path(__file__).resolve().parents[1]


class GuideTests(unittest.TestCase):
    def test_all_days_and_shared_quick_steps_are_present(self):
        text=(ROOT/'docs/SELL-TEST-TENGLISH.md').read_text()
        for day in range(15):self.assertRegex(text,rf'\bDay {day}\b')
        block=text.split('```text\n',1)[1].split('```',1)[0]
        self.assertIn(selltest.help_text(),block)
        self.assertLessEqual(len(selltest.help_text().splitlines()),15)
        self.assertTrue(selltest.help_text().splitlines()[-1].startswith('Next:'))
        self.assertIn('listing-ab.txt',text);self.assertIn('Post number',text)

    def test_every_named_telegram_command_exists(self):
        import telegram_inbox,tg_menu
        text=(ROOT/'docs/SELL-TEST-TENGLISH.md').read_text()
        commands=set(re.findall(r'(?<![\w:/])(/[a-z][a-z-]*)\b',text))
        declared={row['cmd'] for row in tg_menu.BUILTIN_COMMANDS}
        for module in telegram_inbox.discover_plugins():
            declared.update(row['cmd'] for row in getattr(module,'COMMANDS',[]))
        self.assertTrue(commands)
        self.assertEqual(commands-declared,set())

    def test_required_command_forms_reach_real_plugins(self):
        import telegram_inbox,tg_selltest,tg_upload,tg_listed,tg_posts
        from tests.test_cxx_selltest import choices
        with tempfile.TemporaryDirectory() as temp:
            ctx={'db_path':str(Path(temp)/'owner.db'),'products_dir':str(Path(temp)/'products'),
                 'chat_id':CHAT,'now':NOW,'send_text':lambda *a,**k:True,
                 'send_document':lambda *a,**k:True,'send_photo':lambda *a,**k:True,
                 'send_media_group':lambda *a,**k:True,'state_get':lambda k,d=None:d}
            with mock.patch.dict(os.environ,{'TELEGRAM_CHAT_ID':CHAT}), \
                    mock.patch.object(selltest,'candidates',return_value=choices()), \
                    mock.patch.object(telegram_inbox,'build_plugin_ctx',return_value=ctx):
                for command in ('/selltest','/selltest list','/upload 1','/upload 1 sample',
                    '/listed 1 https://example.gumroad.com/l/full-pack','/post today','/posted 1',
                    '/selltest stats 1 views 120 sales 1','/selltest swap-title 1',
                    '/selltest sample 1 https://example.gumroad.com/l/free-sample'):
                    with self.subTest(command=command):
                        handled,reply=telegram_inbox.call_plugins(command,'unused',CHAT,db_path=ctx['db_path'],now=NOW,
                            plugins=[tg_selltest,tg_upload,tg_listed,tg_posts])
                        self.assertTrue(handled)
                        self.assertNotIn('Command sarigga',str(reply))


class HelpTests(unittest.TestCase):
    def test_help_routes_matches_guide_and_does_not_start_an_experiment(self):
        import telegram_inbox,tg_selltest
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'owner.db')
            ctx={'db_path':db,'chat_id':CHAT,'now':NOW,'products_dir':temp}
            with mock.patch.dict(os.environ,{'TELEGRAM_CHAT_ID':CHAT}), \
                    mock.patch.object(selltest,'start',side_effect=AssertionError('Help must not start')), \
                    mock.patch.object(telegram_inbox,'build_plugin_ctx',return_value=ctx):
                handled,reply=telegram_inbox.call_plugins('/selltest help','unused',CHAT,db_path=db,now=NOW,plugins=[tg_selltest])
                self.assertTrue(handled)
                self.assertEqual(reply['text'],selltest.help_text())
                self.assertLessEqual(len(reply['text'].splitlines()),15)
                self.assertTrue(reply['text'].splitlines()[-1].startswith('Next:'))
                self.assertIsNone(tg_selltest.handle('/selltest help',dict(ctx,chat_id='other')))
            self.assertIsNone(selltest.active(db))
