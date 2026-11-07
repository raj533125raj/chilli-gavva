"""tests/test_ki_qa_fix.py — Comprehensive unit tests for product_qa_fix.py

Tests at least 30 scenarios:
  1. Fix small cover image (< 720px short side) upscaled to 1280x720 with LANCZOS padding edge color.
  2. Fix small preview image (< 720px short side) upscaled to 1280x720.
  3. Already large cover image (>= 720px) is not modified.
  4. Fix pin image wrong size (e.g. 800x1200) resized and padded into 1000x1500.
  5. Pin image already 1000x1500 is not modified.
  6. Edge color padding matches border color of the image.
  7. RGBA images preserve alpha during resize / padding.
  8. Fix ZIP containing __MACOSX metadata directory.
  9. Fix ZIP containing .DS_Store file.
  10. Fix ZIP containing absolute paths (/foo/bar).
  11. Fix ZIP containing windows absolute paths (C:\\foo\\bar).
  12. Normal clean ZIP archive is left untouched.
  13. Fix placeholder text in .txt file (e.g. "lorem ipsum" removed).
  14. Fix placeholder text in .md file (e.g. "TODO" removed).
  15. Fix placeholder text: "None", "null", "undefined", "[object" in text files.
  16. Placeholder text in PDF file is marked not_fixed (never edited).
  17. Placeholder text in XLSX file is marked not_fixed (never edited).
  18. Placeholder text in DOCX file is marked not_fixed (never edited).
  19. Text file without placeholders is not modified.
  20. Fix empty sheet named Sheet1 in XLSX removed when other sheets exist.
  21. Fix empty sheet named Sheet2 in XLSX removed when other sheets exist.
  22. Empty sheet not named Sheet1/Sheet2 (e.g. "Notes") is marked not_fixed.
  23. Single-sheet workbook where only sheet is Sheet1 is not removed (workbook needs sheets).
  24. Fix missing frozen header on data sheet in XLSX (freezes row 1 / A2).
  25. Already frozen data sheet in XLSX is not modified.
  26. Instruction / guide sheet without frozen header is not modified.
  27. Unknown problem kind is returned in not_fixed list.
  28. Missing target file in problem record is returned in not_fixed list.
  29. Atomic replace: target file exists and is intact even during write; valid replacement.
  30. Backup file is strictly created outside the pack folder.
  31. Deep QA check produces "fix", fix_folder runs, second check produces "ready".
  32. Launch pack evaluate_pack_quality triggers auto-repair hook on "fix" verdict.
  33. Launch pack hook handles fix_folder crash gracefully without breaking build.
  34. fix_folder handles nonexistent folder safely.
  35. Corrupt / invalid zip handling returns not_fixed gracefully.
"""

import io
import os
import shutil
import tempfile
import unittest
from unittest import mock
import zipfile

from PIL import Image
import openpyxl

import launch_pack
import product_qa_deep
import product_qa_fix


