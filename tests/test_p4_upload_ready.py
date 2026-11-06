"""Production builder to hand-upload pack regressions, using only temporary files."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

import guardrails
import launch_pack
import product_builder as pb
import truth_check
import upload_pack


ROOT = Path(__file__).resolve().parents[1]


def bible(name="paycheck-budget-planner"):
    return json.loads((ROOT / "specs" / "bible" / (name + ".json")).read_text())


class UploadReadyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for target in ("mockups.write_mockups", "mockups.write_pins"):
            patch = mock.patch(target, return_value=[])
            patch.start()
            self.addCleanup(patch.stop)
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection"):
            patch = mock.patch(target, side_effect=AssertionError("Network forbidden"))
            patch.start()
            self.addCleanup(patch.stop)

    def build(self, spec, name=None):
        folder = pb.build_product(spec, spec["approval_id"], str(self.root / "builds"), folder_name=name)
        return self.root / "builds" / folder

    def test_combined_product_real_files_listing_and_export(self):
        spec = bible()
        original = copy.deepcopy(spec)
        folder = self.build(spec)
        self.assertEqual(spec, original, "Building must not mutate caller-owned specs")
        self.assertEqual(len(list((self.root / "builds").iterdir())), 1)
        self.assertEqual(len(list(folder.glob("*.pdf"))), 2)
        self.assertEqual(len(list(folder.glob("*.xlsx"))), 1)
        body = (folder / "listing.md").read_text()
        self.assertIn("## What you get", body)
        self.assertIn("tabs: Instructions, Ledger, Bills", body)
        self.assertNotIn("Price:", body)
        upload_pack._check_money(body, "listing")
        verdict = truth_check.check(str(folder))
        self.assertEqual(verdict["verdict"], "ready", verdict["problems"])
        pack = upload_pack.make(folder, self.root / "packs")
        with zipfile.ZipFile(pack / "product.zip") as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {p.name for p in folder.iterdir()
                             if p.suffix in (".pdf", ".xlsx") or p.name in ("README.md", "LICENSE.txt")})
        self.assertIn("Price: set by owner", (pack / "checklist.txt").read_text())
        self.assertNotIn("Price:", (pack / "listing.txt").read_text())

    def test_all_ten_specs_use_production_builder_and_real_export_gates(self):
        paths = sorted((ROOT / "specs" / "bible").glob("*.json"))
        self.assertEqual(len(paths), 10)
        for path in paths:
            with self.subTest(product=path.stem):
                spec = json.loads(path.read_text())
                folder = self.build(spec)
                facts = pb._delivery_facts(str(folder))
                listing = (folder / "listing.md").read_text()
                for content in facts["contents"]:
                    self.assertIn(content, listing)
                result = truth_check.check(str(folder))
                self.assertEqual(result["verdict"], "ready", result["problems"])
                self.assertTrue((upload_pack.make(folder, self.root / "packs") / "product.zip").is_file())

    def test_single_formats_stay_single(self):
        for name, extensions in (("weekly-planner", {".pdf"}), ("pricing-calculator", {".xlsx"})):
            with self.subTest(name=name):
                folder = self.build(bible(name))
                self.assertEqual({p.suffix for p in folder.iterdir() if p.suffix in (".pdf", ".xlsx")}, extensions)

    def test_sheets_primary_spec_with_pages_also_builds_both(self):
        spec = bible()
        spec["kind"] = "sheets"
        folder = self.build(spec)
        self.assertEqual(len(list(folder.glob("*.pdf"))), 2)
        self.assertEqual(len(list(folder.glob("*.xlsx"))), 1)
        self.assertEqual(truth_check.check(str(folder))["verdict"], "ready")

    def test_parse_keeps_optional_second_format(self):
        spec = bible()
        parsed, reason = pb.parse_spec(json.dumps(spec), "ADHD Planners Agent")
        self.assertIsNone(reason)
        self.assertIn("sheets", parsed)
        parsed, reason = pb.parse_spec(json.dumps(spec), "Budget Sheets Agent")
        self.assertIsNone(reason)
        self.assertIn("pages", parsed)

    def test_pdf_count_uses_built_overflow_pages(self):
        spec = bible("weekly-planner")
        spec["listing"]["description"] = "Includes 9999 printable pages in A4 and US Letter."
        spec["pages"] = [{"title": "Notes", "sections": [
            {"heading": "Writing", "type": "lines", "count": 12} for _ in range(12)]}]
        folder = self.build(spec)
        facts = pb._delivery_facts(str(folder))
        self.assertGreater(facts["pages"], len(spec["pages"]) + 2)
        body = (folder / "listing.md").read_text()
        self.assertIn(f"Includes {facts['pages']} printable pages", body)
        self.assertNotIn("9999", body)
        launch = launch_pack._format_launch_listing(spec, facts["files"], facts)
        self.assertIn(f"Includes {facts['pages']} printable pages", launch)
        self.assertNotIn("9999", launch)

    def test_pdf_size_comes_from_mediabox_even_when_filename_is_misleading(self):
        spec = bible("weekly-planner")
        folder = self.root / "files"
        folder.mkdir()
        pb.build_planner_pdf(str(folder / "claimed-A4.pdf"), spec, "Letter")
        facts = pb._delivery_facts(str(folder))
        self.assertEqual(facts["pdf_sizes"], ["Letter"])
        self.assertIn("US Letter", facts["contents"][0])
        self.assertNotIn(", A4", facts["contents"][0])
        for writer in (lambda: pb._delivery_text("A4 and US Letter pages", spec, facts),
                       lambda: launch_pack._format_launch_listing(spec, [], facts)):
            self.assertNotIn("A4", writer().replace("claimed-A4.pdf", ""))

    def test_unknown_pdf_mediabox_does_not_invent_a_standard_size(self):
        spec = bible("weekly-planner")
        folder = self.root / "files"
        folder.mkdir()
        path = folder / "claimed-A4.pdf"
        pb.build_planner_pdf(str(path), spec, "A4")
        path.write_bytes(path.read_bytes().replace(b"595.28 841.89", b"500.00 700.00"))
        facts = pb._delivery_facts(str(folder))
        self.assertEqual(facts["pdf_sizes"], [])
        self.assertIn("paper size unspecified", facts["contents"][0])

    def test_paper_variants_can_have_different_page_counts(self):
        spec = json.loads((ROOT / "specs/samples/2027-28-teacher-planner-us-test.json").read_text())
        spec["approval_id"] = 42
        folder = self.build(spec)
        facts = pb._delivery_facts(str(folder))
        body = (folder / "listing.md").read_text()
        self.assertIn("page count: 17, A4", body)
        self.assertIn("page count: 18, US Letter", body)
        self.assertEqual(truth_check.check(str(folder))["verdict"], "ready")

    def test_sheet_listing_does_not_repeat_spec_pdf_counts_or_sizes(self):
        spec = bible("pricing-calculator")
        spec["listing"]["description"] += " Includes 999 printable pages in A4 and US Letter."
        folder = self.build(spec)
        facts = pb._delivery_facts(str(folder))
        for body in ((folder / "listing.md").read_text(), launch_pack._format_launch_listing(spec, facts["files"], facts)):
            self.assertNotIn("999", body)
            self.assertNotIn("A4", body)
            self.assertNotIn("US Letter", body)
        self.assertEqual(facts["pdf_sizes"], [])

    def test_listing_money_in_any_field_is_refused_before_listing_is_written(self):
        for field in ("title", "description", "buyer", "tags"):
            for amount in ("$8", "0.00 USD", "₹499", "&#36;8", "**$**8"):
                with self.subTest(field=field, amount=amount):
                    spec = bible("pricing-calculator")
                    spec["listing"][field] = [amount] if field == "tags" else amount
                    name = f"refusal-{field}-{len(list(self.root.iterdir()))}"
                    # A unique folder for each attempt, even though product files may remain on refusal.
                    with tempfile.TemporaryDirectory(dir=self.root) as out:
                        with self.assertRaisesRegex(ValueError, "Money amount found"):
                            pb.build_product(spec, 42, out_dir=out, folder_name=name)
                        self.assertFalse((Path(out) / name / "listing.md").exists())
                    with self.assertRaisesRegex(ValueError, "Money amount found"):
                        launch_pack._format_launch_listing(spec, [])

    def test_launch_pack_rewrites_with_real_combined_inventory(self):
        spec = bible()
        db = str(self.root / "fixture.db")
        conn = guardrails.connect(db)
        conn.execute("INSERT INTO approvals (id, kind, title, payload, status) VALUES (42, 'public_draft', ?, '', 'approved')",
                     ("Draft: [ADHD Planners Agent] Paycheck planner",))
        conn.commit()
        conn.close()
        with mock.patch.object(launch_pack, "is_eligible", return_value=True):
            result = launch_pack.build_launch_pack(42, db_path=db, out_dir=str(self.root / "builds"), spec=spec, max_fix_rounds=1)
        folder = self.root / "builds" / result["folder"]
        body = (folder / "listing.md").read_text()
        self.assertIn("## What you get", body)
        self.assertIn("tabs: Instructions, Ledger, Bills", body)
        self.assertIn(".xlsx", body)
        self.assertIn(".pdf", body)
        self.assertEqual(truth_check.check(str(folder))["verdict"], "ready")
        self.assertTrue((upload_pack.make(folder, self.root / "packs") / "product.zip").is_file())


if __name__ == "__main__":
    unittest.main()
