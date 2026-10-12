import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guardrails  # noqa: E402
import report  # noqa: E402
import workforce  # noqa: E402


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "r.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_counts(self):
        guardrails.request_approval("public_draft", "t", "body", db_path=self.db)
        guardrails.request_approval("code_patch", "p", "diff", db_path=self.db)
        workforce.enqueue("plan", {}, db_path=self.db)
        text = report.build(self.db)
        self.assertIn("2 waiting", text)
        self.assertIn("Code patches waiting for you: 1", text)
        self.assertIn("1 queued", text)
        self.assertIn("Real earnings (with proof): $0.00", text)

    def test_written_once_per_day(self):
        d = os.path.join(self.tmp.name, "reports")
        self.assertIsNotNone(report.write_daily(self.db, d, "2026-10-13"))
        self.assertIsNone(report.write_daily(self.db, d, "2026-10-13"))

    def test_ollama_off_without_config(self):
        import engine
        with mock.patch.object(engine, "load_config", return_value={}):
            self.assertIsNone(engine.AutonomousResearchEngine.generate_with_ollama("hi"))

    def test_ollama_reply(self):
        import engine, io, json
        resp = io.BytesIO(json.dumps({"response": " hello "}).encode())
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *a: False
        with mock.patch.object(engine, "load_config", return_value={"ollama_model": "llama3"}), \
             mock.patch.object(engine.guardrails, "can_call_llm", return_value=True), \
             mock.patch.object(engine.guardrails, "record_call"), \
             mock.patch("urllib.request.urlopen", return_value=resp):
            self.assertEqual(engine.AutonomousResearchEngine.generate_with_ollama("hi"), "hello")


if __name__ == "__main__":
    unittest.main()
