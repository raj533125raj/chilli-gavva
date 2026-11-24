"""CPD-2: end-to-end Gumroad upload preparation through the Telegram plugin loader."""
from __future__ import annotations

import base64
import io
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gumroad_preflight
import telegram_inbox as ti
import tg_upload
import upload_pack

OWNER = str(int("9876" "5432"))
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n1cAAAAASUVORK5CYII=")
MONEY = re.compile(r"(?:[$€£₹]\s*\d|\b(?:USD|INR|EUR|GBP)\s*\d|\d[\d,.]*\s*(?:USD|INR|EUR|GBP)\b)",
                   re.IGNORECASE)


class TelegramUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cpd_tg_upload_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.products = self.root / "products"
        self.products.mkdir()
        self.pack = self.products / "1-sample-pack"
        self.pack.mkdir()
        (self.pack / "listing.md").write_text(
            "# Focus Organizer\n\n"
            "## Description\n"
            "A practical workbook and guide for planning calm daily routines.\n\n"
            "## Tags (3)\n"
            "- focus\n- organizer\n- routine\n\n"
            "## What you get\n"
            "- A workbook\n- A guide\n",
            encoding="utf-8")
        (self.pack / "README.md").write_text("# Focus Organizer\nOpen the workbook and follow the guide.\n",
                                             encoding="utf-8")
        (self.pack / "LICENSE.txt").write_text("Personal use only.\n", encoding="utf-8")
        with zipfile.ZipFile(self.pack / "tracker.xlsx", "w", zipfile.ZIP_DEFLATED) as workbook:
            workbook.writestr("xl/workbook.xml", "<workbook><sheets/></workbook>")
            workbook.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData/></worksheet>")
        (self.pack / "cover.png").write_bytes(PNG)
        images = self.pack / "gumroad-images"
        images.mkdir()
        (images / gumroad_preflight.THUMBNAIL_NAME).write_bytes(PNG)
        (images / gumroad_preflight.COVER_NAME).write_bytes(PNG)

        self.good_checks = [{"name": "Buyer files exist", "ok": True, "fix_te": ""}]
        self.sent = []
        env = mock.patch.dict(os.environ, {
            "TELEGRAM_CHAT_ID": OWNER,
            "CHILLI_PRODUCTS_DIR": str(self.products),
            "CHILLI_TEST": "1",
        }, clear=False)
        env.start()
        self.addCleanup(env.stop)
        patches = (
            mock.patch.object(ti, "send_text", side_effect=self._send_text),
            mock.patch.object(ti, "send_document", side_effect=self._send_file("document")),
            mock.patch.object(ti, "send_photo", side_effect=self._send_file("photo")),
            mock.patch.object(upload_pack.truth_check, "check",
                              return_value={"verdict": "ready", "problems": []}),
            mock.patch.object(upload_pack.truth_check, "image_texts", return_value=[]),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _send_text(self, _token, _chat_id, text, buttons=None, keyboard=None):
        self.sent.append(("text", str(text), None, None))
        return True

    def _send_file(self, kind):
        def send(_token, _chat_id, path, caption=""):
            file_path = Path(path)
            self.sent.append((kind, file_path.name, file_path, file_path.read_bytes()))
            return True
        return send

    def call_loader(self, text, chat_id=OWNER):
        return ti.call_plugins(text, "test-token", chat_id, plugins=[tg_upload])

    def run_upload(self, text="/upload 1", checks=None):
        with mock.patch.object(gumroad_preflight, "check",
                               return_value=self.good_checks if checks is None else checks) as preflight:
            result = self.call_loader(text)
        return result, preflight

    def sent_files(self):
        return [item for item in self.sent if item[0] != "text"]

    def test_slash_upload_builds_and_sends_files_via_loader(self):
        (handled, result), _ = self.run_upload()
        self.assertTrue(handled)
        self.assertEqual(result, {"handled": True})
        self.assertEqual([item[1] for item in self.sent_files()],
                         ["product.zip", gumroad_preflight.THUMBNAIL_NAME,
                          gumroad_preflight.COVER_NAME, "listing.txt"])
        self.assertEqual(len([item for item in self.sent if item[0] == "text"]), 1)

    def test_numbered_plain_upload_alias_works(self):
        (handled, _), _ = self.run_upload("upload 1")
        self.assertTrue(handled)
        self.assertEqual(len(self.sent_files()), 4)

    def test_upload_with_bot_suffix_works(self):
        (handled, _), _ = self.run_upload("/upload@samplebot 1")
        self.assertTrue(handled)
        self.assertEqual(len(self.sent_files()), 4)

    def test_bare_upload_word_shows_help_without_files(self):
        handled, _ = self.call_loader("upload")
        self.assertTrue(handled)
        self.assertIn("/upload N", self.sent[-1][1])
        self.assertEqual(self.sent_files(), [])

    def test_bare_slash_command_shows_help_without_files(self):
        handled, _ = self.call_loader("/upload")
        self.assertTrue(handled)
        self.assertIn("/upload N", self.sent[-1][1])
        self.assertEqual(self.sent_files(), [])

    def test_nonmatching_text_is_not_claimed(self):
        handled, result = self.call_loader("please upload the item")
        self.assertFalse(handled)
        self.assertIsNone(result)
        self.assertEqual(self.sent, [])

    def test_missing_pack_replies_without_files(self):
        handled, _ = self.call_loader("/upload 88")
        self.assertTrue(handled)
        self.assertIn("Pack #88 kanipinchaledu", self.sent[-1][1])
        self.assertEqual(self.sent_files(), [])

    def test_preflight_failure_reports_fixes_and_sends_no_files(self):
        failures = [
            {"name": "Product ZIP opens", "ok": False, "fix_te": "product.zip ni malli valid ZIP ga build cheyandi."},
            {"name": "LICENSE is present", "ok": True, "fix_te": ""},
        ]
        with mock.patch.object(upload_pack, "make") as make:
            (handled, _), preflight = self.run_upload(checks=failures)
        self.assertTrue(handled)
        self.assertIn("❌", self.sent[-1][1])
        self.assertIn("product.zip ni malli valid ZIP ga build cheyandi.", self.sent[-1][1])
        self.assertEqual(self.sent_files(), [])
        make.assert_not_called()
        preflight.assert_called_once_with(self.pack)

    def test_empty_preflight_results_never_allow_upload(self):
        with mock.patch.object(upload_pack, "make") as make:
            (handled, _), _ = self.run_upload(checks=[])
        self.assertTrue(handled)
        self.assertIn("Preflight checks malli run cheyandi.", self.sent[-1][1])
        self.assertEqual(self.sent_files(), [])
        make.assert_not_called()

    def test_preflight_runs_before_upload_pack_make(self):
        order = []
        real_make = upload_pack.make

        def make(source, output):
            order.append("make")
            return real_make(source, output)

        with mock.patch.object(gumroad_preflight, "check",
                               side_effect=lambda _pack: order.append("preflight") or self.good_checks), \
             mock.patch.object(upload_pack, "make", side_effect=make):
            handled, _ = self.call_loader("/upload 1")
        self.assertTrue(handled)
        self.assertEqual(order, ["preflight", "make"])

    def test_upload_pack_is_created_outside_repo_in_temp_products(self):
        real_make = upload_pack.make
        inspected = []

        def make(source, output):
            source, output = Path(source).resolve(), Path(output).resolve()
            self.assertNotIn(ROOT, source.parents)
            self.assertNotIn(ROOT, output.parents)
            self.assertEqual(output.name, "products")
            self.assertFalse(any(path.is_dir() for path in source.iterdir()))
            inspected.append((source, output))
            return real_make(source, output)

        with mock.patch.object(upload_pack, "make", side_effect=make):
            (handled, _), _ = self.run_upload()
        self.assertTrue(handled)
        self.assertEqual(len(inspected), 1)

    def test_checked_images_are_copied_into_temp_upload_folder(self):
        (handled, _), _ = self.run_upload()
        self.assertTrue(handled)
        photos = [item for item in self.sent if item[0] == "photo"]
        self.assertEqual([item[1] for item in photos],
                         [gumroad_preflight.THUMBNAIL_NAME, gumroad_preflight.COVER_NAME])
        self.assertTrue(all(item[3] == PNG for item in photos))

    def test_temp_attachments_exist_during_send_and_are_cleaned_after(self):
        (handled, _), _ = self.run_upload()
        self.assertTrue(handled)
        attachments = self.sent_files()
        self.assertTrue(all(item[2].is_absolute() for item in attachments))
        self.assertTrue(all(not item[2].exists() for item in attachments))

    def test_buyer_zip_is_open_and_contains_product_files(self):
        (handled, _), _ = self.run_upload()
        self.assertTrue(handled)
        archive_path = self.sent_files()[0][2]
        with zipfile.ZipFile(io.BytesIO(self.sent_files()[0][3])) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {"tracker.xlsx", "README.md", "LICENSE.txt"})
        self.assertEqual(archive_path.name, "product.zip")

    def test_generated_listing_is_price_free_and_has_fields(self):
        (handled, _), _ = self.run_upload()
        self.assertTrue(handled)
        listing = next(item[3].decode("utf-8") for item in self.sent_files() if item[1] == "listing.txt")
        self.assertIn("Title: Focus Organizer", listing)
        self.assertIn("Description:", listing)
        self.assertIn("Tags: focus, organizer, routine", listing)
        self.assertNotIn("Price:", listing)

    def test_step_list_covers_all_owner_actions(self):
        (handled, _), _ = self.run_upload()
        self.assertTrue(handled)
        text = next(item[1] for item in self.sent if item[0] == "text")
        for step in ("New product", "Digital product", "product name", "Product.zip",
                     "Cover image", "description", "tags", "Price", "Publish"):
            self.assertIn(step, text)
        self.assertIn("Publish button owner click matrame", text)
        self.assertEqual(len(re.findall(r"(?m)^\d+\.", text)), 9)

    def test_outgoing_text_contains_no_money_amount(self):
        (handled, _), _ = self.run_upload()
        self.assertTrue(handled)
        for kind, text, _path, _data in self.sent:
            self.assertIsNone(MONEY.search(text), text)

    def test_other_product_directories_are_not_silently_removed(self):
        (self.pack / "unexpected").mkdir()
        (handled, _), _ = self.run_upload()
        self.assertTrue(handled)
        self.assertIn("Upload files prepare cheyalekapoyam", self.sent[-1][1])
        self.assertTrue(self.sent[-1][1].splitlines()[-1].startswith("Next: /upload 1"))
        self.assertEqual(self.sent_files(), [])

    def test_symlink_in_product_folder_is_refused(self):
        target = self.root / "external.txt"
        target.write_text("external content")
        (self.pack / "external-link.txt").symlink_to(target)
        (handled, _), _ = self.run_upload()
        self.assertTrue(handled)
        self.assertIn("Upload files prepare cheyalekapoyam", self.sent[-1][1])
        self.assertTrue(self.sent[-1][1].splitlines()[-1].startswith("Next: /upload 1"))
        self.assertEqual(self.sent_files(), [])

    def test_upload_pack_failure_is_reported_without_files(self):
        with mock.patch.object(upload_pack, "make", side_effect=ValueError("private diagnostic")):
            (handled, _), _ = self.run_upload()
        self.assertTrue(handled)
        self.assertIn("Upload files prepare cheyalekapoyam", self.sent[-1][1])
        self.assertTrue(self.sent[-1][1].splitlines()[-1].startswith("Next: /upload 1"))
        self.assertNotIn("private diagnostic", self.sent[-1][1])
        self.assertEqual(self.sent_files(), [])

    def test_failed_file_send_stops_remaining_uploads(self):
        with mock.patch.object(ti, "send_photo", return_value=False):
            (handled, _), _ = self.run_upload()
        self.assertTrue(handled)
        self.assertEqual(len(self.sent_files()), 1)
        self.assertIn("Thumbnail Telegram lo send avvaledu", self.sent[-1][1])

    def test_non_owner_is_ignored_when_owner_is_configured(self):
        handled, result = self.call_loader("/upload 1", chat_id=str(int("1111" "2222")))
        self.assertFalse(handled)
        self.assertIsNone(result)
        self.assertEqual(self.sent, [])

    def test_declared_command_and_word_are_reachable_in_loader(self):
        for text in ("/upload", "upload"):
            with self.subTest(text=text):
                self.sent.clear()
                handled, _ = self.call_loader(text)
                self.assertTrue(handled)
                self.assertIn("/upload N", self.sent[-1][1])


if __name__ == "__main__":
    unittest.main()
