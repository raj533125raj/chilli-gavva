"""tests/test_w1c_themes.py — Unit tests for the Premium PDF Planner Design System.

60+ tests verifying:
- 6 Premium themes with complete token sets
- WCAG AA contrast ratio compliance (>= 4.5:1)
- Type scale & spacing grid consistency
- Niche-to-theme mapping & normalization
- Component rendering: header bands, tabs, dots, lines, grids, habit circles, checkboxes, badges
- Special pages rendering: covers, dividers, how-to-use, legends
- Deterministic byte generation for identical inputs
- Product builder integration with planner & dated_planner
- Page count guarantees & truth-check integrity
- Zero currency/money mentions in code, strings, and generated outputs
"""

import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pdf_design as pd
import pdf_themes
import product_builder as pb


class ThemeTokensAndContrastTests(unittest.TestCase):
    """Test theme tokens, contrast requirements, and color calculations."""

    def test_01_all_six_themes_present(self):
        themes = pdf_themes.list_theme_names()
        self.assertEqual(len(themes), 6)
        expected = [
            "Calm Sage",
            "Midnight Focus",
            "Terracotta",
            "Ocean",
            "Minimal Mono",
            "Festive Marigold",
        ]
        self.assertEqual(sorted(themes), sorted(expected))

    def test_02_theme_slugs_unique(self):
        slugs = [th["slug"] for th in pdf_themes.PREMIUM_THEMES.values()]
        self.assertEqual(len(slugs), 6)
        self.assertEqual(len(set(slugs)), 6)

    def test_03_theme_token_keys_complete(self):
        required_tokens = {
            "bg", "page", "surface", "surface_soft", "accent", "accent_light",
            "accent_soft", "ink", "muted", "faded", "line", "border", "cell",
            "highlight", "bar", "tab_bg", "tab_text", "tab_inactive_bg",
            "tab_inactive_text", "badge_bg", "badge_text", "weekend",
            "on_accent", "on_accent_soft",
        }
        for name, th in pdf_themes.PREMIUM_THEMES.items():
            tok = th["tokens"]
            missing = required_tokens - set(tok.keys())
            self.assertEqual(missing, set(), f"Theme {name} is missing tokens: {missing}")

    def test_04_hex_to_rgb_conversion(self):
        rgb_black = pdf_themes.hex_to_rgb("#000000")
        self.assertEqual(rgb_black, (0.0, 0.0, 0.0))
        rgb_white = pdf_themes.hex_to_rgb("#ffffff")
        self.assertEqual(rgb_white, (1.0, 1.0, 1.0))
        rgb_short = pdf_themes.hex_to_rgb("#fff")
        self.assertEqual(rgb_short, (1.0, 1.0, 1.0))

    def test_05_contrast_ratio_symmetry(self):
        cr1 = pdf_themes.contrast_ratio("#ffffff", "#000000")
        cr2 = pdf_themes.contrast_ratio("#000000", "#ffffff")
        self.assertAlmostEqual(cr1, cr2, places=3)
        self.assertGreater(cr1, 20.0)

    def test_06_contrast_ink_on_bg_meets_wcag_aa(self):
        for name, th in pdf_themes.PREMIUM_THEMES.items():
            tok = th["tokens"]
            cr = pdf_themes.contrast_ratio(tok["ink"], tok["bg"])
            self.assertGreaterEqual(
                cr, 7.0, f"Theme {name} ink on bg failed AAA: {cr:.2f}"
            )

    def test_07_contrast_accent_on_bg_meets_wcag_aa(self):
        for name, th in pdf_themes.PREMIUM_THEMES.items():
            tok = th["tokens"]
            cr = pdf_themes.contrast_ratio(tok["accent"], tok["bg"])
            self.assertGreaterEqual(
                cr, 4.5, f"Theme {name} accent on bg failed AA: {cr:.2f}"
            )

    def test_08_contrast_tab_text_on_tab_bg(self):
        for name, th in pdf_themes.PREMIUM_THEMES.items():
            tok = th["tokens"]
            cr = pdf_themes.contrast_ratio(tok["tab_text"], tok["tab_bg"])
            self.assertGreaterEqual(
                cr, 4.5, f"Theme {name} tab text on active tab bg failed AA: {cr:.2f}"
            )

    def test_09_contrast_badge_text_on_badge_bg(self):
        for name, th in pdf_themes.PREMIUM_THEMES.items():
            tok = th["tokens"]
            cr = pdf_themes.contrast_ratio(tok["badge_text"], tok["badge_bg"])
            self.assertGreaterEqual(
                cr, 4.5, f"Theme {name} badge text on badge bg failed AA: {cr:.2f}"
            )

    def test_10_contrast_on_accent_text(self):
        for name, th in pdf_themes.PREMIUM_THEMES.items():
            tok = th["tokens"]
            cr = pdf_themes.contrast_ratio(tok["on_accent"], tok["accent"])
            self.assertGreaterEqual(
                cr, 4.5, f"Theme {name} on_accent on accent failed AA: {cr:.2f}"
            )


