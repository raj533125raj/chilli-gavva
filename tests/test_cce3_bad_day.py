"""CCE-3: bad-day failures the simulator injects never crash the robot, give one owner line, and recover."""
import errno
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import guardrails


class DiskFullTests(unittest.TestCase):
    """Sim: disk full while writing product files -> OSError traceback in product_builder.write_index (red
    build step) and the draft's daily build slot was used up, so it waited a whole day."""

    def test_builder_main_does_not_crash_and_tells_the_owner(self):
        import product_builder as pb
        sent = []
        full = OSError(errno.ENOSPC, "No space left on device")
        with mock.patch.object(pb, "_build_all", side_effect=full), \
                mock.patch.object(pb.guardrails, "is_killed", return_value=False), \
                mock.patch.object(pb.guardrails, "automation_allowed", return_value=True), \
                mock.patch("cubes.get_cube_state", return_value="on"), \
                mock.patch("rest_window.in_rest_window", return_value=False), \
                mock.patch("notify.send", side_effect=lambda text, db_path=None: sent.append(text) or True):
            pb.main([])
        self.assertEqual(sent, [pb.DISK_FULL_MESSAGE])
        self.assertIn("disk full ayyindi", sent[0])

    def test_other_os_errors_still_fail_loudly(self):
        import product_builder as pb
        with mock.patch.object(pb, "_build_all", side_effect=PermissionError(errno.EACCES, "denied")), \
                mock.patch.object(pb.guardrails, "is_killed", return_value=False), \
                mock.patch.object(pb.guardrails, "automation_allowed", return_value=True), \
                mock.patch("cubes.get_cube_state", return_value="on"), \
                mock.patch("rest_window.in_rest_window", return_value=False):
            with self.assertRaises(PermissionError):
                pb.main([])

    def test_pipeline_gives_back_the_daily_slot_on_disk_full(self):
        import product_pipeline
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "a.db")
            aid = guardrails.request_approval("public_draft", "Draft: [Budget Sheets Agent] Sheet", "body",
                                              db_path=db)
            guardrails.decide(aid, True, db)
            full = OSError(errno.ENOSPC, "No space left on device")
            with mock.patch.object(product_pipeline, "_allowed", return_value=True), \
                    mock.patch("workforce.default_llm", return_value=lambda *a, **k: ""), \
                    mock.patch("format_registry.can_build", side_effect=full):
                with self.assertRaises(OSError):
                    product_pipeline.run_pending(db_path=db, out_dir=os.path.join(tmp, "p"), day="2026-11-16")
            conn = sqlite3.connect(db)
            attempts = conn.execute("SELECT COUNT(*) FROM product_delivery_attempts").fetchone()[0]
            status = conn.execute("SELECT status, lease_until FROM product_deliveries WHERE approval_id=?",
                                  (aid,)).fetchone()
            conn.close()
            self.assertEqual(attempts, 0)                 # the next run may try again today
            self.assertEqual(status, ("retry", 0))
            self.assertIn(aid, [item["id"] for item in product_pipeline.pending(db)])


def _world(sim_dir):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "tools" / "robot_sim_world.py"
    spec = importlib.util.spec_from_file_location("_cce3_world", path)
    world = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(world)
    world.SIM_DIR = sim_dir
    world.OWNER_CHAT = str(int("1234" "5678"))
    return world


