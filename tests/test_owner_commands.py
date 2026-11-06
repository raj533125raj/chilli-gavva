"""Unit tests for owner_commands.py parser.

Tests offline natural language parsing of voice and typed commands in English
and Tenglish for the owner.

Includes:
- At least 80 distinct example sentences covering both languages.
- Number argument parsing (digits and number words).
- Risky intents requiring confirmation.
- Fallback for unknown commands with Tenglish hint.
- Zero network access.
- Clearing GITHUB_* env variables in setUp via mock.patch.dict.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

import owner_commands


class TestOwnerCommands(unittest.TestCase):
    def setUp(self):
        # Clear all GITHUB_* env variables per repository guidelines
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
        patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    # 1. approve_draft
    def test_approve_draft_english(self):
        cases = [
            ("approve draft 59", 59),
            ("approve 42", 42),
            ("accept draft 10", 10),
            ("approve draft five", 5),
        ]
        for text, expected_n in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "approve_draft", f"Failed on: {text}")
            self.assertEqual(res["args"].get("n"), expected_n)
            self.assertTrue(res["needs_confirm"])
            self.assertGreater(res["confidence"], 0.7)

    def test_approve_draft_tenglish(self):
        cases = [
            ("59 approve chey", 59),
            ("draft 59 ni aamodinchu", 59),
            ("okati approve cheyyi", 1),
            ("draft 7 accept chey", 7),
        ]
        for text, expected_n in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "approve_draft", f"Failed on: {text}")
            self.assertEqual(res["args"].get("n"), expected_n)
            self.assertTrue(res["needs_confirm"])

    # 2. reject_draft
    def test_reject_draft_english(self):
        cases = [
            ("reject draft 61, too generic", 61, "too generic"),
            ("reject 12 reason weak niche", 12, "weak niche"),
            ("reject 44 because duplicate", 44, "duplicate"),
            ("reject draft 15", 15, ""),
        ]
        for text, exp_n, exp_reason in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "reject_draft", f"Failed on: {text}")
            self.assertEqual(res["args"].get("n"), exp_n)
            if exp_reason:
                self.assertIn(exp_reason, res["args"].get("reason", ""))
            self.assertTrue(res["needs_confirm"])

    def test_reject_draft_tenglish(self):
        cases = [
            ("draft 61 reject chey, too generic", 61, "too generic"),
            ("61 vaddu reject cheyyi reason weak niche", 61, "weak niche"),
            ("draft 12 tirooskarinchu, duplicate", 12, "duplicate"),
            ("draft 9 vaddu reject chey", 9, ""),
        ]
        for text, exp_n, exp_reason in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "reject_draft", f"Failed on: {text}")
            self.assertEqual(res["args"].get("n"), exp_n)
            if exp_reason:
                self.assertIn(exp_reason, res["args"].get("reason", ""))
            self.assertTrue(res["needs_confirm"])

    # 3. list_drafts
    def test_list_drafts(self):
        cases = [
            "list drafts",
            "show drafts waiting",
            "view pending drafts",
            "drafts chupinchu",
            "pending drafts list chey",
            "enni drafts unnay",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "list_drafts", f"Failed on: {text}")
            self.assertFalse(res["needs_confirm"])

    # 4. show_status
    def test_show_status(self):
        cases = [
            "show status",
            "system status",
            "robot status report",
            "status chupinchu",
            "robot status enti",
            "office ela nadustondi",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "show_status", f"Failed on: {text}")
            self.assertFalse(res["needs_confirm"])

    # 5. show_money
    def test_show_money(self):
        cases = [
            "show money",
            "check earnings",
            "show revenue balance",
            "paisalu chupinchu",
            "entha vachindi",
            "entha sampadincham",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "show_money", f"Failed on: {text}")
            self.assertFalse(res["needs_confirm"])

    # 6. pause_all
    def test_pause_all(self):
        cases = [
            "pause all",
            "stop all",
            "pause everything",
            "anni aapu",
            "mottam aapandi",
            "anni pause chey",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "pause_all", f"Failed on: {text}")
            self.assertTrue(res["needs_confirm"])

    # 7. resume_all
    def test_resume_all(self):
        cases = [
            "resume all",
            "unpause",
            "start all tasks",
            "anni modalu pettu",
            "malli start chey",
            "anni resume chey",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "resume_all", f"Failed on: {text}")
            self.assertTrue(res["needs_confirm"])

    # 8. run_now
    def test_run_now(self):
        cases = [
            "run now",
            "execute now",
            "trigger run immediately",
            "ippude run chey",
            "cycle ippude start chey",
            "ventane run chey",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "run_now", f"Failed on: {text}")
            self.assertTrue(res["needs_confirm"])

    # 9. list_packs
    def test_list_packs(self):
        cases = [
            "list packs",
            "show upload packs",
            "show ready packs",
            "packs chupinchu",
            "upload packs list chey",
            "ready packs anni chudu",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "list_packs", f"Failed on: {text}")
            self.assertFalse(res["needs_confirm"])

    # 10. show_pack
    def test_show_pack(self):
        cases = [
            ("show pack 3", 3),
            ("view pack 2", 2),
            ("display upload pack 5", 5),
            ("pack 3 chupinchu", 3),
            ("pack 2 details choopinchu", 2),
            ("moodu pack chudu", 3),
        ]
        for text, exp_n in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "show_pack", f"Failed on: {text}")
            self.assertEqual(res["args"].get("n"), exp_n)
            self.assertFalse(res["needs_confirm"])

    # 11. rename_agent
    def test_rename_agent(self):
        cases = [
            ("rename agent Scout to Finder", "Scout", "Finder"),
            ("change agent Trend to Tracker", "Trend", "Tracker"),
            ("rename agent Doctor as Healer", "Doctor", "Healer"),
            ("agent Scout peru Finder ga marchu", "Scout", "Finder"),
            ("agent Scout ni Finder ga marchu", "Scout", "Finder"),
            ("agent Trend peru Tracker ani marchu", "Trend", "Tracker"),
        ]
        for text, exp_old, exp_new in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "rename_agent", f"Failed on: {text}")
            self.assertEqual(res["args"].get("old"), exp_old)
            self.assertEqual(res["args"].get("new"), exp_new)
            self.assertTrue(res["needs_confirm"])

    # 12. rename_room
    def test_rename_room(self):
        cases = [
            ("rename room Studio to Workshop", "Studio", "Workshop"),
            ("rename Pinterest room to PinLab", "Pinterest", "PinLab"),
            ("change room Reception to FrontDesk", "Reception", "FrontDesk"),
            ("room Studio peru Workshop ga marchu", "Studio", "Workshop"),
            ("Studio room ni PinLab ga marchu", "Studio", "PinLab"),
            ("Pinterest room peru PinLab ga pettu", "Pinterest", "PinLab"),
        ]
        for text, exp_old, exp_new in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "rename_room", f"Failed on: {text}")
            self.assertEqual(res["args"].get("old"), exp_old)
            self.assertEqual(res["args"].get("new"), exp_new)
            self.assertTrue(res["needs_confirm"])

    # 13. move_room
    def test_move_room(self):
        cases = [
            ("move room Studio near Reception", "Studio", "Reception"),
            ("move Pinterest room next to Council", "Pinterest", "Council"),
            ("move Reception room beside Studio", "Reception", "Studio"),
            ("Studio room ni Reception daggaraki jarupu", "Studio", "Reception"),
            ("Pinterest room ni Council daggaraki pettu", "Pinterest", "Council"),
            ("Studio room Reception pakkana pettu", "Studio", "Reception"),
        ]
        for text, exp_name, exp_near in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "move_room", f"Failed on: {text}")
            self.assertEqual(res["args"].get("name"), exp_name)
            self.assertEqual(res["args"].get("near"), exp_near)
            self.assertTrue(res["needs_confirm"])

    # 14. resize_room
    def test_resize_room(self):
        cases = [
            ("resize room Studio bigger", "Studio", "bigger"),
            ("make Pinterest room smaller", "Pinterest", "smaller"),
            ("make Council room bigger", "Council", "bigger"),
            ("Pinterest room ni pedda chey", "Pinterest", "bigger"),
            ("Council room chinna chey", "Council", "smaller"),
            ("Studio peddadi cheyyi", "Studio", "bigger"),
        ]
        for text, exp_name, exp_size in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "resize_room", f"Failed on: {text}")
            self.assertEqual(res["args"].get("name"), exp_name)
            self.assertEqual(res["args"].get("size"), exp_size)
            self.assertTrue(res["needs_confirm"])

    # 15. theme
    def test_theme(self):
        cases = [
            ("theme dark", "dark"),
            ("theme light", "light"),
            ("switch theme to festival", "festival"),
            ("dark mode pettu", "dark"),
            ("light theme marchu", "light"),
            ("festival mode pettu", "festival"),
        ]
        for text, exp_theme in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "theme", f"Failed on: {text}")
            self.assertEqual(res["args"].get("theme"), exp_theme)
            self.assertFalse(res["needs_confirm"])

    # 16. language
    def test_language(self):
        cases = [
            ("language english", "english"),
            ("set language to english", "english"),
            ("switch language english", "english"),
            ("tenglish lo matladu", "tenglish"),
            ("bhasha tenglish pettu", "tenglish"),
            ("language tenglish marchu", "tenglish"),
        ]
        for text, exp_lang in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "language", f"Failed on: {text}")
            self.assertEqual(res["args"].get("language"), exp_lang)
            self.assertFalse(res["needs_confirm"])

    # 17. focus_room
    def test_focus_room(self):
        cases = [
            ("focus room Council", "Council"),
            ("zoom into Pinterest room", "Pinterest"),
            ("focus on Studio", "Studio"),
            ("Council room meeda focus pettu", "Council"),
            ("Pinterest ni focus chey", "Pinterest"),
            ("Studio room meeda zoom chey", "Studio"),
        ]
        for text, exp_room in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "focus_room", f"Failed on: {text}")
            self.assertEqual(res["args"].get("name"), exp_room)
            self.assertFalse(res["needs_confirm"])

    # 18. hide_money
    def test_hide_money(self):
        cases = [
            "hide money",
            "mask earnings",
            "don't show money",
            "screen meeda money daayi",
            "paisalu daachipettu",
            "money hide chey",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "hide_money", f"Failed on: {text}")
            self.assertFalse(res["needs_confirm"])

    # 19. show_money_on_screen
    def test_show_money_on_screen(self):
        cases = [
            "show money on screen",
            "display money",
            "unhide money on screen",
            "screen meeda paisalu chupinchu",
            "screen lo money chupinchu",
            "screen meeda money display chey",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "show_money_on_screen", f"Failed on: {text}")
            self.assertFalse(res["needs_confirm"])

    # 20. mute
    def test_mute(self):
        cases = [
            "mute",
            "silent mode",
            "turn off sound",
            "sound aapu",
            "silent ga undu",
            "shabdam aapu",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "mute", f"Failed on: {text}")
            self.assertFalse(res["needs_confirm"])

    # 21. unmute
    def test_unmute(self):
        cases = [
            "unmute",
            "turn on sound",
            "enable audio",
            "sound on chey",
            "unmute chey",
            "shabdam on chey",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "unmute", f"Failed on: {text}")
            self.assertFalse(res["needs_confirm"])

    # 22. help
    def test_help(self):
        cases = [
            "help",
            "how to use",
            "show command list",
            "sahaayam",
            "commands chupinchu",
            "em cheyagalanu",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "help", f"Failed on: {text}")
            self.assertFalse(res["needs_confirm"])
            self.assertIn("hint", res)

    # 23. undo_last_ui_change
    def test_undo_last_ui_change(self):
        cases = [
            "undo last change",
            "rollback ui",
            "undo",
            "mundu laaga chey",
            "venakki teesko",
            "undo chey",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "undo_last_ui_change", f"Failed on: {text}")
            self.assertFalse(res["needs_confirm"])

    # 24. daily_report
    def test_daily_report(self):
        cases = [
            "daily report",
            "show today's report",
            "day report",
            "roju report chupinchu",
            "daily report kavali",
            "today report pettu",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "daily_report", f"Failed on: {text}")
            self.assertFalse(res["needs_confirm"])

    # 25. what_happened_today
    def test_what_happened_today(self):
        cases = [
            "what happened today",
            "what did robot do today",
            "today's summary",
            "ee roju em jarigindi",
            "eroju updates enti",
            "ee roju panulu enti",
        ]
        for text in cases:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "what_happened_today", f"Failed on: {text}")
            self.assertFalse(res["needs_confirm"])

    # Edge cases and unknown text handling
    def test_unknown_and_empty(self):
        for text in ["", "   ", "bla bla qwerty 12345", "random chatter from microphone"]:
            res = owner_commands.parse(text)
            self.assertEqual(res["intent"], "unknown", f"Expected unknown for: {text}")
            self.assertEqual(res["confidence"], 0.0)
            self.assertFalse(res["needs_confirm"])
            self.assertIn("hint", res)
            self.assertIn("Ardam kaaledu", res["hint"])


if __name__ == "__main__":
    unittest.main()
