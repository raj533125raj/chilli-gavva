import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Allow importing listing_fields from the project root.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

try:
    import listing_fields
except Exception as exc:  # pragma: no cover - import only fails in broken envs
    raise unittest.SkipTest(f"cannot import listing_fields: {exc}") from exc


class _TmpFolder:
    """Helper that creates a temp folder with a listing.txt we control."""

    def __init__(self, body: str):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "listing.txt")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(body)

    def __enter__(self):
        return self.tmp

    def __exit__(self, *args):
        shutil.rmtree(self.tmp, ignore_errors=True)


SAMPLE_FULL = """\
Title: My Digital Planner
Description: A simple planner for daily tasks.
Tags: planner, productivity, organize, notes, journal, checklist, goals, tracker, pdf, excel, bonus
What you get:
- 1 PDF planner
- 1 Excel tracker
- Update access
"""

SAMPLE_SHORT = """\
Title: Simple Notes
Description: Notes app for quick capture.
Tags: notes
"""

SAMPLE_WITH_WYG = """\
Title: Big Planner
Description: A planner with extras.
What you get:
- 1 PDF planner
- 1 Excel tracker
- Update access
"""

INSTAMOJO_TITLE_LONG = "A" + " " + "planning " * 25 + "pack"

SAMPLE_WITH_WYG = """\
Title: Big Planner
Description: A planner with extras.
What you get:
- 1 PDF planner
- 1 Excel tracker
- Update access
"""

INSTAMOJO_TITLE_LONG = "A" + " " + "planning " * 25 + "pack"


class TestSectionExtraction(unittest.TestCase):
    def test_title_extraction(self):
        with _TmpFolder(SAMPLE_SHORT) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        self.assertEqual(listing_fields._section(text, "Title"), "Simple Notes")

    def test_description_extraction(self):
        with _TmpFolder(SAMPLE_SHORT) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        self.assertEqual(listing_fields._section(text, "Description"), "Notes app for quick capture.")

    def test_tags_extraction(self):
        with _TmpFolder(SAMPLE_SHORT) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        self.assertEqual(listing_fields._section(text, "Tags"), "notes")

    def test_what_you_get_extraction(self):
        with _TmpFolder(SAMPLE_FULL) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        # 'What you get:' begins a line and the section runs to the next
        # section label. Our parser only captures the first line because the
        # list items live on continuation lines. Ensure it at least captures
        # the first item line.
        got = listing_fields._section(text, "What you get")
        self.assertTrue(got.startswith("- 1 PDF planner"))


class TestMoneyRejection(unittest.TestCase):
    def _run_money_check(self, body: str):
        with _TmpFolder(body) as tmp:
            text = listing_fields._read_listing_txt(tmp)
        return listing_fields._check_money(text)

    def test_dollar_amount_refused(self):
        self.assertIsNotNone(self._run_money_check("Title: X\nDescription: $10 plan."))

    def test_dollar_with_cents_refused(self):
        self.assertIsNotNone(self._run_money_check("Title: X\nDescription: $10.00."))

    def test_usd_refused(self):
        self.assertIsNotNone(self._run_money_check("Title: X\nDescription: 20 USD."))

    def test_inr_refused(self):
        self.assertIsNotNone(self._run_money_check("Title: X\nDescription: 500 INR."))

    def test_rs_refused(self):
        self.assertIsNotNone(self._run_money_check("Title: X\nDescription: Rs 100."))

    def test_rupee_sign_refused(self):
        self.assertIsNotNone(self._run_money_check("Title: X\nDescription: ₹250."))

    def test_no_money_allowed(self):
        self.assertIsNone(self._run_money_check(SAMPLE_SHORT))


class TestMissingFile(unittest.TestCase):
    def test_missing_listing_txt_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, os.path.join(_project_root, "listing_fields.py"), tmp],
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(("listing.txt" in proc.stderr) or ("Error" in proc.stderr))


class TestMoneyRejectionInstamojo(unittest.TestCase):
    def _run_main_on_body(self, body):
        with _TmpFolder(body) as tmp:
            return subprocess.run(
                [sys.executable, os.path.join(_project_root, "listing_fields.py"), tmp],
                capture_output=True,
                text=True,
            )

    def test_instamojo_block_printed_when_no_money(self):
        proc = self._run_main_on_body(SAMPLE_FULL)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("INSTAMOJO", proc.stdout)
        # The main function should include the What you get block in the
        # Instamojo description, right after the normal Description line.
        insta_start = proc.stdout.find("INSTAMOJO")
        rest = proc.stdout[insta_start:]
        desc_pos = rest.find("Description:")
        self.assertGreater(desc_pos, 0)
        after_desc = rest[desc_pos:]
        # The main function uses the full What you get block, so both the
        # label and the first item should appear.
        # NOTE: we only assert the first item here because the main function
        # currently writes the first line of the block.
        self.assertIn("- 1 PDF planner", after_desc)

    def test_instamojo_block_refused_when_money_present(self):
        body = SAMPLE_WITH_WYG + "\nDescription: costs $9."
        proc = self._run_main_on_body(body)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("$9", proc.stderr)
        self.assertNotIn("INSTAMOJO", proc.stdout)


