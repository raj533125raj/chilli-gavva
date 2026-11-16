"""OCH-3: secret and privacy sweep over every Telegram command and the daily report.

Fake secret values are placed in the environment (fake bot token, fake chat id,
fake provider API keys, fake worker/KV secrets) and then EVERY declared command
and Tenglish word of every plugin is sent through
``telegram_inbox.call_plugins`` against the twelve real product packs, and the
daily report texts are generated.  Assertions:

  - no reply text, caption, log record or file created during the run contains
    any of the fake secret values;
  - no email address appears anywhere except ``name@example.com``;
  - no money amount appears in any reply, log line or exported file.

Everything runs inside a temporary directory; nothing is written into the repo.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import logging
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

import daily_report
import guardrails
import gumroad_images
import marketing_auto
import mockups
import notify
import product_builder as pb
import secret_guard
import sell_proof
import store_kits
import telegram_inbox as ti

FAKE_TOKEN = str(int("1234" "5678")) + ":fake_token_och_secret_sweep_0001"
FAKE_CHAT_ID = str(int("1234" "5678"))
FAKE_SECRETS = {
    "TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
    "TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
    "GROQ_API_KEY": "gsk_fake_key_och_sweep_" + "0" * 12,
    "GEMINI_API_KEY": "AIza" + "F" * 34,
    "GEMINI_API_KEY_2": "AIza" + "E" * 34,
    "GEMINI_AUTOFIX_KEY": "AIza" + "D" * 34,
    "OPENROUTER_API_KEY": "sk-or-v1-" + "a" * 32,
    "NVIDIA_API_KEY": "nvapi-" + "x" * 25,
    "GITHUB_TOKEN": "ghp_" + "y" * 36,
    "CF_API_TOKEN": "fake_cf_api_token_och_0123456789",
    "CF_ACCOUNT_ID": "fake_cf_account_id_och_0123456789",
    "CF_KV_NAMESPACE_ID": "fake_kv_namespace_id_och_0123456789",
    "WORKER_SECRET": "fake_worker_secret_och_0123456789",
    "ALWAYS_ON_SECRET": "fake_alwayson_secret_och_0123456789",
    "ALWAYS_ON_URL": "https://fake-always-on.example.invalid/worker",
    "CONTROL_SIGNING_KEY": "fake_control_signing_key_och_0123456789",
    "FREELLMAPI_KEY": "fake_freellmapi_key_och_0123456789",
}
SECRET_VALUES = [value for value in FAKE_SECRETS.values() if len(value) >= 12]

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
SAFE_EMAIL = "name@example.com"
MONEY_RE = re.compile(
    r"(?:[₹$€£¥]\s*\d|\b(?:USD|INR|EUR|GBP|Rs\.?)\s*\d|\d[\d,.]*\s*(?:USD|INR|EUR|GBP|Rs\.?)\b)",
    re.IGNORECASE,
)

PLUGIN_NAMES = [Path(path).stem for path in sorted(ROOT.glob("tg_*.py"))]
PLUGINS = [importlib.import_module(name) for name in PLUGIN_NAMES]

# OCH-2 style list for gaps in files this worker does not own:
# (plugin_name, command) -> reason "FOR CLAUDE CODE: file why".
EXPECTED_OTHER_WORKER_GAPS = {}


class _CollectHandler(logging.Handler):
    def __init__(self, store):
        super().__init__(level=logging.DEBUG)
        self.store = store

    def emit(self, record):
        try:
            self.store.append(record.getMessage())
        except Exception:
            pass


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
        pb.truth_check_folder(
            index, folder, db_path=build_db, out_dir=str(products),
            notify_fn=lambda _text: False,
        )
        pack = products / "upload" / folder
        if not (pack / "product.zip").is_file():
            raise AssertionError(f"{kind}: upload pack has no product.zip")
        store_kits.build(pack)
        if gumroad_images.available():
            gumroad_images.for_pack(pack)
        marketing_auto.build_for_pack(pack, build_db, products_dir=products)
    return products, marketing_auto.marketing_root(products)


PACK_COMMANDS = {
    "files", "covers", "pins", "carousel", "reel", "blog", "landing", "launch",
    "kit", "seokit", "sell", "ready", "preflight", "upload", "asset",
    "seo", "lint", "faq", "versions",
}


@unittest.skipUnless(mockups.available(), "needs Pillow and poppler")
class SecretSweepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_cwd = os.getcwd()
        cls.tmp = tempfile.TemporaryDirectory(prefix="och3_secret_sweep_")
        cls.root = Path(cls.tmp.name)
        os.chdir(cls.root)
        cls.products, cls.marketing = _build_real_packs(cls.root)
        cls.db = str(cls.root / "inbox.db")
        _approvals(cls.db)
        cls.log_records = []
        cls.handler = _CollectHandler(cls.log_records)
        logging.getLogger().addHandler(cls.handler)
        cls.env = mock.patch.dict(os.environ, dict(FAKE_SECRETS, CHILLI_TEST="1"), clear=False)
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        cls.addClassCleanup(logging.getLogger().removeHandler, cls.handler)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.old_cwd)
        cls.tmp.cleanup()

    def setUp(self):
        self.rec = Recorder()
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
        self.ctx = ti.build_plugin_ctx(FAKE_TOKEN, FAKE_CHAT_ID, db_path=self.db)
        self.ctx["products_dir"] = str(self.products)
        self.ctx["marketing_dir"] = str(self.marketing)
        self.ctx["sleep_fn"] = lambda _seconds: None

    # -- helpers ---------------------------------------------------------------
    def variants(self, plugin):
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

    def call(self, plugin, text):
        self.rec.clear()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stderr), \
             mock.patch.object(ti, "build_plugin_ctx", return_value=self.ctx):
            handled, reply = ti.call_plugins(
                text, FAKE_TOKEN, FAKE_CHAT_ID, db_path=self.db, plugins=[plugin])
        if isinstance(reply, dict) and reply.get("text"):
            self.rec.texts.append(str(reply["text"]))
            self.rec.callbacks.append((reply.get("buttons"), reply.get("keyboard")))
        return handled, reply, stderr.getvalue()

    def assert_clean(self, label, text):
        self.assertNotIn("traceback", text.lower(), f"{label}: traceback")
        for value in SECRET_VALUES:
            self.assertNotIn(value, text, f"{label}: leaked a fake secret")
        for match in EMAIL_RE.finditer(text):
            # Same allowlist the repo's own secret_guard uses: example.com
            # samples and calendar event UIDs (post-...@chilligavva.local) are
            # not people; every other address is a privacy leak.
            self.assertTrue(secret_guard.SAFE_EMAIL.search(match.group(0)),
                            f"{label}: unsafe email {match.group(0)}")
        self.assertIsNone(MONEY_RE.search(text), f"{label}: leaked money: {text[:200]}")

    def _text_for(self, plugin_name, command):
        plugin = next(p for p in PLUGINS if p.__name__ == plugin_name)
        self.call(plugin, command)
        return self.rec.all_text

    # -- every command of every plugin ----------------------------------------
    def test_plugin_replies_have_no_secrets_emails_or_money(self):
        for plugin in PLUGINS:
            with self.subTest(plugin=plugin.__name__):
                for text in self.variants(plugin):
                    label = f"{plugin.__name__}: {text!r}"
                    reason = EXPECTED_OTHER_WORKER_GAPS.get((plugin.__name__, text))
                    if reason:
                        continue  # covered by the dedicated expected-failure test
                    handled, reply, stderr = self.call(plugin, text)
                    self.assertTrue(handled, f"{label} was not handled")
                    output = self.rec.all_text + "\n" + stderr
                    self.assertTrue(output.strip(), f"{label} sent nothing")
                    for chunk in self.rec.chunks:
                        self.assert_clean(label, chunk)
                    self.assert_clean(label, stderr)
                    self.assert_clean(label, "\n".join(self.log_records))

    # -- the daily report ------------------------------------------------------
    def test_daily_report_has_no_secrets_emails_or_money(self):
        db = str(self.root / "daily.db")
        guardrails.connect(db).close()
        for builder in (daily_report.daily_text, daily_report.report_text):
            with self.subTest(report=builder.__name__):
                text = builder(db_path=db)
                self.assertTrue(text.strip())
                self.assert_clean(f"daily_report.{builder.__name__}", text)

    @unittest.expectedFailure
    def test_weekly_letter_money_amount_is_a_known_gap(self):
        # FOR CLAUDE CODE: daily_report.py — the Sunday letter prints real
        # payout amounts ("Real money: $0.00 this week, $0.00 in total",
        # daily_report.py:195) on purpose; tests/test_daily_report.py asserts
        # that exact line.  The project rule says money amounts are written as
        # [amount], so this sweep documents the gap here instead of editing
        # another worker's file.
        db = str(self.root / "daily.db")
        text = daily_report.weekly_text(db_path=db)
        self.assertTrue(text.strip())
        self.assert_clean("daily_report.weekly_text", text)

    # -- every file created during the sweep -----------------------------------
    def test_exported_files_have_no_secrets_emails_or_money(self):
        text_exts = {".md", ".txt", ".json", ".csv", ".html", ".xml", ".yml", ".yaml",
                     ".py", ".js", ".db", ".sqlite", ".log"}
        scanned = 0
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in text_exts:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scanned += 1
            self.assert_clean(f"exported file {path.name}", content)
        self.assertGreater(scanned, 0, "no exported text files to scan")


if __name__ == "__main__":
    unittest.main()
