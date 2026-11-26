import tempfile
from pathlib import Path
import unittest
from owner_titles import owner_title

class Titles(unittest.TestCase):
    def test_internal_title_uses_buyer_profile(self):
        for raw in ('Draft: [ADHD Planners Agent] Goal-tracki (v4, llm) (rewritten)',
                    'Draft: [Budget Sheets Agent] Google Shee (v4, llm)'):
            title=owner_title(raw)
            self.assertNotRegex(title,r'Draft:|Agent|rewritten|v4|Goal-tracki|Google Shee')
            self.assertGreaterEqual(len(title.split()),6);self.assertLessEqual(len(title.split()),9)
    def test_listing_preferred_and_word_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            title='Families Budget Spreadsheet For Tracking Daily Records And Monthly Plans Together'
            (path/'listing.txt').write_text('Title: '+title+'\n\nDescription:\nActual workbook.')
            result=owner_title({'title':'Draft: [Budget Agent] inside','path':path})
            self.assertEqual(result,'Families Budget Spreadsheet For Tracking Daily Records And Monthly…')
            self.assertNotIn('Monthl…',result)
    def test_short_plain_title_preserves_identity(self):
        title=owner_title('Monthly budget sheet')
        self.assertTrue(title.startswith('Monthly budget sheet '))
        self.assertEqual(len(title.split()),6)
