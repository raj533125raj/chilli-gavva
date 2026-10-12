import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import coder  # noqa: E402
import guardrails  # noqa: E402
import improver  # noqa: E402
import workforce  # noqa: E402

GOOD = """diff --git a/util.py b/util.py
--- a/util.py
+++ b/util.py
@@ -1 +1,2 @@
 x = 1
+y = 2
"""


def make_diff(path, old="x", new="y"):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n"


class ValidateTests(unittest.TestCase):
    def test_good_patch_ok(self):
        self.assertTrue(coder.validate_patch(GOOD, "/nonexistent")[0])

    def test_protected_files_blocked(self):
        for name in ["guardrails.py", "improver.py", "serve.py", "coder.py", "worker_daemon.py",
                     "config.json", ".github/workflows/agent.yml"]:
            ok, why = coder.validate_patch(make_diff(name), "/nonexistent")
            self.assertFalse(ok, name)
            self.assertIn("protected", why)

    def test_unsafe_paths_and_types(self):
        self.assertFalse(coder.validate_patch(make_diff("../evil.py"))[0])
        self.assertFalse(coder.validate_patch(make_diff("/etc/passwd.py"))[0])
        self.assertFalse(coder.validate_patch(make_diff("run.sh"))[0])

    def test_deletion_and_rename_blocked(self):
        d = "diff --git a/util.py b/util.py\n--- a/util.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        self.assertIn("deleting", coder.validate_patch(d)[1])
        r = "diff --git a/a.py b/b.py\nrename from a.py\nrename to b.py\n--- a/a.py\n+++ b/b.py\n"
        self.assertFalse(coder.validate_patch(r)[0])

    def test_existing_tests_cannot_change(self):
        self.assertFalse(coder.validate_patch(make_diff("tests/test_core.py"))[0])

    def test_new_test_file_allowed(self):
        d = "diff --git a/tests/test_new.py b/tests/test_new.py\n--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1 @@\n+x = 1\n"
        self.assertTrue(coder.validate_patch(d, "/nonexistent")[0])

    def test_size_limits(self):
        big = GOOD + "+" + "a" * (coder.MAX_PATCH_CHARS + 1) + "\n"
        self.assertFalse(coder.validate_patch(big)[0])
        many = GOOD + "".join("+l\n" for _ in range(coder.MAX_CHANGED_LINES + 1))
        self.assertFalse(coder.validate_patch(many)[0])

    def test_not_a_diff(self):
        self.assertFalse(coder.validate_patch("just words")[0])

    def test_strip_fences(self):
        out = coder.strip_fences("Here you go:\n```diff\n" + GOOD + "```\nbye")
        self.assertTrue(out.startswith("diff --git"))
        self.assertNotIn("bye", out)


class ProposeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src")
        os.makedirs(self.src)
        with open(os.path.join(self.src, "util.py"), "w") as f:
            f.write("x = 1\n")
        self.db = os.path.join(self.tmp.name, "c.db")
        self.patcher = mock.patch.object(improver, "PROPOSAL_DIR", os.path.join(self.tmp.name, "proposals"))
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_propose_saves_for_approval_and_does_not_edit(self):
        r = coder.propose("add y", ["util.py"], lambda p: "```diff\n" + GOOD + "```", self.db, self.src)
        self.assertTrue(r["ok"], r)
        self.assertEqual(guardrails.get_approval(r["approval_id"], self.db)["kind"], "code_patch")
        self.assertTrue(os.path.exists(r["path"]))
        with open(os.path.join(self.src, "util.py")) as f:
            self.assertEqual(f.read(), "x = 1\n")

    def test_no_llm_answer(self):
        r = coder.propose("add y", ["util.py"], lambda p: None, self.db, self.src)
        self.assertFalse(r["ok"])
        self.assertEqual(guardrails.pending(self.db), [])

    def test_protected_file_not_even_read(self):
        r = coder.propose("x", ["guardrails.py"], lambda p: self.fail("LLM must not be called"), self.db, self.src)
        self.assertFalse(r["ok"])

    def test_patch_that_does_not_apply_rejected(self):
        bad = GOOD.replace(" x = 1", " x = 999")
        r = coder.propose("add y", ["util.py"], lambda p: bad, self.db, self.src)
        self.assertFalse(r["ok"])
        self.assertIn("apply", r["why"])

    def test_worker_step_and_agent(self):
        names = [a["name"] for a in workforce.AGENTS]
        self.assertIn("Coder Worker", names)
        self.assertEqual(workforce.KIND_TO_AGENT["code_proposal"], "Coder Worker")
        workforce.enqueue("code_proposal", {"goal": "add y", "files": ["util.py"]}, db_path=self.db)
        with mock.patch.object(coder, "BASE_DIR", self.src):
            res = workforce.run_once(db_path=self.db, llm=lambda p: GOOD)
        self.assertEqual(res["ran"], 1)
        self.assertEqual(len(guardrails.pending(self.db)), 1)


class VerifyApprovedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "v.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_approved_unverified_are_checked_once(self):
        a = guardrails.request_approval("code_patch", "A", GOOD, db_path=self.db)
        b = guardrails.request_approval("code_patch", "B", GOOD, db_path=self.db)
        guardrails.decide(a, True, self.db)
        calls = []
        verifier = lambda aid: calls.append(aid) or {"ok": True, "why": "tests passed", "next": "git apply x"}
        first = coder.verify_approved(self.db, verifier)
        second = coder.verify_approved(self.db, verifier)
        self.assertEqual(calls, [a])
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertNotIn(b, calls)

    def test_failure_is_recorded(self):
        a = guardrails.request_approval("code_patch", "A", GOOD, db_path=self.db)
        guardrails.decide(a, True, self.db)
        coder.verify_approved(self.db, lambda aid: {"ok": False, "why": "tests failed"})
        conn = improver.connect(self.db)
        row = conn.execute("SELECT ok, why FROM code_verification WHERE approval_id = ?", (a,)).fetchone()
        conn.close()
        self.assertEqual(row[0], 0)


if __name__ == "__main__":
    unittest.main()
