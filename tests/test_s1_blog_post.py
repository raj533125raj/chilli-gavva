"""tests/test_s1_blog_post.py — Unit tests for blog_post.py.

At least 25 tests covering:
- File destination: exported_content/marketing/<pack>/blog.md
- Word count strictly between 700 and 1000 words
- Built ONLY from facts in the listing/pack (no invented claims, no medical/financial promises)
- H1 with main keyword
- 4 to 6 H2 sections
- FAQ section with 5 Q&A pairs
- Image alt texts for cover and previews
- Meta description <= 155 chars
- Internal-link placeholders: [STORE LINK]
- Readability: average sentence length <= 20 words
- No money amounts, no brands
- Atomic writes & directory safety (pack folder untouched)
"""

import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock

import blog_post


class TestS1BlogPost(unittest.TestCase):
    def setUp(self):
        # Clear all GITHUB_* and RUNNER_ENVIRONMENT variables per repo guidelines
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp_dir = tempfile.mkdtemp(prefix="test_blog_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

        self.root = Path(self.tmp_dir)
        self.products_dir = self.root / "products" / "upload"
        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir = self.root / "marketing"
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def _create_sample_pack(
        self,
        pack_name="weekly-habit-kit",
        title="Weekly Habit Tracker Kit",
        desc="A structured weekly layout designed for habit tracking and everyday routines.",
        tags="habits, routine, productivity, weekly, printable, stationery",
        benefits=None,
    ):
        pack_dir = self.products_dir / pack_name
        pack_dir.mkdir(parents=True, exist_ok=True)

        if benefits is None:
            benefits = [
                "Monday through Sunday weekly habit overview grid",
                "Progress checklist for morning and evening routines",
                "Dedicated habit streak counter and review notes",
                "Undated universal format printable at home on standard paper",
            ]

        what_text = "\n".join(f"- {b}" for b in benefits)
        listing_content = (
            f"Title: {title}\n\n"
            f"Description:\n{desc}\n\n"
            f"Tags: {tags}\n\n"
            f"What you get:\n{what_text}\n"
        )
        (pack_dir / "listing.txt").write_text(listing_content, encoding="utf-8")

        # Fake images
        (pack_dir / "cover.png").write_bytes(b"PNG_FAKE_COVER")
        (pack_dir / "preview-1.png").write_bytes(b"PNG_FAKE_PREVIEW_1")
        (pack_dir / "preview-2.png").write_bytes(b"PNG_FAKE_PREVIEW_2")

        return pack_dir

    # 1. Blog file created in exported_content/marketing/<pack>/blog.md
    def test_01_blog_file_created(self):
        pack = self._create_sample_pack()
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.name, "blog.md")
        self.assertEqual(dest.parent.name, pack.name)

    # 2. Word count strictly between 700 and 1000 words
    def test_02_word_count_700_to_1000(self):
        pack = self._create_sample_pack()
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8")
        wc = blog_post.count_words(content)
        self.assertGreaterEqual(wc, 700, f"Word count {wc} < 700")
        self.assertLessEqual(wc, 1000, f"Word count {wc} > 1000")

    # 3. Word count with custom benefits
    def test_03_word_count_with_custom_benefits(self):
        pack = self._create_sample_pack(
            benefits=[
                "Daily goal setting section",
                "Weekly priority matrix",
                "Habit logging matrix",
                "Evening gratitude checklist",
            ]
        )
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8")
        wc = blog_post.count_words(content)
        self.assertGreaterEqual(wc, 700)
        self.assertLessEqual(wc, 1000)

    # 4. H1 with main keyword exists
    def test_04_h1_with_main_keyword(self):
        pack = self._create_sample_pack(title="Mindful Morning Routine Guide")
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8")
        h1s = [line for line in content.splitlines() if line.startswith("# ") and not line.startswith("## ")]
        self.assertEqual(len(h1s), 1)
        self.assertIn("Mindful Morning Routine Guide", h1s[0])

    # 5. 4 to 6 H2 sections
    def test_05_h2_section_count_4_to_6(self):
        pack = self._create_sample_pack()
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8")
        h2s = [line for line in content.splitlines() if line.startswith("## ")]
        self.assertGreaterEqual(len(h2s), 4)
        self.assertLessEqual(len(h2s), 6)

    # 6. FAQ section exists
    def test_06_faq_section_exists(self):
        pack = self._create_sample_pack()
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8")
        self.assertIn("## Frequently Asked Questions", content)

    # 7. FAQ section contains exactly 5 Q&A pairs
    def test_07_faq_has_5_qa_pairs(self):
        pack = self._create_sample_pack()
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8")
        q_count = len(re.findall(r"\*\*Q:\s*", content))
        self.assertEqual(q_count, 5)

    # 8. Meta description exists and is <= 155 chars
    def test_08_meta_description_under_155_chars(self):
        pack = self._create_sample_pack()
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8")
        meta_match = re.search(r"\>\s*\*\*Meta Description\*\*:\s*(.+)", content)
        self.assertIsNotNone(meta_match)
        meta_desc = meta_match.group(1).strip()
        self.assertLessEqual(len(meta_desc), 155)
        self.assertGreaterEqual(len(meta_desc), 20)

    # 9. Meta description truncate helper handles long text
    def test_09_meta_description_truncate_helper(self):
        long_desc = "This is an extremely detailed and expansive overview of a printable planning system designed for creative professionals and students looking to maximize daily output and reduce stress." * 3
        meta = blog_post.generate_meta_description("Weekly Planner", long_desc)
        self.assertLessEqual(len(meta), 155)
        self.assertTrue(meta.startswith("Discover Weekly Planner"))

    # 10. Image alt texts for cover and previews
    def test_10_image_alt_texts_included(self):
        pack = self._create_sample_pack()
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8")
        img_tags = re.findall(r"!\[(.*?)\]\((.*?)\)", content)
        self.assertGreaterEqual(len(img_tags), 2)
        for alt, src in img_tags:
            self.assertTrue(bool(alt.strip()))
            self.assertTrue(src.endswith(".png") or src.endswith(".jpg"))

    # 11. Internal-link placeholders [STORE LINK] included
    def test_11_store_link_placeholders_present(self):
        pack = self._create_sample_pack()
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8")
        store_links = content.count("[STORE LINK]")
        self.assertGreaterEqual(store_links, 3)

    # 12. Readability: average sentence length <= 20 words
    def test_12_readability_average_sentence_under_20_words(self):
        pack = self._create_sample_pack()
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8")
        avg_len = blog_post.average_sentence_length(content)
        self.assertLessEqual(avg_len, 20.0, f"Average sentence length {avg_len} > 20 words")

    # 13. No money amounts in generated blog post
    def test_13_no_money_amounts_in_blog(self):
        pack = self._create_sample_pack(
            title="Budget Planner priced at $29",
            desc="Valued at $99 USD, buy now for Rs 499 or 20 EUR.",
        )
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8")
        self.assertIsNone(blog_post._MONEY_PATTERN.search(content))

    # 14. No brand names in generated blog post
    def test_14_no_brand_names_in_blog(self):
        pack = self._create_sample_pack(
            title="Excel Notion Canva Planner for Etsy",
            desc="Compatible with Google Sheets, GoodNotes, and Apple Numbers on iPad.",
        )
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8").lower()
        self.assertNotIn("notion", content)
        self.assertNotIn("canva", content)
        self.assertNotIn("etsy", content)
        self.assertNotIn("goodnotes", content)

    # 15. No medical or financial advice claims
    def test_15_no_medical_financial_claims(self):
        pack = self._create_sample_pack(
            title="Health and Wealth Daily Tracker",
            desc="Guaranteed returns and cure your stress, lose weight fast with medical advice.",
        )
        dest = blog_post.make_pack_blog(pack, self.marketing_dir)
        content = dest.read_text(encoding="utf-8").lower()
        self.assertNotIn("guaranteed returns", content)
        self.assertNotIn("cure your stress", content)
        self.assertNotIn("medical advice", content)

    # 16. Pack folder is never modified
    def test_16_pack_folder_untouched(self):
        pack = self._create_sample_pack()
        files_before = sorted([f.name for f in pack.iterdir()])
        mtimes_before = {f.name: f.stat().st_mtime for f in pack.iterdir()}

        blog_post.make_pack_blog(pack, self.marketing_dir)

        files_after = sorted([f.name for f in pack.iterdir()])
        mtimes_after = {f.name: f.stat().st_mtime for f in pack.iterdir()}

        self.assertEqual(files_before, files_after)
        self.assertEqual(mtimes_before, mtimes_after)

    # 17. Atomic writes leave no leftover temp folders
    def test_17_atomic_write_leaves_no_temp_folders(self):
        pack = self._create_sample_pack()
        blog_post.make_pack_blog(pack, self.marketing_dir)
        parent = self.marketing_dir
        temp_items = [p.name for p in parent.iterdir() if p.name.startswith(".blog-")]
        self.assertEqual(temp_items, [])

    # 18. Idempotence: running twice produces identical article
    def test_18_idempotence(self):
        pack = self._create_sample_pack()
        out1 = blog_post.make_pack_blog(pack, self.marketing_dir)
        text1 = out1.read_text(encoding="utf-8")

        out2 = blog_post.make_pack_blog(pack, self.marketing_dir)
        text2 = out2.read_text(encoding="utf-8")

        self.assertEqual(text1, text2)

    # 19. Missing listing file falls back to pack folder name
    def test_19_missing_listing_fallback(self):
        pack_dir = self.products_dir / "simple-daily-organizer"
        pack_dir.mkdir(parents=True, exist_ok=True)
        dest = blog_post.make_pack_blog(pack_dir, self.marketing_dir)
        content = dest.read_text(encoding="utf-8")
        self.assertIn("Simple Daily Organizer", content)
        wc = blog_post.count_words(content)
        self.assertGreaterEqual(wc, 700)
        self.assertLessEqual(wc, 1000)

    # 20. CLI: missing pack exits with code 1
    def test_20_cli_missing_pack(self):
        res = blog_post.main(["--pack", "does-not-exist", "--products-dir", str(self.products_dir)])
        self.assertEqual(res, 1)

    # 21. CLI: single pack generates blog post
    def test_21_cli_single_pack(self):
        pack = self._create_sample_pack(pack_name="cli-pack-blog")
        res = blog_post.main([
            "--pack", "cli-pack-blog",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(res, 0)
        self.assertTrue((self.marketing_dir / "cli-pack-blog" / "blog.md").is_file())

    # 22. CLI: --all flag generates blog posts for all packs
    def test_22_cli_all_packs(self):
        self._create_sample_pack(pack_name="blog-pack-1")
        self._create_sample_pack(pack_name="blog-pack-2")

        res = blog_post.main([
            "--all",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(res, 0)
        self.assertTrue((self.marketing_dir / "blog-pack-1" / "blog.md").is_file())
        self.assertTrue((self.marketing_dir / "blog-pack-2" / "blog.md").is_file())

    # 23. CLI: no arguments shows help and exits with 1
    def test_23_cli_no_args(self):
        res = blog_post.main([])
        self.assertEqual(res, 1)

    # 24. Discover packs handles empty directory
    def test_24_discover_packs_empty(self):
        empty_dir = self.root / "empty_dir"
        empty_dir.mkdir()
        self.assertEqual(blog_post.discover_upload_packs(empty_dir), [])

    # 25. Non-existent pack directory raises FileNotFoundError
    def test_25_non_existent_pack_raises(self):
        with self.assertRaises(FileNotFoundError):
            blog_post.make_pack_blog(self.root / "missing_dir", self.marketing_dir)


if __name__ == "__main__":
    unittest.main()