class ThemeMappingAndResolutionTests(unittest.TestCase):
    """Test resolution of theme names, aliases, and niche defaults."""

    def test_11_normalize_exact_names(self):
        for name in pdf_themes.list_theme_names():
            self.assertEqual(pdf_themes.normalize_theme_name(name), name)

    def test_12_normalize_lowercase_and_hyphenated(self):
        self.assertEqual(pdf_themes.normalize_theme_name("calm-sage"), "Calm Sage")
        self.assertEqual(pdf_themes.normalize_theme_name("calm_sage"), "Calm Sage")
        self.assertEqual(pdf_themes.normalize_theme_name("midnight-focus"), "Midnight Focus")
        self.assertEqual(pdf_themes.normalize_theme_name("midnight_focus"), "Midnight Focus")

    def test_13_normalize_short_aliases(self):
        self.assertEqual(pdf_themes.normalize_theme_name("sage"), "Calm Sage")
        self.assertEqual(pdf_themes.normalize_theme_name("midnight"), "Midnight Focus")
        self.assertEqual(pdf_themes.normalize_theme_name("terra"), "Terracotta")
        self.assertEqual(pdf_themes.normalize_theme_name("ocean"), "Ocean")
        self.assertEqual(pdf_themes.normalize_theme_name("mono"), "Minimal Mono")
        self.assertEqual(pdf_themes.normalize_theme_name("marigold"), "Festive Marigold")

    def test_14_normalize_invalid_returns_none(self):
        self.assertIsNone(pdf_themes.normalize_theme_name(""))
        self.assertIsNone(pdf_themes.normalize_theme_name(None))
        self.assertIsNone(pdf_themes.normalize_theme_name("Unknown Rainbow Theme"))

    def test_15_get_theme_by_canonical_name(self):
        th = pdf_themes.get_theme("Calm Sage")
        self.assertEqual(th["slug"], "calm_sage")

    def test_16_get_theme_by_slug(self):
        th = pdf_themes.get_theme("midnight_focus")
        self.assertEqual(th["name"], "Midnight Focus")

    def test_17_get_theme_unknown_raises_key_error(self):
        with self.assertRaises(KeyError):
            pdf_themes.get_theme("nonexistent_theme")

    def test_18_get_theme_for_spec_with_explicit_theme(self):
        spec = {"theme": "Festive Marigold"}
        th = pdf_themes.get_theme_for_spec(spec)
        self.assertEqual(th["name"], "Festive Marigold")

    def test_19_get_theme_for_spec_with_adhd_niche(self):
        spec = {"niche": "ADHD Planners Agent"}
        th = pdf_themes.get_theme_for_spec(spec)
        self.assertEqual(th["name"], "Calm Sage")

    def test_20_get_theme_for_spec_with_2027_planner_niche(self):
        spec = {"niche": "2027 Planners Agent"}
        th = pdf_themes.get_theme_for_spec(spec)
        self.assertEqual(th["name"], "Ocean")

    def test_21_get_theme_for_spec_with_budget_niche(self):
        spec = {"niche": "Budget Sheets Agent"}
        th = pdf_themes.get_theme_for_spec(spec)
        self.assertEqual(th["name"], "Midnight Focus")

    def test_22_get_theme_for_spec_with_freelancer_niche(self):
        spec = {"niche": "Freelancer Trackers Agent"}
        th = pdf_themes.get_theme_for_spec(spec)
        self.assertEqual(th["name"], "Terracotta")

    def test_23_get_theme_for_spec_with_notion_niche(self):
        spec = {"niche": "Notion Templates Agent"}
        th = pdf_themes.get_theme_for_spec(spec)
        self.assertEqual(th["name"], "Minimal Mono")

    def test_24_get_theme_for_spec_fallback_default(self):
        spec = {"niche": "Random Custom Niche"}
        th = pdf_themes.get_theme_for_spec(spec)
        self.assertEqual(th["name"], "Calm Sage")

    def test_25_get_theme_for_spec_title_heuristic_focus(self):
        spec = {"name": "Deep Work Focus Journal"}
        th = pdf_themes.get_theme_for_spec(spec)
        self.assertEqual(th["name"], "Midnight Focus")

    def test_26_get_theme_for_spec_title_heuristic_festive(self):
        spec = {"name": "Diwali Festive Planner"}
        th = pdf_themes.get_theme_for_spec(spec)
        self.assertEqual(th["name"], "Festive Marigold")


