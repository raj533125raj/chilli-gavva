"""Offline regressions for secret-bearing errors and both save retry branches."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote

import doctor
import engine
import export_status
import llm_router
import media_brain
import product_builder
import sanitize_state
import secret_guard
import workforce

ROOT = Path(__file__).resolve().parents[1]


class StorageSafetyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = str(self.root / "agent_data.db")
        self.key = "AIza" + "q" * 35
        self.second = "fixture/second+credential"
        patch = mock.patch.dict(os.environ, {"GEMINI_API_KEY_2": self.second}, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.error = ("request failed https://example.invalid/model?key=" + self.key
                      + "&alt=json; ?key=unrecognized-credential " + self.second)

    def assert_safe(self, value):
        for private in (self.key, self.second, "unrecognized-credential"):
            self.assertNotIn(private, value)
        self.assertEqual(secret_guard.scan_text(value), [])

    def test_shared_scrubber_handles_query_keys_encoding_json_and_full_private_key(self):
        raw = (self.error + " " + quote(self.second, safe="")
               + '\n{"password": "fixture-password-value", "chat_id": "123456789"}'
               + "\n-----BEGIN RSA " + "PRIVATE KEY-----\nfixture-private-body\n-----END RSA " + "PRIVATE KEY-----")
        safe = secret_guard.scrub_text(raw)
        self.assert_safe(safe)
        self.assertNotIn(quote(self.second, safe=""), safe)
        self.assertNotIn("fixture-private-body", safe)
        self.assertNotIn("fixture-password-value", safe)
        self.assertNotIn("123456789", safe)
        self.assertIn("&alt=json", safe)
        self.assertEqual(secret_guard.scrub_text(safe), safe)
        structured = secret_guard.scrub_stored_text(json.dumps({"error": raw, "n": 7}))
        self.assertEqual(json.loads(structured)["n"], 7)
        self.assert_safe(structured)

    def test_chat_id_inside_bot_token_does_not_leave_a_credential_suffix(self):
        raw = "1234567890:" + "A" * 35
        safe = secret_guard.scrub_text(raw, env={"TELEGRAM_CHAT_ID": "123456789"})
        self.assertNotIn("A" * 10, safe)
        self.assertEqual(safe, "[hidden]")

    def test_task_errors_results_heartbeat_and_product_notes_are_scrubbed_before_truncation(self):
        task = workforce.enqueue("research", {}, db_path=self.db)
        with mock.patch.object(doctor, "diagnose_and_remedy"):
            workforce.fail(task, 1, self.error, self.db)
        workforce.heartbeat(self.error, self.db)
        workforce.complete(task, {"error": self.error}, self.db)
        product_builder._record(self.db, 1, "needs_fix", note=self.error)
        with sqlite3.connect(self.db) as conn:
            texts = [conn.execute("SELECT result FROM tasks").fetchone()[0],
                     conn.execute("SELECT info FROM heartbeat").fetchone()[0],
                     conn.execute("SELECT note FROM product_builds").fetchone()[0]]
        for text in texts:
            self.assert_safe(text)
        # Failed tasks retain a safe error even when the doctor is unavailable.
        with mock.patch.object(doctor, "diagnose_and_remedy", side_effect=RuntimeError):
            workforce.fail(task, 1, self.error, self.db)
        with sqlite3.connect(self.db) as conn:
            self.assert_safe(conn.execute("SELECT error FROM tasks").fetchone()[0])

    def test_doctor_media_and_provider_log_share_scrubbing(self):
        facts = doctor.collect_facts("agent", "step", RuntimeError(self.error), last_5_actions=[self.error])
        doctor.record_case(facts, "code_error", self.error, self.error, "open", db_path=self.db)
        media_brain._format_and_save("document", self.error, [self.error], self.error,
                                     raw_text=self.error, db_path=self.db)
        store = engine.EnterpriseStorage(self.db)
        store.log_agent_action("agent", "department", "failed", self.error)
        with contextlib.redirect_stderr(io.StringIO()) as output:
            llm_router._log_fail("Gemini", "model", self.error)
        self.assert_safe(output.getvalue())
        self.assertEqual(secret_guard.scan_file(self.db), [])

    def test_old_rows_and_status_are_repaired_without_losing_state_or_json(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE old_logs (id INTEGER PRIMARY KEY, error TEXT, result TEXT)")
            conn.execute("INSERT INTO old_logs VALUES (1, ?, ?)",
                         (self.error, json.dumps({"message": self.error, "count": 3})))
            conn.execute("CREATE TABLE telegram_state (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO telegram_state VALUES ('update_offset', '42')")
        path = self.root / "status.json"
        path.write_text(json.dumps({"logs": [self.error], "count": 8}))
        self.assertTrue(secret_guard.scan_file(self.db))
        self.assertEqual(sanitize_state.sanitize_database(self.db), 1)
        self.assertTrue(sanitize_state.sanitize_status(path))
        self.assertEqual(sanitize_state.sanitize_database(self.db), 0)
        self.assertFalse(sanitize_state.sanitize_status(path))
        self.assertEqual(secret_guard.scan_file(self.db), [])
        self.assert_safe(path.read_text())
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT value FROM telegram_state").fetchone()[0], "42")
            self.assertEqual(json.loads(conn.execute("SELECT result FROM old_logs").fetchone()[0])["count"], 3)

    def test_status_export_scrubs_nested_text_from_legacy_rows(self):
        path = self.root / "status.json"
        with mock.patch.object(export_status, "build_status", return_value={"generated_at": "now", "logs": [self.error]}), \
                mock.patch.object(export_status, "_ai_health", return_value=[]):
            export_status.write_status(str(path), self.db)
        self.assert_safe(path.read_text())


class PushStateTests(unittest.TestCase):
    def run_script(self, **settings):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "git"
            fake.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ['FAKE_GIT_ROOT'])
cfg = json.loads((root / 'config.json').read_text())
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as f:
    f.write(json.dumps(args) + '\\n')
cmd = args[0]
if cmd == 'rev-parse':
    print('base' if args[1] == 'HEAD~1' else cfg.get('remote', 'moved'))
elif cmd == 'push':
    count = root / 'pushes'
    n = int(count.read_text()) if count.exists() else 0
    count.write_text(str(n + 1))
    sys.exit(0 if n >= cfg.get('fail_pushes', 0) else 1)
elif cmd == 'fetch':
    sys.exit(cfg.get('fetch_rc', 0))
elif cmd == 'diff':
    sys.exit(cfg.get('state_changed', 0))
elif cmd == 'merge-base':
    sys.exit(cfg.get('history_rc', 0))
elif cmd == 'rebase' and '--abort' not in args:
    sys.exit(cfg.get('rebase_rc', 0))
''')
            fake.chmod(0o755)
            (root / "config.json").write_text(json.dumps(settings))
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"], FAKE_GIT_ROOT=str(root))
            result = subprocess.run(["bash", str(ROOT / "scripts/push_state.sh"), "agent_data.db.enc", "web/data/status.json"],
                                    cwd=root, env=env, capture_output=True, text=True)
            calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
        return result, calls

    def test_success_needs_no_fetch(self):
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0)
        self.assertEqual([c[0] for c in calls], ["rev-parse", "push"])

    def test_code_only_remote_change_rebases_and_retries(self):
        result, calls = self.run_script(fail_pushes=1)
        self.assertEqual(result.returncode, 0)
        self.assertIn(["fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main"], calls)
        self.assertEqual(sum(c[0] == "push" for c in calls), 2)
        self.assertIn(["rebase", "-q", "origin/main"], calls)
        self.assertFalse(any("--force" in c for c in calls))

    def test_concurrent_state_writer_is_never_overwritten(self):
        result, calls = self.run_script(fail_pushes=1, state_changed=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not overwriting", result.stdout)
        self.assertFalse(any(c[0] == "rebase" for c in calls))

    def test_unmoved_main_reports_branch_rules_or_permissions(self):
        result, calls = self.run_script(fail_pushes=2, remote="base")
        self.assertEqual(result.returncode, 1)
        self.assertIn("permissions or branch rules", result.stdout)
        self.assertEqual(sum(c[0] == "push" for c in calls), 1)

    def test_fetch_history_rebase_and_second_push_failures_stop(self):
        for config in ({"fetch_rc": 1}, {"history_rc": 1}, {"rebase_rc": 1}, {"fail_pushes": 2}):
            with self.subTest(config=config):
                result, calls = self.run_script(**({"fail_pushes": 1} | config))
                self.assertNotEqual(result.returncode, 0)
                if config.get("rebase_rc"):
                    self.assertIn(["rebase", "--abort"], calls)


if __name__ == "__main__":
    unittest.main()
