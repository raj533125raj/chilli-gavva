"""Tests for recipe_runner.py — YAML recipe loader + enqueue logic."""
import importlib
import os
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


class FakeWorkforce:
    """Minimal workforce stub for enqueue()."""
    def __init__(self):
        self.enqueued = []
        self._next_id = 1

    def enqueue(self, kind, payload, priority=5, db_path=None):
        tid = self._next_id
        self._next_id += 1
        self.enqueued.append({"id": tid, "kind": kind, "payload": payload, "priority": priority})
        return tid


class TestLoadYaml(unittest.TestCase):
    def _rr(self):
        import recipe_runner
        return recipe_runner

    def _write(self, content):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.close()
        self._tmp = f.name
        return f.name

    def tearDown(self):
        tmp = getattr(self, "_tmp", None)
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)

    def test_top_level_fields(self):
        path = self._write("name: my_recipe\ndescription: A test recipe\n")
        rr = self._rr()
        data = rr._load_yaml(path)
        self.assertEqual(data["name"], "my_recipe")
        self.assertEqual(data["description"], "A test recipe")

    def test_tasks_list(self):
        yaml = (
            "name: r\ndescription: d\ntasks:\n"
            "  - kind: research\n    payload:\n      topic: Fish\n"
        )
        path = self._write(yaml)
        rr = self._rr()
        data = rr._load_yaml(path)
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["kind"], "research")
        self.assertEqual(data["tasks"][0]["payload"]["topic"], "Fish")

    def test_multiple_tasks(self):
        yaml = (
            "name: r\ndescription: d\ntasks:\n"
            "  - kind: research\n    payload:\n      topic: A\n"
            "  - kind: draft\n    payload:\n      topic: B\n"
        )
        path = self._write(yaml)
        rr = self._rr()
        data = rr._load_yaml(path)
        self.assertEqual(len(data["tasks"]), 2)
        self.assertEqual(data["tasks"][0]["kind"], "research")
        self.assertEqual(data["tasks"][1]["kind"], "draft")

    def test_priority_parsed(self):
        yaml = (
            "name: r\ndescription: d\ntasks:\n"
            "  - kind: research\n    priority: 8\n    payload:\n      topic: High\n"
        )
        path = self._write(yaml)
        rr = self._rr()
        data = rr._load_yaml(path)
        self.assertEqual(data["tasks"][0]["priority"], 8)

    def test_default_priority_five(self):
        yaml = "name: r\ndescription: d\ntasks:\n  - kind: research\n    payload:\n      topic: X\n"
        path = self._write(yaml)
        rr = self._rr()
        data = rr._load_yaml(path)
        self.assertEqual(data["tasks"][0]["priority"], 5)

    def test_comments_ignored(self):
        yaml = "# this is a comment\nname: r\ndescription: d\ntasks:\n  - kind: draft\n    payload:\n      topic: Y\n"
        path = self._write(yaml)
        rr = self._rr()
        data = rr._load_yaml(path)
        self.assertEqual(data["name"], "r")

    def test_multiple_payload_keys(self):
        yaml = (
            "name: r\ndescription: d\ntasks:\n"
            "  - kind: research\n    payload:\n      topic: Fish\n      region: India\n"
        )
        path = self._write(yaml)
        rr = self._rr()
        data = rr._load_yaml(path)
        t = data["tasks"][0]
        self.assertEqual(t["payload"]["topic"], "Fish")
        self.assertEqual(t["payload"]["region"], "India")

    def test_empty_tasks_list(self):
        yaml = "name: r\ndescription: d\ntasks:\n"
        path = self._write(yaml)
        rr = self._rr()
        data = rr._load_yaml(path)
        self.assertEqual(data["tasks"], [])

    def test_invalid_priority_falls_back(self):
        yaml = (
            "name: r\ndescription: d\ntasks:\n"
            "  - kind: research\n    priority: notanint\n    payload:\n      topic: Z\n"
        )
        path = self._write(yaml)
        rr = self._rr()
        data = rr._load_yaml(path)
        self.assertEqual(data["tasks"][0]["priority"], 5)


class TestListRecipes(unittest.TestCase):
    def setUp(self):
        import recipe_runner
        self._orig = recipe_runner.RECIPES_DIR
        self._tmpdir = tempfile.mkdtemp()
        recipe_runner.RECIPES_DIR = self._tmpdir

    def tearDown(self):
        import recipe_runner
        recipe_runner.RECIPES_DIR = self._orig
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _rr(self):
        import recipe_runner
        return recipe_runner

    def test_empty_dir_returns_empty_list(self):
        rr = self._rr()
        self.assertEqual(rr.list_recipes(), [])

    def test_lists_valid_yaml(self):
        rr = self._rr()
        path = os.path.join(self._tmpdir, "foo.yaml")
        with open(path, "w") as f:
            f.write("name: foo\ndescription: Foo recipe\ntasks:\n  - kind: research\n    payload:\n      topic: X\n")
        result = rr.list_recipes()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "foo")
        self.assertEqual(result[0]["description"], "Foo recipe")
        self.assertEqual(result[0]["task_count"], 1)

    def test_broken_yaml_shows_error(self):
        rr = self._rr()
        path = os.path.join(self._tmpdir, "bad.yaml")
        with open(path, "w") as f:
            f.write("")
        # _load_yaml of empty file returns empty dict — no tasks
        # so task_count = 0, no error expected
        result = rr.list_recipes()
        self.assertEqual(len(result), 1)
        self.assertNotIn("error", result[0])


