"""product_qa_deep.py — Deep product quality checker before a pack is marked ready.

Ensures that a buyer never receives a broken file.
Inspects product files (PDF, XLSX, DOCX, PNG, ZIP) within a product folder:
  1. PDF checks:
     - Uses pypdf / pdfplumber / PyMuPDF if installed; otherwise pure-python fallback.
     - Page count > 0.
     - No blank pages (no text and no images/drawings).
     - Every page the same size.
     - A4 and US Letter versions both present when the folder has a "planner" or "printable" PDF.
     - Text not cut at page edge (text boxes inside a 5 mm safe margin).
     - Fonts embedded or standard 14 Base fonts.
     - No placeholder text ("lorem ipsum", "TODO", "{{", "None", "null", "undefined", "[object").
     - Page numbers in order if present.
  2. XLSX checks (openpyxl if available, otherwise pure-python OpenXML parser):
     - No cells with #REF!, #VALUE!, #DIV/0!, #NAME?.
     - Formulas present where a sheet says "auto"/"total".
     - Frozen header row on data sheets.
     - No empty sheets.
     - Column widths not zero.
  3. DOCX checks (python-docx if available, otherwise pure-python OpenXML parser):
     - Document not empty.
     - No placeholder text (same forbidden tokens as PDF).
  4. Images & ZIP:
     - Cover/preview PNG at least 1000 px on the short side.
     - Pinterest pins exactly 1000x1500 (2:3 aspect ratio).
     - Images not fully one solid colour.
     - ZIP: opens, not empty, no __MACOSX, no .DS_Store, no absolute paths, total uncompressed size < 250 MB.
  5. qa_deep.json written into the pack folder. No money, no titles in logs.
"""

import json
import os
import re
import struct
import zlib
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Any, Optional, Tuple

FORBIDDEN_TEXT_PATTERNS = [
    r"lorem\s+ipsum",
    r"\bTODO\b",
    r"\{\{",
    r"\bNone\b",
    r"\bnull\b",
    r"\bundefined\b",
    r"\[object"
]
FORBIDDEN_RE = re.compile("|".join(FORBIDDEN_TEXT_PATTERNS), re.IGNORECASE)

# 5 mm in PDF points (72 points per inch; 1 inch = 25.4 mm => 5 * 72 / 25.4 ≈ 14.1732 pt)
MARGIN_5MM_PT = 5.0 * 72.0 / 25.4


def _check_forbidden_text(text: str) -> Optional[str]:
    m = FORBIDDEN_RE.search(text)
    if m:
        return m.group(0)
    return None


# ==============================================================================
# Pure Python PNG reader & color check fallback
# ==============================================================================

