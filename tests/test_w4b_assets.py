"""tests/test_w4b_assets.py - Comprehensive Unit Tests for Owner Assets (BINNY-W4B).

Covers:
  Part 1: owner_assets.py core tests (Tests 1-36):
    - Table initialization, idempotent migrations.
    - Magic bytes detection for PNG, JPEG, WEBP.
    - Non-image files and unknown magic byte rejection.
    - SVG refusal in all forms (extension, <svg>, <!doctype svg>, namespaces, UTF-8 BOM).
    - Size validation (<= 2MB allowed, > 2MB rejected).
    - Empty file rejection.
    - SHA-256 deduplication and metadata updates.
    - Kind normalization ('logo', 'icon', 'photo').
    - Listing assets by kind and all.
    - Getting asset by ID and SHA-256.
    - Getting owner active logo (get_logo) with fallback and latest selection.
    - Deleting assets and counting assets.

  Part 2: tg_assets.py and telegram_inbox.py integration tests (Tests 37-75+):
    - COMMANDS structure and metadata.
    - /assets empty state and formatted listing.
    - /asset N retrieval (sending back via send_photo/send_document).
    - /asset N not found handling.
    - /asset delete N confirmation prompt with keyboard.
    - Answering confirmation 'Yes'/'avunu' deletes asset and removes keyboard.
    - Answering confirmation 'No'/'vaddu' cancels deletion and removes keyboard.
    - Plain-text triggers ('assets', 'na assets', 'asset delete 1', 'asset 1 pampu').
    - Photo uploads: valid PNG, JPG, WEBP saved with correct kind.
    - Document uploads: images saved with correct kind.
    - Document uploads: SVG refused.
    - Oversized uploads (> 2MB) refused.
    - Download failure handling.
    - Caption classification: 'idi logo', 'idi icon', 'brand logo', 'face icon', default 'photo'.
    - Unhandled non-asset text returns None (does not hijack builtins).
    - telegram_inbox build_plugin_ctx includes download_file and message.
    - telegram_inbox process_updates routes photo and document to plugins before media_brain.
    - telegram_inbox process_updates falls back to media_brain when plugin returns None.
    - Non-owner chat photo/document ignored.
"""

from __future__ import annotations

import datetime
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import owner_assets
import tg_assets
import telegram_inbox as ti

# Constant safe values
OWNER_CHAT_ID = int("1234" "5678")
OTHER_CHAT_ID = int("8765" "4321")

# Sample valid images
PNG_HEADER = b"\x89PNG\r\n\x1a\n"
SAMPLE_PNG = PNG_HEADER + b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
SAMPLE_JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00" + b"\xff\xdb\x00C\x00"
SAMPLE_WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 \x0e\x00\x00\x00\x30\x01\x00\x9d\x01\x2a\x01\x00\x01\x00"
SAMPLE_SVG = b"<svg xmlns=\"http://www.w3.org/2000/svg\"><circle r=\"10\"/></svg>"
SAMPLE_SVG_BOM = b"\xef\xbb\xbf<svg><rect/></svg>"
SAMPLE_SVG_DOCTYPE = b"<!DOCTYPE svg PUBLIC \"-//W3C//DTD SVG 1.1//EN\" \"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd\"><svg></svg>"


