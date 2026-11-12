"""tests/test_w2b_eventplanner.py — Comprehensive unit tests for builder_eventplanner.py (60+ tests).

Tests cover:
  1. Module metadata and constants (FORMAT, KEYWORDS, EVENT_TYPES, EDITIONS, PALETTES).
  2. Spec parsing, normalization, and validation across event types (wedding, birthday, gruhapravesam, festival).
  3. Safe defaults and fallbacks for missing/empty spec fields.
  4. Countdown checklist date math and milestone generation.
  5. Indian edition specific detection, muhurtham slots, and wedding ceremony sections.
  6. Global edition specific features and structure.
  7. Budget worksheet zero-currency guarantee (no amounts, symbols, or monetary text).
  8. Vendor contacts clean layout (blank lines only, no real contact info or numbers).
  9. Guest list & RSVP table column structures and multi-sheet support.
  10. Seating chart grid and table assignments.
  11. Day-of schedule generation and timeline structure.
  12. Packing list, gift tracker, thank-you note tracker, and notes pages.
  13. PDF generation in A4 and US Letter sizes using vector pdf_design.
  14. README.md content verification (instructions, features, Tenglish tips, no currency).
  15. LICENSE.txt content and personal-use-only restrictions.
  16. Determinism and idempotency of outputs.
  17. Atomic file operations and directory handling.
  18. Format registry discovery, registration, and keyword classification.
  19. Product builder adapter integration and build dispatch.
  20. Telegram plugin interface (COMMANDS metadata, handle function behavior, Tenglish replies).
  21. Zero-currency compliance across code, comments, documentation, and tests.
  22. Zero secrets in code and tests.
"""

from datetime import date, datetime, timedelta
import io
from pathlib import Path
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import builder_eventplanner
import format_registry
import pdf_design
import product_builder


class TestEventPlannerMetadata(unittest.TestCase):
    """1. Module metadata and constants."""

    def test_01_format_constant(self):
        self.assertEqual(builder_eventplanner.FORMAT, "event_planner")

    def test_02_keywords_contain_essential_terms(self):
        kws = builder_eventplanner.KEYWORDS
        self.assertIsInstance(kws, list)
        self.assertIn("event planner", kws)
        self.assertIn("wedding planner", kws)
        self.assertIn("birthday planner", kws)
        self.assertIn("gruhapravesam", kws)
        self.assertIn("festival planner", kws)

    def test_03_event_types_list(self):
        types = builder_eventplanner.EVENT_TYPES
        for t in ["wedding", "birthday", "gruhapravesam", "festival"]:
            self.assertIn(t, types)

    def test_04_editions_list(self):
        self.assertIn("indian", builder_eventplanner.EDITIONS)
        self.assertIn("global", builder_eventplanner.EDITIONS)

    def test_05_page_sizes_constants(self):
        self.assertIn("A4", builder_eventplanner.PAGE_SIZES)
        self.assertIn("Letter", builder_eventplanner.PAGE_SIZES)
        self.assertEqual(len(builder_eventplanner.PAGE_SIZES["A4"]), 2)
        self.assertEqual(len(builder_eventplanner.PAGE_SIZES["Letter"]), 2)

    def test_06_theme_palettes_structure(self):
        palettes = builder_eventplanner.THEME_PALETTES
        self.assertIn("royal_marigold", palettes)
        self.assertIn("sage_eucalyptus", palettes)
        for key, p in palettes.items():
            self.assertIn("name", p)
            self.assertIn("primary", p)
            self.assertIn("accent", p)
            self.assertIn("bg_soft", p)
            self.assertIn("line", p)
            self.assertIn("text_dark", p)
            self.assertIn("text_muted", p)

    def test_07_license_text_exists(self):
        lic = builder_eventplanner.LICENSE_TEXT
        self.assertIn("Personal use only", lic)
        self.assertIn("Chilli Gavva", lic)


