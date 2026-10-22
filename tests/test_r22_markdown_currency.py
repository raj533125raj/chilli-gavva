"""R22 public documentation must not publish currency amounts."""
import os
import re
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURRENCY_AMOUNT = re.compile(
    r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
    re.IGNORECASE,
)


class MarkdownCurrencyTests(unittest.TestCase):
    def test_public_markdown_has_no_currency_amounts(self):
        for directory, subdirs, files in os.walk(ROOT):
            if os.path.relpath(directory, ROOT).split(os.sep)[0] == "archive":
                subdirs[:] = []
                continue
            for name in files:
                if not name.endswith(".md"):
                    continue
                path = os.path.join(directory, name)
                with open(path, encoding="utf-8") as markdown:
                    match = CURRENCY_AMOUNT.search(markdown.read())
                self.assertIsNone(match, f"currency amount in {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    unittest.main()
