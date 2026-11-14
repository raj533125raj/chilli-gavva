"""CC2-2: tests never write into the repo folder.

Runs the test modules that used to leave files in the repo (post plan, lint notes, pack versions,
weekly newsletter / duplicate report) in a child process and checks that no file in the repo was
created, changed or deleted. The snapshot helper itself is tested first, so a clean result means
"nothing was written", not "the check cannot see".
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".jobs", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
# Modules that wrote into the repo before CC1-4 / CC2-2, and what they used to leave behind.
KNOWN_WRITERS = {
    "tests.test_s4_telegram": "exported_content/post_plan.json",
    "tests.test_fb22_weekly_edges": "exported_content/marketing/ (newsletter, dup report, post plan)",
    "tests.test_v1a_plugins": "exported_content/post_plan.json",
    "tests.test_w5c_week_sim": "exported_content/post_plan.json, exported_content/products/versions/",
    "tests.test_o17_lint_fix": "exported_content/products/lint/",
}


def snapshot(root: Path) -> dict:
    """{relative path: (mtime_ns, size)} for every file under root (caches and .git left out)."""
    found = {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                continue
            path = os.path.join(folder, name)
            try:
                info = os.lstat(path)
            except OSError:
                continue
            found[os.path.relpath(path, root)] = (info.st_mtime_ns, info.st_size)
    return found


def changes(before: dict, after: dict) -> list:
    """Readable list of created / changed / deleted files between two snapshots."""
    lines = [f"created: {path}" for path in sorted(after) if path not in before]
    lines += [f"changed: {path}" for path in sorted(after) if path in before and after[path] != before[path]]
    lines += [f"deleted: {path}" for path in sorted(before) if path not in after]
    return lines


def run_module(module: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CHILLI_TEST"] = "1"
    extra = [str(REPO_ROOT), str(REPO_ROOT / "tests")]
    env["PYTHONPATH"] = os.pathsep.join(extra + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return subprocess.run([sys.executable, "-m", "unittest", module], cwd=str(REPO_ROOT), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=240, check=False)


class SnapshotHelperTests(unittest.TestCase):
    """The check must really see new, changed and deleted files."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "exported_content").mkdir()
        (self.root / "exported_content" / "keep.json").write_text("{}", encoding="utf-8")
        (self.root / "web").mkdir()
        (self.root / "web" / "status.json").write_text("{}", encoding="utf-8")
        self.before = snapshot(self.root)

    def test_untouched_folder_has_no_changes(self):
        self.assertEqual(changes(self.before, snapshot(self.root)), [])

    def test_created_file_is_seen(self):
        (self.root / "exported_content" / "post_plan.json").write_text("[]", encoding="utf-8")
        self.assertEqual(changes(self.before, snapshot(self.root)),
                         ["created: " + os.path.join("exported_content", "post_plan.json")])

    def test_created_file_in_new_subfolder_is_seen(self):
        (self.root / "tests" / "out").mkdir(parents=True)
        (self.root / "tests" / "out" / "x.txt").write_text("x", encoding="utf-8")
        self.assertEqual(changes(self.before, snapshot(self.root)),
                         ["created: " + os.path.join("tests", "out", "x.txt")])

    def test_changed_file_is_seen(self):
        (self.root / "web" / "status.json").write_text('{"changed": true}', encoding="utf-8")
        self.assertEqual(changes(self.before, snapshot(self.root)),
                         ["changed: " + os.path.join("web", "status.json")])

    def test_rewritten_file_with_same_size_is_seen(self):
        path = self.root / "web" / "status.json"
        info = path.stat()
        path.write_text("[]", encoding="utf-8")          # same size as "{}"
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 5_000_000_000))
        self.assertEqual(changes(self.before, snapshot(self.root)),
                         ["changed: " + os.path.join("web", "status.json")])

    def test_deleted_file_is_seen(self):
        (self.root / "exported_content" / "keep.json").unlink()
        self.assertEqual(changes(self.before, snapshot(self.root)),
                         ["deleted: " + os.path.join("exported_content", "keep.json")])

    def test_repo_root_file_is_seen(self):
        (self.root / "agent_data.db").write_bytes(b"x")
        self.assertEqual(changes(self.before, snapshot(self.root)), ["created: agent_data.db"])

    def test_python_caches_and_git_are_ignored(self):
        for folder in ("__pycache__", ".git", os.path.join("tests", "__pycache__")):
            (self.root / folder).mkdir(parents=True)
            (self.root / folder / "junk.bin").write_bytes(b"x")
        (self.root / "module.pyc").write_bytes(b"x")
        self.assertEqual(changes(self.before, snapshot(self.root)), [])


class RepoStaysCleanTests(unittest.TestCase):
    """Each known writer runs for real; the repo must look exactly the same afterwards."""

    def check(self, module):
        self.assertTrue((REPO_ROOT / (module.replace(".", os.sep) + ".py")).is_file(), module)
        before = snapshot(REPO_ROOT)
        result = run_module(module)
        after = snapshot(REPO_ROOT)
        self.assertEqual(result.returncode, 0, f"{module} failed:\n{result.stdout[-2000:]}")
        self.assertRegex(result.stdout, r"Ran [1-9]\d* tests? in")
        own = os.path.join("tests", "test_cc2_repo_clean.py")
        found = [line for line in changes(before, after) if not line.endswith(own)]
        self.assertEqual(found, [], f"{module} wrote into the repo (it used to leave: {KNOWN_WRITERS[module]})")

    def test_s4_telegram_leaves_no_post_plan(self):
        self.check("tests.test_s4_telegram")

    def test_fb22_weekly_edges_leaves_marketing_folder_alone(self):
        self.check("tests.test_fb22_weekly_edges")

    def test_v1a_plugins_leaves_no_post_plan(self):
        self.check("tests.test_v1a_plugins")

    def test_w5c_week_sim_leaves_no_plan_or_versions(self):
        self.check("tests.test_w5c_week_sim")

    def test_o17_lint_fix_leaves_no_lint_notes(self):
        self.check("tests.test_o17_lint_fix")

    def test_every_known_writer_has_a_test(self):
        tested = {"tests.test_s4_telegram", "tests.test_fb22_weekly_edges", "tests.test_v1a_plugins",
                  "tests.test_w5c_week_sim", "tests.test_o17_lint_fix"}
        self.assertEqual(set(KNOWN_WRITERS), tested)

    def test_relative_post_plan_default_is_why_cwd_matters(self):
        """The post plan default is a relative path, so it lands wherever the process runs from."""
        import post_planner
        self.assertFalse(os.path.isabs(post_planner.PLAN_PATH))
        self.assertEqual(Path(post_planner.PLAN_PATH).name, "post_plan.json")


if __name__ == "__main__":
    unittest.main()
