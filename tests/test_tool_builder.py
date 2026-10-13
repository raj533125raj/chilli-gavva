"""Tests for tool_builder.py — mini-tool page framework."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tool_builder


GOOD_SPEC = {
    "title": "Word Counter",
    "description": "Count words in text",
    "inputs": [{"id": "text", "type": "textarea", "label": "Text"}],
    "outputs": [{"id": "words", "label": "Words", "formula": "text.trim().split(/\\s+/).filter(Boolean).length"}],
}


class TestValidation(unittest.TestCase):
    def test_valid_spec_no_errors(self):
        self.assertEqual(tool_builder.validate_spec(GOOD_SPEC), [])

    def test_missing_title(self):
        spec = dict(GOOD_SPEC); del spec["title"]
        errs = tool_builder.validate_spec(spec)
        self.assertTrue(any("title" in e for e in errs))

    def test_missing_inputs(self):
        spec = dict(GOOD_SPEC); del spec["inputs"]
        errs = tool_builder.validate_spec(spec)
        self.assertTrue(any("inputs" in e for e in errs))

    def test_missing_outputs(self):
        spec = dict(GOOD_SPEC); del spec["outputs"]
        errs = tool_builder.validate_spec(spec)
        self.assertTrue(any("outputs" in e for e in errs))

    def test_title_too_long(self):
        spec = dict(GOOD_SPEC); spec["title"] = "x" * 121
        errs = tool_builder.validate_spec(spec)
        self.assertTrue(any("title" in e for e in errs))

    def test_unknown_input_type(self):
        spec = json.loads(json.dumps(GOOD_SPEC))
        spec["inputs"][0]["type"] = "file"
        errs = tool_builder.validate_spec(spec)
        self.assertTrue(any("type" in e for e in errs))

    def test_duplicate_input_id(self):
        spec = json.loads(json.dumps(GOOD_SPEC))
        spec["inputs"].append({"id": "text", "type": "text", "label": "Dup"})
        errs = tool_builder.validate_spec(spec)
        self.assertTrue(any("duplicate" in e for e in errs))

    def test_duplicate_output_id(self):
        spec = json.loads(json.dumps(GOOD_SPEC))
        spec["outputs"].append({"id": "words", "label": "Dup", "formula": "0"})
        errs = tool_builder.validate_spec(spec)
        self.assertTrue(any("duplicate" in e for e in errs))

    def test_formula_too_long(self):
        spec = json.loads(json.dumps(GOOD_SPEC))
        spec["outputs"][0]["formula"] = "x" * 401
        errs = tool_builder.validate_spec(spec)
        self.assertTrue(any("formula too long" in e for e in errs))

    def test_missing_formula(self):
        spec = json.loads(json.dumps(GOOD_SPEC))
        del spec["outputs"][0]["formula"]
        errs = tool_builder.validate_spec(spec)
        self.assertTrue(any("formula" in e for e in errs))

    def test_not_a_dict(self):
        errs = tool_builder.validate_spec("hello")
        self.assertTrue(errs)

    def test_empty_inputs_list(self):
        spec = dict(GOOD_SPEC); spec["inputs"] = []
        errs = tool_builder.validate_spec(spec)
        self.assertTrue(any("inputs" in e for e in errs))


class TestRenderTool(unittest.TestCase):
    def test_renders_html(self):
        html = tool_builder.render_tool(GOOD_SPEC)
        self.assertIn("<!doctype html>", html)
        self.assertIn("Word Counter", html)
        self.assertIn("Count words in text", html)

    def test_html_contains_output_id(self):
        html = tool_builder.render_tool(GOOD_SPEC)
        self.assertIn("out-words", html)

    def test_html_contains_input_id(self):
        html = tool_builder.render_tool(GOOD_SPEC)
        self.assertIn('id="text"', html)

    def test_range_input(self):
        spec = json.loads(json.dumps(GOOD_SPEC))
        spec["inputs"].append({"id": "wpm", "type": "range", "label": "WPM",
                                "min": 100, "max": 400, "step": 10, "default": 200})
        html = tool_builder.render_tool(spec)
        self.assertIn('type="range"', html)
        self.assertIn("wpm-val", html)

    def test_select_input(self):
        spec = json.loads(json.dumps(GOOD_SPEC))
        spec["inputs"].append({"id": "lang", "type": "select", "label": "Language",
                                "options": ["English", "Telugu"]})
        html = tool_builder.render_tool(spec)
        self.assertIn("<select", html)
        self.assertIn("Telugu", html)

    def test_percentage_output(self):
        spec = json.loads(json.dumps(GOOD_SPEC))
        spec["outputs"].append({"id": "pct", "label": "Pct", "formula": "50", "format": "percentage"})
        html = tool_builder.render_tool(spec)
        self.assertIn('"%"', html)

    def test_xss_escaping(self):
        spec = json.loads(json.dumps(GOOD_SPEC))
        spec["title"] = '<script>alert(1)</script>'
        html = tool_builder.render_tool(spec)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_dark_mode_media_query(self):
        html = tool_builder.render_tool(GOOD_SPEC)
        self.assertIn("prefers-color-scheme", html)

    def test_no_external_resources(self):
        html = tool_builder.render_tool(GOOD_SPEC)
        self.assertNotIn("cdn.jsdelivr", html)
        self.assertNotIn("googleapis", html)
        self.assertNotIn("analytics", html)


class TestBuildPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "test.db")
        self.site = os.path.join(self.tmp, "tools")

    def test_build_approved_tool(self):
        import guardrails
        # Submit and approve a spec
        aid, errs = tool_builder.submit_spec(GOOD_SPEC, db_path=self.db)
        self.assertEqual(errs, [])
        self.assertIsNotNone(aid)
        guardrails.decide(aid, approved=True, db_path=self.db)
        built = tool_builder.build(db_path=self.db, site_dir=self.site)
        self.assertEqual(len(built), 1)
        self.assertIn("word-counter", built[0])
        idx_path = os.path.join(self.site, built[0], "index.html")
        self.assertTrue(os.path.exists(idx_path))
        with open(idx_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Word Counter", content)

    def test_rejected_not_built(self):
        import guardrails
        aid, errs = tool_builder.submit_spec(GOOD_SPEC, db_path=self.db)
        guardrails.decide(aid, approved=False, db_path=self.db)
        built = tool_builder.build(db_path=self.db, site_dir=self.site)
        self.assertEqual(built, [])

    def test_pending_not_built(self):
        tool_builder.submit_spec(GOOD_SPEC, db_path=self.db)
        built = tool_builder.build(db_path=self.db, site_dir=self.site)
        self.assertEqual(built, [])

    def test_not_built_twice(self):
        import guardrails
        aid, _ = tool_builder.submit_spec(GOOD_SPEC, db_path=self.db)
        guardrails.decide(aid, approved=True, db_path=self.db)
        built1 = tool_builder.build(db_path=self.db, site_dir=self.site)
        built2 = tool_builder.build(db_path=self.db, site_dir=self.site)
        self.assertEqual(len(built1), 1)
        self.assertEqual(len(built2), 0)

    def test_invalid_spec_rejected_at_submit(self):
        bad = {"title": "", "inputs": [], "outputs": []}
        aid, errs = tool_builder.submit_spec(bad, db_path=self.db)
        self.assertIsNone(aid)
        self.assertTrue(len(errs) > 0)

    def test_index_built_after_tool(self):
        import guardrails
        aid, _ = tool_builder.submit_spec(GOOD_SPEC, db_path=self.db)
        guardrails.decide(aid, approved=True, db_path=self.db)
        tool_builder.build(db_path=self.db, site_dir=self.site)
        self.assertTrue(os.path.exists(os.path.join(self.site, "index.html")))

    def test_sample_specs_all_valid(self):
        for name, spec in tool_builder.SAMPLE_SPECS.items():
            errs = tool_builder.validate_spec(spec)
            self.assertEqual(errs, [], msg=f"{name}: {errs}")


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(tool_builder.slugify("Word Counter", "fallback"), "word-counter")

    def test_special_chars(self):
        self.assertEqual(tool_builder.slugify("Hello & World!", "x"), "hello-world")

    def test_empty_uses_fallback(self):
        self.assertEqual(tool_builder.slugify("", "fb"), "fb")

    def test_long_truncated(self):
        self.assertEqual(len(tool_builder.slugify("a" * 100, "x")), 50)


if __name__ == "__main__":
    unittest.main()
