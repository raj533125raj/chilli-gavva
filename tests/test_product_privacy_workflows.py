"""Static checks for encrypted product handling in workflows."""
import os
import re
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import site_builder  # noqa: E402


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

    def test_unapproved_markdown_file_is_not_published(self):
        text = self.read(".github/workflows/publish.yml")
        self.assertNotRegex(text, r"exported_content/\*\.md|for md in exported_content")
        with tempfile.TemporaryDirectory() as tmp:
            exported = os.path.join(tmp, "exported_content")
            os.makedirs(exported)
            unapproved = os.path.join(exported, "unapproved.md")
            with open(unapproved, "w", encoding="utf-8") as handle:
                handle.write("# Fake unapproved draft\nPrivate sample body\n")
            built = site_builder.build(db_path=os.path.join(tmp, "empty.db"),
                                       site_dir=os.path.join(tmp, "site"))
            self.assertEqual(built, [])
            self.assertFalse(os.path.exists(os.path.join(tmp, "site", "index.html")))


if __name__ == "__main__":
    unittest.main()
