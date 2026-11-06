"""tests/test_kf_deep_qa.py — Unit tests for product_qa_deep.py and launch_pack integration.

Tests at least 30 deep quality check scenarios:
  1. PDF: blank page PDF
  2. PDF: mixed page sizes
  3. PDF: placeholder text (lorem ipsum, TODO, {{, None, null, undefined, [object)
  4. PDF: 0 pages empty PDF
  5. PDF: planner without US Letter
  6. PDF: planner without A4
  7. PDF: planner with both A4 and US Letter
  8. PDF: text cut at page edge (< 5mm margin)
  9. PDF: page numbers out of order
  10. PDF: un-embedded non-standard font
  11. XLSX: cell with #REF!
  12. XLSX: cell with #VALUE!
  13. XLSX: cell with #DIV/0!
  14. XLSX: cell with #NAME?
  15. XLSX: missing formula when sheet says "auto"
  16. XLSX: missing formula when sheet says "total"
  17. XLSX: formulas present with auto/total
  18. XLSX: un-frozen data sheet
  19. XLSX: frozen data sheet
  20. XLSX: empty sheet
  21. XLSX: zero column width
  22. DOCX: empty document
  23. DOCX: placeholder text
  24. DOCX: good document
  25. PNG: cover < 720px short side
  26. PNG: preview < 720px short side
  27. PNG: pin bad aspect ratio / dimensions
  28. PNG: pin good dimensions (1000x1500)
  29. PNG: solid one color
  30. ZIP: __MACOSX entry
  31. ZIP: .DS_Store entry
  32. ZIP: absolute path entry
  33. ZIP: total uncompressed size >= 250 MB
  34. ZIP: empty zip
  35. Full good folder -> ready verdict, report outside the pack
  36. Missing library fallback -> skipped/fallback without crash
  37. launch_pack integration lowers score by 25 on fix verdict
  38. launch_pack integration handles QA crash gracefully
"""

import os
import shutil
import struct
import tempfile
import unittest
import zipfile
import zlib
from unittest import mock

import launch_pack
import product_builder as pb
import product_qa_deep


def make_minimal_png(w: int, h: int, color=(100, 150, 200, 255), multi_color=True) -> bytes:
    """Build a minimal valid RGBA PNG in pure Python."""
    header = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)

    raw_rows = bytearray()
    for y in range(h):
        raw_rows.append(0)  # Filter byte: None
        for x in range(w):
            if multi_color and (x + y) % 2 == 0:
                raw_rows.extend([color[0], color[1], color[2], color[3]])
            elif multi_color:
                raw_rows.extend([color[0] ^ 50, color[1], color[2], color[3]])
            else:
                raw_rows.extend([color[0], color[1], color[2], color[3]])

    compressed = zlib.compress(bytes(raw_rows))
    idat_crc = zlib.crc32(b"IDAT" + compressed)
    idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc)

    iend_crc = zlib.crc32(b"IEND")
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)

    return header + ihdr + idat + iend


def make_raw_pdf(pages: list, mediabox=(0, 0, 595.28, 841.89), font_base="Helvetica") -> bytes:
    """Create a raw PDF with given pages of stream contents and optional custom font."""
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        None,  # Pages object
        f"<< /Type /Font /Subtype /Type1 /BaseFont /{font_base} /Encoding /WinAnsiEncoding >>",
        f"<< /Type /Font /Subtype /Type1 /BaseFont /{font_base}-Bold /Encoding /WinAnsiEncoding >>"
    ]
    kids = []
    mb_str = f"[{mediabox[0]} {mediabox[1]} {mediabox[2]} {mediabox[3]}]"
    for ops in pages:
        stream = ops.encode("latin-1")
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_id = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox {mb_str} "
                    f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>")
        kids.append(f"{len(objs)} 0 R")

    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    objs.append("<< /Producer (Deep QA Test) >>")

    out, offsets = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"), []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + (obj if isinstance(obj, bytes) else obj.encode("latin-1")) + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {len(objs)} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


