import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

# Allow importing listing_fields from the project root.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

try:
    import listing_fields
except Exception as exc:  # pragma: no cover - import only fails in broken envs
    raise unittest.SkipTest(f"cannot import listing_fields: {exc}") from exc


class _TmpFolder:
    """Create a temp folder with a listing.txt we control."""

    def __init__(self, body, *, newline="\n"):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "listing.txt")
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            # Write with the requested newline style so we can test CR/LF etc.
            f.write(body.replace("\n", newline))

    def __enter__(self):
        return self.tmp

    def __exit__(self, *args):
        shutil.rmtree(self.tmp, ignore_errors=True)


def _run_main_on_body(body, *, newline="\n", extra_args=None):
    if extra_args is None:
        extra_args = []
    with _TmpFolder(body, newline=newline) as tmp:
        cmd = [sys.executable, os.path.join(_project_root, "listing_fields.py"), tmp]
        if extra_args:
            cmd.extend(extra_args)
        return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Helpers that build category-specific listing bodies
# ---------------------------------------------------------------------------

TELUGU_TITLE = "ప్లానర్ టూల్"
TELUGU_DESC = "ఈ ప్లానర్ రోజు పనులను ట్రాక్ చేయడానికి సహాయపడుతుంది."
TELUGU_TAGS = "ప్లానర్, ప్రొడక్టివిటీ, నోట్స్, ట్రాకర్"
EMOJI_TITLE = "Weekly planner 📅 Planning pack 📅"
EMOJI_DESC = "Plan your week with emojis ✨ and checkboxes ☑️."
EMOJI_TAGS = "planner, productivity, notes, weekly, journal, goals, tracker, pdf, organizer, focus"

LONG_DESC_6000 = "word " * 1200  # well over 6000 chars


class TestEdgeCharactersInTitle(unittest.TestCase):
    """Emoji and Telugu letters must survive through the listing blocks."""

    def test_telugu_title_passes_money_check(self):
        body = f"Title: {TELUGU_TITLE}\nDescription: Plan.\nTags: planner"
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        self.assertIsNone(listing_fields._check_money(text))

    def test_telugu_title_in_gumroad_block(self):
        body = f"Title: {TELUGU_TITLE}\nDescription: Plan.\nTags: planner"
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        title = listing_fields._section(text, "Title")
        out = listing_fields.gumroad_block(title, "Plan.", "planner")
        self.assertIn(TELUGU_TITLE, out)

    def test_telugu_title_in_topmate_block(self):
        body = f"Title: {TELUGU_TITLE}\nDescription: Plan.\nTags: planner"
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        title = listing_fields._section(text, "Title")
        out = listing_fields.topmate_block(title, "Plan.")
        self.assertIn(TELUGU_TITLE, out)

    def test_telugu_title_in_main_output(self):
        body = f"Title: {TELUGU_TITLE}\nDescription: Plan.\nTags: planner"
        proc = _run_main_on_body(body)
        self.assertEqual(proc.returncode, 0)
        self.assertIn(TELUGU_TITLE, proc.stdout)

    def test_emoji_title_in_gumroad_block(self):
        body = f"Title: {EMOJI_TITLE}\nDescription: Plan.\nTags: planner"
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        title = listing_fields._section(text, "Title")
        out = listing_fields.gumroad_block(title, "Plan.", "planner")
        self.assertIn(EMOJI_TITLE, out)

    def test_emoji_title_in_instamojo_block(self):
        body = f"Title: {EMOJI_TITLE}\nDescription: Plan.\nTags: planner"
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        title = listing_fields._section(text, "Title")
        out = listing_fields.instamojo_block(title, "Plan.")
        self.assertIn(EMOJI_TITLE, out)


class TestEdgeCharactersInDescription(unittest.TestCase):
    """Emoji and Telugu letters in Description must be preserved."""

    def test_telugu_description_in_gumroad_block(self):
        body = f"Title: X\nDescription: {TELUGU_DESC}\nTags: planner"
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        desc = listing_fields._section(text, "Description")
        out = listing_fields.gumroad_block("X", desc, "planner")
        self.assertIn(TELUGU_DESC, out)

    def test_telugu_description_in_topmate_block(self):
        body = f"Title: X\nDescription: {TELUGU_DESC}\nTags: planner"
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        desc = listing_fields._section(text, "Description")
        out = listing_fields.topmate_block("X", desc)
        self.assertIn(TELUGU_DESC, out)

    def test_emoji_description_in_gumroad_block(self):
        body = f"Title: X\nDescription: {EMOJI_DESC}\nTags: planner"
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        desc = listing_fields._section(text, "Description")
        out = listing_fields.gumroad_block("X", desc, "planner")
        self.assertIn(EMOJI_DESC, out)

    def test_emoji_description_in_topmate_long_description(self):
        body = f"Title: X\nDescription: {EMOJI_DESC}\nTags: planner"
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        desc = listing_fields._section(text, "Description")
        out = listing_fields.topmate_block("X", desc)
        self.assertIn(EMOJI_DESC, out)


class TestEdgeCharactersInTags(unittest.TestCase):
    """Tags containing emoji/Telugu must be accepted by the money check and kept."""

    def test_telugu_tags_pass_money_check(self):
        body = f"Title: X\nDescription: Plan.\nTags: {TELUGU_TAGS}"
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        self.assertIsNone(listing_fields._check_money(text))

    def test_emoji_tags_pass_money_check(self):
        body = f"Title: X\nDescription: Plan.\nTags: {EMOJI_TAGS}"
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        self.assertIsNone(listing_fields._check_money(text))


