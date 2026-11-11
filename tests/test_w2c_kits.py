"""tests/test_w2c_kits.py — Comprehensive unit tests for store_kits.py and tg_kits.py.

60+ tests covering:
- STORE_LIMITS dictionary completeness, types, and comments per store.
- Unicode and emoji-safe character counting (graphemes).
- Word-boundary trimming with emoji, punctuation, whitespace edge cases.
- Money detection and sanitization.
- Listing file parsing and section extraction.
- Deliverables and images order discovery.
- Tag formatting per store rules (Etsy 13 tags <= 20 chars, Gumroad <= 10 tags, etc.).
- store_kits.build(pack_dir) across all 6 stores:
  Gumroad, Payhip, Etsy, Ko-fi, Topmate, Instamojo.
- All store forms fields present in kit files.
- 'Price: owner decides' placeholder strictly present (no currency amounts anywhere).
- Click checklists in Tenglish house style.
- Atomic file writes and determinism.
- Missing listing.txt error handling.
- CLI argument parsing and error exits.
- Telegram plugin tg_kits.py:
  COMMANDS structure, words, help strings.
  No telegram_inbox import at module level.
  Command parsing for slash commands and Tenglish phrases.
  Defaulting to Gumroad, specific store selection.
  Document delivery and short message with first 3 fields.
  Missing pack and missing number handling.
  Path safety / confinement to exported_content.
  Currency / money absence check.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import store_kits
import tg_kits


class MockContext:
    def __init__(
        self,
        products_dir: Path,
        marketing_dir: Path,
        db_path: str = ":memory:",
        chat_id: int = int("1234" "5678"),
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
        }


class TestStoreKitsModule(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp(prefix="test_w2c_kits_")
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
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_pack(
        self,
        pack_id: int | str,
        title: str = "2027 ADHD Digital Life Planner",
        desc: str = "A comprehensive digital life planner for daily productivity and habits.",
        tags: str = "planner, adhd planner, daily tracker, habit journal, digital printable",
        what_you_get: str = "- 1 PDF Planner (300 pages)\n- 1 Habit Tracker Spreadsheet\n- Lifetime updates",
        extra_files: list[str] | None = None,
        extra_images: list[str] | None = None,
    ) -> Path:
        pack_path = self.upload_dir / f"{pack_id}-adhd-planner"
        pack_path.mkdir(parents=True, exist_ok=True)

        listing_content = (
            f"Title: {title}\n"
            f"Description: {desc}\n"
            f"Tags: {tags}\n"
            f"What you get:\n{what_you_get}\n"
        )
        (pack_path / "listing.txt").write_text(listing_content, encoding="utf-8")
        (pack_path / "product.zip").write_bytes(b"ZIPDATA")
        (pack_path / "cover.png").write_bytes(b"COVERPNG")

        if extra_files:
            for f in extra_files:
                (pack_path / f).write_text("file content", encoding="utf-8")

        if extra_images:
            for img in extra_images:
                (pack_path / img).write_bytes(b"IMGDATA")

        return pack_path

    # =======================================================================
    # 1. STORE_LIMITS Table Tests (1-8)
    # =======================================================================
    def test_01_store_limits_contains_all_six_stores(self):
        expected = {"gumroad", "payhip", "etsy", "kofi", "topmate", "instamojo"}
        self.assertEqual(set(store_kits.STORE_LIMITS.keys()), expected)
        self.assertEqual(set(store_kits.SUPPORTED_STORES), expected)

    def test_02_store_limits_required_keys(self):
        for store, cfg in store_kits.STORE_LIMITS.items():
            self.assertIn("store_name", cfg)
            self.assertIn("file_name", cfg)
            self.assertIn("title_limit", cfg)
            self.assertIn("short_desc_limit", cfg)
            self.assertIn("desc_limit", cfg)
            self.assertIn("max_tags", cfg)
            self.assertIn("tag_char_limit", cfg)
            self.assertIn("category", cfg)
            self.assertIn("price_placeholder", cfg)
            self.assertIn("click_checklist", cfg)

    def test_03_store_limits_comments_present_in_source(self):
        source = Path(store_kits.__file__).read_text(encoding="utf-8")
        self.assertIn("# Gumroad:", source)
        self.assertIn("# Payhip:", source)
        self.assertIn("# Etsy:", source)
        self.assertIn("# Ko-fi:", source)
        self.assertIn("# Topmate:", source)
        self.assertIn("# Instamojo:", source)

    def test_04_etsy_specific_limits(self):
        etsy_cfg = store_kits.STORE_LIMITS["etsy"]
        self.assertEqual(etsy_cfg["title_limit"], 140)
        self.assertEqual(etsy_cfg["max_tags"], 13)
        self.assertEqual(etsy_cfg["tag_char_limit"], 20)

    def test_05_gumroad_specific_limits(self):
        gum_cfg = store_kits.STORE_LIMITS["gumroad"]
        self.assertEqual(gum_cfg["title_limit"], 150)
        self.assertEqual(gum_cfg["max_tags"], 10)

    def test_06_topmate_specific_limits(self):
        top_cfg = store_kits.STORE_LIMITS["topmate"]
        self.assertEqual(top_cfg["title_limit"], 80)
        self.assertEqual(top_cfg["short_desc_limit"], 150)
        self.assertEqual(top_cfg["max_tags"], 5)

    def test_07_instamojo_specific_limits(self):
        insta_cfg = store_kits.STORE_LIMITS["instamojo"]
        self.assertEqual(insta_cfg["title_limit"], 80)
        self.assertEqual(insta_cfg["desc_limit"], 5000)

    def test_08_price_placeholders_have_no_money_amounts(self):
        for store, cfg in store_kits.STORE_LIMITS.items():
            placeholder = cfg["price_placeholder"]
            self.assertIn("owner decides", placeholder)
            self.assertIsNone(store_kits.check_money(placeholder))

    # =======================================================================
    # 2. Grapheme Clusters & Word Boundary Trimming Tests (9-18)
    # =======================================================================
    def test_09_count_graphemes_ascii(self):
        self.assertEqual(store_kits.count_graphemes("hello world"), 11)

    def test_10_count_graphemes_simple_emoji(self):
        # A string with emoji
        self.assertEqual(store_kits.count_graphemes("Planner 📅"), 9)

    def test_11_count_graphemes_zwj_sequences(self):
        # Woman technologist emoji: 👩 + ZWJ + 💻
        zwj_emoji = "👩‍💻"
        self.assertEqual(store_kits.count_graphemes(zwj_emoji), 1)

    def test_12_count_graphemes_combining_accents(self):
        # 'e' + combining acute accent
        accented = "e\u0301"
        self.assertEqual(store_kits.count_graphemes(accented), 1)

    def test_13_trim_to_word_boundary_under_limit(self):
        text = "Short Title"
        self.assertEqual(store_kits.trim_to_word_boundary(text, 50), "Short Title")

    def test_14_trim_to_word_boundary_normal_cut(self):
        text = "This is a very long title that needs to be cut cleanly at word boundary"
        res = store_kits.trim_to_word_boundary(text, 25)
        self.assertLessEqual(len(res), 25)
        self.assertEqual(res, "This is a very long")

    def test_15_trim_to_word_boundary_no_space(self):
        long_word = "Supercalifragilisticexpialidocious"
        res = store_kits.trim_to_word_boundary(long_word, 10)
        self.assertEqual(res, "Supercalif")

    def test_16_trim_to_word_boundary_with_emoji(self):
        text = "Awesome Planner 📅 For Smart Creators"
        res = store_kits.trim_to_word_boundary(text, 20)
        self.assertLessEqual(store_kits.count_graphemes(res), 20)
        self.assertTrue(res.endswith("📅") or res.endswith("Planner"))

    def test_17_trim_to_word_boundary_preserves_spaces_before_cut(self):
        text = "Word1 Word2 Word3 Word4"
        res = store_kits.trim_to_word_boundary(text, 14)
        self.assertEqual(res, "Word1 Word2")

    def test_18_trim_to_word_boundary_exact_match(self):
        text = "Exact 15 Chars!"
        self.assertEqual(store_kits.trim_to_word_boundary(text, 15), "Exact 15 Chars!")

    # =======================================================================
    # 3. Money Detection & Sanitization Tests (19-23)
    # =======================================================================
    def test_19_check_money_detects_dollar(self):
        self.assertIsNotNone(store_kits.check_money("Costs $15 only"))
        self.assertIsNotNone(store_kits.check_money("Just $9.99"))

    def test_20_check_money_detects_inr_rupee(self):
        self.assertIsNotNone(store_kits.check_money("Price: ₹499"))
        self.assertIsNotNone(store_kits.check_money("Pay Rs 250"))
        self.assertIsNotNone(store_kits.check_money("150 INR"))

    def test_21_check_money_returns_none_clean(self):
        self.assertIsNone(store_kits.check_money("Free digital template with lifetime updates"))
        self.assertIsNone(store_kits.check_money("Price: owner decides"))

    def test_22_sanitize_no_money_replaces_amounts(self):
        sanitized = store_kits.sanitize_no_money("Buy for $20 or ₹500 now!")
        self.assertNotIn("$20", sanitized)
        self.assertNotIn("₹500", sanitized)
        self.assertIn("[PRICE SET BY OWNER]", sanitized)

    def test_23_parse_listing_file_rejects_money(self):
        pack_dir = self.upload_dir / "money-pack"
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "listing.txt").write_text("Title: Paid\nDescription: $5 price.", encoding="utf-8")
        with self.assertRaises(ValueError):
            store_kits.parse_listing_file(pack_dir / "listing.txt")

    # =======================================================================
    # 4. Listing Parsing & Deliverable Ordering Tests (24-28)
    # =======================================================================
    def test_24_parse_listing_file_valid(self):
        pack = self._create_pack(1, title="Test Pack", desc="Desc here", tags="a, b", what_you_get="- Item")
        info = store_kits.parse_listing_file(pack / "listing.txt")
        self.assertEqual(info["title"], "Test Pack")
        self.assertEqual(info["description"], "Desc here")
        self.assertEqual(info["tags"], "a, b")
        self.assertEqual(info["what_you_get"], "- Item")

    def test_25_get_files_to_upload_order(self):
        pack = self._create_pack(2, extra_files=["LICENSE.txt", "bonus.pdf", "checklist.txt"])
        files = store_kits.get_files_to_upload(pack)
        self.assertEqual(files[0], "product.zip")
        self.assertIn("LICENSE.txt", files)
        self.assertIn("checklist.txt", files)
        self.assertIn("bonus.pdf", files)

    def test_26_get_images_to_upload_order(self):
        pack = self._create_pack(
            3,
            extra_images=["preview-1.png", "preview-2.png", "preview-10.png", "preview-3.png"]
        )
        images = store_kits.get_images_to_upload(pack)
        self.assertEqual(images[0], "cover.png")
        self.assertEqual(images[1], "preview-1.png")
        self.assertEqual(images[2], "preview-2.png")
        self.assertEqual(images[3], "preview-3.png")
        self.assertEqual(images[4], "preview-10.png")

    def test_27_format_tags_etsy_strict(self):
        raw = "planner, digital life planner for creators 2027, tracker, productivity, adhd"
        tags = store_kits.format_tags_for_store(raw, "etsy")
        self.assertLessEqual(len(tags), 13)
        for t in tags:
            self.assertLessEqual(len(t), 20)
            self.assertTrue(t.islower())

    def test_28_format_tags_kofi_max_5(self):
        raw = "one, two, three, four, five, six, seven"
        tags = store_kits.format_tags_for_store(raw, "kofi")
        self.assertEqual(len(tags), 5)

    # =======================================================================
    # 5. store_kits.build() Generation & Field Verification Tests (29-38)
    # =======================================================================
    def test_29_build_writes_all_six_store_kits(self):
        pack = self._create_pack(10)
        results = store_kits.build(pack)
        self.assertEqual(len(results), 6)
        kits_dir = pack / "store-kits"
        self.assertTrue(kits_dir.is_dir())
        for store, path in results.items():
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent, kits_dir)
            self.assertEqual(path.name, f"{store}.txt")

    def test_30_gumroad_kit_contents(self):
        pack = self._create_pack(11)
        results = store_kits.build(pack)
        content = results["gumroad"].read_text(encoding="utf-8")
        self.assertIn("=== GUMROAD LISTING KIT ===", content)
        self.assertIn("Title:", content)
        self.assertIn("Short Description:", content)
        self.assertIn("Description:", content)
        self.assertIn("Tags", content)
        self.assertIn("Category:", content)
        self.assertIn("Price: owner decides", content)
        self.assertIn("--- CHECKLIST OF CLICKS (TENGLISH) ---", content)
        self.assertIn("Gumroad Dashboard lo Products tab open cheyandi", content)

    def test_31_payhip_kit_contents(self):
        pack = self._create_pack(12)
        results = store_kits.build(pack)
        content = results["payhip"].read_text(encoding="utf-8")
        self.assertIn("=== PAYHIP LISTING KIT ===", content)
        self.assertIn("Payhip Dashboard lo Products", content)
        self.assertIn("Price: owner decides", content)

    def test_32_etsy_kit_contents(self):
        pack = self._create_pack(13)
        results = store_kits.build(pack)
        content = results["etsy"].read_text(encoding="utf-8")
        self.assertIn("=== ETSY LISTING KIT ===", content)
        self.assertIn("Etsy Shop Manager lo Listings", content)
        self.assertIn("13 tags", content)
        self.assertIn("Price: owner decides", content)

    def test_33_kofi_kit_contents(self):
        pack = self._create_pack(14)
        results = store_kits.build(pack)
        content = results["kofi"].read_text(encoding="utf-8")
        self.assertIn("=== KO-FI LISTING KIT ===", content)
        self.assertIn("Ko-fi menu lo 'Shop'", content)
        self.assertIn("Price: owner decides", content)

    def test_34_topmate_kit_contents(self):
        pack = self._create_pack(15)
        results = store_kits.build(pack)
        content = results["topmate"].read_text(encoding="utf-8")
        self.assertIn("=== TOPMATE LISTING KIT ===", content)
        self.assertIn("Topmate dashboard lo Services", content)
        self.assertIn("Price: owner decides", content)

    def test_35_instamojo_kit_contents(self):
        pack = self._create_pack(16)
        results = store_kits.build(pack)
        content = results["instamojo"].read_text(encoding="utf-8")
        self.assertIn("=== INSTAMOJO LISTING KIT ===", content)
        self.assertIn("Instamojo Dashboard lo 'Products & Store'", content)
        self.assertIn("Price: owner decides", content)

    def test_36_title_within_store_limits(self):
        long_title = "Super Ultra Mega Extended Printable Productivity Daily Weekly Life Planner For ADHD Creators"
        pack = self._create_pack(17, title=long_title)
        results = store_kits.build(pack)

        for store, path in results.items():
            content = path.read_text(encoding="utf-8")
            title_line = [l for l in content.splitlines() if l.startswith("Title: ")][0]
            val = title_line.replace("Title: ", "")
            limit = store_kits.STORE_LIMITS[store]["title_limit"]
            self.assertLessEqual(store_kits.count_graphemes(val), limit)

    def test_37_deterministic_kit_generation(self):
        pack = self._create_pack(18)
        run1 = store_kits.build(pack)
        content1 = {k: p.read_text(encoding="utf-8") for k, p in run1.items()}

        run2 = store_kits.build(pack)
        content2 = {k: p.read_text(encoding="utf-8") for k, p in run2.items()}

        self.assertEqual(content1, content2)

    def test_38_atomic_writes_no_tmp_files_left(self):
        pack = self._create_pack(19)
        store_kits.build(pack)
        kits_dir = pack / "store-kits"
        tmp_files = list(kits_dir.glob("*.tmp"))
        self.assertEqual(tmp_files, [])

    # =======================================================================
    # 6. CLI Tests (39-42)
    # =======================================================================
    def test_39_cli_pack_flag(self):
        pack = self._create_pack(20)
        ret = store_kits.main(["--pack", str(pack)])
        self.assertEqual(ret, 0)
        self.assertTrue((pack / "store-kits" / "gumroad.txt").is_file())

    def test_40_cli_all_flag(self):
        pack1 = self._create_pack(21)
        pack2 = self._create_pack(22)
        ret = store_kits.main(["--all", "--products-dir", str(self.products_dir)])
        self.assertEqual(ret, 0)
        self.assertTrue((pack1 / "store-kits" / "etsy.txt").is_file())
        self.assertTrue((pack2 / "store-kits" / "kofi.txt").is_file())

    def test_41_cli_pack_not_found(self):
        ret = store_kits.main(["--pack", "nonexistent_pack_999"])
        self.assertEqual(ret, 1)

    def test_42_discover_ready_packs(self):
        pack1 = self._create_pack(23)
        packs = store_kits.discover_ready_packs(self.products_dir)
        self.assertIn(pack1, packs)

    # =======================================================================
    # 7. Telegram Plugin tg_kits Structure & Parsing Tests (43-52)
    # =======================================================================
    def test_43_plugin_commands_definition(self):
        self.assertIsInstance(tg_kits.COMMANDS, list)
        self.assertEqual(len(tg_kits.COMMANDS), 1)
        cmd_meta = tg_kits.COMMANDS[0]
        self.assertEqual(cmd_meta["cmd"], "/kit")
        self.assertIn("kit pampu", cmd_meta["words"])
        self.assertIn("help_en", cmd_meta)
        self.assertIn("help_te", cmd_meta)

    def test_44_no_telegram_inbox_import(self):
        self.assertNotIn("telegram_inbox", dir(tg_kits))

    def test_45_parse_slash_kit_number(self):
        self.assertEqual(tg_kits.parse_kit_command("/kit 3"), (3, None))
        self.assertEqual(tg_kits.parse_kit_command("/kits 4"), (4, None))

    def test_46_parse_slash_kit_with_store(self):
        self.assertEqual(tg_kits.parse_kit_command("/kit 3 etsy"), (3, "etsy"))
        self.assertEqual(tg_kits.parse_kit_command("/kit 5 topmate"), (5, "topmate"))
        self.assertEqual(tg_kits.parse_kit_command("/kit 2 kofi"), (2, "kofi"))
        self.assertEqual(tg_kits.parse_kit_command("/kit 1 payhip"), (1, "payhip"))

    def test_47_parse_slash_with_bot_suffix(self):
        self.assertEqual(tg_kits.parse_kit_command("/kit@mybot 7"), (7, None))
        self.assertEqual(tg_kits.parse_kit_command("/kit@mybot 7 instamojo"), (7, "instamojo"))

    def test_48_parse_tenglish_pack_n_kit_pampu(self):
        self.assertEqual(tg_kits.parse_kit_command("pack 3 kit pampu"), (3, None))
        self.assertEqual(tg_kits.parse_kit_command("kit 3 pampu"), (3, None))
        self.assertEqual(tg_kits.parse_kit_command("pack 3 ki kit pampu"), (3, None))

    def test_49_parse_tenglish_with_store_name(self):
        self.assertEqual(tg_kits.parse_kit_command("pack 4 etsy kit pampu"), (4, "etsy"))
        self.assertEqual(tg_kits.parse_kit_command("kit 5 topmate"), (5, "topmate"))
        self.assertEqual(tg_kits.parse_kit_command("kit 2 gumroad pampu"), (2, "gumroad"))

    def test_50_parse_store_aliases(self):
        self.assertEqual(tg_kits.parse_kit_command("/kit 3 mojo"), (3, "instamojo"))
        self.assertEqual(tg_kits.parse_kit_command("/kit 4 gum"), (4, "gumroad"))
        self.assertEqual(tg_kits.parse_kit_command("/kit 5 ko-fi"), (5, "kofi"))

    def test_51_parse_kit_without_number(self):
        self.assertEqual(tg_kits.parse_kit_command("/kit"), (None, None))
        self.assertEqual(tg_kits.parse_kit_command("kit pampu"), (None, None))

    def test_52_unrelated_messages_return_none(self):
        self.assertIsNone(tg_kits.handle("hello there", self.ctx))
        self.assertIsNone(tg_kits.handle("/status", self.ctx))
        self.assertIsNone(tg_kits.handle("/packs", self.ctx))
        self.assertIsNone(tg_kits.handle("em chestunnav", self.ctx))

    # =======================================================================
    # 8. tg_kits.handle Execution Tests (53-62)
    # =======================================================================
    def test_53_handle_missing_number_prompts_owner(self):
        res = tg_kits.handle("/kit", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Pack number cheppandi", res["text"])

    def test_54_handle_pack_not_found(self):
        res = tg_kits.handle("/kit 999", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Pack #999", res["text"])
        self.assertIn("dorakaledu", res["text"])

    def test_55_handle_default_gumroad_document_and_summary(self):
        self._create_pack(1)
        res = tg_kits.handle("/kit 1", self.ctx)
        self.assertEqual(res, {"handled": True})

        # Sent 1 document (gumroad.txt)
        self.assertEqual(len(self.mock_ctx.sent_docs), 1)
        path, caption = self.mock_ctx.sent_docs[0]
        self.assertTrue(path.endswith("gumroad.txt"))
        self.assertIn("Gumroad Listing Kit", caption)

        # Sent 1 text summary message with first 3 fields
        self.assertEqual(len(self.mock_ctx.sent_texts), 1)
        summary = self.mock_ctx.sent_texts[0]
        self.assertIn("1️⃣ Title:", summary)
        self.assertIn("2️⃣ Short Desc:", summary)
        self.assertIn("3️⃣ Tags:", summary)

    def test_56_handle_specific_store_etsy(self):
        self._create_pack(2)
        res = tg_kits.handle("/kit 2 etsy", self.ctx)
        self.assertEqual(res, {"handled": True})

        self.assertEqual(len(self.mock_ctx.sent_docs), 1)
        path, caption = self.mock_ctx.sent_docs[0]
        self.assertTrue(path.endswith("etsy.txt"))
        self.assertIn("Etsy Listing Kit", caption)

    def test_57_handle_tenglish_phrase(self):
        self._create_pack(3)
        res = tg_kits.handle("pack 3 topmate kit pampu", self.ctx)
        self.assertEqual(res, {"handled": True})

        self.assertEqual(len(self.mock_ctx.sent_docs), 1)
        path, caption = self.mock_ctx.sent_docs[0]
        self.assertTrue(path.endswith("topmate.txt"))
        self.assertIn("Topmate Listing Kit", caption)

    def test_58_handle_auto_builds_kit_if_missing(self):
        pack = self._create_pack(4)
        kits_dir = pack / "store-kits"
        if kits_dir.exists():
            shutil.rmtree(kits_dir)

        res = tg_kits.handle("/kit 4 payhip", self.ctx)
        self.assertEqual(res, {"handled": True})
        self.assertTrue((kits_dir / "payhip.txt").is_file())

    def test_59_handle_all_six_stores_via_plugin(self):
        pack = self._create_pack(5)
        for store in store_kits.SUPPORTED_STORES:
            ctx_mock = MockContext(self.products_dir, self.marketing_dir)
            res = tg_kits.handle(f"/kit 5 {store}", ctx_mock.as_dict())
            self.assertEqual(res, {"handled": True})
            self.assertEqual(len(ctx_mock.sent_docs), 1)
            doc_path, _ = ctx_mock.sent_docs[0]
            self.assertTrue(doc_path.endswith(f"{store}.txt"))

    def test_60_extract_first_3_fields_structure(self):
        sample = (
            "Title: Best ADHD Daily Planner\n"
            "Short Description: Stay organized and calm.\n"
            "Tags (5 tags):\nadhd, planner, daily\n"
        )
        out = tg_kits.extract_first_3_fields(sample)
        self.assertIn("1️⃣ Title: Best ADHD Daily Planner", out)
        self.assertIn("2️⃣ Short Desc: Stay organized and calm.", out)
        self.assertIn("3️⃣ Tags: adhd, planner, daily", out)

    def test_61_path_security_rejection(self):
        outside_file = self.base_path / "outside.txt"
        outside_file.write_text("evil", encoding="utf-8")
        self.assertFalse(tg_kits._is_safe_path(outside_file, self.ctx))

    def test_62_no_currency_amounts_in_new_code(self):
        import tests.test_r22_markdown_currency as m
        for mod in (store_kits, tg_kits):
            content = Path(mod.__file__).read_text(encoding="utf-8")
            matches = m.CURRENCY_AMOUNT.findall(content)
            self.assertEqual(matches, [], f"Found currency amounts in {mod.__file__}: {matches}")


if __name__ == "__main__":
    unittest.main()
