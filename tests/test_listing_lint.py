"""Tests for listing_lint.py: upload-pack listing.txt checks before publishing."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import listing_lint

LONG_DESC = ("Two printable pages: a Brain Dump page and a Daily Focus page. "
             "Print on A4 or US Letter, use a pen, and keep the sheets on your desk. "
             "This pack is for personal use only; do not resell or share the files. "
             "Perfect for people who want a calm evening routine and a clear head. "
             "No account, no subscription and no spreadsheet formulas are needed to use it.")

GOOD_TAGS = ["focus", "printable planner", "calm routine", "daily plan", "desk pad",
             "habit tracker", "study timer", "weekly menu", "mindful notes", "goal grid",
             "brain dump", "weekly audit", "one page a day"]


def make_listing(title="Focus Planner Printable Pack", description=LONG_DESC,
                 tags=None, what_get=None):
    if tags is None:
        tags = GOOD_TAGS
    if what_get is None:
        what_get = ["A4 PDF", "US Letter PDF", "Readme and personal use licence"]
    body = f"Title: {title}\n\nDescription:\n{description}\n\nTags: {', '.join(tags)}"
    if what_get:
        body += "\n\nWhat you get:\n" + "\n".join(what_get)
    return body + "\n"


def levels(results):
    return {(check, level) for check, level, _message in results}


def run_module(argv):
    """main() with stdout captured; returns (exit_code, text)."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = listing_lint.main(argv)
    return code, buffer.getvalue()


class ListingTextTests(unittest.TestCase):
    def lint(self, text):
        return listing_lint.lint_text(text, name="listing.txt")

    def test_good_listing_is_all_pass_no_warn(self):
        results = self.lint(make_listing())
        fails = [(c, m) for c, l, m in results if l != "PASS"]
        self.assertEqual(fails, [])

    def test_title_too_short_fails(self):
        results = self.lint(make_listing(title="Short title"))
        self.assertIn(("title", "FAIL"), levels(results))
        self.assertTrue(any("[8-9] chars" in m or "chars" in m
                            for c, l, m in results if c == "title"))

    def test_title_too_long_fails(self):
        results = self.lint(make_listing(title="T" * 101))
        self.assertIn(("title", "FAIL"), levels(results))

    def test_title_min_max_boundaries_pass(self):
        self.assertEqual(self.lint(make_listing(title="A" * 20))[0][1], "PASS")
        self.assertEqual(self.lint(make_listing(title="B" * 100))[0][1], "PASS")

    def test_title_line_missing_fails(self):
        text = make_listing().replace("Title: Focus Planner Printable Pack\n", "", 1)
        results = self.lint(text)
        self.assertIn(("title", "FAIL"), levels(results))
        self.assertTrue(any("missing" in m.lower() for c, l, m in results if c == "title"))

    def test_description_too_short_fails(self):
        results = self.lint(make_listing(description="Too short a description."))
        self.assertIn(("description", "FAIL"), levels(results))

    def test_description_too_long_fails(self):
        results = self.lint(make_listing(description=LONG_DESC + " " + ("padded " * 700)))
        self.assertIn(("description", "FAIL"), levels(results))

    def test_missing_description_section_fails(self):
        text = make_listing().replace(
            "\n\nDescription:\n" + LONG_DESC, "", 1)
        results = self.lint(text)
        self.assertIn(("description", "FAIL"), levels(results))

    def test_twelve_tags_fail(self):
        results = self.lint(make_listing(tags=GOOD_TAGS[:-1]))
        self.assertIn(("tags", "FAIL"), levels(results))

    def test_fourteen_tags_fail(self):
        results = self.lint(make_listing(tags=GOOD_TAGS + ["extra tag"]))
        self.assertIn(("tags", "FAIL"), levels(results))

    def test_exactly_13_tags_pass(self):
        results = self.lint(make_listing())
        self.assertIn(("tags", "PASS"), levels(results))
        self.assertTrue(any("13" in m for c, l, m in results if c == "tags"))

    def test_duplicate_tags_case_insensitive_fail(self):
        tags = GOOD_TAGS[:-1] + ["Focus"]
        results = self.lint(make_listing(tags=tags))
        self.assertIn(("tag duplicates", "FAIL"), levels(results))

    def test_tag_over_20_chars_fails(self):
        tags = GOOD_TAGS[:-1] + ["one very long planner tag"]
        results = self.lint(make_listing(tags=tags))
        self.assertIn(("tag length", "FAIL"), levels(results))

    def test_tag_exactly_20_chars_passes(self):
        tags = GOOD_TAGS[:-1] + ["a" * 20]
        results = self.lint(make_listing(tags=tags))
        self.assertIn(("tag length", "PASS"), levels(results))
        self.assertIn(("tags", "PASS"), levels(results))

    def test_dollar_money_fails(self):
        for snippet in ("it costs $12 today", "now 12 $ in shops"):
            results = self.lint(make_listing(description=LONG_DESC + " " + snippet))
            self.assertIn(("money", "FAIL"), levels(results), snippet)

    def test_rupee_and_code_money_fails(self):
        for snippet in ("price 500₹ only", "Rs. 500", "Rs500 shipped", "50 INR",
                        "INR50 online", "USD 500", "500 USD"):
            results = self.lint(make_listing(description=LONG_DESC + " " + snippet))
            self.assertIn(("money", "FAIL"), levels(results), snippet)

    def test_etsy_amazon_canva_fail(self):
        for brand in ("Etsy shop banner", "made in Canva", "Amazon listing file"):
            results = self.lint(make_listing(description=LONG_DESC + " " + brand))
            self.assertIn(("brands", "FAIL"), levels(results), brand)

    def test_bare_notion_fails(self):
        results = self.lint(make_listing(description=LONG_DESC + " Notion template included."))
        self.assertIn(("brands", "FAIL"), levels(results))
        self.assertTrue(any("Notion" in m for c, l, m in results if c == "brands"))

    def test_for_notion_is_allowed(self):
        results = self.lint(make_listing(
            description=LONG_DESC + " It is a printable pack for Notion users who also like paper."))
        self.assertNotIn(("brands", "FAIL"), levels(results))

    def test_long_caps_word_warns(self):
        results = self.lint(make_listing(what_get=["DAILY PLANNER sheet", "US Letter PDF",
                                                   "Readme and licence"]))
        self.assertIn(("caps", "WARN"), levels(results))
        self.assertNotIn(("caps", "WARN"), levels(self.lint(make_listing())))

    def test_caps_acronyms_exempt(self):
        results = self.lint(make_listing(what_get=["PDF file for A4 paper", "US Letter paper",
                                                   "Usa guide page"]))
        self.assertNotIn(("caps", "WARN"), levels(results))

    def test_what_you_get_needs_three_lines(self):
        results = self.lint(make_listing(what_get=["A4 PDF", "US Letter PDF"]))
        self.assertIn(("what you get", "FAIL"), levels(results))

    def test_what_you_get_three_lines_pass(self):
        results = self.lint(make_listing())
        self.assertIn(("what you get", "PASS"), levels(results))

    def test_missing_what_you_get_fails(self):
        results = self.lint(make_listing(what_get=[]))
        self.assertIn(("what you get", "FAIL"), levels(results))


class FolderAndCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def pack(self, name, text=None):
        target = self.root / name
        target.mkdir()
        if text is not None:
            (target / "listing.txt").write_text(text, encoding="utf-8")
        return target

    def test_lint_folder_reads_listing_txt(self):
        pack = self.pack("good-pack", make_listing())
        results, ok = listing_lint.lint_folder(pack)
        self.assertTrue(ok)
        self.assertTrue(all(level == "PASS" for _c, level, _m in results))

    def test_lint_folder_missing_listing_fails(self):
        pack = self.pack("empty-pack")
        results, ok = listing_lint.lint_folder(pack)
        self.assertFalse(ok)
        self.assertEqual(results[0][:2], ("file", "FAIL"))

    def test_cli_three_lines_pass_cli(self):
        self.pack("cli-pack", make_listing())
        code, out = run_module(["--packs-root", str(self.root), "--all"])
        self.assertEqual(code, 0)
        self.assertIn("PASS listing lint", out)

    def test_cli_exit_1_when_a_pack_has_failures(self):
        self.pack("good", make_listing())
        self.pack("bad", make_listing(title="Bad"))
        code, out = run_module(["--packs-root", str(self.root), "--all"])
        self.assertEqual(code, 1)
        self.assertIn("== good", out)
        self.assertIn("== bad", out)
        self.assertIn("FAIL listing lint", out)

    def test_cli_lint_all_no_packs_reports_fail(self):
        code, out = run_module(["--packs-root", str(self.root), "--all"])
        self.assertEqual(code, 1)
        self.assertIn("no listing.txt packs found", out)

    def test_cli_single_pack_folder_exit_1_on_fail(self):
        self.pack("solo", make_listing(title="Too small"))
        code, out = run_module([str(self.root / "solo")])
        self.assertEqual(code, 1)
        self.assertIn("FAIL title", out)

    def test_cli_single_pack_folder_exit_0_on_pass(self):
        self.pack("solo-good", make_listing())
        code, out = run_module([str(self.root / "solo-good")])
        self.assertEqual(code, 0)
        self.assertIn("PASS tags", out)

    def test_cli_folder_and_all_conflict_is_usage_error(self):
        self.pack("both", make_listing())
        with self.assertRaises(SystemExit):
            run_module([str(self.root / "both"), "--all"])

    def test_cli_all_lints_packs_in_sorted_order(self):
        self.pack("alpha", make_listing())
        self.pack("zulu", make_listing())
        _code, out = run_module(["--packs-root", str(self.root), "--all"])
        self.assertLess(out.index("== alpha"), out.index("== zulu"))


if __name__ == "__main__":
    unittest.main()
