"""Signed office controls use fake KV and temporary SQLite; no cloud or agent network calls."""
import contextlib
import datetime
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error
from urllib.parse import unquote, urlsplit, parse_qs

import control_inbox as ci
import guardrails
import office_tasks
import scheduler
import workforce

NOW = 1793356800
KEY = 'fixture-control-signing-value'
ENV = {'CF_ACCOUNT_ID':'fixture-account','CF_KV_NAMESPACE_ID':'fixture-namespace',
       'CF_API_TOKEN':'fixture-access-value','CONTROL_SIGNING_KEY':KEY}
AGENT = 'chilli-gavva-robot-research-worker'


def signed(action='add_mission', target='mission:one', value=None, ident='request:one', nonce=None, stamp=NOW):
    if value is None and action == 'add_mission':
        value = {'title':'Review product ideas', 'agent_id':AGENT, 'every':'1h'}
    record = {'id':ident,'action':action,'target':target,'value':value,
              'created_at':datetime.datetime.fromtimestamp(stamp,datetime.timezone.utc).isoformat().replace('+00:00','Z'),
              'nonce':nonce or ident}
    record['signature'] = hmac.new(KEY.encode(),json.dumps(record,separators=(',',':'),ensure_ascii=False).encode(),hashlib.sha256).hexdigest()
    return record


class FakeKV:
    def __init__(self, records=()):
        self.values = {ci.PREFIX+r['created_at']+':'+r['id']:json.dumps(r) for r in records}
        self.calls = []
        self.fail = None
        self.page_size = 100

    def add(self, record):
        self.values[ci.PREFIX+record['created_at']+':'+record['id']] = json.dumps(record)

    def __call__(self,url,token,method='GET',data=None):
        self.calls.append((url,method,data))
        if self.fail and self.fail(method):
            raise urllib.error.HTTPError(url,403,'fixture-private-error',None,None)
        path = urlsplit(url)
        if '/keys' in path.path:
            query = parse_qs(path.query)
            names = sorted(n for n in self.values if n.startswith(query['prefix'][0]))
            start = int(query.get('cursor',['0'])[0]); page=names[start:start+self.page_size]
            cursor = str(start+len(page)) if start+len(page)<len(names) else ''
            return json.dumps({'success':True,'result':[{'name':n} for n in page], 'result_info':{'cursor':cursor}})
        name = unquote(path.path.split('/values/',1)[1])
        if method == 'PUT':
            self.values[name] = data
            return json.dumps({'success':True})
        if name not in self.values:
            raise urllib.error.HTTPError(url,404,'fixture',None,None)
        return self.values[name]

    def receipt(self,ident='request:one'):
        return json.loads(self.values[ci.RECEIPT_PREFIX+ident])


class OfficeBase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root=Path(directory.name); self.db=str(self.root/'state.db')
        self.kv=FakeKV()
        self.rest=mock.patch('rest_window.in_rest_window',return_value=False)
        self.rest.start(); self.addCleanup(self.rest.stop)
        self.kill=mock.patch.object(guardrails,'is_killed',return_value=False)
        self.kill.start(); self.addCleanup(self.kill.stop)
        self.net=mock.patch('urllib.request.urlopen',side_effect=AssertionError('no network in office tests'))
        self.net.start(); self.addCleanup(self.net.stop)

    def run_request(self,record=None,now=NOW):
        if record: self.kv.add(record)
        return ci.run(self.db,ENV,self.kv,now)

    def rows(self,sql,args=()):
        conn=office_tasks.connect(self.db)
        try: return conn.execute(sql,args).fetchall()
        finally: conn.close()