class TypeScaleAndSpacingGridTests(unittest.TestCase):
    """Test consistency of type scale and spacing grid."""

    def test_27_type_scale_hierarchy(self):
        ts = pdf_themes.TYPE_SCALE
        self.assertGreater(ts["display"], ts["title_lg"])
        self.assertGreater(ts["title_lg"], ts["title_md"])
        self.assertGreater(ts["title_md"], ts["title_sm"])
        self.assertGreater(ts["title_sm"], ts["body_lg"])
        self.assertGreater(ts["body_lg"], ts["body"])
        self.assertGreater(ts["body"], ts["body_sm"])
        self.assertGreater(ts["body_sm"], ts["caption"])
        self.assertGreater(ts["caption"], ts["micro"])

    def test_28_spacing_grid_constants(self):
        sg = pdf_themes.SPACING_GRID
        self.assertEqual(sg["margin_page"], 42.0)
        self.assertEqual(sg["bottom_safe"], 60.0)
        self.assertEqual(sg["footer_y"], 30.0)
        self.assertGreater(sg["margin_page"], 0)


class ComponentRenderingTests(unittest.TestCase):
    """Test individual component rendering functions."""

    def setUp(self):
        self.pdf = pd.Pdf("A4", "Test Footer")
        self.pdf.new_page()

    def test_29_draw_header_band(self):
        start_y = pdf_themes.draw_header_band(
            self.pdf, "Daily Overview", "Anchors and tasks", "DAILY"
        )
        self.assertIsInstance(start_y, float)
        self.assertLess(start_y, self.pdf.h)
        self.assertGreater(start_y, pd.BOTTOM)

    def test_30_draw_page_number(self):
        ops_before = len(self.pdf.pages[-1])
        pdf_themes.draw_page_number(self.pdf, 3, 12)
        ops_after = len(self.pdf.pages[-1])
        self.assertGreater(ops_after, ops_before)

    def test_31_draw_section_tab_active_and_inactive(self):
        pdf_themes.draw_section_tab(self.pdf, 0, "JAN", 5, active=True)
        pdf_themes.draw_section_tab(self.pdf, 1, "FEB", 5, active=False)
        stream = " ".join(self.pdf.pages[-1])
        self.assertIn("JAN", stream)
        self.assertIn("FEB", stream)

    def test_32_draw_dotted_area(self):
        ops_before = len(self.pdf.pages[-1])
        pdf_themes.draw_dotted_area(self.pdf, 50, 400, 100, 100, spacing=20)
        ops_after = len(self.pdf.pages[-1])
        self.assertGreater(ops_after, ops_before + 10)

    def test_33_draw_lined_area(self):
        num_lines = pdf_themes.draw_lined_area(self.pdf, 50, 400, 200, 100, line_spacing=20)
        self.assertEqual(num_lines, 5)

    def test_34_draw_grid_area(self):
        cols, rows = pdf_themes.draw_grid_area(self.pdf, 50, 200, 180, 90, cell_size=18)
        self.assertEqual(cols, 10)
        self.assertEqual(rows, 5)

    def test_35_draw_habit_circles_with_labels(self):
        pdf_themes.draw_habit_circles(self.pdf, 50, 100, count=7, labels=["M", "T", "W", "T", "F", "S", "S"])
        stream = " ".join(self.pdf.pages[-1])
        self.assertIn("M", stream)
        self.assertIn("W", stream)

    def test_36_draw_checkbox_rounded_and_square(self):
        ops_before = len(self.pdf.pages[-1])
        pdf_themes.draw_checkbox(self.pdf, 50, 50, size=10, style="rounded", checked=True)
        pdf_themes.draw_checkbox(self.pdf, 70, 50, size=10, style="square", checked=False)
        self.assertGreater(len(self.pdf.pages[-1]), ops_before)

    def test_37_draw_badge(self):
        bw = pdf_themes.draw_badge(self.pdf, 50, 30, "FEATURED")
        self.assertGreater(bw, 20.0)

    def test_38_draw_legend(self):
        h = pdf_themes.draw_legend(self.pdf, 50, 80, 300)
        self.assertEqual(h, 32.0)


