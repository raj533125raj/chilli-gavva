"""Tests for support_kit.py — buyer support files inside a product pack."""
import hashlib
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
import zipfile

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import support_kit  # noqa: E402

MONEY = re.compile(r"[$₹]|\b\d[\d,.]*\s*(?:USD|INR|Rs\.?|dollars?)\b"
                   r"|\b(?:USD|INR|Rs\.?)\s*\d", re.IGNORECASE)
TENGLISH = re.compile(r"\b(?:kavali|cheyali|cheyandi|idi|unnayo|ledu|avvadu|pettali|chadavalii)\b",
                      re.IGNORECASE)


def make_pack(tmp, name="12-sample-pack", files=None, zipped=None, broken_zip=False):
    root = Path(tmp) / name
    root.mkdir(parents=True, exist_ok=True)
    for filename, data in (files or {}).items():
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.encode("utf-8")
        path.write_bytes(data)
    if zipped is not None or broken_zip:
        archive = root / "product.zip"
        if broken_zip:
            archive.write_bytes(b"this is not a zip")
        else:
            with zipfile.ZipFile(archive, "w") as zf:
                for filename, data in zipped.items():
                    if isinstance(data, str):
                        data = data.encode("utf-8")
                    zf.writestr(filename, data)
    return root


def read(root, name):
    return (Path(root) / "support" / name).read_text(encoding="utf-8")


def pdf_bytes(root):
    return (Path(root) / "support" / support_kit.THANK_YOU_PDF).read_bytes()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def pack(self, **kwargs):
        return make_pack(self.tmp.name, **kwargs)


class DetectionTests(Base):
    def test_empty_pack_has_no_formats(self):
        root = self.pack(files={})
        self.assertEqual(support_kit.detect_formats(root), [])

    def test_xlsx_is_sheets(self):
        self.assertEqual(support_kit.detect_formats(self.pack(files={"a.xlsx": b"x"})), ["sheets"])

    def test_csv_is_sheets(self):
        self.assertEqual(support_kit.detect_formats(self.pack(files={"a.csv": "a,b"})), ["sheets"])

    def test_pdf_is_pdf(self):
        self.assertEqual(support_kit.detect_formats(self.pack(files={"a.pdf": b"%PDF-1.4\n"})), ["pdf"])

    def test_fillable_pdf_is_detected(self):
        data = b"%PDF-1.4\n1 0 obj << /Type /Catalog /AcroForm << /Fields [] >> >>\nendobj\n"
        self.assertEqual(support_kit.detect_formats(self.pack(files={"a.pdf": data})), ["pdf_fillable"])

    def test_resume_docx_is_resume(self):
        self.assertEqual(support_kit.detect_formats(self.pack(files={"my-resume.docx": b"x"})), ["resume"])

    def test_cover_letter_is_resume(self):
        self.assertEqual(support_kit.detect_formats(self.pack(files={"cover-letter.docx": b"x"})), ["resume"])

    def test_plain_docx_is_docx(self):
        self.assertEqual(support_kit.detect_formats(self.pack(files={"notes.docx": b"x"})), ["docx"])

    def test_md_and_csv_is_notion(self):
        self.assertEqual(support_kit.detect_formats(self.pack(files={"page.md": "# x", "db.csv": "a"})), ["notion"])

    def test_notion_named_file_is_notion(self):
        self.assertEqual(support_kit.detect_formats(self.pack(files={"notion-guide.txt": "x"})), ["notion"])

    def test_import_guide_is_notion(self):
        self.assertEqual(support_kit.detect_formats(self.pack(files={"HOW_TO_IMPORT.md": "x"})), ["notion"])

    def test_ics_is_calendar(self):
        self.assertEqual(support_kit.detect_formats(self.pack(files={"plan.ics": "BEGIN:VCALENDAR"})), ["calendar"])

    def test_png_is_images(self):
        self.assertEqual(support_kit.detect_formats(self.pack(files={"cover.png": b"x"})), ["images"])

    def test_text_only_is_text(self):
        self.assertEqual(support_kit.detect_formats(self.pack(files={"readme.txt": "x"})), ["text"])

    def test_zip_xlsx_is_sheets(self):
        root = self.pack(zipped={"book.xlsx": b"x"})
        self.assertEqual(support_kit.detect_formats(root), ["sheets"])

    def test_zip_md_and_csv_is_notion(self):
        root = self.pack(zipped={"page.md": "# x", "db.csv": "a"})
        self.assertEqual(support_kit.detect_formats(root), ["notion"])

    def test_zip_fillable_pdf_is_detected(self):
        root = self.pack(zipped={"form.pdf": b"%PDF-1.4\n/AcroForm\n"})
        self.assertEqual(support_kit.detect_formats(root), ["pdf_fillable"])

    def test_mixed_formats_are_sorted(self):
        root = self.pack(files={"a.xlsx": b"x", "b.pdf": b"%PDF-1.4\n", "c.png": b"x"})
        self.assertEqual(support_kit.detect_formats(root), ["images", "pdf", "sheets"])

    def test_support_folder_is_ignored(self):
        root = self.pack(files={"a.xlsx": b"x", "support/FAQ.md": "old"})
        self.assertEqual(support_kit.detect_formats(root), ["sheets"])

    def test_hidden_files_are_ignored(self):
        root = self.pack(files={".hidden.pdf": b"x", "a.xlsx": b"x"})
        self.assertEqual(support_kit.detect_formats(root), ["sheets"])

    def test_broken_zip_does_not_crash(self):
        root = self.pack(files={"a.txt": "x"}, broken_zip=True)
        self.assertEqual(support_kit.detect_formats(root), ["text"])

    def test_pack_files_sorted(self):
        root = self.pack(files={"z.txt": "x", "a.txt": "x"})
        self.assertEqual([f["name"] for f in support_kit.pack_files(root)], ["a.txt", "z.txt"])

    def test_pack_files_have_size(self):
        root = self.pack(files={"a.txt": "hello"})
        self.assertEqual(support_kit.pack_files(root)[0]["size"], 5)


