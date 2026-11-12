"""Offline upload packs: real truth checks, buyer allowlist and price refusal."""
import base64
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import product_builder
import truth_check
import upload_pack


LISTING = """# **Focus Planner**

## Description
Two printable pages: a Brain Dump page and a Daily Focus page.
Print on A4 or US Letter. Use **a pen** and [read the guide](https://example.invalid/guide).

## Tags (3)
- focus
- printable planner
- calm routine

## What you get
- **A4 PDF**
- `US Letter PDF`
- README and personal use licence
"""
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=")


class UploadPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Build the expensive planner PDFs and base product folder ONCE; every test
        # gets a cheap private copy, so the PDF generator is not re-run 21 times.
        cls._base = tempfile.TemporaryDirectory()
        base = Path(cls._base.name)
        prod = base / "products" / "17-focus-planner"
        prod.mkdir(parents=True)
        (prod / "listing.md").write_text(LISTING)
        (prod / "README.md").write_text("# Focus Planner\nWrite with a pen.\n")
        (prod / "LICENSE.txt").write_text(product_builder.LICENSE_TEXT)
        pages = [
            {"title": "Brain Dump", "sections": [{"heading": "Thoughts", "type": "lines", "count": 20}]},
            {"title": "Daily Focus", "sections": [{"heading": "Priorities", "type": "lines", "count": 20}]}]
        spec = {"name": "Focus Planner", "listing": {"title": "Focus Planner", "buyer": "adults"},
                "pages": pages}
        for size in ("A4", "Letter"):
            product_builder.build_planner_pdf(str(prod / f"focus-{size}.pdf"), spec, size)
        (prod / "cover.png").write_bytes(PNG)
        cls._base_folder = prod

    @classmethod
    def tearDownClass(cls):
        cls._base.cleanup()
        super().tearDownClass()

    def setUp(self):
        # Remove every inherited GITHUB_* key, including newly introduced CI keys.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
        self.environment = mock.patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.folder = self.root / "products" / "17-focus-planner"
        self.folder.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(UploadPackTests._base_folder, self.folder)
        self.out = self.root / "packs"
        self.listing = self.folder / "listing.md"

    def test_good_real_folder_four_files_and_exact_buyer_content(self):
        self.assertEqual(truth_check.check(str(self.folder))["verdict"], "ready")
        (self.folder / "state.enc").write_bytes(b"not plaintext")
        (self.folder / "state.db").write_bytes(b"not a database")
        (self.folder / "state.db-wal").write_text("$999 private state")
        (self.folder / "state.sqlite-journal").write_text("$999 private state")
        (self.folder / "state.db.backup").write_text("$999 private state")
        (self.folder / "state.json").write_text('{"private": true}')
        (self.folder / "extra.txt").write_text("Internal build notes")
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        output = upload_pack.make(self.folder, self.out)
        self.assertEqual(output, self.out / self.folder.name)
        self.assertEqual({p.name for p in output.iterdir()},
                         {"product.zip", "listing.txt", "checklist.txt", "cover.png"})
        with zipfile.ZipFile(output / "product.zip") as archive:
            self.assertEqual(set(archive.namelist()),
                             {"focus-A4.pdf", "focus-Letter.pdf", "README.md", "LICENSE.txt"})
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                self.assertEqual(archive.read(name), before[name])
        self.assertEqual((output / "cover.png").read_bytes(), PNG)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.folder.iterdir()})
        listing = (output / "listing.txt").read_text()
        for expected in ("Title: Focus Planner", "Description:", "Tags: focus, printable planner, calm routine",
                         "What you get:\nA4 PDF\nUS Letter PDF", "a pen", "read the guide"):
            self.assertIn(expected, listing)
        for symbol in ("**", "#", "`", "[", "]", "- "):
            self.assertNotIn(symbol, listing)
        checklist = (output / "checklist.txt").read_text()
        self.assertEqual(checklist.splitlines()[0], "nothing was uploaded")
        for platform in ("Gumroad", "Topmate"):
            section = checklist.split(platform + "\n", 1)[1].split("\n\n", 1)[0]
            for field in ("title", "description", "price yourself", "product.zip", "cover.png"):
                self.assertIn(field, section)

    def test_real_needs_fix_folder_refused_without_output(self):
        self.listing.write_text(LISTING + "\n## Extra\nA one-click duplicate button.\n")
        with self.assertRaisesRegex(ValueError, "NEEDS FIX"):
            upload_pack.make(self.folder, self.out)
        self.assertFalse(self.out.exists())

    def test_checker_called_with_images_and_not_bypassed(self):
        with mock.patch.object(upload_pack.truth_check, "check", return_value={"verdict": "needs fix"}) as check:
            with self.assertRaisesRegex(ValueError, "not ready"):
                upload_pack.make(self.folder, self.out)
        check.assert_called_once_with(str(self.folder.resolve()))

    def test_currency_forms_in_listing_are_refused_not_removed(self):
        for price in ("$8", "8 USD", "USD 8", "0.00 USD", "₹499", "Rs. 1,299", "499 INR", "€2.50",
                      "GBP 4", "4 pounds", "5 dollars", "５ USD", "&#36;8", "**$**8", "₱300", "100 rupees"):
            with self.subTest(price=price):
                self.listing.write_text(LISTING + "\n## Owner note\nPrice: " + price + "\n")
                with self.assertRaisesRegex(ValueError, "Money amount found"):
                    upload_pack.make(self.folder, self.out)
                self.assertFalse(self.out.exists())

    def test_years_counts_percentages_and_currency_words_without_amounts_allowed(self):
        upload_pack._check_money("2027 planner, 12 pages, 100% scale, owner sets price in USD, every rupee", "test")

    def test_money_anywhere_in_readme_licence_or_sidecar_is_refused(self):
        for name in ("README.md", "LICENSE.txt", "notes.txt", "notes.csv", "custom-notes"):
            with self.subTest(name=name):
                path = self.folder / name
                old = path.read_bytes() if path.exists() else None
                path.write_text("Personal use. Pay ₹100.")
                try:
                    with self.assertRaisesRegex(ValueError, "Money amount found"):
                        upload_pack.make(self.folder, self.out)
                finally:
                    if old is None:
                        path.unlink()
                    else:
                        path.write_bytes(old)
        self.assertFalse(self.out.exists())

    def test_pdf_money_is_refused_even_if_listing_is_price_free(self):
        spec = {"name": "Focus Planner", "listing": {"title": "Focus Planner", "buyer": "adults"},
                "pages": [{"title": "A fee of $8", "sections": []}]}
        product_builder.build_planner_pdf(str(self.folder / "priced.pdf"), spec, "A4")
        with self.assertRaisesRegex(ValueError, "Money amount found.*priced.pdf"):
            upload_pack.make(self.folder, self.out)

    def test_workbook_strings_checked_but_absolute_formula_references_allowed(self):
        path = self.folder / "extra.xlsx"
        for text in ("Pay USD 8", "Free of prices"):
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("xl/workbook.xml", '<workbook><sheets/></workbook>')
                archive.writestr("xl/worksheets/sheet1.xml", '<worksheet><c t="inlineStr"><is><t>'
                                 + text + '</t></is></c><c><f>SUM($A$1:$A$8)</f></c></worksheet>')
            if "USD" in text:
                with self.assertRaisesRegex(ValueError, "Money amount found.*extra.xlsx"):
                    upload_pack.make(self.folder, self.out)
            else:
                output = upload_pack.make(self.folder, self.out)
                with zipfile.ZipFile(output / "product.zip") as archive:
                    self.assertIn("extra.xlsx", archive.namelist())

    def test_workbook_cached_string_is_checked(self):
        path = self.folder / "extra.xlsx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/workbook.xml", '<workbook><sheets/></workbook>')
            archive.writestr("xl/worksheets/sheet1.xml", '<worksheet><c t="str"><v>USD 8</v></c></worksheet>')
        with self.assertRaisesRegex(ValueError, "Money amount found"):
            upload_pack.make(self.folder, self.out)

    def test_listing_image_text_is_checked(self):
        with mock.patch.object(upload_pack.truth_check, "image_texts", return_value=["Buy for $8"]):
            with self.assertRaisesRegex(ValueError, "Money amount found.*listing images"):
                upload_pack.make(self.folder, self.out)

    def test_optional_cover_missing_and_first_listing_image_fallback(self):
        (self.folder / "cover.png").unlink()
        output = upload_pack.make(self.folder, self.out)
        self.assertFalse((output / "cover.png").exists())
        self.assertIn("no cover", (output / "checklist.txt").read_text())
        (self.folder / "preview-1.png").write_bytes(PNG)
        other = upload_pack.make(self.folder, self.root / "other-packs")
        self.assertEqual((other / "cover.png").read_bytes(), PNG)

    def test_missing_listing_fields_do_not_get_invented(self):
        for section in ("Description", "Tags (3)", "What you get"):
            with self.subTest(section=section):
                body = LISTING.replace("## " + section, "## Omitted")
                self.listing.write_text(body)
                with mock.patch.object(upload_pack.truth_check, "check", return_value={"verdict": "ready"}):
                    with self.assertRaisesRegex(ValueError, "listing.md is missing"):
                        upload_pack.make(self.folder, self.out)
                self.assertFalse(self.out.exists())

    def test_existing_launch_listing_included_files_heading_is_supported(self):
        listing = upload_pack._listing(LISTING.replace("## What you get", "## Included Files"))
        self.assertIn("What you get:\nA4 PDF\nUS Letter PDF", listing)

    def test_listing_cleaning_in_make(self):
        listing_content = """# Focus Planner Minimal Daily Goal System Routine

## Description
Two printable pages: a Brain Dump page and a Daily Focus page.
Print on A4 or US Letter. Use a pen.
Works with Google Sheets, Numbers, LibreOffice or your preferred spreadsheet program.
Also works on any spreadsheet app.

## Tags (5)
- focus
- daily routine
- a tag that is way longer than twenty chars
- bad@tag
- FOCUS

## What you get
- A4 PDF
- US Letter PDF
- README and personal use licence
"""
        self.listing.write_text(listing_content)
        out = upload_pack.make(self.folder, self.root / "clean-pack")
        listing_txt = (out / "listing.txt").read_text(encoding="utf-8")

        # Check description replacements and personal use notice
        self.assertNotIn("Google Sheets", listing_txt)
        self.assertNotIn("Numbers", listing_txt)
        self.assertNotIn("LibreOffice", listing_txt)
        self.assertNotIn("your preferred spreadsheet program", listing_txt)
        self.assertNotIn("any spreadsheet app", listing_txt)
        self.assertIn("an Excel (.xlsx) file", listing_txt)
        self.assertIn("For personal use only. Do not resell or share the files.", listing_txt)

        # Check tags
        tags_line = [l for l in listing_txt.splitlines() if l.startswith("Tags:")][0]
        tags = [t.strip() for t in tags_line[len("Tags:"):].split(",")]
        self.assertLessEqual(len(tags), 13)
        self.assertIn("focus", tags)
        self.assertIn("daily routine", tags)
        self.assertNotIn("a tag that is way longer than twenty chars", tags)
        self.assertNotIn("bad@tag", tags)
        self.assertEqual(len([t for t in tags if t.casefold() == "focus"]), 1)
        for t in tags:
            self.assertLessEqual(len(t), 20)
            self.assertTrue(all(c.isalnum() or c.isspace() for c in t))

        # Check listing.md in product folder was not changed
        self.assertEqual(self.listing.read_text(), listing_content)

    def test_state_files_are_never_opened_by_exporter(self):
        for name in ("private.enc", "private.db", "private.json", "private.sqlite-wal", "private.db-shm"):
            (self.folder / name).write_text("$999")
        real_scan = upload_pack._scan
        with mock.patch.object(upload_pack, "_scan", wraps=real_scan) as scan:
            upload_pack.make(self.folder, self.out)
        names = {call.args[0].name for call in scan.call_args_list}
        self.assertFalse(any(name.startswith("private") for name in names))

    def test_required_buyer_docs_cannot_be_silently_omitted(self):
        for name in ("README.md", "LICENSE.txt"):
            path = self.folder / name
            content = path.read_bytes()
            path.unlink()
            with self.assertRaisesRegex(ValueError, "requires"):
                upload_pack.make(self.folder, self.out)
            path.write_bytes(content)

    def test_symlinks_and_subdirectories_refused_without_following_them(self):
        link = self.folder / "private.pdf"
        link.symlink_to(self.root / "does-not-exist")
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            upload_pack.make(self.folder, self.out)
        link.unlink()
        (self.folder / "state").mkdir()
        with self.assertRaisesRegex(ValueError, "flat robot-built"):
            upload_pack.make(self.folder, self.out)

    def test_existing_pack_is_unchanged_on_repeat_or_refusal(self):
        output = upload_pack.make(self.folder, self.out)
        before = {p.name: p.read_bytes() for p in output.iterdir()}
        self.listing.write_text(LISTING + "\n$8")
        with self.assertRaisesRegex(ValueError, "already exists"):
            upload_pack.make(self.folder, self.out)
        self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_output_cannot_modify_source_folder(self):
        for out in (self.folder, self.folder.parent):
            with self.subTest(out=out):
                with self.assertRaisesRegex(ValueError, "overlap"):
                    upload_pack.make(self.folder, out)

    def test_failed_write_does_not_leave_a_partial_pack(self):
        with mock.patch.object(upload_pack.shutil, "copyfile", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                upload_pack.make(self.folder, self.out)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_cli_success_and_clear_failure(self):
        script = Path(upload_pack.__file__)
        success = subprocess.run([sys.executable, "-B", str(script), str(self.folder), str(self.out)],
                                 capture_output=True, text=True)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertTrue(success.stdout.startswith("nothing was uploaded\n"))
        failure = subprocess.run([sys.executable, "-B", str(script), str(self.folder), str(self.out)],
                                 capture_output=True, text=True)
        self.assertEqual(failure.returncode, 1)
        self.assertIn("Upload pack refused: Output pack already exists", failure.stderr)
        self.assertNotIn("Traceback", failure.stderr)


if __name__ == "__main__":
    unittest.main()
