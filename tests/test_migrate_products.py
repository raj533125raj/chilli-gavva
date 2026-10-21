"""The product migration is exercised only in a temporary git repo with a fake key."""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_KEY = "made-up-products-test-key"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=fixture-owner", "-c", "user.email=fixture@example.org"]
                          + list(args), cwd=cwd, check=True, capture_output=True, text=True)


@unittest.skipUnless(shutil.which("openssl") and shutil.which("bash"), "needs openssl and bash")
class MigrateProductsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.tmp.name
        git(self.repo, "init", "-q")
        os.makedirs(os.path.join(self.repo, "scripts"))
        for name in ("migrate_products.sh", "db_crypt.py"):
            shutil.copy(os.path.join(REPO, "scripts", name), os.path.join(self.repo, "scripts", name))
        os.makedirs(os.path.join(self.repo, "exported_content", "products", "fixture"))
        with open(os.path.join(self.repo, "exported_content", "products", "fixture", "sample.txt"), "wb") as f:
            f.write(b"fake fixture content")
        with open(os.path.join(self.repo, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("exported_content/products/\n__pycache__/\n")
        git(self.repo, "add", "scripts", ".gitignore")
        git(self.repo, "add", "-f", "exported_content/products")
        git(self.repo, "commit", "-qm", "base")

    def run_migrate(self, key):
        env = dict(os.environ)
        env.pop("DB_KEY", None)
        env.update(GIT_AUTHOR_NAME="fixture-owner", GIT_AUTHOR_EMAIL="fixture@example.org",
                   GIT_COMMITTER_NAME="fixture-owner", GIT_COMMITTER_EMAIL="fixture@example.org",
                   PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(["bash", "scripts/migrate_products.sh"], cwd=self.repo, input=key + "\n",
                              capture_output=True, text=True, env=env)

    def test_verifies_bundle_and_commits_only_encrypted_products(self):
        result = self.run_migrate(TEST_KEY)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(TEST_KEY, result.stdout + result.stderr)
        self.assertIn("bundle verified", result.stdout)
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s").stdout.strip(), "Encrypt product files")
        changed = git(self.repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.splitlines()
        self.assertIn("products.tar.gz.enc", changed)
        added = git(self.repo, "diff-tree", "--no-commit-id", "--name-only", "--diff-filter=A", "-r", "HEAD").stdout.splitlines()
        self.assertFalse(any(path.startswith("exported_content/products/") for path in added))
        tracked = git(self.repo, "ls-files", "exported_content/products").stdout.splitlines()
        self.assertEqual(tracked, [])
        self.assertTrue(os.path.exists(os.path.join(self.repo, "exported_content", "products", "fixture", "sample.txt")))
        self.assertEqual(git(self.repo, "status", "--porcelain").stdout, "")

    def test_empty_key_stops_before_bundle_or_commit(self):
        result = self.run_migrate("")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_KEY is empty", result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "products.tar.gz.enc")))
        self.assertEqual(git(self.repo, "log", "--oneline").stdout.count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