class RequiredFilesTests(Base):
    def setUp(self):
        super().setUp()
        self.root = self.pack(files={"guide.pdf": b"%PDF-1.4\n", "book.xlsx": b"x"})
        self.result = support_kit.build(self.root)

    def test_support_folder_created(self):
        self.assertTrue((self.root / "support").is_dir())

    def test_how_to_print_exists(self):
        self.assertTrue((self.root / "support" / "HOW-TO-PRINT.md").is_file())

    def test_faq_exists(self):
        self.assertTrue((self.root / "support" / "FAQ.md").is_file())

    def test_personal_license_exists(self):
        self.assertTrue((self.root / "support" / "LICENSE-PERSONAL.txt").is_file())

    def test_commercial_note_exists(self):
        self.assertTrue((self.root / "support" / "LICENSE-COMMERCIAL-NOTE.txt").is_file())

    def test_support_replies_exists(self):
        self.assertTrue((self.root / "support" / "SUPPORT-REPLIES.md").is_file())

    def test_thank_you_pdf_exists(self):
        self.assertTrue((self.root / "support" / support_kit.THANK_YOU_PDF).is_file())

    def test_owner_notes_exists(self):
        self.assertTrue((self.root / "support" / "OWNER-NOTES.md").is_file())

    def test_manifest_lists_every_file(self):
        for name in support_kit.REQUIRED_FILES:
            self.assertIn(name, self.result["files"])

    def test_manifest_reports_formats(self):
        self.assertEqual(self.result["formats"], ["pdf", "sheets"])

    def test_manifest_reports_buyer_files(self):
        self.assertIn("guide.pdf", self.result["buyer_files"])

    def test_manifest_has_no_temp_files(self):
        self.assertFalse([p for p in (self.root / "support").glob(".support-*")])

    def test_custom_support_dir(self):
        other = Path(self.tmp.name) / "custom-support"
        result = support_kit.build(self.root, other)
        self.assertEqual(Path(result["support_dir"]), other)
        self.assertTrue((other / "FAQ.md").is_file())

    def test_missing_pack_raises(self):
        with self.assertRaises(NotADirectoryError):
            support_kit.build(Path(self.tmp.name) / "nope")

    def test_buyer_files_have_no_tenglish(self):
        for name in ("HOW-TO-PRINT.md", "FAQ.md", "LICENSE-PERSONAL.txt", "SUPPORT-REPLIES.md"):
            self.assertIsNone(TENGLISH.search(read(self.root, name)), name)

    def test_owner_notes_are_tenglish(self):
        self.assertTrue(TENGLISH.search(read(self.root, "OWNER-NOTES.md")))

    def test_pdf_is_one_page(self):
        self.assertEqual(pdf_bytes(self.root).count(b"/Type /Page "), 1)


