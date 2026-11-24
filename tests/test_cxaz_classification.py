import datetime as dt
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import auto_approve
import daily_report
import guardrails
import improver_loop
import telegram_inbox


class ScoutClassificationTests(unittest.TestCase):
    def test_live_shaped_structured_scout_drafts_and_imp_proposal(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp)/'queue.db')
            with improver_loop.connect(db) as conn:
                for aid, title, kind in zip(range(119,124), ['Invoice Tracker','Teacher Planner','Baby Tracker','Resume Bundle','Budget Sheet'],
                                            ['sheets','teacher_planner','baby_tracker','resume','sheets']):
                    conn.execute("INSERT INTO approvals(id,ts,kind,title,payload,status,review) VALUES(?,?,'scout_idea',?,?,'pending',?)",
                                 (aid,'2026-11-22T00:00:00','[Scout Idea] '+title,'Category: Templates\nNiche: test\nAudience: families\nFormat: '+kind,'Checker: 84/100 | CHECKER: PASS'))
                conn.execute("INSERT INTO learned_rules(id,rule_text,category,problem_key,status,evidence,created_ts) VALUES(7,?,'writer','short_titles','proposed','3 occurrences',?)",
                             ('Keep listing titles clear.','2026-11-24T11:00:00+00:00'))
            drafts, proposals = auto_approve.split_waiting(db)
            self.assertEqual([d['id'] for d in drafts],list(range(119,124)))
            self.assertEqual(proposals,[])
            result = daily_report.product_path(db,dt.datetime(2026,11,24,12))
            self.assertEqual((result['drafts_waiting'],result['proposals_waiting'],result['rule_proposals']),(5,0,1))
            plan = auto_approve._plan(db_path=db,now=dt.datetime(2026,11,24,12,tzinfo=dt.timezone.utc))
            self.assertFalse(any(p.get('why')=='proposal_timeout' for p in plan))
            self.assertIn('Rejected proposal #7',improver_loop.handle_callback('imp:r:7',db))
            self.assertEqual(daily_report.product_path(db,dt.datetime(2026,11,24,12))['drafts_waiting'],5)

    def test_legacy_read_only_summary_schema_skips_irrelevant_product_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'summary.db')
            with sqlite3.connect(db) as conn:
                conn.execute('CREATE TABLE approvals(id INTEGER, status TEXT)')
            conn=guardrails.connect(db)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM approvals').fetchone()[0],0)
            conn.close()

    def test_bare_restore_and_word_alias_show_real_help_through_loader(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'queue.db')
            for text in ('/restore','restore'):
                handled,reply=telegram_inbox.call_plugins(text,'fake',int('1234' '5678'),db_path=db)
                self.assertTrue(handled)
                self.assertIn('/restore N',reply['text'])

    def test_source_and_owner_restore_preserve_other_rejections(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'queue.db')
            aid=guardrails.request_approval('scout_idea','[Scout Idea] Invoice tracker','Format: sheets',db_path=db)
            self.assertEqual(guardrails.get_approval(aid,db)['kind'],'public_draft')
            guardrails.decide(aid,False,db,'owner time ayipoyindi')
            with mock.patch.dict(os.environ,{'TELEGRAM_CHAT_ID':str(int('1234' '5678'))}):
                handled,reply=telegram_inbox.call_plugins('/restore '+str(aid),'fake',int('1234' '5678'),db_path=db)
            self.assertTrue(handled)
            self.assertIn('waiting',reply['text'])
            self.assertEqual(guardrails.get_approval(aid,db)['status'],'pending')
            guardrails.decide(aid,False,db,'Other')
            telegram_inbox.call_plugins('/restore '+str(aid),'fake',int('1234' '5678'),db_path=db)
            self.assertEqual(guardrails.get_approval(aid,db)['status'],'rejected')
