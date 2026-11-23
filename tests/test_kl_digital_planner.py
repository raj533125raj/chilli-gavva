"""KLD-2 tests: hyperlinked digital planner builder."""
import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import builder_digital_planner
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
    data = Path(path).read_bytes()
    m = re.search(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data)
    if m:
        return int(m.group(1))
    # fallback: count page objects
    return len(re.findall(rb"/Type\s*/Page\b(?!s)", data))


def _is_mostly_one_color(path, threshold=0.92):
    with Image.open(path) as im:
        colors = im.convert("RGB").getcolors(maxcolors=10_000_000) or []
    total = sum(c for c, _ in colors)
    dominant = max((c for c, _ in colors), default=0)
    return (dominant / max(1, total)) >= threshold


def _pdf_link_targets(path):
    """Return (page_count, links) where links are (page_obj_num, target_obj_num) from annots."""
    data = Path(path).read_bytes().decode("latin-1", "replace")
    # map object number -> object body
    objs = {}
    for m in re.finditer(r"(\d+)\s+0\s+obj(.*?)endobj", data, re.DOTALL):
        objs[int(m.group(1))] = m.group(2)
    # page objects
    page_objs = [n for n, body in objs.items() if re.search(r"/Type\s*/Page\b(?!s)", body)]
    page_set = set(page_objs)
    links = []
    for n in page_objs:
        body = objs[n]
        annots_m = re.search(r"/Annots\s*\[(.*?)\]", body, re.DOTALL)
        if not annots_m:
            continue
        for ref in re.finditer(r"(\d+)\s+0\s+R", annots_m.group(1)):
            ann_id = int(ref.group(1))
            ann_body = objs.get(ann_id, "")
            dest_m = re.search(r"/Dest\s*\[\s*(\d+)\s+0\s+R", ann_body)
            if dest_m:
                links.append((n, int(dest_m.group(1))))
    return page_objs, links


@unittest.skipUnless(HAS_PILLOW, "Pillow required")
class DigitalPlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kld_digital_"))
        cls.spec = {"title": "Digital iPad Planner", "year": 2027}
        cls.files = builder_digital_planner.build(cls.spec, cls.tmp)
        cls.manifest = json.loads((cls.tmp / "manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_format_and_keywords(self):
        self.assertEqual(builder_digital_planner.FORMAT, "digital_planner")
        kws = {k.lower() for k in builder_digital_planner.KEYWORDS}
        for k in ["digital planner", "ipad planner", "goodnotes planner", "hyperlinked planner", "tablet planner", "notability planner"]:
            self.assertIn(k, kws)

    def test_build_files(self):
        expected = {
            "digital_planner.pdf", "cover.png", "preview_1.png", "preview_2.png", "preview_3.png",
            "README.txt", "LICENSE.txt", "manifest.json",
        }
        self.assertEqual(set(self.files), expected)
        for n in expected:
            self.assertTrue((self.tmp / n).is_file(), n)
            self.assertGreater((self.tmp / n).stat().st_size, 0, n)

    def test_pdf_opens_and_page_count(self):
        path = self.tmp / "digital_planner.pdf"
        data = path.read_bytes()
        self.assertTrue(data.startswith(b"%PDF"))
        pages = _pdf_pages(path)
        self.assertGreaterEqual(pages, 24)
        self.assertEqual(pages, self.manifest["page_count"])

    def test_every_tab_link_points_to_existing_page(self):
        page_objs, links = _pdf_link_targets(self.tmp / "digital_planner.pdf")
        self.assertGreater(len(page_objs), 0)
        self.assertGreater(len(links), 0)
        page_set = set(page_objs)
        for src, tgt in links:
            self.assertIn(src, page_set)
            self.assertIn(tgt, page_set)
        # every page should have tab links (index + 12 months = 13)
        links_by_src = {}
        for src, tgt in links:
            links_by_src.setdefault(src, set()).add(tgt)
        for p in page_objs:
            self.assertGreaterEqual(len(links_by_src.get(p, set())), 13, f"page obj {p} missing tab links")

    def test_classify(self):
        format_registry.discover_builders(force_reload=True)
        self.assertEqual(format_registry.classify("Digital iPad Planner hyperlinked"), "digital_planner")
        self.assertTrue(format_registry.can_build({"kind": "digital_planner"}))

    def test_previews_distinct_and_not_mostly_one_color(self):
        names = ["cover.png", "preview_1.png", "preview_2.png", "preview_3.png"]
        hashes = [_sha256(self.tmp / n) for n in names]
        self.assertEqual(len(set(hashes)), 4)
        self.assertNotEqual(hashes[0], hashes[1])
        for n in names:
            with Image.open(self.tmp / n) as im:
                self.assertGreaterEqual(im.size[0], 1200)
                self.assertGreaterEqual(im.size[1], 800)
            self.assertFalse(_is_mostly_one_color(self.tmp / n), n)

    def test_manifest(self):
        self.assertEqual(self.manifest["format"], "digital_planner")
        self.assertTrue(self.manifest["hyperlinked"])
        self.assertEqual(self.manifest["title"], self.spec["title"])

    def test_readme_has_note_app_instructions(self):
        text = (self.tmp / "README.txt").read_text(encoding="utf-8")
        self.assertIn("GoodNotes", text)
        self.assertIn("HOW TO USE IN A NOTE APP", text)
        self.assertIn("personal use only", (self.tmp / "LICENSE.txt").read_text(encoding="utf-8").lower())

    def test_no_currency_amounts(self):
        for n in ["README.txt", "LICENSE.txt", "manifest.json"]:
            text = (self.tmp / n).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"(USD|INR|Rs\.?)\s*\d|\d[\d,.]*\s*(USD|INR|Rs\.?)", text), n)

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as d2:
            builder_digital_planner.build(self.spec, d2)
            for n in self.files:
                self.assertEqual(_sha256(self.tmp / n), _sha256(Path(d2) / n), n)


if __name__ == "__main__":
    unittest.main()