def _read_png_dimensions_and_pixels(path: str) -> Tuple[Optional[Tuple[int, int]], bool, Optional[str]]:
    """Return ((width, height), is_uniform_color, error_message)."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            w, h = img.size
            # Check if fully one color
            extrema = img.convert("RGBA").getextrema()
            is_single = all(min_val == max_val for min_val, max_val in extrema[:3])
            return (w, h), is_single, None
    except ImportError:
        pass
    except Exception as e:
        return None, False, str(e)

    # Pure Python fallback for PNG
    try:
        with open(path, "rb") as f:
            header = f.read(8)
            if header != b"\x89PNG\r\n\x1a\n":
                return None, False, "Not a valid PNG file"

            w, h = 0, 0
            idat_chunks = []
            bit_depth, color_type = 0, 0

            while True:
                chunk_len_bytes = f.read(4)
                if not chunk_len_bytes or len(chunk_len_bytes) < 4:
                    break
                chunk_len = struct.unpack(">I", chunk_len_bytes)[0]
                chunk_type = f.read(4)
                chunk_data = f.read(chunk_len)
                f.read(4)  # CRC

                if chunk_type == b"IHDR":
                    w, h, bit_depth, color_type = struct.unpack(">IIBB", chunk_data[:10])
                elif chunk_type == b"IDAT":
                    idat_chunks.append(chunk_data)
                elif chunk_type == b"IEND":
                    break

            if w == 0 or h == 0:
                return None, False, "Corrupted PNG header"

            is_single = False
            try:
                raw_data = zlib.decompress(b"".join(idat_chunks))
                if bit_depth == 8:
                    bytes_per_pixel = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type, 3)
                    stride = 1 + w * bytes_per_pixel
                    if len(raw_data) >= stride:
                        first_pixel = raw_data[1: 1 + bytes_per_pixel]
                        all_same = True
                        for row in range(min(h, 50)):
                            row_start = row * stride
                            row_pixels = raw_data[row_start + 1: row_start + stride]
                            if any(row_pixels[i: i + bytes_per_pixel] != first_pixel
                                   for i in range(0, len(row_pixels), bytes_per_pixel)):
                                all_same = False
                                break
                        is_single = all_same
            except Exception:
                is_single = False

            return (w, h), is_single, None
    except Exception as e:
        return None, False, str(e)


# ==============================================================================
# PDF Checks
# ==============================================================================

def _check_pdf(filepath: str, filename: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    problems = []
    meta: Dict[str, Any] = {"file": filename, "pages": 0, "sizes": [], "skipped": []}

    try:
        import pypdf
        return _check_pdf_pypdf(filepath, filename, pypdf)
    except ImportError:
        pass

    try:
        import fitz  # PyMuPDF
        return _check_pdf_fitz(filepath, filename, fitz)
    except ImportError:
        pass

    try:
        import pdfplumber
        return _check_pdf_pdfplumber(filepath, filename, pdfplumber)
    except ImportError:
        pass

    return _check_pdf_pure_python(filepath, filename)


def _check_pdf_pure_python(filepath: str, filename: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    problems = []
    meta = {"file": filename, "pages": 0, "sizes": [], "skipped": []}

    try:
        with open(filepath, "rb") as f:
            data_bytes = f.read()
    except Exception as e:
        problems.append({
            "file": filename, "page": 0, "kind": "pdf_unreadable",
            "detail": f"Could not read PDF: {e}"
        })
        return problems, meta

    data = data_bytes.decode("latin-1", errors="replace")

    page_objs = re.findall(r"/Type\s*/Page\b", data)
    media_boxes = re.findall(r"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]", data)

    if not media_boxes:
        m_root = re.search(r"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]", data)
        if m_root and page_objs:
            media_boxes = [m_root.groups()] * len(page_objs)

    page_count = len(media_boxes) if media_boxes else len(page_objs)
    meta["pages"] = page_count

    if page_count == 0:
        problems.append({
            "file": filename, "page": 0, "kind": "empty_pdf",
            "detail": "PDF has 0 pages"
        })
        return problems, meta

    sizes = []
    for mb in media_boxes:
        w = round(abs(float(mb[2]) - float(mb[0])), 2)
        h = round(abs(float(mb[3]) - float(mb[1])), 2)
        sizes.append((w, h))

    meta["sizes"] = list(set(sizes))
    if len(set(sizes)) > 1:
        problems.append({
            "file": filename, "page": 0, "kind": "mixed_page_sizes",
            "detail": f"Pages have different sizes: {set(sizes)}"
        })

    streams = re.findall(r"stream\r?\n(.*?)\r?\nendstream", data, re.S)
    if not streams:
        streams_compressed = re.findall(r"<<.*?/Filter\s*/FlateDecode.*?>>\s*stream\r?\n(.*?)\r?\nendstream", data, re.S)
        if streams_compressed:
            decompressed = []
            for s in streams_compressed:
                try:
                    raw = s.encode("latin-1")
                    decompressed.append(zlib.decompress(raw).decode("latin-1", errors="replace"))
                except Exception:
                    pass
            if decompressed:
                streams = decompressed

    if not streams:
        meta["skipped"].append("Deep stream inspection (streams compressed or unparseable in pure-python)")

    # Font embedding check
    font_defs = re.findall(r"/Type\s*/Font\b[^>]*?/BaseFont\s*/([A-Za-z0-9+_-]+)", data)
    font_descriptors = re.findall(r"/Type\s*/FontDescriptor\b", data)
    standard_14 = {"Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
                   "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
                   "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
                   "Symbol", "ZapfDingbats"}
    for base_font in font_defs:
        clean_name = base_font.split("/")[-1].split("+")[-1]
        if clean_name not in standard_14 and not font_descriptors:
            problems.append({
                "file": filename, "page": 0, "kind": "font_not_embedded",
                "detail": f"Font {clean_name} is neither standard Base-14 nor embedded"
            })
            break

    page_numbers_seen = []
    op_text = re.compile(r"BT\s+/(?:F\d+|[A-Za-z0-9_-]+)\s+([\d.]+)\s+Tf\s+([\d.-]+)\s+([\d.-]+)\s+Td\s+\((.*?)\)\s+Tj\s+ET")
    op_generic_text = re.compile(r"\((.*?)\)\s*Tj")

    page_width = sizes[0][0] if sizes else 595.28
    page_height = sizes[0][1] if sizes else 841.89

    for idx, stream in enumerate(streams, 1):
        text_matches = op_text.findall(stream)
        raw_texts = op_generic_text.findall(stream)
        drawn = bool(re.search(r"(?:re|m|l|c|h|Do)\s+[SfB]", stream)) or bool(re.search(r"/(?:Im|Image|XObject)\b", stream))

        combined_text_list = []
        for m in text_matches:
            raw_str = m[3].replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
            combined_text_list.append(raw_str)
        if not combined_text_list and raw_texts:
            for rt in raw_texts:
                combined_text_list.append(rt.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\"))

        combined_text = " ".join(combined_text_list).strip()

        if not combined_text and not drawn:
            problems.append({
                "file": filename, "page": idx, "kind": "blank_page",
                "detail": f"Page {idx} has no text and no images/drawings"
            })

        for m in text_matches:
            font_size = float(m[0])
            tx = float(m[1])
            ty = float(m[2])
            txt = m[3]
            approx_w = len(txt) * font_size * 0.5
            if tx < MARGIN_5MM_PT or (tx + approx_w) > (page_width - MARGIN_5MM_PT):
                problems.append({
                    "file": filename, "page": idx, "kind": "text_outside_safe_margin",
                    "detail": f"Text '{txt[:20]}' at x={tx:.1f} is outside 5mm safe margin"
                })
                break
            if ty < MARGIN_5MM_PT or ty > (page_height - MARGIN_5MM_PT):
                problems.append({
                    "file": filename, "page": idx, "kind": "text_outside_safe_margin",
                    "detail": f"Text '{txt[:20]}' at y={ty:.1f} is outside 5mm safe margin"
                })
                break

        bad = _check_forbidden_text(combined_text)
        if bad:
            problems.append({
                "file": filename, "page": idx, "kind": "placeholder_text",
                "detail": f"Forbidden placeholder text '{bad}' found on page {idx}"
            })

        p_match = re.search(r"\bPage\s+(\d+)\s+of\s+(\d+)\b", combined_text, re.IGNORECASE)
        if p_match:
            page_numbers_seen.append((idx, int(p_match.group(1))))

    if len(page_numbers_seen) >= 2:
        for expected_pos, (page_idx, num_val) in enumerate(page_numbers_seen, 1):
            if num_val != page_idx:
                problems.append({
                    "file": filename, "page": page_idx, "kind": "page_number_out_of_order",
                    "detail": f"Page number {num_val} found on physical page {page_idx}"
                })
                break

    return problems, meta


def _check_pdf_pypdf(filepath: str, filename: str, pypdf_mod) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    problems = []
    meta = {"file": filename, "pages": 0, "sizes": [], "skipped": []}
    try:
        reader = pypdf_mod.PdfReader(filepath)
    except Exception as e:
        problems.append({"file": filename, "page": 0, "kind": "pdf_unreadable", "detail": str(e)})
        return problems, meta

    meta["pages"] = len(reader.pages)
    if meta["pages"] == 0:
        problems.append({"file": filename, "page": 0, "kind": "empty_pdf", "detail": "PDF has 0 pages"})
        return problems, meta

    sizes = []
    page_numbers_seen = []

    for idx, page in enumerate(reader.pages, 1):
        mb = page.mediabox
        w = round(float(mb.width), 2)
        h = round(float(mb.height), 2)
        sizes.append((w, h))

        text = page.extract_text() or ""
        has_images = len(page.images) > 0 if hasattr(page, "images") else False

        if not text.strip() and not has_images:
            problems.append({
                "file": filename, "page": idx, "kind": "blank_page",
                "detail": f"Page {idx} has no text and no images"
            })

        bad = _check_forbidden_text(text)
        if bad:
            problems.append({
                "file": filename, "page": idx, "kind": "placeholder_text",
                "detail": f"Forbidden placeholder text '{bad}' found on page {idx}"
            })

        p_match = re.search(r"\bPage\s+(\d+)\s+of\s+(\d+)\b", text, re.IGNORECASE)
        if p_match:
            page_numbers_seen.append((idx, int(p_match.group(1))))

    meta["sizes"] = list(set(sizes))
    if len(set(sizes)) > 1:
        problems.append({
            "file": filename, "page": 0, "kind": "mixed_page_sizes",
            "detail": f"Pages have different sizes: {set(sizes)}"
        })

    if len(page_numbers_seen) >= 2:
        for expected_pos, (page_idx, num_val) in enumerate(page_numbers_seen, 1):
            if num_val != page_idx:
                problems.append({
                    "file": filename, "page": page_idx, "kind": "page_number_out_of_order",
                    "detail": f"Page number {num_val} found on physical page {page_idx}"
                })
                break

    pure_probs, _ = _check_pdf_pure_python(filepath, filename)
    for p in pure_probs:
        if p["kind"] in ("text_outside_safe_margin", "font_not_embedded"):
            problems.append(p)

    return problems, meta


def _check_pdf_fitz(filepath: str, filename: str, fitz_mod) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    problems = []
    meta = {"file": filename, "pages": 0, "sizes": [], "skipped": []}
    try:
        doc = fitz_mod.open(filepath)
    except Exception as e:
        problems.append({"file": filename, "page": 0, "kind": "pdf_unreadable", "detail": str(e)})
        return problems, meta

    meta["pages"] = len(doc)
    if meta["pages"] == 0:
        problems.append({"file": filename, "page": 0, "kind": "empty_pdf", "detail": "PDF has 0 pages"})
        return problems, meta

    sizes = []
    page_numbers_seen = []

    for idx, page in enumerate(doc, 1):
        rect = page.rect
        w, h = round(rect.width, 2), round(rect.height, 2)
        sizes.append((w, h))

        text = page.get_text()
        has_drawings = len(page.get_drawings()) > 0
        has_images = len(page.get_images()) > 0

        if not text.strip() and not has_drawings and not has_images:
            problems.append({
                "file": filename, "page": idx, "kind": "blank_page",
                "detail": f"Page {idx} has no text and no images/drawings"
            })

        bad = _check_forbidden_text(text)
        if bad:
            problems.append({
                "file": filename, "page": idx, "kind": "placeholder_text",
                "detail": f"Forbidden placeholder text '{bad}' found on page {idx}"
            })

        for b in page.get_text("blocks"):
            x0, y0, x1, y1, btext = b[:5]
            if x0 < MARGIN_5MM_PT or x1 > (w - MARGIN_5MM_PT) or y0 < MARGIN_5MM_PT or y1 > (h - MARGIN_5MM_PT):
                problems.append({
                    "file": filename, "page": idx, "kind": "text_outside_safe_margin",
                    "detail": f"Text '{btext[:20].strip()}' outside 5mm safe margin"
                })
                break

        p_match = re.search(r"\bPage\s+(\d+)\s+of\s+(\d+)\b", text, re.IGNORECASE)
        if p_match:
            page_numbers_seen.append((idx, int(p_match.group(1))))

    meta["sizes"] = list(set(sizes))
    if len(set(sizes)) > 1:
        problems.append({
            "file": filename, "page": 0, "kind": "mixed_page_sizes",
            "detail": f"Pages have different sizes: {set(sizes)}"
        })

    if len(page_numbers_seen) >= 2:
        for expected_pos, (page_idx, num_val) in enumerate(page_numbers_seen, 1):
            if num_val != page_idx:
                problems.append({
                    "file": filename, "page": page_idx, "kind": "page_number_out_of_order",
                    "detail": f"Page number {num_val} found on physical page {page_idx}"
                })
                break

    return problems, meta


def _check_pdf_pdfplumber(filepath: str, filename: str, pdfplumber_mod) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    try:
        with pdfplumber_mod.open(filepath) as pdf:
            return _check_pdf_pure_python(filepath, filename)
    except Exception as e:
        return [{"file": filename, "page": 0, "kind": "pdf_unreadable", "detail": str(e)}], {"file": filename, "pages": 0, "sizes": [], "skipped": []}


# ==============================================================================
# XLSX Checks
# ==============================================================================

EXCEL_ERROR_CODES = {"#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#N/A", "#NUM!", "#NULL!"}


def _check_xlsx(filepath: str, filename: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    problems = []
    meta: Dict[str, Any] = {"file": filename, "sheets": [], "skipped": []}

    try:
        import openpyxl
        return _check_xlsx_openpyxl(filepath, filename, openpyxl)
    except ImportError:
        pass

    return _check_xlsx_openxml(filepath, filename)


def _check_xlsx_openxml(filepath: str, filename: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    problems = []
    meta: Dict[str, Any] = {"file": filename, "sheets": [], "skipped": []}

    try:
        with zipfile.ZipFile(filepath, "r") as z:
            wb_xml = z.read("xl/workbook.xml")
            wb_root = ET.fromstring(wb_xml)
            ns = {"ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
                  "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}

            sheets = wb_root.findall(".//ns:sheet", ns)
            if not sheets:
                sheets = wb_root.findall(".//sheet")

            sheet_map = []
            for s in sheets:
                s_name = s.attrib.get("name", "")
                s_id = s.attrib.get("sheetId", "")
                r_id = s.attrib.get(f"{{{ns['r']}}}id", "") or s.attrib.get("id", "")
                sheet_map.append({"name": s_name, "id": s_id, "rid": r_id})

            rels_xml = z.read("xl/_rels/workbook.xml.rels")
            rels_root = ET.fromstring(rels_xml)
            rel_ns = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
            target_by_id = {}
            for rel in rels_root.findall(".//rel:Relationship", rel_ns):
                target_by_id[rel.attrib.get("Id")] = rel.attrib.get("Target")
            if not target_by_id:
                for rel in rels_root.findall(".//Relationship"):
                    target_by_id[rel.attrib.get("Id")] = rel.attrib.get("Target")

            shared_strings = []
            if "xl/sharedStrings.xml" in z.namelist():
                sst_root = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in sst_root.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si") or sst_root.findall(".//si"):
                    t_nodes = si.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t") or si.findall(".//t")
                    shared_strings.append("".join(t.text or "" for t in t_nodes))

            for sm in sheet_map:
                meta["sheets"].append(sm["name"])
                target = target_by_id.get(sm["rid"], f"worksheets/sheet{sm['id']}.xml")
                target_path = target if target.startswith("xl/") else f"xl/{target}"

                if target_path not in z.namelist():
                    continue

                sheet_xml = z.read(target_path)
                s_root = ET.fromstring(sheet_xml)

                rows = s_root.findall(".//ns:row", ns)
                if not rows:
                    rows = s_root.findall(".//row")

                has_cells = False
                sheet_text_combined = []
                sheet_has_formulas = False
                cell_values = []

                for r in rows:
                    cells = r.findall(".//ns:c", ns)
                    if not cells:
                        cells = r.findall(".//c")
                    for c in cells:
                        has_cells = True
                        cell_type = c.attrib.get("t", "")

                        f_node = c.find(".//ns:f", ns)
                        if f_node is None:
                            f_node = c.find(".//f")
                        if f_node is not None and f_node.text:
                            sheet_has_formulas = True
                            formula_text = f_node.text.strip()
                            for err in EXCEL_ERROR_CODES:
                                if err in formula_text:
                                    problems.append({
                                        "file": filename, "page": sm["name"], "kind": "cell_error",
                                        "detail": f"Cell contains error {err} in sheet '{sm['name']}'"
                                    })
                                    break

                        v_node = c.find(".//ns:v", ns)
                        if v_node is None:
                            v_node = c.find(".//v")
                        val_str = ""
                        if v_node is not None and v_node.text:
                            if cell_type == "s":
                                idx = int(v_node.text)
                                if idx < len(shared_strings):
                                    val_str = shared_strings[idx]
                            else:
                                val_str = v_node.text
                        elif cell_type == "inlineStr":
                            is_node = c.find(".//ns:is/ns:t", ns)
                            if is_node is None:
                                is_node = c.find(".//is/t")
                            if is_node is not None and is_node.text:
                                val_str = is_node.text

                        if val_str:
                            cell_values.append(val_str)
                            sheet_text_combined.append(val_str)
                            for err in EXCEL_ERROR_CODES:
                                if err in val_str:
                                    problems.append({
                                        "file": filename, "page": sm["name"], "kind": "cell_error",
                                        "detail": f"Cell contains error {err} in sheet '{sm['name']}'"
                                    })
                                    break

                # 1. No empty sheets
                if not has_cells or not cell_values:
                    problems.append({
                        "file": filename, "page": sm["name"], "kind": "empty_sheet",
                        "detail": f"Sheet '{sm['name']}' has no data or is empty"
                    })

                # 2. Formulas present where a sheet says "auto"/"total"
                combined_sheet_lower = " ".join(sheet_text_combined).lower() + " " + sm["name"].lower()
                if ("auto" in combined_sheet_lower or "total" in combined_sheet_lower) and not sheet_has_formulas:
                    problems.append({
                        "file": filename, "page": sm["name"], "kind": "missing_formula",
                        "detail": f"Sheet '{sm['name']}' mentions 'auto' or 'total' but has no formulas"
                    })

                # 3. Frozen header row on data sheets
                pane = s_root.find(".//ns:pane", ns)
                if pane is None:
                    pane = s_root.find(".//pane")
                is_frozen = pane is not None and pane.attrib.get("state") == "frozen"
                is_data_sheet = "instruction" not in sm["name"].lower() and len(rows) > 1
                if is_data_sheet and not is_frozen:
                    problems.append({
                        "file": filename, "page": sm["name"], "kind": "missing_frozen_header",
                        "detail": f"Data sheet '{sm['name']}' does not have a frozen header row"
                    })

                # 4. Column widths not zero
                cols = s_root.findall(".//ns:col", ns)
                if not cols:
                    cols = s_root.findall(".//col")
                for col in cols:
                    w = float(col.attrib.get("width", "10"))
                    if w <= 0:
                        problems.append({
                            "file": filename, "page": sm["name"], "kind": "zero_column_width",
                            "detail": f"Column in sheet '{sm['name']}' has width {w}"
                        })
                        break

    except Exception as e:
        problems.append({
            "file": filename, "page": 0, "kind": "xlsx_unreadable",
            "detail": f"Failed to parse XLSX: {e}"
        })

    return problems, meta


def _check_xlsx_openpyxl(filepath: str, filename: str, openpyxl_mod) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    problems = []
    meta = {"file": filename, "sheets": [], "skipped": []}
    try:
        wb = openpyxl_mod.load_workbook(filepath, data_only=False)
    except Exception as e:
        problems.append({"file": filename, "page": 0, "kind": "xlsx_unreadable", "detail": str(e)})
        return problems, meta

    meta["sheets"] = wb.sheetnames
    if not wb.sheetnames:
        problems.append({"file": filename, "page": 0, "kind": "empty_workbook", "detail": "No sheets found"})
        return problems, meta

    for name in wb.sheetnames:
        ws = wb[name]
        has_cells = False
        sheet_text = []
        has_formulas = False

        for row in ws.iter_rows(values_only=False):
            for cell in row:
                if cell.value is not None:
                    has_cells = True
                    val_str = str(cell.value)
                    sheet_text.append(val_str)
                    if str(cell.value).startswith("="):
                        has_formulas = True
                    for err in EXCEL_ERROR_CODES:
                        if err in val_str:
                            problems.append({
                                "file": filename, "page": name, "kind": "cell_error",
                                "detail": f"Cell contains error {err} in sheet '{name}'"
                            })
                            break

        if not has_cells:
            problems.append({
                "file": filename, "page": name, "kind": "empty_sheet",
                "detail": f"Sheet '{name}' is empty"
            })

        combined = " ".join(sheet_text).lower() + " " + name.lower()
        if ("auto" in combined or "total" in combined) and not has_formulas:
            problems.append({
                "file": filename, "page": name, "kind": "missing_formula",
                "detail": f"Sheet '{name}' mentions 'auto' or 'total' but has no formulas"
            })

        is_data_sheet = "instruction" not in name.lower() and ws.max_row > 1
        is_frozen = bool(ws.freeze_panes)
        if is_data_sheet and not is_frozen:
            problems.append({
                "file": filename, "page": name, "kind": "missing_frozen_header",
                "detail": f"Data sheet '{name}' does not have a frozen header row"
            })

        for col_letter, col_dim in ws.column_dimensions.items():
            if col_dim.width is not None and col_dim.width <= 0:
                problems.append({
                    "file": filename, "page": name, "kind": "zero_column_width",
                    "detail": f"Column {col_letter} in sheet '{name}' has width {col_dim.width}"
                })
                break

    return problems, meta


# ==============================================================================
# DOCX Checks
# ==============================================================================

def _check_docx(filepath: str, filename: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    problems = []
    meta: Dict[str, Any] = {"file": filename, "paragraphs": 0, "skipped": []}

    try:
        import docx
        return _check_docx_python_docx(filepath, filename, docx)
    except ImportError:
        pass

    return _check_docx_openxml(filepath, filename)


def _check_docx_openxml(filepath: str, filename: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    problems = []
    meta = {"file": filename, "paragraphs": 0, "skipped": []}

    try:
        with zipfile.ZipFile(filepath, "r") as z:
            if "word/document.xml" not in z.namelist():
                problems.append({
                    "file": filename, "page": 0, "kind": "docx_invalid",
                    "detail": "Missing word/document.xml"
                })
                return problems, meta

            doc_xml = z.read("word/document.xml")
            root = ET.fromstring(doc_xml)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

            paragraphs = root.findall(".//w:p", ns)
            if not paragraphs:
                paragraphs = root.findall(".//p")
            meta["paragraphs"] = len(paragraphs)

            text_chunks = []
            for p in paragraphs:
                t_nodes = p.findall(".//w:t", ns)
                if not t_nodes:
                    t_nodes = p.findall(".//t")
                p_text = "".join(t.text or "" for t in t_nodes).strip()
                if p_text:
                    text_chunks.append(p_text)

            combined_text = "\n".join(text_chunks).strip()
            if not combined_text:
                problems.append({
                    "file": filename, "page": 1, "kind": "empty_docx",
                    "detail": f"DOCX document '{filename}' has no text content"
                })
                return problems, meta

            bad = _check_forbidden_text(combined_text)
            if bad:
                problems.append({
                    "file": filename, "page": 1, "kind": "placeholder_text",
                    "detail": f"Forbidden placeholder text '{bad}' found in '{filename}'"
                })

    except Exception as e:
        problems.append({
            "file": filename, "page": 0, "kind": "docx_unreadable",
            "detail": f"Could not read DOCX: {e}"
        })

    return problems, meta


def _check_docx_python_docx(filepath: str, filename: str, docx_mod) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    problems = []
    meta = {"file": filename, "paragraphs": 0, "skipped": []}
    try:
        doc = docx_mod.Document(filepath)
    except Exception as e:
        problems.append({"file": filename, "page": 0, "kind": "docx_unreadable", "detail": str(e)})
        return problems, meta

    meta["paragraphs"] = len(doc.paragraphs)
    combined = "\n".join(p.text for p in doc.paragraphs if p.text).strip()

    if not combined:
        problems.append({
            "file": filename, "page": 1, "kind": "empty_docx",
            "detail": f"DOCX document '{filename}' is empty"
        })
        return problems, meta

    bad = _check_forbidden_text(combined)
    if bad:
        problems.append({
            "file": filename, "page": 1, "kind": "placeholder_text",
            "detail": f"Forbidden placeholder text '{bad}' found in '{filename}'"
        })

    return problems, meta


# ==============================================================================
# Image & ZIP Checks
# ==============================================================================

def _check_image(filepath: str, filename: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    problems = []
    meta = {"file": filename, "size": None}

    dims, is_single, err = _read_png_dimensions_and_pixels(filepath)
    if err:
        problems.append({"file": filename, "page": 0, "kind": "image_invalid", "detail": err})
        return problems, meta

    if dims is None:
        problems.append({"file": filename, "page": 0, "kind": "image_invalid", "detail": "Could not determine image dimensions"})
        return problems, meta

    w, h = dims
    meta["size"] = [w, h]

    lower_name = filename.lower()
    is_cover_or_preview = "cover" in lower_name or "preview" in lower_name
    is_pin = "pin" in lower_name

    if is_cover_or_preview:
        short_side = min(w, h)
        if short_side < 1000:
            problems.append({
                "file": filename, "page": 0, "kind": "image_resolution_too_low",
                "detail": f"Image short side is {short_side}px, minimum is 1000px"
            })

    if is_pin:
        if (w, h) != (1000, 1500):
            problems.append({
                "file": filename, "page": 0, "kind": "invalid_pin_dimensions",
                "detail": f"Pin dimensions {w}x{h} do not match required 1000x1500 (2:3)"
            })

    if is_single:
        problems.append({
            "file": filename, "page": 0, "kind": "monochrome_image",
            "detail": f"Image '{filename}' is entirely a single solid colour"
        })

    return problems, meta


def _check_zip(filepath: str, filename: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    problems = []
    meta = {"file": filename, "uncompressed_size_mb": 0}

    try:
        with zipfile.ZipFile(filepath, "r") as z:
            infos = z.infolist()
            if not infos:
                problems.append({
                    "file": filename, "page": 0, "kind": "empty_zip",
                    "detail": "ZIP archive is empty"
                })
                return problems, meta

            total_uncompressed = sum(info.file_size for info in infos)
            meta["uncompressed_size_mb"] = round(total_uncompressed / (1024 * 1024), 2)

            if total_uncompressed >= 250 * 1024 * 1024:
                problems.append({
                    "file": filename, "page": 0, "kind": "zip_too_large",
                    "detail": f"Total uncompressed size {meta['uncompressed_size_mb']}MB exceeds 250 MB limit"
                })

            for info in infos:
                arcname = info.filename
                if arcname.startswith("/") or arcname.startswith("\\") or (len(arcname) > 1 and arcname[1] == ":"):
                    problems.append({
                        "file": filename, "page": 0, "kind": "zip_absolute_path",
                        "detail": f"Entry '{arcname}' has an absolute path"
                    })
                if "__MACOSX" in arcname:
                    problems.append({
                        "file": filename, "page": 0, "kind": "zip_macosx_metadata",
                        "detail": f"ZIP contains macOS metadata '__MACOSX': '{arcname}'"
                    })
                if os.path.basename(arcname) == ".DS_Store":
                    problems.append({
                        "file": filename, "page": 0, "kind": "zip_ds_store",
                        "detail": f"ZIP contains .DS_Store file: '{arcname}'"
                    })
    except Exception as e:
        problems.append({
            "file": filename, "page": 0, "kind": "corrupt_zip",
            "detail": f"Could not read ZIP archive: {e}"
        })

    return problems, meta


# ==============================================================================
# Main Folder QA Check
# ==============================================================================

def check_folder(folder: str) -> Dict[str, Any]:
    """Perform deep QA checks on a product pack folder.

    Returns:
      {
        "verdict": "ready" | "fix",
        "score": 0-100,
        "problems": [ {file, page, kind, detail} ],
        "checked": [ ... ]
      }
    """
    if not os.path.isdir(folder):
        return {
            "verdict": "fix",
            "score": 0,
            "problems": [{"file": folder, "page": 0, "kind": "missing_folder", "detail": "Folder does not exist"}],
            "checked": []
        }

    all_files = sorted(os.listdir(folder))
    checked_list = []
    problems: List[Dict[str, Any]] = []

    has_planner_or_printable_pdf = False
    pdf_sizes_present = set()

    for fname in all_files:
        fpath = os.path.join(folder, fname)
        if not os.path.isfile(fpath):
            continue

        lower = fname.lower()
        if lower.endswith(".pdf"):
            if "planner" in lower or "printable" in lower:
                has_planner_or_printable_pdf = True
            if "-a4.pdf" in lower or "_a4.pdf" in lower:
                pdf_sizes_present.add("A4")
            if "-letter.pdf" in lower or "_letter.pdf" in lower or "-us-letter.pdf" in lower or "-usletter.pdf" in lower:
                pdf_sizes_present.add("Letter")

    for fname in all_files:
        fpath = os.path.join(folder, fname)
        if not os.path.isfile(fpath):
            continue

        lower = fname.lower()
        ext = os.path.splitext(lower)[1]

        if ext == ".pdf":
            checked_list.append(fname)
            pdf_probs, meta = _check_pdf(fpath, fname)
            problems.extend(pdf_probs)

            for w, h in meta.get("sizes", []):
                if abs(w - 595.28) < 5 and abs(h - 841.89) < 5:
                    pdf_sizes_present.add("A4")
                elif abs(w - 612.0) < 5 and abs(h - 792.0) < 5:
                    pdf_sizes_present.add("Letter")

        elif ext == ".xlsx":
            checked_list.append(fname)
            xlsx_probs, meta = _check_xlsx(fpath, fname)
            problems.extend(xlsx_probs)

        elif ext == ".docx":
            checked_list.append(fname)
            docx_probs, meta = _check_docx(fpath, fname)
            problems.extend(docx_probs)

        elif ext == ".png":
            checked_list.append(fname)
            img_probs, meta = _check_image(fpath, fname)
            problems.extend(img_probs)

        elif ext == ".zip":
            checked_list.append(fname)
            zip_probs, meta = _check_zip(fpath, fname)
            problems.extend(zip_probs)

    if has_planner_or_printable_pdf:
        missing_sizes = []
        if "A4" not in pdf_sizes_present:
            missing_sizes.append("A4")
        if "Letter" not in pdf_sizes_present:
            missing_sizes.append("US Letter")
        if missing_sizes:
            problems.append({
                "file": "folder", "page": 0, "kind": "missing_pdf_size",
                "detail": f"Planner/printable PDF must have both A4 and US Letter sizes; missing: {', '.join(missing_sizes)}"
            })

    score = 100
    for prob in problems:
        kind = prob.get("kind", "")
        if kind in ("empty_pdf", "blank_page", "cell_error", "empty_docx", "corrupt_zip"):
            score -= 25
        elif kind in ("mixed_page_sizes", "missing_pdf_size", "missing_formula", "missing_frozen_header"):
            score -= 15
        elif kind in ("placeholder_text", "text_outside_safe_margin", "font_not_embedded"):
            score -= 15
        elif kind in ("image_resolution_too_low", "invalid_pin_dimensions", "monochrome_image"):
            score -= 10
        elif kind in ("zip_macosx_metadata", "zip_ds_store", "zip_absolute_path", "zip_too_large"):
            score -= 15
        else:
            score -= 10

    score = max(0, min(100, score))
    verdict = "ready" if (score >= 80 and not problems) else "fix"

    result = {
        "verdict": verdict,
        "score": score,
        "problems": problems,
        "checked": checked_list
    }

    out_file = os.path.join(folder, "qa_deep.json")
    try:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
    except Exception:
        pass

    return result
