"""Tests for save_check.py: save only when something real changed (temp git repos, offline)."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import save_check  # noqa: E402


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
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "base")

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

    def test_new_product_file_is_saved(self):
        os.makedirs(os.path.join(self.dir, "exported_content", "products", "9-x"))
        with open(os.path.join(self.dir, "exported_content", "products", "9-x", "cover.png"), "wb") as f:
            f.write(b"png")
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


if __name__ == "__main__":
    unittest.main()
