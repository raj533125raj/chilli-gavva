"""BIG-4 imports retain real listing, buyer and marketing quality."""
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

from tools import sell_proof
import format_registry
import store_kits


class CoverageTests(unittest.TestCase):
    def test_nineteen_formats_have_specs_and_profiles(self):
        kinds = set(format_registry.list_registered_formats())
        self.assertEqual(len(kinds), 19)
        self.assertEqual(kinds, set(sell_proof.FORMAT_ORDER))
        self.assertEqual(kinds, set(store_kits.GUMROAD_PRODUCT_TYPES))
        for kind in kinds:
            self.assertEqual(sell_proof.spec_for(kind)['kind'], kind)


@unittest.skipUnless(Image is not None, 'Pillow required')
class ImportedFormatsTests(unittest.TestCase):
    def test_new_formats_pass_real_listing_pins_carousel_and_upload(self):
        import listing_lint
        import pin_ready
        import carousel_maker
        import zipfile
        for kind in sell_proof.FORMAT_ORDER:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                row = sell_proof.prove(kind, Path(temp))
                self.assertIn(row['status'], ('SELLABLE', 'ALMOST'), row)
                pack = Path(row['pack'])
                self.assertTrue(listing_lint.quality_report(pack)['ok'])
                info = store_kits.parse_listing_file(pack/'listing.txt')
                self.assertEqual(store_kits.gumroad_copy(pack, info)['product_type'], kind)
                with zipfile.ZipFile(pack/'product.zip') as archive:
                    self.assertIsNone(archive.testzip())
                pins = pin_ready.make_pins(pack, Path(temp)/'pins', store_link='https://example.com/pack')
                self.assertTrue(pins['check']['ok'], pins)
                carousel = carousel_maker.make_ready_carousel(pack, Path(temp)/'carousel')
                self.assertTrue(carousel['check']['ok'], carousel)


@unittest.skipUnless(Image is not None, 'Pillow required')
class EvidenceTests(unittest.TestCase):
    def test_every_tablet_page_has_valid_navigation(self):
        import builder_digital_planner as builder
        from tests.test_kl_digital_planner import _pdf_link_targets
        with tempfile.TemporaryDirectory() as temp:
            builder.build({}, temp)
            pages, links = _pdf_link_targets(Path(temp)/'digital_planner.pdf')
            self.assertEqual(set(pages), {src for src, dest in links})
            self.assertTrue(all(dest in set(pages) for src, dest in links))

    def test_profit_deducts_order_costs_and_expenses(self):
        import builder_biz_sheets
        import zipfile
        import xml.etree.ElementTree as ET
        with tempfile.TemporaryDirectory() as temp:
            builder_biz_sheets.build({}, temp)
            with zipfile.ZipFile(Path(temp)/'biz_sheets.xlsx') as book:
                root = ET.fromstring(book.read('xl/worksheets/sheet6.xml'))
            formulas = {c.get('r'): c.find('{*}f').text for c in root.findall('.//{*}c') if c.find('{*}f') is not None}
            for row in range(2, 14):
                self.assertIn('Orders!G', formulas['D'+str(row)])
                self.assertIn('Expenses!D', formulas['D'+str(row)])
                # OOXML stores formulas without the leading "=" (CCA-1 fixed the writer; Excel needs it so)
                self.assertEqual(formulas['E'+str(row)], 'C'+str(row)+'-D'+str(row))

    def test_month_calendar_has_real_dates_and_weekday_alignment(self):
        import builder_digital_planner as builder
        import truth_check
        import calendar
        with tempfile.TemporaryDirectory() as temp:
            builder.build({'year': 2027}, temp)
            pages = truth_check._pdf_pages(Path(temp)/'digital_planner.pdf')
            for month in range(1, 13):
                page = next(p for p in pages if any(t == calendar.month_name[month]+' 2027' for _, _, _, _, t in p['items']))
                dates = [(x, t) for _, size, x, y, t in page['items'] if size == 8 and t.isdigit()]
                self.assertEqual([int(t) for x, t in dates], list(range(1, calendar.monthrange(2027, month)[1]+1)))
                monday_x = next(x for _, size, x, y, t in page['items'] if t == 'Mon')
                tuesday_x = next(x for _, size, x, y, t in page["items"] if t == "Tue")
                expected_x = monday_x-2+calendar.monthrange(2027, month)[0]*(tuesday_x-monday_x)
                self.assertAlmostEqual(dates[0][0], expected_x, delta=0.1)
