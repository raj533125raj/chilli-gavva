"""Unit tests for builder_signature.py (BINNY-V4C).

Comprehensive test coverage:
- FORMAT and KEYWORDS constants
- Placeholders definition and replacement
- 6 HTML email signature templates validation (balanced tags, table-based, inline CSS only, width <= 600px, web-safe fonts)
- Preview PNG generation, Pillow drawing, file dimensions
- README-HOW-TO-INSTALL.txt content, instructions (Gmail, Outlook, Apple Mail), Tenglish phrasing, no money amounts
- Atomic file writing and build(spec, out_dir) manifest
- Edge cases and error handling
"""

import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import builder_signature


class TestSignaturePack(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* env in setUp with mock.patch.dict
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_"):
                os.environ.pop(k, None)
        os.environ["CHILLI_TEST"] = "1"

        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # 1. Constants and metadata
    def test_01_format_constant(self):
        self.assertEqual(builder_signature.FORMAT, "email_signature")

    def test_02_keywords_constant(self):
        self.assertIn("email signature", builder_signature.KEYWORDS)
        self.assertIn("signature template", builder_signature.KEYWORDS)
        self.assertEqual(len(builder_signature.KEYWORDS), 2)

    def test_03_canonical_placeholders_count(self):
        self.assertEqual(len(builder_signature.PLACEHOLDERS), 5)

    def test_04_canonical_placeholders_exact(self):
        expected = ["[NAME]", "[ROLE]", "[PHONE]", "[EMAIL]", "[WEBSITE]"]
        for p in expected:
            self.assertIn(p, builder_signature.PLACEHOLDERS)

    def test_05_web_safe_fonts_defined(self):
        self.assertIn("Arial", builder_signature.WEB_SAFE_FONTS)
        self.assertIn("Helvetica", builder_signature.WEB_SAFE_FONTS)
        self.assertIn("Verdana", builder_signature.WEB_SAFE_FONTS)
        self.assertIn("Trebuchet MS", builder_signature.WEB_SAFE_FONTS)
        self.assertIn("Georgia", builder_signature.WEB_SAFE_FONTS)

    def test_06_template_defs_count(self):
        self.assertEqual(len(builder_signature.TEMPLATE_DEFS), 6)

    def test_07_template_defs_keys(self):
        for tdef in builder_signature.TEMPLATE_DEFS:
            self.assertIn("id", tdef)
            self.assertIn("name", tdef)
            self.assertIn("filename", tdef)
            self.assertIn("preview", tdef)
            self.assertIn("primary_color", tdef)
            self.assertIn("accent_color", tdef)

    def test_08_template_generators_count(self):
        self.assertEqual(len(builder_signature.TEMPLATE_GENERATORS), 6)

    # 2. Template Generation & HTML balanced tags
    def test_09_template_1_balanced_tags(self):
        content = builder_signature.generate_template_1()
        valid, errors = builder_signature.validate_html(content)
        self.assertTrue(valid, f"Template 1 validation errors: {errors}")

    def test_10_template_2_balanced_tags(self):
        content = builder_signature.generate_template_2()
        valid, errors = builder_signature.validate_html(content)
        self.assertTrue(valid, f"Template 2 validation errors: {errors}")

    def test_11_template_3_balanced_tags(self):
        content = builder_signature.generate_template_3()
        valid, errors = builder_signature.validate_html(content)
        self.assertTrue(valid, f"Template 3 validation errors: {errors}")

    def test_12_template_4_balanced_tags(self):
        content = builder_signature.generate_template_4()
        valid, errors = builder_signature.validate_html(content)
        self.assertTrue(valid, f"Template 4 validation errors: {errors}")

    def test_13_template_5_balanced_tags(self):
        content = builder_signature.generate_template_5()
        valid, errors = builder_signature.validate_html(content)
        self.assertTrue(valid, f"Template 5 validation errors: {errors}")

    def test_14_template_6_balanced_tags(self):
        content = builder_signature.generate_template_6()
        valid, errors = builder_signature.validate_html(content)
        self.assertTrue(valid, f"Template 6 validation errors: {errors}")

    # 3. Placeholders in each template
    def test_15_template_1_contains_all_placeholders(self):
        content = builder_signature.generate_template_1()
        for p in builder_signature.PLACEHOLDERS:
            self.assertIn(p, content)

    def test_16_template_2_contains_all_placeholders(self):
        content = builder_signature.generate_template_2()
        for p in builder_signature.PLACEHOLDERS:
            self.assertIn(p, content)

    def test_17_template_3_contains_all_placeholders(self):
        content = builder_signature.generate_template_3()
        for p in builder_signature.PLACEHOLDERS:
            self.assertIn(p, content)

    def test_18_template_4_contains_all_placeholders(self):
        content = builder_signature.generate_template_4()
        for p in builder_signature.PLACEHOLDERS:
            self.assertIn(p, content)

    def test_19_template_5_contains_all_placeholders(self):
        content = builder_signature.generate_template_5()
        for p in builder_signature.PLACEHOLDERS:
            self.assertIn(p, content)

    def test_20_template_6_contains_all_placeholders(self):
        content = builder_signature.generate_template_6()
        for p in builder_signature.PLACEHOLDERS:
            self.assertIn(p, content)

    # 4. Email signature rules (Width <= 600px, inline CSS only, web-safe fonts, table-based)
    def test_21_template_1_rules(self):
        content = builder_signature.generate_template_1()
        valid, errors = builder_signature.check_email_signature_rules(content)
        self.assertTrue(valid, errors)

    def test_22_template_2_rules(self):
        content = builder_signature.generate_template_2()
        valid, errors = builder_signature.check_email_signature_rules(content)
        self.assertTrue(valid, errors)

    def test_23_template_3_rules(self):
        content = builder_signature.generate_template_3()
        valid, errors = builder_signature.check_email_signature_rules(content)
        self.assertTrue(valid, errors)

    def test_24_template_4_rules(self):
        content = builder_signature.generate_template_4()
        valid, errors = builder_signature.check_email_signature_rules(content)
        self.assertTrue(valid, errors)

    def test_25_template_5_rules(self):
        content = builder_signature.generate_template_5()
        valid, errors = builder_signature.check_email_signature_rules(content)
        self.assertTrue(valid, errors)

    def test_26_template_6_rules(self):
        content = builder_signature.generate_template_6()
        valid, errors = builder_signature.check_email_signature_rules(content)
        self.assertTrue(valid, errors)

    def test_27_all_templates_width_lte_600(self):
        for i in range(6):
            content = builder_signature.render_template_html(i)
            widths = re.findall(r'width\s*[:=]\s*["\']?(\d+)', content, re.IGNORECASE)
            for w in widths:
                self.assertLessEqual(int(w), 600, f"Template {i} has width {w} > 600")

    def test_28_all_templates_no_external_stylesheets(self):
        for i in range(6):
            content = builder_signature.render_template_html(i)
            self.assertNotRegex(content, r'<link[^>]+rel=["\']?stylesheet')

    def test_29_all_templates_no_script_tags(self):
        for i in range(6):
            content = builder_signature.render_template_html(i)
            self.assertNotIn("<script", content.lower())

    def test_30_all_templates_have_table(self):
        for i in range(6):
            content = builder_signature.render_template_html(i)
            self.assertIn("<table", content.lower())

    # 5. Placeholder substitution
    def test_31_substitute_placeholders_basic(self):
        raw = "Hello [NAME], your role is [ROLE]."
        subbed = builder_signature.substitute_placeholders(raw, {"[NAME]": "Alice", "[ROLE]": "Engineer"})
        self.assertEqual(subbed, "Hello Alice, your role is Engineer.")

    def test_32_substitute_placeholders_escapes_html(self):
        raw = "Contact: [NAME] and [ROLE]"
        subbed = builder_signature.substitute_placeholders(raw, {"[NAME]": "Alice <Admin>", "[ROLE]": "R&D"})
        self.assertEqual(subbed, "Contact: Alice &lt;Admin&gt; and R&amp;D")

    def test_33_substitute_all_5_placeholders_in_template_1(self):
        content = builder_signature.generate_template_1()
        mapping = {
            "[NAME]": "Chilli Tester",
            "[ROLE]": "Quality Lead",
            "[PHONE]": "+91 9876543210",
            "[EMAIL]": "tester@example.com",
            "[WEBSITE]": "chilligavva.example",
        }
        subbed = builder_signature.substitute_placeholders(content, mapping)
        for p in builder_signature.PLACEHOLDERS:
            self.assertNotIn(p, subbed)
        self.assertIn("Chilli Tester", subbed)
        self.assertIn("Quality Lead", subbed)

    # 6. Preview PNG Rendering (Pillow)
    def test_34_preview_png_created_for_all_6(self):
        for i in range(6):
            png_path = os.path.join(self.tmp_dir, f"test_preview_{i}.png")
            out = builder_signature.render_preview_png(i, png_path)
            self.assertTrue(os.path.isfile(out))
            self.assertGreater(os.path.getsize(out), 500)

    def test_35_preview_png_dimensions(self):
        from PIL import Image
        for i in range(6):
            png_path = os.path.join(self.tmp_dir, f"dim_{i}.png")
            builder_signature.render_preview_png(i, png_path)
            with Image.open(png_path) as im:
                self.assertEqual(im.size, (600, 240))
                self.assertEqual(im.format, "PNG")

    def test_36_preview_png_custom_colors(self):
        spec = {"primary_color": "#ff0000", "accent_color": "#00ff00"}
        png_path = os.path.join(self.tmp_dir, "custom_color.png")
        out = builder_signature.render_preview_png(0, png_path, spec=spec)
        self.assertTrue(os.path.isfile(out))

    # 7. README-HOW-TO-INSTALL.txt tests
    def test_37_readme_content_has_gmail(self):
        readme = builder_signature.get_readme_content()
        self.assertIn("GMAIL", readme.upper())

    def test_38_readme_content_has_outlook(self):
        readme = builder_signature.get_readme_content()
        self.assertIn("OUTLOOK", readme.upper())

    def test_39_readme_content_has_apple_mail(self):
        readme = builder_signature.get_readme_content()
        self.assertIn("APPLE MAIL", readme.upper())

    def test_40_readme_content_exact_tenglish_phrase(self):
        readme = builder_signature.get_readme_content()
        self.assertIn("Settings lo Signature section open cheyandi", readme)

    def test_41_readme_mentions_all_5_placeholders(self):
        readme = builder_signature.get_readme_content()
        for p in builder_signature.PLACEHOLDERS:
            self.assertIn(p, readme)

    def test_42_readme_has_no_currency_amounts(self):
        readme = builder_signature.get_readme_content()
        currency_rx = re.compile(
            r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
            re.IGNORECASE,
        )
        self.assertIsNone(currency_rx.search(readme))

    # 8. build(spec, out_dir) Functionality
    def test_43_build_creates_manifest(self):
        res = builder_signature.build({}, self.tmp_dir)
        self.assertEqual(res["format"], "email_signature")
        self.assertEqual(len(res["templates"]), 6)
        self.assertEqual(len(res["previews"]), 6)
        self.assertTrue(os.path.isfile(res["readme_path"]))

    def test_44_build_creates_expected_files_on_disk(self):
        builder_signature.build({}, self.tmp_dir)
        files = os.listdir(self.tmp_dir)
        for tdef in builder_signature.TEMPLATE_DEFS:
            self.assertIn(tdef["filename"], files)
            self.assertIn(tdef["preview"], files)
        self.assertIn("README-HOW-TO-INSTALL.txt", files)

    def test_45_build_file_count_exact(self):
        builder_signature.build({}, self.tmp_dir)
        files = os.listdir(self.tmp_dir)
        # 6 html + 6 png + 1 txt = 13 files
        self.assertEqual(len(files), 13)

    def test_46_build_with_custom_colors(self):
        spec = {
            "primary_color": "#123456",
            "accent_color": "#789abc",
        }
        res = builder_signature.build(spec, self.tmp_dir)
        for t in res["templates"]:
            with open(t["path"], encoding="utf-8") as f:
                content = f.read()
            self.assertIn("#123456", content)
            self.assertIn("#789abc", content)

    def test_47_build_without_out_dir_uses_default(self):
        with mock.patch("os.makedirs") as mock_dirs, mock.patch("builder_signature.atomic_write") as mock_write, mock.patch("builder_signature.render_preview_png") as mock_preview:
            res = builder_signature.build({})
            self.assertIn("exported_content", res["out_dir"])
            self.assertIn("signatures", res["out_dir"])

    # 9. HTML Validator Edge Cases
    def test_48_validate_html_empty_input(self):
        valid, errors = builder_signature.validate_html("")
        self.assertFalse(valid)
        self.assertIn("empty", errors[0])

    def test_49_validate_html_non_string(self):
        valid, errors = builder_signature.validate_html(None)
        self.assertFalse(valid)

    def test_50_validate_html_unclosed_tag(self):
        bad_html = "<table><tr><td>text</tr></table>"
        valid, errors = builder_signature.validate_html(bad_html)
        self.assertFalse(valid)
        self.assertTrue(any("Mismatched" in e or "Unclosed" in e for e in errors))

    def test_51_validate_html_unexpected_closing_tag(self):
        bad_html = "</div><p>hello</p>"
        valid, errors = builder_signature.validate_html(bad_html)
        self.assertFalse(valid)
        self.assertTrue(any("Unexpected closing tag" in e for e in errors))

    def test_52_validate_html_external_stylesheet_detected(self):
        bad_html = '<html><head><link rel="stylesheet" href="style.css"></head><body><table><tr><td>hi</td></tr></table></body></html>'
        valid, errors = builder_signature.validate_html(bad_html)
        self.assertFalse(valid)
        self.assertTrue(any("External stylesheet" in e for e in errors))

    def test_53_validate_html_script_detected(self):
        bad_html = '<table><tr><td>hi<script>alert(1)</script></td></tr></table>'
        valid, errors = builder_signature.validate_html(bad_html)
        self.assertFalse(valid)
        self.assertTrue(any("Script tag is not allowed" in e for e in errors))

    def test_54_validate_html_self_closing_tags_allowed(self):
        good_html = '<table><tr><td>line 1<br>line 2<hr><img src="pic.png" alt="logo"></td></tr></table>'
        valid, errors = builder_signature.validate_html(good_html)
        self.assertTrue(valid, errors)

    # 10. Email Rule Checker Edge Cases
    def test_55_rules_check_exceeding_width(self):
        bad_html = '<table width="750"><tr><td>wide</td></tr></table>'
        valid, errors = builder_signature.check_email_signature_rules(bad_html)
        self.assertFalse(valid)
        self.assertTrue(any("exceeding 600px" in e for e in errors))

    def test_56_rules_check_missing_table(self):
        bad_html = '<div><p>no table here</p></div>'
        valid, errors = builder_signature.check_email_signature_rules(bad_html)
        self.assertFalse(valid)
        self.assertTrue(any("table-based" in e for e in errors))

    def test_57_rules_check_non_websafe_font(self):
        bad_html = '<table style="font-family: ComicSansCustomX, FancyFont;"><tr><td>custom</td></tr></table>'
        valid, errors = builder_signature.check_email_signature_rules(bad_html)
        self.assertFalse(valid)
        self.assertTrue(any("web-safe fonts" in e for e in errors))

    # 11. render_template_html Index Bounds & Atomic Writes
    def test_58_render_template_html_out_of_bounds_negative(self):
        with self.assertRaises(IndexError):
            builder_signature.render_template_html(-1)

    def test_59_render_template_html_out_of_bounds_high(self):
        with self.assertRaises(IndexError):
            builder_signature.render_template_html(6)

    def test_60_atomic_write_creates_file(self):
        target = os.path.join(self.tmp_dir, "sub", "test.txt")
        builder_signature.atomic_write(target, "atomic content")
        self.assertTrue(os.path.isfile(target))
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "atomic content")

    def test_61_atomic_write_overwrites_existing(self):
        target = os.path.join(self.tmp_dir, "test.txt")
        builder_signature.atomic_write(target, "v1")
        builder_signature.atomic_write(target, "v2")
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "v2")

    # 12. Placeholder and logo slots in templates
    def test_62_template_2_has_logo_slot(self):
        content = builder_signature.generate_template_2()
        self.assertIn("LOGO", content)
        self.assertIn("PHOTO", content)

    def test_63_template_6_has_avatar_slot(self):
        content = builder_signature.generate_template_6()
        self.assertIn("AVATAR", content)

    def test_64_no_money_amounts_in_any_template(self):
        currency_rx = re.compile(
            r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
            re.IGNORECASE,
        )
        for i in range(6):
            content = builder_signature.render_template_html(i)
            self.assertIsNone(currency_rx.search(content))


if __name__ == "__main__":
    unittest.main()
