import calendar, hashlib, hmac, json, os, tempfile, time, unittest
import urllib.error
from unittest import mock
import control_inbox
import notify
import telegram_inbox

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
    def run_one(self, item):
        rows={"one":item}; return control_inbox.run(self.db,self.env,self.http(rows),NOW,lambda _: None), rows
    def test_not_configured(self):
        self.assertEqual(control_inbox.run(self.db, env={}), {"applied":0,"refused":0})
    def test_kv_down(self):
        self.assertEqual(control_inbox.run(self.db, self.env, http_request=lambda *_: (_ for _ in ()).throw(OSError()), now=NOW), {"applied":0,"refused":0})
    def test_kv_http_failure_logs_one_safe_line(self):
        for code in (403, 401, 500):
            for reading_value in (False, True):
                with self.subTest(code=code, reading_value=reading_value):
                    url = control_inbox._url(self.env["CF_ACCOUNT_ID"], self.env["CF_KV_NAMESPACE_ID"], "keys")
                    error = urllib.error.HTTPError(url, code, self.env["CF_API_TOKEN"], None, None)
                    replies = [error]
                    if reading_value:
                        replies.insert(0, json.dumps({"result": [{"name": "control:request:one"}]}))
                    with mock.patch("builtins.print") as output:
                        self.assertEqual(control_inbox.fetch(self.env, mock.Mock(side_effect=replies)), [])
                    reason = f"HTTP {code}"
                    if code == 403:
                        reason += " - check API token has Workers KV Storage Edit"
                    output.assert_called_once_with(f"Control inbox: KV read failed ({reason})")
    def test_kv_exception_logs_class_without_exception_details(self):
        detail = control_inbox._url(self.env["CF_ACCOUNT_ID"], self.env["CF_KV_NAMESPACE_ID"],
                                    self.env["CF_API_TOKEN"] + self.env["CONTROL_SIGNING_KEY"])
        for error in (OSError(detail), ValueError(detail), json.JSONDecodeError(detail, "", 0)):
            with self.subTest(kind=type(error).__name__), mock.patch("builtins.print") as output:
                self.assertEqual(control_inbox.fetch(self.env, mock.Mock(side_effect=error)), [])
            output.assert_called_once_with(f"Control inbox: KV read failed ({type(error).__name__})")
    def test_kv_success_does_not_log_failure(self):
        with mock.patch("builtins.print") as output:
            self.assertEqual(control_inbox.fetch(self.env, self.http({"one": record()})),
                             [("control:request:one", record())])
        output.assert_not_called()
    def test_valid_approve(self):
        with mock.patch("telegram_inbox.handle_command") as command:
            result,_=self.run_one(record())
        self.assertEqual(result["applied"],1); command.assert_called_once_with("approve",[1],self.db)
    def test_valid_reject(self):
        with mock.patch("telegram_inbox.handle_command") as command: result,_=self.run_one(record("reject","2"))
        self.assertEqual(result["applied"],1); command.assert_called_once_with("reject",[2],self.db)
    def test_pause(self):
        with mock.patch("telegram_inbox.handle_command") as command: result,_=self.run_one(record("pause","robot"))
        self.assertEqual(result["applied"],1); command.assert_called_once_with("pause",[],self.db)
    def test_resume(self):
        with mock.patch("telegram_inbox.handle_command") as command: result,_=self.run_one(record("resume","robot"))
        self.assertEqual(result["applied"],1); command.assert_called_once_with("resume",[],self.db)
    def test_set_switch_publish_type(self):
        item=record("set_switch","pinterest"); item["value"]=True; item["signature"]=hmac.new(KEY.encode(),json.dumps({k:item[k] for k in ("id","action","target","value","created_at","nonce")},separators=(",",":"),ensure_ascii=False).encode(),hashlib.sha256).hexdigest()
        with mock.patch("publisher.set_enabled") as enabled: result,_=self.run_one(item)
        self.assertEqual(result["applied"],1); enabled.assert_called_once_with("pinterest",True,self.db)
    def test_set_switch_unknown_type(self):
        item=record("set_switch","unknown"); item["value"]=True; item["signature"]=hmac.new(KEY.encode(),json.dumps({k:item[k] for k in ("id","action","target","value","created_at","nonce")},separators=(",",":"),ensure_ascii=False).encode(),hashlib.sha256).hexdigest()
        self.assertEqual(self.run_one(item)[0]["refused"],1)
    def test_bad_signature(self): self.assertEqual(self.run_one({**record(),"signature":"bad"})[0]["refused"],1)
    def test_expired(self): self.assertEqual(self.run_one(record(age=90000))[0]["refused"],1)
    def test_future_over_five_minutes(self):
        item=record(); item["created_at"]=time.strftime("%Y-%m-%dT%H:%M:%S.123Z",time.gmtime(NOW+301)); item["signature"]=hmac.new(KEY.encode(),json.dumps({k:item[k] for k in ("id","action","target","value","created_at","nonce")},separators=(",",":"),ensure_ascii=False).encode(),hashlib.sha256).hexdigest()
        self.assertEqual(self.run_one(item)[0]["refused"],1)
    def test_replayed_nonce(self):
        self.assertEqual(self.run_one(record())[0]["applied"],1); self.assertEqual(self.run_one(record())[0]["refused"],1)
    def test_unknown_action(self): self.assertEqual(self.run_one(record("unknown"))[0]["refused"],1)
    def test_cross_language_vectors(self):
        with open("tests/fixtures/control_vectors.json", encoding="utf8") as handle: vectors=json.load(handle)
        now=calendar.timegm(time.strptime("2026-10-22T09:00:00Z","%Y-%m-%dT%H:%M:%SZ"))
        expected=[None,None,None,None,"expired","bad signature"]
        self.assertEqual([control_inbox._reason(item,"test-key",now) for item in vectors],expected)
    def test_control_inbox_error_never_stops_cycle(self):
        with mock.patch("notify._telegram_configured",return_value=(True,"fake","1")), mock.patch("telegram_inbox.process_updates",return_value=0), mock.patch("control_inbox.run",side_effect=RuntimeError()), mock.patch("telegram_inbox.alert_new_drafts",return_value=0), mock.patch("telegram_inbox.remind_waiting_drafts",return_value=False), mock.patch("telegram_inbox.send_text",return_value=True), mock.patch("telegram_inbox.subscription_note",return_value=""):
            self.assertTrue(telegram_inbox.run(self.db)["configured"])

if __name__ == "__main__": unittest.main()
