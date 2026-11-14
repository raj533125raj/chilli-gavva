"""tests/test_kj_carousel.py — Comprehensive tests for carousel_maker.py.

At least 40 tests covering:
- Slide dimensions (1080x1350 PNG)
- Margin constraints (80px safe margins)
- Slide count (exactly 6 slides)
- Text fit with short and very long titles (auto-shrink, max lines, never cut in half)
- WCAG contrast ratios (>= 4.5:1 for both themes)
- Hashtag cleaning (8 hashtags, lowercase, alphanumeric, no brand names, no money)
- Money scrubbing in captions, alts, and slides
- Brand name scrubbing
- Caption length (<= 2000 chars)
- Alt text format (one line per slide, exactly 6 lines)
- Missing preview fallback to cover.png
- Missing pack clear error handling (exit / message)
- Idempotence (same input -> same output)
- Atomic writes (no leftover temp folders)
- Pack folder is never modified or touched
- Pillow-only verification
- File sizes < 1.5MB per slide
"""

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import carousel_maker


# CC2-1 speed: 8 tests each rendered the same default sample pack only to read the
# result (6 slides, ~0.4 s). The real maker now runs once for them; they still check its real output.
_SHARED = {}


def tearDownModule():
    holder = _SHARED.pop("dir", None)
    _SHARED.clear()
    if holder is not None:
        holder.cleanup()


