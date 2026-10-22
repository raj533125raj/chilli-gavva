"""R22 public documentation must not publish currency amounts."""
import os
import re
import subprocess
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURRENCY_AMOUNT = re.compile(
    r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
    re.IGNORECASE,
)


class MarkdownCurrencyTests(unittest.TestCase):
    def test_public_markdown_has_no_currency_amounts(self):
        path, match = find_currency_amount()
        self.assertIsNone(match, "currency amount in " + (os.path.relpath(path, ROOT) if path else "tracked Markdown"))


def tracked_markdown_paths(root=ROOT):
    """Only files in Git's public tracked set count as public documentation."""
    result = subprocess.run(["git", "-C", root, "ls-files", "*.md"], check=True, capture_output=True, text=True)
    return [os.path.join(root, name) for name in result.stdout.splitlines()
            if name and name.split("/", 1)[0] != "archive"]


def find_currency_amount(root=ROOT):
    for path in tracked_markdown_paths(root):
        with open(path, encoding="utf-8") as markdown:
            match = CURRENCY_AMOUNT.search(markdown.read())
        if match:
            return path, match
    return None, None


if __name__ == "__main__":
    unittest.main()
