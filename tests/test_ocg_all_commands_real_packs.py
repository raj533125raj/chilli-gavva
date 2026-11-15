"""OCG-1: every Telegram command against one real pack of every sellable type.

The test builds the twelve real product packs with the same code the robot uses
(``tools/sell_proof.spec_for`` + ``product_builder.build_product``), stages the
upload pack + marketing files, then sends every declared command and Tenglish
word through ``telegram_inbox.call_plugins`` with fake send callbacks.

Every reply must exist, stay under Telegram's 4096 character limit, keep money
and secrets out, avoid the "ardam kaaledu" fallback/tracebacks, and name a next
step (a slash command, a Tenglish action, or a button/keyboard choice).
"""
from __future__ import annotations

import contextlib
import importlib
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ["CHILLI_TEST"] = "1"
os.environ["DB_KEY"] = ""

import gumroad_images
import guardrails
import marketing_auto
import mockups
import notify
import product_builder as pb
import secret_guard
import sell_proof
import store_kits
import telegram_inbox as ti
import truth_check

OWNER = str(int("9876" "5432"))
TOKEN = str(int("1234" "5678")) + ":fake_token_for_ocg_tests"
NOW = __import__("datetime").datetime(2026, 11, 15, 10, 0, tzinfo=__import__("datetime").timezone.utc)
MAX_TELEGRAM = 4096
FALLBACK = "ardam kaaledu"

PLUGIN_NAMES = [Path(path).stem for path in sorted((ROOT).glob("tg_*.py"))]
PLUGINS = [importlib.import_module(name) for name in PLUGIN_NAMES]

MONEY_RE = re.compile(
    r"(?:[₹$€£¥]\s*\d|\b(?:USD|INR|EUR|GBP|Rs\.?)\s*\d|\d[\d,.]*\s*(?:USD|INR|EUR|GBP|Rs\.?)\b)",
    re.IGNORECASE,
)
NEXT_RE = re.compile(
    r"(?i)(?:^|[\s(`'\"])/[a-z][a-z0-9_-]*|"
    r"\bnext\b|\btaruvata\b|\btarvata\b|\bthen\b|\bpampandi\b|\bchudandi\b|"
    r"\bcheyandi\b|\brun chey\b|\bopen chey\b|\bupload\b|\bpost chey\b|"
    r"\bmark chey\b|\bready ayyaka\b|\bauto plan\b|\bcopy chesi\b|\bstart chey\b|"
    r"\bfix chesi\b|\bchoose\b|\bselect\b"
)

# Commands whose handlers accept a pack number directly. Appending "1" only to
# these keeps the matrix honest (for example "listed packs 1" is not a command).
PACK_COMMANDS = {
    "files", "covers", "pins", "carousel", "reel", "blog", "landing", "launch",
    "kit", "seokit", "sell", "ready", "preflight", "upload", "asset",
    "seo", "lint", "faq", "versions",
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
        self.groups.append(([str(path) for path in paths], str(caption)))
        return True

    @property
    def chunks(self):
        chunks = list(self.texts)
        chunks.extend(caption for _path, caption in self.documents)
        chunks.extend(caption for _path, caption in self.photos)
        chunks.extend(caption for _paths, caption in self.groups)
        for buttons, keyboard in self.callbacks:
            for keyboard_row in (buttons, keyboard):
                if not keyboard_row:
                    continue
                if isinstance(keyboard_row, dict):
                    keyboard_row = keyboard_row.get("keyboard") or []
                for row in keyboard_row:
                    if isinstance(row, (list, tuple)):
                        chunks.extend(str(label) for label in row)
                    else:
                        chunks.append(str(row))
        return chunks

    @property
    def all_text(self):
        return "\n".join(self.chunks)


def _approvals(db):
    conn = guardrails.connect(db)
    for index, kind in enumerate(sell_proof.FORMAT_ORDER, 1):
        conn.execute(
            "INSERT OR REPLACE INTO approvals "
            "(id, ts, kind, title, payload, status, review) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (index, "2026-11-15T00:00:00", "public_draft",
             f"Draft: {kind} real pack", "PRODUCT NAME: real pack", "approved", ""),
        )
    conn.commit()
    conn.close()