class PrintGuideTests(Base):
    def setUp(self):
        super().setUp()
        self.root = self.pack(files={"a.pdf": b"%PDF-1.4\n"})
        support_kit.build(self.root)
        self.text = read(self.root, "HOW-TO-PRINT.md")

    def test_mentions_a4(self):
        self.assertIn("A4", self.text)

    def test_mentions_letter(self):
        self.assertIn("US Letter", self.text)

    def test_mentions_actual_size(self):
        self.assertIn("Actual size", self.text)

    def test_mentions_duplex(self):
        self.assertIn("Duplex", self.text)

    def test_mentions_long_edge(self):
        self.assertIn("long edge", self.text)

    def test_mentions_paper_weight(self):
        self.assertIn("gsm", self.text)

    def test_mentions_test_page(self):
        self.assertIn("page 1 only", self.text)

    def test_has_numbered_steps(self):
        self.assertRegex(self.text, r"(?m)^1\. ")

    def test_no_money(self):
        self.assertIsNone(MONEY.search(self.text))


class HowToUseTests(Base):
    FORMATS = {
        "sheets": ("HOW-TO-USE-SHEETS.md", "Excel"),
        "pdf": ("HOW-TO-USE-PDF.md", "PDF reader"),
        "pdf_fillable": ("HOW-TO-USE-PDF-FILLABLE.md", "form fields"),
        "notion": ("HOW-TO-USE-NOTION.md", "Import"),
        "resume": ("HOW-TO-USE-RESUME.md", ".docx"),
        "docx": ("HOW-TO-USE-WORD.md", "Word"),
        "calendar": ("HOW-TO-USE-CALENDAR.md", ".ics"),
        "images": ("HOW-TO-USE-IMAGES.md", ".png"),
        "text": ("HOW-TO-USE-TEXT.md", "text editor"),
    }

    def pack_for(self, fmt):
        mapping = {
            "sheets": {"a.xlsx": b"x"},
            "pdf": {"a.pdf": b"%PDF-1.4\n"},
            "pdf_fillable": {"a.pdf": b"%PDF-1.4\n/AcroForm\n"},
            "notion": {"a.md": "# x", "b.csv": "a"},
            "resume": {"a-resume.docx": b"x"},
            "docx": {"a.docx": b"x"},
            "calendar": {"a.ics": "BEGIN:VCALENDAR"},
            "images": {"a.png": b"x"},
            "text": {"a.txt": "x"},
        }
        root = self.pack(name=f"20-{fmt}", files=mapping[fmt])
        support_kit.build(root)
        return root

    def test_guides_only_for_real_formats(self):
        root = self.pack(files={"a.xlsx": b"x"})
        support_kit.build(root)
        guides = sorted(p.name for p in (root / "support").glob("HOW-TO-USE-*.md"))
        self.assertEqual(guides, ["HOW-TO-USE-SHEETS.md"])

    def test_every_guide_has_a_title(self):
        for fmt in self.FORMATS:
            root = self.pack_for(fmt)
            filename, _ = self.FORMATS[fmt]
            self.assertTrue(read(root, filename).startswith("# "))

    def test_every_guide_has_numbered_steps(self):
        for fmt in self.FORMATS:
            root = self.pack_for(fmt)
            filename, _ = self.FORMATS[fmt]
            self.assertRegex(read(root, filename), r"(?m)^1\. ")

    def test_every_guide_points_to_faq(self):
        for fmt in self.FORMATS:
            root = self.pack_for(fmt)
            filename, _ = self.FORMATS[fmt]
            self.assertIn("FAQ.md", read(root, filename))

    def test_every_guide_has_no_money(self):
        for fmt in self.FORMATS:
            root = self.pack_for(fmt)
            filename, _ = self.FORMATS[fmt]
            self.assertIsNone(MONEY.search(read(root, filename)), fmt)


def _guide_contains(fmt, needle):
    def test(self):
        root = HowToUseTests.pack_for(self, fmt)
        filename, _ = HowToUseTests.FORMATS[fmt]
        self.assertIn(needle, read(root, filename))
    return test


for _fmt, (_filename, _needle) in HowToUseTests.FORMATS.items():
    setattr(HowToUseTests, f"test_{_fmt}_guide_mentions_{re.sub(r'[^a-z0-9]+', '_', _needle.lower())}",
            _guide_contains(_fmt, _needle))


