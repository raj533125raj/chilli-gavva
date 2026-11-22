import hashlib
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import builder_biz_sheets
import format_registry

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    Image = None
    HAS_PILLOW = False


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@unittest.skipUnless(HAS_PILLOW, "Pillow is required")
class BizSheetsBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cp_biz_sheets_test_"))
        cls.files = builder_biz_sheets.build({}, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_format_and_keywords(self):
        self.assertEqual(builder_biz_sheets.FORMAT, "biz_sheets")
        words = set(builder_biz_sheets.KEYWORDS)
        self.assertIn("order tracker spreadsheet", words)
        self.assertIn("inventory spreadsheet", words)
        self.assertIn("profit calculator sheet", words)

    def test_all_expected_files_exist(self):
        expected = {
            "biz_sheets.xlsx", "Start_Here.csv", "Orders.csv", "Inventory.csv",
            "Customers.csv", "Expenses.csv", "Monthly_Summary.csv",
            "cover.png", "preview_1.png", "preview_2.png", "preview_3.png",
            "README.txt", "LICENSE.txt", "manifest.json",
        }
        self.assertEqual(set(self.files), expected)
        for name in expected:
            path = self.tmp / name
            self.assertTrue(path.is_file(), name)
            self.assertGreater(path.stat().st_size, 0, name)

    def test_xlsx_has_all_tabs_and_formula_cells(self):
        path = self.tmp / "biz_sheets.xlsx"
        with zipfile.ZipFile(path) as archive:
            workbook = archive.read("xl/workbook.xml").decode("utf-8")
            for tab in ("Start Here", "Orders", "Inventory", "Customers", "Expenses", "Monthly Summary"):
                self.assertIn(f'name="{tab}"', workbook)
            sheet_xml = "\n".join(
                archive.read(f"xl/worksheets/sheet{i}.xml").decode("utf-8")
                for i in range(1, 7)
            )
            self.assertIn("<f>", sheet_xml)
            self.assertIn("Stock Left", sheet_xml)
            self.assertIn("Monthly Summary", sheet_xml)

    def test_inventory_formula_references_stock_columns(self):
        with zipfile.ZipFile(self.tmp / "biz_sheets.xlsx") as archive:
            xml = archive.read("xl/worksheets/sheet3.xml").decode("utf-8")
            self.assertIn("B2", xml)
            self.assertIn("C2", xml)
            self.assertIn("D2", xml)
            self.assertIn("Stock Left", xml)

    def test_monthly_summary_profit_formula_references_sales_and_costs(self):
        with zipfile.ZipFile(self.tmp / "biz_sheets.xlsx") as archive:
            xml = archive.read("xl/worksheets/sheet6.xml").decode("utf-8")
            self.assertIn("Orders!F", xml)
            self.assertIn("Orders!G", xml)
            self.assertIn("=C2-D2", xml)

    def test_registry_classifies_bakery_spreadsheet(self):
        format_registry.reset_registry()
        format_registry.discover_builders(force_reload=True)
        self.assertEqual(format_registry.classify("Bakery order spreadsheet tracker"), builder_biz_sheets.FORMAT)
        self.assertTrue(format_registry.can_build("Bakery order spreadsheet tracker"))

    def test_cover_and_previews_are_distinct_real_pngs(self):
        hashes = set()
        for name in ("cover.png", "preview_1.png", "preview_2.png", "preview_3.png"):
            path = self.tmp / name
            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (1200, 800))
                self.assertGreater(len(set(image.getdata())), 5)
            hashes.add(_sha256(path))
        self.assertEqual(len(hashes), 4)

    def test_manifest_records_tabs_and_csvs(self):
        manifest = json.loads((self.tmp / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["format"], builder_biz_sheets.FORMAT)
        self.assertEqual(manifest["tabs"], [
            "Start Here", "Orders", "Inventory", "Customers", "Expenses", "Monthly Summary",
        ])
        self.assertEqual(len(manifest["csv_tabs"]), 6)

    def test_readme_opens_in_excel_and_sheets(self):
        text = (self.tmp / "README.txt").read_text(encoding="utf-8")
        self.assertIn("Microsoft Excel", text)
        self.assertIn("Google Sheets", text)


if __name__ == "__main__":
    unittest.main()
