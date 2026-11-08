"""Tests for faq_builder.py: buyer FAQ built only from facts in a pack folder."""
import io
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest

import faq_builder


def make_zip(path, entries=("planner-a4.pdf", "about.docx")):
    with zipfile.ZipFile(path, "w") as archive:
        for entry in entries:
            archive.writestr(entry, "x" * 64)


class FaqBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packs_root = self.root / "exported_content" / "products" / "upload"
        self.faq_root = self.root / "exported_content" / "products" / "faq"
        self.packs_root.mkdir(parents=True)

    def pack(self, name=None, files=(), licence=None, zip_entries=None):
        target = self.packs_root / (name or "pack-a")
        target.mkdir(exist_ok=True)
        for file_name in files:
            (target / file_name).write_bytes(b"png" if file_name.endswith(".png") else b"pdf")
        if licence:
            (target / "LICENSE.txt").write_text(licence, encoding="utf-8")
        if zip_entries:
            make_zip(target / "product.zip", entries=zip_entries)
        return target

    def faq(self, **kwargs):
        return faq_builder.build_faq(self.pack(**kwargs))

    def test_q_count_between_six_and_eight(self):
        for faq in (self.faq(), self.faq(files=("cover.png", "planner-letter.pdf"),
                                        licence="Licence: personal use"),
                    self.faq(files=("planner-a4.pdf", "grid.xlsx"))):
            self.assertGreaterEqual(faq.count("\n## "), 6)
            self.assertLessEqual(faq.count("\n## "), 8)

    def test_formats_from_pack_files(self):
        faq = self.faq(files=("planner.pdf", "grid.xlsx", "about.docx", "preview-1.png"))
        for word in ("PDF", "XLSX", "DOCX", "PNG"):
            self.assertIn(word, faq)

    def test_formats_inside_product_zip_counted(self):
        faq = self.faq(zip_entries=("planner-a4.pdf", "about.docx"))
        self.assertIn("PDF", faq)
        self.assertIn("DOCX", faq)

    def test_a4_size_found_from_file_names(self):
        self.assertIn("A4", self.faq(files=("planner-a4.pdf",)))
        self.assertNotIn("US Letter", self.faq(files=("planner-a4.pdf",)))

    def test_us_letter_found_and_not_customer_legal_claim(self):
        faq = self.faq(files=("planner-letter.pdf", "planner-us-letter.pdf"))
        self.assertIn("US Letter", faq)
        self.assertNotIn("A4", faq)

    def test_no_size_question_when_no_size_named(self):
        self.assertNotIn("page sizes", self.faq(files=("planner.pdf",)))

    def test_licence_line_quoted_and_prefix_washed(self):
        faq = self.faq(licence="Licence: personal use for one household")
        self.assertIn("personal use for one household", faq)
        self.assertNotIn("Licence: personal use", faq)

    def test_licence_without_line_marks_still_used(self):
        faq = self.faq(licence="Personal use, single farm family only")
        self.assertIn("Personal use, single farm family only", faq)
        self.assertIn("licence", faq)

    def test_no_licence_question_when_licence_missing(self):
        faq = self.faq(files=("planner.pdf",))
        self.assertNotIn("What can I do with these files?", faq)
        self.assertNotIn("commercial", faq.lower())

    def test_print_answer_keeps_100_percent_scale(self):
        faq = self.faq()
        self.assertIn("100% scale", faq)
        self.assertIn("Fit to page", faq)

    def test_xlsx_apps_answered_when_xlsx_present(self):
        faq = self.faq(files=("grid.xlsx",))
        for word in ("Excel", "Google Sheets", "LibreOffice"):
            self.assertIn(word, faq)

    def test_no_spreadsheet_apps_when_no_xlsx(self):
        faq = self.faq(files=("planner.pdf",))
        self.assertNotIn("Google Sheets", faq)

    def test_no_money_passes_through(self):
        faq = self.faq(licence="Licence: personal use, sold for $4.99 extra? no")
        self.assertNotIn("$", faq)
        self.assertIn("[price removed]", faq)

    def test_listing_title_never_read(self):
        pack = self.pack(files=("planner.pdf",))
        (pack / "listing.txt").write_text("Title: Secret Cowrie Planner\n", encoding="utf-8")
        faq = faq_builder.build_faq(pack)
        self.assertNotIn("Secret Cowrie Planner", faq)

    def test_contact_closing_always_present(self):
        self.assertIn("Questions? Contact the seller", self.faq())

    def test_digital_only_shipping_answer(self):
        faq = self.faq()
        self.assertIn("nothing is mailed", faq)

    def test_missing_folder_raises(self):
        with self.assertRaises(FileNotFoundError):
            faq_builder.build_faq(self.root / "nope")

    def test_corrupt_zip_tolerated(self):
        pack = self.pack(files=("planner.pdf",))
        (pack / "product.zip").write_bytes(b"definitely not a zip")
        faq = faq_builder.build_faq(pack)
        self.assertIn("PDF", faq)

    def test_faq_is_deterministic(self):
        self.assertEqual(self.faq(files=("planner.pdf",)), self.faq(files=("planner.pdf",)))

    def test_write_all_writes_outside_packs_and_names_files(self):
        self.pack("z-pack", files=("planner-a4.pdf",))
        self.pack("a-pack", files=("planner-letter.pdf",))
        written = faq_builder.write_all(self.packs_root, self.faq_root)
        self.assertEqual([path.name for path in written], ["a-pack.md", "z-pack.md"])
        for path in written:
            self.assertTrue(path.is_file())
            self.assertTrue(str(path).startswith(str(self.faq_root)))
            self.assertIn("buyer FAQ", path.read_text(encoding="utf-8"))
        inside = self.packs_root / "z-pack"
        self.assertFalse((inside / "faq").exists())
        self.assertEqual(sorted(p.name for p in inside.iterdir()), ["planner-a4.pdf"])

    def test_write_all_empty_root_reports_nothing(self):
        self.assertEqual(faq_builder.write_all(self.root / "gone", self.faq_root), [])

    def test_cli_prints_paths_and_total(self):
        self.pack("cli-pack", files=("planner.pdf",))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = faq_builder.main([str(self.packs_root), "--all",
                                     "--faq-dir", str(self.faq_root)])
        out = buffer.getvalue().splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(out[0], str(self.faq_root / "cli-pack.md"))
        self.assertTrue((self.faq_root / "cli-pack.md").is_file())


if __name__ == "__main__":
    unittest.main()