class FaqTests(Base):
    def setUp(self):
        super().setUp()
        self.root = self.pack(files={"alpha.xlsx": b"x", "beta.pdf": b"%PDF-1.4\n", "listing.md": "# listing"})
        support_kit.build(self.root)
        self.text = read(self.root, "FAQ.md")

    def test_lists_real_file_names(self):
        self.assertIn("alpha.xlsx", self.text)
        self.assertIn("beta.pdf", self.text)
        self.assertIn("listing.md", self.text)

    def test_counts_real_files(self):
        self.assertIn("contains 3 file(s)", self.text)

    def test_mentions_real_format_labels(self):
        self.assertIn("Spreadsheet", self.text)
        self.assertIn("Printable PDF", self.text)

    def test_says_which_file_first(self):
        self.assertIn("Which file should I open first", self.text)

    def test_answers_software_question(self):
        self.assertIn("What software do I need", self.text)

    def test_answers_print_question(self):
        self.assertIn("Can I print the files", self.text)

    def test_answers_refund_question(self):
        self.assertIn("refund", self.text.lower())

    def test_no_review_claims(self):
        lowered = self.text.lower()
        for bad in ("5 star", "five star", "testimonial", "review says"):
            self.assertNotIn(bad, lowered)

    def test_no_money(self):
        self.assertIsNone(MONEY.search(self.text))

    def test_empty_pack_faq_still_works(self):
        root = self.pack(name="13-empty", files={})
        support_kit.build(root)
        text = read(root, "FAQ.md")
        self.assertIn("0 file(s)", text)
        self.assertIn("The pack folder was empty", text)

    def test_big_pack_is_truncated_in_list(self):
        files = {f"file-{i:02d}.txt": "x" for i in range(20)}
        root = self.pack(name="14-big", files=files)
        support_kit.build(root)
        text = read(root, "FAQ.md")
        self.assertIn("more file(s)", text)
        self.assertNotIn("file-19.txt", text)


class LicenseTests(Base):
    def setUp(self):
        super().setUp()
        self.root = self.pack(files={"a.pdf": b"%PDF-1.4\n"})
        support_kit.build(self.root)
        self.personal = read(self.root, "LICENSE-PERSONAL.txt")
        self.commercial = read(self.root, "LICENSE-COMMERCIAL-NOTE.txt")

    def test_personal_allows_personal_use(self):
        self.assertIn("personal use", self.personal.lower())

    def test_personal_forbids_resale(self):
        self.assertIn("may not sell", self.personal.lower())

    def test_personal_forbids_public_upload(self):
        self.assertIn("public file-sharing", self.personal)

    def test_personal_has_no_income_promise(self):
        self.assertIn("does not promise", self.personal)

    def test_personal_names_the_pack(self):
        self.assertIn("Sample Pack", self.personal)

    def test_commercial_says_owner_decides(self):
        self.assertIn("seller decides", self.commercial)

    def test_commercial_is_not_automatic(self):
        self.assertIn("not an automatic commercial", self.commercial)

    def test_commercial_uses_amount_placeholder(self):
        self.assertIn("[amount]", self.commercial)

    def test_commercial_has_no_income_promise(self):
        self.assertIn("No promise of income", self.commercial)

    def test_personal_has_no_money(self):
        self.assertIsNone(MONEY.search(self.personal))

    def test_commercial_has_no_money(self):
        self.assertIsNone(MONEY.search(self.commercial))


class SupportReplyTests(Base):
    def setUp(self):
        super().setUp()
        self.root = self.pack(files={"a.pdf": b"%PDF-1.4\n"})
        support_kit.build(self.root)
        self.text = read(self.root, "SUPPORT-REPLIES.md")

    def test_has_download_template(self):
        self.assertIn("download problem", self.text.lower())

    def test_has_wrong_size_template(self):
        self.assertIn("wrong size", self.text.lower())

    def test_has_refund_template(self):
        self.assertIn("refund request", self.text.lower())

    def test_has_three_templates(self):
        self.assertEqual(len(re.findall(r"(?m)^## Template \d", self.text)), 3)

    def test_refund_does_not_promise(self):
        self.assertIn("cannot promise a refund", self.text)

    def test_uses_amount_placeholder(self):
        self.assertIn("[amount]", self.text)

    def test_says_store_policy(self):
        self.assertIn("store's refund policy", self.text)

    def test_is_polite(self):
        self.assertIn("Thank you", self.text)

    def test_no_money(self):
        self.assertIsNone(MONEY.search(self.text))


