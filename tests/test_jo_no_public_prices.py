"""Scans recipes/*.yaml and fails if any line contains a currency amount pattern.

Owner rule: no money amounts in the public repo.
Checks patterns like:
  - \\$\\d
  - \\d+\\s*-\\s*\\d+\\s*USD
  - \\bUSD\\b with a number
  - INR/Rs with a number
"""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PRICE_PATTERNS = [
    re.compile(r"\$\s*\d"),
    re.compile(r"\d+\s*-\s*\d+\s*USD", re.IGNORECASE),
    re.compile(r"\bUSD\b\s*\d|\d\s*\bUSD\b", re.IGNORECASE),
    re.compile(r"\b(?:INR|Rs\.?)\b\s*\d|\d\s*\b(?:INR|Rs\.?)\b", re.IGNORECASE),
]


class NoPublicPricesTests(unittest.TestCase):
    def test_recipes_contain_no_price_patterns(self):
        recipe_files = sorted(glob.glob(os.path.join(ROOT, "recipes", "*.yaml")))
        self.assertTrue(recipe_files, "No recipe files found in recipes/*.yaml")

        violations = []
        for path in recipe_files:
            rel_path = os.path.relpath(path, ROOT)
            with open(path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    for pattern in PRICE_PATTERNS:
                        match = pattern.search(line)
                        if match:
                            violations.append(
                                f"{rel_path}:{line_no}: matched '{match.group()}' in line: {line.strip()}"
                            )

        self.assertFalse(
            violations,
            "Found currency amount patterns in recipes:\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
