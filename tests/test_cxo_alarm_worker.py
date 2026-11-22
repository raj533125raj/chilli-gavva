"""Offline Node checks for the Worker; all fetches and KV operations are local fakes."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

class AlarmButtonTests(unittest.TestCase):
    def run_case(self, body):
        self.assertIsNotNone(shutil.which("node"), "Node is required for Worker checks")
        source = (ROOT / "deploy" / "cloudflare_alarm_worker.js").read_text()
        setup = "import assert from 'node:assert/strict';\n" + "const worker = await import('data:text/javascript;base64,' + Buffer.from(" + json.dumps(source) + ").toString('base64'));\n"
        setup += r"""
const values = new Map(), puts = [], calls = [];
const env = { TELEGRAM_BOT_TOKEN: 'test-bot', TELEGRAM_CHAT_ID: '42', GITHUB_TOKEN: 'test-github',
 STATE: { get: async key => values.get(key) || null,
 put: async (key, value, options) => { values.set(key, value); puts.push({key, value, options}); } }};
let ackOK = true, dispatchOK = true;
let updates = [{update_id: 7, callback_query: {id:'owner-press', message:{chat:{id:42}}}},
 {update_id:8, callback_query:{id:'stranger-press',message:{chat:{id:99}}}},
 {update_id:9,message:{chat:{id:42},text:'hello'}}];
const fetchFn = async (url, options={}) => {
 calls.push({url,options});
 if (url.includes('/getUpdates')) return {ok:true,json:async()=>({ok:true,result:updates})};
 if (url.includes('/answerCallbackQuery')) return {ok:ackOK,json:async()=>({ok:ackOK})};
 if (url.includes('/dispatches')) return {status:dispatchOK?204:503};
 throw new Error('Unexpected request');
};
const now = Date.UTC(2026,10,22,9);
const ackCalls = () => calls.filter(call=>call.url.includes('/answerCallbackQuery'));
const dispatchCalls = () => calls.filter(call=>call.url.includes('/dispatches'));
"""
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", setup + body], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_peek_answers_owner_once_without_consuming_and_dispatches(self):
        self.run_case(r"""
assert.equal((await worker.tick(env,now,fetchFn)).action,'doorbell');
assert.equal(ackCalls().length,1);
assert.deepEqual(JSON.parse(ackCalls()[0].options.body),{callback_query_id:'owner-press',text:'Andindi ✅ Robot ~10 min lo chestundi'});
assert.equal(dispatchCalls().length,1);
assert.equal(JSON.parse(dispatchCalls()[0].options.body).inputs.reason,'doorbell');
assert.equal(puts.find(p=>p.key==='answered-button:owner-press').options.expirationTtl,86400);
assert.equal(new URL(calls[0].url).searchParams.has('offset'),false);
assert.deepEqual(JSON.parse(new URL(calls[0].url).searchParams.get('allowed_updates')),['message','callback_query']);
await worker.tick(env,now+60000,fetchFn);
assert.equal(ackCalls().length,1); assert.equal(dispatchCalls().length,1);
""")

    def test_failed_ack_still_dispatches_and_is_retried(self):
        self.run_case(r"""
ackOK=false; assert.equal((await worker.tick(env,now,fetchFn)).action,'doorbell');
assert.equal(values.has('answered-button:owner-press'),false);
ackOK=true; await worker.tick(env,now+60000,fetchFn);
assert.equal(ackCalls().length,2); assert.equal(dispatchCalls().length,1);
assert.equal(values.get('answered-button:owner-press'),'1');
""")

    def test_dispatch_failure_retries_without_answering_twice(self):
        self.run_case(r"""
dispatchOK=false; assert.equal((await worker.tick(env,now,fetchFn)).action,'doorbell-failed');
assert.equal(values.has('clock'),false);
dispatchOK=true; assert.equal((await worker.tick(env,now+60000,fetchFn)).action,'doorbell');
assert.equal(ackCalls().length,1); assert.equal(dispatchCalls().length,2);
""")

    def test_each_pending_owner_button_gets_its_own_receipt(self):
        self.run_case(r"""
updates.push({update_id:10,callback_query:{id:'second-press',message:{chat:{id:42}}}});
await worker.tick(env,now,fetchFn);
assert.deepEqual(ackCalls().map(call=>JSON.parse(call.options.body).callback_query_id),['owner-press','second-press']);
assert.equal(dispatchCalls().length,1);
""")