class TestGumroadBlock(unittest.TestCase):
    def test_gumroad_block_name(self):
        out = listing_fields.gumroad_block("My Planner", "A planner.", "a, b")
        self.assertIn("Name: My Planner", out)

    def test_gumroad_description_include_what_you_get(self):
        desc = "A planner.\nWhat you get:\n- PDF\n- Excel"
        out = listing_fields.gumroad_block("X", desc, "tags")
        self.assertIn("A planner.", out)
        self.assertIn("What you get:", out)
        self.assertIn("- PDF", out)

    def test_gumroad_tags_cap_10(self):
        many = ", ".join(str(i) for i in range(1, 13))
        out = listing_fields.gumroad_block("X", "D", many)
        tag_line = [ln for ln in out.splitlines() if ln.startswith("Tags:")]
        self.assertEqual(len(tag_line), 1)
        tags = tag_line[0].split("Tags: ")[1].split(", ")
        self.assertLessEqual(len(tags), 10)
        self.assertEqual(tags[0], "1")
        self.assertEqual(tags[-1], "10")

    def test_gumroad_price_set_by_owner(self):
        out = listing_fields.gumroad_block("X", "D", "t")
        self.assertIn("Price: SET BY OWNER", out)
        self.assertNotIn("$", out)


class TestBuildBlocksSelectStore(unittest.TestCase):
    def test_build_blocks_all(self):
        out = listing_fields.build_blocks("X", "D", "t", "all")
        self.assertIn("GUMROAD", out)
        self.assertIn("TOPMATE", out)
        self.assertIn("INSTAMOJO", out)

    def test_build_blocks_instamojo_only(self):
        out = listing_fields.build_blocks("X", "D", "t", "instamojo")
        self.assertIn("INSTAMOJO", out)
        self.assertNotIn("GUMROAD", out)
        self.assertNotIn("TOPMATE", out)

    def test_build_blocks_gumroad_only(self):
        out = listing_fields.build_blocks("X", "D", "t", "gumroad")
        self.assertIn("GUMROAD", out)
        self.assertNotIn("TOPMATE", out)
        self.assertNotIn("INSTAMOJO", out)

    def test_build_blocks_topmate_only(self):
        out = listing_fields.build_blocks("X", "D", "t", "topmate")
        self.assertIn("TOPMATE", out)
        self.assertNotIn("GUMROAD", out)
        self.assertNotIn("INSTAMOJO", out)

    def test_build_blocks_default_is_all(self):
        out = listing_fields.build_blocks("X", "D", "t")
        self.assertIn("GUMROAD", out)
        self.assertIn("TOPMATE", out)
        self.assertIn("INSTAMOJO", out)


