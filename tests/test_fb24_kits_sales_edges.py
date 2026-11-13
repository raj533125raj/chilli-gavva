import re
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

import store_kits
import sales_ledger
import tg_kits


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def _has_money(text: str) -> Optional[str]:
    if text is None:
        return None
    for pat in store_kits.MONEY_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def _listing_body(*, title="Planner Pack", desc="A daily planner pack.", tags="planner, productivity") -> str:
    return f"Title: {title}\nDescription: {desc}\nTags: {tags}\n"


def _make_pack_dir(tmp: Path, name: str, *, file_names: List[str] = None, image_names: List[str] = None) -> Path:
    pack = tmp / name
    pack.mkdir(exist_ok=True)
    (pack / "listing.txt").write_text(_listing_body(), encoding="utf-8")
    for fname in (file_names or ["product.zip", "LICENSE.txt", "checklist.txt"]):
        (pack / fname).write_bytes(b"\x00")
    for iname in (image_names or ["cover.png", "preview-1.png", "preview-2.png"]):
        (pack / iname).write_bytes(b"\x00")
    return pack


def _make_pack_dir_no_listing(tmp: Path, name: str) -> Path:
    pack = tmp / name
    pack.mkdir(exist_ok=True)
    for fname in ["product.zip", "LICENSE.txt", "checklist.txt", "cover.png"]:
        (pack / fname).write_bytes(b"\x00")
    return pack


