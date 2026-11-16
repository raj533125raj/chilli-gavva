"""OCG-3: /help is one grouped message built from every plugin's COMMANDS."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["CHILLI_TEST"] = "1"

import tg_menu


class HelpGroupsTests(unittest.TestCase):
    def test_menu_has_sell_packs_reports_settings_and_every_command(self):
        text = tg_menu.format_grouped_menu()
        self.assertLess(len(text), 4096)
        for group in ("Sell", "Packs", "Reports", "Settings"):
            self.assertIn(f"*{group}*:", text)
        for item in tg_menu.collect_all_commands():
            self.assertIn(item["cmd"], text)

    def test_new_plugin_command_appears_without_editing_menu(self):
        with tempfile.TemporaryDirectory(prefix="ocg_help_") as tmp:
            plugin_path = Path(tmp) / "tg_ocg_demo_plugin.py"
            plugin_path.write_text(
                "COMMANDS = [{\n"
                "    'cmd': '/ocgdemo',\n"
                "    'words': ['ocgdemo'],\n"
                "    'help_en': 'Demo command from a new plugin',\n"
                "    'help_te': 'Kotha plugin demo command',\n"
                "}]\n",
                encoding="utf-8",
            )
            sys.path.insert(0, tmp)
            try:
                with mock.patch.object(tg_menu.glob, "glob", return_value=[str(plugin_path)]):
                    text = tg_menu.format_grouped_menu()
                self.assertIn("/ocgdemo", text)
                self.assertIn("Kotha plugin demo command", text)
            finally:
                sys.path.remove(tmp)
                sys.modules.pop("tg_ocg_demo_plugin", None)


if __name__ == "__main__":
    unittest.main()
