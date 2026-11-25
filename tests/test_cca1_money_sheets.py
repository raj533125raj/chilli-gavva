"""CCA-1: money-sheets family (debt payoff, couples budget, freelancer budget, sinking funds).

Real files in temp folders: xlsx structure, real formulas, no typed amounts, CSV per tab, 3 different previews.
"""
import csv
import importlib
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

import format_registry

FORMATS = ["debt_payoff", "couples_budget", "freelance_budget", "sinking_funds"]
LIVE_TITLES = {
    "debt_payoff": "Draft: [Budget Sheets Agent] Google Sheets Debt Snowball Tracker (v4, llm) (rewritten)",
    "couples_budget": "Draft: [Budget Sheets Agent] Couples Budget Spreadsheet for Shared Bills (v4, llm)",
    "freelance_budget": "Draft: [Budget Sheets Agent] Irregular Income Budget for Freelancers",
    "sinking_funds": "Draft: [Budget Sheets Agent] Sinking Funds Tracker - Savings Goals Spreadsheet",
}
# Functions that work the same in Excel 2019+/365 and Google Sheets.
ALLOWED = {"IF", "SUM", "SUMIF", "SUMIFS", "COUNTIF", "COUNTIFS", "N", "MAX", "MIN", "ROUND", "ROUNDUP", "OR",
           "AND", "NOT", "ISNUMBER", "IFERROR", "NPER", "INDEX", "MATCH", "SUMPRODUCT", "REPT", "DATE", "EDATE",
           "TEXT", "AVERAGEIF", "SMALL", "COUNT", "TODAY", "YEAR", "MONTH"}
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def sheets_of(xlsx):
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(xlsx) as z:
        book = ET.fromstring(z.read("xl/workbook.xml"))
        names = [s.get("name") for s in book.iter(NS + "sheet")]
        xml = {name: z.read(f"xl/worksheets/sheet{i + 1}.xml").decode() for i, name in enumerate(names)}
    return names, xml