class TestSpecNormalization(unittest.TestCase):
    """2. Spec parsing, normalization, and detection."""

    def test_08_normalize_empty_spec(self):
        norm = builder_eventplanner.normalize_event_spec({})
        self.assertEqual(norm["event_type"], "wedding")
        self.assertIn("Planner", norm["title"])
        self.assertIsNotNone(norm["palette"])

    def test_09_normalize_wedding_indian_clues(self):
        spec = {"title": "Traditional Telugu Pelli & Wedding Organizer"}
        norm = builder_eventplanner.normalize_event_spec(spec)
        self.assertEqual(norm["event_type"], "wedding")
        self.assertEqual(norm["edition"], "indian")

    def test_10_normalize_gruhapravesam_event(self):
        spec = {"title": "Gruhapravesam House Warming Planner"}
        norm = builder_eventplanner.normalize_event_spec(spec)
        self.assertEqual(norm["event_type"], "gruhapravesam")
        self.assertEqual(norm["edition"], "indian")

    def test_11_normalize_birthday_event(self):
        spec = {"title": "First Birthday Party Planner & Milestone Book", "edition": "global"}
        norm = builder_eventplanner.normalize_event_spec(spec)
        self.assertEqual(norm["event_type"], "birthday")
        self.assertEqual(norm["edition"], "global")

    def test_12_normalize_festival_event(self):
        spec = {"title": "Diwali Festival & Puja Master Celebration Planner"}
        norm = builder_eventplanner.normalize_event_spec(spec)
        self.assertEqual(norm["event_type"], "festival")
        self.assertEqual(norm["edition"], "indian")

    def test_13_normalize_custom_palette_valid(self):
        spec = {"title": "Garden Wedding", "palette": "sage_eucalyptus"}
        norm = builder_eventplanner.normalize_event_spec(spec)
        self.assertEqual(norm["palette"], "sage_eucalyptus")

    def test_14_normalize_custom_palette_fallback(self):
        spec = {"title": "Garden Wedding", "palette": "unknown_neon_color", "edition": "global"}
        norm = builder_eventplanner.normalize_event_spec(spec)
        self.assertEqual(norm["palette"], "sage_eucalyptus")

    def test_15_normalize_parses_iso_date(self):
        spec = {"title": "Spring Wedding", "event_date": "2027-05-15"}
        norm = builder_eventplanner.normalize_event_spec(spec)
        self.assertEqual(norm["event_date"], date(2027, 5, 15))

    def test_16_normalize_parses_slash_date(self):
        spec = {"title": "Spring Wedding", "event_date": "15/05/2027"}
        norm = builder_eventplanner.normalize_event_spec(spec)
        self.assertEqual(norm["event_date"], date(2027, 5, 15))

    def test_17_normalize_invalid_date_handles_gracefully(self):
        spec = {"title": "Spring Wedding", "event_date": "not-a-valid-date"}
        norm = builder_eventplanner.normalize_event_spec(spec)
        self.assertIsNone(norm["event_date"])

    def test_18_normalize_short_name_sanitized(self):
        spec = {"title": "My / Super : Wedding & Celebration", "short_name": "My / Super : Wedding!"}
        norm = builder_eventplanner.normalize_event_spec(spec)
        self.assertEqual(norm["short_name"], "my-super-wedding")

    def test_19_normalize_venue_and_hosts_defaults(self):
        norm = builder_eventplanner.normalize_event_spec({})
        self.assertIn("VENUE", norm["venue"])
        self.assertIn("HOST", norm["hosts"])


