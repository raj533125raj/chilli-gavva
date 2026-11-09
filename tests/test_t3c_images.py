"""tests/test_t3c_images.py — Unit tests for image_optimize.py.

At least 30 tests covering:
- PNG optimize and lossless palette mode conversion for <= 256 colors
- Exact pixel dimensions preserved for PNG and JPG
- Metadata stripping (EXIF, comments, timestamps)
- JPG photographic images saved with quality 85 and optimized
- Never makes a file bigger (original kept intact if candidate is larger or equal)
- Max-size rules for pins (< 1 MB), carousel (< 1.5 MB), covers (< 1 MB), reel (< 2 MB)
- Max-size reporting: passes vs violations
- Dry-run mode: calculates savings and checks rules without modifying any files on disk
- In-place optimization with atomic replacement
- Format savings table (headers, per-file rows, total row, formatting)
- Tenglish house style summary (Telugu sentences + English nouns, passes, fails, dry-run note)
- Guards against modifying inside private pack folders (products/upload/, products/bundles/)
- Idempotence: running optimize multiple times does not degrade or inflate files
- Unsupported formats handling (skipped gracefully)
- Deterministic traversal order
- No money amounts, no brand logos
- Pillow-only dependency check and GITHUB_* environment variable cleanup in setUp
"""

import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from PIL import Image, ImageChops, PngImagePlugin
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import image_optimize