class TelegramBusyTests(unittest.TestCase):
    """Sim: Telegram 429 (3x) then 500 (2x) -> 'Telegram setup check failed' traceback, red inbox step, and a
    'run FAILED' alert. Now the run is skipped quietly and the next healthy run sends ONE Tenglish line."""

    def setUp(self):
        import json
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "a.db")
        self.world = _world(self.tmp.name)
        self.json = json
        os.environ.setdefault("CHILLI_TEST", "1")

    def faults(self, rules):
        with open(os.path.join(self.tmp.name, "faults.json"), "w", encoding="utf-8") as handle:
            handle.write(self.json.dumps(rules))
        with open(os.path.join(self.tmp.name, "fault_counts.json"), "w", encoding="utf-8") as handle:
            handle.write("{}")

    def sent_texts(self):
        path = os.path.join(self.tmp.name, "telegram_out.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [self.json.loads(l)["text"] for l in handle if self.json.loads(l).get("ok")]

    def run_inbox(self):
        import telegram_inbox
        token = "123456789:" + "A" * 35
        with mock.patch("urllib.request.urlopen", side_effect=self.world.fake_urlopen), \
                mock.patch("notify._telegram_configured", return_value=(True, token, self.world.OWNER_CHAT)), \
                mock.patch("time.sleep"), \
                mock.patch("control_inbox.run", return_value={}):
            return telegram_inbox.run(self.db)

    def test_rate_limit_and_server_errors_skip_quietly_then_one_line(self):
        import telegram_inbox
        self.faults([{"service": "telegram", "status": 429, "times": 3, "retry_after": 0},
                     {"service": "telegram", "status": 500, "times": 2}])
        result = self.run_inbox()
        self.assertTrue(result["telegram_busy"])
        self.assertEqual(telegram_inbox.get_state(telegram_inbox.TELEGRAM_OUTAGE_STATE, db_path=self.db), "1")
        self.faults([])
        self.assertFalse(self.run_inbox().get("telegram_busy"))
        notices = [t for t in self.sent_texts() if "Telegram konchem sepu busy" in t]
        self.assertEqual(len(notices), 1)
        self.assertIn("1 run(s)", notices[0])
        self.run_inbox()
        self.assertEqual(len([t for t in self.sent_texts() if "Telegram konchem sepu busy" in t]), 1)

    def test_wrong_token_or_chat_still_fails_loudly(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.faults([{"service": "telegram", "status": status}])
                with self.assertRaisesRegex(RuntimeError, "setup check failed"):
                    self.run_inbox()

    def test_other_runtime_errors_are_not_hidden(self):
        import telegram_inbox
        self.faults([{"service": "telegram", "status": 429, "times": 1, "retry_after": 0}])
        with mock.patch.object(telegram_inbox, "process_updates", side_effect=RuntimeError("bug")):
            with self.assertRaisesRegex(RuntimeError, "bug"):
                self.run_inbox()


class AllAiDownTests(unittest.TestCase):
    """Sim: every AI provider answered 503 for two runs; scout/trend/marketing calls failed quietly and no draft
    task ran, so workforce's AI-down alert never fired: the owner heard nothing."""

    def test_router_counts_real_provider_answers(self):
        import io
        import urllib.error
        import urllib.request
        import llm_router
        req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=b"{}")
        fail = urllib.error.HTTPError(req.full_url, 503, "busy", {}, io.BytesIO(b"{}"))
        ok_body = io.BytesIO(b'{"choices": [{"message": {"content": "hello"}}]}')
        before = llm_router.process_outcomes()
        with mock.patch.object(llm_router.provider_state, "reserve", return_value=True), \
                mock.patch.object(llm_router.provider_state, "observe"), \
                mock.patch.object(llm_router, "_log_fail"), \
                mock.patch.object(llm_router.guardrails, "record_call"), \
                mock.patch("urllib.request.urlopen", side_effect=[fail, ok_body]):
            model = "openai/gpt-oss-20b"
            self.assertEqual(llm_router._request_json(req, "Groq", model, 5, llm_router._chat_parse)[0], None)
            self.assertEqual(llm_router._request_json(req, "Groq", model, 5, llm_router._chat_parse)[0], "hello")
        after = llm_router.process_outcomes()
        self.assertEqual((after["failed"] - before["failed"], after["ok"] - before["ok"]), (1, 1))

    def test_cloud_runner_tells_the_owner_only_when_nothing_answered(self):
        import cloud_runner
        import llm_router
        for outcome, told in (({"ok": 0, "failed": 18}, True), ({"ok": 2, "failed": 18}, False),
                              ({"ok": 0, "failed": 0}, False)):
            with self.subTest(outcome=outcome), \
                    mock.patch.dict(llm_router._PROCESS_OUTCOMES, outcome), \
                    mock.patch.object(cloud_runner.workforce, "_alert_ai_down") as alert:
                cloud_runner._tell_owner_if_ai_down()
            self.assertEqual(alert.called, told)

    def test_ai_down_message_is_tenglish_and_kept_once_a_day(self):
        import workforce
        self.assertIn("AI is down, will retry later", workforce.AI_DOWN_MESSAGE)
        self.assertIn("javabu ivvatledu", workforce.AI_DOWN_MESSAGE)
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "a.db")
            with mock.patch("notify.send", return_value=True) as send:
                self.assertTrue(workforce._alert_ai_down(db))
                self.assertFalse(workforce._alert_ai_down(db))
            send.assert_called_once_with(workforce.AI_DOWN_MESSAGE)


