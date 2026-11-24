import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import guardrails
import product_builder
import promise_check
import selltest
import telegram_inbox

NOW=dt.datetime(2026,11,24,12,tzinfo=dt.timezone.utc)
CHAT=int('1234' '5678')

class PromiseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=str(Path(self.tmp.name)/'queue.db');self.products=Path(self.tmp.name)/'products'
        guardrails.connect(self.db).close()

    def draft(self,status='approved',stamp='2026-11-22T00:00:00'):
        aid=guardrails.request_approval('public_draft','Draft: [Budget Sheets Agent] Budget Sheet','Format: sheets',db_path=self.db)
        with guardrails.connect(self.db) as conn:
            conn.execute('UPDATE approvals SET status=?,ts=?,decided_ts=? WHERE id=?',(status,stamp,stamp,aid))
        return aid

    def test_real_four_approved_zero_built_and_daily_reservation(self):
        for _ in range(4):self.draft()
        result=promise_check.report(self.db,self.products,NOW)
        self.assertIn('4 approved Drafts',result)
        self.assertIn('/view 1',result)
        sent=[]
        self.assertEqual(promise_check.send_daily(sent.append,self.db,self.products,NOW),1)
        self.assertEqual(promise_check.send_daily(sent.append,self.db,self.products,NOW),0)
        self.assertEqual(promise_check.send_daily(sent.append,self.db,self.products,NOW+dt.timedelta(days=1)),1)

    def test_all_five_promises_and_future_decisions(self):
        approved=self.draft();built=self.draft();self.draft('pending')
        product_builder._record(self.db,built,'built','2-missing')
        promise_check.button_received(4,self.db,NOW)
        with selltest._connect(self.db) as conn:
            conn.execute('INSERT INTO sell_test VALUES(1,?)',('2026-11-24',))
            conn.execute('INSERT INTO sell_test_packs VALUES(1,?,?,?,?,?)',(built,'Budget Sheet','2-missing','sheets',90))
        keys={key for key,_ in promise_check.check(self.db,self.products,NOW)}
        self.assertEqual(keys,{'build','kit','decision','button','stats'})
        promise_check.button_answered(4,self.db,NOW)
        selltest.save_stats(built,0,0,self.db,NOW)
        self.assertFalse({'button','stats'} & {key for key,_ in promise_check.check(self.db,self.products,NOW)})

    def test_healthy_and_recent_approval(self):
        self.draft(stamp=NOW.isoformat())
        self.assertEqual(promise_check.report(self.db,self.products,NOW),'Anni promises OK')

    def test_real_button_send_success_and_failure(self):
        update={'update_id':5,'callback_query':{'id':'fake-query','data':'t:ping','message':{'chat':{'id':CHAT},'message_id':4}}}
        for success in (False,True):
            with mock.patch.object(telegram_inbox,'fetch_updates',return_value=[update]), mock.patch.object(telegram_inbox,'acknowledge_update',return_value=True), mock.patch.object(telegram_inbox,'_call',return_value={'ok':success}):
                telegram_inbox.set_state('update_offset','',self.db)
                telegram_inbox.process_updates('fake',CHAT,self.db)
            with guardrails.connect(self.db) as conn:
                answered=conn.execute('SELECT answered_ts FROM button_receipts WHERE update_id=5').fetchone()[0]
            self.assertEqual(bool(answered),success)