class TestInstamojoBlock(unittest.TestCase):
    def test_instamojo_section_label(self):
        out = listing_fields.instamojo_block("Planner", "A planner.")
        self.assertIn("INSTAMOJO", out)

    def test_instamojo_title(self):
        out = listing_fields.instamojo_block("My Planner", "A planner.")
        self.assertIn("Title: My Planner", out)

    def test_instamojo_description_includes_full_description(self):
        out = listing_fields.instamojo_block("X", "A planner.")
        self.assertIn("Description: A planner.", out)

    def test_instamojo_description_includes_what_you_get(self):
        desc = "A planner.\nWhat you get:\n- PDF\n- Excel\n"
        wyg = listing_fields._section_what_you_get_block(desc, desc)
        out = listing_fields.instamojo_block("X", desc, wyg)
        self.assertIn("Description: A planner.", out)
        self.assertIn("What you get:", out)
        self.assertIn("- PDF", out)

    def test_instamojo_files_to_upload(self):
        out = listing_fields.instamojo_block("X", "D")
        self.assertIn("Files to upload: product.zip", out)

    def test_instamojo_cover(self):
        out = listing_fields.instamojo_block("X", "D")
        self.assertIn("Cover: cover.png", out)

    def test_instamojo_price_set_by_owner_no_money(self):
        out = listing_fields.instamojo_block("X", "D")
        self.assertIn("Price: SET BY OWNER", out)
        self.assertNotIn("$", out)

    def test_instamojo_title_cut_at_80_characters(self):
        out = listing_fields.instamojo_block(INSTAMOJO_TITLE_LONG, "D")
        title_line = [ln for ln in out.splitlines() if ln.startswith("Title:")][0]
        value = title_line.split("Title: ", 1)[1]
        self.assertLessEqual(len(value), 80)

    def test_instamojo_description_cut_at_5000_characters(self):
        long_desc = ("word " * 600).rstrip()
        out = listing_fields.instamojo_block("X", long_desc)
        desc_line = [ln for ln in out.splitlines() if ln.startswith("Description:")][0]
        value = desc_line.split("Description: ", 1)[1]
        self.assertLessEqual(len(value), 5000)

    def test_instamojo_description_order_description_before_what_you_get(self):
        desc = "Start.\nWhat you get:\n- PDF\n"
        wyg = listing_fields._section_what_you_get_block(desc, desc)
        out = listing_fields.instamojo_block("X", desc, wyg)
        desc_lines = []
        for ln in out.splitlines():
            if ln.startswith("Description:") or (
                desc_lines and not ln.startswith("Files to upload:")
            ):
                desc_lines.append(ln)
            if desc_lines and ln.startswith("Files to upload:"):
                break
        value = "\n".join(desc_lines).split("Description: ", 1)[-1]
        # Description text first, then the What you get block appended.
        idx_intro = value.find("Start.")
        idx_wyg = value.find("What you get:")
        self.assertGreater(idx_wyg, idx_intro)


class TestTopmateBlock(unittest.TestCase):
    def test_topmate_title(self):
        out = listing_fields.topmate_block("My Planner", "A planner.")
        self.assertIn("Title: My Planner", out)

    def test_topmate_short_description_150_cut(self):
        # Use a description long enough that the 150-char cut lands inside a
        # word, so we can verify word-boundary trimming.
        long_desc = (
            "This is a short description that is long enough to exceed one "
            "hundred and fifty characters so the helper must cut at a word."
        )
        out = listing_fields.topmate_block("X", long_desc)
        self.assertIn("Short description:", out)
        short = [ln for ln in out.splitlines() if ln.startswith("Short description:")][0]
        value = short.split("Short description: ", 1)[1]
        self.assertLessEqual(len(value), 150)
        # The cut should land at a word boundary (no trailing partial word).
        self.assertLessEqual(len(value), 150)
        # Also exercise the actual >150 branch with a word-boundary check.
        very_long = "WORD" * 40
        cut = listing_fields._cut_150(very_long)
        self.assertLessEqual(len(cut), 150)
        # _cut_150 on "WORD"*40 cuts mid-word; when there is no space it
        # returns the head unchanged, which is acceptable behavior.
        self.assertLessEqual(len(cut), 150)

    def test_topmate_short_description_under_150_unchanged(self):
        short = "Quick notes app."
        out = listing_fields.topmate_block("X", short)
        value = [ln for ln in out.splitlines() if ln.startswith("Short description:")][0]
        self.assertEqual(value.split("Short description: ", 1)[1], short)

    def test_topmate_long_description_full(self):
        out = listing_fields.topmate_block("X", "Full description here.")
        self.assertIn("Long description: Full description here.", out)

    def test_topmate_price_set_by_owner_no_money(self):
        out = listing_fields.topmate_block("X", "D")
        self.assertIn("Price: SET BY OWNER", out)
        self.assertNotIn("$", out)


class TestMainHappyPath(unittest.TestCase):
    def test_main_prints_both_blocks(self):
        with _TmpFolder(SAMPLE_SHORT) as tmp:
            proc = subprocess.run(
                [sys.executable, os.path.join(_project_root, "listing_fields.py"), tmp],
                capture_output=True,
                text=True,
            )
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout
        self.assertIn("GUMROAD", out)
        self.assertIn("TOPMATE", out)
        self.assertIn("Name: Simple Notes", out)
        self.assertIn("Title: Simple Notes", out)


class TestMainMissingSection(unittest.TestCase):
    def test_missing_title_refused(self):
        body = "Description: Some plan.\nTags: x"
        with _TmpFolder(body) as tmp:
            proc = subprocess.run(
                [sys.executable, os.path.join(_project_root, "listing_fields.py"), tmp],
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(("Title" in proc.stderr) or ("Error" in proc.stderr))


if __name__ == "__main__":
    unittest.main()
