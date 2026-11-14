"""Tests for listing_lint.py --suggest feature: concrete fix suggestions."""
import io
import os
import shutil
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


def run_module(argv):
    """main() with stdout captured; returns (exit_code, text)."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = listing_lint.main(argv)
    return code, buffer.getvalue()


# Several commands under test write below a relative "exported_content/" path (post plan, pack
# versions, lint notes). Run this module from a temp folder so nothing lands in the repo.
_ORIGINAL_CWD = os.getcwd()
_SCRATCH_CWD = None
_SAVED_DEFAULT = None


def setUpModule():
    global _SCRATCH_CWD
    import tempfile as _tempfile
    _SCRATCH_CWD = _tempfile.TemporaryDirectory()
    os.chdir(_SCRATCH_CWD.name)
    # listing_lint.SUGGEST_ROOT is an absolute repo path: point it at the temp folder too.
    import listing_lint as _module
    from pathlib import Path as _Path
    global _SAVED_DEFAULT
    _SAVED_DEFAULT = _module.SUGGEST_ROOT
    _module.SUGGEST_ROOT = _Path(_SCRATCH_CWD.name) / "exported_content" / "products" / "lint"


def tearDownModule():
    os.chdir(_ORIGINAL_CWD)
    import listing_lint as _module
    _module.SUGGEST_ROOT = _SAVED_DEFAULT
    if _SCRATCH_CWD is not None:
        _SCRATCH_CWD.cleanup()


class SuggestForTests(unittest.TestCase):
    """Unit tests for listing_lint.suggest_for()."""

    def test_title_too_long_suggests_word_cut(self):
        long_title = "A" * 50 + " " + "B" * 60  # way over 100 chars
        text = make_listing(title=long_title)
        suggestions = listing_lint.suggest_for(text)
        joined = "\n".join(suggestions)
        self.assertIn("title too long", joined.lower())
        self.assertIn("cut at a word", joined.lower())

    def test_title_too_long_cut_respects_max(self):
        long_title = "Focus Planner " + "X" * 200
        text = make_listing(title=long_title)
        suggestions = listing_lint.suggest_for(text)
        joined = "\n".join(suggestions)
        # The suggested cut should be <= 100 chars
        for line in suggestions:
            if "cut at a word" in line:
                import re
                m = re.search(r"'([^']*)' \((\d+) chars\)", line)
                if m:
                    self.assertLessEqual(int(m.group(2)), listing_lint.TITLE_MAX)

    def test_duplicate_tags_suggests_removal(self):
        tags = GOOD_TAGS[:-1] + ["Focus", "focus"]
        text = make_listing(tags=tags)
        suggestions = listing_lint.suggest_for(text)
        joined = "\n".join(suggestions)
        self.assertIn("duplicate tags", joined.lower())
        self.assertIn("remove", joined.lower())

    def test_fewer_than_13_tags_suggests_candidates(self):
        tags = GOOD_TAGS[:5]  # only 5 tags
        text = make_listing(tags=tags)
        suggestions = listing_lint.suggest_for(text)
        joined = "\n".join(suggestions)
        self.assertIn("fewer than 13 tags", joined.lower())
        self.assertIn("consider adding", joined.lower())

    def test_fewer_than_13_tags_excludes_brands(self):
        desc = LONG_DESC + " This is an Etsy and Canva and Notion design."
        tags = GOOD_TAGS[:10]  # 10 tags, need 3 more
        text = make_listing(description=desc, tags=tags)
        suggestions = listing_lint.suggest_for(text)
        joined = "\n".join(suggestions)
        self.assertNotIn("Etsy", joined)
        self.assertNotIn("Canva", joined)
        self.assertNotIn("Notion", joined)

    def test_fewer_than_13_tags_candidates_under_20_chars(self):
        tags = GOOD_TAGS[:10]
        text = make_listing(tags=tags)
        suggestions = listing_lint.suggest_for(text)
        joined = "\n".join(suggestions)
        for line in suggestions:
            if "consider adding" in line:
                candidates = line.split("->", 1)[1].strip()
                for tag in candidates.split(","):
                    tag = tag.strip()
                    self.assertLessEqual(len(tag), listing_lint.TAG_MAX_LEN)

    def test_all_caps_suggests_title_case(self):
        text = make_listing(what_get=["DAILY PLANNER sheet", "US Letter PDF",
                                       "Readme and licence"])
        suggestions = listing_lint.suggest_for(text)
        joined = "\n".join(suggestions)
        self.assertIn("all-caps", joined.lower())
        self.assertIn("Title Case", joined)
        self.assertIn("Daily", joined)
        self.assertIn("Planner", joined)

    def test_missing_what_you_get_suggests_template(self):
        text = make_listing(what_get=[])
        suggestions = listing_lint.suggest_for(text)
        joined = "\n".join(suggestions)
        self.assertIn('missing "What you get"', joined)
        self.assertIn("A4 PDF", joined)
        self.assertIn("US Letter PDF", joined)

    def test_pass_checks_no_suggestions(self):
        text = make_listing()
        suggestions = listing_lint.suggest_for(text)
        self.assertEqual(suggestions, [])


class WriteSuggestionsTests(unittest.TestCase):
    """Unit tests for listing_lint.write_suggestions()."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_writes_suggest_file_outside_pack(self):
        pack = self.root / "my-pack"
        pack.mkdir()
        (pack / "listing.txt").write_text(make_listing(title="T" * 200))
        suggestions = listing_lint.suggest_for(
            (pack / "listing.txt").read_text(encoding="utf-8-sig"), name="my-pack")
        path = listing_lint.write_suggestions("my-pack", suggestions)
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent.name, "lint")
        self.assertEqual(path.name, "my-pack.suggest.txt")
        content = path.read_text(encoding="utf-8")
        self.assertIn("Suggestions for my-pack", content)
        self.assertIn("title too long", content.lower())

    def test_creates_lint_directory(self):
        path = listing_lint.write_suggestions("x-pack", ["some suggestion"])
        self.assertTrue(listing_lint.SUGGEST_ROOT.is_dir())
        self.assertTrue(path.is_file())

    def test_no_suggestions_writes_no_suggestions_message(self):
        path = listing_lint.write_suggestions("good-pack", [])
        content = path.read_text(encoding="utf-8")
        self.assertIn("No suggestions for good-pack", content)