class TestRunRecipe(unittest.TestCase):
    def setUp(self):
        self._fw = FakeWorkforce()
        self._wf_patch = unittest.mock.patch.dict(
            sys.modules, {"workforce": self._fw}
        )
        self._wf_patch.start()
        import recipe_runner
        importlib.reload(recipe_runner)
        self._rr = recipe_runner
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        self._wf_patch.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_recipe(self, content, name="test.yaml"):
        path = os.path.join(self._tmpdir, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_missing_file_returns_error(self):
        result = self._rr.run_recipe("/nonexistent/path.yaml")
        self.assertIn("error", result)

    def test_valid_recipe_enqueues_tasks(self):
        yaml = (
            "name: test\ndescription: Test recipe\ntasks:\n"
            "  - kind: research\n    payload:\n      topic: Shrimp\n"
        )
        path = self._write_recipe(yaml)
        result = self._rr.run_recipe(path)
        self.assertNotIn("error", result)
        self.assertEqual(len(result["enqueued"]), 1)
        self.assertEqual(result["enqueued"][0]["kind"], "research")
        self.assertEqual(len(self._fw.enqueued), 1)

    def test_dry_run_does_not_enqueue(self):
        yaml = (
            "name: test\ndescription: d\ntasks:\n"
            "  - kind: research\n    payload:\n      topic: Fish\n"
        )
        path = self._write_recipe(yaml)
        result = self._rr.run_recipe(path, dry_run=True)
        self.assertEqual(result["enqueued"][0]["id"], None)
        self.assertEqual(len(self._fw.enqueued), 0)

    def test_unknown_kind_skipped_with_error(self):
        yaml = (
            "name: test\ndescription: d\ntasks:\n"
            "  - kind: fly_to_moon\n    payload:\n      topic: X\n"
        )
        path = self._write_recipe(yaml)
        result = self._rr.run_recipe(path)
        self.assertEqual(len(result["enqueued"]), 0)
        self.assertGreater(len(result["errors"]), 0)
        self.assertIn("fly_to_moon", result["errors"][0])

    def test_mixed_valid_invalid_kinds(self):
        yaml = (
            "name: test\ndescription: d\ntasks:\n"
            "  - kind: research\n    payload:\n      topic: Good\n"
            "  - kind: bad_kind\n    payload:\n      topic: Bad\n"
        )
        path = self._write_recipe(yaml)
        result = self._rr.run_recipe(path)
        self.assertEqual(len(result["enqueued"]), 1)
        self.assertEqual(len(result["errors"]), 1)

    def test_empty_recipe_returns_error(self):
        yaml = "name: empty\ndescription: d\ntasks:\n"
        path = self._write_recipe(yaml)
        result = self._rr.run_recipe(path)
        self.assertIn("error", result)

    def test_result_contains_name_and_description(self):
        yaml = (
            "name: my_recipe\ndescription: My desc\ntasks:\n"
            "  - kind: draft\n    payload:\n      topic: X\n"
        )
        path = self._write_recipe(yaml)
        result = self._rr.run_recipe(path)
        self.assertEqual(result["name"], "my_recipe")
        self.assertEqual(result["description"], "My desc")

    def test_all_valid_kinds_accepted(self):
        import recipe_runner as rr
        kinds = list(rr.VALID_KINDS)
        yaml = "name: t\ndescription: d\ntasks:\n"
        for k in kinds:
            yaml += f"  - kind: {k}\n    payload:\n      topic: X\n"
        path = self._write_recipe(yaml)
        result = self._rr.run_recipe(path)
        self.assertEqual(len(result["errors"]), 0)
        self.assertEqual(len(result["enqueued"]), len(kinds))

    def test_priority_passed_to_workforce(self):
        yaml = (
            "name: t\ndescription: d\ntasks:\n"
            "  - kind: research\n    priority: 9\n    payload:\n      topic: Hi\n"
        )
        path = self._write_recipe(yaml)
        self._rr.run_recipe(path)
        self.assertEqual(self._fw.enqueued[0]["priority"], 9)


# Make unittest.mock available
import unittest.mock


if __name__ == "__main__":
    unittest.main()