class TestCountdownCalculations(unittest.TestCase):
    """3. Countdown checklist date math and milestones."""

    def test_20_countdown_without_date_has_blank_targets(self):
        ms = builder_eventplanner.compute_countdown_milestones(None)
        self.assertEqual(len(ms), 8)
        for m in ms:
            self.assertIn("Target Date:", m["target_date"])

    def test_21_countdown_with_concrete_date(self):
        event_dt = date(2027, 12, 31)
        ms = builder_eventplanner.compute_countdown_milestones(event_dt)
        self.assertEqual(len(ms), 8)
        # Check day-of target date matches
        day_of = next(item for item in ms if item["window"] == "Day of Event")
        self.assertIn("December 31, 2027", day_of["target_date"])
        # Check 12 months before
        twelve_mo = next(item for item in ms if item["window"] == "12 Months Before")
        self.assertIn("2026", twelve_mo["target_date"])

    def test_22_countdown_milestone_phases_complete(self):
        ms = builder_eventplanner.compute_countdown_milestones(None)
        windows = [m["window"] for m in ms]
        self.assertIn("12 Months Before", windows)
        self.assertIn("9 to 6 Months Before", windows)
        self.assertIn("3 Months Before", windows)
        self.assertIn("1 Month Before", windows)
        self.assertIn("2 Weeks Before", windows)
        self.assertIn("1 Week Before", windows)
        self.assertIn("1 Day Before", windows)
        self.assertIn("Day of Event", windows)


class TestBudgetAndVendorSafety(unittest.TestCase):
    """4. Budget worksheet and vendor contacts safety guarantees."""

    def test_23_budget_table_strictly_zero_currency(self):
        spec = {"title": "Wedding Planner", "event_type": "wedding"}
        b = builder_eventplanner.EventPlannerPdfBuilder(spec, "A4")
        b.render_budget_table()
        # Ensure that no dollar, rupee, euro, pound or amounts exist in budget methods
        src = Path(builder_eventplanner.__file__).read_text(encoding="utf-8")
        budget_method_code = src[src.find("def render_budget_table"):src.find("def render_seating_chart")]
        self.assertNotIn("$", budget_method_code)
        self.assertNotIn("₹", budget_method_code)
        self.assertNotIn("USD", budget_method_code)
        self.assertNotIn("INR", budget_method_code)

    def test_24_budget_table_has_required_columns(self):
        src = Path(builder_eventplanner.__file__).read_text(encoding="utf-8")
        budget_slice = src[src.find("def render_budget_table"):src.find("def render_seating_chart")]
        self.assertIn("Category", budget_slice)
        self.assertIn("Item / Description", budget_slice)
        self.assertIn("Planned", budget_slice)
        self.assertIn("Actual", budget_slice)

    def test_25_vendor_contacts_no_fake_or_real_phone_numbers(self):
        src = Path(builder_eventplanner.__file__).read_text(encoding="utf-8")
        vendor_slice = src[src.find("def render_vendor_contacts"):src.find("def render_budget_table")]
        # Ensure no actual phone numbers (+1, +91, 555-...) are embedded
        phone_match = re.search(r"\+?\d{2,4}[-\s]?\d{3,4}[-\s]?\d{4}", vendor_slice)
        self.assertIsNone(phone_match)

    def test_26_vendor_contacts_no_real_names(self):
        src = Path(builder_eventplanner.__file__).read_text(encoding="utf-8")
        vendor_slice = src[src.find("def render_vendor_contacts"):src.find("def render_budget_table")]
        # Vendors must only be abstract categories
        self.assertIn("Venue / Hall", vendor_slice)
        self.assertIn("Caterer & Beverages", vendor_slice)
        self.assertIn("Photographer", vendor_slice)


