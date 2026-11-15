"""OCD-1: end-to-end and edge-case tests for every Telegram plugin.

All tests use a temp DB, a temp products/marketing folder and fake ctx send
callbacks. The real plugin loader and real plugin handlers run; nothing reaches
the Telegram API or writes inside the repository.
"""
from __future__ import annotations

import base64
import contextlib
import datetime
import glob
import importlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import guardrails
import notify
import secret_guard
import telegram_inbox as ti
import upload_pack

OWNER = str(int("9876" "5432"))
BOT_TOKEN = str(int("1234" "5678")) + ":" + "fake_token_for_ocd_tests"
NOW = datetime.datetime(2026, 11, 14, 10, 0, tzinfo=datetime.timezone.utc)
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n1cAAAAASUVORK5CYII="
)
MONEY_RE = re.compile(
    r"(?:[₹$€£¥]\s*\d|\b(?:USD|INR|EUR|GBP|Rs\.?)\s*\d|\d[\d,.]*\s*(?:USD|INR|EUR|GBP|Rs\.?)\b)",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(
    r"\b(?:gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_\-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|"
    r"sk-(?:or-v1-)?[A-Za-z0-9\-]{32,}|\d{8,10}:AA[A-Za-z0-9_\-]{30,})\b"
)
PLUGIN_NAMES = [Path(path).stem for path in sorted(glob.glob(str(ROOT / "tg_*.py")))]
PLUGINS = [importlib.import_module(name) for name in PLUGIN_NAMES]
PLUGIN_MAP = {plugin.__name__: plugin for plugin in PLUGINS}

_ORIGINAL_CWD = os.getcwd()
_SCRATCH_CWD = None


def setUpModule():
    global _SCRATCH_CWD
    _SCRATCH_CWD = tempfile.TemporaryDirectory(prefix="ocd_cwd_")
    os.chdir(_SCRATCH_CWD.name)


def tearDownModule():
    os.chdir(_ORIGINAL_CWD)
    if _SCRATCH_CWD is not None:
        _SCRATCH_CWD.cleanup()


KNOWN_UNHANDLED = {
    ("tg_assets", "asset pampu"): "declared word returns None instead of asking for an asset number",
    ("tg_assets", "download asset"): "declared word returns None instead of asking for an asset number",
    ("tg_autoapprove", "auto approve status"): "declared status phrase returns None",
    ("tg_autoapprove", "autoapprove settings"): "declared settings phrase returns None",
    ("tg_files", "files"): "declared word returns None without a pack number",
    ("tg_files", "covers"): "declared word returns None without a pack number",
    ("tg_files", "cover variants"): "declared word returns None without a pack number",
    ("tg_files", "pins"): "declared word returns None without a pack number",
    ("tg_files", "pin images"): "declared word returns None without a pack number",
    ("tg_files", "carousel"): "declared word returns None without a pack number",
    ("tg_files", "reel"): "declared word returns None without a pack number",
    ("tg_files", "reel frames"): "declared word returns None without a pack number",
    ("tg_files", "blog"): "declared word returns None without a pack number",
    ("tg_files", "blog.md"): "declared word returns None without a pack number",
    ("tg_files", "landing"): "declared word returns None without a pack number",
    ("tg_files", "landing page"): "declared word returns None without a pack number",
    ("tg_files", "launch"): "declared word returns None without a pack number",
    ("tg_files", "launch checklist"): "declared word returns None without a pack number",
    ("tg_office", "office lo"): "declared office phrase returns None",
    ("tg_office", "office choodu"): "declared office phrase returns None",
    ("tg_posts", "/posted"): "declared command returns None; only 'posted N' works",
    ("tg_posts", "posted"): "declared word returns None; only 'posted N' works",
    ("tg_posts", "post done"): "declared word returns None; only 'posted N' works",
    ("tg_posts", "post chesa"): "declared word returns None; only 'posted N' works",
    ("tg_posts", "done post"): "declared word returns None; only 'posted N' works",
    ("tg_sales", "sales"): "declared word returns None; '/sales' works",
}


class Recorder:
    def __init__(self):
        self.texts = []
        self.documents = []
        self.photos = []
        self.groups = []
        self.callbacks = []

    def clear(self):
        self.texts.clear()
        self.documents.clear()
        self.photos.clear()
        self.groups.clear()
        self.callbacks.clear()

    def send_text(self, token, chat_id, text, buttons=None, keyboard=None):
        self.texts.append(str(text))
        self.callbacks.append((buttons, keyboard))
        return True

    def send_document(self, token, chat_id, path, caption=""):
        self.documents.append((str(path), str(caption)))
        return True

    def send_photo(self, token, chat_id, path, caption=""):
        self.photos.append((str(path), str(caption)))
        return True

    def send_media_group(self, token, chat_id, paths, caption=""):
        self.groups.append(([str(p) for p in paths], str(caption)))
        return True

    @property
    def all_text(self):
        chunks = list(self.texts)
        chunks.extend(caption for _, caption in self.documents)
        chunks.extend(caption for _, caption in self.photos)
        chunks.extend(caption for _, caption in self.groups)
        return "\n".join(chunks)


def _write_png(path):
    path.write_bytes(PNG_BYTES)


def _make_listing():
    return (
        "# Test Pack\n\n"
        "## Description\n"
        "A simple test pack with one workbook and one guide. "
        "The files are ready to use and the guide explains every step. "
        "Personal use only.\n\n"
        "## Tags\n"
        "printable, organizer, digital download\n\n"
        "## What you get\n"
        "- One workbook\n"
        "- One guide\n"
    )


def _make_fixture(products, marketing):
    pack = products / "1-test-pack"
    pack.mkdir(parents=True)
    listing_md = _make_listing()
    (pack / "listing.md").write_text(listing_md, encoding="utf-8")
    (pack / "listing.txt").write_text(upload_pack._listing(listing_md), encoding="utf-8")
    (pack / "checklist.txt").write_text("Checklist\n- Price: owner decides\n", encoding="utf-8")
    (pack / "README.md").write_text("# Test Pack\n\nOpen the guide first.\n", encoding="utf-8")
    _write_png(pack / "cover.png")
    _write_png(pack / "preview-1.png")
    with zipfile.ZipFile(pack / "product.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md", "# Test Pack\n")
        archive.writestr("HOW_TO_IMPORT.md", "# How to use\n")
    kits = pack / "store-kits"
    kits.mkdir()
    (kits / "gumroad.txt").write_text(
        "=== GUMROAD LISTING KIT ===\nTitle: Test Pack\nPrice: owner decides\n", encoding="utf-8")
    mktg = marketing / "1-test-pack"
    for folder in ("covers", "pins", "carousel", "reel", "landing"):
        (mktg / folder).mkdir(parents=True, exist_ok=True)
    _write_png(mktg / "covers" / "cover-a.png")
    _write_png(mktg / "pins" / "pin-1.png")
    _write_png(mktg / "carousel" / "slide-1.png")
    _write_png(mktg / "reel" / "frame-1.png")
    (mktg / "pins" / "ab_titles.txt").write_text(
        "1. Test title one\n2. Test title two\n3. Test title three\n", encoding="utf-8")
    (mktg / "carousel" / "caption.txt").write_text("Carousel caption", encoding="utf-8")
    (mktg / "reel" / "reel.txt").write_text("Reel caption", encoding="utf-8")
    (mktg / "blog.md").write_text("# Blog\n", encoding="utf-8")
    (mktg / "LAUNCH.md").write_text("# Launch\n", encoding="utf-8")
    (mktg / "landing" / "index.html").write_text("<html>Test</html>\n", encoding="utf-8")
    return pack


class PluginMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ocd_plugins_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = str(self.root / "agent_data.db")
        guardrails.connect(self.db).close()
        self.products = self.root / "products"
        self.marketing = self.root / "marketing"
        self.products.mkdir()
        self.marketing.mkdir()
        self.pack = _make_fixture(self.products, self.marketing)
        self.rec = Recorder()
        env = mock.patch.dict(os.environ, {
            "TELEGRAM_CHAT_ID": OWNER,
            "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
            "CHILLI_PRODUCTS_DIR": str(self.products),
            "CHILLI_MARKETING_DIR": str(self.marketing),
            "CHILLI_TEST": "1",
        }, clear=False)
        env.start()
        self.addCleanup(env.stop)
        cfg = mock.patch.object(notify, "_cfg", return_value={})
        cfg.start()
        self.addCleanup(cfg.stop)
        for name, func in (
            ("send_text", self.rec.send_text),
            ("send_document", self.rec.send_document),
            ("send_photo", self.rec.send_photo),
            ("send_media_group", self.rec.send_media_group),
        ):
            patcher = mock.patch.object(ti, name, func)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ctx = ti.build_plugin_ctx(BOT_TOKEN, OWNER, db_path=self.db, now=NOW)
        self.ctx["products_dir"] = str(self.products)
        self.ctx["marketing_dir"] = str(self.marketing)
        self.ctx["sleep_fn"] = lambda _seconds: None

    def assert_safe_output(self):
        output = self.rec.all_text
        self.assertIsNone(MONEY_RE.search(output), output)
        self.assertIsNone(TOKEN_RE.search(output), output)
        self.assertNotIn(BOT_TOKEN, output)
        self.assertEqual(secret_guard.scan_text(output, []), [])

    def call_loader(self, text, plugins=None):
        with mock.patch.object(ti, "build_plugin_ctx", return_value=self.ctx):
            return ti.call_plugins(text, BOT_TOKEN, OWNER, db_path=self.db, now=NOW,
                                   plugins=PLUGINS if plugins is None else plugins)

    def call_plugin(self, plugin, text):
        self.rec.clear()
        result = plugin.handle(text, self.ctx)
        self.assert_safe_output()
        return result

    def process_updates(self, updates, owner=OWNER):
        stats = ti.new_stats()
        with mock.patch.object(ti, "fetch_updates", return_value=updates), \
             mock.patch.object(ti, "acknowledge_update", return_value=None), \
             mock.patch.object(ti, "build_plugin_ctx", return_value=self.ctx):
            handled = ti.process_updates(BOT_TOKEN, owner, db_path=self.db, stats=stats)
        return handled, stats


def _method_for(plugin, text, label, key):
    def test(self):
        result = self.call_plugin(plugin, text)
        self.assertIsNotNone(result, f"{label} not handled by {plugin.__name__}")

    test.__name__ = "test_" + key
    reason = KNOWN_UNHANDLED.get((plugin.__name__, text))
    if reason:
        test.__doc__ = reason
        return unittest.expectedFailure(test)
    return test


def _slug(value):
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return cleaned[:60] or "empty"


def _make_plugin_class(plugin):
    attrs = {"PLUGIN": plugin}
    for idx, entry in enumerate(getattr(plugin, "COMMANDS", []), 1):
        command = str(entry.get("cmd", ""))
        key = f"test_{idx:03d}_cmd_{_slug(command)}"
        attrs[key] = _method_for(plugin, command, "command", key)
        for word_idx, word in enumerate(entry.get("words") or [], 1):
            word = str(word)
            key = f"test_{idx:03d}_word_{word_idx:02d}_{_slug(word)}"
            attrs[key] = _method_for(plugin, word, "documented phrase", key)
    return type(f"TestPlugin_{plugin.__name__}", (PluginMixin, unittest.TestCase), attrs)


for _plugin in PLUGINS:
    globals()[f"TestPlugin_{_plugin.__name__}"] = _make_plugin_class(_plugin)


class TestPluginBehaviour(PluginMixin, unittest.TestCase):
    def test_all_plugins_have_commands_and_handle(self):
        self.assertGreaterEqual(len(PLUGINS), 19)
        for plugin in PLUGINS:
            self.assertTrue(callable(plugin.handle), plugin.__name__)
            self.assertIsInstance(plugin.COMMANDS, list)

    def test_discover_plugins_sorted_and_complete(self):
        names = [plugin.__name__ for plugin in ti.discover_plugins()]
        self.assertEqual(names, sorted(plugin.__name__ for plugin in PLUGINS))
        self.assertEqual(names, sorted(PLUGIN_NAMES))

    def test_loader_reaches_every_declared_command_without_known_bug(self):
        for plugin in PLUGINS:
            for entry in plugin.COMMANDS:
                command = entry["cmd"]
                if (plugin.__name__, command) in KNOWN_UNHANDLED:
                    continue
                with self.subTest(plugin=plugin.__name__, command=command):
                    self.rec.clear()
                    handled, _ = self.call_loader(command, [plugin])
                    self.assertTrue(handled, command)
                    self.assert_safe_output()

    @unittest.expectedFailure
    def test_loader_handles_bare_posted_command(self):
        self.rec.clear()
        handled, _ = self.call_loader("/posted", [PLUGINS[PLUGIN_NAMES.index("tg_posts")]])
        self.assertTrue(handled)

    def test_loader_reaches_every_documented_phrase_without_known_bug(self):
        for plugin in PLUGINS:
            for entry in plugin.COMMANDS:
                for word in entry.get("words") or []:
                    if (plugin.__name__, word) in KNOWN_UNHANDLED:
                        continue
                    with self.subTest(plugin=plugin.__name__, word=word):
                        self.rec.clear()
                        handled, _ = self.call_loader(word, [plugin])
                        self.assertTrue(handled, word)
                        self.assert_safe_output()

    def test_non_owner_message_is_ignored_by_process_updates(self):
        self.rec.clear()
        updates = [{"update_id": 1, "message": {"chat": {"id": int("1111" "2222")}, "text": "/menu"}}]
        handled, stats = self.process_updates(updates)
        self.assertEqual(handled, 0)
        self.assertEqual(stats["skipped"]["not owner chat"], 1)
        self.assertEqual(self.rec.texts, [])

    def test_duplicate_owner_messages_are_answered_once(self):
        self.rec.clear()
        updates = [
            {"update_id": 10, "message": {"chat": {"id": int(OWNER)}, "text": "/menu"}},
            {"update_id": 11, "message": {"chat": {"id": int(OWNER)}, "text": "/menu"}},
        ]
        handled, _stats = self.process_updates(updates)
        self.assertEqual(handled, 2)
        self.assertEqual(len(self.rec.texts), 1)
        self.assertIn("answered 2 messages", self.rec.texts[0])

    def test_owner_only_plugins_reject_non_owner_directly(self):
        self.ctx["chat_id"] = str(int("1111" "2222"))
        for plugin, text in (
            ("tg_dbcare", "/dbcare"),
            ("tg_keyboard", "/decide"),
            ("tg_office", "/office"),
            ("tg_seo", "/seokit 1"),
        ):
            with self.subTest(plugin=plugin):
                self.assertIsNone(PLUGIN_MAP[plugin].handle(text, self.ctx))

    def test_empty_db_decide_reports_no_drafts(self):
        self.rec.clear()
        result = self.call_plugin(PLUGIN_MAP["tg_keyboard"], "/decide")
        self.assertEqual(result, {"handled": True})
        self.assertIn("Waiting drafts em levu", self.rec.all_text)

    def test_huge_db_1000_drafts_decide_stays_bounded(self):
        conn = guardrails.connect(self.db)
        conn.executemany(
            "INSERT INTO approvals (ts, kind, title, payload, status) VALUES (?, 'public_draft', ?, '{}', 'pending')",
            [("2026-11-14T00:00:00", f"Draft {index}") for index in range(1000)],
        )
        conn.commit()
        conn.close()
        self.assertEqual(len(guardrails.pending(self.db)), 1000)
        self.rec.clear()
        result = self.call_plugin(PLUGIN_MAP["tg_keyboard"], "/decide")
        self.assertEqual(result, {"handled": True})
        self.assertLessEqual(len(self.rec.texts), 2)
        self.assertIn("Drafts decision mode on", self.rec.all_text)

    def test_quiet_hours_blocks_post_today(self):
        state = {"quiet_hours": {"enabled": True, "start": 14, "end": 16}}
        ti.set_state("owner_settings", json.dumps(state), self.db)
        quiet, reason = PLUGIN_MAP["tg_posts"].is_quiet_hours(self.ctx)
        self.assertTrue(quiet, reason)
        self.rec.clear()
        result = PLUGIN_MAP["tg_posts"].handle("/post today", self.ctx)
        self.assertIn("Quiet hours", result["text"])
        self.assertEqual(self.rec.texts, [])

    def test_quiet_hours_can_be_off(self):
        state = {"quiet_hours": {"enabled": False, "start": 14, "end": 16}}
        ti.set_state("owner_settings", json.dumps(state), self.db)
        quiet, _reason = PLUGIN_MAP["tg_posts"].is_quiet_hours(self.ctx)
        self.assertFalse(quiet)

    @unittest.expectedFailure
    def test_known_reports_lint_malformed_pack_regex(self):
        """tg_reports /lint builds an unsanitized regex from a malformed pack argument."""
        self.rec.clear()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = PLUGIN_MAP["tg_reports"].handle("/lint [[[", self.ctx)
        self.assertIsNotNone(result)

    def test_loader_survives_plugin_exception(self):
        class Bad:
            __name__ = "tg_bad"

            @staticmethod
            def handle(text, ctx):
                raise RuntimeError("boom")

        class Good:
            __name__ = "tg_good"

            @staticmethod
            def handle(text, ctx):
                return {"text": "recovered"}

        Bad.__name__ = "tg_bad"
        Good.__name__ = "tg_good"
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            handled, result = ti.call_plugins("hello", BOT_TOKEN, OWNER, db_path=self.db,
                                              now=NOW, plugins=[Bad, Good])
        self.assertTrue(handled)
        self.assertEqual(result, {"text": "recovered"})
        self.assertIn("Plugin error in tg_bad: boom", stderr.getvalue())

    def test_two_plugins_same_text_first_sorted_plugin_wins(self):
        class A:
            __name__ = "tg_a"

            @staticmethod
            def handle(text, ctx):
                return {"text": "from A"}

        class B:
            __name__ = "tg_b"

            @staticmethod
            def handle(text, ctx):
                return {"text": "from B"}

        handled, result = ti.call_plugins("same", BOT_TOKEN, OWNER, db_path=self.db,
                                          now=NOW, plugins=[A, B])
        self.assertTrue(handled)
        self.assertEqual(result, {"text": "from A"})

    def test_menu_fallback_does_not_hide_later_plugin(self):
        class Menu:
            __name__ = "tg_menu"

            @staticmethod
            def handle(text, ctx):
                return {"text": "Meeru cheppindi ardam kaaledu. try /menu"}

        class Settings:
            __name__ = "tg_settings"

            @staticmethod
            def handle(text, ctx):
                return {"text": "SETTINGS"}

        handled, result = ti.call_plugins("yes", BOT_TOKEN, OWNER, db_path=self.db,
                                          now=NOW, plugins=[Menu, Settings])
        self.assertTrue(handled)
        self.assertEqual(result, {"text": "SETTINGS"})

    def test_emoji_telugu_and_long_text_never_crash(self):
        for text in ("🚀🙂", "నమస్కారం, ఏం చేయాలి?", "a" * 5000, "/" * 300, "pack 999 files pampu"):
            with self.subTest(text=text[:20]):
                self.rec.clear()
                self.call_loader(text)
                self.assert_safe_output()

    def test_malformed_commands_never_crash(self):
        for text in ("/", "///", "/unknown@bot", "/files 999", "/sell 999", "/ready 999", "/seokit 999"):
            with self.subTest(text=text):
                self.rec.clear()
                self.call_loader(text)
                self.assert_safe_output()

    def test_files_command_sends_product_cover_and_listing(self):
        self.rec.clear()
        result = self.call_plugin(PLUGIN_MAP["tg_files"], "/files 1")
        self.assertEqual(result, {"handled": True})
        names = [Path(path).name for path, _caption in self.rec.documents]
        self.assertIn("product.zip", names)
        self.assertIn("cover.png", names)
        self.assertIn("listing.txt", names)

    def test_cover_variants_are_sent_as_media_group(self):
        self.rec.clear()
        result = self.call_plugin(PLUGIN_MAP["tg_files"], "/covers 1")
        self.assertEqual(result, {"handled": True})
        self.assertEqual(len(self.rec.groups), 1)

    def test_pins_send_media_and_ab_titles(self):
        self.rec.clear()
        result = self.call_plugin(PLUGIN_MAP["tg_files"], "/pins 1")
        self.assertEqual(result, {"handled": True})
        self.assertEqual(len(self.rec.groups), 1)
        self.assertIn("A/B pin titles", self.rec.all_text)

    def test_carousel_and_reel_send_media(self):
        for command, expected in (("/carousel 1", "Carousel Caption"), ("/reel 1", "make_reel")):
            with self.subTest(command=command):
                self.rec.clear()
                result = self.call_plugin(PLUGIN_MAP["tg_files"], command)
                self.assertEqual(result, {"handled": True})
                self.assertEqual(len(self.rec.groups), 1)
                self.assertIn(expected, self.rec.all_text)

    def test_blog_landing_and_launch_send_documents(self):
        for command in ("/blog 1", "/landing 1", "/launch 1"):
            with self.subTest(command=command):
                self.rec.clear()
                result = self.call_plugin(PLUGIN_MAP["tg_files"], command)
                self.assertEqual(result, {"handled": True})
                self.assertGreaterEqual(len(self.rec.documents), 1)

    def test_kit_and_seokit_send_documents(self):
        for plugin, command in (("tg_kits", "/kit 1 gumroad"), ("tg_seo", "/seokit 1")):
            with self.subTest(command=command):
                self.rec.clear()
                result = self.call_plugin(PLUGIN_MAP[plugin], command)
                self.assertEqual(result, {"handled": True})
                self.assertGreaterEqual(len(self.rec.documents), 1)

    def test_ready_and_sell_cards(self):
        for plugin, command in (("tg_ready", "/ready 1"), ("tg_sell", "/sell 1")):
            with self.subTest(command=command):
                self.rec.clear()
                result = self.call_plugin(PLUGIN_MAP[plugin], command)
                self.assertTrue(result)
                self.assertTrue(self.rec.texts or (isinstance(result, dict) and result.get("text")))

    def test_listed_save_and_list(self):
        plugin = PLUGIN_MAP["tg_listed"]
        conn = guardrails.connect(self.db)
        conn.execute(
            "INSERT INTO approvals (id, ts, kind, title, payload, status) "
            "VALUES (1, '2026-11-14T00:00:00', 'public_draft', 'Test Pack', '{}', 'approved')"
        )
        conn.commit()
        conn.close()
        self.rec.clear()
        result = self.call_plugin(plugin, "/listed 1 https://example.gumroad.com/l/p1")
        self.assertIn("Gumroad lo listed", result["text"])
        self.rec.clear()
        result = self.call_plugin(plugin, "/listed")
        self.assertIn("Test Pack", result["text"])

    def test_sales_report_and_undo(self):
        plugin = PLUGIN_MAP["tg_sales"]
        for command in ("/sales today", "/sales undo"):
            with self.subTest(command=command):
                self.rec.clear()
                result = self.call_plugin(plugin, command)
                self.assertTrue(result)
                self.assertTrue(self.rec.all_text or result.get("text"))

    def test_settings_and_autoapprove_cards(self):
        for plugin, command in (("tg_settings", "/settings"), ("tg_autoapprove", "/autoapprove")):
            with self.subTest(command=command):
                self.rec.clear()
                result = self.call_plugin(PLUGIN_MAP[plugin], command)
                self.assertTrue(result)
                self.assertTrue(self.rec.texts or result.get("text"))

    def test_post_preview_and_posted_number(self):
        for command in ("/post tomorrow", "/posted 1"):
            with self.subTest(command=command):
                self.rec.clear()
                result = self.call_plugin(PLUGIN_MAP["tg_posts"], command)
                self.assertTrue(result)
                self.assertTrue(self.rec.texts or result.get("text"))

    def test_health_logs_and_undo_cards(self):
        for command in ("/health", "/logs", "/undo"):
            with self.subTest(command=command):
                self.rec.clear()
                result = self.call_plugin(PLUGIN_MAP["tg_health"], command)
                self.assertTrue(result)
                self.assertTrue(self.rec.texts or result.get("text"))

    def test_dbcare_and_office_cards(self):
        for plugin, command in (("tg_dbcare", "/dbcare"), ("tg_office", "/office")):
            with self.subTest(command=command):
                self.rec.clear()
                result = self.call_plugin(PLUGIN_MAP[plugin], command)
                self.assertTrue(result)
                self.assertTrue(self.rec.texts or result.get("text"))

    def test_reportpdf_builds_document(self):
        self.rec.clear()
        result = self.call_plugin(PLUGIN_MAP["tg_reportpdf"], "/reportpdf")
        self.assertEqual(result, {"handled": True})
        self.assertGreaterEqual(len(self.rec.documents), 1)

    def test_ops_dashboard_and_calendar_documents(self):
        reports = PLUGIN_MAP["tg_reports"]
        for command in ("/dashboard", "/calendar"):
            with self.subTest(command=command):
                self.rec.clear()
                result = self.call_plugin(reports, command)
                self.assertEqual(result, {"handled": True})
                self.assertGreaterEqual(len(self.rec.documents), 1)

    def test_reports_pack_and_global_commands(self):
        reports = PLUGIN_MAP["tg_reports"]
        for command in ("/ops", "/seo 1", "/lint 1", "/faq 1", "/dup", "/keywords",
                        "/versions 1", "/ideas", "/boards"):
            with self.subTest(command=command):
                self.rec.clear()
                result = self.call_plugin(reports, command)
                self.assertTrue(result)
                self.assertTrue(self.rec.texts or result.get("text"))

    def test_menu_and_help(self):
        for command in ("/menu", "/help /files"):
            with self.subTest(command=command):
                self.rec.clear()
                result = self.call_plugin(PLUGIN_MAP["tg_menu"], command)
                self.assertTrue(result)
                self.assertTrue(self.rec.texts or result.get("text"))

    def test_talk_examples(self):
        self.rec.clear()
        result = self.call_plugin(PLUGIN_MAP["tg_talk"], "/talk")
        self.assertIn("Udaharanalu", result["text"])


if __name__ == "__main__":
    unittest.main()
