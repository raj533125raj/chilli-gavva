"""CPC-1: Gumroad preflight checks against temporary product packs."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gumroad_images
import gumroad_preflight

try:
    from PIL import Image
except ImportError:
    Image = None


def _image_bytes(size):
    if Image is None:
        return b"not decoded without Pillow"
    stream = io.BytesIO()
    Image.new("RGB", size, "white").save(stream, "JPEG")
    return stream.getvalue()


THUMBNAIL_BYTES = _image_bytes(gumroad_images.THUMBNAIL_SIZE)
COVER_BYTES = _image_bytes(gumroad_images.COVER_SIZE)


def _listing(title="Sample planning pack", description="A practical guide for organizing daily work.",
             tags="planning, organizer"):
    return f"Title: {title}\n\nDescription:\n{description}\n\nTags: {tags}\n"


def _zip(path, member="buyer-guide.txt", contents=b"Useful buyer content.\n"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zipped:
        if member is not None:
            zipped.writestr(member, contents)
        zipped.writestr("Focus-page.md", "f" * 800)
        zipped.writestr("Notes-section.md", "n" * 800)
        zipped.writestr("Goal-tracker.md", "g" * 800)


class GumroadPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cpc_preflight_")
        self.addCleanup(self.tmp.cleanup)
        self.pack = Path(self.tmp.name) / "pack"
        self.pack.mkdir()
        images = self.pack / "gumroad-images"
        images.mkdir()
        (images / gumroad_preflight.THUMBNAIL_NAME).write_bytes(THUMBNAIL_BYTES)
        (images / gumroad_preflight.COVER_NAME).write_bytes(COVER_BYTES)
        _zip(self.pack / "product.zip")
        (self.pack / "listing.txt").write_text(_listing(), encoding="utf-8")
        (self.pack / "listing.md").write_text(
            "# Sample pack\n\nFocus page, Notes section, Goal tracker.\n", encoding="utf-8")
        (self.pack / "LICENSE.txt").write_text("Personal use license.\n", encoding="utf-8")

    def checks(self):
        return {item["name"]: item for item in gumroad_preflight.check(self.pack)}

    def assert_failed(self, name):
        item = self.checks()[name]
        self.assertFalse(item["ok"], name)
        self.assertTrue(item["fix_te"], name)

    def test_complete_pack_passes(self):
        self.assertTrue(all(item["ok"] for item in gumroad_preflight.check(self.pack)),
                        gumroad_preflight.check(self.pack))

    def test_check_result_shape(self):
        for item in gumroad_preflight.check(self.pack):
            self.assertEqual(set(item), {"name", "ok", "fix_te"})
            self.assertIsInstance(item["ok"], bool)

    def test_missing_thumbnail_fails(self):
        (self.pack / "gumroad-images" / gumroad_preflight.THUMBNAIL_NAME).unlink()
        self.assert_failed("Thumbnail image exists")

    def test_missing_cover_fails(self):
        (self.pack / "gumroad-images" / gumroad_preflight.COVER_NAME).unlink()
        self.assert_failed("Cover image exists")

    @unittest.skipUnless(Image is not None, "Pillow is optional")
    def test_thumbnail_wrong_size_fails(self):
        path = self.pack / "gumroad-images" / gumroad_preflight.THUMBNAIL_NAME
        path.write_bytes(_image_bytes((32, 32)))
        self.assert_failed("Thumbnail image size")

    @unittest.skipUnless(Image is not None, "Pillow is optional")
    def test_cover_wrong_size_fails(self):
        path = self.pack / "gumroad-images" / gumroad_preflight.COVER_NAME
        path.write_bytes(_image_bytes((32, 32)))
        self.assert_failed("Cover image size")

    @unittest.skipUnless(Image is not None, "Pillow is optional")
    def test_unreadable_thumbnail_fails(self):
        (self.pack / "gumroad-images" / gumroad_preflight.THUMBNAIL_NAME).write_bytes(b"bad image")
        self.assert_failed("Thumbnail image is readable")

    @unittest.skipUnless(Image is not None, "Pillow is optional")
    def test_unreadable_cover_fails(self):
        (self.pack / "gumroad-images" / gumroad_preflight.COVER_NAME).write_bytes(b"bad image")
        self.assert_failed("Cover image is readable")

    def test_pillow_absence_skips_dimension_checks(self):
        with mock.patch.object(gumroad_preflight, "_image_dimensions", return_value=None):
            checks = gumroad_preflight.check(self.pack)
        self.assertNotIn("Thumbnail image size", [item["name"] for item in checks])
        self.assertNotIn("Cover image size", [item["name"] for item in checks])

    def test_missing_archive_fails_open_check(self):
        (self.pack / "product.zip").unlink()
        self.assert_failed("Product ZIP opens")

    def test_corrupt_archive_fails_open_check(self):
        (self.pack / "product.zip").write_bytes(b"not a zip archive")
        self.assert_failed("Product ZIP opens")

    def test_corrupt_archive_member_fails_open_check(self):
        archive = self.pack / "product.zip"
        _zip(archive, contents=b"Buyer content.")
        with zipfile.ZipFile(archive) as zipped:
            info = zipped.getinfo("buyer-guide.txt")
            offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        with archive.open("r+b") as output:
            output.seek(offset)
            output.write(b"X")
        self.assert_failed("Product ZIP opens")

    def test_empty_archive_has_no_buyer_files(self):
        with zipfile.ZipFile(self.pack / "product.zip", "w"):
            pass
        (self.pack / "LICENSE.txt").unlink()
        (self.pack / "listing.md").unlink()
        self.assert_failed("Buyer files exist")

    def test_empty_external_buyer_file_fails(self):
        (self.pack / "guide.pdf").write_bytes(b"")
        self.assert_failed("Buyer files are not empty")

    def test_empty_archive_member_fails(self):
        _zip(self.pack / "product.zip", contents=b"")
        self.assert_failed("Buyer files are not empty")

    def test_listing_money_amount_fails(self):
        (self.pack / "listing.txt").write_text(
            _listing(description="This bundle costs $27."), encoding="utf-8")
        self.assert_failed("Listing has no money amount")

    def test_markdown_listing_money_amount_fails(self):
        (self.pack / "listing.md").write_text("Price 27 USD\n", encoding="utf-8")
        self.assert_failed("Listing has no money amount")

    def test_missing_title_fails(self):
        (self.pack / "listing.txt").write_text(_listing(title=""), encoding="utf-8")
        self.assert_failed("Listing title length")

    def test_long_title_fails(self):
        (self.pack / "listing.txt").write_text(_listing(title="x" * 151), encoding="utf-8")
        self.assert_failed("Listing title length")

    def test_title_at_limit_passes(self):
        import store_kits

        title = "x" * store_kits.STORE_LIMITS["gumroad"]["title_limit"]
        (self.pack / "listing.txt").write_text(_listing(title=title), encoding="utf-8")
        self.assertTrue(self.checks()["Listing title length"]["ok"])

    def test_thirteen_tags_pass(self):
        tags = ", ".join(f"tag {index}" for index in range(13))
        (self.pack / "listing.txt").write_text(_listing(tags=tags), encoding="utf-8")
        self.assertTrue(self.checks()["Listing has at most 13 tags"]["ok"])

    def test_more_than_thirteen_tags_fails(self):
        tags = ", ".join(f"tag {index}" for index in range(14))
        (self.pack / "listing.txt").write_text(_listing(tags=tags), encoding="utf-8")
        self.assert_failed("Listing has at most 13 tags")

    def test_missing_description_fails(self):
        (self.pack / "listing.txt").write_text("Title: Pack\n\nTags: planner\n", encoding="utf-8")
        self.assert_failed("Listing description is not empty")

    def test_blank_description_fails(self):
        (self.pack / "listing.txt").write_text(_listing(description="  "), encoding="utf-8")
        self.assert_failed("Listing description is not empty")

    def test_missing_license_fails(self):
        (self.pack / "LICENSE.txt").unlink()
        self.assert_failed("LICENSE is present")

    def test_empty_license_fails(self):
        (self.pack / "LICENSE.txt").write_text(" \n", encoding="utf-8")
        self.assert_failed("LICENSE is present")

    def test_truth_check_failure_is_reported(self):
        with mock.patch("truth_check.check", return_value={"verdict": "needs fix"}):
            self.assert_failed("Truth check is ready")

    def test_no_buyer_files_fails_even_with_archive(self):
        with zipfile.ZipFile(self.pack / "product.zip", "w"):
            pass
        (self.pack / "LICENSE.txt").unlink()
        self.assert_failed("Buyer files exist")

    def test_listing_file_is_not_required_for_no_money_check_to_run(self):
        (self.pack / "listing.txt").unlink()
        self.assertTrue(self.checks()["Listing has no money amount"]["ok"])


if __name__ == "__main__":
    unittest.main()