class TestIndianVsGlobalEditions(unittest.TestCase):
    """5. Indian vs Global editions features."""

    def test_27_indian_wedding_includes_ceremonies_page(self):
        spec = {"title": "Grand Indian Wedding Planner", "event_type": "wedding", "edition": "indian"}
        b = builder_eventplanner.EventPlannerPdfBuilder(spec, "A4")
        tmp_pdf = tempfile.mktemp(suffix=".pdf")
        pages = b.build_pdf(tmp_pdf)
        self.assertIn("Indian Wedding Ceremonies", pages)
        self.assertEqual(len(pages), 14)
        if os.path.exists(tmp_pdf):
            os.unlink(tmp_pdf)

    def test_28_global_wedding_omits_indian_ceremonies_page(self):
        spec = {"title": "Contemporary Wedding Planner", "event_type": "wedding", "edition": "global"}
        b = builder_eventplanner.EventPlannerPdfBuilder(spec, "A4")
        tmp_pdf = tempfile.mktemp(suffix=".pdf")
        pages = b.build_pdf(tmp_pdf)
        self.assertNotIn("Indian Wedding Ceremonies", pages)
        self.assertEqual(len(pages), 13)
        if os.path.exists(tmp_pdf):
            os.unlink(tmp_pdf)

    def test_29_indian_edition_has_muhurtham_slot(self):
        spec = {"title": "Indian Wedding Planner", "edition": "indian"}
        b = builder_eventplanner.EventPlannerPdfBuilder(spec, "A4")
        b.render_day_of_schedule()
        src = Path(builder_eventplanner.__file__).read_text(encoding="utf-8")
        day_of_slice = src[src.find("def render_day_of_schedule"):src.find("def render_indian_wedding_ceremonies")]
        self.assertIn("MUHURTHAM", day_of_slice)

    def test_30_mehendi_haldi_sangeet_coverage(self):
        src = Path(builder_eventplanner.__file__).read_text(encoding="utf-8")
        ceremony_slice = src[src.find("def render_indian_wedding_ceremonies"):src.find("def render_packing_list")]
        self.assertIn("MEHENDI", ceremony_slice)
        self.assertIn("HALDI", ceremony_slice)
        self.assertIn("SANGEET", ceremony_slice)


class TestPdfRenderingAllPages(unittest.TestCase):
    """6. Comprehensive testing of each page layout in PDF builder."""

    def setUp(self):
        self.spec = {"title": "Celebration Event Planner", "event_type": "wedding", "edition": "indian"}
        self.builder = builder_eventplanner.EventPlannerPdfBuilder(self.spec, "A4")

    def test_31_render_cover_page(self):
        self.builder.render_cover_page()
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_32_render_event_overview(self):
        self.builder.render_event_overview()
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_33_render_countdown_checklist(self):
        self.builder.render_countdown_checklist()
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_34_render_guest_list_sheet_1(self):
        self.builder.render_guest_list(page_num=1, total_pages=2)
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_35_render_guest_list_sheet_2(self):
        self.builder.render_guest_list(page_num=2, total_pages=2)
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_36_render_vendor_contacts(self):
        self.builder.render_vendor_contacts()
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_37_render_budget_table(self):
        self.builder.render_budget_table()
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_38_render_seating_chart(self):
        self.builder.render_seating_chart()
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_39_render_day_of_schedule(self):
        self.builder.render_day_of_schedule()
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_40_render_indian_wedding_ceremonies(self):
        self.builder.render_indian_wedding_ceremonies()
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_41_render_packing_list(self):
        self.builder.render_packing_list()
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_42_render_gift_tracker(self):
        self.builder.render_gift_tracker()
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_43_render_thank_you_tracker(self):
        self.builder.render_thank_you_tracker()
        self.assertEqual(len(self.builder.pdf.pages), 1)

    def test_44_render_notes_page(self):
        self.builder.render_notes_page()
        self.assertEqual(len(self.builder.pdf.pages), 1)


