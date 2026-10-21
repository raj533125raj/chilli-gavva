"""Static checks for encrypted product handling in workflows."""
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ProductPrivacyWorkflowTests(unittest.TestCase):
    def read(self, relative):
        with open(os.path.join(REPO, relative), encoding="utf-8") as handle:
            return handle.read()

    def test_publish_opens_database_then_unpacks_products(self):
        text = self.read(".github/workflows/publish.yml")
        opened = text.index("- name: Open the database")
        unpack = text.index("- name: Unpack private products")
        build = text.index("- name: Build owner-approved pages")
        self.assertLess(opened, unpack)
        self.assertLess(unpack, build)
        block = text[unpack:build]
        self.assertIn("DB_KEY: ${{ secrets.DB_KEY }}", block)
        self.assertIn("scripts/db_crypt.py unpack-products", block)

    def test_publish_never_echoes_key(self):
        text = self.read(".github/workflows/publish.yml")
        self.assertIn("agent_data.db.enc", text)
        for line in text.splitlines():
            if re.search(r"\becho\b|\bprintf\b", line):
                self.assertNotRegex(line, r"\$\{?DB_KEY|secrets\.DB_KEY")
        self.assertNotIn("set -x", text)


if __name__ == "__main__":
    unittest.main()