def _build_real_packs(root: Path):
    products = root / "exported_content" / "products"
    products.mkdir(parents=True, exist_ok=True)
    build_db = str(root / "build.db")
    for index, kind in enumerate(sell_proof.FORMAT_ORDER, 1):
        folder = pb.build_product(sell_proof.spec_for(kind), index, out_dir=str(products))
        report = pb.truth_check_folder(
            index, folder, db_path=build_db, out_dir=str(products),
            notify_fn=lambda _text: False,
        )
        if report.get("verdict") != "ready":
            raise AssertionError(f"{kind}: truth check {report.get('verdict')}: {report.get('problems')}")
        pack = products / "upload" / folder
        if not (pack / "product.zip").is_file():
            raise AssertionError(f"{kind}: upload pack has no product.zip")
        store_kits.build(pack)
        if gumroad_images.available():
            gumroad_images.for_pack(pack)
        marketing_auto.build_for_pack(pack, build_db, products_dir=products)
    return products, marketing_auto.marketing_root(products)


@unittest.skipUnless(mockups.available(), "needs Pillow and poppler")
class AllCommandsRealPacksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_cwd = os.getcwd()
        cls.tmp = tempfile.TemporaryDirectory(prefix="ocg_all_commands_")
        cls.root = Path(cls.tmp.name)
        os.chdir(cls.root)
        cls.products, cls.marketing = _build_real_packs(cls.root)
        cls.db = str(cls.root / "inbox.db")
        _approvals(cls.db)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.old_cwd)
        cls.tmp.cleanup()

    def setUp(self):
        self.rec = Recorder()
        env = mock.patch.dict(os.environ, {
            "TELEGRAM_CHAT_ID": OWNER,
            "TELEGRAM_BOT_TOKEN": TOKEN,
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
        self.ctx = ti.build_plugin_ctx(TOKEN, OWNER, db_path=self.db, now=NOW)
        self.ctx["products_dir"] = str(self.products)
        self.ctx["marketing_dir"] = str(self.marketing)
        self.ctx["sleep_fn"] = lambda _seconds: None

    def call(self, plugin, text):
        self.rec.clear()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), \
             mock.patch.object(ti, "build_plugin_ctx", return_value=self.ctx):
            handled, reply = ti.call_plugins(
                text, TOKEN, OWNER, db_path=self.db, now=NOW, plugins=[plugin],
            )
        if isinstance(reply, dict) and reply.get("text"):
            self.rec.texts.append(str(reply["text"]))
            self.rec.callbacks.append((reply.get("buttons"), reply.get("keyboard")))
        return handled, reply, stderr.getvalue()

    def assert_reply_ok(self, plugin, text, handled, reply, stderr):
        label = f"{plugin.__name__}: {text!r}"
        self.assertTrue(handled, f"{label} was not handled")
        self.assertTrue(reply is not None, f"{label} returned no reply")
        self.assertNotIn("Plugin error", stderr, label)
        output = self.rec.all_text
        self.assertTrue(output.strip(), f"{label} sent no text/files")
        for chunk in self.rec.chunks:
            self.assertLess(len(chunk), MAX_TELEGRAM, f"{label} chunk too long: {chunk[:80]!r}")
        self.assertIsNone(MONEY_RE.search(output), f"{label} leaked money: {output[:200]}")
        self.assertNotIn(TOKEN, output, label)
        self.assertEqual(secret_guard.scan_text(output, []), [], f"{label} leaked a secret")
        self.assertNotIn(FALLBACK, output.lower(), label)
        self.assertNotIn("traceback", output.lower(), label)
        self.assertTrue(NEXT_RE.search(output) or any(
            buttons or keyboard for buttons, keyboard in self.rec.callbacks
        ), f"{label} has no next step: {output[:300]}")

    @staticmethod
    def variants(plugin):
        seen = set()
        for entry in getattr(plugin, "COMMANDS", []) or []:
            if not isinstance(entry, dict):
                continue
            command = str(entry.get("cmd") or "").strip()
            if not command:
                continue
            action = command.lstrip("/").lower()
            candidates = [command]
            if action in PACK_COMMANDS:
                candidates.append(f"{command} 1")
            for word in entry.get("words") or []:
                word = str(word).strip()
                if not word:
                    continue
                candidates.append(word)
                if action in PACK_COMMANDS and len(word.split()) == 1:
                    candidates.append(f"{word} 1")
            for text in candidates:
                if text not in seen:
                    seen.add(text)
                    yield text

    def test_every_command_and_word_on_every_real_pack(self):
        for plugin in PLUGINS:
            for text in self.variants(plugin):
                with self.subTest(plugin=plugin.__name__, text=text):
                    handled, reply, stderr = self.call(plugin, text)
                    self.assert_reply_ok(plugin, text, handled, reply, stderr)


if __name__ == "__main__":
    unittest.main()