@unittest.skipUnless(Image is not None, "needs Pillow")
class MoneySheetsBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.built = {}
        for fmt in FORMATS:
            module = importlib.import_module("builder_" + fmt)
            out = Path(cls.tmp.name) / fmt
            cls.built[fmt] = (module, out, module.build({"title": None}, out))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_registered_with_multi_word_keywords(self):
        format_registry.discover_builders(force_reload=True)
        for fmt in FORMATS:
            with self.subTest(fmt):
                module = self.built[fmt][0]
                self.assertEqual(module.FORMAT, fmt)
                self.assertIn(fmt, format_registry.list_registered_formats())
                self.assertGreaterEqual(sum(" " in k for k in module.KEYWORDS), 8)
                self.assertEqual(format_registry.classify(LIVE_TITLES[fmt]), fmt)

    def test_files_tabs_csv_readme_license_manifest(self):
        for fmt in FORMATS:
            with self.subTest(fmt):
                module, out, names = self.built[fmt]
                tabs, _ = sheets_of(out / f"{fmt}.xlsx")
                self.assertEqual(tabs[0], "Start Here")
                self.assertIn("Summary", tabs)
                self.assertGreaterEqual(len(tabs), 4)
                csvs = sorted(n for n in names if n.endswith(".csv"))
                self.assertEqual(len(csvs), len(tabs))
                for name in csvs:
                    with (out / name).open(encoding="utf-8") as handle:
                        self.assertTrue(next(csv.reader(handle)))
                with zipfile.ZipFile(out / f"{fmt}-csv-tabs.zip") as archive:
                    self.assertEqual(sorted(archive.namelist()), csvs)
                for name in ("cover.png", "preview_1.png", "preview_2.png", "preview_3.png", "README.txt",
                             "LICENSE.txt", "manifest.json"):
                    self.assertIn(name, names)
                readme = (out / "README.txt").read_text()
                self.assertIn("HOW TO OPEN IN EXCEL", readme)
                self.assertIn("HOW TO OPEN IN GOOGLE SHEETS", readme)
                self.assertIn("personal use only", (out / "LICENSE.txt").read_text())
                manifest = json.loads((out / "manifest.json").read_text())
                self.assertEqual(manifest["tabs"], tabs)
                self.assertEqual(sorted(manifest["files"]), sorted(names))
                self.assertEqual(sorted(p.name for p in out.iterdir()), sorted(names))

    def test_no_amounts_typed_and_real_formulas(self):
        for fmt in FORMATS:
            with self.subTest(fmt):
                tabs, xml = sheets_of(self.built[fmt][1] / f"{fmt}.xlsx")
                formulas = []
                for tab, body in xml.items():
                    self.assertNotIn("<v>", body, f"{tab}: a typed number")
                    formulas += re.findall(r"<f>(.*?)</f>", body)
                summary = re.findall(r"<f>(.*?)</f>", xml["Summary"])
                self.assertGreaterEqual(len(summary), 10)
                self.assertTrue(any("REPT(" in f for f in formulas), "progress bar")
                for f in formulas:
                    text = f.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
                    self.assertFalse(text.startswith("="), text)
                    bare = re.sub(r'"[^"]*"', '""', text)
                    self.assertEqual(bare.count("("), bare.count(")"), text)
                    self.assertEqual(text.count('"') % 2, 0, text)
                    for func in re.findall(r"([A-Z]+)\(", bare):
                        self.assertIn(func, ALLOWED, text)
                    for sheet in re.findall(r"'([^']+)'!|\b([A-Za-z]+)!", text):
                        name = sheet[0] or sheet[1]
                        self.assertIn(name, tabs, text)

    def test_deep_product_qa_finds_no_sheet_problems(self):
        import product_qa_deep
        for fmt in FORMATS:
            with self.subTest(fmt):
                problems, _ = product_qa_deep._check_xlsx_openxml(str(self.built[fmt][1] / f"{fmt}.xlsx"), f"{fmt}.xlsx")
                self.assertEqual(problems, [])

    def test_three_previews_are_different_and_full_size(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import visual_qa
        for fmt in FORMATS:
            with self.subTest(fmt):
                out = self.built[fmt][1]
                images = [out / n for n in ("cover.png", "preview_1.png", "preview_2.png", "preview_3.png")]
                for path in images:
                    with Image.open(path) as im:
                        self.assertEqual(im.size, (1200, 800))
                for i, a in enumerate(images):
                    for b in images[i + 1:]:
                        self.assertFalse(visual_qa.duplicate_images(a, b), f"{a.name} == {b.name}")

    def test_key_formula_rows_point_at_the_right_cells(self):
        import money_sheets as ms
        import builder_debt_payoff, builder_couples_budget, builder_freelance_budget, builder_sinking_funds
        debt = {t["name"]: t for t in builder_debt_payoff.design()["tabs"]}
        self.assertEqual(ms.row_of(debt["Summary"], "Progress % (paid / starting debt)"), 7)
        self.assertEqual(ms.row_of(debt["Payoff Plan"], "Method (Snowball or Avalanche)"), 3)
        self.assertEqual(ms.row_of(debt["Payoff Plan"], "Extra payment each month (on top of minimums)"), 4)
        self.assertEqual(ms.row_of(debt["Payoff Plan"], "1"), 7)
        couple = {t["name"]: t for t in builder_couples_budget.design()["tabs"]}
        self.assertEqual(ms.row_of(couple["Summary"], "Partner A share of income (0-1, used by the 'By income' split)"), 7)
        self.assertEqual(ms.row_of(couple["Summary"], "Total shared spending"), 10)
        self.assertEqual(ms.row_of(couple["Summary"], "Paid by Partner A"), 11)
        self.assertEqual(ms.row_of(couple["Summary"], "Partner A fair share"), 13)
        self.assertEqual(ms.row_of(couple["Summary"], "Spent % of income"), 17)
        free = {t["name"]: t for t in builder_freelance_budget.design()["tabs"]}
        self.assertEqual(ms.row_of(free["Summary"], "Buffer balance"), 17)
        self.assertEqual(ms.row_of(free["Summary"], "Buffer goal"), 19)
        self.assertEqual(ms.row_of(free["Summary"], "Buffer progress %"), 20)
        sink = {t["name"]: t for t in builder_sinking_funds.design()["tabs"]}
        self.assertEqual(ms.row_of(sink["Summary"], "Total of all goals"), 4)
        self.assertEqual(ms.row_of(sink["Summary"], "Total saved"), 5)
        self.assertEqual(ms.row_of(sink["Summary"], "Overall progress %"), 7)

    def test_dropdowns_for_choice_columns(self):
        _, xml = sheets_of(self.built["couples_budget"][1] / "couples_budget.xlsx")
        self.assertIn("Partner A,Partner B", xml["Shared Expenses"].replace("&quot;", ""))
        self.assertIn("By income", xml["Shared Expenses"])
        _, xml = sheets_of(self.built["debt_payoff"][1] / "debt_payoff.xlsx")
        self.assertIn("Snowball,Avalanche", xml["Payoff Plan"].replace("&quot;", ""))

    def test_bad_spec_is_refused(self):
        import builder_debt_payoff
        with self.assertRaises(ValueError):
            builder_debt_payoff.build("not a dict", self.tmp.name)


if __name__ == "__main__":
    unittest.main()
