"""The public-currency check reads tracked Markdown, never ignored product files."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_r22_markdown_currency import find_currency_amount


class CurrencyScopeTests(unittest.TestCase):
    def test_ignored_product_listing_is_excluded_but_tracked_markdown_is_checked(self):
        with tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q", root], check=True)
            with open(os.path.join(root, ".gitignore"), "w", encoding="utf-8") as ignore:
                ignore.write("exported_content/products/\n")
            tracked = os.path.join(root, "PUBLIC.md")
            with open(tracked, "w", encoding="utf-8") as markdown:
                markdown.write("Public notes without a price.\n")
            ignored = os.path.join(root, "exported_content", "products", "demo", "listing.md")
            os.makedirs(os.path.dirname(ignored))
            with open(ignored, "w", encoding="utf-8") as listing:
                listing.write("Private product price: $3\n")
            subprocess.run(["git", "-C", root, "add", ".gitignore", "PUBLIC.md"], check=True)

            self.assertEqual(find_currency_amount(root), (None, None))

            with open(tracked, "w", encoding="utf-8") as markdown:
                markdown.write("Public price: $3\n")
            path, match = find_currency_amount(root)
            self.assertEqual(path, tracked)
            self.assertEqual(match.group(), "$3")


if __name__ == "__main__":
    unittest.main()
