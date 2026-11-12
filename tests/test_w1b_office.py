"""tests/test_w1b_office.py — Comprehensive unit tests for BINNY-W1B Office Controls.

Tests cover:
  1. Phrase parsing for 40+ distinct English and Tenglish phrases:
     - Room resize: bigger, smaller, pedda chey, chinna chey, enlarge, shrink
     - Room move: next to, beside, near, pakkana pettu, daggaraki jarupu
     - Room add: add room, create room, kotha room add chey, new room
     - Room remove: remove room, delete room, room teeyyi, teesei
     - Room rename: rename room to/as, room peru ga marchu
     - Agent caps: put/set/give red cap on/to agent, agent ki red cap pettu
     - Agent themes/colors: give/set theme to agent, agent ki blue theme pettu
     - Agent icons: put star icon on agent, agent ki star icon pettu
     - Office themes: dark mode, light mode, festival mode, office theme dark pettu
     - Undo phrases: undo, office change undo, undo last office change, venakki teesko
  2. Database storage and schema migrations:
     - Table auto-creation when missing
     - Backward compatibility and column migration
     - Retrieval with status filter and ordering
     - Queue retention policy (max 50 items kept, oldest trimmed)
  3. Undo functionality:
     - Reverting last applied/queued request
     - Setting status to failed and queuing an undo record
     - Empty queue handling
  4. Telegram plugin `tg_office.py`:
     - `/office` command displays room list and recent changes
     - Natural phrase processing and confirmation reply with preview line
     - Reply keyboard `[["↩️ Undo", "🏢 Office menu"]]`
     - Helpful hint for unknown/malformed office commands
     - Non-owner isolation (ignored/None)
     - Empty text / non-string handling
  5. `export_status.py` office requests integration:
     - Only queued requests are included (applied/failed excluded)
     - Data scrubbing: no secrets, no money amounts, clean strings
     - Present in both `build_status_private` and `write_status` output
  6. Safety & Security:
     - Zero money / currency mentions anywhere in outputs
     - Zero secret leaks
"""
import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import export_status
import guardrails
import office_requests
import tg_office

OWNER_CHAT_ID = "12345678"
NON_OWNER_CHAT_ID = "87654321"


