"""Free listing describes the sample honestly and directs buyers to the full pack."""
import json
from pathlib import Path
import unittest
from unittest import mock
try:
    from PIL import Image
except ImportError:
    Image=None
from tests.test_cxx_selltest import NOW


@unittest.skipUnless(Image is not None,'Pillow required')
class SampleListingTests(unittest.TestCase):
    def setUp(self):
        from tests.test_cxad_listings import OwnerCopyFlowTests
        self.fixture=OwnerCopyFlowTests('test_upload_sends_both_titles_and_all_real_copy_in_owner_document')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.db=self.fixture.db;self.products=self.fixture.products;self.pack=self.fixture.pack
        import guardrails
        conn=guardrails.connect(self.db)
        conn.execute("INSERT INTO approvals (id,ts,kind,title,payload,status) VALUES(27,?,'public_draft','Focus Planner','Pack','approved')",(NOW.isoformat(),))
        conn.commit();conn.close()

    def test_sample_copy_names_real_full_pack_and_saved_link_is_in_full_version_page(self):
        import free_sample,tg_listed,selltest_listings,listing_lint,sample_pdf
        url='https://example.gumroad.com/l/full-planner'
        self.assertTrue(tg_listed.set_listed(27,url,self.db,NOW)['ok'])
        folder=free_sample.build(27,self.products,self.db)
        fields=__import__('listing_fields').parse_fields((folder/'listing.txt').read_text())
        full=selltest_listings.make(self.pack,'planner')['A']
        self.assertTrue(fields['description'].startswith('Not sure whether this layout fits your needs?'))
        self.assertIn('Get the full pack, '+full,fields['description'])
        self.assertIn('Full version page in sample.pdf',fields['description'])
        self.assertEqual(json.loads((folder/'sample.json').read_text())['full_pack_title'],full)
        self.assertIn(url,(folder/'README.txt').read_text())
        self.assertTrue(any(url in line for line in sample_pdf.pdf_pages(folder/'sample.pdf')[3][1]))
        self.assertTrue(listing_lint.quality_report(folder)['ok'])
        self.assertIsNone(__import__('sell_ready').check_money_text((folder/'listing.txt').read_text()))

    def test_missing_paid_link_is_honest_and_adding_link_refreshes_cached_sample(self):
        import free_sample,tg_listed
        folder=free_sample.build(27,self.products,self.db)
        before=json.loads((folder/'sample.json').read_text())['source_digest']
        text=(folder/'listing.txt').read_text()
        self.assertIn('Look for that full-pack title in this shop',text)
        self.assertNotIn('for the full-pack link',text)
        self.assertTrue(tg_listed.set_listed(27,'https://example.gumroad.com/l/complete-planner',self.db,NOW)['ok'])
        refreshed=free_sample.build(27,self.products,self.db)
        self.assertNotEqual(json.loads((refreshed/'sample.json').read_text())['source_digest'],before)
        self.assertIn('for the full-pack link',(refreshed/'listing.txt').read_text())
