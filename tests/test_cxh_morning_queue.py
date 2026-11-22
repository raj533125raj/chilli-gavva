"""CXH-3: real morning delivery, accurate counts, blocked reasons and retry."""
import datetime
import unittest
from unittest import mock

import auto_approve
import guardrails
import secret_guard
import telegram_inbox
import tg_settings
from tests.test_cxh_queue_probe import QueueFixture,NOW


class MorningQueueTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.sent=[]
        for target,name,value in ((auto_approve.notify,"_telegram_configured",(False,None,None)),
                                  (tg_settings,"is_quiet_hour",False),
                                  (tg_settings,"get_morning_post_hour",8)):
            patcher=mock.patch.object(target,name,return_value=value)
            patcher.start();self.addCleanup(patcher.stop)
        patcher=mock.patch.object(telegram_inbox,"send_text",side_effect=self.send)
        patcher.start();self.addCleanup(patcher.stop)

    def send(self,token,chat,text):
        self.sent.append((chat,text));return True

    def run_decisions(self,now=NOW):
        return auto_approve.run_auto_decisions(self.db,token="fixture",owner_chat_id=int("1234" "5678"),now=now)

    def test_counts_match_saved_approved_rejected_and_blocked(self):
        auto_approve.save_settings({"enabled":True,"threshold":70,"daily_limit":1},self.db)
        self.add("Safe workbook",88,25)
        self.add("Weak unsupported pack",40,30,verdict="reject")
        self.add("Young draft",95,3)
        self.add("Quota waiting",87,30)
        self.assertEqual(self.run_decisions(),2)
        data=auto_approve.queue_digest(self.db,NOW)
        self.assertEqual((data['waiting'],data['decided'],data['approved'],data['rejected'],data['blocked']),
                         (2,2,1,1,2))
        self.assertEqual(data['reasons'],{'under_24h':1,'daily_limit':1})
        output='\n'.join(text for _,text in self.sent)
        self.assertIn('Waiting 2; decided today 2 (approved 1 / rejected 1); blocked 2',output)
        self.assertIn('daily limit 1',output)
        self.assertEqual(len(self.sent),2)
        for _,text in self.sent:
            self.assertLessEqual(len(text.splitlines()),15)
            self.assertTrue(text.splitlines()[-1].startswith('Next:'))
            self.assertEqual(secret_guard.scan_text(text,[]),[])

    def test_empty_queue_still_sends_morning_summary_once(self):
        self.assertEqual(self.run_decisions(),0)
        self.assertEqual(len(self.sent),1)
        self.assertIn('Waiting 0; decided today 0',self.sent[0][1])
        self.assertEqual(self.run_decisions(),0)
        self.assertEqual(len(self.sent),1)
        self.run_decisions(NOW+datetime.timedelta(days=1))
        self.assertEqual(len(self.sent),2)

    def test_failed_delivery_is_retried(self):
        with mock.patch.object(telegram_inbox,'send_text',return_value=False):
            self.run_decisions()
        self.run_decisions()
        self.assertEqual(len(self.sent),1)

    def test_new_drafts_pause_is_in_actual_morning_message(self):
        for i in range(12):self.add(f'Young item {i}',80+i,2)
        self.run_decisions()
        self.assertIn('Waiting 12',self.sent[0][1])
        self.assertIn('Kotha Drafts ee run lo aapamu',self.sent[0][1])
        self.assertTrue(auto_approve.queue_digest(self.db,NOW)['new_drafts_paused'])

    def test_owner_morning_hour_quiet_and_pause_are_respected(self):
        with mock.patch.object(tg_settings,'get_morning_post_hour',return_value=13):self.run_decisions()
        self.assertEqual(self.sent,[])
        with mock.patch.object(tg_settings,'is_quiet_hour',return_value=True):self.run_decisions()
        self.assertEqual(self.sent,[])
        guardrails.set_paused(True,self.db);self.run_decisions()
        self.assertEqual(self.sent,[])


if __name__=='__main__':unittest.main()