class TestTagCountEdges(unittest.TestCase):
    """1 tag and 25 tags must be accepted; Gumroad caps at 10."""

    def test_single_tag_passes_money_check(self):
        body = "Title: X\nDescription: Plan.\nTags: one"
        proc = _run_main_on_body(body)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Tags: one", proc.stdout)

    def test_25_tags_passes_money_check(self):
        tags = ", ".join(f"tag{i}" for i in range(1, 26))
        body = f"Title: X\nDescription: Plan.\nTags: {tags}"
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        self.assertIsNone(listing_fields._check_money(text))


class TestEmptyWhatYouGet(unittest.TestCase):
    """Empty 'What you get' must not break Gumroad or Instamojo blocks."""

    def test_gumroad_block_when_what_you_get_empty(self):
        desc = "A planner for daily tasks."
        out = listing_fields.gumroad_block("X", desc, "planner")
        self.assertIn("Description: A planner for daily tasks.", out)
        self.assertNotIn("What you get:", out)

    def test_instamojo_block_without_what_you_get_section(self):
        desc = "A planner for daily tasks."
        wyg = listing_fields._section_what_you_get_block(desc, desc)
        self.assertEqual(wyg, "")
        out = listing_fields.instamojo_block("X", desc, wyg)
        self.assertIn("Description: A planner for daily tasks.", out)
        self.assertNotIn("What you get:", out)


class TestLineEndingVariants(unittest.TestCase):
    """CRLF, CR-only and LF-only files must parse the same."""

    body = "Title: Line Endings Test\nDescription: Plan.\nTags: planner"

    def test_crlf_parsed(self):
        proc = _run_main_on_body(self.body, newline="\r\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Name: Line Endings Test", proc.stdout)

    def test_cr_only_parsed(self):
        proc = _run_main_on_body(self.body, newline="\r")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Name: Line Endings Test", proc.stdout)

    def test_lf_only_parsed(self):
        proc = _run_main_on_body(self.body, newline="\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Name: Line Endings Test", proc.stdout)


class TestVeryLongDescription(unittest.TestCase):
    """6000-char descriptions must still produce both store blocks."""

    def _long_body(self, with_what_you_get=False):
        desc = LONG_DESC_6000
        if with_what_you_get:
            desc += "\nWhat you get:\n- PDF\n- Excel\n"
        return f"Title: Long Desc\nDescription: {desc}\nTags: planner"

    def test_gumroad_block_for_6000_char_description(self):
        body = self._long_body()
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        desc = listing_fields._section(text, "Description")
        out = listing_fields.gumroad_block("Long Desc", desc, "planner")
        self.assertIn("GUMROAD", out)
        self.assertIn(desc, out)

    def test_topmate_block_for_6000_char_description(self):
        body = self._long_body()
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        desc = listing_fields._section(text, "Description")
        out = listing_fields.topmate_block("Long Desc", desc)
        self.assertIn("TOPMATE", out)
        self.assertIn("Long description:", out)
        self.assertIn(desc, out)

    def test_instamojo_block_for_6000_char_description(self):
        body = self._long_body(with_what_you_get=True)
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        desc = listing_fields._section(text, "Description")
        wyg = listing_fields._section_what_you_get_block(desc, text)
        out = listing_fields.instamojo_block("Long Desc", desc, wyg)
        self.assertIn("INSTAMOJO", out)
        self.assertIn("Description:", out)

    def test_main_prints_all_three_blocks_for_6000_char_description(self):
        body = self._long_body(with_what_you_get=True)
        proc = _run_main_on_body(body)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("GUMROAD", proc.stdout)
        self.assertIn("TOPMATE", proc.stdout)
        self.assertIn("INSTAMOJO", proc.stdout)


class TestMoneyPatternSpaces(unittest.TestCase):
    """Money with spaces ("Rs 99", "₹ 99", "99 USD") must be refused."""

    def _money_check(self, body):
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        return listing_fields._check_money(text)

    def test_rs_with_space_refused(self):
        self.assertIsNotNone(self._money_check("Title: X\nDescription: costs Rs 99."))

    def test_rupee_sign_with_space_refused(self):
        self.assertIsNotNone(self._money_check("Title: X\nDescription: costs ₹ 99."))

    def test_usd_with_space_refused(self):
        self.assertIsNotNone(self._money_check("Title: X\nDescription: costs 99 USD."))


class TestStoreOptionValues(unittest.TestCase):
    """--store option (when present in the helper) should select the requested store."""

    def _store_proc(self, store):
        body = "Title: Store Test\nDescription: Plan.\nTags: planner"
        return _run_main_on_body(body, extra_args=["--store", store])

    def test_store_gumroad_only(self):
        proc = self._store_proc("gumroad")
        if proc.returncode == 0:
            out = proc.stdout
            self.assertIn("GUMROAD", out)
            self.assertNotIn("TOPMATE", out)
            self.assertNotIn("INSTAMOJO", out)

    def test_store_topmate_only(self):
        proc = self._store_proc("topmate")
        if proc.returncode == 0:
            out = proc.stdout
            self.assertIn("TOPMATE", out)
            self.assertNotIn("GUMROAD", out)
            self.assertNotIn("INSTAMOJO", out)

    def test_store_instamojo_only(self):
        proc = self._store_proc("instamojo")
        if proc.returncode == 0:
            out = proc.stdout
            self.assertIn("INSTAMOJO", out)
            self.assertNotIn("GUMROAD", out)
            self.assertNotIn("TOPMATE", out)

    def test_store_all_is_default_like(self):
        proc = self._store_proc("all")
        if proc.returncode == 0:
            out = proc.stdout
            self.assertIn("GUMROAD", out)
            self.assertIn("TOPMATE", out)
            self.assertIn("INSTAMOJO", out)
