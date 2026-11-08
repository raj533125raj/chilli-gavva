"""Tests for pack_report.py: upload-pack readiness table, QA verdict, marketing kits."""
import io
import itertools
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pack_report

LISTING = ("Title: Focus Planner Printable Pack\n\nDescription:\n"
           + ("calm desk organisation " * 16) + "\n\nTags: focus\n\nWhat you get:\nA4 PDF\n")


class _StubQA:
    def __init__(self, verdict="ready"):
        self.verdict = verdict

    def check_folder(self, folder):
        return {"verdict": self.verdict, "score": 90 if self.verdict == "ready" else 0,
                "problems": [], "checked": []}


def make_zip(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("LICENSE.txt", "personal use licence")
    return path


class PackReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packs_root = self.root / "exported_content" / "products" / "upload"
        self.marketing_root = self.root / "exported_content" / "marketing"
        self.packs_root.mkdir(parents=True)
        self.marketing_root.mkdir(parents=True)
        self._counter = itertools.count(1)

    def pack(self, name=None, with_zip=True, with_listing=True,
             covers_previews_pinterest=False):
        name = name or f"pack-{next(self._counter)}"
        target = self.packs_root / name
        target.mkdir()
        if with_zip:
            make_zip(target / "product.zip")
        if with_listing:
            (target / "listing.txt").write_text(LISTING, encoding="utf-8")
        if covers_previews_pinterest:
            for name_png in ("cover.png", "preview-1.png", "preview-2.png",
                             "preview-3.png", "pinterest.png"):
                (target / name_png).write_bytes(b"png")
        return target

    def marketing(self, pack_id, variant="both"):
        kit = self.marketing_root / pack_id
        for name in (("carousel", "pins") if variant == "both" else (variant,)):
            folder = kit / name
            folder.mkdir(parents=True)
            (folder / "slide-1.png").write_bytes(b"png")

    def row(self, **kwargs):
        return pack_report.report_row(self.pack(**kwargs), self.marketing_root)

    def test_ready_row_with_files_and_real_qa(self):
        row = self.row()
        self.assertEqual(row["qa"], "ready")
        self.assertTrue(row["ready"])

    def test_missing_zip_not_ready(self):
        row = self.row(with_zip=False)
        self.assertFalse(row["files"]["product.zip"])
        self.assertFalse(row["ready"])

    def test_missing_listing_not_ready(self):
        row = self.row(with_listing=False)
        self.assertFalse(row["files"]["listing.txt"])
        self.assertFalse(row["ready"])

    def test_file_flags_match_disk(self):
        row = self.row(covers_previews_pinterest=True)
        self.assertTrue(all(row["files"].values()))
        bare = self.row()
        self.assertFalse(bare["files"]["cover.png"])
        self.assertFalse(bare["files"]["pinterest.png"])
        self.assertFalse(bare["files"]["preview-2.png"])

    def test_zip_size_one_decimal(self):
        row = self.row(name="size-pack")
        self.assertIsInstance(row["zip_size_mb"], float)
        line = pack_report.render_line(row)
        self.assertRegex(line, r"zip ✓ \d+\.\dMB")

    def test_line_shows_all_flags_and_ready_status(self):
        pack = self.pack("flags-pack", covers_previews_pinterest=True)
        self.marketing("flags-pack")
        with patch.object(pack_report, "_qa_deep", _StubQA("ready")):
            line = pack_report.render_line(pack_report.report_row(pack, self.marketing_root))
        for flag in ("zip ✓", "cover ✓", "preview-1 ✓", "preview-2 ✓",
                     "preview-3 ✓", "pinterest ✓", "listing ✓",
                     "qa ready", "marketing ✓  READY"):
            self.assertIn(flag, line)
        self.assertIn("✗", pack_report.render_line(self.row(name="flags-min")))
        self.assertTrue(line.rstrip().endswith("READY"))

    def test_not_ready_row_when_qa_says_fix(self):
        pack = self.pack()
        (pack / "product.zip").write_bytes(b"not a real zip")
        row = pack_report.report_row(pack, self.marketing_root)
        self.assertEqual(row["qa"], "fix")
        self.assertFalse(row["ready"])

    def test_qa_skipped_when_module_unavailable(self):
        saved = pack_report._qa_deep
        pack_report._qa_deep = None
        try:
            row = self.row()
            self.assertEqual(row["qa"], "skipped")
            self.assertFalse(row["ready"])  # unknown QA -> not claimed ready
            saved_verdict = pack_report.deep_qa_verdict(self.pack("extra"))
            self.assertEqual(saved_verdict, "skipped")
        finally:
            pack_report._qa_deep = saved

    def test_marketing_vector_needs_both_kits(self):
        pack = self.pack()
        self.assertFalse(pack_report.marketing_ready(pack, self.marketing_root))
        self.marketing(pack.name, variant="carousel")
        self.assertFalse(pack_report.marketing_ready(pack, self.marketing_root))
        self.marketing(pack.name, variant="pins")
        self.assertTrue(pack_report.marketing_ready(pack, self.marketing_root))

    def test_marketing_empty_kit_folders_do_not_count(self):
        pack = self.pack()
        kit = self.marketing_root / pack.name / "carousel"
        kit.mkdir(parents=True)
        (self.marketing_root / pack.name / "pins").mkdir(parents=True)
        self.assertFalse(pack_report.marketing_ready(pack, self.marketing_root))

    def test_collect_sorted_and_prefixed_marketing_root(self):
        self.pack("zeta-pack")
        self.pack("alpha-pack")
        self.marketing("alpha-pack")
        self.marketing("zeta-pack")
        rows = pack_report.collect(self.packs_root, self.marketing_root)
        self.assertEqual([row["id"] for row in rows], ["alpha-pack", "zeta-pack"])

    def test_summary_counts_ready_packs(self):
        self.pack("good-pack")
        self.pack("loose-pack", with_listing=False)
        rows = pack_report.collect(self.packs_root, self.marketing_root)
        self.assertEqual(pack_report.summary(rows), "Ready: 1 of 2")

    def test_missing_products_root_gives_zero(self):
        rows = pack_report.collect(self.root / "nope", self.marketing_root)
        self.assertEqual(rows, [])
        self.assertEqual(pack_report.summary(rows), "Ready: 0 of 0")

    def test_cli_prints_lines_then_summary(self):
        self.pack("cli-pack", covers_previews_pinterest=True)
        self.marketing("cli-pack")
        buffer = io.StringIO()
        with redirect_stdout(buffer), patch.object(pack_report, "_qa_deep", _StubQA("ready")):
            code = pack_report.main([str(self.packs_root),
                                     "--marketing-dir", str(self.marketing_root)])
        out = buffer.getvalue().splitlines()
        self.assertEqual(code, 0)
        self.assertTrue(out[-1] == "Ready: 1 of 1")
        self.assertTrue(out[-2].endswith("READY"))
        self.assertIn("cli-pack", out[0])

    def test_cli_quiet_about_titles_and_money(self):
        self.pack("quiet-pack")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            pack_report.main([str(self.packs_root), "--marketing-dir",
                              str(self.marketing_root)])
        out = buffer.getvalue()
        self.assertNotIn("$", out)
        self.assertNotIn("Focus Planner", out)

    def test_qa_uses_check_that_never_writes_into_the_pack(self):
        pack = self.pack()
        before = sorted(p.name for p in pack.iterdir())
        pack_report.deep_qa_verdict(pack)
        self.assertEqual(before, sorted(p.name for p in pack.iterdir()))


if __name__ == "__main__":
    unittest.main()