class OwnerNotesTests(Base):
    def setUp(self):
        super().setUp()
        self.root = self.pack(files={"a.xlsx": b"x", "b.pdf": b"%PDF-1.4\n"})
        support_kit.build(self.root)
        self.text = read(self.root, "OWNER-NOTES.md")

    def test_is_tenglish(self):
        self.assertTrue(TENGLISH.search(self.text))

    def test_says_owner_only(self):
        self.assertIn("owner", self.text.lower())

    def test_lists_real_formats(self):
        self.assertIn("Spreadsheet", self.text)
        self.assertIn("Printable PDF", self.text)

    def test_lists_real_files(self):
        self.assertIn("a.xlsx", self.text)
        self.assertIn("b.pdf", self.text)

    def test_asks_owner_to_decide_commercial_use(self):
        self.assertIn("Commercial use", self.text)

    def test_says_nothing_auto_publishes(self):
        self.assertIn("Nothing auto-publish", self.text)

    def test_uses_amount_placeholder(self):
        self.assertIn("[amount]", self.text)

    def test_no_currency_symbols(self):
        self.assertNotIn("$", self.text)
        self.assertNotIn("₹", self.text)


class ThankYouPdfTests(Base):
    def setUp(self):
        super().setUp()
        self.root = self.pack(files={"alpha.xlsx": b"x", "beta.pdf": b"%PDF-1.4\n"})
        support_kit.build(self.root)
        self.data = pdf_bytes(self.root)

    def test_starts_with_pdf_header(self):
        self.assertTrue(self.data.startswith(b"%PDF-1.4"))

    def test_ends_with_eof(self):
        self.assertTrue(self.data.rstrip().endswith(b"%%EOF"))

    def test_exactly_one_page(self):
        self.assertEqual(self.data.count(b"/Type /Page "), 1)

    def test_has_pages_object(self):
        self.assertIn(b"/Type /Pages", self.data)

    def test_has_fonts(self):
        self.assertIn(b"/BaseFont /Helvetica", self.data)

    def test_has_title_metadata(self):
        self.assertIn(b"/Title (Thank you", self.data)

    def test_contains_pack_name(self):
        self.assertIn(b"Sample Pack", self.data)

    def test_contains_quick_start(self):
        self.assertIn(b"quick start", self.data.lower())

    def test_contains_real_file_name(self):
        self.assertIn(b"alpha.xlsx", self.data)

    def test_mentions_license(self):
        self.assertIn(b"LICENSE-PERSONAL.txt", self.data)

    def test_no_money(self):
        self.assertIsNone(MONEY.search(self.data.decode("latin-1")))

    def test_bytes_are_deterministic(self):
        first = hashlib.sha256(self.data).hexdigest()
        support_kit.build(self.root)
        self.assertEqual(hashlib.sha256(pdf_bytes(self.root)).hexdigest(), first)

    def test_long_pack_name_does_not_crash(self):
        root = self.pack(name="15-" + "very-long-name-" * 12, files={"a.pdf": b"%PDF-1.4\n"})
        support_kit.build(root)
        self.assertGreater(len(pdf_bytes(root)), 1000)