class CliSuggestTests(unittest.TestCase):
    """CLI tests for the --suggest flag."""

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

    def test_suggest_single_pack_writes_file(self):
        pack = self.pack("suggest-pack", make_listing(title="T" * 200))
        listing_before = (pack / "listing.txt").read_text(encoding="utf-8")
        code, out = run_module([str(pack), "--suggest"])
        self.assertEqual(code, 1)
        self.assertIn("SUGGEST:", out)
        # listing.txt must not be modified
        self.assertEqual((pack / "listing.txt").read_text(encoding="utf-8"), listing_before)
        # Suggest file written outside the pack
        suggest_root = listing_lint.SUGGEST_ROOT
        suggest_file = suggest_root / "suggest-pack.suggest.txt"
        self.assertTrue(suggest_file.is_file())
        content = suggest_file.read_text(encoding="utf-8")
        self.assertIn("title too long", content.lower())

    def test_suggest_good_pack_no_suggestions(self):
        pack = self.pack("good-pack", make_listing())
        code, out = run_module([str(pack), "--suggest"])
        self.assertEqual(code, 0)
        suggest_file = listing_lint.SUGGEST_ROOT / "good-pack.suggest.txt"
        self.assertTrue(suggest_file.is_file())
        self.assertIn("No suggestions for good-pack", suggest_file.read_text(encoding="utf-8"))

    def test_suggest_with_all(self):
        self.pack("alpha", make_listing(title="T" * 200))
        self.pack("beta", make_listing(what_get=[]))
        code, out = run_module(["--packs-root", str(self.root), "--all", "--suggest"])
        self.assertEqual(code, 1)
        self.assertIn("== alpha", out)
        self.assertIn("== beta", out)
        self.assertIn("SUGGEST:", out)
        self.assertTrue((listing_lint.SUGGEST_ROOT / "alpha.suggest.txt").is_file())
        self.assertTrue((listing_lint.SUGGEST_ROOT / "beta.suggest.txt").is_file())

    def test_suggest_duplicate_tags(self):
        tags = GOOD_TAGS[:-1] + ["Focus", "focus"]
        pack = self.pack("dup-pack", make_listing(tags=tags))
        code, out = run_module([str(pack), "--suggest"])
        self.assertEqual(code, 1)
        self.assertIn("duplicate tags", out.lower())
        suggest_file = listing_lint.SUGGEST_ROOT / "dup-pack.suggest.txt"
        content = suggest_file.read_text(encoding="utf-8")
        self.assertIn("remove", content.lower())

    def test_suggest_all_caps(self):
        pack = self.pack("caps-pack", make_listing(what_get=["DAILY PLANNER sheet",
                                                              "US Letter PDF",
                                                              "Readme and licence"]))
        code, out = run_module([str(pack), "--suggest"])
        self.assertEqual(code, 0)  # caps is WARN, not FAIL
        self.assertIn("Title Case", out)
        suggest_file = listing_lint.SUGGEST_ROOT / "caps-pack.suggest.txt"
        content = suggest_file.read_text(encoding="utf-8")
        self.assertIn("Daily", content)
        self.assertIn("Planner", content)


if __name__ == "__main__":
    unittest.main()