class TestProductQaFix(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* and RUNNER_ENVIRONMENT environment variables
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.test_dir = tempfile.mkdtemp(prefix="qa_fix_test_")
        self.pack_dir = os.path.join(self.test_dir, "pack")
        os.makedirs(self.pack_dir, exist_ok=True)
        self.backup_dir = os.path.join(self.test_dir, "backups")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # 1. Fix small cover image
    def test_01_fix_small_cover_image(self):
        img_path = os.path.join(self.pack_dir, "cover.png")
        img = Image.new("RGB", (600, 400), color=(10, 20, 30))
        img.save(img_path)

        probs = [{"file": "cover.png", "page": 0, "kind": "image_resolution_too_low", "detail": "short side < 720"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        self.assertEqual(len(res["not_fixed"]), 0)
        with Image.open(img_path) as fixed_img:
            self.assertEqual(fixed_img.size, (1280, 720))

    # 2. Fix small preview image
    def test_02_fix_small_preview_image(self):
        img_path = os.path.join(self.pack_dir, "preview-1.png")
        img = Image.new("RGB", (500, 500), color=(40, 50, 60))
        img.save(img_path)

        probs = [{"file": "preview-1.png", "page": 0, "kind": "image_resolution_too_low", "detail": "short side < 720"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        with Image.open(img_path) as fixed_img:
            self.assertEqual(fixed_img.size, (1280, 720))

    # 3. Already large cover image
    def test_03_already_large_cover_image(self):
        img_path = os.path.join(self.pack_dir, "cover.png")
        img = Image.new("RGB", (1280, 720), color=(10, 20, 30))
        img.save(img_path)

        probs = [{"file": "cover.png", "page": 0, "kind": "image_resolution_too_low", "detail": "short side"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["not_fixed"]), 1)
        with Image.open(img_path) as checked_img:
            self.assertEqual(checked_img.size, (1280, 720))

    # 4. Fix pin image wrong size
    def test_04_fix_pin_wrong_size(self):
        img_path = os.path.join(self.pack_dir, "pin-1.png")
        img = Image.new("RGB", (800, 1200), color=(70, 80, 90))
        img.save(img_path)

        probs = [{"file": "pin-1.png", "page": 0, "kind": "invalid_pin_dimensions", "detail": "wrong size"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        with Image.open(img_path) as fixed_img:
            self.assertEqual(fixed_img.size, (1000, 1500))

    # 5. Pin image already 1000x1500
    def test_05_pin_already_correct_size(self):
        img_path = os.path.join(self.pack_dir, "pin-1.png")
        img = Image.new("RGB", (1000, 1500), color=(70, 80, 90))
        img.save(img_path)

        probs = [{"file": "pin-1.png", "page": 0, "kind": "invalid_pin_dimensions", "detail": "wrong size"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["not_fixed"]), 1)

    # 6. Edge color padding matches border
    def test_06_edge_color_padding(self):
        img_path = os.path.join(self.pack_dir, "cover.png")
        edge_color = (25, 45, 65)
        img = Image.new("RGB", (400, 400), color=edge_color)
        img.save(img_path)

        probs = [{"file": "cover.png", "page": 0, "kind": "image_resolution_too_low", "detail": "short side < 720"}]
        product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        with Image.open(img_path) as fixed_img:
            self.assertEqual(fixed_img.size, (1280, 720))
            # Corner pixel in padding should match edge color
            self.assertEqual(fixed_img.getpixel((0, 0)), edge_color)

    # 7. RGBA image preserves mode
    def test_07_rgba_image_mode_preserved(self):
        img_path = os.path.join(self.pack_dir, "preview.png")
        img = Image.new("RGBA", (300, 200), color=(100, 150, 200, 255))
        img.save(img_path)

        probs = [{"file": "preview.png", "page": 0, "kind": "image_resolution_too_low", "detail": "small"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        with Image.open(img_path) as fixed_img:
            self.assertEqual(fixed_img.mode, "RGBA")
            self.assertEqual(fixed_img.size, (1280, 720))

    # 8. Fix ZIP containing __MACOSX metadata
    def test_08_fix_zip_macosx(self):
        zip_path = os.path.join(self.pack_dir, "product.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("good_file.txt", "content")
            z.writestr("__MACOSX/._good_file.txt", "meta")

        probs = [{"file": "product.zip", "page": 0, "kind": "zip_macosx_metadata", "detail": "has __MACOSX"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        with zipfile.ZipFile(zip_path, "r") as z:
            names = z.namelist()
            self.assertIn("good_file.txt", names)
            self.assertNotIn("__MACOSX/._good_file.txt", names)

    # 9. Fix ZIP containing .DS_Store
    def test_09_fix_zip_ds_store(self):
        zip_path = os.path.join(self.pack_dir, "product.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("sheet.csv", "1,2,3")
            z.writestr(".DS_Store", "junk")
            z.writestr("sub/.DS_Store", "sub junk")

        probs = [{"file": "product.zip", "page": 0, "kind": "zip_ds_store", "detail": "has .DS_Store"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        with zipfile.ZipFile(zip_path, "r") as z:
            names = z.namelist()
            self.assertEqual(names, ["sheet.csv"])

    # 10. Fix ZIP containing unix absolute path
    def test_10_fix_zip_unix_absolute_path(self):
        zip_path = os.path.join(self.pack_dir, "product.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("/home/user/document.txt", "hello")

        probs = [{"file": "product.zip", "page": 0, "kind": "zip_absolute_path", "detail": "has absolute"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        with zipfile.ZipFile(zip_path, "r") as z:
            names = z.namelist()
            self.assertEqual(names, ["home/user/document.txt"])

    # 11. Fix ZIP containing windows absolute path
    def test_11_fix_zip_windows_absolute_path(self):
        zip_path = os.path.join(self.pack_dir, "product.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("C:/Users/user/doc.txt", "hello")

        probs = [{"file": "product.zip", "page": 0, "kind": "zip_absolute_path", "detail": "has windows absolute"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        with zipfile.ZipFile(zip_path, "r") as z:
            names = z.namelist()
            self.assertEqual(names, ["Users/user/doc.txt"])

    # 12. Clean ZIP is not modified
    def test_12_clean_zip_not_modified(self):
        zip_path = os.path.join(self.pack_dir, "product.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("clean.txt", "clean")

        probs = [{"file": "product.zip", "page": 0, "kind": "zip_macosx_metadata", "detail": "none"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["not_fixed"]), 1)

    # 13. Fix placeholder text in .txt
    def test_13_fix_placeholder_in_txt(self):
        txt_path = os.path.join(self.pack_dir, "notes.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("Important text with Lorem Ipsum dolor sit amet and real facts.")

        probs = [{"file": "notes.txt", "page": 0, "kind": "placeholder_text", "detail": "contains lorem ipsum"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        with open(txt_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("Lorem Ipsum", content)
        self.assertIn("Important text", content)

    # 14. Fix placeholder text in .md
    def test_14_fix_placeholder_in_md(self):
        md_path = os.path.join(self.pack_dir, "listing.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Title\n\nTODO fill in features here.\nReal description.")

        probs = [{"file": "listing.md", "page": 0, "kind": "placeholder_text", "detail": "contains TODO"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("TODO", content)
        self.assertIn("Real description", content)

    # 15. Fix placeholder tokens: None, null, undefined, [object
    def test_15_fix_placeholder_tokens(self):
        txt_path = os.path.join(self.pack_dir, "info.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("Status: None, value: null, err: undefined, ref: [object Object]")

        probs = [{"file": "info.txt", "page": 0, "kind": "placeholder_text", "detail": "contains tokens"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        with open(txt_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("None", content)
        self.assertNotIn("null", content)
        self.assertNotIn("undefined", content)
        self.assertNotIn("[object", content)

    # 16. Placeholder text in PDF marked not_fixed
    def test_16_placeholder_in_pdf_not_fixed(self):
        pdf_path = os.path.join(self.pack_dir, "guide.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4 mock pdf with TODO inside")

        probs = [{"file": "guide.pdf", "page": 1, "kind": "placeholder_text", "detail": "TODO in PDF"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["not_fixed"]), 1)
        self.assertEqual(len(res["fixed"]), 0)

    # 17. Placeholder text in XLSX marked not_fixed
    def test_17_placeholder_in_xlsx_not_fixed(self):
        xlsx_path = os.path.join(self.pack_dir, "sheet.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Data"
        ws.append(["Header", "TODO"])
        wb.save(xlsx_path)

        probs = [{"file": "sheet.xlsx", "page": "Data", "kind": "placeholder_text", "detail": "TODO in XLSX"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["not_fixed"]), 1)

    # 18. Placeholder text in DOCX marked not_fixed
    def test_18_placeholder_in_docx_not_fixed(self):
        docx_path = os.path.join(self.pack_dir, "doc.docx")
        with open(docx_path, "wb") as f:
            f.write(b"mock docx")

        probs = [{"file": "doc.docx", "page": 1, "kind": "placeholder_text", "detail": "lorem ipsum in DOCX"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["not_fixed"]), 1)

    # 19. Text file without placeholders
    def test_19_text_file_without_placeholders(self):
        txt_path = os.path.join(self.pack_dir, "clean.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("Fully clean text content.")

        probs = [{"file": "clean.txt", "page": 0, "kind": "placeholder_text", "detail": "none"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["not_fixed"]), 1)

    # 20. Fix empty sheet named Sheet1
    def test_20_fix_empty_sheet1(self):
        xlsx_path = os.path.join(self.pack_dir, "budget.xlsx")
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Budget"
        ws1.append(["Category", "Amount"])
        ws1.append(["Utilities", 100])
        wb.create_sheet("Sheet1")
        wb.save(xlsx_path)

        probs = [{"file": "budget.xlsx", "page": "Sheet1", "kind": "empty_sheet", "detail": "Sheet1 is empty"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        check_wb = openpyxl.load_workbook(xlsx_path)
        self.assertNotIn("Sheet1", check_wb.sheetnames)
        self.assertIn("Budget", check_wb.sheetnames)

    # 21. Fix empty sheet named Sheet2
    def test_21_fix_empty_sheet2(self):
        xlsx_path = os.path.join(self.pack_dir, "budget.xlsx")
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Budget"
        ws1.append(["Category", "Amount"])
        wb.create_sheet("Sheet2")
        wb.save(xlsx_path)

        probs = [{"file": "budget.xlsx", "page": "Sheet2", "kind": "empty_sheet", "detail": "Sheet2 is empty"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        check_wb = openpyxl.load_workbook(xlsx_path)
        self.assertNotIn("Sheet2", check_wb.sheetnames)

    # 22. Empty sheet not named Sheet1/Sheet2 is not removed
    def test_22_empty_sheet_not_generic_name(self):
        xlsx_path = os.path.join(self.pack_dir, "budget.xlsx")
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Budget"
        ws1.append(["Category", "Amount"])
        wb.create_sheet("UserCustomNotes")
        wb.save(xlsx_path)

        probs = [{"file": "budget.xlsx", "page": "UserCustomNotes", "kind": "empty_sheet", "detail": "is empty"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["not_fixed"]), 1)
        check_wb = openpyxl.load_workbook(xlsx_path)
        self.assertIn("UserCustomNotes", check_wb.sheetnames)

    # 23. Only sheet is Sheet1 - cannot remove the only sheet
    def test_23_single_sheet_sheet1_cannot_remove(self):
        xlsx_path = os.path.join(self.pack_dir, "budget.xlsx")
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Sheet1"
        wb.save(xlsx_path)

        probs = [{"file": "budget.xlsx", "page": "Sheet1", "kind": "empty_sheet", "detail": "Sheet1 is empty"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["not_fixed"]), 1)
        check_wb = openpyxl.load_workbook(xlsx_path)
        self.assertEqual(check_wb.sheetnames, ["Sheet1"])

    # 24. Fix missing frozen header on data sheet
    def test_24_fix_missing_frozen_header(self):
        xlsx_path = os.path.join(self.pack_dir, "data.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Tracker"
        ws.append(["Date", "Item", "Value"])
        ws.append(["2026-01-01", "Coffee", 5])
        wb.save(xlsx_path)

        probs = [{"file": "data.xlsx", "page": "Tracker", "kind": "missing_frozen_header", "detail": "missing freeze"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        check_wb = openpyxl.load_workbook(xlsx_path)
        self.assertEqual(check_wb["Tracker"].freeze_panes, "A2")

    # 25. Already frozen data sheet
    def test_25_already_frozen_sheet(self):
        xlsx_path = os.path.join(self.pack_dir, "data.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Tracker"
        ws.append(["Date", "Item"])
        ws.append(["2026-01-01", "Coffee"])
        ws.freeze_panes = "A2"
        wb.save(xlsx_path)

        probs = [{"file": "data.xlsx", "page": "Tracker", "kind": "missing_frozen_header", "detail": "missing freeze"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["not_fixed"]), 1)

    # 26. Instruction sheet not modified
    def test_26_instruction_sheet_no_freeze_needed(self):
        xlsx_path = os.path.join(self.pack_dir, "data.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Instructions"
        ws.append(["Step 1", "Read"])
        wb.save(xlsx_path)

        probs = [{"file": "data.xlsx", "page": "Instructions", "kind": "missing_frozen_header", "detail": "no freeze"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        # Instructions sheet is not a data sheet
        check_wb = openpyxl.load_workbook(xlsx_path)
        self.assertFalse(check_wb["Instructions"].freeze_panes)

    # 27. Unknown problem kind
    def test_27_unknown_problem_kind(self):
        txt_path = os.path.join(self.pack_dir, "file.txt")
        with open(txt_path, "w") as f:
            f.write("hello")
        probs = [{"file": "file.txt", "page": 0, "kind": "completely_unknown_issue", "detail": "err"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)
        self.assertEqual(len(res["not_fixed"]), 1)
        self.assertEqual(res["not_fixed"][0]["kind"], "completely_unknown_issue")

    # 28. Missing target file
    def test_28_missing_target_file(self):
        probs = [{"file": "nonexistent.png", "page": 0, "kind": "image_resolution_too_low", "detail": "missing"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)
        self.assertEqual(len(res["not_fixed"]), 1)

    # 29. Atomic replace preserves file
    def test_29_atomic_replace(self):
        txt_path = os.path.join(self.pack_dir, "test.txt")
        with open(txt_path, "w") as f:
            f.write("Line 1\nTODO\nLine 2")

        probs = [{"file": "test.txt", "page": 0, "kind": "placeholder_text", "detail": "TODO"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 1)
        self.assertTrue(os.path.exists(txt_path))
        with open(txt_path, "r") as f:
            lines = f.read().splitlines()
        self.assertIn("Line 1", lines)
        self.assertIn("Line 2", lines)
        self.assertNotIn("TODO", lines)

    # 30. Backups are strictly outside the pack folder
    def test_30_backups_outside_pack(self):
        img_path = os.path.join(self.pack_dir, "cover.png")
        img = Image.new("RGB", (400, 300), color=(12, 34, 56))
        img.save(img_path)

        probs = [{"file": "cover.png", "page": 0, "kind": "image_resolution_too_low", "detail": "small"}]
        product_qa_fix.fix_folder(self.pack_dir, probs)

        # Check no hidden backup dirs created INSIDE self.pack_dir
        for root, dirs, files in os.walk(self.pack_dir):
            for d in dirs:
                self.assertFalse(d.startswith(".backups"), f"Backup dir found inside pack: {d}")

    # 31. Deep QA check produces "fix", fix_folder runs, second check produces "ready"
    def test_31_full_flow_fix_then_ready(self):
        # Create minimal valid pack files
        # 1. Valid cover image that is too small
        img_path = os.path.join(self.pack_dir, "cover.png")
        img = Image.new("RGB", (640, 480), color=(100, 150, 200))
        img.putpixel((10, 10), (120, 160, 210))  # Ensure not single solid color
        img.save(img_path)

        # Initial deep QA check
        initial_check = product_qa_deep.check_folder(self.pack_dir)
        self.assertEqual(initial_check["verdict"], "fix")
        self.assertTrue(any(p["kind"] == "image_resolution_too_low" for p in initial_check["problems"]))

        # Run fix_folder
        fix_res = product_qa_fix.fix_folder(self.pack_dir, initial_check["problems"], backup_dir=self.backup_dir)
        self.assertTrue(len(fix_res["fixed"]) > 0)

        # Second check
        second_check = product_qa_deep.check_folder(self.pack_dir)
        self.assertEqual(second_check["verdict"], "ready")

    # 32. Launch pack evaluate_pack_quality triggers auto-repair hook
    def test_32_launch_pack_evaluate_pack_quality_hook(self):
        img_path = os.path.join(self.pack_dir, "cover.png")
        img = Image.new("RGB", (640, 480), color=(100, 150, 200))
        img.putpixel((10, 10), (120, 160, 210))
        img.save(img_path)

        # Add required launch pack files
        with open(os.path.join(self.pack_dir, "LICENSE.txt"), "w") as f:
            f.write("License")
        with open(os.path.join(self.pack_dir, "CHECKLIST.md"), "w") as f:
            f.write("Checklist")
        with open(os.path.join(self.pack_dir, "FILE_LIST.txt"), "w") as f:
            f.write("Files")

        spec = {
            "listing": {
                "title": "A Very Detailed Product Listing Title",
                "description": "This is a detailed description of the product that is definitely long enough to pass validation easily."
            }
        }

        # Calling evaluate_pack_quality should run deep QA -> fix -> re-check
        score, reasons = launch_pack.evaluate_pack_quality(self.pack_dir, spec)
        # Cover was upscaled and now passes deep QA
        self.assertFalse(any("Deep QA issue: image_resolution_too_low" in r for r in reasons))

    # 33. Launch pack hook handles fix_folder crash gracefully
    def test_33_launch_pack_hook_crash_safety(self):
        img_path = os.path.join(self.pack_dir, "cover.png")
        img = Image.new("RGB", (640, 480), color=(100, 150, 200))
        img.save(img_path)

        spec = {
            "listing": {
                "title": "A Very Detailed Product Listing Title",
                "description": "This is a detailed description of the product that is definitely long enough to pass validation easily."
            }
        }

        with mock.patch("product_qa_fix.fix_folder", side_effect=RuntimeError("Simulated fix crash")):
            score, reasons = launch_pack.evaluate_pack_quality(self.pack_dir, spec)
            # Should not raise exception
            self.assertTrue(any("Deep QA issue" in r for r in reasons))

    # 34. Non-existent folder handled safely
    def test_34_nonexistent_folder_safety(self):
        res = product_qa_fix.fix_folder("/nonexistent/directory/path/123", [{"file": "foo.png", "kind": "any"}])
        self.assertEqual(len(res["fixed"]), 0)
        self.assertEqual(len(res["not_fixed"]), 1)

    # 35. Corrupt zip handling
    def test_35_corrupt_zip_safety(self):
        zip_path = os.path.join(self.pack_dir, "corrupt.zip")
        with open(zip_path, "wb") as f:
            f.write(b"not a valid zip file")

        probs = [{"file": "corrupt.zip", "page": 0, "kind": "zip_macosx_metadata", "detail": "check"}]
        res = product_qa_fix.fix_folder(self.pack_dir, probs, backup_dir=self.backup_dir)

        self.assertEqual(len(res["fixed"]), 0)
        self.assertEqual(len(res["not_fixed"]), 1)


if __name__ == "__main__":
    unittest.main()