class OfficeRequestsPhraseParsingTests(unittest.TestCase):
    """Test 40+ distinct natural language phrases in English and Tenglish."""

    def test_01_room_resize_pedda_chey(self):
        res = office_requests.parse_phrase("office lo Pinterest room pedda chey")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Pinterest")
        self.assertEqual(res["value"], {"action": "resize", "size": "bigger"})
        self.assertIn("peddadi", res["label_te"])

    def test_02_room_resize_chinna_chey(self):
        res = office_requests.parse_phrase("QA room ni chinna chey")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Qa")
        self.assertEqual(res["value"], {"action": "resize", "size": "smaller"})
        self.assertIn("chinnadi", res["label_te"])

    def test_03_room_resize_make_bigger(self):
        res = office_requests.parse_phrase("make Research room bigger")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Research")
        self.assertEqual(res["value"], {"action": "resize", "size": "bigger"})

    def test_04_room_resize_make_smaller(self):
        res = office_requests.parse_phrase("make Content room smaller")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Content")
        self.assertEqual(res["value"], {"action": "resize", "size": "smaller"})

    def test_05_room_resize_expand(self):
        res = office_requests.parse_phrase("expand Marketing room")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Marketing")
        self.assertEqual(res["value"], {"action": "resize", "size": "bigger"})

    def test_06_room_resize_shrink(self):
        res = office_requests.parse_phrase("shrink Studio room")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Studio")
        self.assertEqual(res["value"], {"action": "resize", "size": "smaller"})

    def test_07_room_resize_peddadi_pettu(self):
        res = office_requests.parse_phrase("Executive room peddadi pettu")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Executive")
        self.assertEqual(res["value"], {"action": "resize", "size": "bigger"})

    def test_08_room_move_pakkana_pettu(self):
        res = office_requests.parse_phrase("QA room ni Products pakkana pettu")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Qa")
        self.assertEqual(res["value"], {"action": "move", "near": "Products"})
        self.assertIn("pakkana", res["label_te"])

    def test_09_room_move_daggaraki_jarupu(self):
        res = office_requests.parse_phrase("Studio room ni Reception daggaraki jarupu")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Studio")
        self.assertEqual(res["value"], {"action": "move", "near": "Reception"})

    def test_10_room_move_next_to(self):
        res = office_requests.parse_phrase("move Studio next to Reception")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Studio")
        self.assertEqual(res["value"], {"action": "move", "near": "Reception"})

    def test_11_room_move_beside(self):
        res = office_requests.parse_phrase("put Research room beside Marketing")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Research")
        self.assertEqual(res["value"], {"action": "move", "near": "Marketing"})

    def test_12_room_move_near(self):
        res = office_requests.parse_phrase("place Pinterest room near Instagram")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Pinterest")
        self.assertEqual(res["value"], {"action": "move", "near": "Instagram"})

    def test_13_room_add_kotha_room_add_chey(self):
        res = office_requests.parse_phrase("kotha room Research add chey")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.add")
        self.assertEqual(res["target"], "Research")
        self.assertEqual(res["value"], {"action": "add"})
        self.assertIn("add chesam", res["label_te"])

    def test_14_room_add_new_room_pettu(self):
        res = office_requests.parse_phrase("office lo kotha room Analytics pettu")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.add")
        self.assertEqual(res["target"], "Analytics")
        self.assertEqual(res["value"], {"action": "add"})

    def test_15_room_add_english_add(self):
        res = office_requests.parse_phrase("add room Analytics")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.add")
        self.assertEqual(res["target"], "Analytics")
        self.assertEqual(res["value"], {"action": "add"})

    def test_16_room_add_english_create(self):
        res = office_requests.parse_phrase("create room Lab")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.add")
        self.assertEqual(res["target"], "Lab")
        self.assertEqual(res["value"], {"action": "add"})

    def test_17_room_remove_remove_chey(self):
        res = office_requests.parse_phrase("Marketing room remove chey")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.remove")
        self.assertEqual(res["target"], "Marketing")
        self.assertEqual(res["value"], {"action": "remove"})
        self.assertIn("remove chesam", res["label_te"])

    def test_18_room_remove_teeyyi(self):
        res = office_requests.parse_phrase("room Lab teeyyi")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.remove")
        self.assertEqual(res["target"], "Lab")
        self.assertEqual(res["value"], {"action": "remove"})

    def test_19_room_remove_teesei(self):
        res = office_requests.parse_phrase("office lo Pinterest room teesei")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.remove")
        self.assertEqual(res["target"], "Pinterest")
        self.assertEqual(res["value"], {"action": "remove"})

    def test_20_room_remove_english_delete(self):
        res = office_requests.parse_phrase("delete room QA")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.remove")
        self.assertEqual(res["target"], "Qa")
        self.assertEqual(res["value"], {"action": "remove"})

    def test_21_room_remove_english_remove(self):
        res = office_requests.parse_phrase("remove Research room")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.remove")
        self.assertEqual(res["target"], "Research")
        self.assertEqual(res["value"], {"action": "remove"})

    def test_22_room_rename_peru_ga_marchu(self):
        res = office_requests.parse_phrase("Research room peru Discovery ga marchu")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Research")
        self.assertEqual(res["value"], {"action": "rename", "new_name": "Discovery"})
        self.assertIn("Discovery", res["label_te"])

    def test_23_room_rename_name_ani_marchu(self):
        res = office_requests.parse_phrase("QA room name Testing ani marchu")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Qa")
        self.assertEqual(res["value"], {"action": "rename", "new_name": "Testing"})

    def test_24_room_rename_english_rename_to(self):
        res = office_requests.parse_phrase("rename room QA to Testing")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Qa")
        self.assertEqual(res["value"], {"action": "rename", "new_name": "Testing"})

    def test_25_room_rename_english_rename_as(self):
        res = office_requests.parse_phrase("rename Pinterest room as Visuals")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "room.update")
        self.assertEqual(res["target"], "Pinterest")
        self.assertEqual(res["value"], {"action": "rename", "new_name": "Visuals"})

    def test_26_agent_cap_tenglish_pettu(self):
        res = office_requests.parse_phrase("Pin Designer ki red cap pettu")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "agent.update")
        self.assertEqual(res["target"], "Pin Designer")
        self.assertEqual(res["value"], {"cap": "red"})
        self.assertIn("red cap", res["label_te"])

    def test_27_agent_cap_tenglish_ivvu(self):
        res = office_requests.parse_phrase("Scout agent ki green cap ivvu")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "agent.update")
        self.assertEqual(res["target"], "Scout")
        self.assertEqual(res["value"], {"cap": "green"})

    def test_28_agent_cap_english_put_on(self):
        res = office_requests.parse_phrase("put blue cap on CEO")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "agent.update")
        self.assertEqual(res["target"], "Ceo")
        self.assertEqual(res["value"], {"cap": "blue"})

    def test_29_agent_cap_english_set_for(self):
        res = office_requests.parse_phrase("set red cap for Pin Designer")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "agent.update")
        self.assertEqual(res["target"], "Pin Designer")
        self.assertEqual(res["value"], {"cap": "red"})

    def test_30_agent_cap_english_change_to(self):
        res = office_requests.parse_phrase("change Doctor cap to yellow")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "agent.update")
        self.assertEqual(res["target"], "Doctor")
        self.assertEqual(res["value"], {"cap": "yellow"})

    def test_31_agent_icon_tenglish(self):
        res = office_requests.parse_phrase("CEO ki star icon pettu")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "agent.update")
        self.assertEqual(res["target"], "Ceo")
        self.assertEqual(res["value"], {"icon": "star"})
        self.assertIn("star icon", res["label_te"])

    def test_32_agent_icon_english(self):
        res = office_requests.parse_phrase("put crown icon on CEO")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "agent.update")
        self.assertEqual(res["target"], "Ceo")
        self.assertEqual(res["value"], {"icon": "crown"})

    def test_33_agent_theme_tenglish(self):
        res = office_requests.parse_phrase("Doctor ki blue theme pettu")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "agent.update")
        self.assertEqual(res["target"], "Doctor")
        self.assertEqual(res["value"], {"theme": "blue"})
        self.assertIn("blue theme", res["label_te"])

    def test_34_agent_theme_english_give(self):
        res = office_requests.parse_phrase("give Doctor blue theme")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "agent.update")
        self.assertEqual(res["target"], "Doctor")
        self.assertEqual(res["value"], {"theme": "blue"})

    def test_35_agent_theme_english_set_for(self):
        res = office_requests.parse_phrase("set purple theme for Scout")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "agent.update")
        self.assertEqual(res["target"], "Scout")
        self.assertEqual(res["value"], {"theme": "purple"})

    def test_36_office_theme_dark(self):
        res = office_requests.parse_phrase("office theme dark pettu")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "office.theme")
        self.assertEqual(res["value"], {"theme": "dark"})
        self.assertIn("dark mode", res["label_te"])

    def test_37_office_theme_light_mode(self):
        res = office_requests.parse_phrase("office light mode")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "office.theme")
        self.assertEqual(res["value"], {"theme": "light"})

    def test_38_office_theme_festival(self):
        res = office_requests.parse_phrase("office lo festival theme pettu")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "office.theme")
        self.assertEqual(res["value"], {"theme": "festival"})

    def test_39_undo_office_change_undo(self):
        res = office_requests.parse_phrase("office change undo")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "undo")
        self.assertEqual(res["value"], {"action": "undo"})

    def test_40_undo_undo_office_change(self):
        res = office_requests.parse_phrase("undo office change")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "undo")

    def test_41_undo_venakki_teesko(self):
        res = office_requests.parse_phrase("venakki teesko")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "undo")

    def test_42_undo_bare_undo(self):
        res = office_requests.parse_phrase("undo")
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "undo")

    def test_43_unrelated_text_returns_none(self):
        self.assertIsNone(office_requests.parse_phrase("what is the weather"))
        self.assertIsNone(office_requests.parse_phrase("make a coffee"))
        self.assertIsNone(office_requests.parse_phrase("hello"))
        self.assertIsNone(office_requests.parse_phrase(""))
        self.assertIsNone(office_requests.parse_phrase(None))


