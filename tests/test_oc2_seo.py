"""Offline tests for seo_helper.py and tg_seo.py (OC2-2)."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import seo_helper
import tg_seo


LISTING = (
    "Title: Daily Habit Tracker\n"
    "Sections: daily planner, weekly schedule, goal setting, habit tracker, "
    "monthly overview pages.\n"
    "README: Print at home and organize your week. Includes checklist and "
    "journal pages for home and office."
)

BANNED_LOW = ("etsy", "gumroad", "payhip", "notion", "canva", "pinterest", "amazon")
MONEY_RE = re.compile(r"[₹$€£]|\b(?:USD|INR|EUR|GBP|Rs\.?)\b")


def sample_content(text=LISTING):
    return {
        "pack_number": 3,
        "folder": "3-daily-habit-tracker",
        "title": seo_helper._extract_title(text, "3-daily-habit-tracker"),
        "sections": seo_helper._extract_sections(text),
        "combined": text,
        "sources": ["listing.txt"],
    }


def sample_output():
    return seo_helper.build_seo(sample_content(), pack_number=3)


def write_pack(root, number=3, name=None, files=None, zipped=None):
    folder = Path(root) / (name or f"{number}-daily-habit-tracker")
    folder.mkdir(parents=True, exist_ok=True)
    for filename, body in (files or {}).items():
        target = folder / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            target.write_bytes(body)
        else:
            target.write_text(body, encoding="utf-8")
    if zipped:
        with zipfile.ZipFile(folder / "product.zip", "w") as archive:
            for filename, body in zipped.items():
                archive.writestr(filename, body)
    return folder


class CleaningTests(unittest.TestCase):
    def test_removes_dollar_amount(self):
        self.assertNotIn("$5", seo_helper._remove_money("Habit Tracker $5 Monthly"))

    def test_removes_usd(self):
        self.assertNotIn("10 USD", seo_helper._remove_money("planner for 10 USD"))

    def test_removes_rupees(self):
        self.assertNotIn("100", seo_helper._remove_money("pack Rs 100"))

    def test_keeps_plain_digits(self):
        self.assertIn("12 pages", seo_helper._remove_money("pack of 12 pages"))

    def test_empty_money(self):
        self.assertEqual(seo_helper._remove_money(""), "")

    def test_removes_decimal_money(self):
        self.assertNotIn("$4.99", seo_helper._remove_money("only $4.99 today"))

    def test_removes_brand(self):
        self.assertNotIn("notion", seo_helper._remove_brands("use Notion").lower())

    def test_brand_case_insensitive(self):
        self.assertNotIn("etsy", seo_helper._remove_brands("on Etsy now").lower())

    def test_keeps_normal_word(self):
        self.assertIn("template", seo_helper._remove_brands("notion template"))

    def test_removes_claim(self):
        self.assertNotIn("best seller", seo_helper._remove_claims("our best seller pack").lower())

    def test_removes_hash_one(self):
        self.assertNotIn("#1", seo_helper._remove_claims("the #1 choice"))

    def test_removes_guaranteed(self):
        self.assertNotIn("guaranteed", seo_helper._remove_claims("guaranteed results").lower())

    def test_clean_text_removes_all(self):
        out = seo_helper._clean_text("Best $5 Notion template, #1 best seller")
        for item in ("$", "notion", "best seller", "#1"):
            self.assertNotIn(item, out.lower())

    def test_clean_text_collapses_spaces(self):
        self.assertEqual(seo_helper._clean_text("a    b\t\tc"), "a b c")

    def test_slug_lowercase_hyphens(self):
        self.assertEqual(seo_helper._clean_slug("Daily Habit Tracker"), "daily-habit-tracker")

    def test_slug_strips_brand(self):
        self.assertNotIn("notion", seo_helper._clean_slug("Notion Habit Tracker"))

    def test_slug_max_len(self):
        self.assertLessEqual(len(seo_helper._clean_slug("very long title with many many words here", max_len=20)), 20)

    def test_slug_no_money(self):
        self.assertNotIn("$", seo_helper._clean_slug("planner $5 pack"))

    def test_contains_brand(self):
        self.assertIn("notion", seo_helper._contains_banned("use Notion"))

    def test_contains_claim(self):
        self.assertIn("best seller", seo_helper._contains_banned("our best seller pack"))

    def test_contains_money(self):
        self.assertIn("money", seo_helper._contains_banned("cost $10 today"))

    def test_clean_text_has_no_banned(self):
        self.assertEqual(seo_helper._contains_banned("daily habit tracker pages"), [])

    def test_blocklists_nonempty(self):
        self.assertGreater(len(seo_helper.BRAND_BLOCKLIST), 20)
        self.assertGreater(len(seo_helper.FAKE_CLAIMS_BLOCKLIST), 10)


class KeywordTests(unittest.TestCase):
    def test_total_at_least_40(self):
        self.assertGreaterEqual(seo_helper.build_keyword_set(sample_content())["total"], 40)

    def test_total_matches_lists(self):
        kw = seo_helper.build_keyword_set(sample_content())
        self.assertEqual(kw["total"], len(kw["head"]) + len(kw["long_tail"]))

    def test_head_phrases_multiword(self):
        for item in seo_helper.build_keyword_set(sample_content())["head"]:
            self.assertGreaterEqual(len(item.split()), 2)

    def test_long_tail_multiword(self):
        for item in seo_helper.build_keyword_set(sample_content())["long_tail"]:
            self.assertGreaterEqual(len(item.split()), 2)

    def test_no_duplicates(self):
        kw = seo_helper.build_keyword_set(sample_content())
        self.assertEqual(len(kw["all"]), len(set(kw["all"])))

    def test_thin_text_40_plus(self):
        self.assertGreaterEqual(seo_helper.build_keyword_set({"title": "planner", "sections": [], "combined": "planner"})["total"], 40)

    def test_empty_text_40_plus(self):
        self.assertGreaterEqual(seo_helper.build_keyword_set("")["total"], 40)

    def test_no_brands(self):
        kw = seo_helper.build_keyword_set({"title": "Notion Canva habit tracker", "sections": [], "combined": "Use Notion and Canva"})
        for item in kw["all"]:
            self.assertNotIn("notion", item)
            self.assertNotIn("canva", item)

    def test_no_money(self):
        kw = seo_helper.build_keyword_set({"title": "Habit tracker $5 10 USD", "sections": [], "combined": "only $5"})
        for item in kw["all"]:
            self.assertNotIn("$", item)
            self.assertNotIn("usd", item)

    def test_lowercase(self):
        for item in seo_helper.build_keyword_set("Habit Tracker and Goal Setting")["all"]:
            self.assertEqual(item, item.lower())

    def test_content_words_lead(self):
        kw = seo_helper.build_keyword_set(sample_content())
        self.assertTrue(any("habit" in item for item in kw["head"]))

    def test_metadata_labels_not_tags(self):
        kw = seo_helper.build_keyword_set(sample_content())
        self.assertNotIn("readme", kw["all"])
        self.assertNotIn("sections", kw["all"])


class TitleTests(unittest.TestCase):
    def test_etsy_ten(self):
        self.assertEqual(len(seo_helper.build_title_variants("Daily Habit Tracker", "etsy")["variants"]), 10)

    def test_gumroad_ten(self):
        self.assertEqual(len(seo_helper.build_title_variants("Daily Habit Tracker", "gumroad")["variants"]), 10)

    def test_payhip_ten(self):
        self.assertEqual(len(seo_helper.build_title_variants("Daily Habit Tracker", "payhip")["variants"]), 10)

    def test_etsy_limit(self):
        for variant in seo_helper.build_title_variants("Daily Habit Tracker", "etsy")["variants"]:
            self.assertLessEqual(len(variant), 140)

    def test_gumroad_limit(self):
        for variant in seo_helper.build_title_variants("Daily Habit Tracker", "gumroad")["variants"]:
            self.assertLessEqual(len(variant), 100)

    def test_payhip_limit(self):
        for variant in seo_helper.build_title_variants("Daily Habit Tracker", "payhip")["variants"]:
            self.assertLessEqual(len(variant), 120)

    def test_variants_distinct(self):
        variants = seo_helper.build_title_variants("Daily Habit Tracker", "etsy")["variants"]
        self.assertEqual(len(variants), len({v.lower() for v in variants}))

    def test_variants_contain_base(self):
        variants = seo_helper.build_title_variants("Daily Habit Tracker", "etsy")["variants"]
        self.assertTrue(any("habit" in v.lower() for v in variants))

    def test_unknown_style_falls_back(self):
        self.assertEqual(seo_helper.build_title_variants("Daily Habit Tracker", "unknown")["store_style"], "etsy")

    def test_variants_no_brands(self):
        for variant in seo_helper.build_title_variants("Notion Habit Tracker", "etsy")["variants"]:
            self.assertFalse(seo_helper._contains_banned(variant))

    def test_long_title_fits(self):
        long_title = "Minimalist weekly meal planner and grocery list printable pack for busy families"
        for variant in seo_helper.build_title_variants(long_title, "gumroad")["variants"]:
            self.assertLessEqual(len(variant), 100)

    def test_all_styles_keys(self):
        self.assertEqual(set(seo_helper.build_all_title_variants("Daily Habit Tracker")), {"etsy", "gumroad", "payhip"})

    def test_empty_title(self):
        self.assertEqual(len(seo_helper.build_title_variants("", "etsy")["variants"]), 10)

    def test_prefix_title_kept(self):
        variants = seo_helper.build_title_variants("Planner", "etsy")["variants"]
        self.assertTrue(any(v.lower().startswith("printable") for v in variants))

    def test_no_money_in_titles(self):
        for variant in seo_helper.build_title_variants("Planner $5", "etsy")["variants"]:
            self.assertIsNone(MONEY_RE.search(variant))


class TagTests(unittest.TestCase):
    def test_thirteen(self):
        self.assertEqual(len(seo_helper.build_etsy_tag_set(sample_content())["tags"]), 13)

    def test_max_20_chars(self):
        for tag in seo_helper.build_etsy_tag_set(sample_content())["tags"]:
            self.assertLessEqual(len(tag), 20)

    def test_unique(self):
        tags = seo_helper.build_etsy_tag_set(sample_content())["tags"]
        self.assertEqual(len(tags), len(set(tags)))

    def test_lowercase(self):
        for tag in seo_helper.build_etsy_tag_set(sample_content())["tags"]:
            self.assertEqual(tag, tag.lower())

    def test_no_spaces(self):
        for tag in seo_helper.build_etsy_tag_set(sample_content())["tags"]:
            self.assertNotIn(" ", tag)

    def test_no_brands(self):
        for tag in seo_helper.build_etsy_tag_set({"title": "Notion Canva Etsy planner", "sections": [], "combined": "Notion Canva"})["tags"]:
            self.assertFalse(seo_helper._contains_banned(tag))

    def test_thin_still_13(self):
        self.assertEqual(len(seo_helper.build_etsy_tag_set("planner")["tags"]), 13)

    def test_empty_still_13(self):
        self.assertEqual(len(seo_helper.build_etsy_tag_set("")["tags"]), 13)

    def test_custom_count(self):
        self.assertEqual(len(seo_helper.build_etsy_tag_set(sample_content(), count=5)["tags"]), 5)

    def test_no_money(self):
        for tag in seo_helper.build_etsy_tag_set("planner $5", count=13)["tags"]:
            self.assertIsNone(MONEY_RE.search(tag))


class MetaTests(unittest.TestCase):
    def test_range_listing(self):
        meta = seo_helper.build_meta_description("Daily Habit Tracker", "daily habit tracker")
        self.assertGreaterEqual(meta["char_length"], 150)
        self.assertLessEqual(meta["char_length"], 160)

    def test_chars_match(self):
        meta = seo_helper.build_meta_description("Daily Habit Tracker", "daily habit tracker")
        self.assertEqual(meta["char_length"], len(meta["description"]))

    def test_no_brands(self):
        meta = seo_helper.build_meta_description("Notion Planner", "etsy planner")
        self.assertFalse(seo_helper._contains_banned(meta["description"]))

    def test_no_money(self):
        meta = seo_helper.build_meta_description("Planner $5", "planner")
        self.assertIsNone(MONEY_RE.search(meta["description"]))

    def test_long_title_fits(self):
        title = "Minimalist Weekly Meal Planner and Grocery List Printable Pack for Busy Families"
        self.assertLessEqual(seo_helper.build_meta_description(title, "meal planner")["char_length"], 160)

    def test_empty_title(self):
        meta = seo_helper.build_meta_description("", "")
        self.assertGreaterEqual(meta["char_length"], 150)

    def test_ends_clean(self):
        meta = seo_helper.build_meta_description("Daily Habit Tracker", "habit tracker")
        self.assertEqual(meta["description"], meta["description"].strip())


META_TITLES = [
    "Planner", "Daily Habit Tracker", "ADHD Planner for Adults",
    "Minimalist Weekly Meal Planner", "Digital Budget Tracker",
    "Printable Wall Art Set", "Study Schedule Template",
    "Home Office Organizer", "Self Care Journal", "Project Task Tracker",
]


def _meta_case(title):
    def test(self):
        meta = seo_helper.build_meta_description(title, "planner")
        self.assertGreaterEqual(meta["char_length"], 150)
        self.assertLessEqual(meta["char_length"], 160)
    return test


for index, title in enumerate(META_TITLES):
    setattr(MetaTests, f"test_generated_range_{index}", _meta_case(title))


class PinterestTests(unittest.TestCase):
    def test_five_boards(self):
        self.assertEqual(len(seo_helper.build_pinterest_info("habit tracker", "Daily Habit Tracker")["boards"]), 5)

    def test_ten_pins(self):
        self.assertEqual(len(seo_helper.build_pinterest_info("habit tracker", "Daily Habit Tracker")["pin_keywords"]), 10)

    def test_no_brands(self):
        data = seo_helper.build_pinterest_info("Notion planner", "Etsy Planner")
        for item in data["boards"] + data["pin_keywords"]:
            self.assertFalse(seo_helper._contains_banned(item))

    def test_unique(self):
        data = seo_helper.build_pinterest_info("habit tracker", "Daily Habit Tracker")
        self.assertEqual(len(data["pin_keywords"]), len(set(data["pin_keywords"])))

    def test_contains_keyword(self):
        data = seo_helper.build_pinterest_info("habit tracker", "Daily Habit Tracker")
        self.assertTrue(any("habit" in item.lower() for item in data["pin_keywords"]))

    def test_sections_used(self):
        data = seo_helper.build_pinterest_info("planner", "Planner", ["budget page", "meal plan"])
        self.assertIn("budget page", [item.lower() for item in data["pin_keywords"]])


class AltTextTests(unittest.TestCase):
    def test_default_roles(self):
        self.assertEqual(len(seo_helper.build_alt_texts("Daily Habit Tracker", "habit tracker")["alts"]), 5)

    def test_custom_roles(self):
        data = seo_helper.build_alt_texts("Planner", "planner", ["cover", "mockup"])
        self.assertEqual(data["roles"], ["cover", "mockup"])

    def test_no_brands(self):
        data = seo_helper.build_alt_texts("Notion Planner", "etsy planner")
        for alt in data["alts"]:
            self.assertFalse(seo_helper._contains_banned(alt))

    def test_not_empty(self):
        for alt in seo_helper.build_alt_texts("", "")["alts"]:
            self.assertTrue(alt.strip())

    def test_preview_uses_sections(self):
        data = seo_helper.build_alt_texts("Planner", "planner", ["preview-1"], ["budget page"])
        self.assertIn("budget page", data["alts"][0].lower())

    def test_unknown_role(self):
        data = seo_helper.build_alt_texts("Planner", "planner", ["banner"])
        self.assertIn("banner", data["alts"][0].lower())


class ReadabilityTests(unittest.TestCase):
    def test_empty_warning(self):
        self.assertTrue(seo_helper.check_readability("")["warnings"])

    def test_short_sentence_ok(self):
        self.assertEqual(seo_helper.check_readability("Plan your day. Print the pages.")["warnings"], [])

    def test_long_sentence_warning(self):
        text = " ".join(["word"] * 40) + "."
        self.assertTrue(seo_helper.check_readability(text)["warnings"])

    def test_long_words_warning(self):
        text = "extraordinarily incomprehensible characteristically."
        self.assertTrue(seo_helper.check_readability(text)["warnings"])

    def test_metrics_present(self):
        data = seo_helper.check_readability("Plan your day. Print the pages.")
        self.assertIn("avg_sentence_len", data)
        self.assertIn("long_word_ratio", data)


class StuffingTests(unittest.TestCase):
    def test_high_density_warning(self):
        data = seo_helper.check_keyword_stuffing("planner " * 30, "planner")
        self.assertTrue(data["warnings"])

    def test_low_density_warning(self):
        text = " ".join(["other"] * 300)
        self.assertTrue(seo_helper.check_keyword_stuffing(text, "planner")["warnings"])

    def test_normal_density_clean(self):
        text = (
            "This daily organizer helps you plan the week, track goals and keep a simple layout. "
            "Use the pages at your desk, at home or in the office. Print only the pages you need "
            "and write your tasks in the morning. The clean design keeps your focus on the work "
            "that matters, so the planner stays useful without extra clutter."
        )
        self.assertEqual(seo_helper.check_keyword_stuffing(text, "planner")["warnings"], [])

    def test_empty(self):
        self.assertTrue(seo_helper.check_keyword_stuffing("", "planner")["warnings"])

    def test_count_and_density(self):
        data = seo_helper.check_keyword_stuffing("planner planner other", "planner")
        self.assertGreaterEqual(data["count"], 1)


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="oc2-seo-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_find_exact(self):
        write_pack(self.root, 3)
        self.assertEqual(seo_helper.find_pack_dir(3, self.root).name, "3-daily-habit-tracker")

    def test_find_upload(self):
        pack = write_pack(self.root / "upload", 5)
        self.assertEqual(seo_helper.find_pack_dir(5, self.root), pack)

    def test_no_prefix_collision(self):
        write_pack(self.root, 30)
        self.assertIsNone(seo_helper.find_pack_dir(3, self.root))

    def test_missing_returns_none(self):
        self.assertIsNone(seo_helper.find_pack_dir(9, self.root))

    def test_read_listing(self):
        write_pack(self.root, 3, files={"listing.txt": LISTING})
        content = seo_helper.read_pack_content(3, self.root)
        self.assertIn("listing.txt", content["sources"])

    def test_read_readme(self):
        write_pack(self.root, 3, files={"README.md": "# My Pack\nDetails here."})
        content = seo_helper.read_pack_content(3, self.root)
        self.assertIn("README.md", content["sources"])

    def test_read_support(self):
        write_pack(self.root, 3, files={"support/HOW-TO-USE.md": "# Print\nUse A4."})
        content = seo_helper.read_pack_content(3, self.root)
        self.assertIn("support/HOW-TO-USE.md", content["sources"])

    def test_read_zip_text(self):
        write_pack(self.root, 3, zipped={"README.md": "# Zip Readme\nPages inside."})
        content = seo_helper.read_pack_content(3, self.root)
        self.assertIn("product.zip/README.md", content["sources"])

    def test_zip_binary_ignored(self):
        write_pack(self.root, 3, zipped={"image.png": b"\x89PNG"})
        content = seo_helper.read_pack_content(3, self.root)
        self.assertNotIn("product.zip/image.png", content["sources"])

    def test_zip_missing_ok(self):
        write_pack(self.root, 3)
        self.assertEqual(seo_helper.read_pack_content(3, self.root)["sources"], [])

    def test_bad_zip_ok(self):
        folder = write_pack(self.root, 3)
        (folder / "product.zip").write_bytes(b"not a zip")
        self.assertEqual(seo_helper.read_pack_content(3, self.root)["sources"], [])

    def test_title_from_listing(self):
        folder = write_pack(self.root, 3, files={"listing.txt": "Title: Focus Planner\nBody"})
        self.assertEqual(seo_helper.read_pack_content(3, self.root)["title"], "Focus Planner")

    def test_title_from_heading(self):
        folder = write_pack(self.root, 3, files={"README.md": "# Focus Planner\nBody"})
        self.assertEqual(seo_helper.read_pack_content(3, self.root)["title"], "Focus Planner")

    def test_title_from_folder(self):
        write_pack(self.root, 3, name="3-focus-planner")
        self.assertEqual(seo_helper.read_pack_content(3, self.root)["title"], "Focus Planner")

    def test_sections_from_line(self):
        write_pack(self.root, 3, files={"listing.txt": "Sections: daily, weekly, monthly"})
        self.assertEqual(seo_helper.read_pack_content(3, self.root)["sections"], ["daily", "weekly", "monthly"])

    def test_sections_from_headings(self):
        write_pack(self.root, 3, files={"README.md": "# Title\n## Daily page\n## Goals"})
        self.assertEqual(seo_helper.read_pack_content(3, self.root)["sections"], ["Daily page", "Goals"])

    def test_missing_pack_raises(self):
        with self.assertRaises(seo_helper.PackNotFoundError):
            seo_helper.read_pack_content(3, self.root)

    def test_products_env(self):
        pack = write_pack(self.root, 3)
        with mock.patch.dict(os.environ, {"CHILLI_PRODUCTS_DIR": str(self.root)}):
            self.assertEqual(seo_helper.find_pack_dir(3), pack)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.output = sample_output()

    def test_validation_clean(self):
        self.assertEqual(seo_helper.validate_output(self.output), [])

    def test_thirty_titles(self):
        total = sum(len(v["variants"]) for v in self.output["title_variants"].values())
        self.assertEqual(total, 30)

    def test_summary_pack(self):
        self.assertIn("Pack 3", self.output["summary"])

    def test_summary_no_money(self):
        self.assertIsNone(MONEY_RE.search(self.output["summary"]))

    def test_summary_no_brands(self):
        for brand in BANNED_LOW:
            self.assertNotIn(brand, self.output["summary"].lower())

    def test_document_has_sections(self):
        document = seo_helper.build_seo_document(self.output)
        for heading in ("Keywords", "Title Variants", "Shop Tags", "Meta description", "Pinterest", "Alt texts"):
            self.assertIn(heading, document)

    def test_document_no_money(self):
        self.assertIsNone(MONEY_RE.search(seo_helper.build_seo_document(self.output)))

    def test_document_tag_count(self):
        document = seo_helper.build_seo_document(self.output)
        self.assertEqual(len(re.findall(r"^\s*TAG\d+:", document, flags=re.M)), 13)

    def test_document_title_variant_count(self):
        document = seo_helper.build_seo_document(self.output)
        self.assertEqual(len(re.findall(r"^\s*(?:ETSY|GUMROAD|PAYHIP)\d+:", document, flags=re.M)), 30)

    def test_document_sources(self):
        document = seo_helper.build_seo_document(self.output)
        self.assertIn("listing.txt", document)

    def test_source_banned_warning(self):
        output = seo_helper.build_seo(sample_content("Title: Notion Planner\nUse Canva. Best seller $5"))
        self.assertTrue(any("blocked" in warning for warning in output["warnings"]))
        self.assertFalse(seo_helper._contains_banned(output["title"]))

    def test_output_has_pack_number(self):
        self.assertEqual(self.output["pack_number"], 3)

    def test_validate_detects_short_keywords(self):
        broken = dict(self.output)
        broken["keyword_set"] = {"head": ["a b"], "long_tail": ["c d"], "total": 2, "all": ["a b", "c d"]}
        self.assertTrue(seo_helper.validate_output(broken))

    def test_validate_detects_tag_length(self):
        broken = dict(self.output)
        broken["etsy_tags"] = {"tags": ["x" * 21] + ["t%d" % i for i in range(12)], "count": 13}
        self.assertTrue(any("tag" in item for item in seo_helper.validate_output(broken)))

    def test_validate_detects_meta(self):
        broken = dict(self.output)
        broken["meta_description"] = {"description": "short", "char_length": 5}
        self.assertTrue(any("meta" in item for item in seo_helper.validate_output(broken)))

    def test_validate_detects_title_count(self):
        broken = dict(self.output)
        broken["title_variants"] = {"etsy": {"max_chars": 140, "variants": ["one"]}}
        self.assertTrue(seo_helper.validate_output(broken))

    def test_validate_detects_duplicate_tags(self):
        broken = dict(self.output)
        broken["etsy_tags"] = {"tags": ["same"] * 13, "count": 13}
        self.assertTrue(seo_helper.validate_output(broken))

    def test_build_for_pack(self):
        with tempfile.TemporaryDirectory(prefix="oc2-seo-pack-") as folder:
            write_pack(folder, 4, files={"listing.txt": LISTING})
            output = seo_helper.build_for_pack(4, folder)
        self.assertEqual(output["pack_number"], 4)
        self.assertEqual(seo_helper.validate_output(output), [])


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="oc2-seo-tg-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ctx = {"db_path": str(self.root / "db.sqlite"), "chat_id": "1", "products_dir": str(self.root)}

    def test_command_is_seokit(self):
        self.assertEqual(tg_seo.COMMANDS[0]["cmd"], "/seokit")

    def test_command_is_not_seo(self):
        self.assertNotEqual(tg_seo.COMMANDS[0]["cmd"], "/seo")

    def test_words(self):
        self.assertIn("seo kit", tg_seo.COMMANDS[0]["words"])

    def test_help_texts(self):
        self.assertTrue(tg_seo.COMMANDS[0]["help_en"])
        self.assertTrue(tg_seo.COMMANDS[0]["help_te"])

    def test_parse_slash(self):
        self.assertEqual(tg_seo.parse_pack_number("/seokit 3"), 3)

    def test_parse_bot_suffix(self):
        self.assertEqual(tg_seo.parse_pack_number("/seokit@chilli_bot 3"), 3)

    def test_parse_words(self):
        self.assertEqual(tg_seo.parse_pack_number("seo kit 3"), 3)

    def test_parse_pack_word(self):
        self.assertEqual(tg_seo.parse_pack_number("seo pack 3"), 3)

    def test_parse_for_pack(self):
        self.assertEqual(tg_seo.parse_pack_number("seokit for pack 3"), 3)

    def test_parse_no_number(self):
        self.assertIsNone(tg_seo.parse_pack_number("/seokit"))

    def test_parse_zero(self):
        self.assertEqual(tg_seo.parse_pack_number("/seokit 0"), 0)

    def test_parse_seo_reports_command_none(self):
        self.assertIsNone(tg_seo.parse_pack_number("/seo 3"))

    def test_parse_other_none(self):
        self.assertIsNone(tg_seo.parse_pack_number("hello"))

    def test_filename(self):
        self.assertEqual(tg_seo.document_filename(3), "seo_pack_3.txt")

    def test_handle_missing_pack(self):
        result = tg_seo.handle("/seokit 3", self.ctx)
        self.assertIn("dorakaledu", result["text"])

    def test_handle_other_none(self):
        self.assertIsNone(tg_seo.handle("hello", self.ctx))

    def test_handle_no_number_asks(self):
        result = tg_seo.handle("/seokit", self.ctx)
        self.assertIn("Pack number", result["text"])

    def test_owner_mismatch_silent(self):
        with mock.patch("notify._telegram_configured", return_value=(True, "t", "999")):
            self.assertIsNone(tg_seo.handle("/seokit 3", self.ctx))

    def test_owner_match(self):
        write_pack(self.root, 3, files={"listing.txt": LISTING})
        with mock.patch("notify._telegram_configured", return_value=(True, "t", "1")):
            self.assertIsNotNone(tg_seo.handle("/seokit 3", self.ctx))

    def test_handle_sends_document(self):
        write_pack(self.root, 3, files={"listing.txt": LISTING})
        sent = {}
        def send(path, caption=""):
            sent["path"] = path
            sent["caption"] = caption
            sent["body"] = Path(path).read_text(encoding="utf-8")
        result = tg_seo.handle("/seokit 3", {**self.ctx, "send_document": send})
        self.assertEqual(result, {"handled": True})
        self.assertIn("SEO KIT PACK 3", sent["body"])
        self.assertIn("Pack 3", sent["caption"])

    def test_temp_file_deleted(self):
        write_pack(self.root, 3, files={"listing.txt": LISTING})
        sent = {}
        def send(path, caption=""):
            sent["path"] = path
        tg_seo.handle("/seokit 3", {**self.ctx, "send_document": send})
        self.assertFalse(Path(sent["path"]).exists())

    def test_no_send_document_falls_back_to_text(self):
        write_pack(self.root, 3, files={"listing.txt": LISTING})
        result = tg_seo.handle("/seokit 3", self.ctx)
        self.assertIn("SEO KIT PACK 3", result["text"])

    def test_plugin_does_not_import_telegram_inbox(self):
        source = Path(tg_seo.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import telegram_inbox", source)

    def test_call_plugins_reachable(self):
        import telegram_inbox
        write_pack(self.root, 3, files={"listing.txt": LISTING})
        sent = {}
        def fake_doc(token, chat_id, path, caption=""):
            sent["path"] = path
            sent["caption"] = caption
        with mock.patch.dict(os.environ, {"CHILLI_PRODUCTS_DIR": str(self.root)}), \
             mock.patch.object(telegram_inbox, "send_document", side_effect=fake_doc):
            handled, result = telegram_inbox.call_plugins(
                "/seokit 3", "token", "1", db_path=str(self.root / "db.sqlite"), plugins=[tg_seo]
            )
        self.assertTrue(handled)
        self.assertEqual(result, {"handled": True})
        self.assertIn("Pack 3", sent["caption"])
        self.assertFalse(Path(sent["path"]).exists())

    def test_call_plugins_not_handled(self):
        import telegram_inbox
        handled, result = telegram_inbox.call_plugins(
            "/seo 3", "token", "1", db_path=str(self.root / "db.sqlite"), plugins=[tg_seo]
        )
        self.assertFalse(handled)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
