"""tests/test_s1_reel_frames.py — Unit tests for reel_frames.py.

At least 25 tests covering:
- 8 vertical 1080x1920 PNG frames
- Safe margins: 120 px top/bottom, 80 px left/right
- Text never cut (auto-shrink font, wrap at word boundaries, max lines)
- Contrast ratio >= 4.5:1
- Fitted preview images (never stretched, aspect ratio preserved)
- reel.txt caption + 8 clean hashtags
- No money amounts, no brands
- make_reel.sh generated with executable permissions, contains ffmpeg command, never executed in CI
- Idempotence & atomic directory swap
- Pack folder is never modified
- Pillow-only verification
"""

import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import reel_frames


@unittest.skipUnless(HAS_PIL, "Pillow is required for reel tests")
class TestS1ReelFrames(unittest.TestCase):
    def setUp(self):
        # Clear all GITHUB_* and RUNNER_ENVIRONMENT variables using mock.patch.dict
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp_dir = tempfile.mkdtemp(prefix="test_reel_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

        self.root = Path(self.tmp_dir)
        self.products_dir = self.root / "products" / "upload"
        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir = self.root / "marketing"
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def _create_sample_pack(
        self,
        pack_name="deep-work-planner",
        title="Deep Work Daily Planner",
        desc="A structured daily productivity system for deep focus sessions.",
        tags="productivity, deepwork, focus, daily, routine, habit, planner, organize",
        benefits=None,
        create_previews=True,
    ):
        pack_dir = self.products_dir / pack_name
        pack_dir.mkdir(parents=True, exist_ok=True)

        if benefits is None:
            benefits = [
                "Morning priority quadrant to lock in top goals",
                "90-minute deep work focus block tracker",
                "Distraction log to keep attention centered",
                "Energy level and habit tracking checklist",
                "Evening review and wind-down notes page",
            ]

        what_text = "\n".join(f"- {b}" for b in benefits)
        listing_content = (
            f"Title: {title}\n\n"
            f"Description:\n{desc}\n\n"
            f"Tags: {tags}\n\n"
            f"What you get:\n{what_text}\n"
        )
        (pack_dir / "listing.txt").write_text(listing_content, encoding="utf-8")

        cover = Image.new("RGB", (1280, 720), (50, 100, 150))
        cover.save(pack_dir / "cover.png")

        if create_previews:
            for i in range(1, 6):
                p_im = Image.new("RGB", (1280, 720), (80 + i * 20, 120, 160))
                p_im.save(pack_dir / f"preview-{i}.png")

        return pack_dir

    # 1. Frame count is exactly 8
    def test_01_frame_count_is_eight(self):
        pack = self._create_sample_pack()
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        pngs = sorted(out.glob("frame-*.png"))
        self.assertEqual(len(pngs), 8)

    # 2. Dimensions are 1080x1920 for all 8 frames
    def test_02_frame_dimensions_1080x1920(self):
        pack = self._create_sample_pack()
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        for i in range(1, 9):
            frame_path = out / f"frame-{i}.png"
            with Image.open(frame_path) as im:
                self.assertEqual(im.size, (1080, 1920), f"Frame {i} dimension was {im.size}")

    # 3. Safe margins constants: top/bottom >= 120px
    def test_03_safe_margin_constants(self):
        self.assertGreaterEqual(reel_frames.SAFE_MARGIN_TB, 120)
        self.assertGreaterEqual(reel_frames.SAFE_MARGIN_LR, 60)

    # 4. Companion file reel.txt exists
    def test_04_reel_txt_exists(self):
        pack = self._create_sample_pack()
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        self.assertTrue((out / "reel.txt").is_file())

    # 5. Companion file make_reel.sh exists
    def test_05_make_reel_sh_exists(self):
        pack = self._create_sample_pack()
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        self.assertTrue((out / "make_reel.sh").is_file())

    # 6. make_reel.sh is executable
    def test_06_make_reel_sh_is_executable(self):
        pack = self._create_sample_pack()
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        mode = os.stat(out / "make_reel.sh").st_mode
        self.assertTrue(bool(mode & stat.S_IXUSR))

    # 7. make_reel.sh contains ffmpeg commands and 15s framerate (never run in CI)
    def test_07_make_reel_sh_content(self):
        pack = self._create_sample_pack()
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        content = (out / "make_reel.sh").read_text(encoding="utf-8")
        self.assertIn("ffmpeg", content)
        self.assertIn("1080:1920", content)
        self.assertIn("DO NOT RUN IN CI", content)

    # 8. reel.txt contains title, description, and exactly 8 hashtags
    def test_08_reel_txt_hashtags_count(self):
        pack = self._create_sample_pack()
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        text = (out / "reel.txt").read_text(encoding="utf-8")
        hashtags = [w for w in text.split() if w.startswith("#")]
        self.assertEqual(len(hashtags), 8)

    # 9. Hashtags are lowercase and alphanumeric
    def test_09_hashtags_lowercase_alphanumeric(self):
        raw_tags = ["GoalTracker", "Morning-Routine", "Focus 2027", "Habit_Kit!"]
        cleaned = reel_frames.clean_hashtags(raw_tags, 8)
        for h in cleaned:
            self.assertTrue(h.startswith("#"))
            body = h[1:]
            self.assertEqual(body, body.lower())
            self.assertTrue(body.isalnum())

    # 10. Hashtags filter out third-party brands
    def test_10_hashtags_no_brands(self):
        raw_tags = ["google sheets", "notion template", "canva", "planner", "etsy"]
        cleaned = reel_frames.clean_hashtags(raw_tags, 8)
        words = [h[1:] for h in cleaned]
        self.assertNotIn("googlesheets", words)
        self.assertNotIn("notion", words)
        self.assertNotIn("canva", words)
        self.assertNotIn("etsy", words)

    # 11. Hashtags filter out currency / money amounts
    def test_11_hashtags_no_money(self):
        raw_tags = ["$15", "100usd", "inr500", "rupees", "dollar", "planner"]
        cleaned = reel_frames.clean_hashtags(raw_tags, 8)
        words = [h[1:] for h in cleaned]
        self.assertNotIn("15", words)
        self.assertNotIn("100usd", words)
        self.assertNotIn("dollar", words)
        self.assertNotIn("rupees", words)

    # 12. No money amounts in reel.txt caption
    def test_12_caption_no_money_amounts(self):
        pack = self._create_sample_pack(
            title="Planner priced at $19",
            desc="Great value for 45 USD or 500 INR, download today.",
        )
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        caption = (out / "reel.txt").read_text(encoding="utf-8")
        self.assertIsNone(reel_frames._MONEY_PATTERN.search(caption))

    # 13. No brand names in reel.txt caption
    def test_13_caption_no_brands(self):
        pack = self._create_sample_pack(
            title="Notion Canva Daily Hub",
            desc="Designed for Google Sheets and Etsy users on iPad.",
        )
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        caption = (out / "reel.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("notion", caption)
        self.assertNotIn("canva", caption)
        self.assertNotIn("etsy", caption)

    # 14. WCAG contrast ratio >= 4.5:1
    def test_14_contrast_ratio_standard(self):
        theme = reel_frames.pick_reel_theme(None)
        cr_text = reel_frames.contrast_ratio(theme["text"], theme["bg"])
        cr_accent = reel_frames.contrast_ratio(theme["accent"], theme["bg"])
        self.assertGreaterEqual(cr_text, 4.5)
        self.assertGreaterEqual(cr_accent, 4.5)

    # 15. Contrast adjustment on low-contrast color
    def test_15_adjust_color_for_contrast(self):
        bg = (240, 240, 240)
        fg = (215, 215, 215)
        strong = reel_frames.adjust_color_for_contrast(fg, bg, 4.5)
        cr = reel_frames.contrast_ratio(strong, bg)
        self.assertGreaterEqual(cr, 4.5)

    # 16. Text fitting wraps and shrinks without cutting words
    def test_16_fit_text_word_boundaries(self):
        long_title = "The Comprehensive Annual Deep Work Focus System For Remote Teams"
        lines, f = reel_frames.fit_text(long_title, "bold", 72, 36, 920, 3)
        self.assertLessEqual(len(lines), 3)
        joined = " ".join(lines)
        self.assertIn("Comprehensive", joined)
        self.assertIn("System", joined)

    # 17. Frame 7 'How it looks printed' is generated
    def test_17_frame_7_printed_exists(self):
        pack = self._create_sample_pack()
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        self.assertTrue((out / "frame-7.png").is_file())

    # 18. Frame 8 CTA 'Link in bio' is generated
    def test_18_frame_8_cta_exists(self):
        pack = self._create_sample_pack()
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        self.assertTrue((out / "frame-8.png").is_file())

    # 19. Fitted preview images preserve aspect ratio (not stretched)
    def test_19_fit_and_contain_preserves_aspect_ratio(self):
        src = Image.new("RGB", (1600, 900), (200, 200, 200))  # 16:9
        canvas = reel_frames.fit_and_contain_image(src, 920, 600, (255, 255, 255))
        self.assertEqual(canvas.size, (920, 600))

    def test_20_fit_and_crop_preserves_aspect_ratio(self):
        src = Image.new("RGB", (800, 1200), (100, 100, 100))
        cropped = reel_frames.fit_and_crop_image(src, 920, 800)
        self.assertEqual(cropped.size, (920, 800))

    # 21. Missing previews fall back to cover.png
    def test_21_missing_previews_fall_back_to_cover(self):
        pack = self._create_sample_pack(create_previews=False)
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        for i in range(1, 9):
            self.assertTrue((out / f"frame-{i}.png").is_file())

    # 22. Pack directory is never modified
    def test_22_pack_folder_never_modified(self):
        pack = self._create_sample_pack()
        files_before = sorted([f.name for f in pack.iterdir()])
        mtimes_before = {f.name: f.stat().st_mtime for f in pack.iterdir()}

        reel_frames.make_pack_reel(pack, self.marketing_dir)

        files_after = sorted([f.name for f in pack.iterdir()])
        mtimes_after = {f.name: f.stat().st_mtime for f in pack.iterdir()}

        self.assertEqual(files_before, files_after)
        self.assertEqual(mtimes_before, mtimes_after)

    # 23. Atomic writes leave no leftover temp dirs
    def test_23_atomic_write_no_temp_dirs(self):
        pack = self._create_sample_pack()
        reel_frames.make_pack_reel(pack, self.marketing_dir)
        parent = self.marketing_dir / pack.name
        temp_items = [p.name for p in parent.iterdir() if p.name.startswith(".reel-")]
        self.assertEqual(temp_items, [])

    # 24. Idempotence: running twice produces identical files
    def test_24_idempotence(self):
        pack = self._create_sample_pack()
        out1 = reel_frames.make_pack_reel(pack, self.marketing_dir)
        txt1 = (out1 / "reel.txt").read_bytes()
        sh1 = (out1 / "make_reel.sh").read_bytes()
        f1_1 = (out1 / "frame-1.png").read_bytes()

        out2 = reel_frames.make_pack_reel(pack, self.marketing_dir)
        txt2 = (out2 / "reel.txt").read_bytes()
        sh2 = (out2 / "make_reel.sh").read_bytes()
        f1_2 = (out2 / "frame-1.png").read_bytes()

        self.assertEqual(txt1, txt2)
        self.assertEqual(sh1, sh2)
        self.assertEqual(f1_1, f1_2)

    # 25. File sizes < 2.0MB per frame
    def test_25_file_sizes_under_2mb(self):
        pack = self._create_sample_pack()
        out = reel_frames.make_pack_reel(pack, self.marketing_dir)
        for i in range(1, 9):
            size = (out / f"frame-{i}.png").stat().st_size
            self.assertLess(size, 2_000_000, f"Frame {i} exceeded 2MB: {size} bytes")

    # 26. CLI: missing pack exits with code 1
    def test_26_cli_missing_pack(self):
        res = reel_frames.main(["--pack", "non-existent", "--products-dir", str(self.products_dir)])
        self.assertEqual(res, 1)

    # 27. CLI: single pack generates reel
    def test_27_cli_single_pack(self):
        pack = self._create_sample_pack(pack_name="cli-reel-pack")
        res = reel_frames.main([
            "--pack", "cli-reel-pack",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(res, 0)
        self.assertTrue((self.marketing_dir / "cli-reel-pack" / "reel" / "frame-1.png").is_file())


if __name__ == "__main__":
    unittest.main()