class MissionAddTests(OfficeBase):
    def test_each_interval_is_stored_and_dispatched_once(self):
        for index,(every,seconds) in enumerate(office_tasks.INTERVALS.items()):
            rec=signed(target=f'mission:{index}',ident=f'req:{index}',value={'title':'Review ideas','agent_id':AGENT,'every':every})
            self.run_request(rec)
            self.assertEqual(self.kv.receipt(f'req:{index}')['state'],'applied')
            self.assertEqual(self.rows('SELECT next_run FROM office_missions WHERE id=?',(f'mission:{index}',))[0][0],NOW+seconds)
        self.assertEqual(office_tasks.tick(self.db,NOW)['queued'],0)
        self.assertEqual(office_tasks.tick(self.db,NOW+604800)['queued'],3)
        self.assertEqual(office_tasks.tick(self.db,NOW+604800)['queued'],0)
        self.assertEqual(len(self.rows("SELECT id FROM tasks WHERE kind='office_task'")),3)
        self.assertTrue(all(r[0]>NOW+604800 for r in self.rows('SELECT next_run FROM office_missions')))

    def test_aliases_and_real_office_agent_ids_execute_through_workforce(self):
        self.run_request(signed(value={'title':'Review ideas','agent_id':AGENT,'every':'hourly'}))
        office_tasks.tick(self.db,NOW+3600)
        task=workforce.claim_next(self.db)
        self.assertEqual(task['agent'],'Research Worker')
        reply=workforce.STEPS['office_task'](task,{'db_path':self.db,'llm':lambda p,size=None:'Advisory answer'})
        self.assertEqual(reply['answer'],'Advisory answer')
        self.assertEqual(reply['next'],[])

    def test_scheduler_ticks_outside_recipe_slots_and_respects_pause(self):
        self.run_request(signed()); guardrails.set_paused(True,self.db)
        self.assertEqual(office_tasks.tick(self.db,NOW+3600)['queued'],0)
        guardrails.set_paused(False,self.db)
        with mock.patch.object(scheduler,'_load_schedule',return_value=[]), mock.patch.object(office_tasks,'tick') as tick:
            scheduler.run_now(db_path=self.db)
        tick.assert_called_once_with(self.db,dry_run=False)

    def test_active_mission_and_pending_caps_hold_work(self):
        with mock.patch.object(office_tasks,'MAX_MISSIONS',0):
            self.run_request(signed())
        self.assertEqual(self.kv.receipt()['state'],'held')
        self.assertEqual(self.rows('SELECT COUNT(*) FROM office_missions')[0][0],0)
        self.run_request(signed(ident='request:two'))
        with mock.patch.object(office_tasks,'MAX_PENDING',0):
            self.assertEqual(office_tasks.tick(self.db,NOW+3600)['queued'],0)

    def test_invalid_interval_unknown_agent_and_shell_payload_are_rejected(self):
        for i,value in enumerate(({'title':'x','agent_id':AGENT,'every':'minute'},
                                  {'title':'x','agent_id':'unreported-agent','every':'1h'},
                                  {'title':'x','agent_id':AGENT,'every':'1h','command':'touch unexpected'})):
            self.run_request(signed(ident=f'req:{i}',value=value))
            self.assertEqual(self.kv.receipt(f'req:{i}')['state'],'rejected')
        self.assertFalse((self.root/'unexpected').exists())


class ReceiptSafetyTests(OfficeBase):
    def test_signature_covers_every_field_and_rejects_unknown_types(self):
        for i,field in enumerate(ci.SIGNED_FIELDS):
            record=signed(ident=f'req:{i}'); record[field]='tampered'
            if field=='id': record['id']=f'tampered:{i}'
            self.run_request(record)
            self.assertEqual(self.kv.receipt(record['id'])['state'],'rejected')
        self.run_request(signed(action='arbitrary_shell',ident='unknown',value={'command':'whoami'}))
        self.assertEqual(self.kv.receipt('unknown')['reason'],'unknown action')
        self.assertEqual(self.rows('SELECT COUNT(*) FROM office_missions')[0][0],0)

    def test_receipt_shape_signature_nonce_replay_and_stale_database(self):
        record=signed(); self.run_request(record)
        receipt=self.kv.receipt()
        self.assertEqual(set(receipt),{'id','state','reason','at','signature'})
        self.assertEqual(receipt['at'],'2026-10-30T10:40:00Z')
        self.assertEqual(self.run_request()['refused'],1)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM office_missions')[0][0],1)
        # A newer KV receipt remains authoritative if git failed to save the local database.
        other=str(self.root/'old.db')
        self.assertEqual(ci.run(other,ENV,self.kv,NOW)['refused'],1)
        self.assertEqual(office_tasks.connect(other).execute('SELECT COUNT(*) FROM office_missions').fetchone()[0],0)

    def test_expiry_nonce_reuse_and_unsigned_state_cannot_bypass_authentication(self):
        self.run_request(signed(stamp=NOW-90000,ident='expired'))
        self.assertEqual(self.kv.receipt('expired')['reason'],'expired')
        self.run_request(signed(ident='a',nonce='one'))
        self.run_request(signed(ident='b',nonce='one',target='mission:two'))
        self.assertEqual(self.kv.receipt('b')['state'],'rejected')
        rec=signed(ident='c',target='mission:three'); rec['state']='applied'
        self.run_request(rec)
        self.assertEqual(self.kv.receipt('c')['state'],'applied')

    def test_kv_read_or_write_denial_logs_once_without_secrets_and_does_not_apply(self):
        for method in ('GET','PUT'):
            with self.subTest(method=method):
                self.kv=FakeKV([signed()]); self.kv.fail=lambda m:m==method
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.run_request()
                self.assertEqual(len(output.getvalue().splitlines()),1)
                for private in (*ENV.values(),'fixture-private-error'):
                    self.assertNotIn(private,output.getvalue())
                self.assertEqual(self.rows('SELECT COUNT(*) FROM office_missions')[0][0],0)

    def test_pagination_and_malformed_request_still_write_receipts(self):
        self.kv.page_size=1
        self.kv.add(signed(ident='a')); self.kv.add(signed(ident='b',target='mission:two'))
        self.kv.values[ci.PREFIX+'broken']='not JSON'
        result=self.run_request()
        self.assertEqual(result,{'applied':2,'refused':1})
        self.assertEqual(len([k for k in self.kv.values if k.startswith(ci.RECEIPT_PREFIX)]),3)