@unittest.skipUnless(HAS_PIL, "Pillow is required for image optimization tests")
class TestT3CImages(unittest.TestCase):
    def setUp(self):
        # Clear all GITHUB_* and RUNNER_ENVIRONMENT variables using mock.patch.dict
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        patcher = mock.patch.dict(os.environ, env_clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp_dir = tempfile.mkdtemp(prefix="test_t3c_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

        self.root = Path(self.tmp_dir)
        self.marketing_dir = self.root / "exported_content" / "marketing"
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def _create_test_png(self, path: Path, size=(200, 200), color=(120, 50, 200), metadata: bool = False) -> Path:
        """Create a test PNG image."""
        path.parent.mkdir(parents=True, exist_ok=True)
        im = Image.new("RGB", size, color=color)
        png_info = PngImagePlugin.PngInfo()
        if metadata:
            png_info.add_text("Author", "Test Agent")
            png_info.add_text("Comment", "A test comment metadata")
            png_info.add_text("Software", "Chilli Gavva Test Tool")
            im.save(path, format="PNG", pnginfo=png_info)
        else:
            im.save(path, format="PNG")
        return path

    def _create_test_jpg(self, path: Path, size=(200, 200), quality: int = 100) -> Path:
        """Create a test JPG image."""
        path.parent.mkdir(parents=True, exist_ok=True)
        # Gradient pattern for realistic JPG
        im = Image.new("RGB", size)
        pixels = im.load()
        for x in range(size[0]):
            for y in range(size[1]):
                pixels[x, y] = (x % 256, y % 256, (x + y) % 256)
        im.save(path, format="JPEG", quality=quality)
        return path

    # 1. Exact pixel dimensions preserved for PNG
    def test_01_exact_pixel_dimensions_preserved_png(self):
        p = self.marketing_dir / "pack1" / "pins" / "pin-1.png"
        self._create_test_png(p, size=(1000, 1500))
        res = image_optimize.optimize_file(p, dry_run=False)
        with Image.open(p) as im:
            self.assertEqual(im.size, (1000, 1500))
        self.assertEqual(res["dimensions"], (1000, 1500))

    # 2. Exact pixel dimensions preserved for JPG
    def test_02_exact_pixel_dimensions_preserved_jpg(self):
        p = self.marketing_dir / "pack1" / "landing" / "hero.jpg"
        self._create_test_jpg(p, size=(800, 600))
        res = image_optimize.optimize_file(p, dry_run=False)
        with Image.open(p) as im:
            self.assertEqual(im.size, (800, 600))
        self.assertEqual(res["dimensions"], (800, 600))

    # 3. Stripping metadata from PNG
    def test_03_metadata_stripped_from_png(self):
        p = self.marketing_dir / "pack1" / "covers" / "cover-a.png"
        self._create_test_png(p, size=(1280, 720), metadata=True)
        with Image.open(p) as before:
            self.assertIn("Author", before.text)
        res = image_optimize.optimize_file(p, dry_run=False)
        with Image.open(p) as after:
            self.assertNotIn("Author", getattr(after, "text", {}))
            self.assertNotIn("Comment", getattr(after, "text", {}))

    # 4. PNG lossless palette conversion for simple colors (<= 256 colors)
    def test_04_png_lossless_palette_mode(self):
        p = self.marketing_dir / "pack1" / "pins" / "pin-flat.png"
        # 3 solid colors
        im = Image.new("RGB", (300, 300), color=(255, 0, 0))
        pixels = im.load()
        for x in range(100):
            for y in range(300):
                pixels[x, y] = (0, 255, 0)
        for x in range(100, 200):
            for y in range(300):
                pixels[x, y] = (0, 0, 255)
        p.parent.mkdir(parents=True, exist_ok=True)
        im.save(p, format="PNG")

        orig_size = p.stat().st_size
        res = image_optimize.optimize_file(p, dry_run=False)
        self.assertTrue(res["improved"])
        self.assertEqual(res["strategy"], "png_palette_lossless")
        self.assertLess(p.stat().st_size, orig_size)
        with Image.open(p) as opt_im:
            self.assertEqual(opt_im.mode, "P")

    # 5. PNG with > 256 colors does not force lossy palette
    def test_05_png_high_color_count_preserves_rgb(self):
        p = self.marketing_dir / "pack1" / "carousel" / "slide-1.png"
        # Gradient with thousands of distinct colors
        im = Image.new("RGB", (400, 400))
        pixels = im.load()
        for x in range(400):
            for y in range(400):
                pixels[x, y] = (x % 256, y % 256, (x * 7 + y) % 256)
        p.parent.mkdir(parents=True, exist_ok=True)
        im.save(p, format="PNG")

        res = image_optimize.optimize_file(p, dry_run=False)
        with Image.open(p) as opt_im:
            self.assertIn(opt_im.mode, ("RGB", "RGBA"))

    # 6. JPG photographic images saved with quality 85 and optimized
    def test_06_jpg_quality_85(self):
        p = self.marketing_dir / "pack1" / "landing" / "photo.jpg"
        self._create_test_jpg(p, size=(500, 500), quality=100)
        orig_size = p.stat().st_size
        res = image_optimize.optimize_file(p, dry_run=False)
        self.assertTrue(res["improved"])
        self.assertEqual(res["strategy"], "jpg_quality_85")
        self.assertLess(p.stat().st_size, orig_size)

    # 7. Never makes a file bigger (already optimal file kept intact)
    def test_07_never_makes_file_bigger(self):
        p = self.marketing_dir / "pack1" / "covers" / "tiny.png"
        im = Image.new("1", (10, 10), color=0)
        p.parent.mkdir(parents=True, exist_ok=True)
        im.save(p, format="PNG", optimize=True)
        orig_size = p.stat().st_size

        res = image_optimize.optimize_file(p, dry_run=False)
        self.assertFalse(res["improved"])
        self.assertEqual(res["status"], "no_gain_kept_original")
        self.assertEqual(p.stat().st_size, orig_size)

    # 8. Max size rule for pins (< 1 MB) passes when small
    def test_08_pins_max_size_pass(self):
        p = self.marketing_dir / "pack1" / "pins" / "pin-1.png"
        self._create_test_png(p, size=(100, 100))
        res = image_optimize.check_max_size(p)
        self.assertEqual(res["category"], "pins")
        self.assertEqual(res["limit"], 1_000_000)
        self.assertTrue(res["passed"])

    # 9. Max size rule for pins (< 1 MB) fails when >= 1 MB
    def test_09_pins_max_size_fail(self):
        p = self.marketing_dir / "pack1" / "pins" / "pin-heavy.png"
        res = image_optimize.check_max_size(p, size_bytes=1_050_000)
        self.assertEqual(res["category"], "pins")
        self.assertFalse(res["passed"])

    # 10. Max size rule for carousel (< 1.5 MB) passes
    def test_10_carousel_max_size_pass(self):
        p = self.marketing_dir / "pack1" / "carousel" / "slide-1.png"
        res = image_optimize.check_max_size(p, size_bytes=1_200_000)
        self.assertEqual(res["category"], "carousel")
        self.assertEqual(res["limit"], 1_500_000)
        self.assertTrue(res["passed"])

    # 11. Max size rule for carousel (< 1.5 MB) fails when >= 1.5 MB
    def test_11_carousel_max_size_fail(self):
        p = self.marketing_dir / "pack1" / "carousel" / "slide-heavy.png"
        res = image_optimize.check_max_size(p, size_bytes=1_500_000)
        self.assertEqual(res["category"], "carousel")
        self.assertFalse(res["passed"])

    # 12. Max size rule for covers (< 1 MB) passes
    def test_12_covers_max_size_pass(self):
        p = self.marketing_dir / "pack1" / "covers" / "cover-a.png"
        res = image_optimize.check_max_size(p, size_bytes=800_000)
        self.assertEqual(res["category"], "covers")
        self.assertEqual(res["limit"], 1_000_000)
        self.assertTrue(res["passed"])

    # 13. Max size rule for covers (< 1 MB) fails when >= 1 MB
    def test_13_covers_max_size_fail(self):
        p = self.marketing_dir / "pack1" / "covers" / "cover-huge.png"
        res = image_optimize.check_max_size(p, size_bytes=1_000_001)
        self.assertEqual(res["category"], "covers")
        self.assertFalse(res["passed"])

    # 14. Max size rule for reel (< 2 MB)
    def test_14_reel_max_size(self):
        p = self.marketing_dir / "pack1" / "reel" / "frame-1.png"
        res_pass = image_optimize.check_max_size(p, size_bytes=1_800_000)
        self.assertEqual(res_pass["category"], "reel")
        self.assertEqual(res_pass["limit"], 2_000_000)
        self.assertTrue(res_pass["passed"])

        res_fail = image_optimize.check_max_size(p, size_bytes=2_100_000)
        self.assertFalse(res_fail["passed"])

    # 15. Dry-run mode does not modify files on disk
    def test_15_dry_run_mode_does_not_modify_file(self):
        p = self.marketing_dir / "pack1" / "covers" / "cover-dry.png"
        self._create_test_png(p, size=(600, 400), metadata=True)
        mtime_before = p.stat().st_mtime_ns
        content_before = p.read_bytes()

        res = image_optimize.optimize_file(p, dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["status"], "dry_run")
        self.assertTrue(res["saved_bytes"] > 0)
        self.assertEqual(p.stat().st_mtime_ns, mtime_before)
        self.assertEqual(p.read_bytes(), content_before)

    # 16. In-place optimization with atomic replacement
    def test_16_atomic_replace_updates_file(self):
        p = self.marketing_dir / "pack1" / "covers" / "cover-atomic.png"
        self._create_test_png(p, size=(800, 600), metadata=True)
        size_before = p.stat().st_size

        res = image_optimize.optimize_file(p, dry_run=False)
        self.assertEqual(res["status"], "optimized")
        self.assertLess(p.stat().st_size, size_before)
        # Ensure no leftover temp files in directory
        temp_files = list(p.parent.glob(".*_opt_*"))
        self.assertEqual(temp_files, [])

    # 17. Guard against modifying files inside private pack folders (products/upload)
    def test_17_forbidden_inside_pack_folder_upload(self):
        pack_img = self.root / "products" / "upload" / "pack1" / "preview.png"
        self._create_test_png(pack_img, size=(200, 200))
        with self.assertRaises(PermissionError):
            image_optimize.optimize_file(pack_img, dry_run=False)

    # 18. Guard against modifying files inside bundles folder
    def test_18_forbidden_inside_pack_folder_bundles(self):
        bundle_img = self.root / "products" / "bundles" / "bundle1" / "cover.png"
        self._create_test_png(bundle_img, size=(200, 200))
        with self.assertRaises(PermissionError):
            image_optimize.optimize_file(bundle_img, dry_run=False)

    # 19. Format savings table contains headers and totals
    def test_19_format_savings_table(self):
        sample_results = [
            {
                "filename": "pin-1.png",
                "original_size": 1000,
                "optimized_size": 700,
                "saved_bytes": 300,
                "saved_percent": 30.0,
                "status": "optimized",
                "size_rule": {"passed": True, "category": "pins"},
            },
            {
                "filename": "slide-1.png",
                "original_size": 2000,
                "optimized_size": 1800,
                "saved_bytes": 200,
                "saved_percent": 10.0,
                "status": "optimized",
                "size_rule": {"passed": False, "category": "carousel"},
            },
        ]
        table = image_optimize.format_savings_table(sample_results)
        self.assertIn("File", table)
        self.assertIn("Original", table)
        self.assertIn("Optimized", table)
        self.assertIn("Saved", table)
        self.assertIn("TOTAL", table)
        self.assertIn("pin-1.png", table)
        self.assertIn("slide-1.png", table)
        self.assertIn("EXCEED (carousel)", table)

    # 20. Empty results table message
    def test_20_empty_results_table(self):
        table = image_optimize.format_savings_table([])
        self.assertEqual(table, "No marketing images found to optimize.")

    # 21. Tenglish summary on success
    def test_21_tenglish_summary_success(self):
        sample_results = [
            {
                "original_size": 2048,
                "saved_bytes": 1024,
                "size_rule": {"passed": True, "category": "pins"},
            }
        ]
        summary = image_optimize.generate_tenglish_summary(sample_results, dry_run=False)
        self.assertIn("Marketing images anni baaga optimize ayyayi", summary)
        self.assertIn("save chesamu", summary)
        self.assertIn("max-size rules anni pass ayyayi", summary)

    # 22. Tenglish summary with dry-run note
    def test_22_tenglish_summary_dry_run(self):
        sample_results = [
            {
                "original_size": 500000,
                "saved_bytes": 200000,
                "size_rule": {"passed": True, "category": "covers"},
            }
        ]
        summary = image_optimize.generate_tenglish_summary(sample_results, dry_run=True)
        self.assertIn("dry-run check", summary)

    # 23. Tenglish summary on rule violations
    def test_23_tenglish_summary_violations(self):
        sample_results = [
            {
                "original_size": 2000000,
                "saved_bytes": 1000,
                "size_rule": {"passed": False, "category": "pins"},
            }
        ]
        summary = image_optimize.generate_tenglish_summary(sample_results, dry_run=False)
        self.assertIn("size limits daatayi", summary)
        self.assertIn("pins", summary)

    # 24. Tenglish summary when no images exist
    def test_24_tenglish_summary_empty(self):
        summary = image_optimize.generate_tenglish_summary([])
        self.assertIn("Marketing folder lo images emi levu", summary)

    # 25. Idempotence: re-optimizing does not corrupt or enlarge file
    def test_25_idempotence(self):
        p = self.marketing_dir / "pack1" / "pins" / "pin-repeat.png"
        self._create_test_png(p, size=(400, 400), metadata=True)
        res1 = image_optimize.optimize_file(p, dry_run=False)
        size1 = p.stat().st_size

        res2 = image_optimize.optimize_file(p, dry_run=False)
        size2 = p.stat().st_size
        self.assertEqual(size1, size2)
        self.assertEqual(res2["status"], "no_gain_kept_original")

        with Image.open(p) as final_im:
            self.assertEqual(final_im.size, (400, 400))

    # 26. Unsupported extension is gracefully skipped
    def test_26_unsupported_extension_skipped(self):
        txt_file = self.marketing_dir / "pack1" / "pins" / "notes.txt"
        txt_file.parent.mkdir(parents=True, exist_ok=True)
        txt_file.write_text("Hello world")

        res = image_optimize.optimize_file(txt_file, dry_run=False)
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["saved_bytes"], 0)

    # 27. optimize_marketing_folder recursive discovery and deterministic sorting
    def test_27_optimize_marketing_folder_deterministic(self):
        p1 = self.marketing_dir / "pack-b" / "pins" / "pin-2.png"
        p2 = self.marketing_dir / "pack-a" / "covers" / "cover-1.png"
        p3 = self.marketing_dir / "pack-a" / "pins" / "pin-1.png"
        self._create_test_png(p1)
        self._create_test_png(p2)
        self._create_test_png(p3)

        found = image_optimize.find_marketing_images(self.marketing_dir)
        self.assertEqual(len(found), 3)
        # Check alphabetical sorting
        self.assertEqual(found[0], p2)
        self.assertEqual(found[1], p3)
        self.assertEqual(found[2], p1)

    # 28. optimize_marketing_folder end-to-end execution
    def test_28_optimize_marketing_folder_end_to_end(self):
        p1 = self.marketing_dir / "pack1" / "pins" / "pin-1.png"
        p2 = self.marketing_dir / "pack1" / "carousel" / "slide-1.png"
        self._create_test_png(p1, metadata=True)
        self._create_test_png(p2, metadata=True)

        res = image_optimize.optimize_marketing_folder(marketing_dir=self.marketing_dir, dry_run=False)
        self.assertEqual(res["total_images"], 2)
        self.assertTrue(res["all_rules_passed"])
        self.assertIn("TOTAL", res["table"])
        self.assertIn("optimize ayyayi", res["tenglish_summary"])

    # 29. No currency amounts or price patterns in image_optimize.py
    def test_29_no_currency_amounts_in_image_optimize(self):
        code_path = Path(image_optimize.__file__)
        code_text = code_path.read_text(encoding="utf-8")
        # Ensure no currency symbols ($ ₹ USD INR) with digits
        import re
        curr_pat = re.compile(r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)", re.I)
        self.assertIsNone(curr_pat.search(code_text))

    # 30. No brand logos or banned external names
    def test_30_no_banned_brands(self):
        banned = ["canva", "etsy", "notion", "shopify", "gumroad", "topmate", "goodnotes"]
        summary = image_optimize.generate_tenglish_summary([
            {"original_size": 100, "saved_bytes": 10, "size_rule": {"passed": True, "category": "pins"}}
        ])
        for brand in banned:
            self.assertNotIn(brand, summary.lower())

    # 31. Corrupted image bytes handling in optimize_image_bytes
    def test_31_corrupted_image_handling(self):
        bad_bytes = b"not an image at all"
        best, mod, info = image_optimize.optimize_image_bytes(bad_bytes, "corrupt.png")
        self.assertFalse(mod)
        self.assertEqual(best, bad_bytes)
        self.assertIn("error", info)

    # 32. Non-existent file raises FileNotFoundError
    def test_32_non_existent_file_error(self):
        missing = self.marketing_dir / "does_not_exist.png"
        with self.assertRaises(FileNotFoundError):
            image_optimize.optimize_file(missing)


if __name__ == "__main__":
    unittest.main()
