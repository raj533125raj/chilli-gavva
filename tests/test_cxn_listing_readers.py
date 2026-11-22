"""Natural description sentences retain their text when they contain colons."""
import unittest
from listing_fields import parse_fields

class ListingReaderTests(unittest.TestCase):
    def test_colon_in_description_is_buyer_content(self):
        fields = parse_fields("Description:\nTwo printable pages: plan your day and record your notes.\nPrint on A4 paper.\nTags: daily planner, notes, printable\nTheme: Calm")
        self.assertIn("Two printable pages:", fields["description"])
        self.assertIn("Print on A4 paper.", fields["description"])
        self.assertNotIn("Theme:", fields["tags"])

    def test_markdown_metadata_does_not_extend_title(self):
        fields = parse_fields("# Daily planning template\n**Buyer:** adults\n**Platform:** Store\n## Description\nUse these pages.")
        self.assertEqual(fields["title"], "Daily planning template")
        self.assertEqual(fields["description"], "Use these pages.")
