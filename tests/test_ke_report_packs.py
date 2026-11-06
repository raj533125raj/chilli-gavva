"""Tests for upload packs line in daily_report.py (BINNY-KE ROBOT)."""
import datetime
import os
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daily_report as dr  # noqa: E402
import guardrails  # noqa: E402
import product_builder as pb  # noqa: E402

MONEY_PATTERN = re.compile(
    r"(\$\d+|\d+\s*\$|\b\d+\s*(?:dollars|USD|EUR|GBP|INR|Rupees)\b|[\$\€\£\₹]\d+)",
    re.IGNORECASE,
)


class TestKeReportPacks(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* env in setUp with mock.patch.dict(os.environ, {}) then delete the keys
        self.env_patcher = mock.patch.dict(os.environ, {})
        self.env_patcher.start()
        for k in [k for k in list(os.environ.keys()) if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT"]:
            del os.environ[k]
        self.addCleanup(self.env_patcher.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.db = os.path.join(self.tmp.name, "ke_daily.db")
        self.products_dir = os.path.join(self.tmp.name, "exported_content", "products")
        os.makedirs(self.products_dir, exist_ok=True)

        self.fixed_now = datetime.datetime(2026, 11, 6, 12, 0, 0)

    def test_missing_products_folder(self):
        missing_dir = os.path.join(self.tmp.name, "nonexistent_dir")
        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {}, products_dir=missing_dir)
        self.assertIn("Upload packs ready: 0", text)
        self.assertNotIn("(send /packs to see them)", text)
        self.assertLessEqual(len(text), 3500)

    def test_zero_upload_packs_with_empty_folder(self):
        upload_dir = os.path.join(self.products_dir, "upload")
        os.makedirs(upload_dir, exist_ok=True)

        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {}, products_dir=self.products_dir)
        self.assertIn("Upload packs ready: 0", text)
        self.assertNotIn("(send /packs to see them)", text)
        self.assertLessEqual(len(text), 3500)

    def test_two_ready_upload_packs(self):
        upload_dir = pathlib.Path(self.products_dir) / "upload"
        p1 = upload_dir / "1-focus-planner"
        p2 = upload_dir / "2-habit-tracker"
        p1.mkdir(parents=True, exist_ok=True)
        p2.mkdir(parents=True, exist_ok=True)

        # Both have product.zip and listing.txt
        (p1 / "product.zip").write_bytes(b"zip1")
        (p1 / "listing.txt").write_text("Title: Focus Planner\n", encoding="utf-8")
        (p2 / "product.zip").write_bytes(b"zip2")
        (p2 / "listing.txt").write_text("Title: Habit Tracker\n", encoding="utf-8")

        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {}, products_dir=self.products_dir)
        self.assertIn("Upload packs ready: 2 (send /packs to see them)", text)
        self.assertLessEqual(len(text), 3500)

    def test_incomplete_packs_not_counted(self):
        upload_dir = pathlib.Path(self.products_dir) / "upload"
        p1 = upload_dir / "1-ready"
        p2 = upload_dir / "2-missing-zip"
        p3 = upload_dir / "3-missing-listing"
        p4 = upload_dir / "file.txt"
        p1.mkdir(parents=True, exist_ok=True)
        p2.mkdir(parents=True, exist_ok=True)
        p3.mkdir(parents=True, exist_ok=True)
        p4.write_text("not a directory", encoding="utf-8")

        (p1 / "product.zip").write_bytes(b"zip")
        (p1 / "listing.txt").write_text("Title: Ready\n", encoding="utf-8")

        (p2 / "listing.txt").write_text("Title: No Zip\n", encoding="utf-8")
        (p3 / "product.zip").write_bytes(b"zip")

        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {}, products_dir=self.products_dir)
        self.assertIn("Upload packs ready: 1 (send /packs to see them)", text)
        self.assertLessEqual(len(text), 3500)

    def test_mock_product_builder_products_dir_default(self):
        upload_dir = pathlib.Path(self.products_dir) / "upload"
        p1 = upload_dir / "1-pack"
        p2 = upload_dir / "2-pack"
        p1.mkdir(parents=True, exist_ok=True)
        p2.mkdir(parents=True, exist_ok=True)
        (p1 / "product.zip").write_bytes(b"zip")
        (p1 / "listing.txt").write_text("Title: 1\n", encoding="utf-8")
        (p2 / "product.zip").write_bytes(b"zip")
        (p2 / "listing.txt").write_text("Title: 2\n", encoding="utf-8")

        with mock.patch.object(pb, "PRODUCTS_DIR", self.products_dir):
            text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {})
            self.assertIn("Upload packs ready: 2 (send /packs to see them)", text)

    def test_no_money_no_titles_no_paths_and_existing_lines_unchanged(self):
        upload_dir = pathlib.Path(self.products_dir) / "upload"
        p1 = upload_dir / "1-secret-folder-name"
        p1.mkdir(parents=True, exist_ok=True)
        (p1 / "product.zip").write_bytes(b"zip")
        (p1 / "listing.txt").write_text("Title: Secret Product Title $50 USD\n", encoding="utf-8")

        text = dr.daily_text(self.db, now=self.fixed_now, status_fn=lambda: {
            "month_minutes": 100, "month_runs": 10, "door_bell": True, "telegram_token_ok": "yes"
        }, products_dir=self.products_dir)

        self.assertIn("Upload packs ready: 1 (send /packs to see them)", text)
        # Ensure no pack titles or paths leaked into the upload packs line or text
        self.assertNotIn("Secret Product Title", text)
        self.assertNotIn("secret-folder-name", text)
        self.assertNotIn(str(upload_dir), text)
        self.assertNotIn("/upload/", text)

        # Existing lines check
        self.assertIn("Chilli Gavva daily report (2026-11-06)", text)
        self.assertIn("Drafts: 0 made, 0 waiting, 0 approved (last 24h)", text)
        self.assertIn("Auto-decided (last 24h): 0 approved, 0 rejected", text)
        self.assertIn("Products built: 0", text)
        self.assertIn("Built products (last 24h): none", text)
        self.assertIn("GitHub minutes this month: 100 of 2000 free (10 runs)", text)
        self.assertIn("Door bell: on", text)
        self.assertIn("Button presses received in the last 24h: 0", text)
        self.assertIn("Errors: none", text)
        self.assertIn("Next step for owner: nothing needed", text)

        # Money regex check
        self.assertEqual(MONEY_PATTERN.findall(text), [])
        self.assertLessEqual(len(text), 3500)


if __name__ == "__main__":
    unittest.main()
