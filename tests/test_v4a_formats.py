"""tests/test_v4a_formats.py — Unit tests for BINNY-V4A Format Registry & 'Can We Build It?' Feasibility.

Covers:
1. Format registry registration and unregistration.
2. Discovery of builder_<format>.py modules.
3. Edge cases in discovery (invalid attributes, missing build, malformed files).
4. Keyword matching and classification (multi-word, priority, case-insensitivity).
5. Fallback built-in formats (sheets, planner, notion, resume, dated_planner).
6. Feasibility check: can_build(idea_text), can_build(spec dict).
7. Tenglish unbuildable reasons and tone (no currency, no secrets).
8. Product pipeline integration: unbuildable draft transitions to 'needs_owner'.
9. Product builder integration: registered builder dispatch and truth checks.
10. Isolation and environment reset tests.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import format_registry
import guardrails
import product_builder
import product_pipeline
from tests.test_product_builder import SPECS


class TestFormatRegistryBasics(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in os.environ if k.startswith("GITHUB_")},
            clear=False,
        )
        self.env_patcher.start()
        format_registry.reset_registry()

    def tearDown(self):
        self.env_patcher.stop()
        format_registry.reset_registry()

    def test_01_builtin_formats_registered(self):
        formats = format_registry.list_registered_formats()
        for expected in ("sheets", "planner", "dated_planner", "notion", "resume"):
            self.assertIn(expected, formats)

    def test_02_register_custom_format_valid(self):
        def dummy_build(spec, out_dir):
            return ["file.txt"]

        res = format_registry.register_format(
            "flashcards", ["flashcards", "revision cards"], build_fn=dummy_build
        )
        self.assertTrue(res)
        self.assertIn("flashcards", format_registry.list_registered_formats())
        self.assertEqual(format_registry.get_builder("flashcards"), dummy_build)

    def test_03_register_invalid_format_id(self):
        self.assertFalse(format_registry.register_format("", ["keyword"]))
        self.assertFalse(format_registry.register_format(None, ["keyword"]))
        self.assertFalse(format_registry.register_format("   ", ["keyword"]))

    def test_04_unregister_format(self):
        format_registry.register_format("temp_format", ["temp"])
        self.assertIn("temp_format", format_registry.list_registered_formats())
        res = format_registry.unregister_format("temp_format")
        self.assertTrue(res)
        self.assertNotIn("temp_format", format_registry.list_registered_formats())

    def test_05_unregister_nonexistent_format(self):
        self.assertFalse(format_registry.unregister_format("does_not_exist"))
        self.assertFalse(format_registry.unregister_format(""))

    def test_06_get_format_info(self):
        info = format_registry.get_format_info("planner")
        self.assertIsNotNone(info)
        self.assertEqual(info["format"], "planner")
        self.assertIn("keywords", info)
        self.assertTrue(info["builtin"])

    def test_07_get_format_info_nonexistent(self):
        self.assertIsNone(format_registry.get_format_info("unheard_of"))
        self.assertIsNone(format_registry.get_format_info(None))

    def test_08_get_builder_builtin_returns_none(self):
        self.assertIsNone(format_registry.get_builder("sheets"))
        self.assertIsNone(format_registry.get_builder("planner"))

    def test_09_reset_registry_cleans_custom(self):
        format_registry.register_format("checklist_v1", ["checklist"])
        self.assertIn("checklist_v1", format_registry.list_registered_formats())
        format_registry.reset_registry()
        self.assertNotIn("checklist_v1", format_registry.list_registered_formats())


class TestModuleDiscovery(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in os.environ if k.startswith("GITHUB_")},
            clear=False,
        )
        self.env_patcher.start()
        format_registry.reset_registry()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.env_patcher.stop()
        self.temp_dir.cleanup()
        format_registry.reset_registry()

    def test_10_discover_valid_builder(self):
        builder_file = Path(self.temp_dir.name) / "builder_sticker_pack.py"
        code = '''FORMAT = "sticker_pack"
KEYWORDS = ["sticker pack", "digital stickers", "goodnotes stickers"]
def build(spec, out_dir):
    return ["stickers.zip"]
'''
        builder_file.write_text(code, encoding="utf-8")
        discovered = format_registry.discover_builders(
            search_dir=self.temp_dir.name, force_reload=True
        )
        self.assertIn("sticker_pack", discovered)
        info = discovered["sticker_pack"]
        self.assertEqual(info["format"], "sticker_pack")
        self.assertIn("digital stickers", info["keywords"])
        self.assertFalse(info["builtin"])
        self.assertTrue(callable(info["build"]))

    def test_11_discover_ignores_non_builder_files(self):
        other_file = Path(self.temp_dir.name) / "helper_stickers.py"
        other_file.write_text("FORMAT = 'sticker_helper'", encoding="utf-8")
        discovered = format_registry.discover_builders(
            search_dir=self.temp_dir.name, force_reload=True
        )
        self.assertNotIn("sticker_helper", discovered)

    def test_12_discover_skips_missing_format_id(self):
        bad_file = Path(self.temp_dir.name) / "builder_bad1.py"
        bad_file.write_text(
            'KEYWORDS = ["bad"]\ndef build(spec, out_dir): return []\n',
            encoding="utf-8",
        )
        discovered = format_registry.discover_builders(
            search_dir=self.temp_dir.name, force_reload=True
        )
        self.assertNotIn("bad1", discovered)

    def test_13_discover_skips_non_callable_build(self):
        bad_file = Path(self.temp_dir.name) / "builder_bad2.py"
        bad_file.write_text(
            'FORMAT = "bad2"\nKEYWORDS = ["bad"]\nbuild = "not a callable"\n',
            encoding="utf-8",
        )
        discovered = format_registry.discover_builders(
            search_dir=self.temp_dir.name, force_reload=True
        )
        self.assertNotIn("bad2", discovered)

    def test_14_discover_skips_syntax_error_file_gracefully(self):
        broken_file = Path(self.temp_dir.name) / "builder_broken.py"
        broken_file.write_text("def broken_syntax( {", encoding="utf-8")
        discovered = format_registry.discover_builders(
            search_dir=self.temp_dir.name, force_reload=True
        )
        self.assertNotIn("broken", discovered)

    def test_15_discover_multiple_builders(self):
        file_a = Path(self.temp_dir.name) / "builder_audiocourse.py"
        file_a.write_text(
            'FORMAT = "audiocourse"\nKEYWORDS = ["audio course"]\ndef build(spec, out_dir): return []\n',
            encoding="utf-8",
        )
        file_b = Path(self.temp_dir.name) / "builder_wallart.py"
        file_b.write_text(
            'FORMAT = "wallart"\nKEYWORDS = ["wall art", "printable poster"]\ndef build(spec, out_dir): return []\n',
            encoding="utf-8",
        )
        discovered = format_registry.discover_builders(
            search_dir=self.temp_dir.name, force_reload=True
        )
        self.assertIn("audiocourse", discovered)
        self.assertIn("wallart", discovered)


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in os.environ if k.startswith("GITHUB_")},
            clear=False,
        )
        self.env_patcher.start()
        format_registry.reset_registry()

    def tearDown(self):
        self.env_patcher.stop()
        format_registry.reset_registry()

    def test_16_classify_planner_keywords(self):
        self.assertEqual(
            format_registry.classify("Daily ADHD Focus Routine Planner printable PDF"),
            "planner",
        )
        self.assertEqual(
            format_registry.classify("Simple printable weekly checklist workbook"),
            "planner",
        )

    def test_17_classify_sheets_keywords(self):
        self.assertEqual(
            format_registry.classify("Monthly budget spreadsheet in google sheets with formulas"),
            "sheets",
        )
        self.assertEqual(
            format_registry.classify("Freelancer income tracker excel xlsx calculator"),
            "sheets",
        )

    def test_18_classify_notion_keywords(self):
        self.assertEqual(
            format_registry.classify("Second Brain Notion template system with databases"),
            "notion",
        )
        self.assertEqual(
            format_registry.classify("Student life Notion dashboard and planner"),
            "notion",
        )

    def test_19_classify_resume_keywords(self):
        self.assertEqual(
            format_registry.classify("Modern ATS resume bundle with cover letter docx"),
            "resume",
        )
        self.assertEqual(
            format_registry.classify("Professional job seeker CV template and application pack"),
            "resume",
        )

    def test_20_classify_dated_planner_keywords(self):
        self.assertEqual(
            format_registry.classify("2027 planner with monthly calendar pages"),
            "dated_planner",
        )
        self.assertEqual(
            format_registry.classify("Annual dated calendar planner PDF"),
            "dated_planner",
        )

    def test_21_classify_case_insensitivity(self):
        self.assertEqual(
            format_registry.classify("MONTHLY BUDGET SPREADSHEET"),
            "sheets",
        )
        self.assertEqual(
            format_registry.classify("nOtIoN dAsHbOaRd SyStEm"),
            "notion",
        )

    def test_22_classify_explicit_kind_attribute(self):
        self.assertEqual(
            format_registry.classify("Draft: custom\nkind: resume\nInstructions here"),
            "resume",
        )
        self.assertEqual(
            format_registry.classify("Title: pack\nformat: sheets\nSimple items"),
            "sheets",
        )

    def test_23_classify_niche_agent_titles(self):
        self.assertEqual(
            format_registry.classify("Draft: [ADHD Planners Agent] Daily Focus Plan"),
            "planner",
        )
        self.assertEqual(
            format_registry.classify("Draft: [Budget Sheets Agent] Zero Sum Plan"),
            "sheets",
        )
        self.assertEqual(
            format_registry.classify("Draft: [Resume Bundle Agent] Senior Tech Resume"),
            "resume",
        )
        self.assertEqual(
            format_registry.classify("Draft: [Notion Templates Agent] Freelancer OS"),
            "notion",
        )
        self.assertEqual(
            format_registry.classify("Draft: [2027 Planners Agent] Teacher Dated Book"),
            "dated_planner",
        )

    def test_24_classify_unbuildable_returns_none(self):
        self.assertIsNone(
            format_registry.classify("A native iOS Swift mobile app with push notifications")
        )
        self.assertIsNone(
            format_registry.classify("SaaS subscription web app for inventory tracking")
        )
        self.assertIsNone(
            format_registry.classify("Physical ceramic coffee mug with logo")
        )
        self.assertIsNone(
            format_registry.classify("Cryptocurrency trading bot in Rust")
        )

    def test_25_classify_empty_or_none(self):
        self.assertIsNone(format_registry.classify(""))
        self.assertIsNone(format_registry.classify(None))
        self.assertIsNone(format_registry.classify("    "))

    def test_26_classify_registered_custom_builder(self):
        format_registry.register_format(
            "flashcards",
            ["flashcards", "study cards", "anki deck"],
            build_fn=lambda s, o: [],
        )
        self.assertEqual(
            format_registry.classify("Medical student pharmacology flashcards study cards"),
            "flashcards",
        )

    def test_27_classify_multiword_phrase_beats_single_word(self):
        format_registry.register_format(
            "study_cards",
            ["medical study flashcards"],
            build_fn=lambda s, o: [],
        )
        format_registry.register_format(
            "generic_cards",
            ["flashcards"],
            build_fn=lambda s, o: [],
        )
        res = format_registry.classify("Medical study flashcards for pharmacology students")
        self.assertEqual(res, "study_cards")


class TestCanBuildFeasibility(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in os.environ if k.startswith("GITHUB_")},
            clear=False,
        )
        self.env_patcher.start()
        format_registry.reset_registry()

    def tearDown(self):
        self.env_patcher.stop()
        format_registry.reset_registry()

    def test_28_can_build_string_input(self):
        self.assertTrue(format_registry.can_build("Printable weekly meal planner"))
        self.assertTrue(format_registry.can_build("Google sheets budget tracker"))
        self.assertFalse(format_registry.can_build("Mobile application for gym"))

    def test_29_can_build_spec_dict_with_kind(self):
        self.assertTrue(format_registry.can_build({"kind": "planner"}))
        self.assertTrue(format_registry.can_build({"kind": "sheets"}))
        self.assertTrue(format_registry.can_build({"kind": "notion"}))
        self.assertTrue(format_registry.can_build({"kind": "resume"}))
        self.assertTrue(format_registry.can_build({"kind": "dated_planner"}))
        self.assertFalse(format_registry.can_build({"kind": "mobile_app"}))
        self.assertFalse(format_registry.can_build({"kind": "saas"}))

    def test_30_can_build_spec_dict_with_format_key(self):
        self.assertTrue(format_registry.can_build({"format": "planner"}))
        self.assertFalse(format_registry.can_build({"format": "hardware"}))

    def test_31_can_build_spec_dict_without_kind_infers_from_text(self):
        spec = {
            "name": "Teacher Daily Planner",
            "description": "A structured printable PDF worksheet for classrooms",
        }
        self.assertTrue(format_registry.can_build(spec))

        unbuildable_spec = {
            "name": "Cloud Backend API",
            "description": "Golang microservice with Kubernetes helm charts",
        }
        self.assertFalse(format_registry.can_build(unbuildable_spec))

    def test_32_can_build_invalid_types(self):
        self.assertFalse(format_registry.can_build(None))
        self.assertFalse(format_registry.can_build(""))
        self.assertFalse(format_registry.can_build(12345))
        self.assertFalse(format_registry.can_build([]))


class TestTenglishUnbuildableReason(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in os.environ if k.startswith("GITHUB_")},
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    def test_33_reason_contains_tenglish_and_no_currency(self):
        reason = format_registry.get_tenglish_unbuildable_reason("any draft text")
        self.assertIsInstance(reason, str)
        self.assertIn("builder dorakaledu", reason)
        self.assertIn("owner review kavali", reason)
        for sym in ("$", "₹", "€", "£", "USD", "INR", "price", "cost"):
            self.assertNotIn(sym, reason)

    def test_34_reason_house_style_capitalization(self):
        reason = format_registry.get_tenglish_unbuildable_reason()
        self.assertTrue(reason.startswith("Ee draft ki"))


class TestProductPipelineIntegration(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in os.environ if k.startswith("GITHUB_")},
            clear=False,
        )
        self.env_patcher.start()
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db")
        self.db = self.temp_db.name
        self.temp_out = tempfile.TemporaryDirectory()
        self.out = self.temp_out.name
        format_registry.reset_registry()

    def tearDown(self):
        self.env_patcher.stop()
        self.temp_db.close()
        self.temp_out.cleanup()
        format_registry.reset_registry()

    def _setup_approval(self, title, payload):
        aid = guardrails.request_approval(
            "public_draft", title, payload, db_path=self.db
        )
        guardrails.decide(aid, True, db_path=self.db)
        return aid

    def test_35_pipeline_stops_unbuildable_draft_and_marks_needs_owner(self):
        unbuildable_title = "Draft: [SaaS Agent] Cloud Billing Platform"
        unbuildable_payload = (
            "PRODUCT NAME: Cloud Billing Platform\n"
            "BUYER: enterprise\n"
            "WHAT'S INSIDE: Python backend and React frontend application\n"
            "HOW TO USE: Deploy with Docker\n"
            "LISTING DESCRIPTION: A complete SaaS subscription web app\n"
            "SEARCH TAGS: saas, billing, stripe, enterprise, cloud\n"
        )
        aid = self._setup_approval(unbuildable_title, unbuildable_payload)

        results = product_pipeline.run_pending(db_path=self.db, out_dir=self.out)

        app = guardrails.get_approval(aid, db_path=self.db)
        self.assertEqual(app["status"], "needs_owner")
        with guardrails.connect(self.db) as conn:
            row = conn.execute("SELECT reason FROM approvals WHERE id=?", (aid,)).fetchone()
            self.assertIsNotNone(row)
            self.assertIn("builder dorakaledu", row[0])

        with product_pipeline.connect(self.db) as conn:
            row = conn.execute(
                "SELECT status, reason FROM product_deliveries WHERE approval_id=?",
                (aid,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "retry")
            self.assertIn("feasibility", row[1])

        self.assertEqual(os.listdir(self.out), [])

    def test_36_pipeline_allows_custom_registered_format(self):
        def custom_build(spec, folder):
            p = os.path.join(folder, "card.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("Card content")
            return ["card.txt"]

        format_registry.register_format(
            "recipe_card",
            ["recipe card", "recipe cards"],
            build_fn=custom_build,
        )

        title = "Draft: [Recipe Agent] Vegan Dinner Recipe Card"
        payload = (
            "PRODUCT NAME: Vegan Recipe Card\n"
            "BUYER: home cooks\n"
            "WHAT'S INSIDE: Printable recipe cards for weekly dinners\n"
            "HOW TO USE: Follow the card\n"
            "LISTING DESCRIPTION: Easy vegan recipe cards\n"
            "SEARCH TAGS: recipe card, vegan, dinner, kitchen, food\n"
        )
        aid = self._setup_approval(title, payload)
        self.assertTrue(format_registry.can_build(f"{title}\n{payload}"))


class TestProductBuilderIntegration(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in os.environ if k.startswith("GITHUB_")},
            clear=False,
        )
        self.env_patcher.start()
        self.temp_out = tempfile.TemporaryDirectory()
        self.out = self.temp_out.name
        format_registry.reset_registry()

    def tearDown(self):
        self.env_patcher.stop()
        self.temp_out.cleanup()
        format_registry.reset_registry()

    def test_37_dispatch_to_builtin_builder(self):
        disp = product_builder.get_builder_dispatch("planner")
        self.assertIsNotNone(disp)
        self.assertEqual(disp, product_builder.BUILDERS["planner"])

    def test_38_dispatch_to_custom_registered_builder(self):
        def my_build(spec, folder):
            p = os.path.join(folder, "output.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("My custom builder output")
            return ["output.txt"]

        format_registry.register_format(
            "custom_item", ["custom item"], build_fn=my_build
        )
        disp = product_builder.get_builder_dispatch("custom_item")
        self.assertIsNotNone(disp)

        folder = os.path.join(self.out, "test_item")
        os.makedirs(folder, exist_ok=True)
        files, how_to = disp(folder, {"instructions": ["Read output.txt"]})
        self.assertEqual(files, ["output.txt"])
        self.assertEqual(how_to, ["Read output.txt"])
        self.assertTrue(os.path.isfile(os.path.join(folder, "output.txt")))

    def test_39_dispatch_unknown_format_returns_none(self):
        self.assertIsNone(product_builder.get_builder_dispatch("nonexistent_kind"))

    def test_40_build_product_with_custom_registered_format(self):
        def my_build(spec, folder):
            p = os.path.join(folder, "data.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write('{"test": true}')
            return ["data.json"]

        format_registry.register_format(
            "json_tool", ["json tool"], build_fn=my_build
        )

        spec = {
            "kind": "json_tool",
            "name": "JSON Tool Kit",
            "short_name": "json-tool",
            "cover_image_idea": "A clean view of JSON data",
            "instructions": ["Step 1: Open data.json"],
            "listing": {
                "title": "JSON Tool Kit for Developers",
                "description": "A developer tool product built by format builder.",
                "tags": ["json", "tool", "data", "dev", "tech"],
                "buyer": "developers",
                "platform": "Gumroad",
            },
        }

        folder_name = product_builder.build_product(spec, 777, self.out)
        self.assertEqual(folder_name, "777-json-tool")
        built_dir = os.path.join(self.out, folder_name)
        self.assertTrue(os.path.isdir(built_dir))
        self.assertTrue(os.path.isfile(os.path.join(built_dir, "data.json")))
        self.assertTrue(os.path.isfile(os.path.join(built_dir, "README.md")))
        self.assertTrue(os.path.isfile(os.path.join(built_dir, "listing.md")))
        self.assertTrue(os.path.isfile(os.path.join(built_dir, "LICENSE.txt")))

    def test_41_build_product_raises_for_unregistered_kind(self):
        spec = {
            "kind": "unknown_xyz",
            "short_name": "xyz",
            "listing": {"title": "X", "description": "Y", "tags": []},
        }
        with self.assertRaises(KeyError):
            product_builder.build_product(spec, 999, self.out)


class TestEdgeCasesAndSafety(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in os.environ if k.startswith("GITHUB_")},
            clear=False,
        )
        self.env_patcher.start()
        format_registry.reset_registry()

    def tearDown(self):
        self.env_patcher.stop()
        format_registry.reset_registry()

    def test_42_empty_keywords_in_registration(self):
        res = format_registry.register_format("no_kws", [], build_fn=lambda s, o: [])
        self.assertTrue(res)
        self.assertIn("no_kws", format_registry.list_registered_formats())
        self.assertIsNone(format_registry.classify("some random string"))

    def test_43_whitespace_handling_in_keywords(self):
        format_registry.register_format(
            "spaced_fmt", ["  spaced keyword  ", ""], build_fn=lambda s, o: []
        )
        self.assertEqual(
            format_registry.classify("Here is a spaced keyword in text"),
            "spaced_fmt",
        )

    def test_44_keyword_overlapping_substring(self):
        format_registry.register_format(
            "art_general", ["art"], build_fn=lambda s, o: []
        )
        format_registry.register_format(
            "wall_art", ["wall art"], build_fn=lambda s, o: []
        )
        self.assertEqual(
            format_registry.classify("Beautiful printable wall art for living rooms"),
            "wall_art",
        )

    def test_45_no_secrets_in_format_registry(self):
        source = (Path(__file__).resolve().parent.parent / "format_registry.py").read_text(encoding="utf-8")
        self.assertNotIn("AI_STUDIO_KEY", source)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", source)
        self.assertNotIn("DB_KEY", source)

    def test_46_no_money_in_format_registry(self):
        import re
        source = (Path(__file__).resolve().parent.parent / "format_registry.py").read_text(encoding="utf-8")
        currency_pattern = re.compile(r"(\$|₹|€|£)\s*\d+|\b\d+\s*(USD|INR|EUR|GBP)\b")
        matches = currency_pattern.findall(source)
        self.assertEqual(matches, [], f"Found currency in format_registry.py: {matches}")

    def test_47_builder_returning_non_list_is_handled(self):
        def bad_return(spec, folder):
            return "not-a-list"

        format_registry.register_format("bad_ret", ["bad_ret"], build_fn=bad_return)
        disp = product_builder.get_builder_dispatch("bad_ret")
        folder = tempfile.mkdtemp()
        files, how_to = disp(folder, {})
        self.assertEqual(files, [])

    def test_48_multiple_calls_to_discover_cached(self):
        reg1 = format_registry.discover_builders()
        reg2 = format_registry.discover_builders()
        self.assertEqual(reg1, reg2)

    def test_49_classify_symbols_and_punctuation(self):
        text = "Check out this: [Planner] / (Worksheet) - daily focus!!!"
        self.assertEqual(format_registry.classify(text), "planner")

    def test_50_can_build_with_case_variant_spec_kind(self):
        self.assertTrue(format_registry.can_build({"kind": "PLANNER"}))
        self.assertTrue(format_registry.can_build({"kind": "  sheets  "}))

    def test_51_tokenize_handles_none_and_empty(self):
        self.assertEqual(format_registry._tokenize(None), set())
        self.assertEqual(format_registry._tokenize(""), set())
        self.assertEqual(
            format_registry._tokenize("Hello, World! 123-abc"),
            {"hello", "world", "123-abc"},
        )

    def test_52_all_builtin_formats_have_descriptions(self):
        for fid in ("sheets", "planner", "dated_planner", "notion", "resume"):
            info = format_registry.get_format_info(fid)
            self.assertTrue(len(info["description"]) > 5)

    def test_53_build_product_preserves_existing_planner_builds(self):
        spec = {
            "kind": "planner",
            "name": "Standard Test Planner",
            "short_name": "test-planner",
            "cover_image_idea": "Clean desk layout",
            "pages": [{"title": "Day", "subtitle": "Plan", "sections": [{"heading": "Goals", "type": "lines", "items": [], "count": 3}]}],
            "instructions": ["Print at 100% scale."],
            "listing": {
                "title": "Standard Test Planner Printable",
                "description": "A daily printable planner for focus and productivity.",
                "tags": ["planner", "printable", "daily", "routine", "focus"],
                "buyer": "adults",
                "platform": "Gumroad",
            },
        }
        with tempfile.TemporaryDirectory() as td:
            folder = product_builder.build_product(spec, 555, td)
            self.assertTrue(os.path.isdir(os.path.join(td, folder)))
            self.assertTrue(os.path.isfile(os.path.join(td, folder, "test-planner-A4.pdf")))
            self.assertTrue(os.path.isfile(os.path.join(td, folder, "test-planner-Letter.pdf")))

    def test_54_build_product_preserves_existing_sheets_builds(self):
        raw = json.dumps(SPECS["Budget Sheets Agent"])
        spec, _ = product_builder.parse_spec(raw, "Budget Sheets Agent")
        with tempfile.TemporaryDirectory() as td:
            folder = product_builder.build_product(spec, 556, td)
            self.assertTrue(os.path.isdir(os.path.join(td, folder)))
            self.assertTrue(os.path.isfile(os.path.join(td, folder, f"{spec['short_name']}.xlsx")))

    def test_55_build_product_preserves_existing_resume_builds(self):
        raw = json.dumps(SPECS["Resume Bundle Agent"])
        spec, _ = product_builder.parse_spec(raw, "Resume Bundle Agent")
        with tempfile.TemporaryDirectory() as td:
            folder = product_builder.build_product(spec, 557, td)
            self.assertTrue(os.path.isdir(os.path.join(td, folder)))
            self.assertTrue(os.path.isfile(os.path.join(td, folder, f"{spec['short_name']}-resume.docx")))

    def test_56_build_product_preserves_existing_notion_builds(self):
        raw = json.dumps(SPECS["Notion Templates Agent"])
        spec, _ = product_builder.parse_spec(raw, "Notion Templates Agent")
        with tempfile.TemporaryDirectory() as td:
            folder = product_builder.build_product(spec, 558, td)
            self.assertTrue(os.path.isdir(os.path.join(td, folder)))
            self.assertTrue(os.path.isfile(os.path.join(td, folder, f"{spec['short_name']}-notion-import.zip")))
            self.assertTrue(os.path.isfile(os.path.join(td, folder, "HOW_TO_IMPORT.md")))

    def test_57_build_product_preserves_existing_dated_planner_builds(self):
        spec = {
            "kind": "dated_planner",
            "name": "2027 Dated Planner",
            "short_name": "planner-2027",
            "cover_image_idea": "Calendar preview",
            "span": "2027",
            "start_day": "Monday",
            "instructions": ["Print at 100% scale."],
            "listing": {
                "title": "2027 Dated Planner Calendar",
                "description": "A dated calendar planner for year 2027.",
                "tags": ["2027 planner", "dated planner", "calendar", "printable", "monthly"],
                "buyer": "teachers",
                "platform": "Gumroad",
            },
        }
        with tempfile.TemporaryDirectory() as td:
            folder = product_builder.build_product(spec, 559, td)
            self.assertTrue(os.path.isdir(os.path.join(td, folder)))
            self.assertTrue(os.path.isfile(os.path.join(td, folder, "planner-2027-A4.pdf")))

    def test_58_pipeline_run_pending_processes_buildable_spec(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as fdb:
            db_path = fdb.name
            aid = guardrails.request_approval(
                "public_draft",
                "Draft: [ADHD Planners Agent] Weekly Plan",
                "Printable daily routine sheets",
                db_path=db_path,
            )
            guardrails.decide(aid, True, db_path=db_path)
            with mock.patch("product_pipeline.launch_pack.build_launch_pack") as mock_build, \
                 mock.patch("product_pipeline.checker.review_passes", return_value=True), \
                 mock.patch("product_pipeline.product_council.get_council_summary", return_value={"verdict": "PASS"}):
                mock_build.return_value = {"status": "failed", "reason": "Stop here"}
                with tempfile.TemporaryDirectory() as tout:
                    results = product_pipeline.run_pending(db_path=db_path, out_dir=tout)
                    app = guardrails.get_approval(aid, db_path=db_path)
                    self.assertNotEqual(app["status"], "needs_owner")

    def test_59_classify_tie_breaker_priority(self):
        self.assertEqual(
            format_registry.classify("Minimalist Notion Daily Life Planner template"),
            "notion",
        )

    def test_60_classify_handles_special_characters_safely(self):
        weird_text = "!@#$%^&*()_+=-`~[]\\{}|;':\",./<>? planner excel sheet"
        fmt = format_registry.classify(weird_text)
        self.assertIn(fmt, ("planner", "sheets"))

    def test_61_can_build_handles_draft_payload_without_headings(self):
        plain_text = "This is a simple checklist for home baking steps and daily routine."
        self.assertTrue(format_registry.can_build(plain_text))


if __name__ == "__main__":
    unittest.main()
