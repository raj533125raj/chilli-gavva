"""CXG-2: every owned command and alias through the real plugin dispatcher.

The sample database, packs, generated documents and working directory are
temporary. Transport callbacks record actual replies; no Telegram call occurs.
"""
import os
import re
import unittest
from unittest import mock

import secret_guard
import telegram_inbox as ti
from tests.test_ocd_plugins_e2e import PluginMixin, PLUGINS, OWNER, BOT_TOKEN, NOW, MONEY_RE


EXCLUDED = {"tg_bundle", "tg_preflight", "tg_upload", "tg_autoapprove",
            "tg_teams", "tg_posts", "tg_health"}
OWNED = [plugin for plugin in PLUGINS if plugin.__name__ not in EXCLUDED]
PYTHON_WORDS = re.compile(r"\b(?:None|True|False|dict|traceback|\w+Error|\w+Exception)\b", re.I)
LOCAL_PATH = re.compile(r"(?:/Users/|/private/|/tmp/|[A-Za-z]:\\)")


class ReplyStyleFixture(PluginMixin):
    def setUp(self):
        super().setUp()
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        import product_builder
        import upload_pack
        for module, name, value in ((product_builder, "PRODUCTS_DIR", str(self.products)),
                                    (upload_pack, "PRODUCTS_DIR", str(self.products))):
            if hasattr(module, name):
                patcher = mock.patch.object(module, name, value)
                patcher.start()
                self.addCleanup(patcher.stop)

    def assert_reply_style(self, value, allow_money=False):
        value = str(value)
        self.assertTrue(value.strip(), "empty owner reply")
        lines = value.splitlines()
        self.assertLessEqual(len(lines), 15, value)
        self.assertTrue(lines[-1].startswith("Next:"), value)
        self.assertEqual(sum(line.startswith("Next:") for line in lines), 1, value)
        self.assertNotRegex(value, PYTHON_WORDS)
        if not allow_money:
            self.assertNotRegex(value, MONEY_RE)
        self.assertNotRegex(value, LOCAL_PATH)
        self.assertEqual(secret_guard.scan_text(value), [], value)
        self.assertNotIn(BOT_TOKEN, value)

    def run_command(self, command, plugins=None, allow_money=False):
        self.rec.clear()
        with mock.patch.object(ti, "build_plugin_ctx", return_value=self.ctx), \
             mock.patch.object(ti, "record_plugin_crash", side_effect=AssertionError("plugin crashed")):
            handled, result = ti.call_plugins(command, BOT_TOKEN, OWNER, db_path=self.db,
                                               now=NOW, plugins=plugins)
        self.assertTrue(handled, command)
        self.assertIsInstance(result, dict, command)
        output = list(self.rec.texts)
        output += [caption for _, caption in self.rec.documents + self.rec.photos + self.rec.groups]
        if "text" in result:
            output.append(result["text"])
        self.assertTrue(output, command)
        for value in output:
            with self.subTest(reply=value):
                self.assert_reply_style(value, allow_money=allow_money)
        return result


def command_test(plugin, command, full_loader):
    def test(self):
        self.run_command(command, plugins=None if full_loader else [plugin],
                         allow_money=plugin.__name__ == "tg_sales")
    return test


class EveryOwnedCommandTests(ReplyStyleFixture, unittest.TestCase):
    def test_discovery_covers_every_owned_handler(self):
        discovered = {plugin.__name__ for plugin in ti.discover_plugins()}
        self.assertEqual(discovered - EXCLUDED, {plugin.__name__ for plugin in OWNED})
        self.assertEqual(len(OWNED), 24)
        self.assertIn("tg_selltest", {plugin.__name__ for plugin in OWNED})
        self.assertIn("tg_site", {plugin.__name__ for plugin in OWNED})
        self.assertIn("tg_selltest_doctor", {plugin.__name__ for plugin in OWNED})
        for name in ('tg_restore','tg_storekit','tg_buyercheck','tg_listnext'):
            self.assertIn(name,{plugin.__name__ for plugin in OWNED})


for plugin in OWNED:
    for index, entry in enumerate(plugin.COMMANDS):
        for number, command in enumerate([entry["cmd"]] + entry.get("words", [])):
            name = f"test_{plugin.__name__}_{index:02d}_{number:02d}"
            setattr(EveryOwnedCommandTests, name + "_isolated_dispatch", command_test(plugin, command, False))
        setattr(EveryOwnedCommandTests, f"test_{plugin.__name__}_{index:02d}_full_loader",
                command_test(plugin, entry["cmd"], True))


class PackReplyStyleTests(ReplyStyleFixture, unittest.TestCase):
    def test_next_step_does_not_confuse_a_help_instruction_with_a_ready_pack(self):
        import tg_talk
        with mock.patch.object(ti,'handle_command',wraps=ti.handle_command) as builtin:
            reply=self.run_command('em cheyali ippudu?',plugins=[tg_talk])
        self.assertIn('Ee roju posts:',reply['text'])
        self.assertNotIn('Ippudu ee packs ni Gumroad lo pettochu:',reply['text'])
        self.assertTrue(any(call.args[0]=='today' for call in builtin.call_args_list))

    def test_real_sample_pack_commands(self):
        for command in ("/files 1", "/covers 1", "/pins 1", "/carousel 1", "/reel 1", "/blog 1",
                        "/landing 1", "launch checklist 1", "/launch 1", "/kit 1 gumroad",
                        "/ready 1", "/sell 1", "/seo 1", "/lint 1", "/faq 1", "/versions 1",
                        "/seokit 1", "/help /files", "namaste", "/unknown_cxg_command"):
            with self.subTest(command=command):
                self.run_command(command)

    def test_missing_pack_commands(self):
        for command in ("/files 999", "/ready 999", "/sell 999", "/launch 999",
                        "/kit 999 gumroad", "/seo 999", "/lint 999", "/seokit 999"):
            with self.subTest(command=command):
                self.run_command(command)

    def test_private_sale_amount_is_confirmed_only_by_sales(self):
        result = self.run_command("sale pack 1 Gumroad " + chr(36) + str(12),
                                  plugins=[p for p in OWNED if p.__name__ == "tg_sales"],
                                  allow_money=True)
        self.assertIn("USD " + str(12), result["text"])


if __name__ == "__main__":
    unittest.main()