class OfficeDatabaseAndStorageTests(unittest.TestCase):
    """Test office_requests table creation, migration, storage, trimming, and undo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "office.db")

    def test_44_table_auto_creation_and_insert(self):
        req = office_requests.store_request(
            kind="room.update",
            target="Pinterest",
            value={"action": "resize", "size": "bigger"},
            label_te="Pinterest room pedda chesam",
            db_path=self.db_path,
        )
        self.assertTrue(req["id"].startswith("off_"))
        self.assertEqual(req["kind"], "room.update")
        self.assertEqual(req["status"], "queued")

        rows = office_requests.list_office_requests(db_path=self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target"], "Pinterest")
        self.assertEqual(rows[0]["value"], {"action": "resize", "size": "bigger"})

    def test_45_table_migration_with_preexisting_different_cols(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE office_requests (id TEXT PRIMARY KEY, nonce TEXT, receipt TEXT)")
        conn.close()

        req = office_requests.store_request(
            kind="agent.update",
            target="CEO",
            value={"cap": "gold"},
            label_te="CEO ki gold cap",
            db_path=self.db_path,
        )
        self.assertEqual(req["target"], "CEO")
        rows = office_requests.list_office_requests(db_path=self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target"], "CEO")

    def test_46_list_filtering_by_status(self):
        office_requests.store_request("room.add", "Lab", None, "add lab", db_path=self.db_path, status="queued")
        office_requests.store_request("room.remove", "Old", None, "remove old", db_path=self.db_path, status="applied")
        office_requests.store_request("room.update", "Studio", None, "update studio", db_path=self.db_path, status="failed")

        queued = office_requests.list_office_requests(db_path=self.db_path, status="queued")
        applied = office_requests.list_office_requests(db_path=self.db_path, status="applied")
        failed = office_requests.list_office_requests(db_path=self.db_path, status="failed")
        all_reqs = office_requests.list_office_requests(db_path=self.db_path)

        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["target"], "Lab")
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["target"], "Old")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["target"], "Studio")
        self.assertEqual(len(all_reqs), 3)

    def test_47_retention_policy_trims_to_last_50(self):
        for i in range(60):
            office_requests.store_request(
                kind="room.update",
                target=f"Room_{i}",
                value={"index": i},
                label_te=f"Room {i}",
                db_path=self.db_path,
            )

        rows = office_requests.list_office_requests(db_path=self.db_path, limit=100)
        self.assertEqual(len(rows), 50)
        targets = [r["target"] for r in rows]
        self.assertIn("Room_59", targets)
        self.assertIn("Room_10", targets)
        self.assertNotIn("Room_0", targets)
        self.assertNotIn("Room_9", targets)

    def test_48_undo_last_request_success(self):
        office_requests.store_request("room.add", "Music", None, "Add music room", db_path=self.db_path)
        office_requests.store_request("agent.update", "Doctor", {"cap": "white"}, "Doctor cap", db_path=self.db_path)

        undone = office_requests.undo_last_request(db_path=self.db_path)
        self.assertIsNotNone(undone)
        self.assertEqual(undone["target"], "Doctor")

        all_reqs = office_requests.list_office_requests(db_path=self.db_path)
        self.assertEqual(all_reqs[0]["kind"], "undo")
        self.assertEqual(all_reqs[0]["status"], "applied")
        self.assertEqual(all_reqs[1]["status"], "failed")

    def test_49_undo_empty_queue(self):
        undone = office_requests.undo_last_request(db_path=self.db_path)
        self.assertIsNone(undone)


class TgOfficePluginTests(unittest.TestCase):
    """Test tg_office.py plugin handlers, menu, replies, keyboards, and owner gating."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "office_tg.db")

        self.sent_messages = []

        def _fake_send_text(text, keyboard=None):
            self.sent_messages.append({"text": text, "keyboard": keyboard})
            return True

        self.ctx_owner = {
            "db_path": self.db_path,
            "chat_id": OWNER_CHAT_ID,
            "now": datetime.datetime.now(datetime.timezone.utc),
            "send_text": _fake_send_text,
            "send_document": lambda *a, **k: None,
            "send_photo": lambda *a, **k: None,
            "send_media_group": lambda *a, **k: None,
            "state_get": lambda k, d=None: d,
            "state_set": lambda k, v: None,
        }

        self.ctx_non_owner = dict(self.ctx_owner)
        self.ctx_non_owner["chat_id"] = NON_OWNER_CHAT_ID

        self.env_patcher = mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": OWNER_CHAT_ID})
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

    def test_50_plugin_commands_metadata(self):
        self.assertTrue(hasattr(tg_office, "COMMANDS"))
        cmds = tg_office.COMMANDS
        self.assertEqual(cmds[0]["cmd"], "/office")
        self.assertIn("office", cmds[0]["words"])
        self.assertIn("help_te", cmds[0])
        self.assertIn("help_en", cmds[0])

    def test_51_non_owner_ignored(self):
        res = tg_office.handle("/office", self.ctx_non_owner)
        self.assertIsNone(res)
        self.assertEqual(len(self.sent_messages), 0)

        res2 = tg_office.handle("office lo Pinterest room pedda chey", self.ctx_non_owner)
        self.assertIsNone(res2)
        self.assertEqual(len(self.sent_messages), 0)

    def test_52_office_menu_command(self):
        res = tg_office.handle("/office", self.ctx_owner)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.sent_messages), 1)
        sent = self.sent_messages[0]
        self.assertIn("Binny Mills Office Menu", sent["text"])
        self.assertIn("Current Rooms", sent["text"])
        self.assertIn("Recent Changes", sent["text"])
        self.assertEqual(sent["keyboard"], [["↩️ Undo", "🏢 Office menu"]])

    def test_53_office_menu_word_trigger(self):
        res = tg_office.handle("office menu", self.ctx_owner)
        self.assertEqual(res, {"handled": True})
        self.assertIn("Binny Mills Office Menu", self.sent_messages[0]["text"])

    def test_54_plain_phrase_room_resize_flow(self):
        res = tg_office.handle("office lo Pinterest room pedda chey", self.ctx_owner)
        self.assertEqual(res, {"handled": True})
        sent = self.sent_messages[0]
        self.assertIn("Office request queue lo pettam!", sent["text"])
        self.assertIn("Preview: [room.update] Pinterest", sent["text"])
        self.assertIn("Binny Mills app lo auto ga apply avutundi", sent["text"])
        self.assertEqual(sent["keyboard"], [["↩️ Undo", "🏢 Office menu"]])

        reqs = office_requests.list_office_requests(db_path=self.db_path)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["target"], "Pinterest")
        self.assertEqual(reqs[0]["status"], "queued")

    def test_55_plain_phrase_agent_cap_flow(self):
        res = tg_office.handle("Pin Designer ki red cap pettu", self.ctx_owner)
        self.assertEqual(res, {"handled": True})
        sent = self.sent_messages[0]
        self.assertIn("Pin Designer agent ki red cap pettam", sent["text"])
        self.assertIn("Preview: [agent.update] Pin Designer", sent["text"])

    def test_56_undo_button_flow(self):
        tg_office.handle("kotha room Research add chey", self.ctx_owner)
        self.assertEqual(len(office_requests.list_office_requests(db_path=self.db_path)), 1)

        self.sent_messages.clear()
        res = tg_office.handle("↩️ Undo", self.ctx_owner)
        self.assertEqual(res, {"handled": True})
        sent = self.sent_messages[0]
        self.assertIn("Last office change undo chesam!", sent["text"])
        self.assertIn("Research", sent["text"])
        self.assertIn("Preview: [undo]", sent["text"])

    def test_57_undo_when_queue_empty(self):
        res = tg_office.handle("office change undo", self.ctx_owner)
        self.assertEqual(res, {"handled": True})
        sent = self.sent_messages[0]
        self.assertIn("Undo cheyadaniki recent changes em levu", sent["text"])

    def test_58_unknown_office_phrase_hint(self):
        res = tg_office.handle("office lo something weird pettu", self.ctx_owner)
        self.assertEqual(res, {"handled": True})
        sent = self.sent_messages[0]
        self.assertIn("Office command sarigga ardam kaaledu", sent["text"])
        self.assertIn("Ila try cheyandi", sent["text"])
        self.assertIn("/office", sent["text"])

    def test_59_unrelated_message_returns_none(self):
        res = tg_office.handle("random chit chat", self.ctx_owner)
        self.assertIsNone(res)
        self.assertEqual(len(self.sent_messages), 0)