class DamagedReadyPackTests(unittest.TestCase):
    """Sim: a READY pack's PDF was replaced with garbage; nothing noticed and the pack stayed 'ready to upload'."""

    def _ready_pack(self, tmp, pdf_bytes):
        import json
        import product_pipeline
        db, out = os.path.join(tmp, "a.db"), os.path.join(tmp, "products")
        aid = guardrails.request_approval("public_draft", "Draft: [2027 Planners Agent] Planner", "body", db_path=db)
        guardrails.decide(aid, True, db)
        folder = f"{aid}-planner-2027"
        os.makedirs(os.path.join(out, folder))
        with open(os.path.join(out, folder, "planner-A4.pdf"), "wb") as handle:
            handle.write(pdf_bytes)
        with open(os.path.join(out, folder, "README.md"), "w", encoding="utf-8") as handle:
            handle.write("readme")
        with product_pipeline.connect(db) as conn:
            conn.execute("INSERT INTO product_deliveries (approval_id, status, folder, files, notified) "
                         "VALUES (?, 'ready', ?, ?, 1)", (aid, folder, json.dumps(["README.md", "planner-A4.pdf"])))
        return db, out, aid

    def test_damaged_pdf_takes_the_pack_back_and_tells_the_owner_once(self):
        import product_pipeline
        with tempfile.TemporaryDirectory() as tmp:
            db, out, aid = self._ready_pack(tmp, b"%PDF-1.4\n%garbage\x00\xff")
            sent = []
            self.assertEqual(product_pipeline.check_ready_files(db, out, send=sent.append), [(aid, "planner-A4.pdf")])
            self.assertEqual(len(sent), 1)
            self.assertIn(f"Pack #{aid} lo file paadayyindi (planner-A4.pdf)", sent[0])
            conn = sqlite3.connect(db)
            row = conn.execute("SELECT status, notified FROM product_deliveries WHERE approval_id=?", (aid,)).fetchone()
            conn.close()
            self.assertEqual(row, ("retry", 0))
            self.assertIn(aid, [item["id"] for item in product_pipeline.pending(db)])     # rebuilt by the pipeline
            import product_builder
            self.assertEqual(product_builder.builds(db)[0]["status"], "retry")   # fresh build, not a re-pack
            self.assertEqual(product_pipeline.check_ready_files(db, out, send=sent.append), [])
            self.assertEqual(len(sent), 1)

    def test_good_pack_is_left_alone(self):
        import product_pipeline
        with tempfile.TemporaryDirectory() as tmp:
            db, out, aid = self._ready_pack(tmp, b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
            sent = []
            self.assertEqual(product_pipeline.check_ready_files(db, out, send=sent.append), [])
            self.assertEqual(sent, [])
            self.assertNotIn(aid, [item["id"] for item in product_pipeline.pending(db)])

    def test_damaged_file_rules(self):
        import zipfile
        import product_pipeline
        with tempfile.TemporaryDirectory() as tmp:
            good_zip, bad_zip = os.path.join(tmp, "a.xlsx"), os.path.join(tmp, "b.docx")
            with zipfile.ZipFile(good_zip, "w") as archive:
                archive.writestr("x.xml", "<x/>")
            with open(bad_zip, "wb") as handle:
                handle.write(b"PK\x03\x04 half written")
            self.assertEqual(product_pipeline.damaged_file(good_zip), "")
            self.assertEqual(product_pipeline.damaged_file(bad_zip), "broken file")
            self.assertEqual(product_pipeline.damaged_file(os.path.join(tmp, "notes.txt")), "")


if __name__ == "__main__":
    unittest.main()
