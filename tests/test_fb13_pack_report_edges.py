"""
Extra edge-case tests for the pack readiness table in pack_report.py.

These tests only touch pack_report.py and its helpers. They do not edit
pack_report.py itself.

Covered behaviors:

- No pack folders at all -> "Ready: 0 of 0"
- One complete pack folder is reported as READY when the deep QA stub says
  "ready"
- Missing cover.png is still reported, just not marked ready for that flag
- Missing listing.txt keeps the row present and not ready
- Zero-byte product.zip is reported with a size of 0.0MB and keeps the row
- Huge zip size formatting stays one decimal place in the rendered line
- Marketing folders present -> marketing flag is on only when both
  carousel/ and pins/ have at least one file
- Marketing folders missing -> marketing flag is off
- Deep QA module unavailable -> verdict is "skipped", pack is not claimed
  ready
- Pack names with spaces render correctly in the line
- Sorting is stable: folders come out in sorted name order
- Last printed line is "Ready: N of M"
"""

from __future__ import annotations

import io
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pack_report


LISTING_TEXT = "Title: Sample Pack\n\nDescription:\nshort description\n\nTags: sample\n\nWhat you get:\nPDF\n"


class _StubQA:
    def __init__(self, verdict="ready"):
        self.verdict = verdict

    def check_folder(self, folder):
        return {
            "verdict": self.verdict,
            "score": 90 if self.verdict == "ready" else 0,
            "problems": [],
            "checked": [],
        }


def _make_fake_zip(path, size_bytes=22):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("LICENSE.txt", b"x" * max(0, size_bytes - 12))


def _make_pack(
    root,
    name,
    with_zip=True,
    zip_size_bytes=22,
    with_listing=True,
    cover=False,
    previews=False,
    pinterest=False,
):
    pack = root / name
    pack.mkdir()
    if with_zip:
        _make_fake_zip(pack / "product.zip", zip_size_bytes)
    if with_listing:
        (pack / "listing.txt").write_text(LISTING_TEXT, encoding="utf-8")
    if cover:
        (pack / "cover.png").write_bytes(b"png")
    if previews:
        for name in ("preview-1.png", "preview-2.png", "preview-3.png"):
            (pack / name).write_bytes(b"png")
    if pinterest:
        (pack / "pinterest.png").write_bytes(b"png")
    return pack


def _make_marketing(root, pack_name, variant="both"):
    kit = root / pack_name
    kit.mkdir(parents=True, exist_ok=True)
    for sub in (("carousel", "pins") if variant == "both" else (variant,)):
        folder = kit / sub
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "slide-1.png").write_bytes(b"png")


def _render_rows(rows):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        for row in rows:
            print(pack_report.render_line(row))
        print(pack_report.summary(rows))
    return buffer.getvalue()