class IdempotencyTests(Base):
    def setUp(self):
        super().setUp()
        self.root = self.pack(files={"a.xlsx": b"x", "b.pdf": b"%PDF-1.4\n"})

    def test_first_build_writes_files(self):
        result = support_kit.build(self.root)
        self.assertGreaterEqual(len(result["written"]), len(support_kit.REQUIRED_FILES))

    def test_second_build_writes_nothing(self):
        support_kit.build(self.root)
        result = support_kit.build(self.root)
        self.assertEqual(result["written"], [])

    def test_second_build_reports_all_unchanged(self):
        support_kit.build(self.root)
        result = support_kit.build(self.root)
        self.assertEqual(sorted(result["unchanged"]), sorted(result["files"]))

    def test_text_content_is_identical(self):
        support_kit.build(self.root)
        before = {p.name: p.read_bytes() for p in (self.root / "support").iterdir()}
        support_kit.build(self.root)
        after = {p.name: p.read_bytes() for p in (self.root / "support").iterdir()}
        self.assertEqual(before, after)

    def test_mtime_is_preserved_for_unchanged_files(self):
        support_kit.build(self.root)
        path = self.root / "support" / "FAQ.md"
        first = path.stat().st_mtime_ns
        support_kit.build(self.root)
        self.assertEqual(path.stat().st_mtime_ns, first)

    def test_changed_pack_refreshes_faq(self):
        support_kit.build(self.root)
        (self.root / "later.csv").write_text("a,b")
        result = support_kit.build(self.root)
        self.assertIn("FAQ.md", result["written"])
        self.assertIn("later.csv", read(self.root, "FAQ.md"))

    def test_build_does_not_delete_owner_files(self):
        support_kit.build(self.root)
        owner = self.root / "support" / "MY-NOTE.txt"
        owner.write_text("keep me")
        support_kit.build(self.root)
        self.assertTrue(owner.is_file())

    def test_empty_pack_still_gets_all_required_files(self):
        root = self.pack(name="16-empty")
        support_kit.build(root)
        for name in support_kit.REQUIRED_FILES:
            self.assertTrue((root / "support" / name).is_file(), name)

    def test_zip_pack_works(self):
        root = self.pack(name="17-upload", zipped={"book.xlsx": b"x", "guide.pdf": b"%PDF-1.4\n"})
        result = support_kit.build(root)
        self.assertIn("book.xlsx", result["buyer_files"])
        self.assertIn("HOW-TO-USE-SHEETS.md", result["files"])

    def test_rescan_after_build_does_not_see_support_files(self):
        support_kit.build(self.root)
        formats = support_kit.detect_formats(self.root)
        self.assertEqual(formats, ["pdf", "sheets"])


class EdgeTests(Base):
    def test_unicode_pack_name_is_safe(self):
        root = self.pack(name="18-planer-ürün", files={"a.pdf": b"%PDF-1.4\n"})
        support_kit.build(root)
        self.assertTrue((root / "support" / "FAQ.md").is_file())

    def test_display_name_strips_numeric_prefix(self):
        root = self.pack(name="99-my-great-pack", files={"a.txt": "x"})
        result = support_kit.build(root)
        self.assertEqual(result["pack"], "My Great Pack")

    def test_buyer_files_do_not_leak_absolute_path(self):
        root = self.pack(files={"a.pdf": b"%PDF-1.4\n"})
        support_kit.build(root)
        for name in ("FAQ.md", "HOW-TO-PRINT.md", "SUPPORT-REPLIES.md"):
            self.assertNotIn(str(Path(self.tmp.name)), read(root, name))

    def test_owner_notes_do_not_leak_absolute_path(self):
        root = self.pack(files={"a.pdf": b"%PDF-1.4\n"})
        support_kit.build(root)
        self.assertNotIn(str(Path(self.tmp.name)), read(root, "OWNER-NOTES.md"))

    def test_duplicate_names_in_zip_and_folder_count_once(self):
        root = self.pack(files={"a.xlsx": b"x"}, zipped={"a.xlsx": b"x"})
        names = [item["name"] for item in support_kit.pack_files(root)]
        self.assertEqual(names.count("a.xlsx"), 1)

    def test_larger_duplicate_wins(self):
        root = self.pack(files={"a.xlsx": b"x" * 5}, zipped={"a.xlsx": b"x" * 50})
        self.assertEqual(support_kit.pack_files(root)[0]["size"], 50)

    def test_support_dir_is_not_listed_as_buyer_file(self):
        root = self.pack(files={"a.pdf": b"%PDF-1.4\n"})
        support_kit.build(root)
        self.assertNotIn("FAQ.md", [f["name"] for f in support_kit.pack_files(root)])

    def test_markdown_faq_has_question_headings(self):
        root = self.pack(files={"a.pdf": b"%PDF-1.4\n"})
        support_kit.build(root)
        self.assertGreaterEqual(read(root, "FAQ.md").count("\n## "), 8)

    def test_main_prints_files(self):
        import io
        from unittest import mock
        root = self.pack(files={"a.pdf": b"%PDF-1.4\n"})
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            support_kit.main([str(root)])
        self.assertIn("Support files", buffer.getvalue())
        self.assertIn("FAQ.md", buffer.getvalue())

    def test_format_labels_cover_every_detected_format(self):
        for fmt in support_kit.FORMAT_LABELS:
            self.assertTrue(support_kit.FORMAT_LABELS[fmt])

    def test_how_to_use_table_covers_every_labelled_format(self):
        for fmt in support_kit.FORMAT_LABELS:
            self.assertIn(fmt, support_kit.HOW_TO_USE)


if __name__ == "__main__":
    unittest.main()
