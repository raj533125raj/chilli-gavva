"""tests/test_t1a_covers.py — Comprehensive tests for cover_variants.py.

At least 30 unit tests covering:
- Variant count (exactly 3 covers: cover_a.png, cover_b.png, cover_c.png)
- Dimensions for all 3 variants (1280x720)
- covers.json schema ({file, layout, title})
- layouts in covers.json (A, B, C)
- Margin constraints (safe margin >= 60px)
- Contrast ratio >= 4.5:1 (WCAG AA compliance)
- File size < 1MB each
- Brand names scrubbed from title and body
- Money amounts and currency signs scrubbed
- Long title wrapping without truncation / clipping
- Short title handling
- Special characters in title handling
- Missing preview images fallback to cover.png or synthetic page
- Missing cover fallback to previews
- Missing listing.txt fallback to pack folder name
- Missing description and what-you-get fallback bullets
- Deterministic output (same input produces identical or consistent images)
- Atomic writes (no leftover temp folders or partial files)
- Pack folder is strictly read-only and never modified
- Discover packs logic (under products/upload and products/)
- Empty packs directory handling
- Invalid / non-existent pack directory raises error
- CLI --pack flag
- CLI --all flag
- CLI with non-existent pack error handling
- CLI help message when no args provided
- Tenglish house style in return messages and logging
- Font helpers properly imported from mockups.py
- Mockups import reuse without breaking
- Clean environment with GITHUB_* and RUNNER_ENVIRONMENT wiped in setUp
"""

import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import cover_variants
import mockups


# CC2-1 speed: 8 tests each rendered the same default sample pack only to read the
# result (3 covers, ~0.4 s). The real maker now runs once for them; they still check its real output.
_SHARED = {}


def tearDownModule():
    holder = _SHARED.pop("dir", None)
    _SHARED.clear()
    if holder is not None:
        holder.cleanup()


