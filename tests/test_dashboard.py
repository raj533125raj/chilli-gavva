"""Tests for the status snapshot, chat answers, and the local server's safety checks."""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chat  # noqa: E402
import export_status  # noqa: E402
import guardrails  # noqa: E402
import serve  # noqa: E402
import workforce  # noqa: E402


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        self._old = guardrails.KILL_FILE
        guardrails.KILL_FILE = os.path.join(self.tmp.name, "KILL")
        os.environ.pop("AGENT_PAUSED", None)
        self._wf_cfg = workforce.CONFIG_PATH
        workforce.CONFIG_PATH = os.path.join(self.tmp.name, "wf_config.json")
        with open(workforce.CONFIG_PATH, "w") as f:
            f.write('{"daily_drafts": 1}')

    def tearDown(self):
        workforce.CONFIG_PATH = self._wf_cfg
        guardrails.KILL_FILE = self._old
        self.tmp.cleanup()

    def test_snapshot_shape_and_no_secrets(self):
        workforce.enqueue("plan", {"topic": "demo"}, db_path=self.db)
        workforce.run_once(max_tasks=10, db_path=self.db, llm=lambda p, size=None: "PRODUCT NAME: Demo\nPRICE: 5 USD")
        status = export_status.build_status(self.db)
        self.assertNotIn("mode", status)
        self.assertEqual(status["money"], {"real_usd": 0.0})
        self.assertEqual(len(status["agents"]), len(workforce.AGENTS))
        self.assertEqual(len(status["pending_approvals"]), 1)
        blob = json.dumps(status).lower()
        for secret_word in ("api_key", "gsk_", "bearer", "payload"):
            self.assertNotIn(secret_word, blob)
        out = export_status.write_status(os.path.join(self.tmp.name, "out", "status.json"), self.db)
        with open(out) as f:
            self.assertEqual(json.load(f)["money"], {"real_usd": 0.0})

    def test_real_money_is_shown(self):
        import sqlite3
        import record_earning
        record_earning.connect(self.db).close()   # creates the ledger table
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO campaigns (timestamp, niche, content_title, clicks, earnings, status, source) "
                     "VALUES ('t','n','c',1,5.0,'DONE','real')")
        conn.commit(); conn.close()
        status = export_status.build_status(self.db)
        self.assertEqual(status["money"]["real_usd"], 5.0)

    def test_old_database_without_source_column(self):
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE campaigns (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, niche TEXT, "
                     "content_title TEXT, clicks INTEGER, earnings REAL, status TEXT)")
        conn.execute("INSERT INTO campaigns (timestamp, niche, content_title, clicks, earnings, status) "
                     "VALUES ('t', 'n', 'c', 10, 120.0, 'COMPLETED')")
        conn.commit(); conn.close()
        status = export_status.build_status(self.db)       # must migrate, not crash
        self.assertEqual(status["money"], {"real_usd": 0.0})   # old fake money is never shown

    def make_old_simulation_rows(self):
        import engine
        import record_earning
        store = engine.EnterpriseStorage(self.db)
        store.save_campaign("n", "fake campaign", 10, 142.75, "COMPLETED")
        store.log_agent_action("CMO-Growth-Agent", "Marketing", "OK", "fake log")
        store.log_discovery("x", "fake discovery", 120.0, "DISCOVERED")
        record_earning.add(4.5, "PAYOUT-1", "real sale", db_path=self.db)
        approval = guardrails.request_approval("public_draft", "Draft 1", "text", db_path=self.db)
        guardrails.decide(approval, True, self.db)
        return approval

    def count(self, table, where="1"):
        import sqlite3
        conn = sqlite3.connect(self.db)
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
        conn.close()
        return n

    def test_purge_removes_fake_rows_and_keeps_real_ones(self):
        approval = self.make_old_simulation_rows()
        removed = export_status.purge_simulated(self.db)
        self.assertEqual(removed, {"campaigns": 1, "agent_logs": 1, "research_discoveries": 1})
        self.assertEqual(self.count("campaigns"), 1)                    # the real sale stays
        self.assertEqual(self.count("campaigns", "source = 'real'"), 1)
        self.assertEqual(guardrails.get_approval(approval, self.db)["status"], "approved")   # untouched
        status = export_status.build_status(self.db)
        self.assertEqual(status["money"], {"real_usd": 4.5})
        self.assertEqual(status["logs"], [])
        self.assertEqual(export_status.purge_simulated(self.db), {})     # second run: nothing left

    def test_purge_on_an_empty_database(self):
        self.assertEqual(export_status.purge_simulated(self.db), {})

    def test_cloud_runner_purges_before_writing_the_dashboard(self):
        import cloud_runner
        from unittest.mock import patch
        self.make_old_simulation_rows()
        with patch.object(export_status.guardrails, "DB_PATH", self.db), \
                patch.object(cloud_runner.workforce, "run_once", lambda **k: {"status": "ok", "ran": 0, "failed": 0}), \
                patch.object(cloud_runner.export_status, "write_status", lambda: None):
            cloud_runner.main()
        self.assertEqual(self.count("campaigns", "source != 'real'"), 0)
        self.assertEqual(self.count("agent_logs"), 0)

    def test_dashboard_page_has_no_simulated_money(self):
        with open(os.path.join(os.path.dirname(export_status.__file__), "web", "index.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertNotIn("simulated_usd", page)
        self.assertNotIn("SIMULATION", page)

    def test_chat_answers_are_honest(self):
        status = export_status.build_status(self.db)
        answer = chat.reply("how much money did we earn?", status)
        self.assertIn("Real money earned so far: $0.00", answer)
        self.assertNotIn("imulated", answer)
        self.assertIn("No drafts", chat.reply("anything pending approval?", status))
        self.assertIn("Agents working now", chat.reply("what is the team doing", status))
        self.assertIn("Kill switch", chat.reply("stop everything", status))
        self.assertIn("Groq key", chat.reply("write me a poem", status, llm=lambda p: None))
        self.assertEqual(chat.reply("write me a poem", status, llm=lambda p: "A poem."), "A poem.")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        hdrs = dict(headers or {})
        if host:
            hdrs["Host"] = host
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_index_served(self):
        status, data = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Chilli Gavva Office", data)

    def test_path_traversal_blocked(self):
        status, _ = self.request("GET", "/../serve.py")
        self.assertEqual(status, 404)
        status, _ = self.request("GET", "/..%2fconfig.json")
        self.assertEqual(status, 404)

    def test_post_needs_custom_header(self):
        status, _ = self.request("POST", "/api/chat", {"message": "hi"})
        self.assertEqual(status, 403)

    def test_foreign_host_rejected(self):
        status, _ = self.request("GET", "/", host="evil.example.com")
        self.assertEqual(status, 403)
        status, _ = self.request("POST", "/api/kill", {}, {"X-Requested-With": "chilli"}, host="evil.example.com")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
