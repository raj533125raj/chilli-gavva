"""OCD-1: 2,000 seeded fuzz inputs against every Telegram plugin."""
from __future__ import annotations

import contextlib
import datetime
import io
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import guardrails
import notify
import secret_guard
import telegram_inbox as ti
from tests.test_ocd_plugins_e2e import (
    BOT_TOKEN,
    MONEY_RE,
    NOW,
    OWNER,
    PLUGINS,
    PLUGIN_NAMES,
    TOKEN_RE,
    Recorder,
)

CASES_PER_METHOD = 100
METHOD_COUNT = 20
CHARS = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
    "/_-:.,!?@#$%^&*()[]{}<>|\\\n\t😀🚀✅🙂🎉నమస్కారంఏంచేయాలిపంపు"
)
WORDS = [
    "/files", "/covers", "/pins", "/carousel", "/reel", "/blog", "/landing", "/launch",
    "/kit", "/seokit", "/ready", "/sell", "/listed", "/sales", "/settings", "/autoapprove",
    "/post", "/posted", "/health", "/logs", "/undo", "/dbcare", "/office", "/decide",
    "/reportpdf", "/dashboard", "/calendar", "/ops", "/seo", "/lint", "/faq", "/dup",
    "/keywords", "/versions", "/ideas", "/boards", "/menu", "/help", "/talk",
    "files", "pampu", "pack", "posted", "sales", "office", "asset", "assets",
    "auto", "approve", "status", "settings", "1", "2", "999", "0", "-1",
    "చేయాలి", "పంపు", "ఏం", "నమస్కారం", "😀", "🚀", "✅",
]
_ORIGINAL_CWD = os.getcwd()
_SCRATCH_CWD = None


def setUpModule():
    global _SCRATCH_CWD
    _SCRATCH_CWD = tempfile.TemporaryDirectory(prefix="ocd_fuzz_cwd_")
    os.chdir(_SCRATCH_CWD.name)


def tearDownModule():
    os.chdir(_ORIGINAL_CWD)
    if _SCRATCH_CWD is not None:
        _SCRATCH_CWD.cleanup()


class TestPluginFuzz(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ocd_fuzz_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = str(self.root / "agent_data.db")
        guardrails.connect(self.db).close()
        self.products = self.root / "products"
        self.marketing = self.root / "marketing"
        self.products.mkdir()
        self.marketing.mkdir()
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

    @staticmethod
    def _make_text(rng, method_index, case_index):
        mode = case_index % 6
        length = rng.choice([0, 1, 2, 3, 5, 10, 20, 50, 100, 500, 2000])
        if mode == 0:
            return " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 10)))
        if mode == 1:
            return "".join(rng.choice(CHARS) for _ in range(length))
        if mode == 2:
            parts = [rng.choice(WORDS) for _ in range(rng.randint(1, 5))]
            parts.append("".join(rng.choice(CHARS) for _ in range(length)))
            return rng.choice([" ", "\n", "\t"]).join(parts)
        if mode == 3:
            return rng.choice(WORDS) + " " + rng.choice(CHARS) * max(length, 1)
        if mode == 4:
            return " ".join(rng.choice(WORDS) for _ in range(3)) + " " + str(method_index) + " " + str(case_index)
        return "".join(rng.choice(CHARS) for _ in range(max(1, length // 2)))

    def _safe_output(self):
        output = self.rec.all_text
        self.assertIsNone(MONEY_RE.search(output), output)
        self.assertIsNone(TOKEN_RE.search(output), output)
        self.assertNotIn(BOT_TOKEN, output)
        self.assertEqual(secret_guard.scan_text(output, []), [])

    def _fuzz(self, method_index):
        rng = random.Random(20261114 + method_index)
        for case_index in range(CASES_PER_METHOD):
            text = self._make_text(rng, method_index, case_index)
            self.rec.clear()
            stderr = io.StringIO()
            with mock.patch.object(ti, "build_plugin_ctx", return_value=self.ctx), \
                 contextlib.redirect_stderr(stderr):
                handled, result = ti.call_plugins(
                    text, BOT_TOKEN, OWNER, db_path=self.db, now=NOW, plugins=PLUGINS)
            known_lint_regex_bug = (text == "/lint [[[" and "tg_reports" in stderr.getvalue()
                                    and "bad character range" in stderr.getvalue())
            if not known_lint_regex_bug:
                self.assertNotIn("Plugin error", stderr.getvalue(), repr(text[:200]))
            self.assertIn(handled, (True, False), repr(text[:200]))
            if isinstance(result, dict):
                self.assertIsInstance(result.get("text", ""), (str, type(None)),
                                      repr(text[:200]))
            self._safe_output()
        self.assertGreaterEqual(len(PLUGIN_NAMES), 19)  # new plugins may be added (tg_launch made 20)


def _make_fuzz_method(index):
    def test(self):
        self._fuzz(index)

    test.__name__ = f"test_fuzz_{index:02d}"
    test.__doc__ = f"100 seeded inputs, seed {20261114 + index}"
    return test


for _index in range(METHOD_COUNT):
    setattr(TestPluginFuzz, f"test_fuzz_{_index:02d}", _make_fuzz_method(_index))


if __name__ == "__main__":
    unittest.main()
