import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import builder_smallbiz
import format_registry

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    Image = None
    HAS_PILLOW = False


def pdf_pages(path):
    match = re.search(rb"/Count (\d+)", Path(path).read_bytes())
    return int(match.group(1)) if match else None


@unittest.skipUnless(HAS_PILLOW, "Pillow is required")
class SmallBusinessBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cp_smallbiz_test_"))
        cls.files = builder_smallbiz.build({}, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_format_and_keywords(self):
        self.assertEqual(builder_smallbiz.FORMAT, "small_business_log")
        words = set(builder_smallbiz.KEYWORDS)
        self.assertIn("bakery order", words)
        self.assertIn("inventory log", words)

    def test_all_files_exist(self):
        expected = {
            "small_business_log_a4.pdf", "small_business_log_letter.pdf",
            "cover.png", "preview_1.png", "preview_2.png", "preview_3.png",
            "README.txt", "LICENSE.txt", "manifest.json",
        }
        self.assertEqual(set(self.files), expected)
        for name in expected:
            self.assertTrue((self.tmp / name).is_file())
            self.assertGreater((self.tmp / name).stat().st_size, 0)

    def test_pdf_page_counts(self):
        for paper in ("a4", "letter"):
            path = self.tmp / f"small_business_log_{paper}.pdf"
            self.assertGreaterEqual(pdf_pages(path), 12)

    def test_pdf_headers(self):
        for paper in ("a4", "letter"):
            data = (self.tmp / f"small_business_log_{paper}.pdf").read_bytes()
            self.assertTrue(data.startswith(b"%PDF"))
            self.assertIn(b"Home Business Order", data)

    def test_previews_are_real_png_and_distinct(self):
        hashes = set()
        for name in ("cover.png", "preview_1.png", "preview_2.png", "preview_3.png"):
            path = self.tmp / name
            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreater(len(set(image.getdata())), 5)
            hashes.add(hash(path.read_bytes()))
        self.assertEqual(len(hashes), 4)

    def test_manifest_and_license(self):
        manifest = json.loads((self.tmp / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["format"], builder_smallbiz.FORMAT)
        self.assertGreaterEqual(manifest["pages_per_pdf"], 12)
        self.assertIn("personal use only", (self.tmp / "LICENSE.txt").read_text(encoding="utf-8").lower())

    def test_registry_classifies_custom_bakery(self):
        format_registry.reset_registry()
        format_registry.discover_builders(force_reload=True)
        self.assertEqual(format_registry.classify("Custom Bakery Order and Logistics Log"), builder_smallbiz.FORMAT)
        self.assertTrue(format_registry.can_build("Custom Bakery Order and Logistics Log"))


if __name__ == "__main__":
    unittest.main()
