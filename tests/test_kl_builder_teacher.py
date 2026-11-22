"""KLA-1 tests: Teacher Lesson Plan Organizer builder."""
import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import builder_teacher
import format_registry

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    HAS_PILLOW = False


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pdf_pages(path):
    m = re.search(rb"/Count (\d+)", Path(path).read_bytes())
    return int(m.group(1)) if m else None


def _is_single_color(path):
    with Image.open(path) as im:
        colors = im.convert("RGB").getcolors(maxcolors=1_000_000)
    return colors is not None and len(colors) <= 1


@unittest.skipUnless(HAS_PILLOW, "Pillow is required for teacher planner builds")
class TeacherBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kla_teacher_"))
        cls.spec = {"title": "Lesson Plan Organizer for Teachers", "year": 2027}
        cls.files = builder_teacher.build(cls.spec, cls.tmp)
        cls.manifest = json.loads((cls.tmp / "manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_format_name(self):
        self.assertEqual(builder_teacher.FORMAT, "teacher_planner")

    def test_keywords(self):
        kws = {k.lower() for k in builder_teacher.KEYWORDS}
        for expected in ["lesson plan", "teacher planner", "lesson plan organizer", "classroom planner", "teacher organizer"]:
            self.assertIn(expected, kws)

    def test_build_returns_sorted_names(self):
        self.assertEqual(self.files, sorted(self.files))

    def test_all_expected_files_exist(self):
        expected = {
            "teacher_planner_a4.pdf", "teacher_planner_letter.pdf",
            "cover.png", "preview_1.png", "preview_2.png", "preview_3.png",
            "README.txt", "LICENSE.txt", "manifest.json",
        }
        self.assertEqual(set(self.files), expected)
        for name in expected:
            p = self.tmp / name
            self.assertTrue(p.is_file(), name)
            self.assertGreater(p.stat().st_size, 0, name)

    def test_pdfs_open_and_page_count(self):
        for paper in ("a4", "letter"):
            path = self.tmp / f"teacher_planner_{paper}.pdf"
            data = path.read_bytes()
            self.assertTrue(data.startswith(b"%PDF"), paper)
            self.assertEqual(_pdf_pages(path), 12, paper)

    def test_previews_not_one_colour(self):
        for name in ("cover.png", "preview_1.png", "preview_2.png", "preview_3.png"):
            with Image.open(self.tmp / name) as im:
                self.assertEqual(im.size, (1200, 800), name)
            self.assertFalse(_is_single_color(self.tmp / name), name)

    def test_previews_visually_distinct(self):
        hashes = {_sha256(self.tmp / n) for n in ("cover.png", "preview_1.png", "preview_2.png", "preview_3.png")}
        self.assertEqual(len(hashes), 4)

    def test_manifest_records_format_title_files_page_count(self):
        self.assertEqual(self.manifest["format"], "teacher_planner")
        self.assertEqual(self.manifest["title"], self.spec["title"])
        self.assertEqual(self.manifest["pages_per_pdf"], 12)
        self.assertEqual(self.manifest["files"], self.files)
        self.assertEqual(self.manifest["paper_sizes"], ["a4", "letter"])

    def test_readme_has_required_sections(self):
        text = (self.tmp / "README.txt").read_text(encoding="utf-8")
        self.assertIn("WHAT IS INSIDE", text)
        self.assertIn("HOW TO PRINT", text)
        self.assertIn("A4", text)
        self.assertIn("US Letter", text)

    def test_license_personal_use(self):
        text = (self.tmp / "LICENSE.txt").read_text(encoding="utf-8").lower()
        self.assertIn("personal use only", text)

    def test_no_currency_amounts(self):
        for name in ("README.txt", "LICENSE.txt", "manifest.json"):
            text = (self.tmp / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"(USD|INR|Rs\.?)\s*\d|\d[\d,.]*\s*(USD|INR|Rs\.?)", text), name)

    def test_classify_returns_format(self):
        format_registry.discover_builders(force_reload=True)
        self.assertEqual(format_registry.classify("Lesson Plan Organizer for teachers"), "teacher_planner")

    def test_can_build_true(self):
        format_registry.discover_builders(force_reload=True)
        self.assertTrue(format_registry.can_build({"kind": "teacher_planner"}))
        self.assertTrue(format_registry.can_build("teacher planner for classroom"))

    def test_normalize_spec_defaults(self):
        norm = builder_teacher.normalize_spec({})
        self.assertEqual(norm["title"], builder_teacher.DEFAULT_TITLE)
        self.assertEqual(norm["year"], 2027)
        self.assertTrue(norm["periods"])
        self.assertTrue(norm["subjects"])

    def test_normalize_spec_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            builder_teacher.normalize_spec("not a dict")
        with self.assertRaises(ValueError):
            builder_teacher.normalize_spec({"year": "soon"})

    def test_build_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d2:
            builder_teacher.build(self.spec, d2)
            for name in self.files:
                self.assertEqual(_sha256(self.tmp / name), _sha256(Path(d2) / name), name)


if __name__ == "__main__":
    unittest.main()
