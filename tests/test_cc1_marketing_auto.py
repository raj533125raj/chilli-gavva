"""CC1-1: marketing files made automatically for every READY pack.

Everything runs in temp folders. The text makers (captions, FAQ, landing page, calendar) are the
real modules; the Pin image maker is replaced by a small file-writing stand-in in most tests so
they stay fast and need no Pillow. One test builds a real pack with the real Pin maker (Pillow).
"""
import datetime
import json
import logging
import os
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guardrails
import make_marketing
import marketing_auto as ma
import pin_variants
import product_builder
import product_pipeline

try:
    from PIL import Image
    HAS_PILLOW = True
except Exception:  # pragma: no cover - CI installs Pillow
    Image = None
    HAS_PILLOW = False

REPO = Path(__file__).resolve().parent.parent
NOW = datetime.datetime(2026, 11, 14, 9, 0, tzinfo=datetime.timezone.utc)
LISTING = ("Title: Monthly Budget Sheet Printable\n\nDescription:\nA simple budget sheet with clear "
           "sections.\nFor personal use only.\n\nTags: budget, planner, printable, monthly\n\n"
           "What you get:\n2 PDF files\n")
LISTING_MD = ("# Weekly Focus Planner\n\n**Buyer:** adults  \n**Platform:** Gumroad\n\n## Description\n\n"
              "A calm weekly planner with clear writing sections for personal use.\n\n## Tags (3)\n\n"
              "- planner\n- weekly\n- printable\n\n## What you get\n\n- 4 printable pages\n\n"
              "## Included Files\n\n- `planner-a4.pdf`\n")


