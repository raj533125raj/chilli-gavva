"""Tests for save_check.py: save only when something real changed (temp git repos, offline)."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts"))

import save_check  # noqa: E402

TEST_KEY = "unit-test-only-key-123"


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t"] + list(args), cwd=cwd, check=True,
                   capture_output=True)


class SaveCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        git(self.dir, "init", "-q")
        self.db = os.path.join(self.dir, "agent_data.db")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE approvals (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO approvals (title) VALUES ('one')")
        conn.commit()
        conn.close()
        os.makedirs(os.path.join(self.dir, "web", "data"))
        self.write_status("2026-10-15T10:00:00", 3)
        # Production commits the database encrypted: the committed copy is agent_data.db.enc, plaintext is ignored.
        os.makedirs(os.path.join(self.dir, "scripts"))
        shutil.copy(os.path.join(REPO, "scripts", "db_crypt.py"), os.path.join(self.dir, "scripts", "db_crypt.py"))
        self._old_key = os.environ.get("DB_KEY")
        os.environ["DB_KEY"] = TEST_KEY
        self.addCleanup(self._restore_key)
        import db_crypt
        db_crypt.encrypt(self.db, os.path.join(self.dir, "agent_data.db.enc"))
        with open(os.path.join(self.dir, ".gitignore"), "w") as f:
            f.write("agent_data.db\n")
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "base")

    def _restore_key(self):
        if self._old_key is None:
            os.environ.pop("DB_KEY", None)
        else:
            os.environ["DB_KEY"] = self._old_key

    def write_status(self, ts, done, age=5):
        with open(os.path.join(self.dir, "web", "data", "status.json"), "w") as f:
            json.dump({"generated_at": ts, "worker": {"age_seconds": age}, "task_counts": {"done": done}}, f)

    def test_quiet_run_is_not_saved(self):
        conn = sqlite3.connect(self.db)
        conn.execute("VACUUM")                       # same rows, different file bytes
        conn.close()
        self.write_status("2026-10-15T10:30:00", 3, age=900)
        self.assertEqual(save_check.real_changes(self.dir), [])
        self.assertEqual(save_check.main(self.dir), 3)

    def test_new_row_is_saved(self):
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO approvals (title) VALUES ('two')")
        conn.commit()
        conn.close()
        self.assertEqual(save_check.real_changes(self.dir), ["database rows"])
        self.assertEqual(save_check.main(self.dir), 0)

    def test_dashboard_numbers_are_saved(self):
        self.write_status("2026-10-15T10:30:00", 4)
        self.assertEqual(save_check.real_changes(self.dir), ["dashboard numbers"])

    def test_new_encrypted_products_bundle_is_saved(self):
        with open(os.path.join(self.dir, "products.tar.gz.enc"), "wb") as f:
            f.write(b"fake encrypted bundle")
        self.assertEqual(save_check.real_changes(self.dir), ["product or lock files"])

    def test_problems_count_as_changed(self):
        with open(self.db, "wb") as f:
            f.write(b"not a database")
        reasons = save_check.real_changes(self.dir)
        self.assertTrue(reasons and "could not compare" in reasons[0])
        self.assertEqual(save_check.main(self.dir), 0)

    def test_workflow_uses_the_check(self):
        with open(os.path.join(os.path.dirname(save_check.__file__), ".github", "workflows", "agent.yml")) as f:
            text = f.read()
        save_step = text.split("- name: Save state")[1]
        self.assertLess(save_step.index("python3 save_check.py"), save_step.index("git add"))
        self.assertIn('if [ "$rc" = "3" ]', save_step)


class TelegramStateIsSavedTests(unittest.TestCase):
    """The bot must never answer the same command twice: Telegram memory always counts as a real change."""

    def setUp(self):
        import datetime
        from unittest import mock
        import daily_report
        import guardrails
        import telegram_inbox as ti
        import workforce
        self.mock, self.dt, self.dr, self.g, self.ti, self.wf = mock, datetime, daily_report, guardrails, ti, workforce
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.db = os.path.join(self.dir, "agent_data.db")
        guardrails.request_approval("public_draft", "Draft: [ADHD Planners Agent] x", "body", db_path=self.db)
        ti.set_state("update_offset", 100, self.db)
        ti.set_state("last_alerted_id", 1, self.db)
        workforce.connect(self.db).close()
        git(self.dir, "init", "-q")
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "base")
        patches = [mock.patch.object(ti, "send_text", return_value=True),
                   mock.patch.object(ti, "_call", return_value={"ok": True})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def updates(self, items):
        with self.mock.patch.object(self.ti, "fetch_updates", return_value=items):
            self.ti.process_updates("tok", "111", self.db)

    def test_quiet_run_skips(self):
        self.updates([])
        self.ti.alert_new_drafts("tok", "111", self.db)
        self.assertEqual(save_check.real_changes(self.dir), [])

    def test_every_telegram_action_is_saved(self):
        actions = {
            "command": lambda: self.updates([{"update_id": 101, "message": {"chat": {"id": 111}, "text": "/list"}}]),
            "stranger": lambda: self.updates([{"update_id": 102, "message": {"chat": {"id": 9}, "text": "/pause"}}]),
            "button": lambda: self.updates([{"update_id": 103, "callback_query": {
                "id": "q", "data": "v:1", "from": {"id": 111}, "message": {"chat": {"id": 111}}}}]),
            "draft alert": lambda: (self.g.request_approval("public_draft", "Draft: new", "b", db_path=self.db),
                                    git(self.dir, "commit", "-qam", "draft"),
                                    self.ti.alert_new_drafts("tok", "111", self.db)),
            "daily report": lambda: self.dr.maybe_send(lambda t: True, self.ti.get_state, self.ti.set_state, self.db,
                                                       now=self.dt.datetime(2026, 10, 16, 4, 0),
                                                       status_fn=lambda: {}),
            "ai-down alert": lambda: self.mock.patch("notify.send", return_value=True).start()
            and self.wf._alert_ai_down(self.db),
        }
        for name, act in actions.items():
            with self.subTest(action=name):
                act()
                self.assertIn("database rows", save_check.real_changes(self.dir))
                git(self.dir, "checkout", "-q", "--", ".")
        self.mock.patch.stopall()


if __name__ == "__main__":
    unittest.main()