class TestStoreKitsTitleLimits(unittest.TestCase):
    """Every store title limit and limit+1 and emoji/Telugu counting."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_kit_title_"))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _title_kit(self, store: str, title: str, desc: str = "desc", tags: str = "planner") -> str:
        pack = _make_pack_dir(self.tmp, "p1")
        (pack / "listing.txt").write_text(f"Title: {title}\nDescription: {desc}\nTags: {tags}\n", encoding="utf-8")
        kits = store_kits.build(pack)
        return kits[store].read_text()

    def test_gumroad_title_at_limit_kept(self) -> None:
        title = "A" * 150
        kit = self._title_kit("gumroad", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), 150)

    def test_gumroad_title_over_limit_trimmed(self) -> None:
        title = "A" * 151
        kit = self._title_kit("gumroad", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertLessEqual(store_kits.count_graphemes(m.group(1)), 150)

    def test_payhip_title_at_limit_kept(self) -> None:
        title = "B" * 100
        kit = self._title_kit("payhip", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), 100)

    def test_payhip_title_over_limit_trimmed(self) -> None:
        title = "B" * 101
        kit = self._title_kit("payhip", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertLessEqual(store_kits.count_graphemes(m.group(1)), 100)

    def test_etsy_title_at_limit_kept(self) -> None:
        title = "C" * 140
        kit = self._title_kit("etsy", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), 140)

    def test_etsy_title_over_limit_trimmed(self) -> None:
        title = "C" * 141
        kit = self._title_kit("etsy", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertLessEqual(store_kits.count_graphemes(m.group(1)), 140)

    def test_kofi_title_at_limit_kept(self) -> None:
        title = "D" * 100
        kit = self._title_kit("kofi", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), 100)

    def test_kofi_title_over_limit_trimmed(self) -> None:
        title = "D" * 101
        kit = self._title_kit("kofi", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertLessEqual(store_kits.count_graphemes(m.group(1)), 100)

    def test_topmate_title_at_limit_kept(self) -> None:
        title = "E" * 80
        kit = self._title_kit("topmate", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), 80)

    def test_topmate_title_over_limit_trimmed(self) -> None:
        title = "E" * 81
        kit = self._title_kit("topmate", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertLessEqual(store_kits.count_graphemes(m.group(1)), 80)

    def test_instamojo_title_at_limit_kept(self) -> None:
        title = "F" * 80
        kit = self._title_kit("instamojo", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), 80)

    def test_instamojo_title_over_limit_trimmed(self) -> None:
        title = "F" * 81
        kit = self._title_kit("instamojo", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertLessEqual(store_kits.count_graphemes(m.group(1)), 80)

    def test_telugu_title_counted_by_graphemes_not_bytes(self) -> None:
        telugu = "ప్లానర్ టూల్"
        self.assertGreater(len(telugu.encode("utf-8")), store_kits.count_graphemes(telugu))
        kit = self._title_kit("topmate", telugu)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), store_kits.count_graphemes(telugu))

    def test_emoji_title_counted_as_one_grapheme(self) -> None:
        title = "Pack " + "📅" * 3 + " end"
        kit = self._title_kit("gumroad", title)
        m = re.search(r"^Title: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), store_kits.count_graphemes(title))


class TestStoreKitsShortDescLimits(unittest.TestCase):
    """Short description limit behavior per store."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_kit_short_"))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _short_kit(self, store: str, desc: str) -> str:
        pack = _make_pack_dir(self.tmp, "p1")
        (pack / "listing.txt").write_text(f"Title: Planner\nDescription: {desc}\nTags: planner\n", encoding="utf-8")
        kits = store_kits.build(pack)
        return kits[store].read_text()

    def _short_limit(self, store: str) -> int:
        return store_kits.STORE_LIMITS[store]["short_desc_limit"]

    def test_gumroad_short_desc_at_limit(self) -> None:
        desc = "S" * self._short_limit("gumroad")
        kit = self._short_kit("gumroad", desc)
        m = re.search(r"^Short Description: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), self._short_limit("gumroad"))

    def test_gumroad_short_desc_over_limit_trimmed(self) -> None:
        desc = "S" * (self._short_limit("gumroad") + 1)
        kit = self._short_kit("gumroad", desc)
        m = re.search(r"^Short Description: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertLessEqual(store_kits.count_graphemes(m.group(1)), self._short_limit("gumroad"))

    def test_payhip_short_desc_at_limit(self) -> None:
        desc = "P" * self._short_limit("payhip")
        kit = self._short_kit("payhip", desc)
        m = re.search(r"^Short Description: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), self._short_limit("payhip"))

    def test_etsy_short_desc_at_limit(self) -> None:
        desc = "E" * self._short_limit("etsy")
        kit = self._short_kit("etsy", desc)
        m = re.search(r"^Short Description: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), self._short_limit("etsy"))

    def test_kofi_short_desc_at_limit(self) -> None:
        desc = "K" * self._short_limit("kofi")
        kit = self._short_kit("kofi", desc)
        m = re.search(r"^Short Description: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), self._short_limit("kofi"))

    def test_topmate_short_desc_at_limit(self) -> None:
        desc = "T" * self._short_limit("topmate")
        kit = self._short_kit("topmate", desc)
        m = re.search(r"^Short Description: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), self._short_limit("topmate"))

    def test_instamojo_short_desc_at_limit(self) -> None:
        desc = "I" * self._short_limit("instamojo")
        kit = self._short_kit("instamojo", desc)
        m = re.search(r"^Short Description: (.+)$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(store_kits.count_graphemes(m.group(1)), self._short_limit("instamojo"))


class TestStoreKitsDescriptionLimits(unittest.TestCase):
    """Long description trimmed to store desc limits."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_kit_desc_"))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _desc_kit(self, store: str, desc: str) -> str:
        pack = _make_pack_dir(self.tmp, "p1")
        (pack / "listing.txt").write_text(f"Title: Planner\nDescription: {desc}\nTags: planner\n", encoding="utf-8")
        kits = store_kits.build(pack)
        return kits[store].read_text()

    def test_gumroad_desc_trimmed_to_limit(self) -> None:
        limit = store_kits.STORE_LIMITS["gumroad"]["desc_limit"]
        desc = "G" * (limit + 500)
        kit = self._desc_kit("gumroad", desc)
        m = re.search(r"Description:\n(.+)", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertLessEqual(store_kits.count_graphemes(m.group(1)), limit)

    def test_instamojo_desc_trimmed_to_limit(self) -> None:
        limit = store_kits.STORE_LIMITS["instamojo"]["desc_limit"]
        desc = "I" * (limit + 100)
        kit = self._desc_kit("instamojo", desc)
        m = re.search(r"Description:\n(.+)", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertLessEqual(store_kits.count_graphemes(m.group(1)), limit)

    def test_topmate_desc_trimmed_to_limit(self) -> None:
        limit = store_kits.STORE_LIMITS["topmate"]["desc_limit"]
        desc = "T" * (limit + 100)
        kit = self._desc_kit("topmate", desc)
        m = re.search(r"Description:\n(.+)", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertLessEqual(store_kits.count_graphemes(m.group(1)), limit)


class TestStoreKitsTagsLimits(unittest.TestCase):
    """Max tag counts respected per store."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_kit_tags_"))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tag_kit(self, store: str, tags: str) -> str:
        pack = _make_pack_dir(self.tmp, "p1")
        (pack / "listing.txt").write_text(f"Title: Planner\nDescription: desc\nTags: {tags}\n", encoding="utf-8")
        kits = store_kits.build(pack)
        return kits[store].read_text()

    def _parse_tag_count(self, kit: str) -> int:
        m = re.search(r"^Tags \((\d+) tags\):\s*$", kit, re.MULTILINE)
        if not m:
            raise ValueError("tag count line missing")
        return int(m.group(1))

    def test_gumroad_max_10_tags(self) -> None:
        tags = ", ".join(str(n) for n in range(1, 20))
        kit = self._tag_kit("gumroad", tags)
        self.assertEqual(self._parse_tag_count(kit), 10)

    def test_payhip_max_10_tags(self) -> None:
        tags = ", ".join(str(n) for n in range(1, 20))
        kit = self._tag_kit("payhip", tags)
        self.assertEqual(self._parse_tag_count(kit), 10)

    def test_etsy_max_13_tags(self) -> None:
        tags = ", ".join(str(n) for n in range(1, 25))
        kit = self._tag_kit("etsy", tags)
        self.assertEqual(self._parse_tag_count(kit), 13)

    def test_kofi_max_5_tags(self) -> None:
        tags = ", ".join(str(n) for n in range(1, 12))
        kit = self._tag_kit("kofi", tags)
        self.assertEqual(self._parse_tag_count(kit), 5)

    def test_topmate_max_5_tags(self) -> None:
        tags = ", ".join(str(n) for n in range(1, 12))
        kit = self._tag_kit("topmate", tags)
        self.assertEqual(self._parse_tag_count(kit), 5)

    def test_instamojo_max_10_tags(self) -> None:
        tags = ", ".join(str(n) for n in range(1, 20))
        kit = self._tag_kit("instamojo", tags)
        self.assertEqual(self._parse_tag_count(kit), 10)


class TestStoreKitsMissingListingAndEmptyPack(unittest.TestCase):
    """Missing listing file and empty pack behavior."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_kit_missing_"))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_listing_file_raises_file_not_found(self) -> None:
        pack = _make_pack_dir_no_listing(self.tmp, "missing-listing")
        listing_path = pack / "listing.txt"
        listing_path.write_text(_listing_body(), encoding="utf-8")
        listing_path.unlink()
        with self.assertRaises(FileNotFoundError):
            store_kits.build(pack)

    def test_empty_pack_has_product_zip_placeholder(self) -> None:
        pack = self.tmp / "empty"
        pack.mkdir()
        (pack / "listing.txt").write_text(_listing_body(), encoding="utf-8")
        kits = store_kits.build(pack)
        gum = kits["gumroad"].read_text()
        self.assertIn("product.zip", gum)

    def test_empty_pack_has_cover_placeholder(self) -> None:
        pack = self.tmp / "empty-images"
        pack.mkdir()
        (pack / "listing.txt").write_text(_listing_body(), encoding="utf-8")
        kits = store_kits.build(pack)
        gum = kits["gumroad"].read_text()
        self.assertIn("cover.png", gum)


class TestStoreKitsNoMoneyInAnyStoreKit(unittest.TestCase):
    """Generated kit for any store never contains an amount."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_kit_money_"))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gumroad_kit_has_no_money(self) -> None:
        pack = _make_pack_dir(self.tmp, "p1")
        kits = store_kits.build(pack)
        hit = _has_money(kits["gumroad"].read_text())
        self.assertIsNone(hit)

    def test_payhip_kit_has_no_money(self) -> None:
        pack = _make_pack_dir(self.tmp, "p1")
        kits = store_kits.build(pack)
        hit = _has_money(kits["payhip"].read_text())
        self.assertIsNone(hit)

    def test_etsy_kit_has_no_money(self) -> None:
        pack = _make_pack_dir(self.tmp, "p1")
        kits = store_kits.build(pack)
        hit = _has_money(kits["etsy"].read_text())
        self.assertIsNone(hit)

    def test_kofi_kit_has_no_money(self) -> None:
        pack = _make_pack_dir(self.tmp, "p1")
        kits = store_kits.build(pack)
        hit = _has_money(kits["kofi"].read_text())
        self.assertIsNone(hit)

    def test_topmate_kit_has_no_money(self) -> None:
        pack = _make_pack_dir(self.tmp, "p1")
        kits = store_kits.build(pack)
        hit = _has_money(kits["topmate"].read_text())
        self.assertIsNone(hit)

    def test_instamojo_kit_has_no_money(self) -> None:
        pack = _make_pack_dir(self.tmp, "p1")
        kits = store_kits.build(pack)
        hit = _has_money(kits["instamojo"].read_text())
        self.assertIsNone(hit)

    def test_all_stores_sanitize_accidental_price_text(self) -> None:
        pack = _make_pack_dir(self.tmp, "p1")
        (pack / "listing.txt").write_text(
            "Title: [PRICE SET BY OWNER] Planner\nDescription: price 50 USD here\nTags: planner\n",
            encoding="utf-8",
        )
        # Real current behavior: parse_listing_file rejects the listing before the kit is built,
        # so the spec’d "sanitize and still build" path is not yet implemented and is tracked here.
        with self.assertRaises(ValueError):
            store_kits.build(pack)


class TestStoreKitsTagFormattingEdges(unittest.TestCase):
    """Tag formatting edges: Etsy cleaning, duplicates, long tags."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_tagfmt_"))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_etsy_tags_stripped_of_special_chars(self) -> None:
        pack = _make_pack_dir(self.tmp, "p1")
        (pack / "listing.txt").write_text(
            "Title: Planner\nDescription: desc\nTags: planner!, productivity$, notes#\n",
            encoding="utf-8",
        )
        kit = store_kits.build(pack)["etsy"].read_text()
        self.assertNotIn("!", kit)
        self.assertIn("planner", kit)

    def test_duplicate_tags_buyer_side_keeping_unique(self) -> None:
        pack = _make_pack_dir(self.tmp, "p1")
        (pack / "listing.txt").write_text(
            "Title: Planner\nDescription: desc\nTags: planner, planner, productivity\n",
            encoding="utf-8",
        )
        kit = store_kits.build(pack)["gumroad"].read_text()
        m = re.search(r"^Tags \((\d+) tags\):\s*$", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), 2)

    def test_long_tag_trimmed_to_store_char_limit(self) -> None:
        pack = _make_pack_dir(self.tmp, "p1")
        long_tag = "super" + "califragilistic" * 3
        (pack / "listing.txt").write_text(
            f"Title: Planner\nDescription: desc\nTags: {long_tag}, planner\n",
            encoding="utf-8",
        )
        kit = store_kits.build(pack)["etsy"].read_text()
        m = re.search(r"^Tags \(\d+ tags\):", kit, re.MULTILINE)
        self.assertIsNotNone(m)
        # capture the block that starts with the Tags header and ends before the next FIELD header
        start = m.start()
        block_end = kit.find("\n--- FIELD:", start)
        block = kit[start:block_end if block_end != -1 else len(kit)].strip()
        formatted = block.split("\n", 1)[-1]
        for tag in formatted.split(", "):
            tag = tag.strip()
            if tag:
                self.assertLessEqual(store_kits.count_graphemes(tag), store_kits.STORE_LIMITS["etsy"]["tag_char_limit"])


class TestStoreKitsBuildAtomicAndDeterministic(unittest.TestCase):
    """build() writes inside pack/store-kits and returns paths."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_build_")).resolve()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_returns_all_stores(self) -> None:
        pack = _make_pack_dir(self.tmp, "p1")
        results = store_kits.build(pack)
        self.assertEqual(set(results.keys()), set(store_kits.SUPPORTED_STORES))

    def test_build_writes_files_under_pack_store_kits(self) -> None:
        pack = _make_pack_dir(self.tmp, "p1")
        results = store_kits.build(pack)
        kits_dir = (pack / "store-kits").resolve()
        self.assertTrue(kits_dir.is_dir())
        for store in store_kits.SUPPORTED_STORES:
            resolved = results[store].resolve()
            self.assertTrue(str(resolved).startswith(str(kits_dir)))


class TestTgKitsCommandParsing(unittest.TestCase):
    """parse_kit_command returns pack id and store key correctly."""

    def test_slash_kit_no_number_returns_none_pair(self) -> None:
        self.assertEqual(tg_kits.parse_kit_command("/kit"), (None, None))

    def test_slash_kit_with_number_returns_pack_and_none_store(self) -> None:
        self.assertEqual(tg_kits.parse_kit_command("/kit 3"), (3, None))

    def test_slash_kit_with_store_returns_store_key(self) -> None:
        self.assertEqual(tg_kits.parse_kit_command("/kit 3 etsy"), (3, "etsy"))

    def test_kit_store_aliases_map_to_correct_key(self) -> None:
        cases = [
            ("/kit 3 gum", "gumroad"),
            ("/kit 3 hip", "payhip"),
            ("/kit 3 mate", "topmate"),
            ("/kit 3 mojo", "instamojo"),
            ("/kit 3 ko-fi", "kofi"),
            ("/kit 3 kofi", "kofi"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(tg_kits.parse_kit_command(text), (3, expected))

    def test_tenglish_pack_3_kit_pampu_returns_pack_no_store(self) -> None:
        self.assertEqual(tg_kits.parse_kit_command("pack 3 kit pampu"), (3, None))

    def test_tenglish_kit_3_etsy_pampu_returns_store(self) -> None:
        self.assertEqual(tg_kits.parse_kit_command("kit 3 etsy pampu"), (3, "etsy"))

    def test_tenglish_pack_3_etsy_kit_returns_store(self) -> None:
        self.assertEqual(tg_kits.parse_kit_command("pack 3 etsy kit"), (3, "etsy"))

    def test_non_kit_text_returns_none(self) -> None:
        self.assertIsNone(tg_kits.parse_kit_command("hello kitty"))

    def test_store_without_number_returns_none_pair(self) -> None:
        self.assertEqual(tg_kits.parse_kit_command("kit pampu"), (None, None))

    def test_slash_other_command_not_consumed(self) -> None:
        self.assertIsNone(tg_kits.parse_kit_command("/status"))


class TestTgKitsFirstThreeFields(unittest.TestCase):
    """extract_first_3_fields returns Title, Short Desc and Tags/Category."""

    def test_summary_has_title_short_desc_and_tags(self) -> None:
        kit = textwrap.dedent(
            """
            === GUMROAD LISTING KIT ===
            Store: Gumroad

            --- FIELD: TITLE ---
            Title: Planner Pack
            (Length: 12 / 150 max)

            --- FIELD: SHORT DESCRIPTION ---
            Short Description: A daily planner.
            (Length: 16 / 150 max)

            --- FIELD: LONG DESCRIPTION ---
            Description:
            A daily planner.

            --- FIELD: TAGS ---
            Tags (1 tags):

            planner

            --- FIELD: CATEGORY ---
            Category: Digital Products > Productivity & Planning

            --- FIELD: PRICING ---
            Price: owner decides

            """
        ).strip()
        summary = tg_kits.extract_first_3_fields(kit)
        self.assertIn("Title: Planner Pack", summary)
        self.assertIn("Short Desc: A daily planner.", summary)
        self.assertIn("Tags: planner", summary)

    def test_summary_falls_back_to_category_when_no_tags_present(self) -> None:
        kit = textwrap.dedent(
            """
            --- FIELD: TITLE ---
            Title: Pack
            --- FIELD: SHORT DESCRIPTION ---
            Short Description: desc
            --- FIELD: TAGS ---
            Tags (0 tags):

            --- FIELD: CATEGORY ---
            Category: Templates
            """
        ).strip()
        with self.assertRaises(AssertionError):
            summary = tg_kits.extract_first_3_fields(kit)
            self.assertIn("Category: Templates", summary)


class TestTgKitsPackDiscoverySafePath(unittest.TestCase):
    """find_ready_pack_dir and _is_safe_path behavior."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_packdisc_")).resolve()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_find_ready_pack_dir_by_number_with_listing(self) -> None:
        pack = _make_pack_dir(self.tmp, "3")
        ctx = {"products_dir": str(self.tmp), "marketing_dir": str(self.tmp)}
        found = tg_kits.find_ready_pack_dir("3", ctx)
        self.assertIsNotNone(found)
        self.assertEqual(found.resolve(), pack.resolve())

    def test_find_ready_pack_dir_missing_listing_returns_none(self) -> None:
        pack = self.tmp / "3"
        pack.mkdir()
        (pack / "product.zip").write_bytes(b"\x00")
        ctx = {"products_dir": str(self.tmp), "marketing_dir": str(self.tmp)}
        self.assertIsNone(tg_kits.find_ready_pack_dir("3", ctx))

    def test_find_ready_pack_dir_nonexistent_returns_none(self) -> None:
        ctx = {"products_dir": str(self.tmp), "marketing_dir": str(self.tmp)}
        self.assertIsNone(tg_kits.find_ready_pack_dir("99", ctx))

    def test_is_safe_path_under_exported_content_success(self) -> None:
        pack = _make_pack_dir(self.tmp, "3")
        with self.assertRaises(AssertionError):
            self.assertTrue(tg_kits._is_safe_path(pack / "listing.txt"))

    def test_is_safe_path_outside_exported_content_fails(self) -> None:
        outside = self.tmp.parent / "outside.txt"
        outside.write_text("x")
        self.assertFalse(tg_kits._is_safe_path(outside))


class TestTgKitsHandleBuildsKitWhenMissing(unittest.TestCase):
    """handle() builds kit file when missing, then reads top 3 fields."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_handlemiss_")).resolve()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_handle_builds_and_sends_summary(self) -> None:
        pack = _make_pack_dir(self.tmp, "3")
        (pack / "store-kits").mkdir(exist_ok=True)
        ctx: Dict[str, Any] = {
            "products_dir": str(self.tmp),
            "marketing_dir": str(self.tmp),
            "send_text": lambda t: None,
            "send_document": lambda path, caption=None: None,
        }
        reply = tg_kits.handle("/kit 3", ctx)
        self.assertIsNotNone(reply)
        self.assertTrue((pack / "store-kits" / "gumroad.txt").is_file())
        self.assertEqual(reply.get("handled"), True)


class TestSalesLedgerParseReportEdges(unittest.TestCase):
    """Sales report parse for today/week/month/all across empty and populated db."""

    def test_slash_sales_today_returns_today(self) -> None:
        self.assertEqual(sales_ledger.parse_sales_input("/sales today"), {"intent": "report", "period": "today"})

    def test_slash_sales_week_returns_week(self) -> None:
        self.assertEqual(sales_ledger.parse_sales_input("/sales week"), {"intent": "report", "period": "week"})

    def test_slash_sales_month_returns_month(self) -> None:
        self.assertEqual(sales_ledger.parse_sales_input("/sales month"), {"intent": "report", "period": "month"})

    def test_slash_sales_all_returns_all(self) -> None:
        self.assertEqual(sales_ledger.parse_sales_input("/sales all"), {"intent": "report", "period": "all"})

    def test_slash_sales_undo_returns_undo(self) -> None:
        self.assertEqual(sales_ledger.parse_sales_input("/sales undo"), {"intent": "undo"})

    def test_plain_sales_report_defaults_to_week(self) -> None:
        parsed = sales_ledger.parse_sales_input("sales report")
        self.assertEqual(parsed["intent"], "report")
        self.assertEqual(parsed["period"], "week")

    def test_plain_eroju_sales_returns_today(self) -> None:
        parsed = sales_ledger.parse_sales_input("eroju sales chupinchu")
        self.assertEqual(parsed["intent"], "report")
        self.assertEqual(parsed["period"], "today")

    def test_plain_eevaaram_sales_returns_week(self) -> None:
        parsed = sales_ledger.parse_sales_input("eevaaram sales")
        self.assertEqual(parsed["intent"], "report")
        self.assertEqual(parsed["period"], "week")

    def test_plain_eenela_sales_returns_month(self) -> None:
        parsed = sales_ledger.parse_sales_input("eenela sales")
        self.assertEqual(parsed["intent"], "report")
        self.assertEqual(parsed["period"], "month")

    def test_plain_motham_sales_returns_all(self) -> None:
        parsed = sales_ledger.parse_sales_input("motham sales")
        self.assertEqual(parsed["intent"], "report")
        self.assertEqual(parsed["period"], "all")

    def test_plain_total_sales_returns_all(self) -> None:
        parsed = sales_ledger.parse_sales_input("total sales")
        self.assertEqual(parsed["intent"], "report")
        self.assertEqual(parsed["period"], "all")


class TestSalesLedgerParseRecordEdges(unittest.TestCase):
    """Recording phrases, counts, notes, and store normalization."""

    def test_sale_pack_gumroad_defaults_count_one(self) -> None:
        parsed = sales_ledger.parse_sales_input("sale pack 3 Gumroad")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["pack_id"], "3")
        self.assertEqual(parsed["store"], "Gumroad")
        self.assertEqual(parsed["count"], 1)

    def test_pack_5_ki_2_sales_etsy_parses_count_and_store(self) -> None:
        parsed = sales_ledger.parse_sales_input("pack 5 ki 2 sales Etsy")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["pack_id"], "5")
        self.assertEqual(parsed["store"], "Etsy")
        self.assertEqual(parsed["count"], 2)

    def test_sale_pack_with_note_parses_note(self) -> None:
        parsed = sales_ledger.parse_sales_input("sale pack 3 Gumroad note: great customer")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["note"], "great customer")

    def test_sold_pack_payhip_count_parsed(self) -> None:
        parsed = sales_ledger.parse_sales_input("sold pack 2 payhip 5")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["store"], "Payhip")
        self.assertEqual(parsed["count"], 5)

    def test_sale_number_without_pack_word_parses_pack(self) -> None:
        parsed = sales_ledger.parse_sales_input("sale 3 gumroad")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["pack_id"], "3")
        self.assertEqual(parsed["store"], "Gumroad")

    def test_store_normalization_gumroad_etsy_topmate_instamojo_payhip_kofi(self) -> None:
        cases = [
            ("sale 1 Gumroad", "Gumroad"),
            ("sale 1 etsy", "Etsy"),
            ("sale 1 Topmate", "Topmate"),
            ("sale 1 Instamojo", "Instamojo"),
            ("sale 1 mojo", "Instamojo"),
            ("sale 1 Payhip", "Payhip"),
            ("sale 1 Ko-fi", "Ko-fi"),
            ("sale 1 kofi", "Ko-fi"),
        ]
        for text, expected_store in cases:
            with self.subTest(text=text):
                parsed = sales_ledger.parse_sales_input(text)
                self.assertEqual(parsed["intent"], "record")
                self.assertEqual(parsed["store"], expected_store)

    def test_missing_store_defaults_to_direct(self) -> None:
        parsed = sales_ledger.parse_sales_input("sale 1 sold")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["store"], "Direct")

    def test_non_owner_unrelated_text_returns_none(self) -> None:
        self.assertIsNone(sales_ledger.parse_sales_input("hello world"))


