"""Roadmap #41 (.gitignore), review checklist doc, and #59b (600x600 thumbnail for the store)."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mockups  # noqa: E402
import product_builder as pb  # noqa: E402


def gitignore_lines():
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]


class GitignoreTests(unittest.TestCase):
    def test_local_only_files_are_ignored(self):
        lines = gitignore_lines()
        for entry in ("*.sqlite", ".env", ".env.*", "archive/", "old_copy_*/", "team/", "logs/"):
            self.assertIn(entry, lines)

    def test_saved_state_is_never_ignored(self):
        # "Save state" commits these every run; `git add` fails on an ignored path.
        # The database is the exception: only its encrypted copy is committed, so the plaintext is ignored.
        lines = gitignore_lines()
        self.assertIn("agent_data.db", lines)
        for kept in (".scheduler_locks/", ".scheduler_locks", "agent_data.db.enc", "exported_content/"):
            self.assertNotIn(kept, lines)

    def test_git_agrees(self):
        try:
            out = subprocess.run(["git", "check-ignore", "--no-index", "agent_data.db", "agent_data.db.enc",
                                  ".scheduler_locks/x.lock", "team/a.md", ".env", "x.sqlite", "logs/run.txt"], cwd=ROOT,
                                 capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git not available")
        ignored = set(out.stdout.split())
        self.assertIn("agent_data.db", ignored, "the plaintext database is ignored")
        self.assertNotIn("agent_data.db.enc", ignored, "the encrypted database is committed")
        self.assertNotIn(".scheduler_locks/x.lock", ignored)
        self.assertTrue({"team/a.md", ".env", "x.sqlite", "logs/run.txt"} <= ignored)


class ChecklistTests(unittest.TestCase):
    def test_review_checklist_is_in_docs(self):
        with open(os.path.join(ROOT, "docs", "REVIEW_CHECKLIST.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("## Prompt-injection guard", text)


@unittest.skipUnless(mockups.available(), "needs Pillow and poppler (free image tools)")
class ThumbnailTests(unittest.TestCase):
    def test_bundle_gets_a_600_square_thumbnail_from_its_real_cover(self):
        from PIL import Image
        spec = pb.bundle_spec(os.path.join(ROOT, "specs", "bundles", "adhd-printable-planner-kit.json"))
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, pb.build_product(spec, "bundle", d, folder_name="kit"))
            with Image.open(os.path.join(folder, "thumbnail.png")) as im:
                self.assertEqual(im.size, (600, 600))
            mockups.DRAWN = []
            try:
                info = mockups.product_info(folder)
                with Image.open(os.path.join(folder, "cover.png")) as cover:
                    mockups.thumbnail_image(info, cover.convert("RGB"))
                drawn = list(mockups.DRAWN)
            finally:
                mockups.DRAWN = None
        self.assertIn("14 pages · A4 + US Letter", drawn)     # proven facts only
        self.assertFalse([t for t in drawn if t.endswith("...")])


if __name__ == "__main__":
    unittest.main()
