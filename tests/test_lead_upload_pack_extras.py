"""Lead fix (2026-11-15): upload_pack.make puts every listed buyer file into product.zip.

Before, make() zipped only PDF / XLSX / README / LICENSE, so /upload (tg_upload calls make() directly)
gave buyers a zip without the resume .docx, the signature .html, the Notion import .zip or the art PNGs.
"""
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


@unittest.skipUnless(HAVE_PIL, "Pillow needed to build products")
class UploadPackListedExtras(unittest.TestCase):
    def build(self, kind):
        import product_builder as pb
        from tools import sell_proof
        root = Path(self.tmp.name)
        products = root / "products"
        products.mkdir()
        db = root / "agent.db"
        with unittest.mock.patch.object(pb, "PRODUCTS_DIR", str(products)):
            name = pb.build_product(sell_proof.spec_for(kind), 7, out_dir=str(products))
        return products / name

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def check_kind(self, kind):
        import product_builder as pb
        import truth_check
        import upload_pack
        folder = self.build(kind)
        if truth_check.check(str(folder)).get("verdict") != "ready":
            self.skipTest(f"{kind} not ready in this environment")
        out = Path(self.tmp.name) / "fresh-upload"
        pack = upload_pack.make(str(folder), str(out))
        wanted = pb.listed_files((folder / "listing.md").read_text(encoding="utf-8"))
        with zipfile.ZipFile(pack / "product.zip") as archive:
            names = set(archive.namelist())
        missing = [n for n in wanted if (folder / n).is_file()
                   and not n.lower().startswith(pb.LISTING_IMAGE_PREFIXES) and n not in names]
        self.assertEqual(missing, [], f"{kind}: listed buyer files missing from product.zip")
        self.assertFalse(any(n.lower().startswith(pb.LISTING_IMAGE_PREFIXES) for n in names),
                         "listing pictures must not go into the buyer zip")
        return names

    def test_resume_docx_in_zip(self):
        names = self.check_kind("resume")
        self.assertTrue(any(n.lower().endswith(".docx") for n in names), names)

    def test_email_signature_html_in_zip(self):
        names = self.check_kind("email_signature")
        self.assertTrue(any(n.lower().endswith(".html") for n in names), names)

    def test_wall_art_listed_files_in_zip(self):
        self.check_kind("wall_art")

    def test_notion_listed_files_in_zip(self):
        self.check_kind("notion")

    def test_symlink_and_unlisted_files_never_added(self):
        import upload_pack
        src = Path(self.tmp.name) / "p"
        src.mkdir()
        (src / "secret.txt").write_text("x")
        os.symlink(src / "secret.txt", src / "link.txt")
        listing = "## What you get\n- `link.txt` x\n- `cover.png` y\n- `missing.docx` z\n"
        self.assertEqual(upload_pack._listed_extras(src, listing, set()), [])


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