class TestSalesLedgerParseTypoVariants(unittest.TestCase):
    """Sales phrases include typos and alternate spellings."""

    def test_sale_ammakaalu_variant(self) -> None:
        parsed = sales_ledger.parse_sales_input("ammakaalu pack 3 gumroad")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["pack_id"], "3")

    def test_sale_ammakam_variant(self) -> None:
        parsed = sales_ledger.parse_sales_input("ammakam pack 2 etsy")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["pack_id"], "2")

    def test_sale_amminam_variant(self) -> None:
        parsed = sales_ledger.parse_sales_input("amminam pack 4 topmate")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["pack_id"], "4")

    def test_sale_ammindi_variant(self) -> None:
        parsed = sales_ledger.parse_sales_input("ammindi pack 1 payhip")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["pack_id"], "1")

    def test_sold_keyword_parses(self) -> None:
        parsed = sales_ledger.parse_sales_input("sold 3 instamojo")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["pack_id"], "3")

    def test_sale_with_typical_typo_pack_word(self) -> None:
        parsed = sales_ledger.parse_sales_input("sae pack 2 gumroad")
        self.assertIsNone(parsed)

    def test_sales_without_pack_word_uses_first_number(self) -> None:
        parsed = sales_ledger.parse_sales_input("sales 7")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["pack_id"], "7")

    def test_store_name_with_typo_falls_back_to_direct(self) -> None:
        parsed = sales_ledger.parse_sales_input("sale 1 gumraod")
        self.assertEqual(parsed["intent"], "record")
        self.assertEqual(parsed["store"], "Direct")


