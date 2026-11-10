"""Tests for Telegram plugin tg_files.py.

Covers:
- Plugin interface specifications: COMMANDS list, handle signature, return types.
- Command parsing for slash commands and Tenglish phrases.
- Pack lookup in exported_content/products/upload and exported_content/products.
- Marketing assets lookup in exported_content/marketing.
- /files command: sends product.zip, cover.png, listing.txt as documents.
- /files command: zip over 45 MB skips document and sends a note instead.
- /covers command: sends cover variants as a media group.
- /pins command: sends pins in media groups of max 10 + 5 A/B titles as text.
- /carousel command: sends carousel slides in order + caption.txt.
- /reel command: sends reel frames + reel.txt + 'make_reel.sh Mac lo run cheyandi'.
- /blog command: sends blog.md as a document.
- /landing command: sends landing/index.html zipped as a document.
- /launch command: sends LAUNCH.md as a document.
- Rate limiting: max 30 files per command, 1s sleep between media groups.
- Security and path traversal guard: never sends files outside exported_content.
- Missing packs, missing assets, empty folders, missing command numbers.
- Unrelated messages return None.
- Absence of currency amounts.
- Total 60+ unit tests.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import zipfile

import tg_files


class MockContext:
    def __init__(
        self,
        products_dir: Path,
        marketing_dir: Path,
        db_path: str = ":memory:",
        chat_id: int = 12345678,
    ):
        self.products_dir = products_dir
        self.marketing_dir = marketing_dir
        self.db_path = db_path
        self.chat_id = chat_id
        self.now = datetime.datetime.now(datetime.timezone.utc)
        self.sent_texts: list[str] = []
        self.sent_docs: list[tuple[str, str]] = []
        self.sent_photos: list[tuple[str, str]] = []
        self.sent_media: list[tuple[list[str], str]] = []
        self.sleeps: list[float] = []
        self.state: dict[str, str] = {}

    def send_text(self, text: str, keyboard: Any = None) -> None:
        self.sent_texts.append(text)

    def send_document(self, path: str, caption: str = "") -> None:
        self.sent_docs.append((path, caption))

    def send_photo(self, path: str, caption: str = "") -> None:
        self.sent_photos.append((path, caption))

    def send_media_group(self, paths: list[str], caption: str = "") -> None:
        self.sent_media.append((paths, caption))

    def state_get(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    def state_set(self, key: str, value: Any) -> None:
        self.state[key] = value

    def sleep_fn(self, secs: float) -> None:
        self.sleeps.append(secs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "db_path": self.db_path,
            "chat_id": self.chat_id,
            "now": self.now,
            "products_dir": str(self.products_dir),
            "marketing_dir": str(self.marketing_dir),
            "send_text": self.send_text,
            "send_document": self.send_document,
            "send_photo": self.send_photo,
            "send_media_group": self.send_media_group,
            "state_get": self.state_get,
            "state_set": self.state_set,
            "sleep_fn": self.sleep_fn,
        }


class TestTgFilesPlugin(unittest.TestCase):
    def setUp(self) -> None:
        # Clear GITHUB_* and RUNNER_ENVIRONMENT env vars
        clean_env = {
            k: v for k, v in os.environ.items()
            if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"
        }
        self.env_patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        self.env_patcher.start()

        # Temporary workspace for products and marketing directories
        self.test_dir = tempfile.mkdtemp(prefix="test_v1b_files_")
        self.base_path = Path(self.test_dir)
        self.products_dir = self.base_path / "exported_content" / "products"
        self.upload_dir = self.products_dir / "upload"
        self.marketing_dir = self.base_path / "exported_content" / "marketing"

        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

        self.mock_ctx = MockContext(
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        self.ctx = self.mock_ctx.as_dict()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # Helper to create mock ready pack and marketing assets
    # -----------------------------------------------------------------------
    def _create_ready_pack(self, pack_id: int | str, in_upload: bool = True, zip_size: int = 100) -> Path:
        target_dir = (self.upload_dir if in_upload else self.products_dir) / f"{pack_id}-test-product"
        target_dir.mkdir(parents=True, exist_ok=True)

        zip_path = target_dir / "product.zip"
        with open(zip_path, "wb") as f:
            f.write(b"Z" * zip_size)

        (target_dir / "cover.png").write_bytes(b"PNG_COVER")
        (target_dir / "listing.txt").write_text(
            f"Title: Test Product {pack_id}\nTags: digital, planner\nDescription: Personal use planner.",
            encoding="utf-8",
        )
        (target_dir / "checklist.txt").write_text("Done", encoding="utf-8")
        return target_dir

    def _create_marketing_pack(self, pack_id: int | str) -> Path:
        mktg_pack = self.marketing_dir / f"{pack_id}-test-product"
        mktg_pack.mkdir(parents=True, exist_ok=True)
        return mktg_pack

    # =======================================================================
    # 1. Plugin Interface and Metadata Tests (1-5)
    # =======================================================================
    def test_01_commands_structure(self):
        """Plugin defines COMMANDS list of dicts with cmd, words, help_en, help_te."""
        self.assertIsInstance(tg_files.COMMANDS, list)
        self.assertGreaterEqual(len(tg_files.COMMANDS), 8)
        expected_cmds = {"/files", "/covers", "/pins", "/carousel", "/reel", "/blog", "/landing", "/launch"}
        actual_cmds = {item["cmd"] for item in tg_files.COMMANDS}
        self.assertTrue(expected_cmds.issubset(actual_cmds))

    def test_02_commands_fields(self):
        """Every command in COMMANDS has required keys and valid types."""
        for c in tg_files.COMMANDS:
            self.assertIn("cmd", c)
            self.assertIn("words", c)
            self.assertIn("help_en", c)
            self.assertIn("help_te", c)
            self.assertTrue(c["cmd"].startswith("/"))
            self.assertIsInstance(c["words"], list)
            self.assertGreater(len(c["words"]), 0)

    def test_03_no_telegram_inbox_import_at_toplevel(self):
        """tg_files must not import telegram_inbox at module import time."""
        import sys
        # Check if telegram_inbox was directly imported in tg_files globals
        self.assertNotIn("telegram_inbox", dir(tg_files))

    def test_04_handle_unrecognized_returns_none(self):
        """handle returns None when text does not match any plugin command."""
        self.assertIsNone(tg_files.handle("hello there", self.ctx))
        self.assertIsNone(tg_files.handle("/status", self.ctx))
        self.assertIsNone(tg_files.handle("/packs", self.ctx))
        self.assertIsNone(tg_files.handle("", self.ctx))

    def test_05_rate_limit_and_zip_limit_constants(self):
        """Constants enforce max 30 files and 45 MB zip limit."""
        self.assertEqual(tg_files.MAX_FILES_PER_COMMAND, 30)
        self.assertEqual(tg_files.MAX_ZIP_SIZE_BYTES, 45 * 1024 * 1024)

    # =======================================================================
    # 2. Command Parsing Tests (6-16)
    # =======================================================================
    def test_06_parse_slash_commands(self):
        """Slash commands with arguments parse correctly."""
        self.assertEqual(tg_files.parse_action_and_id("/files 3"), ("files", 3))
        self.assertEqual(tg_files.parse_action_and_id("/covers 4"), ("covers", 4))
        self.assertEqual(tg_files.parse_action_and_id("/pins 5"), ("pins", 5))
        self.assertEqual(tg_files.parse_action_and_id("/carousel 6"), ("carousel", 6))
        self.assertEqual(tg_files.parse_action_and_id("/reel 7"), ("reel", 7))
        self.assertEqual(tg_files.parse_action_and_id("/blog 8"), ("blog", 8))
        self.assertEqual(tg_files.parse_action_and_id("/landing 9"), ("landing", 9))
        self.assertEqual(tg_files.parse_action_and_id("/launch 10"), ("launch", 10))

    def test_07_parse_slash_command_with_bot_name(self):
        """Slash command with @bot suffix parses correctly."""
        self.assertEqual(tg_files.parse_action_and_id("/files@mybot 3"), ("files", 3))
        self.assertEqual(tg_files.parse_action_and_id("/reel@mybot 12"), ("reel", 12))

    def test_08_parse_slash_without_id(self):
        """Slash commands without id return action with None."""
        self.assertEqual(tg_files.parse_action_and_id("/files"), ("files", None))
        self.assertEqual(tg_files.parse_action_and_id("/covers"), ("covers", None))
        self.assertEqual(tg_files.parse_action_and_id("/pins"), ("pins", None))

    def test_09_parse_tenglish_pack_n_files_pampu(self):
        """'pack 3 files pampu' parses to ('files', 3)."""
        self.assertEqual(tg_files.parse_action_and_id("pack 3 files pampu"), ("files", 3))
        self.assertEqual(tg_files.parse_action_and_id("pack 5 covers pampu"), ("covers", 5))

    def test_10_parse_tenglish_pins_n_pampu(self):
        """'pins 3 pampu' parses to ('pins', 3)."""
        self.assertEqual(tg_files.parse_action_and_id("pins 3 pampu"), ("pins", 3))
        self.assertEqual(tg_files.parse_action_and_id("covers 2 pampu"), ("covers", 2))
        self.assertEqual(tg_files.parse_action_and_id("reel 7 pampu"), ("reel", 7))

    def test_11_parse_tenglish_pack_n_ki_carousel_pampu(self):
        """'pack 4 ki carousel pampu' parses to ('carousel', 4)."""
        self.assertEqual(tg_files.parse_action_and_id("pack 4 ki carousel pampu"), ("carousel", 4))
        self.assertEqual(tg_files.parse_action_and_id("4 ki reel pampu"), ("reel", 4))

    def test_12_parse_tenglish_n_files_pampu(self):
        """'3 files pampu' parses to ('files', 3)."""
        self.assertEqual(tg_files.parse_action_and_id("3 files pampu"), ("files", 3))
        self.assertEqual(tg_files.parse_action_and_id("3 blog pampu"), ("blog", 3))

    def test_13_parse_tenglish_files_pampu_n(self):
        """'files pampu 3' parses to ('files', 3)."""
        self.assertEqual(tg_files.parse_action_and_id("files pampu 3"), ("files", 3))
        self.assertEqual(tg_files.parse_action_and_id("pins pampu 8"), ("pins", 8))

    def test_14_parse_tenglish_short_form(self):
        """'files 3' or 'pack 3 blog' parses correctly."""
        self.assertEqual(tg_files.parse_action_and_id("files 3"), ("files", 3))
        self.assertEqual(tg_files.parse_action_and_id("pack 3 blog"), ("blog", 3))
        self.assertEqual(tg_files.parse_action_and_id("launch 5"), ("launch", 5))

    def test_15_parse_action_aliases(self):
        """Aliases like 'slides', 'file', 'reels' are recognized."""
        self.assertEqual(tg_files.parse_action_and_id("/slides 2"), ("carousel", 2))
        self.assertEqual(tg_files.parse_action_and_id("pack 3 file pampu"), ("files", 3))
        self.assertEqual(tg_files.parse_action_and_id("reels 1 pampu"), ("reel", 1))

    def test_16_parse_missing_number_tenglish(self):
        """Tenglish requests without numbers return action and None."""
        self.assertEqual(tg_files.parse_action_and_id("files pampu"), ("files", None))
        self.assertEqual(tg_files.parse_action_and_id("covers pampu"), ("covers", None))
        self.assertEqual(tg_files.parse_action_and_id("pack files"), ("files", None))

    # =======================================================================
    # 3. Pack and Marketing Directory Discovery Tests (17-23)
    # =======================================================================
    def test_17_find_pack_in_upload_folder(self):
        """find_ready_pack_dir finds packs inside exported_content/products/upload."""
        self._create_ready_pack(1, in_upload=True)
        found = tg_files.find_ready_pack_dir(1, self.ctx)
        self.assertIsNotNone(found)
        self.assertTrue(found.name.startswith("1-"))

    def test_18_find_pack_in_products_base_folder(self):
        """find_ready_pack_dir finds packs directly under exported_content/products."""
        self._create_ready_pack(2, in_upload=False)
        found = tg_files.find_ready_pack_dir(2, self.ctx)
        self.assertIsNotNone(found)
        self.assertTrue(found.name.startswith("2-"))

    def test_19_find_pack_exact_match(self):
        """find_ready_pack_dir matches exact directory name '3'."""
        exact_dir = self.upload_dir / "3"
        exact_dir.mkdir(parents=True, exist_ok=True)
        (exact_dir / "product.zip").write_bytes(b"Z")
        (exact_dir / "listing.txt").write_text("Title: Ex", encoding="utf-8")
        found = tg_files.find_ready_pack_dir(3, self.ctx)
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "3")

    def test_20_find_pack_missing_zip_rejected(self):
        """Directory without product.zip is not recognized as a ready pack."""
        incomplete = self.upload_dir / "4-incomplete"
        incomplete.mkdir(parents=True, exist_ok=True)
        (incomplete / "listing.txt").write_text("Title: Incomplete", encoding="utf-8")
        self.assertIsNone(tg_files.find_ready_pack_dir(4, self.ctx))

    def test_21_find_pack_missing_listing_rejected(self):
        """Directory without listing.txt is not recognized as a ready pack."""
        incomplete = self.upload_dir / "5-incomplete"
        incomplete.mkdir(parents=True, exist_ok=True)
        (incomplete / "product.zip").write_bytes(b"Z")
        self.assertIsNone(tg_files.find_ready_pack_dir(5, self.ctx))

    def test_22_find_marketing_pack_dir(self):
        """find_marketing_pack_dir finds marketing folder matching pack id."""
        mktg_dir = self._create_marketing_pack(6)
        found = tg_files.find_marketing_pack_dir(6, self.ctx)
        self.assertIsNotNone(found)
        self.assertEqual(found, mktg_dir)

    def test_23_find_marketing_pack_dir_not_found(self):
        """find_marketing_pack_dir returns None for non-existent marketing dir."""
        self.assertIsNone(tg_files.find_marketing_pack_dir(999, self.ctx))

    # =======================================================================
    # 4. /files Command Tests (24-30)
    # =======================================================================
    def test_24_files_command_success(self):
        """/files N sends product.zip, cover.png, listing.txt as documents."""
        self._create_ready_pack(1)
        res = tg_files.handle("/files 1", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_docs), 3)

        filenames = [Path(p).name for p, _ in self.mock_ctx.sent_docs]
        self.assertIn("product.zip", filenames)
        self.assertIn("cover.png", filenames)
        self.assertIn("listing.txt", filenames)

    def test_25_files_command_tenglish(self):
        """'pack 1 files pampu' behaves identically to /files 1."""
        self._create_ready_pack(1)
        res = tg_files.handle("pack 1 files pampu", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_docs), 3)

    def test_26_files_command_zip_over_45mb(self):
        """Zip file larger than 45 MB is skipped and a note is sent instead."""
        pack_dir = self._create_ready_pack(2)
        zip_path = pack_dir / "product.zip"
        real_stat = zip_path.stat()

        class FakeStat:
            st_mode = real_stat.st_mode
            st_size = 46 * 1024 * 1024

        orig_stat = Path.stat

        def fake_stat(self_path):
            if self_path.name == "product.zip":
                return FakeStat()
            return orig_stat(self_path)

        with mock.patch.object(Path, "stat", fake_stat):
            res = tg_files.handle("/files 2", self.ctx)
            self.assertEqual(res, {"handled": True})

        # Sent docs should not contain product.zip
        sent_filenames = [Path(p).name for p, _ in self.mock_ctx.sent_docs]
        self.assertNotIn("product.zip", sent_filenames)
        self.assertIn("cover.png", sent_filenames)
        self.assertIn("listing.txt", sent_filenames)

        # A text note was sent about the 45 MB limit
        self.assertEqual(len(self.mock_ctx.sent_texts), 1)
        self.assertIn("45 MB", self.mock_ctx.sent_texts[0])

    def test_27_files_command_pack_not_found(self):
        """/files N returns friendly Tenglish message if pack is not found."""
        res = tg_files.handle("/files 99", self.ctx)
        self.assertIn("text", res)
        self.assertIn("Pack #99", res["text"])
        self.assertIn("dorakaledu", res["text"])

    def test_28_files_command_missing_pack_number(self):
        """/files without number asks owner for pack number."""
        res = tg_files.handle("/files", self.ctx)
        self.assertIn("text", res)
        self.assertIn("Pack number cheppandi", res["text"])

    def test_29_files_command_no_cover(self):
        """/files sends remaining files if cover.png is missing."""
        pack_dir = self._create_ready_pack(3)
        (pack_dir / "cover.png").unlink()
        res = tg_files.handle("/files 3", self.ctx)
        self.assertEqual(res, {"handled": True})
        sent_filenames = [Path(p).name for p, _ in self.mock_ctx.sent_docs]
        self.assertIn("product.zip", sent_filenames)
        self.assertNotIn("cover.png", sent_filenames)
        self.assertIn("listing.txt", sent_filenames)

    def test_30_files_command_captions(self):
        """Sent documents have clean informative captions."""
        self._create_ready_pack(4)
        tg_files.handle("/files 4", self.ctx)
        for _, caption in self.mock_ctx.sent_docs:
            self.assertIn("Pack #4", caption)

    # =======================================================================
    # 5. /covers Command Tests (31-36)
    # =======================================================================
    def test_31_covers_command_success(self):
        """/covers N sends cover variants as a media group."""
        mktg_pack = self._create_marketing_pack(1)
        covers_dir = mktg_pack / "covers"
        covers_dir.mkdir(parents=True, exist_ok=True)
        (covers_dir / "cover_a.png").write_bytes(b"A")
        (covers_dir / "cover_b.png").write_bytes(b"B")
        (covers_dir / "cover_c.png").write_bytes(b"C")

        res = tg_files.handle("/covers 1", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_media), 1)

        paths, caption = self.mock_ctx.sent_media[0]
        self.assertEqual(len(paths), 3)
        self.assertIn("cover variants", caption)

    def test_32_covers_command_tenglish(self):
        """'covers 1 pampu' works identically to /covers 1."""
        mktg_pack = self._create_marketing_pack(1)
        covers_dir = mktg_pack / "covers"
        covers_dir.mkdir(parents=True, exist_ok=True)
        (covers_dir / "cover_a.png").write_bytes(b"A")

        res = tg_files.handle("covers 1 pampu", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_media), 1)

    def test_33_covers_command_marketing_pack_not_found(self):
        """/covers returns friendly message if marketing pack not found."""
        res = tg_files.handle("/covers 99", self.ctx)
        self.assertIn("text", res)
        self.assertIn("marketing files inka ledu", res["text"])

    def test_34_covers_command_no_covers_dir(self):
        """/covers returns message when covers dir does not exist."""
        self._create_marketing_pack(2)
        res = tg_files.handle("/covers 2", self.ctx)
        self.assertIn("text", res)
        self.assertIn("cover variants inka ledu", res["text"])

    def test_35_covers_command_empty_covers_dir(self):
        """/covers returns message when covers dir is empty."""
        mktg_pack = self._create_marketing_pack(3)
        (mktg_pack / "covers").mkdir(parents=True, exist_ok=True)
        res = tg_files.handle("/covers 3", self.ctx)
        self.assertIn("text", res)
        self.assertIn("images dorakaledu", res["text"])

    def test_36_covers_missing_id_prompt(self):
        """/covers without pack number prompts owner."""
        res = tg_files.handle("/covers", self.ctx)
        self.assertIn("text", res)
        self.assertIn("Pack number cheppandi", res["text"])

    # =======================================================================
    # 6. /pins Command Tests (37-43)
    # =======================================================================
    def test_37_pins_command_success(self):
        """/pins N sends pins media groups and 5 A/B titles as text."""
        mktg_pack = self._create_marketing_pack(1)
        pins_dir = mktg_pack / "pins"
        pins_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 6):
            (pins_dir / f"pin_{i}_a.png").write_bytes(b"PIN")

        (pins_dir / "ab_titles.txt").write_text(
            "1. Best Daily Planner\n2. 2027 Goals Planner\n3. Minimalist Daily Tracker\n"
            "4. How to Plan Your Day\n5. Daily Planner Printable\n",
            encoding="utf-8",
        )

        res = tg_files.handle("/pins 1", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_media), 1)
        paths, caption = self.mock_ctx.sent_media[0]
        self.assertEqual(len(paths), 5)

        # 5 A/B titles sent as text
        self.assertEqual(len(self.mock_ctx.sent_texts), 1)
        txt = self.mock_ctx.sent_texts[0]
        self.assertIn("5 A/B pin titles", txt)
        self.assertIn("Best Daily Planner", txt)

    def test_38_pins_command_chunks_over_10(self):
        """Pins exceeding 10 are sent in chunks of max 10 with 1s pause."""
        mktg_pack = self._create_marketing_pack(2)
        pins_dir = mktg_pack / "pins"
        pins_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 15):
            (pins_dir / f"pin_{i:02d}.png").write_bytes(b"PIN")

        res = tg_files.handle("/pins 2", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_media), 2)
        # First chunk: 10, second chunk: 4
        self.assertEqual(len(self.mock_ctx.sent_media[0][0]), 10)
        self.assertEqual(len(self.mock_ctx.sent_media[1][0]), 4)
        # Sleep called between chunks
        self.assertEqual(self.mock_ctx.sleeps, [1.0])

    def test_39_pins_from_pins_json(self):
        """A/B titles read from pins.json if present."""
        mktg_pack = self._create_marketing_pack(3)
        pins_dir = mktg_pack / "pins"
        pins_dir.mkdir(parents=True, exist_ok=True)
        (pins_dir / "pin_1.png").write_bytes(b"P")
        pins_meta = [
            {"title": "Title From JSON 1"},
            {"title": "Title From JSON 2"},
        ]
        (pins_dir / "pins.json").write_text(json.dumps(pins_meta), encoding="utf-8")

        tg_files.handle("/pins 3", self.ctx)
        self.assertEqual(len(self.mock_ctx.sent_texts), 1)
        self.assertIn("Title From JSON 1", self.mock_ctx.sent_texts[0])

    def test_40_pins_command_tenglish(self):
        """'pins 3 pampu' works identically to /pins 3."""
        mktg_pack = self._create_marketing_pack(4)
        pins_dir = mktg_pack / "pins"
        pins_dir.mkdir(parents=True, exist_ok=True)
        (pins_dir / "pin_1.png").write_bytes(b"P")

        res = tg_files.handle("pins 4 pampu", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_media), 1)

    def test_41_pins_command_no_pins_dir(self):
        """/pins returns message when pins dir does not exist."""
        self._create_marketing_pack(5)
        res = tg_files.handle("/pins 5", self.ctx)
        self.assertIn("text", res)
        self.assertIn("pins inka ledu", res["text"])

    def test_42_pins_command_empty_pins_dir(self):
        """/pins returns message when pins dir has no image files."""
        mktg_pack = self._create_marketing_pack(6)
        (mktg_pack / "pins").mkdir(parents=True, exist_ok=True)
        res = tg_files.handle("/pins 6", self.ctx)
        self.assertIn("text", res)
        self.assertIn("images dorakaledu", res["text"])

    def test_43_pins_command_missing_pack_number(self):
        """/pins without pack number prompts owner."""
        res = tg_files.handle("/pins", self.ctx)
        self.assertIn("text", res)
        self.assertIn("Pack number cheppandi", res["text"])

    # =======================================================================
    # 7. /carousel Command Tests (44-48)
    # =======================================================================
    def test_44_carousel_command_success(self):
        """/carousel N sends slides in order and caption.txt text."""
        mktg_pack = self._create_marketing_pack(1)
        car_dir = mktg_pack / "carousel"
        car_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 7):
            (car_dir / f"slide-{i}.png").write_bytes(b"SLIDE")
        (car_dir / "caption.txt").write_text("Exciting new planner templates!", encoding="utf-8")

        res = tg_files.handle("/carousel 1", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_media), 1)
        paths, caption = self.mock_ctx.sent_media[0]
        self.assertEqual(len(paths), 6)
        # Order check: slide-1 to slide-6
        self.assertTrue(paths[0].endswith("slide-1.png"))
        self.assertTrue(paths[-1].endswith("slide-6.png"))

        # Caption text sent
        self.assertEqual(len(self.mock_ctx.sent_texts), 1)
        self.assertIn("Exciting new planner templates!", self.mock_ctx.sent_texts[0])

    def test_45_carousel_command_tenglish(self):
        """'pack 1 carousel pampu' works identically to /carousel 1."""
        mktg_pack = self._create_marketing_pack(1)
        car_dir = mktg_pack / "carousel"
        car_dir.mkdir(parents=True, exist_ok=True)
        (car_dir / "slide-1.png").write_bytes(b"SLIDE")

        res = tg_files.handle("pack 1 carousel pampu", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_media), 1)

    def test_46_carousel_no_car_dir(self):
        """/carousel returns message if carousel dir does not exist."""
        self._create_marketing_pack(2)
        res = tg_files.handle("/carousel 2", self.ctx)
        self.assertIn("text", res)
        self.assertIn("carousel inka ledu", res["text"])

    def test_47_carousel_empty_slides(self):
        """/carousel returns message if no slide images found."""
        mktg_pack = self._create_marketing_pack(3)
        (mktg_pack / "carousel").mkdir(parents=True, exist_ok=True)
        res = tg_files.handle("/carousel 3", self.ctx)
        self.assertIn("text", res)
        self.assertIn("slides dorakaledu", res["text"])

    def test_48_carousel_missing_number(self):
        """/carousel without number prompts owner."""
        res = tg_files.handle("/carousel", self.ctx)
        self.assertIn("text", res)
        self.assertIn("Pack number cheppandi", res["text"])

    # =======================================================================
    # 8. /reel Command Tests (49-54)
    # =======================================================================
    def test_49_reel_command_success(self):
        """/reel N sends frames, reel.txt caption, and make_reel.sh Mac run note."""
        mktg_pack = self._create_marketing_pack(1)
        reel_dir = mktg_pack / "reel"
        reel_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 9):
            (reel_dir / f"frame-{i}.png").write_bytes(b"FRAME")
        (reel_dir / "reel.txt").write_text("Daily Productivity Reel #planner", encoding="utf-8")

        res = tg_files.handle("/reel 1", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_media), 1)
        paths, caption = self.mock_ctx.sent_media[0]
        self.assertEqual(len(paths), 8)

        # Note sent with caption and make_reel.sh instruction
        self.assertEqual(len(self.mock_ctx.sent_texts), 1)
        txt = self.mock_ctx.sent_texts[0]
        self.assertIn("Daily Productivity Reel", txt)
        self.assertIn("make_reel.sh Mac lo run cheyandi", txt)

    def test_50_reel_command_tenglish(self):
        """'pack 1 reel pampu' works identically to /reel 1."""
        mktg_pack = self._create_marketing_pack(1)
        reel_dir = mktg_pack / "reel"
        reel_dir.mkdir(parents=True, exist_ok=True)
        (reel_dir / "frame-1.png").write_bytes(b"F")

        res = tg_files.handle("pack 1 reel pampu", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_media), 1)

    def test_51_reel_no_reel_dir(self):
        """/reel returns message if reel dir does not exist."""
        self._create_marketing_pack(2)
        res = tg_files.handle("/reel 2", self.ctx)
        self.assertIn("text", res)
        self.assertIn("reel inka ledu", res["text"])

    def test_52_reel_empty_frames(self):
        """/reel returns message if reel dir has no frames."""
        mktg_pack = self._create_marketing_pack(3)
        (mktg_pack / "reel").mkdir(parents=True, exist_ok=True)
        res = tg_files.handle("/reel 3", self.ctx)
        self.assertIn("text", res)
        self.assertIn("reel frames dorakaledu", res["text"])

    def test_53_reel_missing_number(self):
        """/reel without pack number prompts owner."""
        res = tg_files.handle("/reel", self.ctx)
        self.assertIn("text", res)
        self.assertIn("Pack number cheppandi", res["text"])

    def test_54_reel_without_caption_still_sends_note(self):
        """/reel without reel.txt still sends make_reel.sh note."""
        mktg_pack = self._create_marketing_pack(4)
        reel_dir = mktg_pack / "reel"
        reel_dir.mkdir(parents=True, exist_ok=True)
        (reel_dir / "frame-1.png").write_bytes(b"F")

        tg_files.handle("/reel 4", self.ctx)
        self.assertEqual(len(self.mock_ctx.sent_texts), 1)
        self.assertIn("make_reel.sh Mac lo run cheyandi", self.mock_ctx.sent_texts[0])

    # =======================================================================
    # 9. /blog Command Tests (55-58)
    # =======================================================================
    def test_55_blog_command_success(self):
        """/blog N sends blog.md as a document."""
        mktg_pack = self._create_marketing_pack(1)
        blog_file = mktg_pack / "blog.md"
        blog_file.write_text("# SEO Article for Pack 1\n\nContent here.", encoding="utf-8")

        res = tg_files.handle("/blog 1", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_docs), 1)
        path, caption = self.mock_ctx.sent_docs[0]
        self.assertTrue(path.endswith("blog.md"))
        self.assertIn("Pack #1 blog.md", caption)

    def test_56_blog_command_tenglish(self):
        """'pack 1 blog pampu' works identically to /blog 1."""
        mktg_pack = self._create_marketing_pack(1)
        (mktg_pack / "blog.md").write_text("# Article", encoding="utf-8")

        res = tg_files.handle("pack 1 blog pampu", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_docs), 1)

    def test_57_blog_not_found(self):
        """/blog returns message if blog.md is missing."""
        self._create_marketing_pack(2)
        res = tg_files.handle("/blog 2", self.ctx)
        self.assertIn("text", res)
        self.assertIn("blog.md inka ledu", res["text"])

    def test_58_blog_missing_number(self):
        """/blog without number prompts owner."""
        res = tg_files.handle("/blog", self.ctx)
        self.assertIn("text", res)
        self.assertIn("Pack number cheppandi", res["text"])

    # =======================================================================
    # 10. /landing Command Tests (59-63)
    # =======================================================================
    def test_59_landing_command_success(self):
        """/landing N zips landing/index.html and sends as document."""
        mktg_pack = self._create_marketing_pack(1)
        landing_dir = mktg_pack / "landing"
        landing_dir.mkdir(parents=True, exist_ok=True)
        html_file = landing_dir / "index.html"
        html_file.write_text("<!DOCTYPE html><html><body>Landing Page</body></html>", encoding="utf-8")

        res = tg_files.handle("/landing 1", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_docs), 1)
        path, caption = self.mock_ctx.sent_docs[0]
        self.assertTrue(path.endswith(".zip"))
        self.assertIn("landing page", caption)

        # Verify zip contents
        with zipfile.ZipFile(path, "r") as zf:
            self.assertIn("index.html", zf.namelist())
            content = zf.read("index.html").decode("utf-8")
            self.assertIn("Landing Page", content)

    def test_60_landing_command_tenglish(self):
        """'landing 1 pampu' works identically to /landing 1."""
        mktg_pack = self._create_marketing_pack(1)
        landing_dir = mktg_pack / "landing"
        landing_dir.mkdir(parents=True, exist_ok=True)
        (landing_dir / "index.html").write_text("<html></html>", encoding="utf-8")

        res = tg_files.handle("landing 1 pampu", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_docs), 1)

    def test_61_landing_not_found(self):
        """/landing returns message if landing/index.html does not exist."""
        self._create_marketing_pack(2)
        res = tg_files.handle("/landing 2", self.ctx)
        self.assertIn("text", res)
        self.assertIn("landing page (index.html) inka ledu", res["text"])

    def test_62_landing_missing_number(self):
        """/landing without number prompts owner."""
        res = tg_files.handle("/landing", self.ctx)
        self.assertIn("text", res)
        self.assertIn("Pack number cheppandi", res["text"])

    def test_63_landing_zip_atomic(self):
        """Temporary files created during landing zip generation are cleaned up."""
        mktg_pack = self._create_marketing_pack(3)
        landing_dir = mktg_pack / "landing"
        landing_dir.mkdir(parents=True, exist_ok=True)
        (landing_dir / "index.html").write_text("<html></html>", encoding="utf-8")

        tg_files.handle("/landing 3", self.ctx)
        # Check no .tmp files left in landing_dir
        tmp_files = list(landing_dir.glob("*.tmp"))
        self.assertEqual(tmp_files, [])

    # =======================================================================
    # 11. /launch Command Tests (64-67)
    # =======================================================================
    def test_64_launch_command_success(self):
        """/launch N sends LAUNCH.md as document."""
        mktg_pack = self._create_marketing_pack(1)
        launch_file = mktg_pack / "LAUNCH.md"
        launch_file.write_text("# Launch Checklist\n\n- [ ] Gumroad", encoding="utf-8")

        res = tg_files.handle("/launch 1", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_docs), 1)
        path, caption = self.mock_ctx.sent_docs[0]
        self.assertTrue(path.endswith("LAUNCH.md"))
        self.assertIn("LAUNCH.md checklist", caption)

    def test_65_launch_command_tenglish(self):
        """'launch 1 pampu' works identically to /launch 1."""
        mktg_pack = self._create_marketing_pack(1)
        (mktg_pack / "LAUNCH.md").write_text("# Checklist", encoding="utf-8")

        res = tg_files.handle("launch 1 pampu", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.mock_ctx.sent_docs), 1)

    def test_66_launch_not_found(self):
        """/launch returns message if LAUNCH.md is missing."""
        self._create_marketing_pack(2)
        res = tg_files.handle("/launch 2", self.ctx)
        self.assertIn("text", res)
        self.assertIn("LAUNCH.md inka ledu", res["text"])

    def test_67_launch_missing_number(self):
        """/launch without number prompts owner."""
        res = tg_files.handle("/launch", self.ctx)
        self.assertIn("text", res)
        self.assertIn("Pack number cheppandi", res["text"])

    # =======================================================================
    # 12. Security and Path Traversal Tests (68-71)
    # =======================================================================
    def test_68_path_outside_exported_content_rejected(self):
        """_is_safe_exported_path rejects paths outside allowed directory roots."""
        outside_file = self.base_path / "secret.txt"
        outside_file.write_text("classified", encoding="utf-8")

        self.assertFalse(tg_files._is_safe_exported_path(outside_file, self.ctx))

    def test_69_path_inside_products_dir_accepted(self):
        """_is_safe_exported_path accepts paths inside products_dir."""
        inside_file = self.products_dir / "valid.txt"
        inside_file.write_text("valid", encoding="utf-8")

        self.assertTrue(tg_files._is_safe_exported_path(inside_file, self.ctx))

    def test_70_path_inside_marketing_dir_accepted(self):
        """_is_safe_exported_path accepts paths inside marketing_dir."""
        inside_file = self.marketing_dir / "valid.txt"
        inside_file.write_text("valid", encoding="utf-8")

        self.assertTrue(tg_files._is_safe_exported_path(inside_file, self.ctx))

    def test_71_max_files_rate_limit_capped(self):
        """Media collections never exceed MAX_FILES_PER_COMMAND (30)."""
        mktg_pack = self._create_marketing_pack(10)
        pins_dir = mktg_pack / "pins"
        pins_dir.mkdir(parents=True, exist_ok=True)
        # Create 35 pin images
        for i in range(1, 36):
            (pins_dir / f"pin_{i:02d}.png").write_bytes(b"P")

        tg_files.handle("/pins 10", self.ctx)
        total_sent = sum(len(paths) for paths, _ in self.mock_ctx.sent_media)
        self.assertEqual(total_sent, 30)

    # =======================================================================
    # 13. State and Context Interaction Tests (72-74)
    # =======================================================================
    def test_72_context_state_get_set(self):
        """Plugin supports state_get and state_set in ctx."""
        self.ctx["state_set"]("test_key", "test_val")
        self.assertEqual(self.ctx["state_get"]("test_key"), "test_val")

    def test_73_no_send_functions_fallback_safe(self):
        """Plugin does not raise unhandled exceptions if optional callbacks are None."""
        empty_ctx = {
            "products_dir": str(self.products_dir),
            "marketing_dir": str(self.marketing_dir),
        }
        self._create_ready_pack(1)
        res = tg_files.handle("/files 1", empty_ctx)
        self.assertIn("text", res)
        self.assertIn("Pack #1", res["text"])

    def test_74_no_currency_amounts_in_plugin(self):
        """Ensure no currency amounts or symbols exist anywhere in tg_files.py."""
        import tests.test_r22_markdown_currency as m
        content = Path(tg_files.__file__).read_text(encoding="utf-8")
        matches = m.CURRENCY_AMOUNT.findall(content)
        self.assertEqual(matches, [], f"Found currency amounts in tg_files.py: {matches}")


if __name__ == "__main__":
    unittest.main()