class SpecialPagesRenderingTests(unittest.TestCase):
    """Test rendering of cover, divider, how-to-use, and legend across all 6 themes individually."""

    def _cover_spec(self, theme_name):
        return {
            "name": f"Mindful {theme_name} Planner",
            "listing": {"title": f"Mindful {theme_name} Planner", "buyer": "Daily Planners", "tags": ["planner"], "platform": "Gumroad"},
            "instructions": ["Print it."],
        }

    def test_39_render_cover_calm_sage(self):
        th = pdf_themes.get_theme("Calm Sage")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_cover_page(pdf, self._cover_spec("Calm Sage"), theme=th)
        self.assertEqual(len(pdf.pages), 1)
        stream = "\n".join(pdf.pages[0])
        self.assertIn("THIS PLANNER BELONGS TO:", stream)
        self.assertIn("Theme: Calm Sage", stream)

    def test_40_render_cover_midnight_focus(self):
        th = pdf_themes.get_theme("Midnight Focus")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_cover_page(pdf, self._cover_spec("Midnight Focus"), theme=th)
        self.assertEqual(len(pdf.pages), 1)
        stream = "\n".join(pdf.pages[0])
        self.assertIn("THIS PLANNER BELONGS TO:", stream)
        self.assertIn("Theme: Midnight Focus", stream)

    def test_41_render_cover_terracotta(self):
        th = pdf_themes.get_theme("Terracotta")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_cover_page(pdf, self._cover_spec("Terracotta"), theme=th)
        self.assertEqual(len(pdf.pages), 1)
        stream = "\n".join(pdf.pages[0])
        self.assertIn("THIS PLANNER BELONGS TO:", stream)
        self.assertIn("Theme: Terracotta", stream)

    def test_42_render_cover_ocean(self):
        th = pdf_themes.get_theme("Ocean")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_cover_page(pdf, self._cover_spec("Ocean"), theme=th)
        self.assertEqual(len(pdf.pages), 1)
        stream = "\n".join(pdf.pages[0])
        self.assertIn("THIS PLANNER BELONGS TO:", stream)
        self.assertIn("Theme: Ocean", stream)

    def test_43_render_cover_minimal_mono(self):
        th = pdf_themes.get_theme("Minimal Mono")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_cover_page(pdf, self._cover_spec("Minimal Mono"), theme=th)
        self.assertEqual(len(pdf.pages), 1)
        stream = "\n".join(pdf.pages[0])
        self.assertIn("THIS PLANNER BELONGS TO:", stream)
        self.assertIn("Theme: Minimal Mono", stream)

    def test_44_render_cover_festive_marigold(self):
        th = pdf_themes.get_theme("Festive Marigold")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_cover_page(pdf, self._cover_spec("Festive Marigold"), theme=th)
        self.assertEqual(len(pdf.pages), 1)
        stream = "\n".join(pdf.pages[0])
        self.assertIn("THIS PLANNER BELONGS TO:", stream)
        self.assertIn("Theme: Festive Marigold", stream)

    def test_45_render_divider_calm_sage(self):
        th = pdf_themes.get_theme("Calm Sage")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_divider_page(pdf, "Monthly Spreads", 1, "Plan ahead", theme=th)
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("MONTHLY SPREADS", "\n".join(pdf.pages[0]))

    def test_46_render_divider_midnight_focus(self):
        th = pdf_themes.get_theme("Midnight Focus")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_divider_page(pdf, "Deep Work Blocks", 2, "High output", theme=th)
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("DEEP WORK BLOCKS", "\n".join(pdf.pages[0]))

    def test_47_render_divider_terracotta(self):
        th = pdf_themes.get_theme("Terracotta")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_divider_page(pdf, "Creative Projects", 3, "Inspiration", theme=th)
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("CREATIVE PROJECTS", "\n".join(pdf.pages[0]))

    def test_48_render_divider_ocean(self):
        th = pdf_themes.get_theme("Ocean")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_divider_page(pdf, "Term Overview", 4, "Academic goals", theme=th)
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("TERM OVERVIEW", "\n".join(pdf.pages[0]))

    def test_49_render_divider_minimal_mono(self):
        th = pdf_themes.get_theme("Minimal Mono")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_divider_page(pdf, "Daily Tracking", 5, "Precision", theme=th)
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("DAILY TRACKING", "\n".join(pdf.pages[0]))

    def test_50_render_divider_festive_marigold(self):
        th = pdf_themes.get_theme("Festive Marigold")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_divider_page(pdf, "Celebration & Holidays", 6, "Joyful planning", theme=th)
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("CELEBRATION & HOLIDAYS", "\n".join(pdf.pages[0]))

    def test_51_render_how_to_use_calm_sage(self):
        th = pdf_themes.get_theme("Calm Sage")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_how_to_use_page(pdf, {"instructions": ["Step 1", "Step 2"]}, theme=th, week_start="Sunday")
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("How to Use This Planner", "\n".join(pdf.pages[0]))

    def test_52_render_how_to_use_midnight_focus(self):
        th = pdf_themes.get_theme("Midnight Focus")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_how_to_use_page(pdf, {"instructions": ["Step 1", "Step 2"]}, theme=th, week_start="Monday")
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("How to Use This Planner", "\n".join(pdf.pages[0]))

    def test_53_render_how_to_use_terracotta(self):
        th = pdf_themes.get_theme("Terracotta")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_how_to_use_page(pdf, {"instructions": ["Step 1", "Step 2"]}, theme=th, week_start="Monday")
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("How to Use This Planner", "\n".join(pdf.pages[0]))

    def test_54_render_how_to_use_ocean(self):
        th = pdf_themes.get_theme("Ocean")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_how_to_use_page(pdf, {"instructions": ["Step 1", "Step 2"]}, theme=th, week_start="Sunday")
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("How to Use This Planner", "\n".join(pdf.pages[0]))

    def test_55_render_how_to_use_minimal_mono(self):
        th = pdf_themes.get_theme("Minimal Mono")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_how_to_use_page(pdf, {"instructions": ["Step 1", "Step 2"]}, theme=th, week_start="Monday")
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("How to Use This Planner", "\n".join(pdf.pages[0]))

    def test_56_render_how_to_use_festive_marigold(self):
        th = pdf_themes.get_theme("Festive Marigold")
        pdf = pd.Pdf("A4")
        pdf_themes.draw_how_to_use_page(pdf, {"instructions": ["Step 1", "Step 2"]}, theme=th, week_start="Sunday")
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("How to Use This Planner", "\n".join(pdf.pages[0]))


