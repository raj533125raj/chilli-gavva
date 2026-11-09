"""tests/test_t3a_pdf_meta.py — unit tests for PDF metadata (Title, Author, Subject, Keywords, Creator) and outline bookmarks.

Pure-python tests. Tests clear GITHUB_* env in setUp using mock.patch.dict.
Checks that built PDFs have:
  - document Title (product name)
  - Author 'Chilli Gavva'
  - Subject (short description)
  - Keywords (tags)
  - Creator 'Chilli Gavva robot'
  - outline bookmarks for each section / page title known to the builder
  - valid PDF syntax, trailer, catalog, offsets, and object references
  - no money amounts
"""
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

try:
    import pypdf
except ImportError:
    pypdf = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pdf_design as pd
import product_builder as pb


def parse_pdf_objects(data_str):
    """Parses PDF objects from raw latin-1 string into dict: {obj_num: content_inside_obj}."""
    pattern = re.compile(r"(\d+)\s+0\s+obj\s*(.*?)\s*endobj", re.S)
    return {int(m.group(1)): m.group(2).strip() for m in pattern.finditer(data_str)}


def extract_pdf_string(s, key):
    """Extracts a PDF literal string with balanced escaped parenthesis."""
    idx = s.find("/" + key)
    if idx == -1:
        return None
    open_paren = s.find("(", idx)
    if open_paren == -1:
        return None
    i = open_paren + 1
    depth = 1
    out = []
    while i < len(s) and depth > 0:
        if s[i] == "\\":
            if i + 1 < len(s):
                out.append(s[i:i + 2])
                i += 2
                continue
        elif s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                break
        out.append(s[i])
        i += 1
    raw = "".join(out)
    return raw.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")


def parse_info_dict(data_str):
    """Extracts entries from the /Info dictionary."""
    trailer_m = re.search(r"trailer\s*<<.*?/Info\s+(\d+)\s+0\s+R.*?>>", data_str, re.S)
    if not trailer_m:
        return {}
    info_id = int(trailer_m.group(1))
    objs = parse_pdf_objects(data_str)
    info_body = objs.get(info_id, "")
    entries = {}
    for key in ("Title", "Author", "Subject", "Keywords", "Creator", "Producer", "ChilliStyle"):
        val = extract_pdf_string(info_body, key)
        if val is not None:
            entries[key] = val
    return entries


def parse_catalog_and_outlines(data_str):
    """Extracts /Outlines items and destinations from PDF data."""
    objs = parse_pdf_objects(data_str)
    cat_body = objs.get(1, "")
    outlines_m = re.search(r"/Outlines\s+(\d+)\s+0\s+R", cat_body)
    if not outlines_m:
        return None, []
    outlines_id = int(outlines_m.group(1))
    outlines_body = objs.get(outlines_id, "")
    count_m = re.search(r"/Count\s+(\d+)", outlines_body)
    count = int(count_m.group(1)) if count_m else 0
    first_m = re.search(r"/First\s+(\d+)\s+0\s+R", outlines_body)
    last_m = re.search(r"/Last\s+(\d+)\s+0\s+R", outlines_body)
    first_id = int(first_m.group(1)) if first_m else None
    last_id = int(last_m.group(1)) if last_m else None

    # Follow the linked list of outline items
    items = []
    curr_id = first_id
    visited = set()
    while curr_id and curr_id not in visited and curr_id in objs:
        visited.add(curr_id)
        item_body = objs[curr_id]
        title = extract_pdf_string(item_body, "Title") or ""
        dest_m = re.search(r"/Dest\s*\[(\d+)\s+0\s+R\s+/Fit\]", item_body)
        dest_page_id = int(dest_m.group(1)) if dest_m else None
        prev_m = re.search(r"/Prev\s+(\d+)\s+0\s+R", item_body)
        next_m = re.search(r"/Next\s+(\d+)\s+0\s+R", item_body)
        parent_m = re.search(r"/Parent\s+(\d+)\s+0\s+R", item_body)
        items.append({
            "id": curr_id,
            "title": title,
            "dest_page_id": dest_page_id,
            "prev": int(prev_m.group(1)) if prev_m else None,
            "next": int(next_m.group(1)) if next_m else None,
            "parent": int(parent_m.group(1)) if parent_m else None,
        })
        curr_id = int(next_m.group(1)) if next_m else None

    meta = {
        "outlines_id": outlines_id,
        "count": count,
        "first_id": first_id,
        "last_id": last_id,
    }
    return meta, items


