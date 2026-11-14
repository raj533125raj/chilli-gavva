"""Regression tests for format_registry Python 3.9 compatibility."""

import unittest

import format_registry


class TestDeferredAnnotations(unittest.TestCase):
    def test_union_annotations_are_deferred_strings(self):
        self.assertIsInstance(format_registry.get_format_info.__annotations__["return"], str)
        self.assertIsInstance(format_registry.classify.__annotations__["return"], str)

    def test_registry_still_works(self):
        info = format_registry.get_format_info("planner")
        self.assertIsInstance(info, dict)
        self.assertIsNone(format_registry.get_format_info("unheard_of"))


class TestModulePathCollision(unittest.TestCase):
    def test_same_module_name_from_new_path_replaces_and_warns(self):
        import importlib.util
        import io
        import sys
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "one"
            second = root / "two"
            first.mkdir()
            second.mkdir()
            body = "FORMAT = {fmt!r}\nKEYWORDS = ['collision']\ndef build(spec, out_dir):\n    return []\n"
            (first / "builder_oc3collide.py").write_text(body.format(fmt="oc3_collide_one"), encoding="utf-8")
            (second / "builder_oc3collide.py").write_text(body.format(fmt="oc3_collide_two"), encoding="utf-8")

            spec = importlib.util.spec_from_file_location("builder_oc3collide", first / "builder_oc3collide.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules["builder_oc3collide"] = module
            spec.loader.exec_module(module)
            self.addCleanup(sys.modules.pop, "builder_oc3collide", None)
            self.addCleanup(format_registry.reset_registry)

            err = io.StringIO()
            with redirect_stderr(err):
                registry = format_registry.discover_builders(second, force_reload=True)

        self.assertIn("oc3_collide_two", registry)
        self.assertNotIn("oc3_collide_one", registry)
        self.assertIn("different path", err.getvalue())
