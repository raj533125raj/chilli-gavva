"""The daily target counts actual valid kits and gives actionable blockers."""
from pathlib import Path
import tempfile
import unittest
try:
    from PIL import Image
except ImportError:
    Image=None
import product_counts
from tests.test_cxx_selltest import NOW


class EmptyProgressTests(unittest.TestCase):
    def test_reading_missing_database_never_creates_it(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/'missing.db'
            result=product_counts.sell_test_progress(str(db),Path(temp)/'products',NOW)
            self.assertEqual(result['ready'],0)
            self.assertFalse(db.exists())
            self.assertIn('Kotha Drafts',product_counts.sell_test_line(str(db),temp,NOW))


@unittest.skipUnless(Image is not None,'Pillow required')
class ReadyProgressTests(unittest.TestCase):
    def setUp(self):
        from tests.test_cxad_sample_copy import SampleListingTests
        import product_builder
        self.fixture=SampleListingTests('test_sample_copy_names_real_full_pack_and_saved_link_is_in_full_version_page')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.db=self.fixture.db;self.products=self.fixture.products;self.pack=self.fixture.pack
        product_builder._record(self.db,27,'built',self.pack.name)

    def test_daily_report_counts_a_real_complete_kit(self):
        import daily_report
        self.assertEqual(product_counts.sell_test_progress(self.db,self.products,NOW)['ready'],1)
        text=daily_report.daily_text(self.db,NOW,status_fn=lambda:{},products_dir=self.products)
        self.assertIn('Sell test ready: 1/8 packs',text)
        self.assertIn('Blocks:',text)
        self.assertNotRegex(text,r'[$₹€£]')

    def test_missing_or_damaged_kit_and_failed_database_state_do_not_count(self):
        import product_builder
        kit=self.pack/'gumroad-upload'
        cover=kit/'cover.png';content=cover.read_bytes();cover.unlink()
        line=product_counts.sell_test_line(self.db,self.products,NOW)
        self.assertIn('0/8',line);self.assertIn('cover.png',line)
        cover.write_bytes(content)
        (kit/'product.zip').write_bytes(b'broken')
        self.assertEqual(product_counts.sell_test_progress(self.db,self.products,NOW)['ready'],0)
        product_builder._record(self.db,27,'needs_fix',self.pack.name,'Checker review needed')
        self.assertEqual(product_counts.sell_test_progress(self.db,self.products,NOW)['ready'],0)
        import guardrails
        conn=guardrails.connect(self.db);conn.execute("UPDATE approvals SET status='rejected' WHERE id=27");conn.commit();conn.close()
        self.assertNotIn('Pack fixes leda Hold: /ready',product_counts.sell_test_progress(self.db,self.products,NOW)['blockers'])

    def test_waiting_checker_revision_is_named(self):
        import guardrails,checker,datetime
        aid=guardrails.request_approval('public_draft','Private Planner','Real PDF pages',checker.format_review(88,['Check layout'],'revise'),self.db)
        conn=guardrails.connect(self.db)
        conn.execute('UPDATE approvals SET ts=? WHERE id=?',((NOW-datetime.timedelta(hours=30)).isoformat(),aid));conn.commit();conn.close()
        self.assertIn('Checker REVISE',product_counts.sell_test_line(self.db,self.products,NOW))