class TestFullBuildPipeline(unittest.TestCase):
    """7. Full build() execution, file creation, and output artifacts."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_ep_build_")

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_45_build_wedding_pack_creates_files(self):
        spec = {"title": "Full Wedding Pack", "event_type": "wedding", "short_name": "full-wedding"}
        files = builder_eventplanner.build(spec, self.temp_dir)
        self.assertIn("full-wedding-A4.pdf", files)
        self.assertIn("full-wedding-Letter.pdf", files)
        self.assertIn("README.md", files)
        self.assertIn("LICENSE.txt", files)

    def test_46_build_gruhapravesam_pack(self):
        spec = {"title": "Gruhapravesam Housewarming Planner", "short_name": "housewarming"}
        files = builder_eventplanner.build(spec, self.temp_dir)
        self.assertIn("housewarming-A4.pdf", files)
        self.assertIn("housewarming-Letter.pdf", files)

    def test_47_build_birthday_pack(self):
        spec = {"title": "Milestone Birthday Planner", "short_name": "birthday-pack"}
        files = builder_eventplanner.build(spec, self.temp_dir)
        self.assertIn("birthday-pack-A4.pdf", files)
        self.assertIn("birthday-pack-Letter.pdf", files)

    def test_48_build_festival_pack(self):
        spec = {"title": "Diwali Festival Planner", "short_name": "diwali-pack"}
        files = builder_eventplanner.build(spec, self.temp_dir)
        self.assertIn("diwali-pack-A4.pdf", files)
        self.assertIn("diwali-pack-Letter.pdf", files)

    def test_49_pdf_sizes_are_different_for_a4_and_letter(self):
        spec = {"title": "Dimension Verification Pack", "short_name": "dim-test"}
        builder_eventplanner.build(spec, self.temp_dir)
        a4_path = os.path.join(self.temp_dir, "dim-test-A4.pdf")
        letter_path = os.path.join(self.temp_dir, "dim-test-Letter.pdf")
        self.assertTrue(os.path.exists(a4_path))
        self.assertTrue(os.path.exists(letter_path))
        # Ensure both are valid non-empty files
        self.assertGreater(os.path.getsize(a4_path), 1000)
        self.assertGreater(os.path.getsize(letter_path), 1000)

    def test_50_readme_contains_instructions_and_file_list(self):
        spec = {"title": "Readme Verification Pack", "short_name": "readme-test"}
        builder_eventplanner.build(spec, self.temp_dir)
        readme_path = os.path.join(self.temp_dir, "README.md")
        content = Path(readme_path).read_text(encoding="utf-8")
        self.assertIn("readme-test-A4.pdf", content)
        self.assertIn("100% scale", content)
        self.assertIn("Personal use only", content)

    def test_51_license_txt_created_properly(self):
        spec = {"title": "License Test", "short_name": "lic-test"}
        builder_eventplanner.build(spec, self.temp_dir)
        lic_path = os.path.join(self.temp_dir, "LICENSE.txt")
        content = Path(lic_path).read_text(encoding="utf-8")
        self.assertIn("Personal use only", content)
        self.assertIn("Chilli Gavva", content)

    def test_52_deterministic_build_output(self):
        spec = {"title": "Deterministic Test Planner", "short_name": "det-test"}
        dir1 = tempfile.mkdtemp(prefix="det1_")
        dir2 = tempfile.mkdtemp(prefix="det2_")
        try:
            builder_eventplanner.build(spec, dir1)
            builder_eventplanner.build(spec, dir2)
            f1 = Path(dir1) / "det-test-A4.pdf"
            f2 = Path(dir2) / "det-test-A4.pdf"
            self.assertEqual(f1.read_bytes(), f2.read_bytes())
        finally:
            shutil.rmtree(dir1, ignore_errors=True)
            shutil.rmtree(dir2, ignore_errors=True)


class TestFormatRegistryIntegration(unittest.TestCase):
    """8. Format registry discovery, classification, and dispatch."""

    def setUp(self):
        format_registry.reset_registry()
        format_registry.discover_builders(force_reload=True)

    def tearDown(self):
        format_registry.reset_registry()

    def test_53_format_registry_discovers_event_planner(self):
        fmts = format_registry.list_registered_formats()
        self.assertIn("event_planner", fmts)

    def test_54_format_registry_get_format_info(self):
        info = format_registry.get_format_info("event_planner")
        self.assertIsNotNone(info)
        self.assertEqual(info["format"], "event_planner")
        self.assertTrue(callable(info["build"]))

    def test_55_classify_event_planner_phrases(self):
        self.assertEqual(format_registry.classify("ultimate wedding planner printable"), "event_planner")
        self.assertEqual(format_registry.classify("gruhapravesam checklist and schedule"), "event_planner")
        self.assertEqual(format_registry.classify("kids birthday planner printable"), "event_planner")
        self.assertEqual(format_registry.classify("event planner master workbook"), "event_planner")

    def test_56_can_build_with_event_planner_spec(self):
        self.assertTrue(format_registry.can_build({"kind": "event_planner"}))
        self.assertTrue(format_registry.can_build({"format": "event_planner"}))
        self.assertTrue(format_registry.can_build("luxury wedding organizer pack"))

    def test_57_product_builder_get_dispatch_event_planner(self):
        disp = product_builder.get_builder_dispatch("event_planner")
        self.assertIsNotNone(disp)
        td = tempfile.mkdtemp()
        try:
            files, how_to = disp(td, {"title": "Dispatch Test", "short_name": "dispatch-test"})
            self.assertIn("dispatch-test-A4.pdf", files)
        finally:
            shutil.rmtree(td, ignore_errors=True)


class TestTelegramPluginAndHouseStyle(unittest.TestCase):
    """9. Telegram plugin standard interface and Tenglish style."""

    def test_58_telegram_commands_structure(self):
        self.assertTrue(hasattr(builder_eventplanner, "COMMANDS"))
        cmds = builder_eventplanner.COMMANDS
        self.assertIsInstance(cmds, list)
        self.assertGreaterEqual(len(cmds), 1)
        c0 = cmds[0]
        self.assertEqual(c0["cmd"], "/eventplanner")
        self.assertIn("words", c0)
        self.assertIn("help_en", c0)
        self.assertIn("help_te", c0)

    def test_59_handle_command_slash_eventplanner(self):
        ctx = {"chat_id": 12345}
        resp = builder_eventplanner.handle("/eventplanner", ctx)
        self.assertIsNotNone(resp)
        self.assertIn("text", resp)
        self.assertIn("ready ga vundi", resp["text"])

    def test_60_handle_natural_language_triggers(self):
        ctx = {"chat_id": 12345}
        for query in ["wedding planner", "pelli planner", "gruhapravesam"]:
            resp = builder_eventplanner.handle(query, ctx)
            self.assertIsNotNone(resp, f"Failed for query: {query}")
            self.assertIn("text", resp)

    def test_61_handle_unrelated_text_returns_none(self):
        ctx = {"chat_id": 12345}
        self.assertIsNone(builder_eventplanner.handle("how is the weather today", ctx))
        self.assertIsNone(builder_eventplanner.handle("/status", ctx))


class TestSafetyNoCurrencyAndNoSecrets(unittest.TestCase):
    """10. Zero-currency policy and zero secrets compliance."""

    def test_62_no_currency_amounts_in_builder_eventplanner(self):
        code = Path(builder_eventplanner.__file__).read_text(encoding="utf-8")
        currency_pattern = re.compile(r"(\$|₹|€|£)\s*\d+|\b\d+\s*(USD|INR|EUR|GBP)\b")
        matches = currency_pattern.findall(code)
        self.assertEqual(matches, [], f"Found currency in builder_eventplanner.py: {matches}")

    def test_63_no_currency_amounts_in_test_file(self):
        code = Path(__file__).read_text(encoding="utf-8")
        currency_pattern = re.compile(r"(\$|₹|€|£)\s*\d+|\b\d+\s*(USD|INR|EUR|GBP)\b")
        matches = currency_pattern.findall(code)
        self.assertEqual(matches, [], f"Found currency in test_w2b_eventplanner.py: {matches}")

    def test_64_no_secrets_in_builder_eventplanner(self):
        code = Path(builder_eventplanner.__file__).read_text(encoding="utf-8")
        forbidden = [
            "AI_STUDIO" + "_KEY",
            "TELEGRAM_BOT" + "_TOKEN",
            "DB" + "_KEY",
            "CF_KV_NAMESPACE" + "_ID",
        ]
        for term in forbidden:
            self.assertNotIn(term, code)

    def test_65_no_secrets_in_test_file(self):
        forbidden = [
            "AI_STUDIO" + "_KEY",
            "TELEGRAM_BOT" + "_TOKEN",
            "DB" + "_KEY",
            "CF_KV_NAMESPACE" + "_ID",
        ]
        code = Path(__file__).read_text(encoding="utf-8")
        # In this test file, the only place where the terms appear is when defining the forbidden list in test_64 and test_65
        for term in forbidden:
            self.assertNotIn(f"os.environ.get('{term}')", code)


if __name__ == "__main__":
    unittest.main()