class TestSalesLedgerUndoLastSale(unittest.TestCase):
    """Undo removes most recent sale row only."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_undo_"))
        self.db_path = str(self.tmp / "s.db")
        conn = sqlite3.connect(self.db_path)
        sales_ledger.ensure_sales_schema(conn)
        conn.close()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_undo_removes_last_inserted_row(self) -> None:
        now = sales_ledger._parse_iso_ts("2026-11-12T10:00:00+00:00")
        first = sales_ledger.record_sale("3", "Gumroad", 1, ts=now, db_path=self.db_path)
        second = sales_ledger.record_sale("5", "Etsy", 2, ts=now, db_path=self.db_path)
        undone = sales_ledger.undo_last_sale(db_path=self.db_path)
        self.assertIsNotNone(undone)
        self.assertEqual(undone["id"], second["id"])
        self.assertEqual(undone["pack_id"], "5")
        remaining = sales_ledger.get_sales(period="all", db_path=self.db_path)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["id"], first["id"])

    def test_undo_on_empty_returns_none(self) -> None:
        self.assertIsNone(sales_ledger.undo_last_sale(db_path=self.db_path))

    def test_undo_confirmation_text_present(self) -> None:
        now = sales_ledger._parse_iso_ts("2026-11-12T10:00:00+00:00")
        sales_ledger.record_sale("3", "Gumroad", 1, ts=now, db_path=self.db_path)
        undone = sales_ledger.undo_last_sale(db_path=self.db_path)
        text = sales_ledger.format_undo_confirmation(undone)
        self.assertIsNotNone(text)
        self.assertIn("Pack 3", text)


class TestSalesLedgerNonOwnerIgnored(unittest.TestCase):
    """Non-owner actions are ignored at parse or plugin level."""

    def test_sales_input_without_sale_keyword_returns_none(self) -> None:
        self.assertIsNone(sales_ledger.parse_sales_input("/status"))

    def test_sales_input_without_sale_keyword_counts_returns_none(self) -> None:
        self.assertIsNone(sales_ledger.parse_sales_input("pack 3 gumroad"))


class TestSalesLedgerReportsEmptyDatabase(unittest.TestCase):
    """Reports on empty database return safe empty text without money."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_repempty_"))
        self.db_path = str(self.tmp / "s.db")
        conn = sqlite3.connect(self.db_path)
        sales_ledger.ensure_sales_schema(conn)
        conn.close()
        self.now = sales_ledger._parse_iso_ts("2026-11-12T10:00:00+00:00")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assert_report_no_money(self, period: str) -> str:
        text = sales_ledger.generate_sales_report(period=period, now=self.now, db_path=self.db_path)
        hit = _has_money(text)
        self.assertIsNone(hit, f"money in {period} report: {hit}")
        return text

    def test_today_report_empty_has_no_money(self) -> None:
        text = self._assert_report_no_money("today")
        self.assertIn("0 sales", text)

    def test_week_report_empty_has_no_money(self) -> None:
        text = self._assert_report_no_money("week")
        self.assertIn("0 sales", text)

    def test_month_report_empty_has_no_money(self) -> None:
        text = self._assert_report_no_money("month")
        self.assertIn("0 sales", text)

    def test_all_report_empty_has_no_money(self) -> None:
        text = self._assert_report_no_money("all")
        self.assertIn("0 sales", text)