class TestOwnerAssetsCore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test_assets.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_init_db_creates_table(self):
        owner_assets.init_db(self.db)
        con = sqlite3.connect(self.db)
        try:
            cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='owner_assets'")
            self.assertIsNotNone(cur.fetchone())
        finally:
            con.close()

    def test_02_init_db_idempotent(self):
        owner_assets.init_db(self.db)
        owner_assets.init_db(self.db)
        self.assertEqual(owner_assets.count_assets(self.db), 0)

    def test_03_detect_png(self):
        fmt, mime = owner_assets.detect_image_format(SAMPLE_PNG)
        self.assertEqual(fmt, "PNG")
        self.assertEqual(mime, "image/png")

    def test_04_detect_jpg(self):
        fmt, mime = owner_assets.detect_image_format(SAMPLE_JPG)
        self.assertEqual(fmt, "JPEG")
        self.assertEqual(mime, "image/jpeg")

    def test_05_detect_webp(self):
        fmt, mime = owner_assets.detect_image_format(SAMPLE_WEBP)
        self.assertEqual(fmt, "WEBP")
        self.assertEqual(mime, "image/webp")

    def test_06_detect_unknown_format(self):
        fmt, mime = owner_assets.detect_image_format(b"RANDOM_BYTES_HERE_NOT_AN_IMAGE")
        self.assertIsNone(fmt)
        self.assertIsNone(mime)

    def test_07_detect_short_bytes(self):
        fmt, mime = owner_assets.detect_image_format(b"abc")
        self.assertIsNone(fmt)
        self.assertIsNone(mime)

    def test_08_is_svg_by_filename(self):
        self.assertTrue(owner_assets.is_svg(b"some content", "logo.svg"))
        self.assertTrue(owner_assets.is_svg(b"some content", "LOGO.SVG"))

    def test_09_is_svg_by_tag(self):
        self.assertTrue(owner_assets.is_svg(SAMPLE_SVG, "image.dat"))

    def test_10_is_svg_with_bom(self):
        self.assertTrue(owner_assets.is_svg(SAMPLE_SVG_BOM, "unknown"))

    def test_11_is_svg_with_doctype(self):
        self.assertTrue(owner_assets.is_svg(SAMPLE_SVG_DOCTYPE, "unknown"))

    def test_12_is_svg_negative_for_png(self):
        self.assertFalse(owner_assets.is_svg(SAMPLE_PNG, "image.png"))

    def test_13_validate_empty_data(self):
        ok, err, info = owner_assets.validate_asset_bytes(b"", "empty.png")
        self.assertFalse(ok)
        self.assertIn("empty", err.lower())

    def test_14_validate_refuses_svg(self):
        ok, err, info = owner_assets.validate_asset_bytes(SAMPLE_SVG, "test.svg")
        self.assertFalse(ok)
        self.assertIn("SVG", err)

    def test_15_validate_refuses_oversized(self):
        oversized = SAMPLE_PNG + b"\x00" * (2 * 1024 * 1024 + 10)
        ok, err, info = owner_assets.validate_asset_bytes(oversized, "big.png")
        self.assertFalse(ok)
        self.assertIn("2 MB", err)

    def test_16_validate_accepts_valid_png(self):
        ok, err, info = owner_assets.validate_asset_bytes(SAMPLE_PNG, "pic.png")
        self.assertTrue(ok)
        self.assertEqual(info["format"], "PNG")
        self.assertEqual(info["mime_type"], "image/png")
        self.assertIn("sha256", info)

    def test_17_validate_accepts_valid_jpg(self):
        ok, err, info = owner_assets.validate_asset_bytes(SAMPLE_JPG, "pic.jpg")
        self.assertTrue(ok)
        self.assertEqual(info["format"], "JPEG")

    def test_18_validate_accepts_valid_webp(self):
        ok, err, info = owner_assets.validate_asset_bytes(SAMPLE_WEBP, "pic.webp")
        self.assertTrue(ok)
        self.assertEqual(info["format"], "WEBP")

    def test_19_normalize_kind(self):
        self.assertEqual(owner_assets.normalize_kind("logo"), "logo")
        self.assertEqual(owner_assets.normalize_kind("Brand Logo"), "logo")
        self.assertEqual(owner_assets.normalize_kind("face icon"), "icon")
        self.assertEqual(owner_assets.normalize_kind("icon"), "icon")
        self.assertEqual(owner_assets.normalize_kind("avatar"), "icon")
        self.assertEqual(owner_assets.normalize_kind("photo"), "photo")
        self.assertEqual(owner_assets.normalize_kind(""), "photo")
        self.assertEqual(owner_assets.normalize_kind("something else"), "photo")

    def test_20_store_asset_png_success(self):
        res = owner_assets.store_asset(self.db, SAMPLE_PNG, "my_logo.png", kind="logo", caption="Main store logo")
        self.assertTrue(res["ok"])
        self.assertFalse(res["duplicate"])
        self.assertEqual(res["kind"], "logo")
        self.assertEqual(res["id"], 1)

    def test_21_store_asset_deduplication(self):
        res1 = owner_assets.store_asset(self.db, SAMPLE_PNG, "my_logo.png", kind="logo", caption="First")
        res2 = owner_assets.store_asset(self.db, SAMPLE_PNG, "my_logo_v2.png", kind="icon", caption="Second")
        self.assertTrue(res2["ok"])
        self.assertTrue(res2["duplicate"])
        self.assertEqual(res2["id"], res1["id"])
        self.assertEqual(res2["kind"], "icon")  # updated kind

    def test_22_store_asset_svg_fails(self):
        res = owner_assets.store_asset(self.db, SAMPLE_SVG, "logo.svg")
        self.assertFalse(res["ok"])
        self.assertIn("error", res)

    def test_23_store_asset_oversized_fails(self):
        big = SAMPLE_PNG + b"\x00" * (2 * 1024 * 1024 + 50)
        res = owner_assets.store_asset(self.db, big, "big.png")
        self.assertFalse(res["ok"])

    def test_24_list_assets_empty(self):
        items = owner_assets.list_assets(self.db)
        self.assertEqual(items, [])

    def test_25_list_assets_all(self):
        owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        owner_assets.store_asset(self.db, SAMPLE_JPG, "photo.jpg", kind="photo")
        items = owner_assets.list_assets(self.db)
        self.assertEqual(len(items), 2)
        self.assertNotIn("data", items[0])  # no heavy binary blob in list

    def test_26_list_assets_filter_kind(self):
        owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        owner_assets.store_asset(self.db, SAMPLE_JPG, "photo.jpg", kind="photo")
        owner_assets.store_asset(self.db, SAMPLE_WEBP, "icon.webp", kind="icon")
        logos = owner_assets.list_assets(self.db, kind="logo")
        icons = owner_assets.list_assets(self.db, kind="icon")
        self.assertEqual(len(logos), 1)
        self.assertEqual(logos[0]["filename"], "logo.png")
        self.assertEqual(len(icons), 1)

    def test_27_get_asset_found(self):
        res = owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo", caption="Store logo")
        asset = owner_assets.get_asset(self.db, res["id"])
        self.assertIsNotNone(asset)
        self.assertEqual(asset["data"], SAMPLE_PNG)
        self.assertEqual(asset["caption"], "Store logo")

    def test_28_get_asset_not_found(self):
        asset = owner_assets.get_asset(self.db, 999)
        self.assertIsNone(asset)

    def test_29_get_asset_by_sha256(self):
        res = owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        asset = owner_assets.get_asset_by_sha256(self.db, res["sha256"])
        self.assertIsNotNone(asset)
        self.assertEqual(asset["id"], res["id"])

    def test_30_get_asset_by_sha256_missing(self):
        asset = owner_assets.get_asset_by_sha256(self.db, "nonexistenthash")
        self.assertIsNone(asset)

    def test_31_get_logo_none_when_empty(self):
        logo = owner_assets.get_logo(self.db)
        self.assertIsNone(logo)

    def test_32_get_logo_returns_latest_logo(self):
        owner_assets.store_asset(self.db, SAMPLE_PNG, "old_logo.png", kind="logo")
        owner_assets.store_asset(self.db, SAMPLE_JPG, "new_logo.jpg", kind="logo")
        logo = owner_assets.get_logo(self.db)
        self.assertIsNotNone(logo)
        self.assertEqual(logo["filename"], "new_logo.jpg")
        self.assertEqual(logo["data"], SAMPLE_JPG)

    def test_33_get_logo_ignores_non_logos(self):
        owner_assets.store_asset(self.db, SAMPLE_JPG, "avatar.jpg", kind="icon")
        logo = owner_assets.get_logo(self.db)
        self.assertIsNone(logo)

    def test_34_delete_asset_existing(self):
        res = owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        ok = owner_assets.delete_asset(self.db, res["id"])
        self.assertTrue(ok)
        self.assertIsNone(owner_assets.get_asset(self.db, res["id"]))

    def test_35_delete_asset_non_existent(self):
        ok = owner_assets.delete_asset(self.db, 999)
        self.assertFalse(ok)

    def test_36_count_assets(self):
        self.assertEqual(owner_assets.count_assets(self.db), 0)
        owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        owner_assets.store_asset(self.db, SAMPLE_JPG, "photo.jpg", kind="photo")
        self.assertEqual(owner_assets.count_assets(self.db), 2)
        self.assertEqual(owner_assets.count_assets(self.db, kind="logo"), 1)
        self.assertEqual(owner_assets.count_assets(self.db, kind="photo"), 1)
        self.assertEqual(owner_assets.count_assets(self.db, kind="icon"), 0)


class TestTgAssetsPluginAndInbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test_tg_assets.db")
        self.state_store = {}

        self.mock_sent_text = []
        self.mock_sent_photos = []
        self.mock_sent_docs = []

        # Fake files dictionary for download_file: file_id -> bytes
        self.fake_remote_files = {}

        def _fake_download(file_id, dest_path, max_bytes=2 * 1024 * 1024):
            if file_id not in self.fake_remote_files:
                return False
            data = self.fake_remote_files[file_id]
            if len(data) > max_bytes:
                return False
            dest_dir = os.path.dirname(dest_path)
            if dest_dir:
                os.makedirs(dest_dir, exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(data)
            return True

        self.fake_download_fn = _fake_download

    def tearDown(self):
        self.tmp.cleanup()

    def _make_ctx(self, message=None):
        return {
            "db_path": self.db,
            "chat_id": str(OWNER_CHAT_ID),
            "now": datetime.datetime.now(datetime.timezone.utc),
            "send_text": lambda t, keyboard=None: self.mock_sent_text.append((t, keyboard)),
            "send_document": lambda p, caption="": self.mock_sent_docs.append((p, caption)),
            "send_photo": lambda p, caption="": self.mock_sent_photos.append((p, caption)),
            "send_media_group": lambda ps, caption="": None,
            "download_file": self.fake_download_fn,
            "state_get": lambda k, default=None: self.state_store.get(k, default),
            "state_set": lambda k, v: self.state_store.__setitem__(k, v),
            "message": message,
        }

    def test_37_commands_metadata(self):
        cmds = tg_assets.COMMANDS
        self.assertTrue(any(c["cmd"] == "/assets" for c in cmds))
        self.assertTrue(any(c["cmd"] == "/asset" for c in cmds))
        for c in cmds:
            self.assertIn("help_en", c)
            self.assertIn("help_te", c)
            self.assertIn("words", c)

    def test_38_parse_kind_from_caption(self):
        self.assertEqual(tg_assets.parse_kind_from_caption("idi logo")[0], "logo")
        self.assertEqual(tg_assets.parse_kind_from_caption("Here is our brand logo")[0], "logo")
        self.assertEqual(tg_assets.parse_kind_from_caption("idi icon")[0], "icon")
        self.assertEqual(tg_assets.parse_kind_from_caption("my face icon")[0], "icon")
        self.assertEqual(tg_assets.parse_kind_from_caption("random product photo")[0], "photo")
        self.assertEqual(tg_assets.parse_kind_from_caption("")[0], "photo")

    def test_39_handle_assets_empty_state(self):
        ctx = self._make_ctx()
        res = tg_assets.handle("/assets", ctx)
        self.assertIsNotNone(res)
        self.assertIn("text", res)
        self.assertIn("Owner Assets emi levu", res["text"])

    def test_40_handle_assets_listing_with_items(self):
        owner_assets.store_asset(self.db, SAMPLE_PNG, "shop_logo.png", kind="logo")
        owner_assets.store_asset(self.db, SAMPLE_WEBP, "avatar.webp", kind="icon")
        ctx = self._make_ctx()
        res = tg_assets.handle("/assets", ctx)
        self.assertIsNotNone(res)
        self.assertIn("Owner Assets (2 items)", res["text"])
        self.assertIn("shop_logo.png", res["text"])
        self.assertIn("avatar.webp", res["text"])

    def test_41_handle_assets_plain_text_trigger(self):
        ctx = self._make_ctx()
        for trig in ["assets", "na assets", "asset list", "assets list", "my assets"]:
            res = tg_assets.handle(trig, ctx)
            self.assertIsNotNone(res, f"Failed for trigger: {trig}")
            self.assertIn("Owner Assets", res["text"])

    def test_42_handle_asset_help_alone(self):
        ctx = self._make_ctx()
        res = tg_assets.handle("/asset", ctx)
        self.assertIsNotNone(res)
        self.assertIn("/assets - Saved assets list", res["text"])

    def test_43_handle_asset_get_not_found(self):
        ctx = self._make_ctx()
        res = tg_assets.handle("/asset 1", ctx)
        self.assertIsNotNone(res)
        self.assertIn("kanipinchaledu", res["text"])

    def test_44_handle_asset_get_success(self):
        owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        ctx = self._make_ctx()
        res = tg_assets.handle("/asset 1", ctx)
        self.assertTrue(res.get("handled") or "text" in res)
        self.assertTrue(len(self.mock_sent_photos) > 0 or len(self.mock_sent_docs) > 0)

    def test_45_handle_asset_get_plain_trigger(self):
        owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        ctx = self._make_ctx()
        res = tg_assets.handle("asset 1 pampu", ctx)
        self.assertTrue(res.get("handled") or "text" in res)
        self.assertTrue(len(self.mock_sent_photos) > 0 or len(self.mock_sent_docs) > 0)

    def test_46_handle_asset_delete_prompt(self):
        owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        ctx = self._make_ctx()
        res = tg_assets.handle("/asset delete 1", ctx)
        self.assertIsNotNone(res)
        self.assertIn("delete cheyala", res["text"])
        self.assertIn("keyboard", res)
        self.assertEqual(self.state_store.get(tg_assets.STATE_PENDING_DELETE), "1")

    def test_47_handle_asset_delete_plain_trigger(self):
        owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        ctx = self._make_ctx()
        res = tg_assets.handle("asset 1 delete", ctx)
        self.assertIsNotNone(res)
        self.assertIn("delete cheyala", res["text"])

    def test_48_handle_asset_delete_confirm_yes(self):
        res_store = owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        self.state_store[tg_assets.STATE_PENDING_DELETE] = str(res_store["id"])
        ctx = self._make_ctx()
        res = tg_assets.handle("Yes", ctx)
        self.assertIsNotNone(res)
        self.assertIn("delete chesam", res["text"])
        self.assertEqual(res["keyboard"], "remove")
        self.assertIsNone(self.state_store.get(tg_assets.STATE_PENDING_DELETE))
        self.assertIsNone(owner_assets.get_asset(self.db, res_store["id"]))

    def test_49_handle_asset_delete_confirm_avunu(self):
        res_store = owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        self.state_store[tg_assets.STATE_PENDING_DELETE] = str(res_store["id"])
        ctx = self._make_ctx()
        res = tg_assets.handle("avunu", ctx)
        self.assertIn("delete chesam", res["text"])

    def test_50_handle_asset_delete_cancel_no(self):
        res_store = owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        self.state_store[tg_assets.STATE_PENDING_DELETE] = str(res_store["id"])
        ctx = self._make_ctx()
        res = tg_assets.handle("No", ctx)
        self.assertIn("cancel chesam", res["text"])
        self.assertEqual(res["keyboard"], "remove")
        self.assertIsNone(self.state_store.get(tg_assets.STATE_PENDING_DELETE))
        self.assertIsNotNone(owner_assets.get_asset(self.db, res_store["id"]))

    def test_51_handle_asset_delete_cancel_vaddu(self):
        res_store = owner_assets.store_asset(self.db, SAMPLE_PNG, "logo.png", kind="logo")
        self.state_store[tg_assets.STATE_PENDING_DELETE] = str(res_store["id"])
        ctx = self._make_ctx()
        res = tg_assets.handle("vaddu", ctx)
        self.assertIn("cancel chesam", res["text"])

    def test_52_photo_upload_saves_logo_when_caption_has_idi_logo(self):
        self.fake_remote_files["f_logo"] = SAMPLE_PNG
        msg = {
            "chat": {"id": OWNER_CHAT_ID},
            "photo": [{"file_id": "f_logo", "file_size": len(SAMPLE_PNG)}],
            "caption": "idi logo",
        }
        ctx = self._make_ctx(message=msg)
        res = tg_assets.handle("idi logo", ctx)
        self.assertIsNotNone(res)
        self.assertIn("Asset save ayyindi", res["text"])
        self.assertIn("LOGO", res["text"])
        logo = owner_assets.get_logo(self.db)
        self.assertIsNotNone(logo)

    def test_53_photo_upload_saves_icon_when_caption_has_idi_icon(self):
        self.fake_remote_files["f_icon"] = SAMPLE_WEBP
        msg = {
            "chat": {"id": OWNER_CHAT_ID},
            "photo": [{"file_id": "f_icon", "file_size": len(SAMPLE_WEBP)}],
            "caption": "idi icon",
        }
        ctx = self._make_ctx(message=msg)
        res = tg_assets.handle("idi icon", ctx)
        self.assertIsNotNone(res)
        self.assertIn("ICON", res["text"])
        icons = owner_assets.list_assets(self.db, kind="icon")
        self.assertEqual(len(icons), 1)

    def test_54_photo_upload_default_to_photo_kind(self):
        self.fake_remote_files["f_pic"] = SAMPLE_JPG
        msg = {
            "chat": {"id": OWNER_CHAT_ID},
            "photo": [{"file_id": "f_pic", "file_size": len(SAMPLE_JPG)}],
            "caption": "check this out",
        }
        ctx = self._make_ctx(message=msg)
        res = tg_assets.handle("check this out", ctx)
        self.assertIsNotNone(res)
        self.assertIn("PHOTO", res["text"])
        photos = owner_assets.list_assets(self.db, kind="photo")
        self.assertEqual(len(photos), 1)

    def test_55_document_upload_image_success(self):
        self.fake_remote_files["f_doc_png"] = SAMPLE_PNG
        msg = {
            "chat": {"id": OWNER_CHAT_ID},
            "document": {
                "file_id": "f_doc_png",
                "file_name": "branding_logo.png",
                "file_size": len(SAMPLE_PNG),
                "mime_type": "image/png",
            },
            "caption": "idi logo",
        }
        ctx = self._make_ctx(message=msg)
        res = tg_assets.handle("idi logo", ctx)
        self.assertIsNotNone(res)
        self.assertIn("Asset save ayyindi", res["text"])

    def test_56_document_upload_svg_refused(self):
        self.fake_remote_files["f_doc_svg"] = SAMPLE_SVG
        msg = {
            "chat": {"id": OWNER_CHAT_ID},
            "document": {
                "file_id": "f_doc_svg",
                "file_name": "vector_logo.svg",
                "file_size": len(SAMPLE_SVG),
                "mime_type": "image/svg+xml",
            },
            "caption": "idi logo",
        }
        ctx = self._make_ctx(message=msg)
        res = tg_assets.handle("idi logo", ctx)
        self.assertIsNotNone(res)
        self.assertIn("SVG", res["text"])
        self.assertEqual(owner_assets.count_assets(self.db), 0)

    def test_57_upload_oversized_refused(self):
        oversized_data = SAMPLE_PNG + b"\x00" * (2 * 1024 * 1024 + 50)
        self.fake_remote_files["f_big"] = oversized_data
        msg = {
            "chat": {"id": OWNER_CHAT_ID},
            "photo": [{"file_id": "f_big", "file_size": len(oversized_data)}],
            "caption": "huge image",
        }
        ctx = self._make_ctx(message=msg)
        res = tg_assets.handle("huge image", ctx)
        self.assertIsNotNone(res)
        self.assertIn("2 MB", res["text"])
        self.assertEqual(owner_assets.count_assets(self.db), 0)

    def test_58_upload_download_error_handled(self):
        msg = {
            "chat": {"id": OWNER_CHAT_ID},
            "photo": [{"file_id": "f_nonexistent", "file_size": 100}],
            "caption": "missing file",
        }
        ctx = self._make_ctx(message=msg)
        res = tg_assets.handle("missing file", ctx)
        self.assertIsNotNone(res)
        self.assertIn("fail", res["text"].lower())

    def test_59_unhandled_text_returns_none(self):
        ctx = self._make_ctx()
        self.assertIsNone(tg_assets.handle("/status", ctx))
        self.assertIsNone(tg_assets.handle("random text hello", ctx))
        self.assertIsNone(tg_assets.handle("/list", ctx))

    def test_60_inbox_build_plugin_ctx_has_download_file_and_message(self):
        fake_msg = {"text": "hello"}
        ctx = ti.build_plugin_ctx("fake_token", OWNER_CHAT_ID, db_path=self.db, message=fake_msg)
        self.assertIn("download_file", ctx)
        self.assertTrue(callable(ctx["download_file"]))
        self.assertIn("message", ctx)
        self.assertEqual(ctx["message"], fake_msg)

    def test_61_download_telegram_file_to_path_helper(self):
        with mock.patch("media_brain.download_telegram_file", return_value=(SAMPLE_PNG, None)):
            out_path = os.path.join(self.tmp.name, "downloaded.png")
            ok = ti.download_telegram_file_to_path("tok", "file123", out_path, max_bytes=1024 * 1024)
            self.assertTrue(ok)
            self.assertTrue(os.path.isfile(out_path))
            with open(out_path, "rb") as f:
                self.assertEqual(f.read(), SAMPLE_PNG)

    def test_62_download_telegram_file_to_path_failure_when_err(self):
        with mock.patch("media_brain.download_telegram_file", return_value=(None, "network timeout")):
            out_path = os.path.join(self.tmp.name, "downloaded_fail.png")
            ok = ti.download_telegram_file_to_path("tok", "file123", out_path)
            self.assertFalse(ok)
            self.assertFalse(os.path.isfile(out_path))

    def test_63_download_telegram_file_to_path_failure_when_too_large(self):
        too_big = b"x" * 200
        with mock.patch("media_brain.download_telegram_file", return_value=(too_big, None)):
            out_path = os.path.join(self.tmp.name, "downloaded_big.png")
            ok = ti.download_telegram_file_to_path("tok", "file123", out_path, max_bytes=100)
            self.assertFalse(ok)

    def test_64_process_updates_routes_photo_to_plugin(self):
        upds = [{
            "update_id": 101,
            "message": {
                "chat": {"id": OWNER_CHAT_ID},
                "photo": [{"file_id": "p1", "file_size": len(SAMPLE_PNG)}],
                "caption": "idi logo",
            }
        }]
        stats = {"text": 0, "commands": 0, "other": 0, "skipped": {"not owner chat": 0}}
        with mock.patch.object(ti, "fetch_updates", return_value=upds), \
             mock.patch.object(ti, "_call", return_value={"ok": True}), \
             mock.patch("media_brain.download_telegram_file", return_value=(SAMPLE_PNG, None)), \
             mock.patch.object(ti, "send_text") as mock_send, \
             mock.patch("media_brain.process_photo") as mock_mb_photo:
            handled = ti.process_updates("fake_token", OWNER_CHAT_ID, db_path=self.db, stats=stats)
            self.assertEqual(handled, 1)
            mock_send.assert_called_once()
            call_text = mock_send.call_args[0][2]
            self.assertIn("Asset save ayyindi", call_text)
            self.assertIn("LOGO", call_text)
            # media_brain should NOT have been called because plugin handled it
            mock_mb_photo.assert_not_called()

    def test_65_process_updates_routes_document_to_plugin(self):
        upds = [{
            "update_id": 102,
            "message": {
                "chat": {"id": OWNER_CHAT_ID},
                "document": {
                    "file_id": "d1",
                    "file_name": "my_icon.png",
                    "file_size": len(SAMPLE_PNG),
                    "mime_type": "image/png"
                },
                "caption": "idi icon",
            }
        }]
        stats = {"text": 0, "commands": 0, "other": 0, "skipped": {"not owner chat": 0}}
        with mock.patch.object(ti, "fetch_updates", return_value=upds), \
             mock.patch.object(ti, "_call", return_value={"ok": True}), \
             mock.patch("media_brain.download_telegram_file", return_value=(SAMPLE_PNG, None)), \
             mock.patch.object(ti, "send_text") as mock_send, \
             mock.patch("media_brain.process_document") as mock_mb_doc:
            handled = ti.process_updates("fake_token", OWNER_CHAT_ID, db_path=self.db, stats=stats)
            self.assertEqual(handled, 1)
            mock_send.assert_called_once()
            call_text = mock_send.call_args[0][2]
            self.assertIn("Asset save ayyindi", call_text)
            self.assertIn("ICON", call_text)
            mock_mb_doc.assert_not_called()

    def test_66_process_updates_falls_back_to_media_brain_when_unhandled(self):
        # A PDF document is not handled by tg_assets -> falls back to media_brain.process_document
        upds = [{
            "update_id": 103,
            "message": {
                "chat": {"id": OWNER_CHAT_ID},
                "document": {
                    "file_id": "d_pdf",
                    "file_name": "notes.pdf",
                    "file_size": 500,
                    "mime_type": "application/pdf"
                },
                "caption": "read this note",
            }
        }]
        stats = {"text": 0, "commands": 0, "other": 0, "skipped": {"not owner chat": 0}}
        with mock.patch.object(ti, "fetch_updates", return_value=upds), \
             mock.patch.object(ti, "_call", return_value={"ok": True}), \
             mock.patch("media_brain.process_document", return_value=("Doc parsed by media brain", [], 1)) as mock_mb_doc, \
             mock.patch.object(ti, "send_text") as mock_send:
            handled = ti.process_updates("fake_token", OWNER_CHAT_ID, db_path=self.db, stats=stats)
            self.assertEqual(handled, 1)
            mock_mb_doc.assert_called_once()

    def test_67_non_owner_photo_ignored(self):
        upds = [{
            "update_id": 104,
            "message": {
                "chat": {"id": OTHER_CHAT_ID},
                "photo": [{"file_id": "p_intruder", "file_size": len(SAMPLE_PNG)}],
                "caption": "idi logo",
            }
        }]
        stats = {"text": 0, "commands": 0, "other": 0, "skipped": {"not owner chat": 0}}
        with mock.patch.object(ti, "fetch_updates", return_value=upds), \
             mock.patch.object(ti, "_call", return_value={"ok": True}), \
             mock.patch.object(ti, "send_text") as mock_send:
            handled = ti.process_updates("fake_token", OWNER_CHAT_ID, db_path=self.db, stats=stats)
            self.assertEqual(handled, 0)
            mock_send.assert_not_called()
            self.assertEqual(stats["skipped"]["not owner chat"], 1)

    def test_68_process_updates_assets_slash_command(self):
        owner_assets.store_asset(self.db, SAMPLE_PNG, "test.png", kind="photo")
        upds = [{
            "update_id": 105,
            "message": {
                "chat": {"id": OWNER_CHAT_ID},
                "text": "/assets",
            }
        }]
        stats = {"text": 0, "commands": 0, "other": 0, "skipped": {"not owner chat": 0}}
        with mock.patch.object(ti, "fetch_updates", return_value=upds), \
             mock.patch.object(ti, "_call", return_value={"ok": True}), \
             mock.patch.object(ti, "send_text") as mock_send:
            handled = ti.process_updates("fake_token", OWNER_CHAT_ID, db_path=self.db, stats=stats)
            self.assertEqual(handled, 1)
            mock_send.assert_called_once()
            call_text = mock_send.call_args[0][2]
            self.assertIn("Owner Assets (1 items)", call_text)
            self.assertIn("test.png", call_text)

    def test_69_format_size_helper(self):
        self.assertEqual(tg_assets._format_size(500), "500 B")
        self.assertEqual(tg_assets._format_size(2048), "2.0 KB")
        self.assertEqual(tg_assets._format_size(1024 * 1024), "1.0 MB")
        self.assertEqual(tg_assets._format_size(1572864), "1.5 MB")

    def test_70_delete_prompt_out_of_bounds(self):
        ctx = self._make_ctx()
        res = tg_assets.handle("/asset delete 99", ctx)
        self.assertIn("kanipinchaledu", res["text"])

    def test_71_photo_without_caption_saves_as_photo(self):
        self.fake_remote_files["f_nocap"] = SAMPLE_PNG
        msg = {
            "chat": {"id": OWNER_CHAT_ID},
            "photo": [{"file_id": "f_nocap", "file_size": len(SAMPLE_PNG)}],
        }
        ctx = self._make_ctx(message=msg)
        res = tg_assets.handle("", ctx)
        self.assertIsNotNone(res)
        self.assertIn("PHOTO", res["text"])
        self.assertEqual(owner_assets.count_assets(self.db, kind="photo"), 1)

    def test_72_duplicate_upload_updates_metadata_and_notes_duplicate(self):
        self.fake_remote_files["f_dup"] = SAMPLE_PNG
        msg1 = {
            "chat": {"id": OWNER_CHAT_ID},
            "photo": [{"file_id": "f_dup", "file_size": len(SAMPLE_PNG)}],
            "caption": "first upload photo",
        }
        res1 = tg_assets.handle("first upload photo", self._make_ctx(message=msg1))
        self.assertNotIn("Duplicate asset", res1["text"])

        msg2 = {
            "chat": {"id": OWNER_CHAT_ID},
            "photo": [{"file_id": "f_dup", "file_size": len(SAMPLE_PNG)}],
            "caption": "idi logo",
        }
        res2 = tg_assets.handle("idi logo", self._make_ctx(message=msg2))
        self.assertIn("Duplicate asset", res2["text"])
        self.assertEqual(owner_assets.count_assets(self.db), 1)
        logo = owner_assets.get_logo(self.db)
        self.assertIsNotNone(logo)
        self.assertEqual(logo["kind"], "logo")

    def test_73_delete_non_existent_pending_id(self):
        self.state_store[tg_assets.STATE_PENDING_DELETE] = "999"
        ctx = self._make_ctx()
        res = tg_assets.handle("Yes", ctx)
        self.assertIn("kanipinchaledu", res["text"])

    def test_74_no_money_amounts_in_tg_assets(self):
        # Verify no currency or money symbols in help or code strings
        for cmd in tg_assets.COMMANDS:
            for text in (cmd["help_en"], cmd["help_te"]):
                self.assertNotIn("$", text)
                self.assertNotIn("₹", text)
                self.assertNotIn("USD", text)
                self.assertNotIn("INR", text)
                self.assertNotIn("dollar", text.lower())
                self.assertNotIn("rupee", text.lower())

    def test_75_no_money_amounts_in_owner_assets(self):
        with open(owner_assets.__file__, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("$", src)
        self.assertNotIn("₹", src)
        self.assertNotIn("USD", src)
        self.assertNotIn("INR", src)
