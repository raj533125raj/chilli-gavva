"""Comprehensive unit test suite for BINNY-V5A: make_marketing.py.

Tests cover:
- All 10 per-pack generators (carousel_maker, pin_variants, cover_variants, reel_frames,
  blog_post, shorts_script, landing_page, seo_meta, review_request, launch_checklist)
- All 8 global generators (pin_boards, sitemap_builder, catalog_sheet, gift_guide,
  keyword_bank, dup_check, image_optimize, marketing_index)
- Per-step timeouts and timeout error recovery
- Missing module handling and clean skipping
- Step filtering: --only and --skip
- Dry-run vs actual --optimize
- Atomic make_report.json writing and schema validation
- Summary table rendering and CLI exit codes
- Currency suppression (no money amounts in outputs or reports)
- Safe idempotent executions without touching pack folders
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import types
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_marketing


class _QAStub:
    def __init__(self, verdict: str = "ready"):
        self.verdict = verdict

    def check_folder(self, folder: str):
        return {"verdict": self.verdict, "score": 95, "problems": [], "checked": []}


def create_fake_module(name: str, return_val: Any = None, should_fail: bool = False, delay: float = 0.0):
    """Create a fake module with standard generator function signatures."""
    mod = types.ModuleType(name)

    def fake_func(*args, **kwargs):
        if delay > 0:
            time.sleep(delay)
        if should_fail:
            raise RuntimeError(f"{name} intentional failure")
        return return_val or f"{name}_output"

    fn_map = {
        "carousel_maker": "make_pack_carousel",
        "pin_variants": "generate_pins_for_pack",
        "cover_variants": "make_pack_covers",
        "reel_frames": "make_pack_reel",
        "blog_post": "make_pack_blog",
        "shorts_script": "generate_shorts_script_for_pack",
        "landing_page": "build_landing_page",
        "seo_meta": "generate_pack_seo_meta",
        "review_request": "write_review_request",
        "launch_checklist": "write_launch_checklist",
        "pin_boards": "write_board_plan",
        "sitemap_builder": "build_site_index",
        "catalog_sheet": "build_catalog",
        "gift_guide": "generate_all_seasonal_collections",
        "keyword_bank": "export_keyword_bank",
        "dup_check": "check_duplicate_content",
        "image_optimize": "optimize_marketing_folder",
        "marketing_index": "build_index",
    }
    fn_name = fn_map.get(name, "run")
    setattr(mod, fn_name, fake_func)
    return mod


def create_all_fake_modules(failing: Optional[set] = None, slow: Optional[dict] = None) -> dict[str, Any]:
    failing = failing or set()
    slow = slow or {}
    modules = {}
    for step in make_marketing.ALL_STEPS:
        is_fail = step in failing
        delay = slow.get(step, 0.0)
        modules[step] = create_fake_module(step, should_fail=is_fail, delay=delay)
    return modules


class V5AMarketingAllTests(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* env per Jules rules
        self.env_patch = patch.dict(os.environ, {}, clear=False)
        self.env_patch.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_"):
                del os.environ[k]
        self.addCleanup(self.env_patch.stop)

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packs_dir = self.root / "exported_content" / "products" / "upload"
        self.marketing_dir = self.root / "exported_content" / "marketing"
        self.products_dir = self.root / "exported_content" / "products"
        self.packs_dir.mkdir(parents=True)
        self.marketing_dir.mkdir(parents=True)

        self.qa_patch = patch.object(make_marketing, "_qa_deep", _QAStub("ready"))
        self.qa_patch.start()
        self.addCleanup(self.qa_patch.stop)

    def create_pack(self, name: str = "pack-1", ready: bool = True, has_zip: bool = True, has_listing: bool = True):
        pdir = self.packs_dir / name
        pdir.mkdir(parents=True, exist_ok=True)
        if has_zip:
            zpath = pdir / "product.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr("LICENSE.txt", "personal use licence")
        if has_listing:
            (pdir / "listing.txt").write_text(
                f"Title: Test Pack {name}\n\nDescription:\nFeatures for test.\n\nTags: tag1, tag2\n",
                encoding="utf-8",
            )
        return pdir

    # 1. Sanitization & Currency washing
    def test_sanitize_text_washes_dollar_amounts(self):
        self.assertEqual(make_marketing.sanitize_text("Item costs $25.00 today"), "Item costs [amount] today")

    def test_sanitize_text_washes_currency_words(self):
        self.assertEqual(make_marketing.sanitize_text("Price is 50 USD total"), "Price is [amount] total")
        self.assertEqual(make_marketing.sanitize_text("Earned 100 dollars quickly"), "Earned [amount] quickly")
        self.assertEqual(make_marketing.sanitize_text("Cost is ₹500 only"), "Cost is [amount] only")

    def test_sanitize_text_handles_non_string(self):
        self.assertEqual(make_marketing.sanitize_text(1234), "1234")

    # 2. Timeout helper tests
    def test_run_with_timeout_success(self):
        ok, res, err = make_marketing.run_with_timeout(lambda x: x * 2, args=(5,), timeout=1.0)
        self.assertTrue(ok)
        self.assertEqual(res, 10)
        self.assertIsNone(err)

    def test_run_with_timeout_times_out(self):
        def sleep_fn():
            time.sleep(0.5)
            return "done"
        ok, res, err = make_marketing.run_with_timeout(sleep_fn, timeout=0.05)
        self.assertFalse(ok)
        self.assertIsNone(res)
        self.assertIn("timed out after 0.1s", err)

    def test_run_with_timeout_captures_exception(self):
        def bad_fn():
            raise ValueError("Something broke $10")
        ok, res, err = make_marketing.run_with_timeout(bad_fn, timeout=1.0)
        self.assertFalse(ok)
        self.assertIsNone(res)
        self.assertIn("Something broke [amount]", err)

    # 3. Step filtering: only and skip
    def test_resolve_step_filter_none(self):
        only, skip = make_marketing.resolve_step_filter(None, None)
        self.assertEqual(only, set())
        self.assertEqual(skip, set())

    def test_resolve_step_filter_comma_strings(self):
        only, skip = make_marketing.resolve_step_filter("carousel_maker, blog_post", "pins, seo_meta")
        self.assertEqual(only, {"carousel_maker", "blog_post"})
        self.assertEqual(skip, {"pins", "seo_meta"})

    def test_resolve_step_filter_lists(self):
        only, skip = make_marketing.resolve_step_filter(["carousel_maker", "cover_variants"], ["blog_post"])
        self.assertEqual(only, {"carousel_maker", "cover_variants"})
        self.assertEqual(skip, {"blog_post"})

    def test_is_step_enabled_logic(self):
        only = {"step_a", "step_b"}
        skip = {"step_b"}
        self.assertTrue(make_marketing.is_step_enabled("step_a", only, skip))
        self.assertFalse(make_marketing.is_step_enabled("step_b", only, skip))
        self.assertFalse(make_marketing.is_step_enabled("step_c", only, skip))
        self.assertTrue(make_marketing.is_step_enabled("step_x", set(), set()))
        self.assertFalse(make_marketing.is_step_enabled("step_x", set(), {"step_x"}))

    # 4. Pack steps: single step runs
    def test_run_pack_step_missing_module(self):
        p = self.create_pack("p1")
        res = make_marketing.run_pack_step("non_existent_module", p, self.marketing_dir, modules={})
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["error"], "module missing")

    def test_run_pack_step_success(self):
        p = self.create_pack("p1")
        mods = create_all_fake_modules()
        res = make_marketing.run_pack_step("carousel_maker", p, self.marketing_dir, modules=mods)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["output"], "carousel_maker_output")
        self.assertGreaterEqual(res["duration_sec"], 0.0)

    def test_run_pack_step_failing(self):
        p = self.create_pack("p1")
        mods = create_all_fake_modules(failing={"cover_variants"})
        res = make_marketing.run_pack_step("cover_variants", p, self.marketing_dir, modules=mods)
        self.assertEqual(res["status"], "failed")
        self.assertIn("intentional failure", res["error"])

    def test_run_pack_step_timeout(self):
        p = self.create_pack("p1")
        mods = create_all_fake_modules(slow={"blog_post": 0.2})
        res = make_marketing.run_pack_step("blog_post", p, self.marketing_dir, timeout=0.05, modules=mods)
        self.assertEqual(res["status"], "failed")
        self.assertIn("timed out", res["error"])

    # 5. Pack steps: all 10 steps run individually
    def test_all_10_pack_steps_invocations(self):
        p = self.create_pack("p10")
        mods = create_all_fake_modules()
        for step in make_marketing.PACK_STEPS:
            res = make_marketing.run_pack_step(step, p, self.marketing_dir, modules=mods)
            self.assertEqual(res["status"], "ok", f"Step {step} failed")
            self.assertIsNotNone(res["output"])

    # 6. Global steps: all 8 steps run individually
    def test_all_8_global_steps_invocations(self):
        mods = create_all_fake_modules()
        for step in make_marketing.GLOBAL_STEPS:
            res = make_marketing.run_global_step(step, self.products_dir, self.marketing_dir, modules=mods)
            self.assertEqual(res["status"], "ok", f"Global step {step} failed")
            self.assertIsNotNone(res["output"])

    def test_global_step_image_optimize_dry_run_flag(self):
        mod = create_fake_module("image_optimize")
        called_args = []
        def fake_opt(marketing_dir="exported_content/marketing", dry_run=False):
            called_args.append(dry_run)
            return {"dry_run": dry_run}
        mod.optimize_marketing_folder = fake_opt

        # When optimize=False, dry_run should be True
        res = make_marketing.run_global_step("image_optimize", self.products_dir, self.marketing_dir,
                                            optimize=False, modules={"image_optimize": mod})
        self.assertEqual(res["status"], "ok")
        self.assertTrue(called_args[0])

        # When optimize=True, dry_run should be False
        called_args.clear()
        res = make_marketing.run_global_step("image_optimize", self.products_dir, self.marketing_dir,
                                            optimize=True, modules={"image_optimize": mod})
        self.assertEqual(res["status"], "ok")
        self.assertFalse(called_args[0])

    def test_global_step_missing_module(self):
        res = make_marketing.run_global_step("unknown_step", self.products_dir, self.marketing_dir, modules={})
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["error"], "module missing")

    def test_global_step_timeout(self):
        mods = create_all_fake_modules(slow={"catalog_sheet": 0.2})
        res = make_marketing.run_global_step("catalog_sheet", self.products_dir, self.marketing_dir,
                                            timeout=0.05, modules=mods)
        self.assertEqual(res["status"], "failed")
        self.assertIn("timed out", res["error"])

    # 7. Process pack execution and state
    def test_process_pack_full_success(self):
        p = self.create_pack("full-pack")
        mods = create_all_fake_modules()
        res = make_marketing.process_pack(p, self.marketing_dir, modules=mods)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["errors"]), 0)
        self.assertEqual(len(res["steps"]), 10)
        for sinfo in res["steps"].values():
            self.assertEqual(sinfo["status"], "ok")

    def test_process_pack_with_failing_step_continues_others(self):
        p = self.create_pack("partial-pack")
        mods = create_all_fake_modules(failing={"cover_variants", "shorts_script"})
        res = make_marketing.process_pack(p, self.marketing_dir, modules=mods)
        self.assertFalse(res["ok"])
        self.assertEqual(res["steps"]["cover_variants"]["status"], "failed")
        self.assertEqual(res["steps"]["shorts_script"]["status"], "failed")
        self.assertEqual(res["steps"]["carousel_maker"]["status"], "ok")
        self.assertEqual(res["steps"]["pin_variants"]["status"], "ok")
        self.assertEqual(res["steps"]["landing_page"]["status"], "ok")

    def test_process_pack_legacy_makers_tuple(self):
        p = self.create_pack("legacy-pack")
        c_mod = create_fake_module("carousel_maker")
        p_mod = create_fake_module("pin_variants")
        res = make_marketing.process_pack(p, self.marketing_dir, makers=(c_mod, p_mod),
                                         modules={"cover_variants": None})
        self.assertEqual(res["carousel"], "carousel_maker_output")
        self.assertEqual(res["pins"], "pin_variants_output")

    def test_process_pack_skips_steps_by_only(self):
        p = self.create_pack("only-pack")
        mods = create_all_fake_modules()
        res = make_marketing.process_pack(p, self.marketing_dir, modules=mods, only="carousel_maker, seo_meta")
        self.assertEqual(res["steps"]["carousel_maker"]["status"], "ok")
        self.assertEqual(res["steps"]["seo_meta"]["status"], "ok")
        self.assertEqual(res["steps"]["pin_variants"]["status"], "skipped")
        self.assertEqual(res["steps"]["blog_post"]["status"], "skipped")

    def test_process_pack_skips_steps_by_skip(self):
        p = self.create_pack("skip-pack")
        mods = create_all_fake_modules()
        res = make_marketing.process_pack(p, self.marketing_dir, modules=mods, skip="carousel_maker, reel_frames")
        self.assertEqual(res["steps"]["carousel_maker"]["status"], "skipped")
        self.assertEqual(res["steps"]["reel_frames"]["status"], "skipped")
        self.assertEqual(res["steps"]["pin_variants"]["status"], "ok")

    # 8. README writing
    def test_write_readme_contains_all_made_statuses(self):
        p = self.create_pack("readme-pack")
        mods = create_all_fake_modules(failing={"blog_post"})
        res = make_marketing.process_pack(p, self.marketing_dir, modules=mods)
        readme = make_marketing.write_readme(self.marketing_dir, "readme-pack", res)
        self.assertTrue(readme.is_file())
        text = readme.read_text(encoding="utf-8")
        self.assertIn("carousel: ok", text)
        self.assertIn("pins: ok", text)
        self.assertIn("covers: ok", text)
        self.assertIn("reel: ok", text)
        self.assertIn("blog: FAILED", text)
        self.assertIn("Pack folder untouched.", text)

    def test_write_readme_does_not_modify_pack_folder(self):
        p = self.create_pack("untouched-pack")
        before = sorted(x.name for x in p.iterdir())
        mods = create_all_fake_modules()
        res = make_marketing.process_pack(p, self.marketing_dir, modules=mods)
        make_marketing.write_readme(self.marketing_dir, "untouched-pack", res)
        after = sorted(x.name for x in p.iterdir())
        self.assertEqual(before, after)

    # 9. Global generators runner
    def test_run_global_generators_all_success(self):
        mods = create_all_fake_modules()
        res = make_marketing.run_global_generators(self.products_dir, self.marketing_dir, modules=mods)
        self.assertEqual(len(res), 8)
        for gname in make_marketing.GLOBAL_STEPS:
            self.assertEqual(res[gname]["status"], "ok")

    def test_run_global_generators_with_filter(self):
        mods = create_all_fake_modules()
        res = make_marketing.run_global_generators(
            self.products_dir, self.marketing_dir, modules=mods, only="sitemap_builder, dup_check"
        )
        self.assertEqual(res["sitemap_builder"]["status"], "ok")
        self.assertEqual(res["dup_check"]["status"], "ok")
        self.assertEqual(res["pin_boards"]["status"], "skipped")
        self.assertEqual(res["marketing_index"]["status"], "skipped")

    def test_run_global_generators_one_failure_does_not_stop_rest(self):
        mods = create_all_fake_modules(failing={"sitemap_builder"})
        res = make_marketing.run_global_generators(self.products_dir, self.marketing_dir, modules=mods)
        self.assertEqual(res["sitemap_builder"]["status"], "failed")
        self.assertEqual(res["pin_boards"]["status"], "ok")
        self.assertEqual(res["catalog_sheet"]["status"], "ok")
        self.assertEqual(res["marketing_index"]["status"], "ok")

    # 10. make_report.json writing and schema
    def test_write_make_report_creates_valid_json(self):
        p_res = [{
            "id": "pack-1",
            "ok": True,
            "errors": [],
            "files": ["slide-1.png"],
            "steps": {"carousel_maker": {"status": "ok", "duration_sec": 0.1}},
        }]
        g_res = {
            "marketing_index": {"status": "ok", "duration_sec": 0.05, "error": None}
        }
        rpt_path = make_marketing.write_make_report(
            self.marketing_dir,
            pack_results=p_res,
            global_results=g_res,
            skipped_notes=["bad-pack: not ready"],
            elapsed_sec=1.23,
            optimize=True,
        )
        self.assertTrue(rpt_path.is_file())
        data = json.loads(rpt_path.read_text(encoding="utf-8"))
        self.assertEqual(data["elapsed_sec"], 1.23)
        self.assertTrue(data["optimize"])
        self.assertEqual(data["total_packs"], 1)
        self.assertEqual(data["skipped_packs"], ["bad-pack: not ready"])
        self.assertEqual(data["summary"]["packs_ok"], 1)
        self.assertEqual(data["summary"]["global_ok"], 1)
        self.assertEqual(data["summary"]["global_failed"], 0)

    # 11. Full run() integration with fakes
    def test_run_single_pack_does_not_run_global_steps(self):
        self.create_pack("pack-solo")
        mods = create_all_fake_modules()
        results, readmes, skipped = make_marketing.run(
            pack_id="pack-solo",
            packs_dir=self.packs_dir,
            marketing_dir=self.marketing_dir,
            modules=mods,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "pack-solo")
        self.assertEqual(len(readmes), 1)
        self.assertEqual(skipped, [])

        report_file = self.marketing_dir / "make_report.json"
        self.assertTrue(report_file.is_file())
        data = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertEqual(data["global_steps"], {})

    def test_run_all_packs_runs_global_steps(self):
        self.create_pack("p-alpha")
        self.create_pack("p-beta")
        mods = create_all_fake_modules()
        results, readmes, skipped = make_marketing.run(
            all_packs=True,
            packs_dir=self.packs_dir,
            marketing_dir=self.marketing_dir,
            modules=mods,
        )
        self.assertEqual(len(results), 2)
        self.assertEqual([r["id"] for r in results], ["p-alpha", "p-beta"])
        self.assertEqual(len(readmes), 2)

        report_file = self.marketing_dir / "make_report.json"
        self.assertTrue(report_file.is_file())
        data = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertEqual(len(data["global_steps"]), 8)

    def test_run_skips_unready_packs(self):
        self.create_pack("ready-pack")
        self.create_pack("unready-pack", has_zip=False)
        mods = create_all_fake_modules()
        results, readmes, skipped = make_marketing.run(
            all_packs=True,
            packs_dir=self.packs_dir,
            marketing_dir=self.marketing_dir,
            modules=mods,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "ready-pack")
        self.assertEqual(len(skipped), 1)
        self.assertIn("unready-pack: not ready", skipped[0])

    def test_run_explicit_missing_pack(self):
        mods = create_all_fake_modules()
        results, readmes, skipped = make_marketing.run(
            pack_id="ghost-pack",
            packs_dir=self.packs_dir,
            marketing_dir=self.marketing_dir,
            modules=mods,
        )
        self.assertEqual(results, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("ghost-pack: pack folder not found", skipped[0])

    def test_run_idempotent_multiple_runs(self):
        self.create_pack("idem-pack")
        mods = create_all_fake_modules()
        res1, readmes1, skip1 = make_marketing.run(
            all_packs=True,
            packs_dir=self.packs_dir,
            marketing_dir=self.marketing_dir,
            modules=mods,
        )
        res2, readmes2, skip2 = make_marketing.run(
            all_packs=True,
            packs_dir=self.packs_dir,
            marketing_dir=self.marketing_dir,
            modules=mods,
        )
        self.assertEqual(len(res1), len(res2))
        self.assertEqual(skip1, skip2)
        readme1_content = readmes1[0].read_text(encoding="utf-8")
        readme2_content = readmes2[0].read_text(encoding="utf-8")
        self.assertEqual(readme1_content, readme2_content)

    # 12. Render table tests
    def test_render_table_formatting_with_globals(self):
        results = [{
            "id": "p1",
            "carousel": "path",
            "pins": "path",
            "files": ["f1", "f2"],
            "errors": [],
            "ok": True,
        }]
        globals_res = {
            "pin_boards": {"status": "ok", "duration_sec": 0.1, "error": None},
            "dup_check": {"status": "failed", "duration_sec": 0.2, "error": "dup fail"},
            "sitemap_builder": {"status": "skipped", "duration_sec": 0.0, "error": "module missing"},
        }
        lines = make_marketing.render_table(results, ["skip note"], global_results=globals_res)
        table_str = "\n".join(lines)
        self.assertIn("p1: carousel ok · pins ok · 2 files · p1/README.txt", table_str)
        self.assertIn("skip skip note", table_str)
        self.assertIn("Global generators:", table_str)
        self.assertIn("pin_boards", table_str)
        self.assertIn("dup_check", table_str)
        self.assertIn("Marketing: 1 of 1 packs fully made, 1 skipped, 1 errors", table_str)

    # 13. CLI execution tests
    def test_cli_requires_pack_or_all(self):
        with self.assertRaises(SystemExit):
            make_marketing.main([])

    def test_cli_pack_success_exit_0(self):
        self.create_pack("cli-pack")
        mods = create_all_fake_modules()
        buf = io.StringIO()
        with patch("make_marketing.load_module", side_effect=lambda name: mods.get(name)):
            with redirect_stdout(buf):
                code = make_marketing.main([
                    "--pack", "cli-pack",
                    "--packs-dir", str(self.packs_dir),
                    "--marketing-dir", str(self.marketing_dir),
                ])
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn("cli-pack:", out)

    def test_cli_pack_failure_exit_1(self):
        self.create_pack("cli-fail")
        mods = create_all_fake_modules(failing={"blog_post"})
        buf = io.StringIO()
        with patch("make_marketing.load_module", side_effect=lambda name: mods.get(name)):
            with redirect_stdout(buf):
                code = make_marketing.main([
                    "--pack", "cli-fail",
                    "--packs-dir", str(self.packs_dir),
                    "--marketing-dir", str(self.marketing_dir),
                ])
        self.assertEqual(code, 1)

    def test_cli_all_with_filter_only(self):
        self.create_pack("pack-only")
        mods = create_all_fake_modules()
        buf = io.StringIO()
        with patch("make_marketing.load_module", side_effect=lambda name: mods.get(name)):
            with redirect_stdout(buf):
                code = make_marketing.main([
                    "--all",
                    "--only", "carousel_maker, marketing_index",
                    "--packs-dir", str(self.packs_dir),
                    "--marketing-dir", str(self.marketing_dir),
                ])
        self.assertEqual(code, 0)
        report_data = json.loads((self.marketing_dir / "make_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report_data["packs"][0]["steps"]["carousel_maker"]["status"], "ok")
        self.assertEqual(report_data["packs"][0]["steps"]["pin_variants"]["status"], "skipped")
        self.assertEqual(report_data["global_steps"]["marketing_index"]["status"], "ok")
        self.assertEqual(report_data["global_steps"]["catalog_sheet"]["status"], "skipped")

    def test_cli_all_with_optimize_flag(self):
        self.create_pack("pack-opt")
        opt_mod = create_fake_module("image_optimize")
        record = []
        def opt_func(marketing_dir=None, dry_run=False):
            record.append(dry_run)
            return {"res": "ok"}
        opt_mod.optimize_marketing_folder = opt_func
        mods = create_all_fake_modules()
        mods["image_optimize"] = opt_mod

        buf = io.StringIO()
        with patch("make_marketing.load_module", side_effect=lambda name: mods.get(name)):
            with redirect_stdout(buf):
                code = make_marketing.main([
                    "--all",
                    "--optimize",
                    "--packs-dir", str(self.packs_dir),
                    "--marketing-dir", str(self.marketing_dir),
                ])
        self.assertEqual(code, 0)
        self.assertEqual(record, [False])

    # 14. Real module integration tests (where available)
    def test_real_modules_can_be_discovered(self):
        for step in make_marketing.ALL_STEPS:
            mod = make_marketing.load_module(step)
            self.assertIsNotNone(mod, f"Real module {step} failed to import")

    def test_real_run_with_minimal_stub_pack(self):
        # Test executing real modules with a mock pack directory
        pack = self.create_pack("real-stub")
        (pack / "preview-1.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
        (pack / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

        # Run non-PIL or lightweight steps
        result = make_marketing.process_pack(
            pack,
            self.marketing_dir,
            only="seo_meta, review_request, launch_checklist",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["steps"]["seo_meta"]["status"], "ok")
        self.assertEqual(result["steps"]["review_request"]["status"], "ok")
        self.assertEqual(result["steps"]["launch_checklist"]["status"], "ok")

        self.assertTrue((self.marketing_dir / "real-stub" / "seo_meta.json").is_file())
        self.assertTrue((self.marketing_dir / "real-stub" / "review_request.md").is_file())
        self.assertTrue((self.marketing_dir / "real-stub" / "LAUNCH.md").is_file())

    def test_real_global_steps_execution(self):
        # Run lightweight global steps
        pack = self.create_pack("global-test")
        (pack / "listing.txt").write_text("Title: Wedding Planner\nDescription: Nice planner\nTags: wedding, bride\n", encoding="utf-8")

        res = make_marketing.run_global_generators(
            self.products_dir,
            self.marketing_dir,
            only="keyword_bank, dup_check, marketing_index",
        )
        self.assertEqual(res["keyword_bank"]["status"], "ok")
        self.assertEqual(res["dup_check"]["status"], "ok")
        self.assertEqual(res["marketing_index"]["status"], "ok")
        self.assertTrue((self.marketing_dir / "keywords.json").is_file())
        self.assertTrue((self.marketing_dir / "dup_report.json").is_file())
        self.assertTrue((self.marketing_dir / "index.html").is_file())

    # 15. Additional edge cases and robustness tests
    def test_empty_packs_dir_all_packs(self):
        res, readmes, skipped = make_marketing.run(
            all_packs=True,
            packs_dir=self.packs_dir,
            marketing_dir=self.marketing_dir,
            modules=create_all_fake_modules(),
        )
        self.assertEqual(res, [])
        self.assertEqual(readmes, [])
        self.assertEqual(skipped, [])

    def test_nonexistent_packs_dir(self):
        bad_dir = self.root / "does_not_exist"
        res, readmes, skipped = make_marketing.run(
            all_packs=True,
            packs_dir=bad_dir,
            marketing_dir=self.marketing_dir,
            modules=create_all_fake_modules(),
        )
        self.assertEqual(res, [])

    def test_made_files_empty_directory(self):
        empty_dir = self.marketing_dir / "empty_pack"
        empty_dir.mkdir(parents=True)
        self.assertEqual(make_marketing.made_files(empty_dir), [])

    def test_made_files_nonexistent_directory(self):
        bad_dir = self.marketing_dir / "no_such_pack"
        self.assertEqual(make_marketing.made_files(bad_dir), [])

    def test_qa_verdict_when_qa_deep_is_none(self):
        with patch.object(make_marketing, "_qa_deep", None):
            self.assertEqual(make_marketing.qa_verdict(self.packs_dir / "any"), "skipped")

    def test_qa_verdict_exception_handling(self):
        class BrokenQA:
            def check_folder(self, folder):
                raise RuntimeError("QA crash")
        with patch.object(make_marketing, "_qa_deep", BrokenQA()):
            self.assertEqual(make_marketing.qa_verdict(self.packs_dir / "any"), "skipped")

    def test_load_makers_returns_tuple(self):
        c, p = make_marketing.load_makers()
        # Modules exist in repo
        self.assertIsNotNone(c)
        self.assertIsNotNone(p)

    def test_no_currency_in_report_json(self):
        p = self.create_pack("currency-check")
        mods = create_all_fake_modules(failing={"cover_variants"})
        # inject money into error
        mods["cover_variants"].make_pack_covers = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Loss of $500.00"))
        res = make_marketing.process_pack(p, self.marketing_dir, modules=mods)
        make_marketing.write_make_report(
            self.marketing_dir,
            pack_results=[res],
            global_results={},
            skipped_notes=[],
            elapsed_sec=0.5,
        )
        content = (self.marketing_dir / "make_report.json").read_text(encoding="utf-8")
        self.assertNotIn("$500", content)
        self.assertIn("[amount]", content)

    # 16. Ten additional tests to exceed 60 unit tests
    def test_select_ready_packs_empty(self):
        self.assertEqual(make_marketing.select_ready_packs(self.root / "empty_dir"), [])

    def test_select_ready_packs_filters_unready(self):
        self.create_pack("ready-p1")
        self.create_pack("not-ready-p2", has_listing=False)
        ready = make_marketing.select_ready_packs(self.packs_dir)
        self.assertEqual([p.name for p in ready], ["ready-p1"])

    def test_atomic_write_json_nested(self):
        data = {"key": {"nested": [1, 2, 3], "flag": True}}
        out_f = self.marketing_dir / "sub" / "test.json"
        res_p = make_marketing.atomic_write_json(out_f, data)
        self.assertEqual(res_p, out_f)
        loaded = json.loads(out_f.read_text(encoding="utf-8"))
        self.assertEqual(loaded, data)

    def test_resolve_step_filter_whitespace(self):
        only, skip = make_marketing.resolve_step_filter("  carousel_maker ,  pin_variants  ", " dup_check ,  ")
        self.assertEqual(only, {"carousel_maker", "pin_variants"})
        self.assertEqual(skip, {"dup_check"})

    def test_run_pack_step_unknown_step_raises(self):
        p = self.create_pack("unk-step")
        fake_mod = types.ModuleType("some_mod")
        res = make_marketing.run_pack_step("weird_step", p, self.marketing_dir, modules={"weird_step": fake_mod})
        self.assertEqual(res["status"], "failed")
        self.assertIn("Unknown pack step: weird_step", res["error"])

    def test_run_global_step_unknown_step_raises(self):
        fake_mod = types.ModuleType("some_global")
        res = make_marketing.run_global_step("weird_global", self.products_dir, self.marketing_dir,
                                            modules={"weird_global": fake_mod})
        self.assertEqual(res["status"], "failed")
        self.assertIn("Unknown global step: weird_global", res["error"])

    def test_render_table_empty_results(self):
        lines = make_marketing.render_table([], [])
        self.assertTrue(lines[-1].startswith("Marketing: 0 of 0 packs fully made"))

    def test_render_table_skipped_packs_only(self):
        lines = make_marketing.render_table([], ["pack-x: not ready", "pack-y: missing"])
        self.assertIn("skip pack-x: not ready", lines)
        self.assertIn("skip pack-y: missing", lines)
        self.assertIn("0 of 0 packs fully made, 2 skipped", lines[-1])

    def test_cli_custom_timeout_flag(self):
        self.create_pack("timeout-pack")
        mods = create_all_fake_modules(slow={"blog_post": 0.2})
        buf = io.StringIO()
        with patch("make_marketing.load_module", side_effect=lambda name: mods.get(name)):
            with redirect_stdout(buf):
                code = make_marketing.main([
                    "--pack", "timeout-pack",
                    "--timeout", "0.05",
                    "--packs-dir", str(self.packs_dir),
                    "--marketing-dir", str(self.marketing_dir),
                ])
        self.assertEqual(code, 1)

    def test_cli_global_step_failure_exits_1(self):
        self.create_pack("good-p")
        mods = create_all_fake_modules(failing={"marketing_index"})
        buf = io.StringIO()
        with patch("make_marketing.load_module", side_effect=lambda name: mods.get(name)):
            with redirect_stdout(buf):
                code = make_marketing.main([
                    "--all",
                    "--packs-dir", str(self.packs_dir),
                    "--marketing-dir", str(self.marketing_dir),
                ])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
