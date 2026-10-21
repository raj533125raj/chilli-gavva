"""migrate_db.sh: encrypts, checks, and commits only the encrypted database. Temp git repo, made-up test key."""
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_KEY = "unit-test-only-key-456"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t"] + list(args), cwd=cwd,
                          check=True, capture_output=True, text=True)


@unittest.skipUnless(shutil.which("openssl") and shutil.which("bash"), "needs openssl and bash")
class MigrateDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        git(self.dir, "init", "-q")
        os.makedirs(os.path.join(self.dir, "scripts"))
        for name in ("migrate_db.sh", "db_crypt.py"):
            shutil.copy(os.path.join(REPO, "scripts", name), os.path.join(self.dir, "scripts", name))
        conn = sqlite3.connect(os.path.join(self.dir, "agent_data.db"))
        conn.execute("CREATE TABLE t (x TEXT)")
        conn.execute("INSERT INTO t VALUES ('row')")
        conn.commit()
        conn.close()
        with open(os.path.join(self.dir, ".gitignore"), "w") as f:
            f.write("agent_data.db\n")
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "base")

    def run_migrate(self, key):
        return subprocess.run(["bash", "scripts/migrate_db.sh"], cwd=self.dir, input=key + "\n",
                              capture_output=True, text=True)

    def test_encrypts_checks_and_commits_only_the_encrypted_file(self):
        result = self.run_migrate(TEST_KEY)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(TEST_KEY, result.stdout + result.stderr, "the key is never printed")
        last = git(self.dir, "log", "-1", "--format=%s").stdout.strip()
        self.assertEqual(last, "Encrypt agent database")
        files = git(self.dir, "show", "--name-only", "--format=", "HEAD").stdout.split()
        self.assertEqual(files, ["agent_data.db.enc"])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "agent_data.db.enc")))

    def test_empty_key_stops_before_anything_is_written(self):
        result = self.run_migrate("")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_KEY is empty", result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "agent_data.db.enc")))
        self.assertEqual(git(self.dir, "log", "--oneline").stdout.count("\n"), 1, "no commit was made")

    def test_missing_database_stops(self):
        os.remove(os.path.join(self.dir, "agent_data.db"))
        result = self.run_migrate(TEST_KEY)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("agent_data.db not found", result.stdout)

    def test_existing_encrypted_file_stops(self):
        open(os.path.join(self.dir, "agent_data.db.enc"), "wb").close()
        result = self.run_migrate(TEST_KEY)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stdout)


if __name__ == "__main__":
    unittest.main()
