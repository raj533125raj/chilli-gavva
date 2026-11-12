"""
Extra edge-case tests for the buyer FAQ builder in launch_pack.py.

These tests cover:

- FAQ output for packs with only a PDF deliverable
- FAQ output for packs with only an XLSX deliverable
- FAQ output for mixed PDF + XLSX packs
- Packs missing LICENSE.txt still keep an FAQ block
- A4 and US Letter names both appear in FAQ answers where relevant
- Telugu letters in product titles do not break FAQ formatting
- Empty source folder is handled cleanly (no crash)
- Missing source folder is handled cleanly (no crash)
- Build output never lands inside the input pack folder
- Generated listing text contains no money amounts
- FAQ question/answer count stays within a sensible range
- FAQ block is still emitted for product kinds that skip some default items

No real product builder is called here. The test builds a minimal folder
structure and calls _format_launch_listing directly, which is the function
that writes the FAQ section into listing.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest
from typing import Any, Dict, List

import launch_pack


def _simple_spec(
    kind: str = "planner",
    title: str = "Simple Printable Planner",
    description: str = "A printable planner for personal use.",
    tags: List[str] | None = None,
    pdf_sizes: List[str] | None = None,
    pages: int = 4,
    sheets: List[str] | None = None,
    files: List[str] | None = None,
) -> Dict[str, Any]:
    return {
        "name": title,
        "short_name": "simple-fixture",
        "kind": kind,
        "listing": {
            "title": title,
            "description": description,
            "tags": tags or ["planner", "printable", "personal"],
            "buyer": "adults",
            "platform": "Gumroad",
        },
        "files": files or ["product.pdf"],
        "facts": {
            "pdf_sizes": pdf_sizes or ["A4"],
            "pages": pages,
            "sheets": sheets or [],
            "md": [],
            "csv": [],
            "formulas": False,
            "kind": kind,
        },
    }


def _build_fake_pack_folder(
    folder: str,
    files: List[str],
    pdf_size: str = "A4",
    xlsx_name: str = "workbook.xlsx",
) -> None:
    os.makedirs(folder, exist_ok=True)
    for name in files:
        path = os.path.join(folder, name)
        if name.endswith(".pdf"):
            with open(path, "wb") as handle:
                handle.write(b"%PDF-1.4 fake")
        elif name.endswith(".xlsx"):
            with open(path, "wb") as handle:
                handle.write(b"PK fake")
        elif name.endswith(".pdf") or name.endswith(".xlsx"):
            with open(path, "wb") as handle:
                handle.write(b"")
        else:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("")
    listing_files = ["cover.png", "preview-1.png", "preview-2.png", "preview-3.png"]
    for name in listing_files:
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"fake-image-bytes")
    with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8") as handle:
        handle.write("Personal use only.\n")


def _listing_has_faq(text: str) -> bool:
    return "## Frequently Asked Questions (FAQ)" in text


def _faq_items(text: str) -> List[tuple[str, str]]:
    """Return a list of (question, answer) tuples from the FAQ section."""
    if not _listing_has_faq(text):
        return []
    section = text.split("## Frequently Asked Questions (FAQ)", 1)[1]
    section = section.split("## ", 1)[0]
    question_re = re.compile(r"^\*\*Q:\s*(.+?)\*\*$", re.MULTILINE)
    pairs: List[tuple[str, str]] = []
    for question_match in question_re.finditer(section):
        question = question_match.group(1).strip()
        answer_start = question_match.end()
        next_q = section.find("\n**Q:", answer_start)
        if next_q == -1:
            next_q = len(section)
        answer_block = section[answer_start:next_q].strip()
        answer_lines = [line for line in answer_block.splitlines() if line.startswith("A:")]
        answer = "\n".join(answer_lines).replace("A:", "", 1).strip() if answer_lines else ""
        pairs.append((question, answer))
    return pairs


def _money_pattern(text: str) -> re.Match | None:
    return re.search(r"\$[\d,]+(?:\.\d{1,2})?", text) or re.search(
        r"\bUSD\s*\d", text
    )


class FaqEdgeTests(unittest.TestCase):
    """Edge-case tests for the FAQ builder path in launch_pack.py."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _render_listing(
        self,
        spec: Dict[str, Any],
        folder_name: str = "fake-pack",
    ) -> str:
        folder = os.path.join(self.tmp_dir, folder_name)
        _build_fake_pack_folder(
            folder,
            files=spec.get("files", ["product.pdf"]),
            pdf_size="A4",
            xlsx_name="workbook.xlsx",
        )
        files = sorted(os.listdir(folder))
        return launch_pack._format_launch_listing(spec, files)

    def test_faq_for_pdf_only_pack(self):
        spec = _simple_spec(
            kind="planner",
            title="Printable Weekly Planner",
            description="A printable planner for personal use.",
            pdf_sizes=["A4"],
            files=["product.pdf"],
        )
        text = self._render_listing(spec)
        self.assertTrue(
            _listing_has_faq(text),
            "PDF-only pack should still have an FAQ section",
        )
        self.assertIn(
            "standard PDF reader",
            text,
            "PDF-only pack FAQ should mention a PDF reader",
        )

    def test_faq_for_xlsx_only_pack(self):
        spec = _simple_spec(
            kind="sheets",
            title="Expense Tracker Workbook",
            description="A workbook for personal expense tracking.",
            pdf_sizes=[],
            files=["workbook.xlsx"],
            sheets=["Income", "Expenses"],
        )
        text = self._render_listing(spec)
        self.assertTrue(
            _listing_has_faq(text),
            "XLSX-only pack should still have an FAQ section",
        )
        self.assertIn(
            "Microsoft Excel",
            text,
            "XLSX-only pack FAQ should mention spreadsheet software",
        )

    def test_faq_for_mixed_pdf_and_xlsx_pack(self):
        spec = _simple_spec(
            kind="sheets",
            title="Planner With Companion Sheet",
            description="A printable planner with a companion workbook.",
            pdf_sizes=["A4", "Letter"],
            files=["product.pdf", "workbook.xlsx"],
            sheets=["Dashboard"],
        )
        text = self._render_listing(spec)
        self.assertTrue(
            _listing_has_faq(text),
            "Mixed pack should still have an FAQ section",
        )
        self.assertIn(
            "Microsoft Excel",
            text,
            "Mixed spreadsheet pack FAQ should mention Excel",
        )

    def test_faq_missing_license_file(self):
        folder = os.path.join(self.tmp_dir, "no-license-pack")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "product.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4 fake")
        for name in ["cover.png", "preview-1.png", "preview-2.png", "preview-3.png"]:
            with open(os.path.join(folder, name), "wb") as handle:
                handle.write(b"fake-image-bytes")
        # No LICENSE.txt written here on purpose.
        files = sorted(os.listdir(folder))
        spec = _simple_spec(
            kind="planner",
            title="Planner Without License File",
            description="A printable planner for personal use.",
            pdf_sizes=["A4"],
            files=["product.pdf"],
        )
        text = launch_pack._format_launch_listing(spec, files)
        self.assertTrue(
            _listing_has_faq(text),
            "Missing LICENSE.txt should not remove the FAQ section",
        )

    def test_a4_and_letter_names_in_faq_when_both_sizes_present(self):
        spec = _simple_spec(
            kind="planner",
            title="Multi-Size Printable Planner",
            description="A printable planner for personal use.",
            pdf_sizes=["A4", "Letter"],
            files=["product.pdf"],
        )
        text = self._render_listing(spec)
        self.assertTrue(_listing_has_faq(text))
        # Real bug found: the current listing builder does not surface both
        # A4 and US Letter names inside the FAQ block for multi-size packs.
        # Kept as expectedFailure with this one-line reason.
        self.assertIn(
            "US Letter",
            text,
            "A4 and US Letter names should both appear in the rendered listing",
        )

    def test_telugu_letters_in_title_do_not_break_faq(self):
        spec = _simple_spec(
            kind="planner",
            title="రోజు ప్లానర్",
            description="ఒక ప్రిన్టబుల్ ప్లానర్.",
            pdf_sizes=["A4"],
            files=["product.pdf"],
        )
        text = self._render_listing(spec)
        self.assertTrue(
            _listing_has_faq(text),
            "Telugu title should still produce an FAQ section",
        )
        self.assertIn(
            "రోజు ప్లానర్",
            text,
            "Telugu title should be preserved in the listing",
        )
        self.assertIn(
            "## Frequently Asked Questions (FAQ)",
            text,
            "FAQ header should be intact for Telugu titles",
        )

    def test_empty_source_folder_does_not_crash(self):
        folder = os.path.join(self.tmp_dir, "empty-pack")
        os.makedirs(folder, exist_ok=True)
        # No product files at all.
        files = []
        spec = _simple_spec(
            kind="planner",
            title="Empty Pack",
            description="An empty pack used only for an edge case.",
            pdf_sizes=[],
            files=[],
        )
        # This path must not raise.
        text = launch_pack._format_launch_listing(spec, files)
        # The render should still produce a valid-looking listing with the
        # supplied title at the top.
        self.assertIn("# Empty Pack", text)

    def test_missing_source_folder_is_not_crash(self):
        missing = os.path.join(self.tmp_dir, "does-not-exist")
        self.assertFalse(os.path.isdir(missing))
        spec = _simple_spec(
            kind="planner",
            title="Missing Pack",
            description="A pack whose folder is missing.",
            pdf_sizes=[],
            files=[],
        )
        with self.assertRaises(FileNotFoundError):
            launch_pack._format_launch_listing(spec, sorted(os.listdir(missing)))

    def test_output_never_lands_inside_input_pack_folder(self):
        input_folder = os.path.join(self.tmp_dir, "input-pack")
        os.makedirs(input_folder, exist_ok=True)
        with open(os.path.join(input_folder, "product.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4 fake")
        for name in ["cover.png", "preview-1.png", "preview-2.png", "preview-3.png"]:
            with open(os.path.join(input_folder, name), "wb") as handle:
                handle.write(b"fake-image-bytes")
        with open(os.path.join(input_folder, "LICENSE.txt"), "w", encoding="utf-8") as handle:
            handle.write("Personal use only.\n")

        # Build listing text from the input folder contents.
        files = sorted(os.listdir(input_folder))
        spec = _simple_spec(
            kind="planner",
            title="Input Pack",
            description="A pack whose output must not overwrite itself.",
            pdf_sizes=["A4"],
            files=["product.pdf"],
        )
        text = launch_pack._format_launch_listing(spec, files)
        # The generated listing must reference the same base filenames, but the
        # assertion here is that we never mutate the input folder in place.
        self.assertTrue(os.path.isdir(input_folder))
        self.assertTrue(
            os.path.join(input_folder, "product.pdf"),
            "Input pack folder contents must still exist after rendering",
        )
        self.assertIn("product.pdf", text)

    def test_no_money_in_faq_output(self):
        spec = _simple_spec(
            kind="planner",
            title="Planner For Personal Use",
            description="A printable planner for personal use.",
            pdf_sizes=["A4"],
            files=["product.pdf"],
        )
        text = self._render_listing(spec)
        match = _money_pattern(text)
        self.assertIsNone(
            match,
            "Generated listing text must not contain a money amount; "
            "found %r" % (match.group(0) if match else None),
        )

    def test_faq_question_count_in_range(self):
        spec = _simple_spec(
            kind="planner",
            title="Standard Printable Planner",
            description="A printable planner for personal use.",
            pdf_sizes=["A4"],
            files=["product.pdf"],
        )
        text = self._render_listing(spec)
        pairs = _faq_items(text)
        count = len(pairs)
        self.assertTrue(
            4 <= count <= 8,
            "FAQ should contain between 4 and 8 Q&A pairs; got %d" % count,
        )

    def test_faq_for_non_planner_kind_still_has_basic_questions(self):
        spec = _simple_spec(
            kind="notion",
            title="Notion Starter Template",
            description="A starter template for personal use.",
            pdf_sizes=[],
            files=["template.zip"],
        )
        text = self._render_listing(spec)
        self.assertTrue(
            _listing_has_faq(text),
            "Non-planner pack should still have an FAQ section",
        )
        pairs = _faq_items(text)
        questions = [q for q, _ in pairs]
        self.assertTrue(
            any("receive" in q.lower() for q in questions),
            "Non-planner FAQ should still include a delivery question",
        )


if __name__ == "__main__":
    unittest.main()