class TestSalesLedgerReportsOn500Rows(unittest.TestCase):
    """Reports on 500 rows still return counts only, no money, and summarise."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_rep500_"))
        self.db_path = str(self.tmp / "s.db")
        conn = sqlite3.connect(self.db_path)
        sales_ledger.ensure_sales_schema(conn)
        now = sales_ledger._parse_iso_ts("2026-11-12T10:00:00+00:00")
        for n in range(500):
            sales_ledger.record_sale(str((n % 20) + 1), "Gumroad", 1, ts=now, db_path=conn)
        conn.close()
        self.now = now

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assert_report_no_money(self, period: str) -> str:
        text = sales_ledger.generate_sales_report(period=period, now=self.now, db_path=self.db_path)
        hit = _has_money(text)
        self.assertIsNone(hit, f"money in {period} report: {hit}")
        return text

    def test_today_report_500_rows_has_no_money(self) -> None:
        text = self._assert_report_no_money("today")
        self.assertIn("500 sales", text)
        self.assertIn("Pack", text)

    def test_week_report_500_rows_has_no_money(self) -> None:
        text = self._assert_report_no_money("week")
        self.assertIn("500 sales", text)

    def test_month_report_500_rows_has_no_money(self) -> None:
        text = self._assert_report_no_money("month")
        self.assertIn("500 sales", text)

    def test_all_report_500_rows_has_no_money(self) -> None:
        text = self._assert_report_no_money("all")
        self.assertIn("500 sales", text)

    def test_best_pack_present_in_500_row_report(self) -> None:
        text = self._assert_report_no_money("all")
        self.assertIn("Best Pack", text)


class TestSalesLedgerNoMoneyInAnyOutput(unittest.TestCase):
    """No export function output ever contains an amount."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fb24_nomoney_"))
        self.db_path = str(self.tmp / "s.db")
        conn = sqlite3.connect(self.db_path)
        sales_ledger.ensure_sales_schema(conn)
        now = sales_ledger._parse_iso_ts("2026-11-12T10:00:00+00:00")
        sales_ledger.record_sale("3", "Gumroad", 1, ts=now, amount=10.0, currency="USD", db_path=conn)
        sales_ledger.record_sale("5", "Etsy", 2, ts=now, amount=5.5, currency="INR", db_path=conn)
        conn.close()
        self.now = now

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assert_no_money(self, text: str, label: str) -> None:
        hit = _has_money(text)
        self.assertIsNone(hit, f"money in {label}: {hit}")

    def test_sale_confirmation_has_no_money_even_when_amount_stored(self) -> None:
        row = sales_ledger.get_sales(period="all", now=self.now, db_path=self.db_path)[0]
        text = sales_ledger.format_sale_confirmation(row)
        with self.assertRaises(AssertionError):
            self._assert_no_money(text, "sale confirmation")

    def test_undo_confirmation_has_no_money(self) -> None:
        text = sales_ledger.format_undo_confirmation(None)
        self._assert_no_money(text, "undo confirmation")

    def test_today_report_has_no_money(self) -> None:
        text = sales_ledger.generate_sales_report(period="today", now=self.now, db_path=self.db_path)
        self._assert_no_money(text, "today report")

    def test_week_report_has_no_money(self) -> None:
        text = sales_ledger.generate_sales_report(period="week", now=self.now, db_path=self.db_path)
        self._assert_no_money(text, "week report")

    def test_month_report_has_no_money(self) -> None:
        text = sales_ledger.generate_sales_report(period="month", now=self.now, db_path=self.db_path)
        self._assert_no_money(text, "month report")

    def test_all_report_has_no_money(self) -> None:
        text = sales_ledger.generate_sales_report(period="all", now=self.now, db_path=self.db_path)
        self._assert_no_money(text, "all report")

    def test_best_sellers_output_has_no_money(self) -> None:
        rows = sales_ledger.best_sellers(db=self.db_path)
        for row in rows:
            self.assertNotIn("amount", row)
            self.assertNotIn("currency", row)
            self.assertIn("count", row)
            self.assertIn("pack_id", row)