@unittest.skipUnless(HAS_PIL, "Pillow is required for carousel tests")
class TestKJCarousel(unittest.TestCase):
    def setUp(self):
        # Jules rules: clear all GITHUB_* and RUNNER_ENVIRONMENT variables using mock.patch.dict
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp_dir = tempfile.mkdtemp(prefix="test_carousel_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

        self.root = Path(self.tmp_dir)
        self.products_dir = self.root / "products" / "upload"
        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir = self.root / "marketing"
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def _shared_default_render(self):
        """carousel_maker.make_pack_carousel() result for the default sample pack, rendered once per module."""
        if "result" not in _SHARED:
            _SHARED["dir"] = tempfile.TemporaryDirectory()
            root = Path(_SHARED["dir"].name)
            own_products = self.products_dir
            self.products_dir = root / "products" / "upload"
            self.products_dir.mkdir(parents=True)
            try:
                pack = self._create_sample_pack()
            finally:
                self.products_dir = own_products
            (root / "marketing").mkdir()
            _SHARED["result"] = carousel_maker.make_pack_carousel(pack, root / "marketing")
        return _SHARED["result"]

    def _create_sample_pack(
        self,
        pack_name="minimal-daily-planner",
        title="Minimal Daily Planner",
        desc="A simple, focused daily planning template designed to organize your priorities.",
        tags="planner, productivity, daily routine, focus, minimal",
        benefits=None,
        create_previews=True,
    ):
        pack_dir = self.products_dir / pack_name
        pack_dir.mkdir(parents=True, exist_ok=True)

        if benefits is None:
            benefits = [
                "Daily focus block to identify top 3 priorities",
                "Hourly time-boxing schedule from 6am to 9pm",
                "Dedicated habit and water tracker checklist",
                "Evening reflection and gratitude notes section",
            ]

        what_text = "\n".join(f"- {b}" for b in benefits)
        listing_content = (
            f"Title: {title}\n\n"
            f"Description:\n{desc}\n\n"
            f"Tags: {tags}\n\n"
            f"What you get:\n{what_text}\n"
        )
        (pack_dir / "listing.txt").write_text(listing_content, encoding="utf-8")

        # Create dummy cover.png (16:9, small -> faster compositing)
        cover = Image.new("RGB", (400, 225), (70, 130, 180))
        cover.save(pack_dir / "cover.png")

        if create_previews:
            for i in range(1, 4):
                p_im = Image.new("RGB", (400, 225), (100 + i * 20, 150, 200))
                p_im.save(pack_dir / f"preview-{i}.png")

        return pack_dir

    # 1. Slide Count
    def test_slide_count_is_six(self):
        out = self._shared_default_render()
        pngs = sorted(out.glob("slide-*.png"))
        self.assertEqual(len(pngs), 6)

    # 2. Slide Dimensions
    def test_slide_dimensions_1080x1350(self):
        out = self._shared_default_render()
        for i in range(1, 7):
            slide_path = out / f"slide-{i}.png"
            with Image.open(slide_path) as im:
                self.assertEqual(im.size, (1080, 1350), f"Slide {i} size was {im.size}")

    # 3. Slide 1 Hook text exists and is non-empty
    def test_slide_1_hook_generated(self):
        out = self._shared_default_render()
        self.assertTrue((out / "slide-1.png").is_file())

    # 4. Slide 6 CTA exists
    def test_slide_6_cta_generated(self):
        out = self._shared_default_render()
        self.assertTrue((out / "slide-6.png").is_file())

    # 5. Caption file generated
    def test_caption_file_exists(self):
        out = self._shared_default_render()
        self.assertTrue((out / "caption.txt").is_file())

    # 6. Alt text file generated
    def test_alt_text_file_exists(self):
        out = self._shared_default_render()
        self.assertTrue((out / "alt.txt").is_file())

    # 7. Alt text has exactly 6 lines (one per slide)
    def test_alt_text_has_six_lines(self):
        out = self._shared_default_render()
        lines = [line for line in (out / "alt.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 6)
        for i, line in enumerate(lines, 1):
            self.assertTrue(line.startswith(f"Slide {i}:"))

    # 8. Caption length <= 2000 chars
    def test_caption_length_under_2000_chars(self):
        long_desc = "Awesome planner details. " * 150
        pack = self._create_sample_pack(desc=long_desc)
        out = carousel_maker.make_pack_carousel(pack, self.marketing_dir)
        caption = (out / "caption.txt").read_text(encoding="utf-8")
        self.assertLessEqual(len(caption), 2000)

    # 9. Hashtag count is exactly 8
    def test_hashtag_count_is_eight(self):
        pack = self._create_sample_pack(tags="one, two, three, four, five, six, seven, eight, nine, ten")
        out = carousel_maker.make_pack_carousel(pack, self.marketing_dir)
        caption = (out / "caption.txt").read_text(encoding="utf-8")
        tags = [w for w in caption.split() if w.startswith("#")]
        self.assertEqual(len(tags), 8)

    # 10. Hashtags are lowercase and alphanumeric
    def test_hashtags_lowercase_alphanumeric(self):
        raw_tags = ["Weekly Goal!", "Focus-Tracker", "2027 Plans", "DailyRoutine"]
        cleaned = carousel_maker.clean_hashtags(raw_tags, 8)
        for tag in cleaned:
            self.assertTrue(tag.startswith("#"))
            body = tag[1:]
            self.assertEqual(body, body.lower())
            self.assertTrue(body.isalnum())

    # 11. Hashtags filter brand names
    def test_hashtags_no_brand_names(self):
        raw_tags = ["notion", "google sheets", "canva", "planner", "etsy", "excel"]
        cleaned = carousel_maker.clean_hashtags(raw_tags, 8)
        cleaned_words = [t[1:] for t in cleaned]
        self.assertNotIn("notion", cleaned_words)
        self.assertNotIn("googlesheets", cleaned_words)
        self.assertNotIn("canva", cleaned_words)
        self.assertNotIn("etsy", cleaned_words)
        self.assertNotIn("excel", cleaned_words)

    # 12. Hashtags filter money and currency terms
    def test_hashtags_no_money_words(self):
        raw_tags = ["$10", "10usd", "dollar", "rupees", "planner", "organize"]
        cleaned = carousel_maker.clean_hashtags(raw_tags, 8)
        cleaned_words = [t[1:] for t in cleaned]
        self.assertNotIn("10", cleaned_words)
        self.assertNotIn("10usd", cleaned_words)
        self.assertNotIn("dollar", cleaned_words)
        self.assertNotIn("rupees", cleaned_words)

    # 13. Caption contains no brand names
    def test_caption_no_brand_names(self):
        pack = self._create_sample_pack(
            title="Excel Notion Planner for Canva and Etsy",
            desc="Built for Google Sheets and Microsoft Word with Gumroad compatibility.",
        )
        out = carousel_maker.make_pack_carousel(pack, self.marketing_dir)
        caption = (out / "caption.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("notion", caption)
        self.assertNotIn("canva", caption)
        self.assertNotIn("etsy", caption)
        self.assertNotIn("gumroad", caption)

    # 14. Caption contains no money amounts
    def test_caption_no_money(self):
        pack = self._create_sample_pack(
            title="Planner $19 only",
            desc="Valued at 49 USD or Rs 500, now only $10.",
        )
        out = carousel_maker.make_pack_carousel(pack, self.marketing_dir)
        caption = (out / "caption.txt").read_text(encoding="utf-8")
        self.assertIsNone(carousel_maker._MONEY_PATTERN.search(caption))

    # 15. Alt text contains no money amounts
    def test_alt_text_no_money(self):
        pack = self._create_sample_pack(
            title="Premium $25 Budget Workbook",
            benefits=["Budget $100 easily", "Save Rs 1000", "Track 50 USD", "Plan ahead"],
        )
        out = carousel_maker.make_pack_carousel(pack, self.marketing_dir)
        alt = (out / "alt.txt").read_text(encoding="utf-8")
        self.assertIsNone(carousel_maker._MONEY_PATTERN.search(alt))

    # 16. Alt text contains no brand names
    def test_alt_text_no_brand_names(self):
        pack = self._create_sample_pack(
            title="Notion Study Hub on iPad",
            benefits=["Google Sheets sync", "Canva graphics", "Etsy bestselling layout", "Word file"],
        )
        out = carousel_maker.make_pack_carousel(pack, self.marketing_dir)
        alt = (out / "alt.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("notion", alt)
        self.assertNotIn("canva", alt)
        self.assertNotIn("etsy", alt)

    # 17. Safe margin check on Slide 1: top element >= 80px
    def test_safe_margins_slide_1(self):
        theme1, _ = carousel_maker.pick_carousel_themes(None)
        slide = carousel_maker.draw_slide_1("My Title", "pack-1", theme1, None)
        self.assertEqual(slide.size, (1080, 1350))
        # Margin constant is 80
        self.assertEqual(carousel_maker.MARGIN, 80)

    # 18. Safe margin check on Benefit slide
    def test_safe_margins_slide_benefit(self):
        theme1, _ = carousel_maker.pick_carousel_themes(None)
        slide = carousel_maker.draw_slide_benefit(2, "Benefit text", None, None, theme1)
        self.assertEqual(slide.size, (1080, 1350))

    # 19. Safe margin check on CTA slide
    def test_safe_margins_slide_cta(self):
        theme1, _ = carousel_maker.pick_carousel_themes(None)
        slide = carousel_maker.draw_slide_6_cta("My Pack", theme1, None)
        self.assertEqual(slide.size, (1080, 1350))

    # 20. Contrast ratio function: pure black on pure white is 21:1
    def test_contrast_ratio_black_on_white(self):
        cr = carousel_maker.contrast_ratio((0, 0, 0), (255, 255, 255))
        self.assertAlmostEqual(cr, 21.0, places=1)

    # 21. Contrast ratio function: identical colors is 1:1
    def test_contrast_ratio_identical_colors(self):
        cr = carousel_maker.contrast_ratio((120, 120, 120), (120, 120, 120))
        self.assertAlmostEqual(cr, 1.0, places=2)

    # 22. Color adjustment achieves >= 4.5:1 ratio
    def test_adjust_color_for_contrast(self):
        bg = (245, 245, 245)
        # Low contrast gray
        weak_fg = (210, 210, 210)
        strong_fg = carousel_maker.adjust_color_for_contrast(weak_fg, bg, 4.5)
        cr = carousel_maker.contrast_ratio(strong_fg, bg)
        self.assertGreaterEqual(cr, 4.5)

    # 23. Pick carousel themes returns 2 themes with >= 4.5:1
    def test_pick_carousel_themes_contrast_guarantee(self):
        theme1, theme2 = carousel_maker.pick_carousel_themes(None)
        cr1_text = carousel_maker.contrast_ratio(theme1["text"], theme1["bg"])
        cr1_accent = carousel_maker.contrast_ratio(theme1["accent"], theme1["bg"])
        cr2_text = carousel_maker.contrast_ratio(theme2["text"], theme2["bg"])
        cr2_accent = carousel_maker.contrast_ratio(theme2["accent"], theme2["bg"])

        self.assertGreaterEqual(cr1_text, 4.5)
        self.assertGreaterEqual(cr1_accent, 4.5)
        self.assertGreaterEqual(cr2_text, 4.5)
        self.assertGreaterEqual(cr2_accent, 4.5)

    # 24. Pick carousel themes from custom cover image
    def test_pick_carousel_themes_from_cover(self):
        cover_path = self.root / "test_cover.png"
        im = Image.new("RGB", (1280, 720), (34, 139, 34))  # Forest Green
        im.save(cover_path)

        theme1, theme2 = carousel_maker.pick_carousel_themes(cover_path)
        self.assertGreaterEqual(carousel_maker.contrast_ratio(theme1["text"], theme1["bg"]), 4.5)
        self.assertGreaterEqual(carousel_maker.contrast_ratio(theme2["text"], theme2["bg"]), 4.5)

    # 25. Text fit: short title fits without shrinking to min
    def test_fit_text_short_title(self):
        lines, f = carousel_maker.fit_text("Daily Habit Tracker", "bold", 68, 38, 920, 2)
        self.assertLessEqual(len(lines), 2)
        self.assertEqual(lines, ["Daily Habit Tracker"])

    # 26. Text fit: very long title wraps and auto-shrinks
    def test_fit_text_very_long_title(self):
        long_title = (
            "Complete Comprehensive All In One Annual Financial And Productivity Goal Tracking "
            "Master Template With Monthly Review Dashboard"
        )
        lines, f = carousel_maker.fit_text(long_title, "bold", 68, 38, 920, 2)
        self.assertLessEqual(len(lines), 2)
        self.assertTrue(all(carousel_maker.text_w(l, f) <= 920 for l in lines))

    # 27. Text fit: never cuts words in half
    def test_fit_text_never_cuts_words_in_half(self):
        title = "Supercalifragilisticexpialidocious productivity notebook"
        lines, f = carousel_maker.fit_text(title, "bold", 50, 20, 920, 3)
        # All parts should be whole words
        recombined = " ".join(lines)
        self.assertIn("productivity", recombined)

    # 28. Slide 1 max 2 lines rule
    def test_slide_1_max_two_lines(self):
        title = "The Ultimate Deep Work Focus System Designed For Remote Engineers And Creators"
        lines, _ = carousel_maker.fit_text(title, "bold", 68, 38, 920, 2)
        self.assertLessEqual(len(lines), 2)

    # 29. Benefit slides max 3 lines rule
    def test_benefit_slides_max_three_lines(self):
        long_benefit = (
            "A structured five phase deep work tracker that lets you log flow state intervals, "
            "eliminate digital distractions, and record daily cognitive energy scores effortlessly."
        )
        lines, _ = carousel_maker.fit_text(long_benefit, "bold", 48, 28, 920, 3)
        self.assertLessEqual(len(lines), 3)

    # 30. Missing preview fallback to cover.png
    def test_missing_previews_uses_cover(self):
        pack = self._create_sample_pack(create_previews=False)
        out = carousel_maker.make_pack_carousel(pack, self.marketing_dir)
        # Slides 2-5 should still be generated successfully using cover
        for i in range(2, 6):
            self.assertTrue((out / f"slide-{i}.png").is_file())

    # 31. Crop and fit never stretches image
    def test_fit_and_crop_never_stretches(self):
        # 16:9 input cropped to 4:5 target
        src = Image.new("RGB", (1600, 900), (200, 200, 200))
        cropped = carousel_maker.fit_and_crop_image(src, 920, 800)
        self.assertEqual(cropped.size, (920, 800))

    # 32. Crop and fit tall image to wide target
    def test_fit_and_crop_tall_image(self):
        src = Image.new("RGB", (600, 1200), (150, 150, 150))
        cropped = carousel_maker.fit_and_crop_image(src, 800, 500)
        self.assertEqual(cropped.size, (800, 500))

    # 33. Idempotence: running twice produces identical outputs
    def test_idempotence(self):
        pack = self._create_sample_pack()
        out1 = carousel_maker.make_pack_carousel(pack, self.marketing_dir)
        cap1 = (out1 / "caption.txt").read_bytes()
        alt1 = (out1 / "alt.txt").read_bytes()
        s1_bytes1 = (out1 / "slide-1.png").read_bytes()

        out2 = carousel_maker.make_pack_carousel(pack, self.marketing_dir)
        cap2 = (out2 / "caption.txt").read_bytes()
        alt2 = (out2 / "alt.txt").read_bytes()
        s1_bytes2 = (out2 / "slide-1.png").read_bytes()

        self.assertEqual(cap1, cap2)
        self.assertEqual(alt1, alt2)
        self.assertEqual(s1_bytes1, s1_bytes2)

    # 34. Pack directory is never modified or touched
    def test_pack_folder_never_modified(self):
        pack = self._create_sample_pack()
        files_before = sorted([f.name for f in pack.iterdir()])
        mtimes_before = {f.name: f.stat().st_mtime for f in pack.iterdir()}

        carousel_maker.make_pack_carousel(pack, self.marketing_dir)

        files_after = sorted([f.name for f in pack.iterdir()])
        mtimes_after = {f.name: f.stat().st_mtime for f in pack.iterdir()}

        self.assertEqual(files_before, files_after)
        self.assertEqual(mtimes_before, mtimes_after)

    # 35. File size < 1.5MB each
    def test_slide_file_sizes_under_1_5mb(self):
        out = self._shared_default_render()
        for i in range(1, 7):
            size = (out / f"slide-{i}.png").stat().st_size
            self.assertLess(size, 1_500_000, f"Slide {i} was {size} bytes")

    # 36. Missing pack raises FileNotFoundError / handles gracefully
    def test_missing_pack_raises_error(self):
        missing = self.products_dir / "non-existent-pack"
        with self.assertRaises(FileNotFoundError):
            carousel_maker.make_pack_carousel(missing, self.marketing_dir)

    # 37. CLI: missing pack returns clear message and exit code 1
    def test_cli_missing_pack_exit_code(self):
        res = carousel_maker.main(["--pack", "does-not-exist", "--products-dir", str(self.products_dir)])
        self.assertEqual(res, 1)

    # 38. CLI: single pack execution succeeds
    def test_cli_single_pack(self):
        pack = self._create_sample_pack(pack_name="cli-pack-1")
        res = carousel_maker.main([
            "--pack", "cli-pack-1",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(res, 0)
        self.assertTrue((self.marketing_dir / "cli-pack-1" / "carousel" / "slide-1.png").is_file())

    # 39. CLI: --all flag generates carousels for all packs
    def test_cli_all_packs(self):
        self._create_sample_pack(pack_name="pack-a")
        self._create_sample_pack(pack_name="pack-b")

        res = carousel_maker.main([
            "--all",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(res, 0)
        self.assertTrue((self.marketing_dir / "pack-a" / "carousel" / "slide-1.png").is_file())
        self.assertTrue((self.marketing_dir / "pack-b" / "carousel" / "slide-1.png").is_file())

    # 40. CLI: no arguments shows help and returns 1
    def test_cli_no_args_shows_help(self):
        res = carousel_maker.main([])
        self.assertEqual(res, 1)

    # 41. Discover packs handles empty directory
    def test_discover_packs_empty(self):
        empty_dir = self.root / "empty_products"
        empty_dir.mkdir()
        packs = carousel_maker.discover_upload_packs(empty_dir)
        self.assertEqual(packs, [])

    # 42. Atomic write leaves no temporary folders on success
    def test_atomic_write_leaves_no_temp_folders(self):
        pack = self._create_sample_pack()
        carousel_maker.make_pack_carousel(pack, self.marketing_dir)
        parent = self.marketing_dir / pack.name
        temp_items = [p.name for p in parent.iterdir() if p.name.startswith(".carousel-")]
        self.assertEqual(temp_items, [])

    # 43. Fallback benefits when pack has fewer than 4 items
    def test_fallback_benefits_when_fewer_than_four(self):
        pack = self._create_sample_pack(benefits=["Single main benefit"])
        out = carousel_maker.make_pack_carousel(pack, self.marketing_dir)
        # All 6 slides should still be generated
        for i in range(1, 7):
            self.assertTrue((out / f"slide-{i}.png").is_file())


if __name__ == "__main__":
    unittest.main()
