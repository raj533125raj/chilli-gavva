import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guardrails  # noqa: E402
import site_builder  # noqa: E402


class SiteBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "s.db")
        self.site = os.path.join(self.tmp.name, "site")

    def tearDown(self):
        self.tmp.cleanup()

    def draft(self, text, approve=True, kind="public_draft"):
        aid = guardrails.request_approval(kind, "Draft title", text, db_path=self.db)
        if approve:
            guardrails.decide(aid, True, self.db)
        return aid

    def read(self, slug):
        with open(os.path.join(self.site, slug, "index.html"), encoding="utf-8") as f:
            return f.read()

    def test_only_approved_public_drafts_are_built(self):
        ok = self.draft("# Free tool\n\nTry it today.")
        self.draft("# Pending\n\nnope", approve=False)
        rejected = self.draft("# Rejected\n\nnope", approve=False)
        guardrails.decide(rejected, False, self.db)
        self.draft("--- a/x\n+++ b/x", kind="code_patch")
        built = site_builder.build(self.db, self.site)
        self.assertEqual(len(built), 1)
        self.assertTrue(built[0].startswith(f"{ok}-free-tool"))
        self.assertIn("<h1>Free tool</h1>", self.read(built[0]))

    def test_everything_is_escaped_and_no_scripts_or_external_files(self):
        self.draft('# <script>alert(1)</script>\n\nHello <img src=x onerror=alert(2)> & "friends"')
        slug = site_builder.build(self.db, self.site)[0]
        page = self.read(slug).lower()
        self.assertNotIn("<script", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("http://", page.replace("w3.org", ""))
        self.assertNotIn("https://", page)
        self.assertNotIn("src=", page.replace("&lt;img src=x", ""))   # only the escaped text mentions src

    def test_slug_is_safe(self):
        self.draft("# ../../etc/passwd  ☃ ???\n\nbody")
        slug = site_builder.build(self.db, self.site)[0]
        self.assertRegex(slug, r"^[0-9]+-[a-z0-9-]*$")
        self.assertTrue(os.path.realpath(os.path.join(self.site, slug)).startswith(os.path.realpath(self.site)))

    def test_not_rebuilt_and_index_lists_pages(self):
        self.draft("# One\n\nbody")
        self.assertEqual(len(site_builder.build(self.db, self.site)), 1)
        self.assertEqual(site_builder.build(self.db, self.site), [])
        self.draft("# Two\n\nbody")
        self.assertEqual(len(site_builder.build(self.db, self.site)), 1)
        with open(os.path.join(self.site, "index.html"), encoding="utf-8") as f:
            index = f.read()
        self.assertEqual(index.count("<li>"), 2)

    def test_footer_disclaimer_present(self):
        self.draft("# T\n\nbody")
        page = self.read(site_builder.build(self.db, self.site)[0])
        self.assertIn("No income or results are promised", page)


if __name__ == "__main__":
    unittest.main()
