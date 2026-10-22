import hashlib, hmac, json, os, tempfile, time, unittest
from unittest import mock
import control_inbox

KEY = "test-signing-key"
NOW = 1792656000

def record(action="approve", target="1", nonce="n1", age=0):
    item = {"id":"r1", "action":action, "target":target, "value":None,
            "created_at":time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW-age)), "nonce":nonce}
    item["signature"] = hmac.new(KEY.encode(), json.dumps(item, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
    return item

class ControlInboxTests(unittest.TestCase):
    def setUp(self): self.db = tempfile.mktemp(); self.env = {"CF_ACCOUNT_ID":"a","CF_KV_NAMESPACE_ID":"n","CF_API_TOKEN":"t","CONTROL_SIGNING_KEY":KEY}
    def tearDown(self):
        if os.path.exists(self.db): os.unlink(self.db)
    def http(self, rows):
        def call(url, token, method="GET", data=None):
            if "keys?" in url: return json.dumps({"result":[{"name":f"control:request:{k}"} for k in rows]})
            key = url.rsplit("/",1)[-1].removeprefix("control:request:")
            if method == "PUT": rows[key]["written"] = data; return ""
            return json.dumps(rows[key])
        return call
    def test_not_configured_and_kv_down_are_harmless(self):
        self.assertEqual(control_inbox.run(self.db, env={}), {"applied":0,"refused":0})
        self.assertEqual(control_inbox.run(self.db, self.env, http_request=lambda *_: (_ for _ in ()).throw(OSError()), now=NOW), {"applied":0,"refused":0})
    def test_valid_bad_old_replay_and_unknown_requests(self):
        rows={"r1":record(), "bad":record(nonce="bad"), "old":record(nonce="old",age=90000), "unknown":record("nope",nonce="u")}; rows["bad"]["signature"]="bad"
        sent=[]
        with mock.patch("telegram_inbox.handle_command") as command:
            result=control_inbox.run(self.db,self.env,self.http(rows),NOW, sent.append)
        self.assertEqual(result,{"applied":1,"refused":3}); command.assert_called_once_with("approve",[1],self.db); self.assertEqual(sent,["Office: approved draft #1"])
        self.assertIn("applied",rows["r1"]["written"])
        self.assertEqual(control_inbox.run(self.db,self.env,self.http({"again":record()}),NOW, sent.append)["refused"],1)

if __name__ == "__main__": unittest.main()
