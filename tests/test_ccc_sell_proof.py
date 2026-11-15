"""CCC-10: the robot's real path (build -> Truth Checker -> upload pack -> /sell) works for every type.

Everything is built in temp folders. Nothing is sent, uploaded or written into the repo folder.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import product_builder as pb
import sell_ready
import sell_proof

try:
    import PIL  # noqa: F401
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def staged(kind, tmp, approval_id=5):
    """Build `kind` and run the real truth_check_folder (which makes the upload pack)."""
    products = Path(tmp) / "products"
    products.mkdir(parents=True, exist_ok=True)
    folder = pb.build_product(sell_proof.spec_for(kind), approval_id, out_dir=str(products))
    result = pb.truth_check_folder(approval_id, folder, db_path=str(Path(tmp) / "t.db"), out_dir=str(products),
                                   notify_fn=lambda text: True)
    return products / folder, products / "upload" / folder, result


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class TruthSourceBugFixTests(unittest.TestCase):
    def test_upload_pack_is_checked_on_its_product_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, pack, result = staged("planner", tmp)
            self.assertEqual(result["verdict"], "ready")
            self.assertEqual(sell_ready.truth_source(pack), folder)
            self.assertEqual(sell_ready.inspect_truth_check(pack)["verdict"], "ready")

    def test_a_bad_product_still_fails_through_the_upload_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, pack, _result = staged("planner", tmp)
            a4 = next(folder.glob("*-A4.pdf"))
            a4.unlink()                                   # the listing still promises an A4 file
            check = sell_ready.inspect_truth_check(pack)
            self.assertEqual(check["verdict"], "needs fix")
            self.assertTrue(check["hard_fail"])
            self.assertTrue(any("A4" in issue for issue in check["issues"]), check["issues"])
            self.assertEqual(sell_ready.evaluate_pack(pack)["status"], sell_ready.STATUS_NOT_YET)

    def test_other_folders_are_checked_where_they_are(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "7-pack"
            plain.mkdir()
            self.assertEqual(sell_ready.truth_source(plain), plain)
            lone = Path(tmp) / "upload" / "8-pack"            # upload pack without a product folder
            lone.mkdir(parents=True)
            self.assertEqual(sell_ready.truth_source(lone), lone)
            empty = Path(tmp) / "9-pack"                      # product folder without listing.md
            empty.mkdir()
            (Path(tmp) / "upload" / "9-pack").mkdir()
            self.assertEqual(sell_ready.truth_source(Path(tmp) / "upload" / "9-pack"),
                             Path(tmp) / "upload" / "9-pack")

    def test_truth_check_rules_are_untouched(self):
        import truth_check
        self.assertEqual((truth_check.READY_MIN, truth_check.RED_FLAG, truth_check.EXTRA_PROOF_PROMISES), (80, 95, 3))


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class BuyerZipTests(unittest.TestCase):
    def test_resume_zip_gets_the_word_files_the_listing_promises(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, pack, _result = staged("resume", tmp)
            with zipfile.ZipFile(pack / "product.zip") as archive:
                names = set(archive.namelist())
            listed = pb.listed_files((folder / "listing.md").read_text(encoding="utf-8"))
            self.assertTrue(any(name.endswith(".docx") for name in listed))
            for name in listed:
                self.assertIn(name, names)

    def test_signature_zip_has_html_templates_and_pictures_but_no_listing_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            _folder, pack, _result = staged("email_signature", tmp)
            with zipfile.ZipFile(pack / "product.zip") as archive:
                names = set(archive.namelist())
            self.assertEqual(len([n for n in names if n.endswith(".html")]), 6)
            self.assertEqual(len([n for n in names if n.startswith("signature_") and n.endswith(".png")]), 6)
            self.assertFalse([n for n in names if n.startswith(pb.LISTING_IMAGE_PREFIXES)])
            self.assertNotIn("listing.md", names)

    def test_only_listed_files_are_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            product, pack = Path(tmp) / "p", Path(tmp) / "k"
            product.mkdir()
            pack.mkdir()
            (product / "listing.md").write_text("# T\n\n## What you get\n\n- `a.docx` — Word document\n"
                                                "- `cover.png` — PNG image\n\n## Tags (1)\n\n- t\n", encoding="utf-8")
            for name in ("a.docx", "b.docx", "cover.png"):
                (product / name).write_bytes(b"x")
            with zipfile.ZipFile(pack / "product.zip", "w") as archive:
                archive.writestr("README.md", "readme")
            self.assertEqual(pb.complete_buyer_zip(product, pack), ["a.docx"])
            with zipfile.ZipFile(pack / "product.zip") as archive:
                self.assertEqual(sorted(archive.namelist()), ["README.md", "a.docx"])
            self.assertEqual(pb.complete_buyer_zip(product, pack), [])        # second run adds nothing

    def test_notion_pack_is_staged_with_its_import_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            _folder, pack, result = staged("notion", tmp)
            self.assertEqual(result["verdict"], "ready", result["problems"])
            self.assertTrue((pack / "product.zip").is_file(), "upload_pack refused the Notion pack")
            with zipfile.ZipFile(pack / "product.zip") as archive:
                names = set(archive.namelist())
            self.assertIn("client-crm-notion-import.zip", names)
            self.assertIn("HOW_TO_IMPORT.md", names)
            self.assertIn("client-crm-import-guide-A4.pdf", names)
            self.assertIn("client-crm-import-guide-Letter.pdf", names)


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class NotionGuideTests(unittest.TestCase):
    def test_guide_has_a_page_per_notion_page_and_database(self):
        import truth_check
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / pb.build_product(sell_proof.spec_for("notion"), 3, out_dir=tmp)
            pages = truth_check._pdf_pages(str(folder / "client-crm-import-guide-A4.pdf"))
            self.assertEqual(len(pages), 1 + 2 + 2)
            texts = " | ".join(t for page in pages for *_rest, t in page["items"])
            for word in ("Import Guide", "Start Here", "Weekly Review", "Database: Clients", "Database: Projects",
                         "Select", "Date", "Client A"):
                self.assertIn(word, texts)

    def test_property_types(self):
        self.assertEqual(pb._property_type("Due", ["2026-01-01"]), "Date")
        self.assertEqual(pb._property_type("Status", ["Open"]), "Select")
        self.assertEqual(pb._property_type("Notes", ["x"]), "Text")
        self.assertEqual(pb._property_type("Hours", ["2", "3.5"]), "Number")

    def test_wrap_keeps_lines_inside_the_deep_qa_estimate(self):
        long_text = "illicit little lists fill title lines " * 20
        for line in pb._guide_wrap(long_text, 10, 500):
            self.assertLessEqual(len(line) * 10 * 0.5, 500)


class EmptyFolderTests(unittest.TestCase):
    def test_only_empty_folders_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "marketing", "images"))
            os.makedirs(os.path.join(tmp, "keep"))
            Path(tmp, "keep", "file.txt").write_text("x", encoding="utf-8")
            removed = pb.remove_empty_dirs(tmp)
            self.assertEqual({os.path.relpath(p, tmp) for p in removed},
                             {os.path.join("marketing", "images"), "marketing"})
            self.assertTrue(Path(tmp, "keep", "file.txt").is_file())

    @unittest.skipUnless(HAS_PIL, "needs Pillow")
    def test_flashcards_product_folder_is_flat_so_upload_pack_accepts_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, pack, _result = staged("flashcards", tmp)
            self.assertFalse([p for p in folder.iterdir() if p.is_dir()])
            self.assertTrue((pack / "product.zip").is_file())


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class SellProofToolTests(unittest.TestCase):
    def test_three_types_are_sellable_through_the_real_path(self):
        rows = sell_proof.run(["notion", "resume", "wall_art"])
        for row in rows:
            with self.subTest(kind=row["type"]):
                self.assertIn(row["status"], ("SELLABLE", "ALMOST"), row["fixes"])
                self.assertEqual(row["zip"], "ok")
                self.assertTrue(row["sell_message"])
                self.assertTrue(row["truth"].startswith("ready"))

    def test_table_and_exit_code(self):
        rows = [{"type": "planner", "status": "SELLABLE", "truth": "ready 100", "zip": "ok", "sell_message": True,
                 "fixes": []},
                {"type": "notion", "status": "NOT YET", "truth": "needs fix 80", "zip": "-", "sell_message": False,
                 "fixes": ["Upload pack refused"]}]
        text = sell_proof.table(rows)
        self.assertIn("1/2 SELLABLE or ALMOST", text)
        self.assertIn("Upload pack refused", text)

    def test_main_runs_one_type_and_prints_json(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = sell_proof.main(["--only", "habit_tracker", "--json"])
        self.assertEqual(code, 0)
        self.assertIn('"status": "SELLABLE"', out.getvalue())

    def test_keep_refuses_the_repo_folder_and_unknown_types(self):
        with self.assertRaises(SystemExit):
            sell_proof.run(["sheets"], keep=str(ROOT / "tmp_sell_proof"))
        self.assertFalse((ROOT / "tmp_sell_proof").exists())
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            sell_proof.main(["--only", "teapot"])

    def test_every_type_has_a_spec(self):
        for kind in sell_proof.FORMAT_ORDER:
            self.assertEqual(sell_proof.spec_for(kind)["kind"], kind)


if __name__ == "__main__":
    unittest.main()