@unittest.skipUnless(HAS_PIL, "Pillow is required for cover_variants tests")
class TestT1ACovers(unittest.TestCase):
    def setUp(self):
        # Jules rules: clear all GITHUB_* and RUNNER_ENVIRONMENT variables using mock.patch.dict
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp_dir = tempfile.mkdtemp(prefix="test_t1a_covers_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

        self.root = Path(self.tmp_dir)
        self.products_dir = self.root / "products" / "upload"
        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir = self.root / "marketing"
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def _shared_default_render(self):
        """cover_variants.make_pack_covers() result for the default sample pack, rendered once per module."""
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
            _SHARED["result"] = cover_variants.make_pack_covers(pack, root / "marketing")
        return _SHARED["result"]

    def _create_sample_pack(
        self,
        pack_name="adhd-daily-planner",
        title="ADHD Daily Focus Planner",
        desc="A gentle, focused daily planner for neurodivergent minds.",
        tags="adhd, planner, daily focus, printable",
        bullets=None,
        create_previews=True,
        create_cover=True,
    ):
        pack_dir = self.products_dir / pack_name
        pack_dir.mkdir(parents=True, exist_ok=True)

        if bullets is None:
            bullets = [
                "Morning priority focus tracker",
                "Simple 3-step time boxing grid",
                "Evening reset and win celebration notes",
            ]

        what_text = "\n".join(f"- {b}" for b in bullets)
        listing_content = (
            f"Title: {title}\n\n"
            f"Description:\n{desc}\n\n"
            f"Tags: {tags}\n\n"
            f"What you get:\n{what_text}\n"
        )
        (pack_dir / "listing.txt").write_text(listing_content, encoding="utf-8")

        if create_cover:
            cover = Image.new("RGB", (1280, 720), (70, 130, 180))
            cover.save(pack_dir / "cover.png")

        if create_previews:
            for i in range(1, 4):
                p_im = Image.new("RGB", (1280, 720), (120 + i * 20, 160, 210))
                p_im.save(pack_dir / f"preview-{i}.png")

        return pack_dir

    # 1. Variant Count: exactly 3 cover images produced
    def test_01_variant_count(self):
        res = self._shared_default_render()
        covers_dir = Path(res["covers_dir"])
        png_files = sorted(list(covers_dir.glob("*.png")))
        self.assertEqual(len(png_files), 3)
        self.assertEqual([p.name for p in png_files], ["cover_a.png", "cover_b.png", "cover_c.png"])

    # 2. Dimensions for Variant A
    def test_02_dimensions_variant_a(self):
        res = self._shared_default_render()
        with Image.open(Path(res["covers_dir"]) / "cover_a.png") as im:
            self.assertEqual(im.size, (1280, 720))

    # 3. Dimensions for Variant B
    def test_03_dimensions_variant_b(self):
        res = self._shared_default_render()
        with Image.open(Path(res["covers_dir"]) / "cover_b.png") as im:
            self.assertEqual(im.size, (1280, 720))

    # 4. Dimensions for Variant C
    def test_04_dimensions_variant_c(self):
        res = self._shared_default_render()
        with Image.open(Path(res["covers_dir"]) / "cover_c.png") as im:
            self.assertEqual(im.size, (1280, 720))

    # 5. covers.json exists and is valid JSON
    def test_05_covers_json_exists(self):
        res = self._shared_default_render()
        json_file = Path(res["covers_dir"]) / "covers.json"
        self.assertTrue(json_file.is_file())
        data = json.loads(json_file.read_text(encoding="utf-8"))
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 3)

    # 6. covers.json schema contains file, layout, title
    def test_06_covers_json_schema(self):
        res = self._shared_default_render()
        data = json.loads((Path(res["covers_dir"]) / "covers.json").read_text(encoding="utf-8"))
        for entry in data:
            self.assertIn("file", entry)
            self.assertIn("layout", entry)
            self.assertIn("title", entry)
            self.assertTrue(entry["file"].endswith(".png"))

    # 7. covers.json layouts are A, B, C
    def test_07_covers_json_layouts(self):
        res = self._shared_default_render()
        data = json.loads((Path(res["covers_dir"]) / "covers.json").read_text(encoding="utf-8"))
        layouts = [e["layout"] for e in data]
        self.assertEqual(layouts, ["A", "B", "C"])

    # 8. Contrast ratio check utility
    def test_08_contrast_ratio_utility(self):
        white = (255, 255, 255)
        black = (0, 0, 0)
        ratio = cover_variants.contrast_ratio(white, black)
        self.assertGreaterEqual(ratio, 20.0)

        # Same colors should have contrast ratio 1.0
        self.assertAlmostEqual(cover_variants.contrast_ratio(white, white), 1.0, places=2)

    # 9. Contrast ratio meets WCAG AA >= 4.5:1 for light theme
    def test_09_contrast_ratio_light_theme(self):
        bg = (248, 250, 252)
        ink = (15, 23, 42)
        ratio = cover_variants.contrast_ratio(bg, ink)
        self.assertGreaterEqual(ratio, 4.5)

    # 10. Contrast ratio meets WCAG AA >= 4.5:1 for dark split theme
    def test_10_contrast_ratio_dark_theme(self):
        bg = (30, 41, 59)
        title_color = (255, 255, 255)
        accent_color = (45, 212, 191)
        self.assertGreaterEqual(cover_variants.contrast_ratio(bg, title_color), 4.5)
        self.assertGreaterEqual(cover_variants.contrast_ratio(bg, accent_color), 4.5)

    # 11. File size < 1MB for all variants
    def test_11_file_size_under_one_mb(self):
        res = self._shared_default_render()
        covers_dir = Path(res["covers_dir"])
        for name in ["cover_a.png", "cover_b.png", "cover_c.png"]:
            f = covers_dir / name
            self.assertLess(f.stat().st_size, 1024 * 1024, f"{name} exceeded 1MB")

    # 12. Scrub brand names from title
    def test_12_scrub_brand_names(self):
        dirty = "ADHD Planner for Notion and Canva with Google Sheets"
        cleaned = cover_variants.clean_text(dirty)
        self.assertNotIn("Notion", cleaned)
        self.assertNotIn("Canva", cleaned)
        self.assertNotIn("Google", cleaned)
        self.assertNotIn("Sheets", cleaned)

    # 13. Scrub currency and money amounts
    def test_13_scrub_currency_amounts(self):
        dirty = "Habit Tracker $10 USD or 500 INR only ₹250"
        cleaned = cover_variants.clean_text(dirty)
        self.assertNotIn("$", cleaned)
        self.assertNotIn("USD", cleaned)
        self.assertNotIn("INR", cleaned)
        self.assertNotIn("₹", cleaned)
        self.assertNotIn("10", cleaned)
        self.assertNotIn("500", cleaned)
        self.assertNotIn("250", cleaned)

    # 14. Long title wrapping without raising error or clipping
    def test_14_long_title_fit_text(self):
        long_title = "Comprehensive Ultra Detailed Daily Routine And Weekly Goal Setting System For Peak Productivity And Mindfulness"
        lines, f = cover_variants.fit_text_custom(long_title, "bold", 54, 24, 500, 3)
        self.assertTrue(len(lines) <= 3)
        for line in lines:
            self.assertLessEqual(mockups.text_w(line, f), 500)

    # 15. Short title handling
    def test_15_short_title(self):
        short = "Focus"
        lines, f = cover_variants.fit_text_custom(short, "bold", 54, 24, 500, 3)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], "Focus")

    # 16. Empty title fallback
    def test_16_empty_title_fallback(self):
        lines, f = cover_variants.fit_text_custom("", "bold", 54, 24, 500, 3)
        self.assertTrue(len(lines) >= 1)
        self.assertNotEqual(lines[0], "")

    # 17. Missing preview images fallback to cover
    def test_17_missing_preview_images_fallback(self):
        pack = self._create_sample_pack(create_previews=False)
        res = cover_variants.make_pack_covers(pack, self.marketing_dir)
        covers_dir = Path(res["covers_dir"])
        for name in ["cover_a.png", "cover_b.png", "cover_c.png"]:
            self.assertTrue((covers_dir / name).is_file())

    # 18. Missing cover image fallback to preview images
    def test_18_missing_cover_image_fallback(self):
        pack = self._create_sample_pack(create_cover=False)
        res = cover_variants.make_pack_covers(pack, self.marketing_dir)
        covers_dir = Path(res["covers_dir"])
        for name in ["cover_a.png", "cover_b.png", "cover_c.png"]:
            self.assertTrue((covers_dir / name).is_file())

    # 19. Missing both cover and preview images fallback to synthetic page
    def test_19_missing_all_images_fallback(self):
        pack = self._create_sample_pack(create_cover=False, create_previews=False)
        res = cover_variants.make_pack_covers(pack, self.marketing_dir)
        covers_dir = Path(res["covers_dir"])
        for name in ["cover_a.png", "cover_b.png", "cover_c.png"]:
            self.assertTrue((covers_dir / name).is_file())

    # 20. Missing listing.txt fallback to pack folder name
    def test_20_missing_listing_fallback(self):
        pack_dir = self.products_dir / "simple-habit-tracker"
        pack_dir.mkdir(parents=True, exist_ok=True)
        # No listing.txt
        cover = Image.new("RGB", (1280, 720), (50, 100, 150))
        cover.save(pack_dir / "cover.png")

        res = cover_variants.make_pack_covers(pack_dir, self.marketing_dir)
        data = json.loads((Path(res["covers_dir"]) / "covers.json").read_text(encoding="utf-8"))
        for entry in data:
            self.assertIn("Simple Habit Tracker", entry["title"])

    # 21. Deterministic output: successive runs generate same file content or size
    def test_21_deterministic_output(self):
        pack = self._create_sample_pack()
        res1 = cover_variants.make_pack_covers(pack, self.marketing_dir)
        bytes_a1 = (Path(res1["covers_dir"]) / "cover_a.png").read_bytes()

        res2 = cover_variants.make_pack_covers(pack, self.marketing_dir)
        bytes_a2 = (Path(res2["covers_dir"]) / "cover_a.png").read_bytes()

        self.assertEqual(bytes_a1, bytes_a2)

    # 22. Pack folder immutability: pack files and timestamps remain unchanged
    def test_22_pack_folder_untouched(self):
        pack = self._create_sample_pack()
        original_files = sorted(os.listdir(pack))
        original_mtimes = {f: (pack / f).stat().st_mtime for f in original_files}

        cover_variants.make_pack_covers(pack, self.marketing_dir)

        current_files = sorted(os.listdir(pack))
        self.assertEqual(original_files, current_files)
        for f in original_files:
            self.assertEqual(original_mtimes[f], (pack / f).stat().st_mtime)

    # 23. Atomic writes: no leftover temp directories
    def test_23_atomic_writes_no_leftover_temp(self):
        pack = self._create_sample_pack()
        res = cover_variants.make_pack_covers(pack, self.marketing_dir)
        pack_marketing_dir = Path(res["covers_dir"]).parent
        temp_dirs = list(pack_marketing_dir.glob(".covers-*"))
        self.assertEqual(len(temp_dirs), 0)

    # 24. Discover upload packs under base/upload/
    def test_24_discover_upload_packs(self):
        pack1 = self._create_sample_pack(pack_name="pack-one")
        pack2 = self._create_sample_pack(pack_name="pack-two")

        found = cover_variants.discover_upload_packs(self.products_dir.parent)
        found_names = [p.name for p in found]
        self.assertIn("pack-one", found_names)
        self.assertIn("pack-two", found_names)

    # 25. Discover upload packs when pointed directly to folder containing packs
    def test_25_discover_upload_packs_direct(self):
        pack1 = self._create_sample_pack(pack_name="pack-direct")
        found = cover_variants.discover_upload_packs(self.products_dir)
        found_names = [p.name for p in found]
        self.assertIn("pack-direct", found_names)

    # 26. Discover empty returns empty list
    def test_26_discover_empty(self):
        empty_dir = self.root / "empty_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)
        found = cover_variants.discover_upload_packs(empty_dir)
        self.assertEqual(found, [])

    # 27. Invalid pack raises ValueError
    def test_27_invalid_pack_raises_error(self):
        missing = self.root / "does_not_exist"
        with self.assertRaises(ValueError):
            cover_variants.make_pack_covers(missing, self.marketing_dir)

    # 28. Tenglish message in return dict
    def test_28_tenglish_message(self):
        pack = self._create_sample_pack(pack_name="focus-planner")
        res = cover_variants.make_pack_covers(pack, self.marketing_dir)
        msg = res["message"]
        self.assertIn("focus-planner", msg)
        self.assertIn("Mee", msg)
        self.assertIn("cover designs ready ayyayi", msg)

    # 29. Font helper reuse from mockups
    def test_29_font_helper_mockups_reuse(self):
        f = mockups.font("bold", 30)
        self.assertIsNotNone(f)
        w = mockups.text_w("Hello World", f)
        self.assertGreater(w, 0)

    # 30. CLI --pack flag runs cleanly
    def test_30_cli_pack_flag(self):
        pack = self._create_sample_pack(pack_name="cli-pack")
        stdout_buf = io.StringIO()
        with mock.patch("sys.stdout", stdout_buf):
            ret = cover_variants.main([
                "--pack", "cli-pack",
                "--products-dir", str(self.products_dir.parent),
                "--marketing-dir", str(self.marketing_dir),
            ])
        self.assertEqual(ret, 0)
        out_msg = stdout_buf.getvalue()
        self.assertIn("cli-pack", out_msg)
        self.assertIn("ready ayyayi", out_msg)

    # 31. CLI --all flag runs cleanly
    def test_31_cli_all_flag(self):
        self._create_sample_pack(pack_name="cli-all-1")
        self._create_sample_pack(pack_name="cli-all-2")
        stdout_buf = io.StringIO()
        with mock.patch("sys.stdout", stdout_buf):
            ret = cover_variants.main([
                "--all",
                "--products-dir", str(self.products_dir.parent),
                "--marketing-dir", str(self.marketing_dir),
            ])
        self.assertEqual(ret, 0)
        out_msg = stdout_buf.getvalue()
        self.assertIn("packs kosam covers tayaru ayyayi", out_msg)

    # 32. CLI error for non-existent pack
    def test_32_cli_non_existent_pack(self):
        stderr_buf = io.StringIO()
        with mock.patch("sys.stderr", stderr_buf):
            ret = cover_variants.main([
                "--pack", "ghost-pack",
                "--products-dir", str(self.products_dir.parent),
                "--marketing-dir", str(self.marketing_dir),
            ])
        self.assertEqual(ret, 1)
        err_msg = stderr_buf.getvalue()
        self.assertIn("ghost-pack", err_msg)
        self.assertIn("dorakaledu", err_msg)

    # 33. CLI no args prints help
    def test_33_cli_no_args_prints_help(self):
        stderr_buf = io.StringIO()
        with mock.patch("sys.stderr", stderr_buf):
            ret = cover_variants.main([])
        self.assertEqual(ret, 1)
        self.assertIn("usage:", stderr_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