class TestKfDeepQA(unittest.TestCase):

    def setUp(self):
        # Clear all GITHUB_* and RUNNER_ENVIRONMENT variables using mock.patch.dict
        cleaned_env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        self.env_patcher = mock.patch.dict(os.environ, cleaned_env, clear=True)
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # 1. PDF: blank page PDF
    def test_pdf_blank_page(self):
        pdf_bytes = make_raw_pdf([""])  # 1 page, empty stream
        pdf_path = os.path.join(self.tmp_dir, "test.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertEqual(res["verdict"], "fix")
        self.assertTrue(any(p["kind"] == "blank_page" for p in res["problems"]))

    # 2. PDF: mixed page sizes
    def test_pdf_mixed_page_sizes(self):
        objs = [
            "<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.28 841.89] /Contents 4 0 R >>",
            b"<< /Length 30 >>\nstream\nBT /F1 12 Tf 50 500 Td (A4) Tj ET\nendstream",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612.0 792.0] /Contents 6 0 R >>",
            b"<< /Length 34 >>\nstream\nBT /F1 12 Tf 50 500 Td (Letter) Tj ET\nendstream"
        ]
        out, offsets = bytearray(b"%PDF-1.4\n"), []
        for i, obj in enumerate(objs, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + (obj if isinstance(obj, bytes) else obj.encode("latin-1")) + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
        out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()

        pdf_path = os.path.join(self.tmp_dir, "mixed.pdf")
        with open(pdf_path, "wb") as f:
            f.write(out)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "mixed_page_sizes" for p in res["problems"]))

    # 3. PDF: placeholder text
    def test_pdf_placeholder_text_lorem(self):
        stream = "BT /F1 12 Tf 50 500 Td (Lorem ipsum dolor sit amet) Tj ET"
        pdf_path = os.path.join(self.tmp_dir, "lorem.pdf")
        with open(pdf_path, "wb") as f:
            f.write(make_raw_pdf([stream]))

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "placeholder_text" for p in res["problems"]))

    def test_pdf_placeholder_text_todo(self):
        stream = "BT /F1 12 Tf 50 500 Td (This is a TODO item) Tj ET"
        pdf_path = os.path.join(self.tmp_dir, "todo.pdf")
        with open(pdf_path, "wb") as f:
            f.write(make_raw_pdf([stream]))

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "placeholder_text" for p in res["problems"]))

    def test_pdf_placeholder_text_curly(self):
        stream = "BT /F1 12 Tf 50 500 Td (Hello {{ name }}) Tj ET"
        pdf_path = os.path.join(self.tmp_dir, "curly.pdf")
        with open(pdf_path, "wb") as f:
            f.write(make_raw_pdf([stream]))

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "placeholder_text" for p in res["problems"]))

    def test_pdf_placeholder_text_none_null_undefined(self):
        stream = "BT /F1 12 Tf 50 500 Td (Value is None and null and undefined [object Object]) Tj ET"
        pdf_path = os.path.join(self.tmp_dir, "badtokens.pdf")
        with open(pdf_path, "wb") as f:
            f.write(make_raw_pdf([stream]))

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "placeholder_text" for p in res["problems"]))

    # 4. PDF: 0 pages empty PDF
    def test_pdf_zero_pages(self):
        objs = [
            "<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [] /Count 0 >>"
        ]
        out, offsets = bytearray(b"%PDF-1.4\n"), []
        for i, obj in enumerate(objs, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + obj.encode("latin-1") + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
        out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()

        pdf_path = os.path.join(self.tmp_dir, "zero.pdf")
        with open(pdf_path, "wb") as f:
            f.write(out)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "empty_pdf" for p in res["problems"]))

    # 5. PDF: planner without US Letter
    def test_planner_without_letter(self):
        stream = "BT /F1 12 Tf 50 500 Td (A4 Planner Daily) Tj ET"
        pdf_path = os.path.join(self.tmp_dir, "my-planner-a4.pdf")
        with open(pdf_path, "wb") as f:
            f.write(make_raw_pdf([stream], mediabox=(0, 0, 595.28, 841.89)))

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "missing_pdf_size" for p in res["problems"]))

    # 6. PDF: planner without A4
    def test_planner_without_a4(self):
        stream = "BT /F1 12 Tf 50 500 Td (Letter Planner Daily) Tj ET"
        pdf_path = os.path.join(self.tmp_dir, "my-planner-letter.pdf")
        with open(pdf_path, "wb") as f:
            f.write(make_raw_pdf([stream], mediabox=(0, 0, 612.0, 792.0)))

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "missing_pdf_size" for p in res["problems"]))

    # 7. PDF: planner with both A4 and US Letter
    def test_planner_with_both_sizes(self):
        stream_a4 = "BT /F1 12 Tf 50 500 Td (A4 Planner) Tj ET"
        stream_let = "BT /F1 12 Tf 50 500 Td (Letter Planner) Tj ET"
        with open(os.path.join(self.tmp_dir, "daily-planner-a4.pdf"), "wb") as f:
            f.write(make_raw_pdf([stream_a4], mediabox=(0, 0, 595.28, 841.89)))
        with open(os.path.join(self.tmp_dir, "daily-planner-letter.pdf"), "wb") as f:
            f.write(make_raw_pdf([stream_let], mediabox=(0, 0, 612.0, 792.0)))

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertFalse(any(p["kind"] == "missing_pdf_size" for p in res["problems"]))

    # 8. PDF: text cut at page edge (< 5mm margin)
    def test_pdf_text_outside_safe_margin(self):
        # 5mm is approx 14.17 pt. Placing text at x=5 is outside margin.
        stream = "BT /F1 12 Tf 5.0 500.0 Td (Near margin) Tj ET"
        pdf_path = os.path.join(self.tmp_dir, "margin.pdf")
        with open(pdf_path, "wb") as f:
            f.write(make_raw_pdf([stream]))

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "text_outside_safe_margin" for p in res["problems"]))

    # 9. PDF: page numbers out of order
    def test_pdf_page_numbers_out_of_order(self):
        s1 = "BT /F1 12 Tf 50 500 Td (Page 2 of 2) Tj ET"
        s2 = "BT /F1 12 Tf 50 500 Td (Page 1 of 2) Tj ET"
        pdf_path = os.path.join(self.tmp_dir, "pages.pdf")
        with open(pdf_path, "wb") as f:
            f.write(make_raw_pdf([s1, s2]))

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "page_number_out_of_order" for p in res["problems"]))

    # 10. PDF: non-standard font without FontDescriptor
    def test_pdf_font_not_embedded(self):
        stream = "BT /F1 12 Tf 50 500 Td (Custom Font) Tj ET"
        pdf_path = os.path.join(self.tmp_dir, "font.pdf")
        with open(pdf_path, "wb") as f:
            f.write(make_raw_pdf([stream], font_base="ComicSansMS"))

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "font_not_embedded" for p in res["problems"]))

    # 11. XLSX: cell with #REF!
    def test_xlsx_cell_ref_error(self):
        wb = pb.Workbook()
        s = wb.add_sheet("Sheet1")
        pb.Workbook.put(s, 1, 1, "Header")
        pb.Workbook.put(s, 2, 1, "#REF!")
        xlsx_path = os.path.join(self.tmp_dir, "ref_error.xlsx")
        wb.save(xlsx_path)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "cell_error" and "#REF!" in p["detail"] for p in res["problems"]))

    # 12. XLSX: cell with #VALUE!
    def test_xlsx_cell_value_error(self):
        wb = pb.Workbook()
        s = wb.add_sheet("Sheet1")
        pb.Workbook.put(s, 1, 1, "Header")
        pb.Workbook.put(s, 2, 1, "#VALUE!")
        xlsx_path = os.path.join(self.tmp_dir, "val_error.xlsx")
        wb.save(xlsx_path)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "cell_error" and "#VALUE!" in p["detail"] for p in res["problems"]))

    # 13. XLSX: cell with #DIV/0!
    def test_xlsx_cell_div0_error(self):
        wb = pb.Workbook()
        s = wb.add_sheet("Sheet1")
        pb.Workbook.put(s, 1, 1, "Header")
        pb.Workbook.put(s, 2, 1, "#DIV/0!")
        xlsx_path = os.path.join(self.tmp_dir, "div0_error.xlsx")
        wb.save(xlsx_path)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "cell_error" and "#DIV/0!" in p["detail"] for p in res["problems"]))

    # 14. XLSX: cell with #NAME?
    def test_xlsx_cell_name_error(self):
        wb = pb.Workbook()
        s = wb.add_sheet("Sheet1")
        pb.Workbook.put(s, 1, 1, "Header")
        pb.Workbook.put(s, 2, 1, "#NAME?")
        xlsx_path = os.path.join(self.tmp_dir, "name_error.xlsx")
        wb.save(xlsx_path)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "cell_error" and "#NAME?" in p["detail"] for p in res["problems"]))

    # 15. XLSX: missing formula when sheet says "auto"
    def test_xlsx_missing_formula_auto(self):
        wb = pb.Workbook()
        s = wb.add_sheet("Auto Tracker")
        pb.Workbook.put(s, 1, 1, "Category")
        pb.Workbook.put(s, 2, 1, "Travel")
        xlsx_path = os.path.join(self.tmp_dir, "auto_no_formula.xlsx")
        wb.save(xlsx_path)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "missing_formula" for p in res["problems"]))

    # 16. XLSX: missing formula when sheet says "total"
    def test_xlsx_missing_formula_total(self):
        wb = pb.Workbook()
        s = wb.add_sheet("Summary")
        pb.Workbook.put(s, 1, 1, "Category")
        pb.Workbook.put(s, 2, 1, "Total Spent")
        xlsx_path = os.path.join(self.tmp_dir, "total_no_formula.xlsx")
        wb.save(xlsx_path)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "missing_formula" for p in res["problems"]))

    # 17. XLSX: formulas present with auto/total
    def test_xlsx_formula_present(self):
        wb = pb.Workbook()
        s = wb.add_sheet("Summary")
        pb.Workbook.put(s, 1, 1, "Total Spent")
        pb.Workbook.put(s, 2, 1, 100, formula="SUM(B2:B10)")
        xlsx_path = os.path.join(self.tmp_dir, "good_formula.xlsx")
        wb.save(xlsx_path)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertFalse(any(p["kind"] == "missing_formula" for p in res["problems"]))

    # 18. XLSX: un-frozen data sheet
    def test_xlsx_unfrozen_data_sheet(self):
        wb = pb.Workbook()
        s = wb.add_sheet("Data", freeze=False)
        pb.Workbook.put(s, 1, 1, "Header")
        pb.Workbook.put(s, 2, 1, "Row 1")
        xlsx_path = os.path.join(self.tmp_dir, "unfrozen.xlsx")
        wb.save(xlsx_path)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "missing_frozen_header" for p in res["problems"]))

    # 19. XLSX: frozen data sheet
    def test_xlsx_frozen_data_sheet(self):
        wb = pb.Workbook()
        s = wb.add_sheet("Data", freeze=True)
        pb.Workbook.put(s, 1, 1, "Header")
        pb.Workbook.put(s, 2, 1, "Row 1")
        xlsx_path = os.path.join(self.tmp_dir, "frozen.xlsx")
        wb.save(xlsx_path)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertFalse(any(p["kind"] == "missing_frozen_header" for p in res["problems"]))

    # 20. XLSX: empty sheet
    def test_xlsx_empty_sheet(self):
        wb = pb.Workbook()
        wb.add_sheet("BlankSheet")
        xlsx_path = os.path.join(self.tmp_dir, "empty_sheet.xlsx")
        wb.save(xlsx_path)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "empty_sheet" for p in res["problems"]))

    # 21. XLSX: zero column width
    def test_xlsx_zero_column_width(self):
        wb = pb.Workbook()
        s = wb.add_sheet("Sheet1", widths=[0, 15])
        pb.Workbook.put(s, 1, 1, "Header")
        xlsx_path = os.path.join(self.tmp_dir, "zero_width.xlsx")
        wb.save(xlsx_path)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "zero_column_width" for p in res["problems"]))

    # 22. DOCX: empty document
    def test_docx_empty(self):
        docx_path = os.path.join(self.tmp_dir, "empty.docx")
        pb.build_docx(docx_path, [])

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "empty_docx" for p in res["problems"]))

    # 23. DOCX: placeholder text
    def test_docx_placeholder_text(self):
        docx_path = os.path.join(self.tmp_dir, "placeholder.docx")
        pb.build_docx(docx_path, [("Normal", "Here is some lorem ipsum dolor text")])

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "placeholder_text" for p in res["problems"]))

    # 24. DOCX: good document
    def test_docx_good(self):
        docx_path = os.path.join(self.tmp_dir, "resume.docx")
        pb.build_docx(docx_path, [("Title", "Senior Software Engineer"), ("Normal", "Proven experience building systems.")])

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertFalse(any(p["kind"] in ("empty_docx", "placeholder_text") for p in res["problems"]))

    # 25. PNG: cover < 720px short side
    def test_png_cover_too_small(self):
        png_data = make_minimal_png(600, 600)
        with open(os.path.join(self.tmp_dir, "cover.png"), "wb") as f:
            f.write(png_data)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "image_resolution_too_low" for p in res["problems"]))

    # 26. PNG: preview < 720px short side
    def test_png_preview_too_small(self):
        png_data = make_minimal_png(650, 1200)
        with open(os.path.join(self.tmp_dir, "preview-1.png"), "wb") as f:
            f.write(png_data)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "image_resolution_too_low" for p in res["problems"]))

    # 27. PNG: pin bad dimensions
    def test_png_pin_bad_dimensions(self):
        png_data = make_minimal_png(800, 1200)
        with open(os.path.join(self.tmp_dir, "pin-1.png"), "wb") as f:
            f.write(png_data)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "invalid_pin_dimensions" for p in res["problems"]))

    # 28. PNG: pin good dimensions (1000x1500)
    def test_png_pin_good_dimensions(self):
        png_data = make_minimal_png(1000, 1500)
        with open(os.path.join(self.tmp_dir, "pin-1.png"), "wb") as f:
            f.write(png_data)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertFalse(any(p["kind"] == "invalid_pin_dimensions" for p in res["problems"]))

    # 29. PNG: solid one color
    def test_png_solid_color(self):
        png_data = make_minimal_png(1000, 1000, color=(50, 50, 50, 255), multi_color=False)
        with open(os.path.join(self.tmp_dir, "cover.png"), "wb") as f:
            f.write(png_data)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "monochrome_image" for p in res["problems"]))

    # 30. ZIP: __MACOSX entry
    def test_zip_macosx_metadata(self):
        zip_path = os.path.join(self.tmp_dir, "bundle.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("__MACOSX/._test.txt", "data")
            z.writestr("test.txt", "real data")

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "zip_macosx_metadata" for p in res["problems"]))

    # 31. ZIP: .DS_Store entry
    def test_zip_ds_store(self):
        zip_path = os.path.join(self.tmp_dir, "bundle.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr(".DS_Store", "data")
            z.writestr("test.txt", "real data")

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "zip_ds_store" for p in res["problems"]))

    # 32. ZIP: absolute path entry
    def test_zip_absolute_path(self):
        zip_path = os.path.join(self.tmp_dir, "bundle.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("/etc/passwd", "root:x:0:0")

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "zip_absolute_path" for p in res["problems"]))

    # 33. ZIP: total uncompressed size >= 250 MB
    def test_zip_too_large(self):
        zip_path = os.path.join(self.tmp_dir, "huge.zip")
        filename = b"big.dat"
        data = b"0" * 10
        crc = 0
        uncomp_sz = 260 * 1024 * 1024
        comp_sz = 10

        local_header = (
            b"PK\x03\x04\x14\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            + struct.pack("<IIIHH", crc, comp_sz, uncomp_sz, len(filename), 0)
            + filename
            + data
        )

        central_header = (
            b"PK\x01\x02\x14\x00\x14\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            + struct.pack("<IIIHHHHHII", crc, comp_sz, uncomp_sz, len(filename), 0, 0, 0, 0, 0, 0)
            + filename
        )

        eocd = (
            b"PK\x05\x06\x00\x00\x00\x00\x01\x00\x01\x00"
            + struct.pack("<IIH", len(central_header), len(local_header), 0)
        )

        with open(zip_path, "wb") as f:
            f.write(local_header + central_header + eocd)

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "zip_too_large" for p in res["problems"]))

    # 34. ZIP: empty zip
    def test_zip_empty(self):
        zip_path = os.path.join(self.tmp_dir, "empty.zip")
        with zipfile.ZipFile(zip_path, "w"):
            pass

        res = product_qa_deep.check_folder(self.tmp_dir)
        self.assertTrue(any(p["kind"] == "empty_zip" for p in res["problems"]))

    # 35. Full good folder -> ready verdict, report outside the pack
    def test_good_folder_ready_verdict(self):
        # Good cover and preview
        with open(os.path.join(self.tmp_dir, "cover.png"), "wb") as f:
            f.write(make_minimal_png(1200, 1200))
        with open(os.path.join(self.tmp_dir, "preview-1.png"), "wb") as f:
            f.write(make_minimal_png(1200, 1200))

        # Good PDF
        stream_a4 = "BT /F1 12 Tf 50 500 Td (Verified daily planner) Tj ET"
        with open(os.path.join(self.tmp_dir, "planner-a4.pdf"), "wb") as f:
            f.write(make_raw_pdf([stream_a4], mediabox=(0, 0, 595.28, 841.89)))
        with open(os.path.join(self.tmp_dir, "planner-letter.pdf"), "wb") as f:
            f.write(make_raw_pdf([stream_a4], mediabox=(0, 0, 612.0, 792.0)))

        # Good XLSX
        wb = pb.Workbook()
        s = wb.add_sheet("Ledger", freeze=True)
        pb.Workbook.put(s, 1, 1, "Item")
        pb.Workbook.put(s, 2, 1, "Desk")
        wb.save(os.path.join(self.tmp_dir, "sheet.xlsx"))

        # Good ZIP
        with zipfile.ZipFile(os.path.join(self.tmp_dir, "archive.zip"), "w") as z:
            z.writestr("notes.txt", "Some personal notes")

        report_dir = tempfile.mkdtemp()
        qa_json_path = os.path.join(report_dir, "qa_deep.json")
        res = product_qa_deep.check_folder(self.tmp_dir, report_path=qa_json_path)
        self.assertEqual(res["verdict"], "ready")
        self.assertEqual(res["score"], 100)
        self.assertEqual(len(res["problems"]), 0)

        # Report is written outside the pack, never inside it
        self.assertTrue(os.path.isfile(qa_json_path))
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "qa_deep.json")))

    # 36. Missing library fallback -> skipped/fallback without crash
    def test_missing_library_fallback_does_not_crash(self):
        stream = "BT /F1 12 Tf 50 500 Td (Working with pure-python fallback) Tj ET"
        with open(os.path.join(self.tmp_dir, "doc.pdf"), "wb") as f:
            f.write(make_raw_pdf([stream]))

        with mock.patch.dict("sys.modules", {"pypdf": None, "pdfplumber": None, "fitz": None, "openpyxl": None, "docx": None, "PIL": None}):
            res = product_qa_deep.check_folder(self.tmp_dir)
            self.assertIn("verdict", res)
            self.assertIn("score", res)

    # 37. launch_pack integration lowers score by 25 on fix verdict
    def test_launch_pack_integration_lowers_score(self):
        with open(os.path.join(self.tmp_dir, "cover.png"), "wb") as f:
            f.write(make_minimal_png(1200, 1200))
        with open(os.path.join(self.tmp_dir, "preview-1.png"), "wb") as f:
            f.write(make_minimal_png(1200, 1200))
        with open(os.path.join(self.tmp_dir, "preview-2.png"), "wb") as f:
            f.write(make_minimal_png(1200, 1200))
        with open(os.path.join(self.tmp_dir, "preview-3.png"), "wb") as f:
            f.write(make_minimal_png(1200, 1200))

        # Blank PDF deliverable
        with open(os.path.join(self.tmp_dir, "planner.pdf"), "wb") as f:
            f.write(make_raw_pdf([""]))

        for doc in ["listing.md", "README.md", "LICENSE.txt", "CHECKLIST.md", "FILE_LIST.txt"]:
            with open(os.path.join(self.tmp_dir, doc), "w") as f:
                f.write("content")

        spec = {
            "listing": {
                "title": "A Complete Valid Title For Testing",
                "description": "This is a comprehensive description that easily exceeds one hundred characters in total length for quality evaluation.",
                "tags": [f"tag{i}" for i in range(13)]
            }
        }

        score, reasons = launch_pack.evaluate_pack_quality(self.tmp_dir, spec)
        self.assertTrue(any("Deep QA issue:" in r for r in reasons))

    # 38. launch_pack integration handles QA crash gracefully
    def test_launch_pack_integration_handles_qa_crash(self):
        spec = {"listing": {"title": "Title Here", "description": "Desc"}}
        with mock.patch("product_qa_deep.check_folder", side_effect=RuntimeError("Simulated failure")):
            score, reasons = launch_pack.evaluate_pack_quality(self.tmp_dir, spec)
            self.assertTrue(any("deep QA skipped: RuntimeError" in r for r in reasons))


class GuideSheetTests(unittest.TestCase):
    def test_instructions_sheet_needs_no_formulas(self):
        self.assertTrue(product_qa_deep._is_guide_sheet("Instructions"))
        self.assertTrue(product_qa_deep._is_guide_sheet("How to use"))
        self.assertFalse(product_qa_deep._is_guide_sheet("Monthly Budget"))


if __name__ == "__main__":
    unittest.main()
