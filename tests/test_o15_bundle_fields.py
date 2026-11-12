import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
import unittest

# Allow importing listing_fields from the project root.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

try:
    import listing_fields
except Exception as exc:  # pragma: no cover - import only fails in broken envs
    raise unittest.SkipTest(f"cannot import listing_fields: {exc}") from exc


BUNDLE_LISTING = """\
Title: ADHD Complete Digital Bundle (2 Pack Set)
Description: Get 2 complete, matching digital products together in this comprehensive bundle.

What you will receive in this collection:

1. Daily Focus Planner: A simple planner for daily tasks.
2. Brain Dump Planner: Capture every thought in one place.

Each pack includes its full deliverables, documentation, and user guides.
Everything is organized into dedicated product folders inside your instant download.

Tags: planner, productivity, adhd, bundle kit, complete collection, digital bundle, value pack

What you get:
- [Daily Focus Planner] 1 PDF planner
- [Daily Focus Planner] 1 Excel tracker
- [Brain Dump Planner] 1 PDF planner
"""

BUNDLE_LISTING_3_PACKS = BUNDLE_LISTING.replace("(2 Pack Set)", "(3 Pack Set)").replace("Get 2 complete", "Get 3 complete")


def _make_member_zips(zip_path, names):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.writestr(name, b"member pack content")