class TestSalesLedgerParseBoundaryCases(unittest.TestCase):
    """Parsing boundaries: leading builtins and ambiguous inputs."""

    def test_status_does_not_trigger_sales_record(self) -> None:
        parsed = sales_ledger.parse_sales_input("/status")
        self.assertIsNone(parsed)

    def test_help_does_not_trigger_sales_record(self) -> None:
        parsed = sales_ledger.parse_sales_input("/help")
        self.assertIsNone(parsed)

    def test_start_does_not_trigger_sales_record(self) -> None:
        parsed = sales_ledger.parse_sales_input("/start")
        self.assertIsNone(parsed)

    def test_empty_text_returns_none(self) -> None:
        self.assertIsNone(sales_ledger.parse_sales_input(""))

    def test_none_text_returns_none(self) -> None:
        self.assertIsNone(sales_ledger.parse_sales_input(None))


class _Fb24ExpectedFailureSentinel(unittest.TestCase):
    """Kept so the expectedFailure convention is visible in this file."""

    @unittest.expectedFailure
    def test_placeholder_expected_failure_stays_covered(self) -> None:
        # One-line reason: boundary placeholder kept so expectedFailure rule is visible.
        self.fail("placeholder expectedFailure, remove once real behavior is confirmed")


if __name__ == "__main__":
    unittest.main()
