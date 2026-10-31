"""The live-state boundary holds with CHILLI_TEST absent and pre-existing failures."""
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import guardrails
import llm_provider_state as state
import llm_router as router

ROOT = Path(__file__).resolve().parents[1]


class RouterTestIsolationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "agent_data.db"
        # Seed through SQLite directly: this represents an already decrypted
        # runtime database, not test provider activity.
        today = router.datetime.datetime.now(router.datetime.timezone.utc).date().isoformat()
        data = dict(day=today, calls=500, kind="timeout", streak=3,
                    until=time.time() + 86400, probe_until=0, last_kind="timeout",
                    failures={"timeout": 3}, recent={})
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE llm_model_health (provider TEXT, model TEXT, data TEXT, PRIMARY KEY(provider,model))")
            conn.executemany("INSERT INTO llm_model_health VALUES (?, ?, ?)",
                             [(provider, model, json.dumps(data)) for provider, model in
                              [("Groq", "small-m"), ("Groq", "large-m"), ("Gemini", "gem-a"), ("Gemini", "gem-b")]])
        self.before = self.db.read_bytes()

    def test_health_suite_twice_with_existing_live_breakers_is_repeatable_and_read_only(self):
        script = """
import io, pathlib, sys, unittest
import guardrails, llm_provider_state
guardrails.DB_PATH = sys.argv[1]
llm_provider_state._REAL_DB = pathlib.Path(sys.argv[1])
for run in range(2):
    output = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName('tests.test_llm_health')
    result = unittest.TextTestRunner(stream=output).run(suite)
    if not result.wasSuccessful():
        print(output.getvalue())
        raise SystemExit(1)
"""
        env = dict(os.environ)
        env.pop("CHILLI_TEST", None)
        child = subprocess.run([sys.executable, "-c", script, str(self.db)], cwd=ROOT,
                               env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertEqual(self.db.read_bytes(), self.before)

    def test_plain_unittest_fallback_protects_default_explicit_and_direct_state_paths(self):
        memory = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(memory.close)
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fixture-key", "GEMINI_MODEL": "gem-a"}, clear=True), \
             patch.object(guardrails, "_TESTING", False), \
             patch.object(guardrails, "DB_PATH", str(self.db)), \
             patch.object(state, "_REAL_DB", self.db), \
             patch.object(state, "_memory", memory), \
             patch.object(router, "_MEMORY_STATE", memory), \
             patch.object(router, "_MEM_COOLDOWN", {}), \
             patch("urllib.request.urlopen", side_effect=socket.timeout("fixture timeout")) as network, \
             redirect_stderr(io.StringIO()):
            self.assertTrue(state.running_tests())
            self.assertIs(router._state_path(), memory)
            # A private transport call must isolate global budget accounting too.
            router._gemini("fixture")
            self.assertEqual(network.call_count, 1)
            router.chat("fixture")
            router.chat_vision(b"fixture", "image/png", db_path=str(self.db))
            router.chat_audio(b"fixture", "audio/wav", db_path=str(self.db))
            router.health(db_path=str(self.db))
            router.set_cooldown("Gemini", db_path=str(self.db))
            router.log_provider("Gemini", False, db_path=str(self.db))
            self.assertTrue(state.reserve("Groq", "small-m", 10, str(self.db)))
            state.observe("Groq", "small-m", False, "timeout", path=str(self.db))
            self.assertTrue(state.snapshots(str(self.db)))
        self.assertEqual(self.db.read_bytes(), self.before)

    def test_plain_unittest_default_guardrails_database_is_disposable(self):
        script = """
import pathlib, sys, tempfile, unittest
import guardrails
runtime = pathlib.Path(guardrails.__file__).with_name('agent_data.db').resolve()
assert pathlib.Path(guardrails.DB_PATH).resolve() != runtime
assert pathlib.Path(guardrails.DB_PATH).resolve().is_relative_to(pathlib.Path(tempfile.gettempdir()).resolve())
conn = guardrails.connect()
conn.execute("INSERT INTO control VALUES ('fixture', 'test-only')")
conn.commit()
conn.close()
assert pathlib.Path(guardrails.DB_PATH).is_file()
import agent_memory
assert agent_memory.DB_DEFAULT == guardrails.DB_PATH
agent_memory.add_fact('fixture', 'fixture', 'test-only')
"""
        env = dict(os.environ)
        env.pop("CHILLI_TEST", None)
        child = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)

    def test_production_default_and_explicit_temp_databases_keep_persistent_state(self):
        with patch.object(state, "running_tests", return_value=False), \
             patch.object(guardrails, "_TESTING", False), \
             patch.object(guardrails, "DB_PATH", str(self.db)), \
             patch.object(state, "_REAL_DB", self.db):
            self.assertEqual(router._state_path(), str(self.db))
            self.assertFalse(state.reserve("Groq", "small-m", 10, str(self.db)))
        fixture = str(self.db.with_name("explicit-fixture.db"))
        self.assertTrue(state.reserve("Groq", "fixture-model", 1, fixture))
        self.assertFalse(state.reserve("Groq", "fixture-model", 1, fixture))
        self.assertTrue(Path(fixture).is_file())