class _BundleFolder:
    """Create a temp bundle folder: listing.txt, cover.png, bundle.zip with N member zips."""

    def __init__(self, listing_body, member_count=2):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "listing.txt"), "w", encoding="utf-8") as f:
            f.write(listing_body)
        with open(os.path.join(self.tmp, "cover.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        members = [f"{i + 1}-pack-{i + 1}.zip" for i in range(member_count)]
        _make_member_zips(os.path.join(self.tmp, "bundle.zip"), members)

    def __enter__(self):
        return self.tmp

    def __exit__(self, *args):
        shutil.rmtree(self.tmp, ignore_errors=True)


class _PlainFolder:
    """Create a temp regular (non-bundle) upload pack folder with product.zip."""

    def __init__(self, listing_body):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "listing.txt"), "w", encoding="utf-8") as f:
            f.write(listing_body)
        with open(os.path.join(self.tmp, "cover.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        with zipfile.ZipFile(os.path.join(self.tmp, "product.zip"), "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("planner.pdf", b"pdf content")

    def __enter__(self):
        return self.tmp

    def __exit__(self, *args):
        shutil.rmtree(self.tmp, ignore_errors=True)


def _run_main_on_folder(folder):
    return subprocess.run(
        [sys.executable, os.path.join(_project_root, "listing_fields.py"), folder],
        capture_output=True, text=True,
    )


class TestBundleDetection(unittest.TestCase):
    def test_is_bundle_folder_true(self):
        with _BundleFolder(BUNDLE_LISTING) as tmp:
            self.assertTrue(listing_fields._is_bundle_folder(tmp))

    def test_is_bundle_folder_false_for_regular_pack(self):
        with _PlainFolder("Title: X\nDescription: Plan.\nTags: planner") as tmp:
            self.assertFalse(listing_fields._is_bundle_folder(tmp))

    def test_bundle_member_count_from_zip(self):
        with _BundleFolder(BUNDLE_LISTING, member_count=2) as tmp:
            self.assertEqual(listing_fields._bundle_member_count(tmp), 2)

    def test_bundle_member_count_three(self):
        with _BundleFolder(BUNDLE_LISTING_3_PACKS, member_count=3) as tmp:
            self.assertEqual(listing_fields._bundle_member_count(tmp), 3)

    def test_bundle_member_count_zero_without_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(listing_fields._bundle_member_count(tmp), 0)


class TestGumroadBundleBlock(unittest.TestCase):
    def test_gumroad_bundle_uses_bundle_zip(self):
        out = listing_fields.gumroad_block("My Bundle", "Desc.", "a, b", member_count=2)
        self.assertIn("Files to upload: bundle.zip", out)
        self.assertNotIn("product.zip", out)

    def test_gumroad_bundle_has_members_line(self):
        out = listing_fields.gumroad_block("My Bundle", "Desc.", "a, b", member_count=2)
        self.assertIn("BUNDLE", out)
        self.assertIn("Members: 2 packs", out)

    def test_gumroad_bundle_keeps_cover_and_price(self):
        out = listing_fields.gumroad_block("My Bundle", "Desc.", "a, b", member_count=2)
        self.assertIn("Cover: cover.png", out)
        self.assertIn("Price: SET BY OWNER", out)
        self.assertNotIn("$", out)

    def test_gumroad_bundle_no_members_when_count_none(self):
        out = listing_fields.gumroad_block("My Bundle", "Desc.", "a, b")
        self.assertIn("Files to upload: product.zip", out)
        self.assertNotIn("BUNDLE", out)
        self.assertNotIn("Members:", out)


class TestTopmateBundleBlock(unittest.TestCase):
    def test_topmate_bundle_uses_bundle_zip(self):
        out = listing_fields.topmate_block("My Bundle", "Desc.", member_count=2)
        self.assertIn("Files: bundle.zip", out)
        self.assertNotIn("product.zip", out)

    def test_topmate_bundle_has_members_line(self):
        out = listing_fields.topmate_block("My Bundle", "Desc.", member_count=2)
        self.assertIn("BUNDLE", out)
        self.assertIn("Members: 2 packs", out)

    def test_topmate_bundle_keeps_price(self):
        out = listing_fields.topmate_block("My Bundle", "Desc.", member_count=2)
        self.assertIn("Price: SET BY OWNER", out)
        self.assertNotIn("$", out)


class TestInstamojoBundleBlock(unittest.TestCase):
    def test_instamojo_bundle_uses_bundle_zip(self):
        out = listing_fields.instamojo_block("My Bundle", "Desc.", member_count=2)
        self.assertIn("Files to upload: bundle.zip", out)
        self.assertNotIn("product.zip", out)

    def test_instamojo_bundle_has_members_line(self):
        out = listing_fields.instamojo_block("My Bundle", "Desc.", member_count=2)
        self.assertIn("BUNDLE", out)
        self.assertIn("Members: 2 packs", out)

    def test_instamojo_bundle_keeps_cover_and_price(self):
        out = listing_fields.instamojo_block("My Bundle", "Desc.", member_count=2)
        self.assertIn("Cover: cover.png", out)
        self.assertIn("Price: SET BY OWNER", out)
        self.assertNotIn("$", out)


class TestBuildBlocksBundle(unittest.TestCase):
    def test_build_blocks_bundle_all_stores(self):
        out = listing_fields.build_blocks("X", "D", "t", "all", member_count=2)
        self.assertIn("GUMROAD", out)
        self.assertIn("TOPMATE", out)
        self.assertIn("INSTAMOJO", out)
        self.assertIn("bundle.zip", out)
        self.assertIn("BUNDLE", out)
        self.assertIn("Members: 2 packs", out)

    def test_build_blocks_bundle_gumroad_only(self):
        out = listing_fields.build_blocks("X", "D", "t", "gumroad", member_count=3)
        self.assertIn("GUMROAD", out)
        self.assertNotIn("TOPMATE", out)
        self.assertNotIn("INSTAMOJO", out)
        self.assertIn("Members: 3 packs", out)

    def test_build_blocks_non_bundle_still_product_zip(self):
        out = listing_fields.build_blocks("X", "D", "t", "all")
        self.assertIn("Files to upload: product.zip", out)
        self.assertNotIn("BUNDLE", out)


class TestBundleMainEndToEnd(unittest.TestCase):
    def test_main_prints_bundle_blocks(self):
        with _BundleFolder(BUNDLE_LISTING) as tmp:
            proc = _run_main_on_folder(tmp)
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout
        self.assertIn("GUMROAD", out)
        self.assertIn("TOPMATE", out)
        self.assertIn("INSTAMOJO", out)
        self.assertIn("Files to upload: bundle.zip", out)
        self.assertIn("BUNDLE", out)
        self.assertIn("Members: 2 packs", out)
        self.assertIn("Cover: cover.png", out)
        self.assertIn("Price: SET BY OWNER", out)
        self.assertNotIn("product.zip", out)
        self.assertNotIn("$", out)

    def test_main_regular_folder_still_product_zip(self):
        with _PlainFolder("Title: X\nDescription: Plan.\nTags: planner") as tmp:
            proc = _run_main_on_folder(tmp)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Files to upload: product.zip", proc.stdout)
        self.assertNotIn("BUNDLE", proc.stdout)


class TestBundleMoneyRejection(unittest.TestCase):
    def test_bundle_listing_refused_when_money_present(self):
        body = BUNDLE_LISTING + "\nDescription: costs $9."
        with _BundleFolder(body) as tmp:
            proc = _run_main_on_folder(tmp)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("$9", proc.stderr)
        self.assertNotIn("BUNDLE", proc.stdout)


if __name__ == "__main__":
    unittest.main()
