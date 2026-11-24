import os
from unittest import mock
from tests.test_cxan_promises import PromiseTests, NOW, CHAT
import promise_check
import telegram_inbox

class PromiseDoctorTests(PromiseTests):
    def test_real_loader_same_four_approved_report_without_alert_reservation(self):
        for _ in range(4):self.draft()
        ctx=telegram_inbox.build_plugin_ctx('fake',CHAT,self.db,NOW)
        ctx['products_dir']=self.products
        with mock.patch.dict(os.environ,{'TELEGRAM_CHAT_ID':str(CHAT)}),mock.patch.object(telegram_inbox,'build_plugin_ctx',return_value=ctx):
            handled,result=telegram_inbox.call_plugins('/doctor promises','fake',CHAT,self.db,NOW)
        self.assertTrue(handled)
        self.assertEqual(result['text'],promise_check.report(self.db,self.products,NOW))
        self.assertIn('4 approved Drafts',result['text'])
        self.assertEqual(promise_check.send_daily(lambda text:True,self.db,self.products,NOW),1)