class ThemeIntegrationAndDeterminismTests(unittest.TestCase):
    """Test integration with product_builder and deterministic outputs."""

    def test_57_theme_to_pdf_design_style_format(self):
        st = pdf_themes.theme_to_pdf_design_style("Calm Sage")
        self.assertIn("name", st)
        self.assertIn("accent", st)
        self.assertIn("ink", st)
        self.assertIn("round", st)

    def test_58_register_themes_in_pdf_design(self):
        pdf_themes.register_themes_in_pdf_design()
        for th in pdf_themes.PREMIUM_THEMES.values():
            self.assertIn(th["slug"], pd.STYLES)
            self.assertIn(th["name"], pd.STYLES)

    def test_59_deterministic_pdf_bytes_calm_sage(self):
        spec = {
            "name": "Deterministic Planner",
            "short_name": "det-planner",
            "theme": "Calm Sage",
            "listing": {"title": "Deterministic Planner", "buyer": "Professionals", "tags": ["planner"], "platform": "Gumroad"},
            "pages": [{"title": "Day 1", "sections": [{"heading": "Goals", "type": "lines", "count": 3}]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            p1 = os.path.join(tmp, "out1.pdf")
            p2 = os.path.join(tmp, "out2.pdf")
            pdf_themes.register_themes_in_pdf_design()
            st = pdf_themes.theme_to_pdf_design_style("Calm Sage")
            spec["style"] = "calm_sage"

            pd.build_planner_pdf(p1, spec, "A4")
            pd.build_planner_pdf(p2, spec, "A4")

            with open(p1, "rb") as f1, open(p2, "rb") as f2:
                b1, b2 = f1.read(), f2.read()
            self.assertEqual(b1, b2)

    def test_60_deterministic_pdf_bytes_midnight_focus(self):
        spec = {
            "name": "Midnight Executive",
            "short_name": "mid-exec",
            "theme": "Midnight Focus",
            "listing": {"title": "Midnight Executive", "buyer": "Executives", "tags": ["planner"], "platform": "Gumroad"},
            "pages": [{"title": "Today", "sections": [{"heading": "Focus", "type": "lines", "count": 4}]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            p1 = os.path.join(tmp, "out1.pdf")
            p2 = os.path.join(tmp, "out2.pdf")
            pdf_themes.register_themes_in_pdf_design()
            spec["style"] = "midnight_focus"

            pd.build_planner_pdf(p1, spec, "A4")
            pd.build_planner_pdf(p2, spec, "A4")

            with open(p1, "rb") as f1, open(p2, "rb") as f2:
                b1, b2 = f1.read(), f2.read()
            self.assertEqual(b1, b2)

    def test_61_product_builder_planner_uses_theme(self):
        spec = {
            "name": "Ocean Teacher Planner",
            "kind": "planner",
            "niche": "ADHD Planners Agent",
            "theme": "Ocean",
            "short_name": "ocean-teacher",
            "listing": {
                "title": "Ocean Teacher Planner",
                "description": "Clear structured daily planner for classroom teachers.",
                "buyer": "Teachers",
                "tags": ["planner", "ocean"],
                "price_usd": 8,
                "platform": "Gumroad",
            },
            "instructions": ["Print it at 100% scale."],
            "cover_image_idea": "A clean desk flatlay.",
            "pages": [{"title": "Weekly Tasks", "sections": [{"heading": "Tasks", "type": "lines", "count": 5}]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            folder_name = pb.build_product(spec, 301, out_dir=tmp)
            folder_path = os.path.join(tmp, folder_name)
            pdf_a4 = os.path.join(folder_path, "ocean-teacher-A4.pdf")
            self.assertTrue(os.path.isfile(pdf_a4))
            with open(pdf_a4, "rb") as f:
                data = f.read()
            # Must contain theme style marker
            self.assertIn(b"/ChilliStyle (ocean)", data)

    def test_62_product_builder_dated_planner_uses_theme(self):
        spec = {
            "name": "Terracotta Dated Planner",
            "kind": "dated_planner",
            "niche": "2027 Planners Agent",
            "theme": "Terracotta",
            "short_name": "terra-dated",
            "listing": {
                "title": "Terracotta Dated Planner",
                "description": "Dated planner covering all 12 months with terracotta styling.",
                "buyer": "Planners",
                "tags": ["2027 planner", "dated planner"],
                "price_usd": 9,
                "platform": "Gumroad",
            },
            "instructions": ["Print the PDF."],
            "cover_image_idea": "Warm earthy desk setup.",
            "pages": [{"title": "Project List", "sections": [{"heading": "Projects", "type": "lines", "count": 4}]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            folder_name = pb.build_product(spec, 302, out_dir=tmp)
            folder_path = os.path.join(tmp, folder_name)
            pdf_a4 = os.path.join(folder_path, "terra-dated-A4.pdf")
            self.assertTrue(os.path.isfile(pdf_a4))
            with open(pdf_a4, "rb") as f:
                data = f.read()
            self.assertIn(b"/ChilliStyle (terracotta)", data)

    def test_63_page_count_stability_on_standard_planner(self):
        spec = {
            "name": "Standard Count Check",
            "short_name": "count-check",
            "theme": "Minimal Mono",
            "style": "minimal_mono",
            "listing": {"title": "Standard Count Check", "buyer": "Users", "tags": ["planner"], "platform": "Gumroad"},
            "pages": [
                {"title": "Page 1", "sections": [{"heading": "Sec 1", "type": "lines", "count": 4}]},
                {"title": "Page 2", "sections": [{"heading": "Sec 2", "type": "lines", "count": 4}]},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            pdf_themes.register_themes_in_pdf_design()
            p = os.path.join(tmp, "count.pdf")
            count = pd.build_planner_pdf(p, spec, "A4")
            # Cover (1) + How-to (1) + 2 content pages = 4 pages
            self.assertEqual(count, 4)

    def test_64_no_money_amounts_in_pdf_themes_module(self):
        with open(pdf_themes.__file__, "r", encoding="utf-8") as f:
            code = f.read()
        # Strictly forbidden currency signs or phrases
        forbidden = [r"\$\d+", r"USD\s*\d+", r"INR\s*\d+", r"Rs\.?\s*\d+", r"£\d+", r"€\d+", r"₹\d+"]
        for pat in forbidden:
            matches = re.findall(pat, code, re.IGNORECASE)
            self.assertEqual(matches, [], f"Found forbidden currency pattern {pat} in pdf_themes.py: {matches}")

    def test_65_no_money_amounts_in_generated_pdf_streams(self):
        spec = {
            "name": "Clean Text Check",
            "short_name": "clean-check",
            "theme": "Festive Marigold",
            "style": "festive_marigold",
            "listing": {"title": "Clean Text Check", "buyer": "Users", "tags": ["planner"], "platform": "Gumroad"},
            "pages": [{"title": "Day 1", "sections": [{"heading": "Sec 1", "type": "lines", "count": 4}]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            pdf_themes.register_themes_in_pdf_design()
            p = os.path.join(tmp, "clean.pdf")
            pd.build_planner_pdf(p, spec, "A4")
            with open(p, "rb") as f:
                data = f.read().decode("latin-1")
            forbidden = [r"\$\d+", r"USD\s*\d+", r"INR\s*\d+", r"Rs\.?\s*\d+", r"£\d+", r"€\d+", r"₹\d+"]
            for pat in forbidden:
                matches = re.findall(pat, data, re.IGNORECASE)
                self.assertEqual(matches, [], f"Found forbidden currency in generated PDF: {matches}")


if __name__ == "__main__":
    unittest.main()