class BasePdfMetaTest(unittest.TestCase):
    def setUp(self):
        super().setUp()
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp_dir = tempfile.mkdtemp()

    def make_sample_planner_spec(self, name="Energy-Based Daily Planner"):
        return {
            "name": name,
            "short_name": name.lower().replace(" ", "-"),
            "niche": "ADHD Planners Agent",
            "style": "B",
            "listing": {
                "title": f"Printable {name}",
                "description": "Flexible daily planning system for neurodivergent minds.",
                "tags": ["daily planner", "energy tracker", "printable template"],
                "buyer": "busy adults",
            },
            "instructions": ["Print at 100% scale."],
            "pages": [
                {
                    "title": "Energy-Based Daily Plan",
                    "subtitle": "Match your tasks to capacity",
                    "sections": [
                        {"heading": "Capacity Check", "type": "checklist", "items": ["High", "Low"]},
                        {"heading": "Tasks", "type": "lines", "count": 4},
                    ],
                },
                {
                    "title": "Low-Energy Reset Sheet",
                    "subtitle": "Gentle structure",
                    "sections": [
                        {"heading": "Tiny Steps", "type": "checkboxes", "count": 3},
                    ],
                },
            ],
        }

    def make_dated_planner_spec(self, name="2027 Teacher Planner"):
        return {
            "name": name,
            "short_name": name.lower().replace(" ", "-"),
            "niche": "2027 Planners Agent",
            "calendar": "jan-dec",
            "year": 2027,
            "listing": {
                "title": f"{name} Printable",
                "description": "Comprehensive school year planner with monthly calendar pages.",
                "tags": ["teacher planner", "2027", "school planner"],
                "buyer": "teachers",
            },
            "instructions": ["Print at 100% scale."],
            "pages": [
                {
                    "title": "Class Roster",
                    "sections": [{"heading": "Students", "type": "lines", "count": 30}],
                }
            ],
        }