class ExportStatusOfficeIntegrationTests(unittest.TestCase):
    """Test export_status.py office_requests integration and secret scrubbing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "export.db")

    def test_60_export_status_private_contains_queued_only(self):
        office_requests.store_request("room.update", "Pinterest", {"size": "bigger"}, "resize", db_path=self.db_path, status="queued")
        office_requests.store_request("room.remove", "QA", None, "remove", db_path=self.db_path, status="applied")
        office_requests.store_request("room.add", "Lab", None, "add", db_path=self.db_path, status="failed")

        status_priv = export_status.build_status_private(db_path=self.db_path)
        self.assertIn("office_requests", status_priv)
        off_reqs = status_priv["office_requests"]
        self.assertEqual(len(off_reqs), 1)
        self.assertEqual(off_reqs[0]["target"], "Pinterest")
        self.assertEqual(off_reqs[0]["status"], "queued")

    def test_61_write_status_file_includes_scrubbed_office_requests(self):
        out_file = os.path.join(self.tmp.name, "status.json")
        office_requests.store_request(
            kind="agent.update",
            target="Scout",
            value={"cap": "green", "fake_key": "gsk_" + "1234567890123456789012345678"},
            label_te="Scout cap update",
            db_path=self.db_path,
            status="queued"
        )

        export_status.write_status(path=out_file, db_path=self.db_path)
        self.assertTrue(os.path.exists(out_file))

        with open(out_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("office_requests", data)
        reqs = data["office_requests"]
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["target"], "Scout")
        json_str = json.dumps(reqs)
        self.assertNotIn("gsk_" + "1234567890123456789012345678", json_str)

    def test_62_zero_currency_mentions_in_code_and_docs(self):
        """Ensure no money/currency amounts exist in tg_office or office_requests."""
        import re
        forbidden = re.compile(
            r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
            re.IGNORECASE,
        )

        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "office_requests.py"), "r", encoding="utf-8") as f:
            self.assertIsNone(forbidden.search(f.read()))

        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tg_office.py"), "r", encoding="utf-8") as f:
            self.assertIsNone(forbidden.search(f.read()))


if __name__ == "__main__":
    unittest.main()