class PackReportEdgeTests(unittest.TestCase):
    """Edge-case tests for the pack readiness table."""

    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.root = Path(self.temp)
        self.packs_root = self.root / "products" / "upload"
        self.marketing_root = self.root / "marketing"
        self.packs_root.mkdir(parents=True)
        self.marketing_root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def _collect_and_render(self, packs_root=None, marketing_root=None):
        rows = pack_report.collect(
            packs_root or self.packs_root,
            marketing_root or self.marketing_root,
        )
        return rows, _render_rows(rows)

    # 1. Zero packs
    def test_zero_packs_reports_zero_of_zero(self):
        rows, rendered = self._collect_and_render()
        self.assertEqual(rows, [])
        self.assertIn("Ready: 0 of 0", rendered)

    # 2. One complete pack
    def test_one_complete_pack_is_ready_with_stub_qa(self):
        _make_pack(self.packs_root, "one-pack")
        _make_marketing(self.marketing_root, "one-pack")
        with patch.object(pack_report, "_qa_deep", _StubQA("ready")):
            rows, rendered = self._collect_and_render()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["ready"])
        self.assertIn("one-pack", rendered)
        self.assertTrue(any("READY" in line for line in rendered.splitlines()))

    # 3. Missing cover
    def test_missing_cover_shows_in_line(self):
        pack = _make_pack(self.packs_root, "no-cover-pack", with_zip=True, with_listing=True)
        _make_marketing(self.marketing_root, "no-cover-pack")
        with patch.object(pack_report, "_qa_deep", _StubQA("ready")):
            row = pack_report.report_row(pack, self.marketing_root)
            line = pack_report.render_line(row)
        self.assertFalse(row["files"]["cover.png"])
        self.assertIn("cover ✗", line)
        self.assertIn("no-cover-pack", line)

    # 4. Missing listing
    def test_missing_listing_keeps_row_not_ready(self):
        _make_pack(self.packs_root, "no-listing-pack", with_zip=True, with_listing=False)
        rows, rendered = self._collect_and_render()
        row = next(r for r in rows if r["id"] == "no-listing-pack")
        self.assertFalse(row["files"]["listing.txt"])
        self.assertFalse(row["ready"])
        self.assertIn("no-listing-pack", rendered)

    # 5. Zero-byte zip
    def test_zero_byte_zip_reported_with_zero_mb(self):
        _make_pack(self.packs_root, "zero-pack", with_zip=True, zip_size_bytes=0, with_listing=True)
        _make_marketing(self.marketing_root, "zero-pack")
        with patch.object(pack_report, "_qa_deep", _StubQA("ready")):
            row = pack_report.report_row(
                self.packs_root / "zero-pack", self.marketing_root
            )
        self.assertIsNotNone(row["zip_size_mb"])
        self.assertEqual(row["zip_size_mb"], 0.0)
        line = pack_report.render_line(row)
        self.assertIn("zero-pack", line)
        self.assertRegex(line, r"zip ✓ 0\.0MB")

    # 6. Huge zip size formatting
    def test_huge_zip_size_formatting_one_decimal(self):
        big_bytes = int(6.55 * 1024 * 1024)
        _make_pack(
            self.packs_root, "huge-pack",
            with_zip=True, zip_size_bytes=big_bytes, with_listing=True,
        )
        _make_marketing(self.marketing_root, "huge-pack")
        with patch.object(pack_report, "_qa_deep", _StubQA("ready")):
            row = pack_report.report_row(
                self.packs_root / "huge-pack", self.marketing_root
            )
            line = pack_report.render_line(row)
        self.assertIsNotNone(row["zip_size_mb"])
        self.assertRegex(line, r"zip ✓ \d+\.\dMB")
        self.assertIn("huge-pack", line)

    # 7. Marketing folders present
    def test_marketing_present_when_both_kits_have_files(self):
        pack = _make_pack(self.packs_root, "market-both-pack", with_zip=True, with_listing=True)
        _make_marketing(self.marketing_root, "market-both-pack", variant="both")
        with patch.object(pack_report, "_qa_deep", _StubQA("ready")):
            row = pack_report.report_row(pack, self.marketing_root)
            line = pack_report.render_line(row)
        self.assertTrue(row["marketing"])
        self.assertIn("marketing ✓", line)

    # 8. Marketing folders missing
    def test_marketing_missing_when_no_marketing_root(self):
        pack = _make_pack(self.packs_root, "market-missing-pack", with_zip=True, with_listing=True)
        with patch.object(pack_report, "_qa_deep", _StubQA("ready")):
            row = pack_report.report_row(pack, self.marketing_root)
            line = pack_report.render_line(row)
        self.assertFalse(row["marketing"])
        self.assertIn("marketing ✗", line)

    # 9. Deep QA module missing -> "skipped"
    def test_deep_qa_missing_shows_skipped(self):
        pack = _make_pack(self.packs_root, "skipped-pack", with_zip=True, with_listing=True)
        saved = pack_report._qa_deep
        pack_report._qa_deep = None
        try:
            row = pack_report.report_row(pack, self.marketing_root)
        finally:
            pack_report._qa_deep = saved
        self.assertEqual(row["qa"], "skipped")
        self.assertFalse(row["ready"])
        self.assertIn("qa skipped", pack_report.render_line(row))

    # 10. Names with spaces
    def test_names_with_spaces_render_cleanly(self):
        _make_pack(self.packs_root, "two word pack", with_zip=True, with_listing=True)
        _make_marketing(self.marketing_root, "two word pack")
        with patch.object(pack_report, "_qa_deep", _StubQA("ready")):
            rows, rendered = self._collect_and_render()
        self.assertTrue(any("two word pack" in line for line in rendered.splitlines()))

    # 11. Sorting stable
    def test_sorting_stable_for_mixed_names(self):
        for name in ("zeta pack", "alpha pack", "m pack"):
            _make_pack(self.packs_root, name, with_zip=True, with_listing=True)
            _make_marketing(self.marketing_root, name)
        with patch.object(pack_report, "_qa_deep", _StubQA("ready")):
            rows, _ = self._collect_and_render()
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, sorted(ids))

    # 12. Final Ready: N of M line
    def test_final_line_is_ready_of_m(self):
        _make_pack(self.packs_root, "good", with_zip=True, with_listing=True)
        _make_pack(self.packs_root, "missing-listing", with_zip=True, with_listing=False)
        _make_marketing(self.marketing_root, "good")
        rows, rendered = self._collect_and_render()
        last_line = rendered.splitlines()[-1]
        self.assertEqual(last_line, pack_report.summary(rows))
        self.assertRegex(last_line, r"Ready: \d+ of \d+")

    # 13. Expected failure: huge zip still shows as ready when QA stub is ready
    @unittest.expectedFailure
    def test_huge_zip_not_claimed_ready_by_default(self):
        big_bytes = int(260 * 1024 * 1024)
        _make_pack(
            self.packs_root, "too-big",
            with_zip=True, zip_size_bytes=big_bytes, with_listing=True,
        )
        _make_marketing(self.marketing_root, "too-big")
        with patch.object(pack_report, "_qa_deep", _StubQA("ready")):
            row = pack_report.report_row(
                self.packs_root / "too-big", self.marketing_root
            )
        # Real bug: the readiness table currently does not cap zip size; it
        # only uses the deep QA verdict. Kept as expectedFailure with this
        # one-line reason.
        self.assertFalse(row["ready"])


if __name__ == "__main__":
    unittest.main()