class TestPdfDocumentMetadata(BasePdfMetaTest):
    def test_01_metadata_title_present(self):
        spec = self.make_sample_planner_spec("Focus Daily Planner")
        path = os.path.join(self.temp_dir, "focus.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        info = parse_info_dict(data)
        self.assertEqual(info.get("Title"), "Focus Daily Planner")

    def test_02_metadata_author_chilli_gavva(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "author.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        info = parse_info_dict(data)
        self.assertEqual(info.get("Author"), "Chilli Gavva")

    def test_03_metadata_creator_chilli_gavva_robot(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "creator.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        info = parse_info_dict(data)
        self.assertEqual(info.get("Creator"), "Chilli Gavva robot")

    def test_04_metadata_subject_short_description(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "subject.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        info = parse_info_dict(data)
        self.assertEqual(info.get("Subject"), spec["listing"]["description"])

    def test_05_metadata_keywords_tags(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "keywords.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        info = parse_info_dict(data)
        self.assertEqual(info.get("Keywords"), "daily planner, energy tracker, printable template")

    def test_06_metadata_producer_unchanged(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "producer.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        info = parse_info_dict(data)
        self.assertEqual(info.get("Producer"), "Chilli Gavva Product Builder")

    def test_07_metadata_chillistyle_preserved(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "style.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        info = parse_info_dict(data)
        self.assertEqual(info.get("ChilliStyle"), "B")

    def test_08_dated_planner_metadata_title_and_author(self):
        spec = self.make_dated_planner_spec("2027 Academic Planner")
        path = os.path.join(self.temp_dir, "dated.pdf")
        pd.build_dated_planner_pdf(path, spec, "Letter")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        info = parse_info_dict(data)
        self.assertEqual(info.get("Title"), "2027 Academic Planner")
        self.assertEqual(info.get("Author"), "Chilli Gavva")
        self.assertEqual(info.get("Creator"), "Chilli Gavva robot")
        self.assertEqual(info.get("Subject"), spec["listing"]["description"])
        self.assertEqual(info.get("Keywords"), "teacher planner, 2027, school planner")

    def test_09_metadata_empty_or_missing_listing(self):
        spec = {"name": "Barebones", "pages": []}
        path = os.path.join(self.temp_dir, "bare.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        info = parse_info_dict(data)
        self.assertEqual(info.get("Title"), "Barebones")
        self.assertEqual(info.get("Author"), "Chilli Gavva")
        self.assertEqual(info.get("Creator"), "Chilli Gavva robot")
        self.assertNotIn("Subject", info)
        self.assertNotIn("Keywords", info)

    def test_10_metadata_special_characters_escaped(self):
        spec = {
            "name": "Planner (Special) & 100%",
            "listing": {
                "title": "Title (With Parentheses)",
                "description": "A description with (parens) and special — dashes.",
                "tags": ["one (tag)", "two"],
            },
            "pages": [],
        }
        path = os.path.join(self.temp_dir, "special.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        info = parse_info_dict(data)
        self.assertEqual(info.get("Title"), "Planner (Special) & 100%")
        self.assertIn("one (tag)", info.get("Keywords"))


class TestPdfBookmarksAndOutline(BasePdfMetaTest):
    def test_11_catalog_has_outlines_reference(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "cat_outlines.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        meta, items = parse_catalog_and_outlines(data)
        self.assertIsNotNone(meta)
        self.assertGreater(meta["outlines_id"], 0)

    def test_12_outlines_object_has_first_last_count(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "outlines_obj.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        meta, items = parse_catalog_and_outlines(data)
        self.assertEqual(meta["count"], len(items))
        self.assertEqual(meta["first_id"], items[0]["id"])
        self.assertEqual(meta["last_id"], items[-1]["id"])

    def test_13_cover_and_how_to_use_in_bookmarks(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "front_bm.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        _, items = parse_catalog_and_outlines(data)
        titles = [item["title"] for item in items]
        self.assertEqual(titles[0], "Cover")
        self.assertEqual(titles[1], "How to use")

    def test_14_content_pages_in_bookmarks(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "content_bm.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        _, items = parse_catalog_and_outlines(data)
        titles = [item["title"] for item in items]
        self.assertIn("Energy-Based Daily Plan", titles)
        self.assertIn("Low-Energy Reset Sheet", titles)

    def test_15_bookmark_count_matches_page_count(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "count_match.pdf")
        page_count = pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        meta, items = parse_catalog_and_outlines(data)
        self.assertEqual(len(items), page_count)
        self.assertEqual(meta["count"], page_count)

    def test_16_outline_item_linked_list_prev_next(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "links.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        _, items = parse_catalog_and_outlines(data)
        self.assertIsNone(items[0]["prev"])
        self.assertEqual(items[0]["next"], items[1]["id"])
        for i in range(1, len(items) - 1):
            self.assertEqual(items[i]["prev"], items[i - 1]["id"])
            self.assertEqual(items[i]["next"], items[i + 1]["id"])
        self.assertEqual(items[-1]["prev"], items[-2]["id"])
        self.assertIsNone(items[-1]["next"])

    def test_17_outline_item_parent_is_outlines_object(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "parent.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        meta, items = parse_catalog_and_outlines(data)
        for item in items:
            self.assertEqual(item["parent"], meta["outlines_id"])

    def test_18_outline_dest_targets_valid_page_objects(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "dest.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        objs = parse_pdf_objects(data)
        _, items = parse_catalog_and_outlines(data)
        for idx, item in enumerate(items):
            page_id = item["dest_page_id"]
            self.assertIn(page_id, objs)
            page_obj = objs[page_id]
            self.assertIn("/Type /Page", page_obj)
            # Check destination matches sequential page numbers (0-indexed: 6, 8, 10, ...)
            self.assertEqual(page_id, 6 + 2 * idx)

    def test_19_dated_planner_year_at_a_glance_bookmark(self):
        spec = self.make_dated_planner_spec("2027 Calendar")
        path = os.path.join(self.temp_dir, "dated_bm.pdf")
        pd.build_dated_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        _, items = parse_catalog_and_outlines(data)
        titles = [item["title"] for item in items]
        self.assertIn("2027 at a glance", titles)

    def test_20_dated_planner_all_months_in_bookmarks(self):
        spec = self.make_dated_planner_spec("2027 Calendar")
        path = os.path.join(self.temp_dir, "months_bm.pdf")
        pd.build_dated_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        _, items = parse_catalog_and_outlines(data)
        titles = [item["title"] for item in items]
        months = ["January 2027", "February 2027", "March 2027", "April 2027", "May 2027", "June 2027",
                  "July 2027", "August 2027", "September 2027", "October 2027", "November 2027", "December 2027"]
        for m in months:
            self.assertIn(m, titles)

    def test_21_dated_planner_content_page_bookmark(self):
        spec = self.make_dated_planner_spec("2027 Calendar")
        path = os.path.join(self.temp_dir, "roster_bm.pdf")
        pd.build_dated_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        _, items = parse_catalog_and_outlines(data)
        titles = [item["title"] for item in items]
        self.assertIn("Class Roster", titles)

    def test_22_multipage_continuation_in_bookmarks(self):
        spec = {
            "name": "Multi Overflow Planner",
            "listing": {"title": "Multi Overflow"},
            "pages": [{
                "title": "Week plan",
                "subtitle": "",
                "sections": [{"heading": f"Block {i}", "type": "lines", "items": [], "count": 12} for i in range(9)],
            }],
        }
        path = os.path.join(self.temp_dir, "overflow.pdf")
        n = pd.build_planner_pdf(path, spec, "A4")
        self.assertGreaterEqual(n, 4)
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        _, items = parse_catalog_and_outlines(data)
        titles = [item["title"] for item in items]
        self.assertIn("Week plan", titles)
        self.assertTrue(any("Week plan (cont.)" in t for t in titles))


class TestPdfStructureAndIntegrity(BasePdfMetaTest):
    def test_23_pdf_header_and_magic_bytes(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "header.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            content = f.read()
        self.assertTrue(content.startswith(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"))

    def test_24_pdf_xref_table_and_offsets(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "xref.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read()
        self.assertIn(b"\nstartxref\n", data)
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        # Verify xref table starts at offset
        startxref_val = int(re.search(rb"startxref\s+(\d+)\s+%%EOF", data).group(1))
        self.assertEqual(data[startxref_val:startxref_val + 4], b"xref")

    def test_25_trailer_root_and_info(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "trailer.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        trailer_m = re.search(r"trailer\s*<<\s*/Size\s+(\d+)\s+/Root\s+(\d+)\s+0\s+R\s+/Info\s+(\d+)\s+0\s+R\s*>>", data)
        self.assertIsNotNone(trailer_m)
        root_id = int(trailer_m.group(2))
        info_id = int(trailer_m.group(3))
        self.assertEqual(root_id, 1)
        objs = parse_pdf_objects(data)
        self.assertIn("/Type /Catalog", objs[root_id])
        self.assertIn("/Producer", objs[info_id])

    def test_26_direct_pdf_save_without_outlines(self):
        path = os.path.join(self.temp_dir, "no_outlines.pdf")
        pdf = pd.Pdf("A4", footer="Direct Save", style="A")
        pdf.new_page()
        pdf.text(50, 500, "Hello World")
        pdf.save(path, title="Manual Title")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        objs = parse_pdf_objects(data)
        cat_body = objs[1]
        self.assertNotIn("/Outlines", cat_body)
        info = parse_info_dict(data)
        self.assertEqual(info.get("Title"), "Manual Title")
        self.assertEqual(info.get("Author"), "Chilli Gavva")
        self.assertEqual(info.get("Creator"), "Chilli Gavva robot")

    def test_27_direct_pdf_save_with_custom_meta(self):
        path = os.path.join(self.temp_dir, "custom_meta.pdf")
        pdf = pd.Pdf("A4", footer="Custom", style="A")
        pdf.new_page()
        pdf.text(50, 500, "Page 1")
        meta = {
            "Title": "Custom Title",
            "Author": "Chilli Gavva",
            "Subject": "Custom Subject",
            "Keywords": ["kw1", "kw2"],
            "Creator": "Chilli Gavva robot",
        }
        pdf.save(path, meta=meta, outlines=[("Home", 0)])
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        info = parse_info_dict(data)
        self.assertEqual(info.get("Title"), "Custom Title")
        self.assertEqual(info.get("Subject"), "Custom Subject")
        self.assertEqual(info.get("Keywords"), "kw1, kw2")
        meta_out, items = parse_catalog_and_outlines(data)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Home")

    def test_28_no_money_amounts_in_pdf_or_meta(self):
        spec = self.make_sample_planner_spec()
        path = os.path.join(self.temp_dir, "no_currency.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        # Ensure no currency patterns
        self.assertIsNone(re.search(r"\$\d+", data))
        self.assertIsNone(re.search(r"\bUSD\s*\d+", data))
        self.assertIsNone(re.search(r"\bINR\s*\d+", data))
        self.assertIsNone(re.search(r"\bRs\.?\s*\d+", data))

    def test_29_product_builder_build_planner_generates_metadata(self):
        spec = self.make_sample_planner_spec("Product Builder Daily")
        files, notes = pb._build_planner(self.temp_dir, spec)
        self.assertEqual(len(files), 2)
        for fname in files:
            fpath = os.path.join(self.temp_dir, fname)
            with open(fpath, "rb") as f:
                data = f.read().decode("latin-1")
            info = parse_info_dict(data)
            self.assertEqual(info.get("Title"), "Product Builder Daily")
            self.assertEqual(info.get("Author"), "Chilli Gavva")
            self.assertEqual(info.get("Creator"), "Chilli Gavva robot")
            _, items = parse_catalog_and_outlines(data)
            self.assertGreater(len(items), 0)

    def test_30_product_builder_build_dated_planner_generates_metadata(self):
        spec = self.make_dated_planner_spec("Dated Builder Planner")
        files, notes = pb._build_dated_planner(self.temp_dir, spec)
        self.assertEqual(len(files), 2)
        for fname in files:
            fpath = os.path.join(self.temp_dir, fname)
            with open(fpath, "rb") as f:
                data = f.read().decode("latin-1")
            info = parse_info_dict(data)
            self.assertEqual(info.get("Title"), "Dated Builder Planner")
            self.assertEqual(info.get("Author"), "Chilli Gavva")
            self.assertEqual(info.get("Creator"), "Chilli Gavva robot")
            _, items = parse_catalog_and_outlines(data)
            self.assertGreaterEqual(len(items), 14)

    @unittest.skipUnless(pypdf is not None, "pypdf not installed in environment")
    def test_31_pypdf_reader_parses_metadata_and_outlines_if_installed(self):
        spec = self.make_sample_planner_spec("PyPdf Verified")
        path = os.path.join(self.temp_dir, "pypdf_check.pdf")
        pd.build_planner_pdf(path, spec, "A4")
        reader = pypdf.PdfReader(path)
        meta = reader.metadata
        self.assertEqual(meta.title, "PyPdf Verified")
        self.assertEqual(meta.author, "Chilli Gavva")
        self.assertEqual(meta.creator, "Chilli Gavva robot")
        outlines = reader.outline
        self.assertGreater(len(outlines), 0)

    def test_32_single_page_outline_boundary(self):
        path = os.path.join(self.temp_dir, "single.pdf")
        pdf = pd.Pdf("A4", style="A")
        pdf.new_page()
        pdf.save(path, title="Single Page", outlines=[("Sole Page", 0)])
        with open(path, "rb") as f:
            data = f.read().decode("latin-1")
        meta, items = parse_catalog_and_outlines(data)
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["prev"])
        self.assertIsNone(items[0]["next"])
        self.assertEqual(items[0]["title"], "Sole Page")


if __name__ == "__main__":
    unittest.main()