class FakePins:
    """Stand-in for pin_variants: same path contract, tiny files, real caption functions."""

    def __init__(self, fail=False, pins=6):
        self.calls = 0
        self.fail = fail
        self.pins = pins
        self.extract_pack_info = pin_variants.extract_pack_info
        self.generate_ab_titles = pin_variants.generate_ab_titles
        self.generate_image_captions_and_alt = pin_variants.generate_image_captions_and_alt

    def generate_pins_for_pack(self, pack_dir, out_dir=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("pin maker broke at $12 step")
        target = Path(out_dir) / "marketing" / Path(pack_dir).name / "pins"
        target.mkdir(parents=True, exist_ok=True)
        meta = []
        for index in range(1, self.pins + 1):
            name = f"pin_{index}_a.png"
            (target / name).write_bytes(b"PNG" + bytes([index]))
            meta.append({"file": name, "layout": "A", "title": f"Pin {index}",
                         "description": "Budget sheet", "alt": "Pin"})
        (target / "pins.json").write_text(json.dumps(meta), encoding="utf-8")
        return {"pins_dir": str(target)}

    def generate_social_variants(self, pack_dir, out_dir=None):
        target = Path(out_dir) / "marketing" / Path(pack_dir).name / "posts"
        target.mkdir(parents=True, exist_ok=True)
        (target / "post_1_hero.png").write_bytes(b"PNGpost")
        return {"posts_dir": str(target)}


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.products = self.root / "exported_content" / "products"
        self.products.mkdir(parents=True)
        self.db = str(self.root / "robot.db")
        self.pins = FakePins()
        self.mods = {"pin_variants": self.pins}
        logging.getLogger("marketing_auto").setLevel(logging.CRITICAL)
        self.addCleanup(logging.getLogger("marketing_auto").setLevel, logging.NOTSET)

    def make_pack(self, name="7-budget-sheet", listing=LISTING, upload=False):
        pack = (self.products / "upload" / name) if upload else (self.products / name)
        pack.mkdir(parents=True)
        with zipfile.ZipFile(pack / "product.zip", "w") as bundle:
            bundle.writestr("budget-a4.pdf", "pdf")
            bundle.writestr("budget-us-letter.pdf", "pdf")
        (pack / "listing.txt").write_text(listing, encoding="utf-8")
        (pack / "LICENSE.txt").write_text("Personal use only.\n", encoding="utf-8")
        return pack

    def build(self, pack, **options):
        options.setdefault("modules", self.mods)
        options.setdefault("now", NOW)
        return ma.build_for_pack(pack, self.db, **options)

    def out(self, pack):
        return self.products / "marketing" / pack.name

    def snapshot(self, folder):
        return {p.relative_to(folder).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in sorted(Path(folder).rglob("*")) if p.is_file()}


class HelperTests(Base):
    def test_approval_id_from_folder_name(self):
        self.assertEqual(ma.approval_id_of("12-budget-sheet"), 12)
        self.assertEqual(ma.approval_id_of("12"), 12)

    def test_approval_id_missing(self):
        self.assertIsNone(ma.approval_id_of("bundle-home"))
        self.assertIsNone(ma.approval_id_of("12abc"))

    def test_products_dir_of_plain_pack(self):
        self.assertEqual(ma.products_dir_of(self.make_pack()), self.products)

    def test_products_dir_of_upload_pack(self):
        self.assertEqual(ma.products_dir_of(self.make_pack(upload=True)), self.products)

    def test_marketing_folder_is_under_products(self):
        pack = self.make_pack()
        self.assertEqual(ma.pack_marketing_dir(pack), self.products / "marketing" / pack.name)

    def test_upload_pack_marketing_folder_is_under_products_not_upload(self):
        pack = self.make_pack(upload=True)
        self.assertEqual(ma.pack_marketing_dir(pack), self.products / "marketing" / pack.name)

    def test_clean_error_removes_money(self):
        self.assertNotIn("$12", ma.clean_error("failed at $12 step"))
        self.assertIn("[amount]", ma.clean_error("failed at $12 step"))

    def test_clean_error_is_short_single_line(self):
        text = ma.clean_error("a\nb " + "x" * 500)
        self.assertNotIn("\n", text)
        self.assertLessEqual(len(text), 200)

    def test_listing_path_prefers_txt(self):
        pack = self.make_pack()
        (pack / "listing.md").write_text(LISTING_MD, encoding="utf-8")
        self.assertEqual(ma.listing_path(pack).name, "listing.txt")

    def test_listing_path_none(self):
        empty = self.products / "9-empty"
        empty.mkdir()
        self.assertIsNone(ma.listing_path(empty))

    def test_module_signature_real_and_fake(self):
        self.assertEqual(len(ma.module_signature(pin_variants)), 64)
        self.assertEqual(ma.module_signature(types.SimpleNamespace()), "")

    def test_resolve_modules_loads_real_and_keeps_overrides(self):
        mods = ma.resolve_modules({"pin_variants": self.pins, "faq_builder": None})
        self.assertIs(mods["pin_variants"], self.pins)
        self.assertIsNone(mods["faq_builder"])
        self.assertEqual(mods["post_planner"].__name__, "post_planner")
        self.assertEqual(sorted(mods), ["calendar_export", "faq_builder", "landing_page",
                                        "pin_variants", "post_planner"])


class PrivacyTests(Base):
    def test_real_gitignore_covers_products_folder(self):
        self.assertIn("exported_content/products", ma.gitignore_dirs())

    def test_real_private_folder_is_private(self):
        self.assertTrue(ma.is_private_dir(REPO / "exported_content" / "products" / "marketing" / "7-x"))

    def test_public_marketing_folder_is_not_private(self):
        self.assertFalse(ma.is_private_dir(REPO / "exported_content" / "marketing" / "7-x"))

    def test_repo_root_is_not_private(self):
        self.assertFalse(ma.is_private_dir(REPO / "docs"))

    def test_folder_outside_repo_is_private(self):
        self.assertTrue(ma.is_private_dir(self.products / "marketing"))

    def test_gitignore_parser_skips_comments_globs_and_negations(self):
        ignore = self.root / ".gitignore"
        ignore.write_text("# note\n*.db\n!site/.gitkeep\nprivate/\n\nlogs/\n", encoding="utf-8")
        self.assertEqual(ma.gitignore_dirs(ignore), ["private", "logs"])

    def test_custom_repo_root_rules(self):
        (self.root / ".gitignore").write_text("exported_content/products/\n", encoding="utf-8")
        self.assertTrue(ma.is_private_dir(self.products / "marketing", repo_root=self.root))
        self.assertFalse(ma.is_private_dir(self.root / "exported_content" / "marketing", repo_root=self.root))

    def test_build_refuses_a_public_folder(self):
        pack = self.make_pack()
        with mock.patch.object(ma, "is_private_dir", return_value=False):
            result = self.build(pack)
        self.assertEqual(result["status"], "failed")
        self.assertIn("not private", result["error"])
        self.assertFalse((self.products / "marketing").exists())
        self.assertEqual(self.pins.calls, 0)


class BuildTests(Base):
    def test_all_five_parts_are_made(self):
        result = self.build(self.make_pack())
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["built"], list(ma.PARTS))
        self.assertEqual(result["error"], "")

    def test_files_really_exist(self):
        pack = self.make_pack()
        self.build(pack)
        out = self.out(pack)
        for name in ("pins/pin_1_a.png", "pins/pins.json", "posts/post_1_hero.png", "captions.txt",
                     "FAQ.md", "landing/index.html", "calendar/post_plan.json",
                     "calendar/post_plan.ics", "calendar/post_plan.csv", "manifest.json"):
            self.assertTrue((out / name).is_file(), name)
            self.assertGreater((out / name).stat().st_size, 0, name)

    def test_line_is_tenglish_and_counts_real_files(self):
        result = self.build(self.make_pack())
        self.assertEqual(result["line"], "Marketing files ready: 6 Pins, 1 Posts, 30 rojula plan, "
                                         "FAQ, landing page, captions")

    def test_run_for_pack_returns_the_line(self):
        line = ma.run_for_pack(self.make_pack(), self.db, modules=self.mods, now=NOW)
        self.assertTrue(line.startswith("Marketing files ready: 6 Pins"))
        self.assertIn("30 rojula plan", line)

    def test_run_for_pack_default_db_argument_is_optional(self):
        with mock.patch.object(ma, "record") as saved:
            line = ma.run_for_pack(self.make_pack(), modules=self.mods, now=NOW)
        self.assertIn("FAQ", line)
        self.assertIsNone(saved.call_args[0][1])

    def test_pin_count_follows_the_files(self):
        self.pins.pins = 4
        self.assertIn("4 Pins", self.build(self.make_pack())["line"])

    def test_pack_folder_is_untouched(self):
        pack = self.make_pack()
        before = self.snapshot(pack)
        self.build(pack)
        self.assertEqual(self.snapshot(pack), before)

    def test_upload_pack_is_supported(self):
        pack = self.make_pack(upload=True)
        result = self.build(pack)
        self.assertEqual(result["status"], "ready")
        self.assertTrue((self.products / "marketing" / pack.name / "FAQ.md").is_file())
        self.assertFalse((self.products / "upload" / "marketing").exists())

    def test_captions_have_english_and_tenglish_for_six_pins(self):
        pack = self.make_pack()
        self.build(pack)
        text = (self.out(pack) / "captions.txt").read_text(encoding="utf-8")
        self.assertEqual(text.count("== Pin "), 6)
        self.assertEqual(text.count("English: "), 6)
        self.assertEqual(text.count("Tenglish: "), 6)
        self.assertIn("Budget Sheet", text)
        self.assertIn("#budget", text)

    def test_captions_match_the_real_pin_maker_wording(self):
        pack = self.make_pack()
        self.build(pack)
        text = (self.out(pack) / "captions.txt").read_text(encoding="utf-8")
        info = pin_variants.extract_pack_info(pack)
        title = pin_variants.generate_ab_titles(info["raw_title"])[0]
        _alt, english, tenglish = pin_variants.generate_image_captions_and_alt(
            info["pack_name"], title, "hero mockup", "pin", info["description"])
        self.assertIn(english, text)
        self.assertIn(tenglish, text)

    def test_faq_is_the_real_buyer_faq(self):
        pack = self.make_pack()
        self.build(pack)
        text = (self.out(pack) / "FAQ.md").read_text(encoding="utf-8")
        self.assertIn("buyer FAQ", text)
        self.assertIn("PDF", text)
        self.assertIn("A4", text)
        self.assertGreaterEqual(text.count("## "), 6)

    def test_landing_page_has_the_listing_title(self):
        pack = self.make_pack()
        self.build(pack)
        html_text = (self.out(pack) / "landing" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Monthly Budget Sheet Printable", html_text)
        self.assertIn("<html", html_text.lower())

    def test_calendar_is_thirty_days_for_this_pack_only(self):
        pack = self.make_pack()
        result = self.build(pack)
        plan = json.loads((self.out(pack) / "calendar" / "post_plan.json").read_text(encoding="utf-8"))
        self.assertEqual({item["pack_id"] for item in plan}, {pack.name})
        self.assertEqual(len({item["date"] for item in plan}), 30)
        self.assertEqual(min(item["date"] for item in plan), "2026-11-14")
        self.assertEqual(result["parts"]["calendar"]["meta"]["days"], 30)

    def test_calendar_ignores_other_packs(self):
        self.make_pack("8-other-pack")
        pack = self.make_pack()
        self.build(pack)
        csv_text = (self.out(pack) / "calendar" / "post_plan.csv").read_text(encoding="utf-8")
        self.assertNotIn("8-other-pack", csv_text)

    def test_calendar_ics_is_a_calendar_and_uses_made_pins(self):
        pack = self.make_pack()
        self.build(pack)
        ics = (self.out(pack) / "calendar" / "post_plan.ics").read_text(encoding="utf-8")
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertIn("BEGIN:VEVENT", ics)
        plan = (self.out(pack) / "calendar" / "post_plan.json").read_text(encoding="utf-8")
        self.assertIn("pin_1_a.png", plan)

    def test_no_money_amounts_in_text_files(self):
        pack = self.make_pack(listing=LISTING.replace("clear sections", "clear sections worth $9 or 5 USD"))
        self.build(pack)
        for name in ("captions.txt", "FAQ.md", "landing/index.html", "calendar/post_plan.csv"):
            text = (self.out(pack) / name).read_text(encoding="utf-8")
            self.assertIsNone(make_marketing.MONEY_RE.search(text), name)

    def test_robot_built_folder_with_listing_md(self):
        pack = self.products / "5-weekly-focus-planner"
        pack.mkdir()
        (pack / "listing.md").write_text(LISTING_MD, encoding="utf-8")
        (pack / "planner-a4.pdf").write_bytes(b"pdf")
        before = self.snapshot(pack)
        result = self.build(pack)
        self.assertEqual(result["status"], "ready")
        self.assertIn("Weekly Focus Planner", (self.out(pack) / "landing" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("Weekly Focus Planner", (self.out(pack) / "captions.txt").read_text(encoding="utf-8"))
        self.assertEqual(self.snapshot(pack), before)
        self.assertFalse((pack / "listing.txt").exists())

    def test_listing_md_converter_fallback(self):
        with mock.patch.dict(sys.modules, {"upload_pack": None}):
            text = ma._listing_txt_from_md(LISTING_MD)
        self.assertIn("Title: Weekly Focus Planner", text)
        self.assertIn("Tags: planner, weekly, printable", text)
        self.assertIn("4 printable pages", text)

    def test_no_temp_folders_left_behind(self):
        pack = self.make_pack()
        with mock.patch.object(tempfile, "mkdtemp", wraps=tempfile.mkdtemp) as made:
            self.build(pack)
        stages = [call for call in made.call_args_list if call.kwargs.get("prefix") == "marketing_auto_"]
        self.assertEqual(len(stages), 1)
        leftovers = [p for p in Path(tempfile.gettempdir()).glob("marketing_auto_*")
                     if (p / pack.name).is_dir() and (p / pack.name / "product.zip").is_file()
                     and str(self.root) in str(p)]
        self.assertEqual(leftovers, [])
        self.assertEqual([p.name for p in self.out(pack).rglob(".tmp_*")], [])


class ManifestTests(Base):
    def test_manifest_lists_every_file_with_sha256(self):
        pack = self.make_pack()
        self.build(pack)
        manifest = json.loads((self.out(pack) / "manifest.json").read_text(encoding="utf-8"))
        on_disk = sorted(p.relative_to(self.out(pack)).as_posix() for p in self.out(pack).rglob("*")
                         if p.is_file() and p.name != "manifest.json")
        self.assertEqual(sorted(manifest["files"]), on_disk)
        for name, digest in manifest["files"].items():
            self.assertEqual(digest, ma._sha_file(self.out(pack) / name), name)

    def test_manifest_parts_and_status(self):
        pack = self.make_pack()
        self.build(pack)
        manifest = ma.load_manifest(self.out(pack))
        self.assertEqual(sorted(manifest["parts"]), sorted(ma.PARTS))
        self.assertEqual(manifest["status"], "ready")
        self.assertTrue(manifest["line"].startswith("Marketing files ready"))
        self.assertTrue(all(part["status"] == "ok" for part in manifest["parts"].values()))

    def test_load_manifest_bad_json(self):
        pack = self.make_pack()
        self.out(pack).mkdir(parents=True)
        (self.out(pack) / "manifest.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(ma.load_manifest(self.out(pack)), {})

    def test_broken_manifest_means_full_rebuild(self):
        pack = self.make_pack()
        self.build(pack)
        (self.out(pack) / "manifest.json").write_text("[]", encoding="utf-8")
        self.assertEqual(self.build(pack)["built"], list(ma.PARTS))

    def test_second_run_builds_nothing_and_writes_nothing(self):
        pack = self.make_pack()
        self.build(pack)
        before = self.snapshot(self.out(pack))
        result = self.build(pack, now=NOW + datetime.timedelta(days=3))
        self.assertEqual(result["built"], [])
        self.assertFalse(result["changed"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.snapshot(self.out(pack)), before)
        self.assertEqual(self.pins.calls, 1)

    def test_second_run_keeps_the_same_line(self):
        pack = self.make_pack()
        first = self.build(pack)["line"]
        self.assertEqual(self.build(pack)["line"], first)

    def test_licence_change_rebuilds_only_faq_and_landing(self):
        pack = self.make_pack()
        self.build(pack)
        (pack / "LICENSE.txt").write_text("Personal and classroom use.\n", encoding="utf-8")
        result = self.build(pack)
        self.assertEqual(result["built"], ["faq", "landing"])
        self.assertEqual(self.pins.calls, 1)
        self.assertIn("classroom", (self.out(pack) / "FAQ.md").read_text(encoding="utf-8"))

    def test_listing_change_keeps_faq(self):
        pack = self.make_pack()
        self.build(pack)
        faq_before = (self.out(pack) / "FAQ.md").stat().st_mtime_ns
        (pack / "listing.txt").write_text(LISTING.replace("Monthly", "Yearly"), encoding="utf-8")
        result = self.build(pack)
        self.assertEqual(result["built"], ["pins", "captions", "landing", "calendar"])
        self.assertEqual((self.out(pack) / "FAQ.md").stat().st_mtime_ns, faq_before)
        self.assertIn("Yearly", (self.out(pack) / "landing" / "index.html").read_text(encoding="utf-8"))

    def test_new_image_rebuilds_image_parts_only(self):
        pack = self.make_pack()
        self.build(pack)
        (pack / "extra-note.txt").write_text("note", encoding="utf-8")
        self.assertEqual(self.build(pack)["built"], ["faq", "landing"])   # file names changed only

    def test_deleted_file_rebuilds_only_that_part(self):
        pack = self.make_pack()
        self.build(pack)
        (self.out(pack) / "captions.txt").unlink()
        result = self.build(pack)
        self.assertEqual(result["built"], ["captions"])
        self.assertTrue((self.out(pack) / "captions.txt").is_file())

    def test_damaged_file_is_repaired(self):
        pack = self.make_pack()
        self.build(pack)
        good = (self.out(pack) / "FAQ.md").read_text(encoding="utf-8")
        (self.out(pack) / "FAQ.md").write_text("changed by hand", encoding="utf-8")
        self.assertEqual(self.build(pack)["built"], ["faq"])
        self.assertEqual((self.out(pack) / "FAQ.md").read_text(encoding="utf-8"), good)

    def test_pin_rebuild_also_refreshes_the_calendar(self):
        pack = self.make_pack()
        self.build(pack)
        (self.out(pack) / "pins" / "pin_2_a.png").unlink()
        self.assertEqual(self.build(pack)["built"], ["pins", "calendar"])

    def test_stale_pin_files_are_removed_on_rebuild(self):
        pack = self.make_pack()
        self.build(pack)
        self.pins.pins = 3
        result = self.build(pack, force=True)
        self.assertEqual(len(list((self.out(pack) / "pins").glob("*.png"))), 3)
        self.assertIn("3 Pins", result["line"])
        self.assertNotIn("pins/pin_6_a.png", ma.load_manifest(self.out(pack))["files"])

    def test_force_rebuilds_everything(self):
        pack = self.make_pack()
        self.build(pack)
        self.assertEqual(self.build(pack, force=True)["built"], list(ma.PARTS))
        self.assertEqual(self.pins.calls, 2)

    def test_maker_code_change_rebuilds_its_part(self):
        pack = self.make_pack()
        self.build(pack)
        real = ma.module_signature

        def changed(module):
            return "new-code" if getattr(module, "__name__", "") == "faq_builder" else real(module)

        with mock.patch.object(ma, "module_signature", side_effect=changed):
            self.assertEqual(self.build(pack)["built"], ["faq", "landing"])

    def test_needs_build_reasons(self):
        pack = self.make_pack()
        self.build(pack)
        entry = ma.load_manifest(self.out(pack))["parts"]["faq"]
        self.assertEqual(ma.needs_build("faq", entry, entry["fingerprint"], self.out(pack)), (False, "unchanged"))
        self.assertEqual(ma.needs_build("faq", None, "x", self.out(pack)), (True, "new"))
        self.assertEqual(ma.needs_build("faq", entry, "other", self.out(pack)), (True, "sources changed"))
        failed = dict(entry, status="failed")
        self.assertEqual(ma.needs_build("faq", failed, entry["fingerprint"], self.out(pack)),
                         (True, "last run failed"))


class FailureTests(Base):
    def test_failing_pin_maker_never_stops_the_others(self):
        self.pins.fail = True
        pack = self.make_pack()
        result = self.build(pack)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["parts"]["pins"]["status"], "failed")
        for part in ("captions", "faq", "landing", "calendar"):
            self.assertEqual(result["parts"][part]["status"], "ok", part)
        self.assertTrue((self.out(pack) / "FAQ.md").is_file())

    def test_partial_line_says_what_is_missing(self):
        self.pins.fail = True
        line = self.build(self.make_pack())["line"]
        self.assertTrue(line.startswith("Marketing files konni ready: "))
        self.assertIn("(inka raledu: Pins)", line)
        self.assertNotIn("0 Pins", line)

    def test_failure_is_logged_without_money(self):
        self.pins.fail = True
        logging.getLogger("marketing_auto").setLevel(logging.WARNING)
        with self.assertLogs("marketing_auto", level="WARNING") as captured:
            self.build(self.make_pack())
        joined = "\n".join(captured.output)
        self.assertIn("pins failed", joined)
        self.assertNotIn("$12", joined)

    def test_failed_part_is_retried_next_run(self):
        self.pins.fail = True
        pack = self.make_pack()
        self.build(pack)
        self.pins.fail = False
        result = self.build(pack)
        self.assertEqual(result["built"], ["pins", "calendar"])
        self.assertEqual(result["status"], "ready")

    def test_missing_maker_module_is_a_clean_failure(self):
        result = self.build(self.make_pack(), modules={"pin_variants": self.pins, "faq_builder": None})
        self.assertEqual(result["parts"]["faq"]["status"], "failed")
        self.assertIn("missing", result["parts"]["faq"]["error"])
        self.assertEqual(result["parts"]["calendar"]["status"], "ok")

    def test_everything_failing_gives_empty_line(self):
        mods = {name: None for name in ("pin_variants", "faq_builder", "landing_page",
                                        "post_planner", "calendar_export")}
        result = self.build(self.make_pack(), modules=mods)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["line"], "")

    def test_missing_pack_folder_never_raises(self):
        result = self.build(self.products / "99-nothing")
        self.assertEqual(result["status"], "failed")
        self.assertIn("pack folder not found", result["error"])
        self.assertEqual(ma.run_for_pack(self.products / "99-nothing", self.db), "")

    def test_pack_without_listing_is_refused(self):
        pack = self.products / "3-no-listing"
        pack.mkdir()
        (pack / "file.pdf").write_bytes(b"pdf")
        result = self.build(pack)
        self.assertIn("no listing", result["error"])
        self.assertFalse(self.out(pack).exists())

    def test_marketing_folder_itself_is_not_a_pack(self):
        pack = self.make_pack()
        self.build(pack)
        (self.out(pack) / "listing.txt").write_text(LISTING, encoding="utf-8")
        result = self.build(self.out(pack))
        self.assertEqual(result["status"], "failed")
        self.assertFalse((self.products / "marketing" / "marketing").exists())

    def test_symlinked_pack_is_refused(self):
        pack = self.make_pack()
        link = self.products / "6-link"
        try:
            link.symlink_to(pack, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        self.assertEqual(self.build(link)["status"], "failed")

    def test_maker_exception_inside_build_is_caught(self):
        with mock.patch.dict(ma.MAKERS, {"faq": mock.Mock(side_effect=ValueError("boom"))}):
            result = self.build(self.make_pack())
        self.assertEqual(result["parts"]["faq"]["status"], "failed")
        self.assertIn("boom", result["parts"]["faq"]["error"])

    def test_database_problem_never_raises(self):
        with mock.patch.object(ma, "record", side_effect=OSError("disk")):
            result = self.build(self.make_pack())
        self.assertEqual(result["status"], "ready")


class SummaryLineTests(unittest.TestCase):
    def parts(self, **status):
        base = {"pins": {"status": "ok", "meta": {"pins": 6, "posts": 5}},
                "captions": {"status": "ok"}, "faq": {"status": "ok"}, "landing": {"status": "ok"},
                "calendar": {"status": "ok", "meta": {"days": 30}}}
        for name, value in status.items():
            base[name] = {"status": value}
        return base

    def test_full_line(self):
        self.assertEqual(ma.summary_line(self.parts()),
                         "Marketing files ready: 6 Pins, 5 Posts, 30 rojula plan, FAQ, landing page, captions")

    def test_no_posts_no_posts_word(self):
        parts = self.parts()
        parts["pins"]["meta"]["posts"] = 0
        self.assertEqual(ma.summary_line(parts),
                         "Marketing files ready: 6 Pins, 30 rojula plan, FAQ, landing page, captions")

    def test_missing_calendar(self):
        line = ma.summary_line(self.parts(calendar="failed"))
        self.assertNotIn("rojula plan", line)
        self.assertIn("inka raledu: plan", line)

    def test_nothing_made(self):
        self.assertEqual(ma.summary_line({}), "")

    def test_line_has_no_money_and_is_one_line(self):
        line = ma.summary_line(self.parts())
        self.assertIsNone(make_marketing.MONEY_RE.search(line))
        self.assertNotIn("\n", line)

    def test_overall_status(self):
        self.assertEqual(ma.overall_status(self.parts()), "ready")
        self.assertEqual(ma.overall_status(self.parts(faq="failed")), "partial")
        self.assertEqual(ma.overall_status({}), "failed")


class DatabaseTests(Base):
    def test_marketing_ready_is_recorded(self):
        pack = self.make_pack()
        self.build(pack)
        row = ma.status(pack.name, self.db)
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["approval_id"], 7)
        self.assertEqual(row["parts"], {part: "ok" for part in ma.PARTS})
        self.assertTrue(row["line"].startswith("Marketing files ready"))
        self.assertEqual(row["day"], "2026-11-14")
        self.assertGreater(row["files"], 8)

    def test_table_is_made_through_guardrails_connect(self):
        with mock.patch.object(guardrails, "connect", wraps=guardrails.connect) as connect:
            self.build(self.make_pack())
        self.assertTrue(any(call.args and call.args[0] == self.db for call in connect.call_args_list))
        conn = guardrails.connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT status FROM marketing_ready").fetchall(), [("ready",)])

    def test_partial_and_error_are_recorded(self):
        self.pins.fail = True
        pack = self.make_pack()
        self.build(pack)
        row = ma.status(pack.name, self.db)
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["parts"]["pins"], "failed")
        self.assertIn("pins:", row["error"])
        self.assertNotIn("$12", row["error"])

    def test_failed_pack_is_recorded(self):
        self.build(self.products / "99-nothing")
        self.assertEqual(ma.status("99-nothing", self.db)["status"], "failed")

    def test_status_unknown_pack(self):
        self.assertIsNone(ma.status("nope", self.db))

    def test_save_false_writes_no_row(self):
        pack = self.make_pack()
        self.build(pack, save=False)
        self.assertIsNone(ma.status(pack.name, self.db))

    def test_all_status_lists_packs_in_id_order(self):
        self.build(self.make_pack("12-b"))
        self.build(self.make_pack("3-a"))
        self.assertEqual([row["pack"] for row in ma.all_status(self.db)], ["3-a", "12-b"])


class ReadyHookTests(Base):
    """READY packs from the delivery table -> marketing + the line in the READY Telegram message."""

    def setUp(self):
        super().setUp()
        for patch in (mock.patch.object(product_builder, "PRODUCTS_DIR", str(self.products)),
                      mock.patch.dict(product_builder.OWNER_HOLDS, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)
        real = ma.resolve_modules
        patch = mock.patch.object(ma, "resolve_modules",
                                  side_effect=lambda modules=None: real(dict(self.mods, **(modules or {}))))
        patch.start()
        self.addCleanup(patch.stop)
        self.sent = []

    def ready(self, name, notified=0, digest=None, status="ready", approved=True):
        pack = self.make_pack(name)
        aid = guardrails.request_approval("public_draft", f"Draft: [Budget Sheets Agent] {name}", "body",
                                          None, self.db)
        with product_pipeline.connect(self.db) as conn:
            if approved:
                conn.execute("UPDATE approvals SET status='approved' WHERE id=?", (aid,))
            conn.execute("INSERT INTO product_deliveries (approval_id, status, folder, files, digest, notified) "
                         "VALUES (?, ?, ?, '[]', ?, ?)",
                         (aid, status, name, digest if digest is not None
                          else product_pipeline._inventory(str(pack))[1], notified))
        return aid, pack

    def send(self, text):
        self.sent.append(text)
        return True

    def test_ready_message_carries_the_marketing_line(self):
        aid, pack = self.ready("1-budget-sheet")
        product_pipeline.notify_ready(self.db, self.send)
        self.assertEqual(len(self.sent), 1)
        lines = self.sent[0].splitlines()
        self.assertTrue(lines[0].startswith(f"Product ready: #{aid} "))
        self.assertEqual(lines[1], f"/pack {aid}")
        self.assertEqual(lines[2], "Marketing files ready: 6 Pins, 1 Posts, 30 rojula plan, FAQ, "
                                   "landing page, captions")
        self.assertTrue((self.out(pack) / "manifest.json").is_file())

    def test_ready_message_is_sent_once(self):
        self.ready("1-budget-sheet")
        product_pipeline.notify_ready(self.db, self.send)
        product_pipeline.notify_ready(self.db, self.send)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.pins.calls, 1)

    def test_marketing_failure_never_stops_the_ready_message(self):
        aid, _pack = self.ready("1-budget-sheet")
        with mock.patch.object(ma, "run_ready", side_effect=RuntimeError("broken")):
            product_pipeline.notify_ready(self.db, self.send)
        self.assertEqual(self.sent, [f"Product ready: #{aid} Draft: [Budget Sheets Agent] 1-budget-sheet "
                                     f"— files ready to upload\n/pack {aid}"])

    def test_run_ready_itself_never_raises(self):
        self.ready("1-budget-sheet")
        with mock.patch.object(ma, "ready_packs", side_effect=RuntimeError("db gone")):
            self.assertEqual(ma.run_ready(self.db), {})

    def test_partial_marketing_still_sends_ready_with_honest_line(self):
        self.pins.fail = True
        self.ready("1-budget-sheet")
        product_pipeline.notify_ready(self.db, self.send)
        self.assertIn("Marketing files konni ready", self.sent[0])
        self.assertIn("inka raledu: Pins", self.sent[0])

    def test_pack_missing_on_disk_sends_plain_ready_and_writes_nothing(self):
        aid, pack = self.ready("1-budget-sheet")
        import shutil
        shutil.rmtree(pack)
        product_pipeline.notify_ready(self.db, self.send)
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("Marketing", self.sent[0])
        self.assertIsNone(ma.status("1-budget-sheet", self.db))
        self.assertFalse((self.products / "marketing").exists())

    def test_changed_pack_files_get_no_marketing(self):
        _aid, pack = self.ready("1-budget-sheet")
        (pack / "listing.txt").write_text(LISTING + "edited after encryption\n", encoding="utf-8")
        self.assertEqual(ma.run_ready(self.db), {})
        self.assertFalse((self.products / "marketing").exists())

    def test_delivery_without_digest_gets_no_marketing(self):
        self.ready("1-budget-sheet", digest="")
        self.assertEqual(ma.run_ready(self.db), {})
        self.assertEqual(self.pins.calls, 0)

    def test_backfill_is_three_packs_per_run(self):
        ids = [self.ready(f"{n}-pack-{n}", notified=1)[0] for n in range(1, 6)]
        first = ma.run_ready(self.db, now=NOW)
        self.assertEqual(sorted(first), ids[:3])
        self.assertEqual(self.pins.calls, 3)
        second = ma.run_ready(self.db, now=NOW)
        self.assertEqual(sorted(second), ids)
        self.assertEqual(self.pins.calls, 5)
        ma.run_ready(self.db, now=NOW)
        self.assertEqual(self.pins.calls, 5)

    def test_unsent_ready_pack_goes_before_backfill(self):
        for n in range(1, 4):
            self.ready(f"{n}-old-{n}", notified=1)
        new_id, _pack = self.ready("4-new-pack", notified=0)
        lines = ma.run_ready(self.db, limit=1, now=NOW)
        self.assertEqual(list(lines), [new_id])

    def test_limit_zero_builds_nothing(self):
        self.ready("1-budget-sheet")
        self.assertEqual(ma.run_ready(self.db, limit=0), {})
        self.assertEqual(self.pins.calls, 0)

    def test_not_ready_and_not_approved_packs_are_skipped(self):
        self.ready("1-prepared", status="prepared")
        self.ready("2-unapproved", approved=False)
        self.assertEqual(ma.run_ready(self.db), {})
        self.assertEqual(ma.ready_packs(self.db), [])

    def test_owner_hold_pack_is_skipped(self):
        aid, _pack = self.ready("1-budget-sheet")
        with mock.patch.dict(product_builder.OWNER_HOLDS, {aid: "NOT FOR SALE"}):
            self.assertEqual(ma.run_ready(self.db), {})
        self.assertEqual(self.pins.calls, 0)

    def test_unsafe_folder_names_are_skipped(self):
        aid, _pack = self.ready("1-budget-sheet")
        with product_pipeline.connect(self.db) as conn:
            conn.execute("UPDATE product_deliveries SET folder='../1-budget-sheet' WHERE approval_id=?", (aid,))
        self.assertEqual(ma.run_ready(self.db), {})

    def test_failed_marketing_retries_once_per_day(self):
        self.pins.fail = True
        aid, _pack = self.ready("1-budget-sheet", notified=1)
        ma.run_ready(self.db, now=NOW)
        ma.run_ready(self.db, now=NOW)
        self.assertEqual(self.pins.calls, 1)
        self.pins.fail = False
        lines = ma.run_ready(self.db, now=NOW + datetime.timedelta(days=1))
        self.assertEqual(self.pins.calls, 2)
        self.assertTrue(lines[aid].startswith("Marketing files ready"))

    def test_lost_marketing_folder_is_rebuilt(self):
        _aid, pack = self.ready("1-budget-sheet", notified=1)
        ma.run_ready(self.db, now=NOW)
        import shutil
        shutil.rmtree(self.out(pack))
        ma.run_ready(self.db, now=NOW)
        self.assertTrue((self.out(pack) / "FAQ.md").is_file())

    def test_ready_packs_without_delivery_table(self):
        self.assertEqual(ma.ready_packs(self.db), [])

    def test_hook_uses_the_private_products_folder(self):
        _aid, pack = self.ready("1-budget-sheet")
        product_pipeline.notify_ready(self.db, self.send)
        made = sorted(p.name for p in self.products.iterdir())
        self.assertEqual(made, ["1-budget-sheet", "marketing"])
        self.assertFalse((self.root / "exported_content" / "marketing").exists())

    def test_delivery_digest_is_unchanged_by_marketing(self):
        _aid, pack = self.ready("1-budget-sheet")
        before = product_pipeline._inventory(str(pack))
        product_pipeline.notify_ready(self.db, self.send)
        self.assertEqual(product_pipeline._inventory(str(pack)), before)


class CommandLineTests(Base):
    def run_main(self, *argv):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ma.main(list(argv))
        return code, buffer.getvalue()

    def test_pack_command_prints_line_and_parts(self):
        pack = self.make_pack()
        real = ma.resolve_modules
        with mock.patch.object(ma, "resolve_modules", side_effect=lambda modules=None: real(self.mods)):
            code, text = self.run_main("--pack", str(pack), "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("Marketing files ready", text)
        self.assertIn("calendar", text)

    def test_status_command(self):
        self.build(self.make_pack())
        code, text = self.run_main("--status", "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("7-budget-sheet: ready", text)

    def test_status_command_empty(self):
        code, text = self.run_main("--status", "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("Inka marketing files record kaaledu", text)

    def test_backfill_command_with_nothing_ready(self):
        code, text = self.run_main("--backfill", "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("levu", text)


@unittest.skipUnless(HAS_PILLOW, "needs Pillow for real Pin images")
class RealPackTests(Base):
    """No stand-ins: the real Pin, caption, FAQ, landing and calendar makers on a real temp pack."""

    def test_real_pack_gets_real_files(self):
        pack = self.make_pack("21-budget-sheet")
        Image.new("RGB", (800, 1000), (240, 230, 210)).save(pack / "cover.png")
        Image.new("RGB", (800, 1000), (210, 230, 240)).save(pack / "preview-1.png")
        before = self.snapshot(pack)
        line = ma.run_for_pack(pack, self.db, now=NOW)
        out = self.out(pack)
        self.assertEqual(line, "Marketing files ready: 11 Pins, 5 Posts, 30 rojula plan, FAQ, "
                               "landing page, captions")
        pins = sorted((out / "pins").glob("*.png"))
        self.assertEqual(len(pins), 11)
        self.assertEqual(len(list((out / "pins").glob("variant_pin_*.png"))), 6)
        for path in pins:
            with Image.open(path) as image:
                self.assertEqual(image.size, (1000, 1500), path.name)
        with Image.open(out / "posts" / "post_1_hero.png") as image:
            self.assertEqual(image.size, (1080, 1080))
        self.assertTrue((out / "landing" / "cover.png").is_file())
        self.assertIn("Monthly Budget Sheet Printable", (out / "landing" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("BEGIN:VCALENDAR", (out / "calendar" / "post_plan.ics").read_text(encoding="utf-8"))
        self.assertEqual((out / "captions.txt").read_text(encoding="utf-8").count("Tenglish: "), 6)
        self.assertIn("buyer FAQ", (out / "FAQ.md").read_text(encoding="utf-8"))
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["files"]), sum(1 for p in out.rglob("*") if p.is_file()) - 1)
        self.assertEqual(ma.status(pack.name, self.db)["status"], "ready")
        self.assertEqual(self.snapshot(pack), before)
        # second run: nothing rebuilt, nothing rewritten
        stamp = self.snapshot(out)
        result = ma.build_for_pack(pack, self.db, now=NOW)
        self.assertEqual(result["built"], [])
        self.assertEqual(self.snapshot(out), stamp)


if __name__ == "__main__":
    unittest.main()
