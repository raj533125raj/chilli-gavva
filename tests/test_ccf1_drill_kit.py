"""CCF-1: the auto-fix drill kit (tools/autofix_drill.py) — its own parts, checked without running a drill.

The drills themselves (10 injected failures through the real auto-fix flow) are in
tests/test_ccf_autofix_drill.py.
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import autofix_drill as drill   # noqa: E402


def load_world():
    spec = importlib.util.spec_from_file_location("robot_sim_world_ccf1", TOOLS / "robot_sim_world.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScenarioTests(unittest.TestCase):
    def test_ten_drills_with_the_asked_failures(self):
        self.assertEqual(drill.SCENARIO_KEYS, [
            "import_error", "failing_test", "timeout", "syntax_error", "control_copy", "bad_workflow_step",
            "flaky_test", "money_string", "broken_builder", "plugin_none"])
        for scenario in drill.SCENARIOS:
            with self.subTest(drill=scenario["key"]):
                self.assertIn(scenario["expect"], ("autofix", "owner", "retry"))
                self.assertTrue(scenario["title"])
                for step in scenario["steps"]:
                    self.assertIn(step, drill.AGENT_STEPS)

    def test_every_injection_fits_the_code_and_the_recorded_answer_undoes_it(self):
        for scenario in drill.SCENARIOS:
            for name, old, new in scenario["inject"]:
                with self.subTest(drill=scenario["key"], file=name):
                    text = (ROOT / name).read_text(encoding="utf-8")
                    self.assertIn(old, text, "the text this drill breaks is not in the file any more")
                    broken = text.replace(old, new, 1)
                    self.assertNotEqual(broken, text)
                    for fix_name, fix_old, fix_new in scenario["answer"]["edits"]:
                        if fix_name == name:
                            self.assertIn(fix_old, broken)
                            self.assertEqual(broken.replace(fix_old, fix_new, 1), text)

    def test_an_auto_fix_answer_has_a_regression_test_and_touches_no_protected_file(self):
        import autofix
        for scenario in drill.SCENARIOS:
            if scenario["expect"] != "autofix":
                continue
            with self.subTest(drill=scenario["key"]):
                files = list(scenario["answer"]["files"])
                self.assertTrue(any(name.startswith("tests/test_") for name in files))
                for name in files + [edit[0] for edit in scenario["answer"]["edits"]]:
                    self.assertFalse(autofix.protected_path(name))
                for content in scenario["answer"]["files"].values():
                    compile(content, "regression", "exec")

    def test_drill_test_modules_exist(self):
        for scenario in drill.SCENARIOS:
            for name in scenario["tests"]:
                self.assertTrue((ROOT / "tests" / f"{name}.py").is_file(), name)

    def test_no_money_amount_is_written_in_the_tool(self):
        source = (TOOLS / "autofix_drill.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"[$₹€£]\s?\d")


class WorkflowReadingTests(unittest.TestCase):
    def test_every_step_the_drill_plays_is_in_the_real_workflow_files(self):
        for filename, steps in (("agent.yml", drill.AGENT_STEPS), ("autofix.yml", drill.FIX_STEPS),
                                ("autofix-check.yml", drill.CHECK_STEPS)):
            text = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
            for key, name in steps.items():
                with self.subTest(workflow=filename, step=key):
                    script = drill.workflow_step_script(text, name)
                    self.assertTrue(script.strip())
                    self.assertNotIn("${{", script)

    def test_step_script_plain_and_block(self):
        text = ("jobs:\n  a:\n    steps:\n      - name: One\n        env:\n          X: 1\n        run: echo one\n"
                "      - name: Two\n        run: |\n          echo two\n          echo three\n\n"
                "      - name: Three\n        uses: x/y@v1\n")
        self.assertEqual(drill.workflow_step_script(text, "One"), "echo one")
        self.assertEqual(drill.workflow_step_script(text, "Two"), "echo two\necho three\n")
        with self.assertRaises(drill.DrillError):
            drill.workflow_step_script(text, "Three")
        with self.assertRaises(drill.DrillError):
            drill.workflow_step_script(text, "Four")

    def test_keep_heredoc_lines(self):
        script = "run x <<'TAG'\npython3 a.py\ngit checkout -- b\npython3 c.py\nTAG\n"
        self.assertEqual(drill.keep_heredoc_lines(script, "TAG", ("python3 a.py",)),
                         "run x <<'TAG'\npython3 a.py\nTAG\n")
        with self.assertRaises(drill.DrillError):
            drill.keep_heredoc_lines(script, "OTHER", ("python3",))
        with self.assertRaises(drill.DrillError):
            drill.keep_heredoc_lines(script, "TAG", ("ruby",))

    def test_step_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out.txt"
            path.write_text("started=true\nprompt<<robot_1\nline one\nline = two\nrobot_1\nbusy=false\n",
                            encoding="utf-8")
            self.assertEqual(drill.step_outputs(path),
                             {"started": "true", "prompt": "line one\nline = two", "busy": "false"})
            self.assertEqual(drill.step_outputs(Path(folder) / "missing.txt"), {})

    def test_helper_test_modules_follow_imports(self):
        with tempfile.TemporaryDirectory() as folder:
            tests = Path(folder) / "tests"
            tests.mkdir()
            (tests / "test_a.py").write_text("import test_b\nfrom tests.test_c import x\n", encoding="utf-8")
            (tests / "test_b.py").write_text("import os\n", encoding="utf-8")
            (tests / "test_c.py").write_text("import test_missing\n", encoding="utf-8")
            self.assertEqual(drill.helper_test_modules(["test_a"], Path(folder)), ["test_a", "test_b", "test_c"])


class ReportTests(unittest.TestCase):
    ROWS = [{"key": "failing_test", "title": "A failing test", "expect": "autofix", "verdict": "PASS",
             "cells": {"found": "yes", "route": "auto-fix started", "fix_branch": "yes", "tests_green": "yes",
                       "owner_msg": "clear"}, "notes": []},
            {"key": "timeout", "title": "A timeout", "expect": "autofix", "verdict": "FAIL",
             "cells": {"found": "NO alert"}, "notes": ["no owner alert after the second failure"]}]

    def test_report_has_the_five_questions_and_a_total(self):
        text = drill.format_report(self.ROWS)
        for label in ("found?", "route", "fix branch?", "tests green?", "owner msg?"):
            self.assertIn(label, text)
        self.assertIn("! no owner alert after the second failure", text)
        self.assertIn("DRILLS: 2 | as expected: 1 | not as expected: 1", text)

    def test_keep_folder_inside_the_repo_is_refused(self):
        self.assertTrue(drill.inside_repo(ROOT / "exported_content" / "drill"))
        self.assertFalse(drill.inside_repo(Path(tempfile.gettempdir()) / "drill"))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            drill.main(["--keep", str(ROOT / "drill-keep")])
        self.assertFalse((ROOT / "drill-keep").exists())

    def test_unknown_drill_is_refused_and_list_names_all(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            drill.main(["--only", "no_such_drill"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(drill.main(["--list"]), 0)
        for key in drill.SCENARIO_KEYS:
            self.assertIn(key, out.getvalue())


class FakeRequest:
    def __init__(self, method, data=None):
        self.method = method
        self.data = json.dumps(data).encode("utf-8") if data is not None else None

    def get_method(self):
        return self.method


class HubTests(unittest.TestCase):
    """The pretend GitHub of the drill: pull requests, merge, checks and workflow requests."""

    def setUp(self):
        self.world = load_world()
        self.saved = []
        self.world._save = lambda name, data: self.saved.append(name)
        self.world._append = lambda name, row: None
        self.world._body = lambda req: req.data or b""
        self.hub = {"git_dir": "/nonexistent/origin.git", "next_pull": 3, "pulls": {
            "2": {"number": 2, "title": "Auto-fix: Run tests first", "state": "open", "body": "x",
                  "head_ref": "autofix/run-9", "head_sha": "a" * 40, "base": "main"}}}

    def call(self, method, path, data=None):
        return self.world._hub(self.hub, FakeRequest(method, data), "https://api.github.com/x", method, path,
                               "sim/robot")

    def test_open_pull_requests_look_like_github(self):
        rows = self.call("GET", "/pulls")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["head"], {"ref": "autofix/run-9", "sha": "a" * 40, "repo": {"full_name": "sim/robot"}})
        self.assertEqual(rows[0]["user"]["login"], "github-actions[bot]")
        self.assertEqual(rows[0]["mergeable_state"], "clean")

    def test_a_pull_request_needs_a_real_branch(self):
        with self.assertRaises(Exception):
            self.call("POST", "/pulls", {"title": "t", "head": "no-such-branch", "base": "main"})
        self.assertEqual(self.hub["next_pull"], 3)

    def test_merge_needs_the_exact_commit_and_happens_once(self):
        with self.assertRaises(Exception):
            self.call("PUT", "/pulls/2/merge", {"sha": "b" * 40})
        self.assertEqual(self.call("PUT", "/pulls/2/merge", {"sha": "a" * 40})["merged"], True)
        self.assertEqual(self.hub["merged"][0]["number"], 2)
        self.assertEqual(self.call("GET", "/pulls"), [])
        with self.assertRaises(Exception):
            self.call("PUT", "/pulls/2/merge", {"sha": "a" * 40})

    def test_close_checks_and_workflow_requests(self):
        self.assertEqual(self.call("PATCH", "/pulls/2", {"state": "closed"})["state"], "closed")
        self.assertEqual(self.call("GET", "/commits/" + "a" * 40 + "/check-runs"), {"total_count": 0, "check_runs": []})
        self.call("POST", "/actions/workflows/autofix.yml/dispatches", {"ref": "main", "inputs": {"summary": "s"}})
        self.assertEqual(self.hub["dispatches"][0]["workflow"], "autofix.yml")
        self.assertEqual(self.hub["dispatches"][0]["inputs"], {"summary": "s"})
        self.assertIsNone(self.call("GET", "/git/ref/heads/main"))


if __name__ == "__main__":
    unittest.main()
