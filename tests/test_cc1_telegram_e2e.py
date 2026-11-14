"""CC1-4: every Telegram command really works, end to end.

Each test sends fake Telegram updates into telegram_inbox.process_updates and reads what the fake
send functions received. Nothing here calls a plugin helper directly: if a command is not reachable
through the inbox, the test fails.

Covered: every built-in command, every COMMANDS entry (and every declared word) of every tg_*.py,
Hi, /start, unknown text, numbers in words, a non-owner chat, duplicate update ids, quiet hours,
and one regression test per bug fixed in this task.

The working directory is a temp folder (old commands write below a relative exported_content/).
No Telegram, no network, no AI.
"""
from __future__ import annotations

import base64
import glob
import importlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guardrails
import telegram_inbox
import tg_sell

REPO = Path(__file__).resolve().parent.parent
OWNER = int("1234" "5678")
STRANGER = int("8765" "4321")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
BAD_WORDS = ("ardam kaaledu",)
BUILTIN = telegram_inbox._COMMANDS.split("|")


def plugin_entries():
    found = []
    for path in sorted(glob.glob(str(REPO / "tg_*.py"))):
        module = importlib.import_module(Path(path).stem)
        for entry in getattr(module, "COMMANDS", []) or []:
            found.append((module.__name__, entry))
    return found


PLUGIN_ENTRIES = plugin_entries()


class Inbox(unittest.TestCase):
    """Temp folder, temp database, fake Telegram."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.root)
        self.db = str(self.root / "robot.db")
        self.products = self.root / "exported_content" / "products"
        self.products.mkdir(parents=True)
        self.marketing = self.root / "exported_content" / "marketing"
        self.marketing.mkdir(parents=True)
        self.updates, self.sent, self.acked = [], [], []
        self.next_id = 1000

        def send_text(token, chat, text, buttons=None, keyboard=None):
            self.sent.append({"kind": "text", "chat": chat, "text": text, "buttons": buttons, "keyboard": keyboard})
            return True

        def send_file(kind):
            def send(token, chat, path, caption=""):
                self.sent.append({"kind": kind, "chat": chat, "path": path, "text": caption})
                return True
            return send

        import product_builder
        patches = [
            mock.patch.dict(os.environ, {"CHILLI_PRODUCTS_DIR": str(self.products),
                                         "CHILLI_MARKETING_DIR": str(self.marketing),
                                         "GEMINI_AUTOFIX_KEY": ""}),
            mock.patch.object(product_builder, "PRODUCTS_DIR", str(self.products)),
            mock.patch("truth_check.check", return_value={"verdict": "ready", "score": 100, "problems": []}),
            mock.patch("product_qa_deep.check_folder", return_value={"verdict": "ready", "score": 100, "problems": []}),
            mock.patch.object(telegram_inbox, "fetch_updates", side_effect=lambda token, offset: list(self.updates)),
            mock.patch.object(telegram_inbox, "acknowledge_update",
                              side_effect=lambda token, update_id: self.acked.append(update_id) or True),
            mock.patch.object(telegram_inbox, "_call", return_value={"ok": True}),
            mock.patch.object(telegram_inbox, "edit_text", return_value=True),
            mock.patch.object(telegram_inbox, "send_text", side_effect=send_text),
            mock.patch.object(telegram_inbox, "send_document", side_effect=send_file("document")),
            mock.patch.object(telegram_inbox, "send_photo", side_effect=send_file("photo")),
            mock.patch.object(telegram_inbox, "send_media_group",
                              side_effect=lambda token, chat, paths, caption="":
                              self.sent.append({"kind": "group", "chat": chat, "path": list(paths), "text": caption}) or True),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    # -- helpers -------------------------------------------------------------
    def update(self, text, chat=OWNER, update_id=None):
        if update_id is None:
            update_id = self.next_id
            self.next_id += 1
        return {"update_id": update_id, "message": {"message_id": update_id, "chat": {"id": chat}, "text": text}}

    def poll(self, updates):
        self.updates = list(updates)
        self.sent = []
        return telegram_inbox.process_updates("test-token", OWNER, db_path=self.db)

    def say(self, text, chat=OWNER):
        handled = self.poll([self.update(text, chat)])
        return handled, self.texts()

    def press(self, data, chat=OWNER):
        update_id = self.next_id
        self.next_id += 1
        return self.poll([{"update_id": update_id, "callback_query": {
            "id": "cb", "data": data, "message": {"message_id": 7, "chat": {"id": chat}}}}])

    def texts(self):
        return [item["text"] for item in self.sent if item["kind"] == "text"]

    def files(self, kind="document"):
        return [Path(item["path"]).name for item in self.sent if item["kind"] == kind]

    def assert_real_reply(self, text):
        handled, texts = self.say(text)
        self.assertEqual(handled, 1, text)
        self.assertTrue(self.sent, f"no reply at all for {text!r}")
        said = "\n".join(texts)
        self.assertTrue(said.strip() or any(item["kind"] != "text" for item in self.sent), text)
        for bad in BAD_WORDS:
            self.assertNotIn(bad, said.lower(), text)
        for item in self.sent:
            self.assertEqual(str(item["chat"]), str(OWNER), text)
        return said

    def add_draft(self, title="Draft: [Budget Sheets Agent] Monthly budget sheet"):
        return guardrails.request_approval("public_draft", title, "PRODUCT NAME: Budget sheet\nBUYER: adults\n",
                                           None, self.db)

    def add_pack(self, name="3-focus-planner", title="Focus Planner Printable Pack"):
        pack = self.products / name
        pack.mkdir(parents=True)
        with zipfile.ZipFile(pack / "product.zip", "w") as bundle:
            bundle.writestr("planner.pdf", b"%PDF-1.4 test\n%%EOF")
            bundle.writestr("LICENSE.txt", "Personal Licence")
        description = ("A clean, minimal focus planner designed to help you stay organized without overwhelming "
                       "you. Includes daily priority blocks, habit trackers, and notes sections. Print on standard "
                       "A4 or US Letter paper for best results. Personal use licence included. Just print and "
                       "write. Each page keeps wide margins and calm spacing so the layout stays easy to read.")
        tags = ("focus planner, daily planner, printable page, desk pad, calm routine, habit tracker, study timer, "
                "weekly menu, mindful notes, goal grid, brain dump, weekly audit, one page day")
        (pack / "listing.txt").write_text(
            f"Title: {title}\n\nDescription:\n{description}\n\nTags: {tags}\n\nWhat you get:\n- A4 PDF file\n"
            "- US Letter PDF file\n\nTheme: Calm Sage\n", encoding="utf-8")
        (pack / "checklist.txt").write_text("Checklist:\n- [ ] Upload product.zip\n- Theme: Calm Sage\n", encoding="utf-8")
        (pack / "LICENSE.txt").write_text("Personal use licence only.\n", encoding="utf-8")
        (pack / "cover.png").write_bytes(PNG)
        (pack / "preview-1.png").write_bytes(PNG)
        return pack

    def add_private_marketing(self, name="3-focus-planner"):
        """Files where marketing_auto (CC1-1) puts them: <products>/marketing/<pack>/."""
        target = self.products / "marketing" / name
        (target / "pins").mkdir(parents=True)
        for index in range(1, 4):
            (target / "pins" / f"pin_{index}_a.png").write_bytes(PNG)
        (target / "pins" / "pins.json").write_text(json.dumps(
            [{"file": f"pin_{i}_a.png", "layout": "A", "title": f"Pin title {i}", "description": "d", "alt": "a"}
             for i in range(1, 4)]), encoding="utf-8")
        (target / "landing").mkdir()
        (target / "landing" / "index.html").write_text("<html><body>Focus Planner</body></html>", encoding="utf-8")
        return target


class BuiltInCommandTests(Inbox):
    """One test per built-in command is added below this class."""

    def test_the_list_of_built_ins_is_what_we_expect(self):
        self.assertEqual(len(BUILTIN), len(set(BUILTIN)))
        for name in ("status", "list", "view", "approve", "reject", "packs", "pack", "doctor", "help", "start"):
            self.assertIn(name, BUILTIN)

    def test_plain_word_forms_work_too(self):
        for word in ("status", "list", "packs", "pipeline", "doctor", "help", "week", "today"):
            self.assert_real_reply(word)

    def test_command_with_bot_name(self):
        said = self.assert_real_reply("/status@chilli_gavva_bot")
        self.assertIn("Chilli Gavva Status", said)


def _builtin_test(name):
    def test(self):
        said = self.assert_real_reply("/" + name)
        self.assertGreater(len(said.strip()), 5)
    return test


for _name in BUILTIN:
    setattr(BuiltInCommandTests, f"test_builtin_{_name}", _builtin_test(_name))


class BuiltInBehaviourTests(Inbox):
    def test_status_is_the_real_status(self):
        _handled, texts = self.say("/status")
        self.assertEqual(texts, [telegram_inbox.status_text(self.db)])

    def test_list_shows_drafts_with_buttons(self):
        aid = self.add_draft()
        self.say("/list")
        self.assertIn("Monthly budget sheet", self.sent[0]["text"])
        cards = [item for item in self.sent if item.get("buttons")]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["buttons"], telegram_inbox.draft_buttons(aid))

    def test_view_shows_the_draft_text(self):
        aid = self.add_draft()
        _handled, texts = self.say(f"/view {aid}")
        self.assertIn("Budget sheet", texts[0])

    def test_view_without_number_asks(self):
        self.add_draft()
        said = self.assert_real_reply("/view")
        self.assertIn("/view", said)

    def test_approve_changes_the_draft(self):
        aid = self.add_draft()
        said = self.assert_real_reply(f"/approve {aid}")
        self.assertIn(f"#{aid}", said)
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "approved")

    def test_reject_changes_the_draft(self):
        aid = self.add_draft()
        self.assert_real_reply(f"/reject {aid}")
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "rejected")

    def test_approve_several(self):
        first, second = self.add_draft(), self.add_draft("Draft: [ADHD Planners Agent] Focus planner")
        self.assert_real_reply(f"/approve {first} {second}")
        self.assertEqual(guardrails.get_approval(second, self.db)["status"], "approved")

    def test_approve_unknown_id_is_said_plainly(self):
        said = self.assert_real_reply("/approve 999")
        self.assertIn("#999", said)

    def test_pause_and_resume(self):
        self.assert_real_reply("/pause")
        self.assertTrue(guardrails.is_paused(self.db))
        self.assert_real_reply("/resume")
        self.assertFalse(guardrails.is_paused(self.db))

    def test_packs_lists_a_ready_pack(self):
        self.add_pack()
        said = self.assert_real_reply("/packs")
        self.assertIn("Focus Planner Printable Pack", said)

    def test_pack_details(self):
        self.add_pack()
        said = self.assert_real_reply("/pack 3")
        self.assertIn("Focus Planner Printable Pack", said)

    def test_buttontest_sends_a_button(self):
        self.say("/buttontest")
        self.assertTrue(any(item.get("buttons") == [[("Test button", "t:ping")]] for item in self.sent))

    def test_help_and_start_are_the_help_text(self):
        for command in ("/help", "/start"):
            _handled, texts = self.say(command)
            self.assertEqual(texts, [telegram_inbox.HELP])

    def test_start_with_draft_payload_opens_that_draft(self):
        aid = self.add_draft()
        payload = base64.urlsafe_b64encode(str(aid).encode()).decode().rstrip("=")
        self.say(f"/start ap_{payload}")
        self.assertIn("Budget sheet", self.sent[0]["text"])
        self.assertEqual(self.sent[0]["buttons"], telegram_inbox.draft_buttons(str(aid)))

    def test_start_with_bad_payload_is_just_help(self):
        _handled, texts = self.say("/start ap_!!!")
        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].strip())

    def test_doctor_is_the_doctor_report(self):
        _handled, texts = self.say("/doctor")
        self.assertEqual(texts, [telegram_inbox.handle_command("doctor", [], self.db)])
        self.assertIn("Doctor", texts[0])

    def test_button_press_approves(self):
        aid = self.add_draft()
        handled = self.press(f"a:{aid}")
        self.assertEqual(handled, 1)
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "approved")
        self.assertTrue(self.sent or telegram_inbox.edit_text.called)    # the card is edited in place

    def test_button_press_from_stranger_is_ignored(self):
        aid = self.add_draft()
        handled = self.press(f"a:{aid}", chat=STRANGER)
        self.assertEqual(handled, 0)
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "pending")
        self.assertEqual(self.sent, [])


class PluginCommandTests(Inbox):
    """One test per COMMANDS entry of every tg_*.py is added below this class."""

    def test_every_plugin_has_commands(self):
        modules = {name for name, _ in PLUGIN_ENTRIES}
        for path in glob.glob(str(REPO / "tg_*.py")):
            self.assertIn(Path(path).stem, modules)
        self.assertGreaterEqual(len(PLUGIN_ENTRIES), 35)

    def test_entries_follow_the_interface(self):
        for module, entry in PLUGIN_ENTRIES:
            self.assertTrue(entry["cmd"].startswith("/"), module)
            self.assertIsInstance(entry["words"], list, module)
            self.assertTrue(entry["help_en"] and entry["help_te"], module)

    def test_pack_commands_with_a_number(self):
        for command in ("/files", "/covers", "/pins", "/carousel", "/reel", "/blog", "/landing", "/launch", "/kit",
                        "/ready", "/sell", "/seo", "/lint", "/faq", "/versions"):
            said = self.assert_real_reply(f"{command} 3")
            self.assertIn("3", said, command)


def _plugin_command_test(command):
    def test(self):
        self.assert_real_reply(command)
    return test


def _plugin_words_test(words):
    def test(self):
        for word in words:
            self.assert_real_reply(word)
    return test


for _module, _entry in PLUGIN_ENTRIES:
    _slug = _entry["cmd"].lstrip("/")
    setattr(PluginCommandTests, f"test_command_{_module}_{_slug}", _plugin_command_test(_entry["cmd"]))
    setattr(PluginCommandTests, f"test_words_{_module}_{_slug}", _plugin_words_test(list(_entry["words"])))


class PackFlowTests(Inbox):
    """Commands against a real temp pack."""

    def test_files_sends_the_pack_files(self):
        self.add_pack()
        self.say("/files 3")
        self.assertEqual(self.files(), ["product.zip", "cover.png", "listing.txt"])

    def test_sell_sends_fields_files_images_checklist(self):
        self.add_pack()
        _handled, texts = self.say("/sell 3")
        self.assertIn("Pack #3", texts[0])
        self.assertIn("1/4 GUMROAD FIELDS", texts[1])
        self.assertIn("Price: owner decides", texts[1])
        self.assertEqual(self.files(), ["product.zip", "LICENSE.txt"])
        self.assertEqual(self.files("photo"), ["cover.png", "preview-1.png"])
        self.assertIn("4/4 GUMROAD CHECKLIST", texts[2])

    def test_sell_alone_lists_the_pack(self):
        self.add_pack()
        _handled, texts = self.say("/sell")
        self.assertIn("/sell 3 — Focus Planner Printable Pack", texts[0])
        self.assertEqual(self.sent[0]["keyboard"], [["/sell 3"]])

    def test_kit_sends_the_gumroad_kit(self):
        self.add_pack()
        said = self.assert_real_reply("/kit 3")
        self.assertIn("gumroad.txt", self.files())
        self.assertTrue(said.strip())

    def test_ready_card(self):
        self.add_pack()
        said = self.assert_real_reply("/ready 3")
        self.assertIn("Sell Ready Card", said)

    def test_lint_seo_faq_for_the_pack(self):
        self.add_pack()
        for command, word in (("/lint 3", "Lint"), ("/seo 3", "SEO"), ("/faq 3", "FAQ")):
            said = self.assert_real_reply(command)
            self.assertIn(word, said, command)

    def test_no_money_amounts_in_sell_flow(self):
        self.add_pack()
        self.say("/sell 3")
        for item in self.sent:
            self.assertIsNone(tg_sell.MONEY_RE.search(item["text"]))


class GreetingAndUnknownTests(Inbox):
    def test_hi_gets_greeting_status_and_menu(self):
        for word in ("Hi", "hi", "hello", "namaste"):
            _handled, texts = self.say(word)
            self.assertEqual(len(texts), 1, word)
            self.assertIn("Namaste", texts[0], word)
            self.assertIn("/list", texts[0], word)

    def test_menu(self):
        said = self.assert_real_reply("/menu")
        for command in ("/list", "/packs", "/sell", "/files", "/status"):
            self.assertIn(command, said)

    def test_help_for_one_command(self):
        said = self.assert_real_reply("/help files")
        self.assertIn("/files", said)

    def test_unknown_text_gets_the_hint_once(self):
        handled, texts = self.say("nenu bayataki veltunna")
        self.assertEqual(handled, 1)
        self.assertEqual(len(texts), 1)
        self.assertIn("help", texts[0].lower())

    def test_unknown_slash_command_gets_suggestions(self):
        _handled, texts = self.say("/nosuchcommand")
        self.assertEqual(len(texts), 1)
        self.assertIn("/menu", texts[0])

    def test_typo_gets_a_did_you_mean(self):
        _handled, texts = self.say("/stats")
        self.assertIn("/status", texts[0])

    def test_empty_like_messages_do_not_crash(self):
        for text in (".", "?", "👍"):
            handled, _texts = self.say(text)
            self.assertEqual(handled, 1, text)

    def test_message_without_text_is_counted_as_other(self):
        stats = telegram_inbox.new_stats()
        self.updates = [{"update_id": 5, "message": {"message_id": 5, "chat": {"id": OWNER}, "sticker": {}}}]
        self.sent = []
        handled = telegram_inbox.process_updates("test-token", OWNER, db_path=self.db, stats=stats)
        self.assertEqual(handled, 0)
        self.assertEqual(stats["other"], 1)
        self.assertEqual(self.sent, [])


class NumberWordTests(Inbox):
    def test_telugu_ordinal_sell(self):
        self.add_pack()
        _handled, texts = self.say("moodo pack sell chey")
        self.assertIn("1/4 GUMROAD FIELDS", texts[1])
        self.assertEqual(self.files(), ["product.zip", "LICENSE.txt"])

    def test_telugu_cardinal_files(self):
        self.add_pack()
        self.say("pack moodu files pampu")
        self.assertEqual(self.files(), ["product.zip", "cover.png", "listing.txt"])

    def test_english_word_files(self):
        self.add_pack()
        self.say("pack three files pampu")
        self.assertEqual(self.files(), ["product.zip", "cover.png", "listing.txt"])

    def test_number_words_up_to_twenty(self):
        for word, number in (("okato", 1), ("aido", 5), ("padakondo", 11), ("iravai", 20), ("twelfth", 12)):
            _handled, texts = self.say(f"{word} pack sell chey")
            self.assertIn(f"Pack #{number} ", texts[0], word)

    def test_view_draft_by_number_word(self):
        aid = self.add_draft()
        self.assertEqual(aid, 1)
        _handled, texts = self.say("okato draft chupinchu")
        self.assertEqual(texts, [telegram_inbox.handle_command("view", [1], self.db)])

    def test_approve_by_number_word_asks_first(self):
        aid = self.add_draft()
        self.say("draft okati approve chey")
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "pending")
        self.assertTrue(self.sent)


class OwnerOnlyTests(Inbox):
    def test_stranger_gets_no_reply_for_any_command(self):
        aid = self.add_draft()
        self.add_pack()
        for text in ["/status", "/list", f"/approve {aid}", "/pause", "/sell 3", "/files 3", "Hi", "/menu",
                     "moodo pack sell chey", "em cheyali ippudu?", "/settings", "/sales"]:
            handled, _texts = self.say(text, chat=STRANGER)
            self.assertEqual(handled, 0, text)
            self.assertEqual(self.sent, [], text)
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "pending")
        self.assertFalse(guardrails.is_paused(self.db))

    def test_stranger_is_counted_as_skipped(self):
        stats = telegram_inbox.new_stats()
        self.updates = [self.update("/status", chat=STRANGER)]
        telegram_inbox.process_updates("test-token", OWNER, db_path=self.db, stats=stats)
        self.assertEqual(stats["skipped"]["not owner chat"], 1)
        self.assertEqual(stats["handled"], 0)

    def test_owner_and_stranger_in_one_batch(self):
        handled = self.poll([self.update("/status", chat=STRANGER), self.update("/status")])
        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(str(self.sent[0]["chat"]), str(OWNER))

    def test_stranger_update_is_still_acknowledged(self):
        self.poll([self.update("/status", chat=STRANGER, update_id=77)])
        self.assertEqual(self.acked, [77])


class DuplicateUpdateTests(Inbox):
    def test_same_update_delivered_again_is_not_answered_twice(self):
        update = self.update("/status", update_id=200)
        self.assertEqual(self.poll([update]), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.poll([update]), 0)
        self.assertEqual(self.sent, [])

    def test_offset_is_saved_after_each_update(self):
        self.poll([self.update("/status", update_id=300), self.update("/list", update_id=301)])
        self.assertEqual(int(telegram_inbox.get_state("update_offset", None, self.db)), 302)

    def test_old_update_ids_are_skipped(self):
        self.poll([self.update("/status", update_id=400)])
        self.assertEqual(self.poll([self.update("/list", update_id=399)]), 0)
        self.assertEqual(self.sent, [])

    def test_same_command_twice_in_one_batch_is_answered_once_with_count(self):
        handled = self.poll([self.update("/status"), self.update("/status")])
        self.assertEqual(handled, 2)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("(answered 2 messages)", self.sent[0]["text"])

    def test_approve_twice_in_one_batch_approves_once(self):
        aid = self.add_draft()
        self.poll([self.update(f"/approve {aid}"), self.update(f"/approve {aid}")])
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "approved")
        self.assertEqual(len(self.texts()), 1)

    def test_update_without_integer_id_is_ignored(self):
        self.assertEqual(self.poll([{"update_id": "abc", "message": {"chat": {"id": OWNER}, "text": "/status"}}]), 0)
        self.assertEqual(self.sent, [])

    def test_fetch_error_stops_before_any_reply(self):
        def broken(token, offset):
            telegram_inbox.LAST_FETCH_ERROR = "HTTP 502"
            return []
        with mock.patch.object(telegram_inbox, "fetch_updates", side_effect=broken):
            with self.assertRaises(RuntimeError):
                telegram_inbox.process_updates("test-token", OWNER, db_path=self.db)
        self.assertEqual(self.sent, [])
        telegram_inbox.LAST_FETCH_ERROR = None


class QuietHoursTests(Inbox):
    def turn_quiet_on(self):
        _handled, texts = self.say("quiet 0-23")
        self.assertIn("Quiet hours ON", texts[0])
        _handled, texts = self.say("Yes")
        self.assertIn("Setting update chesam", texts[0])

    def test_quiet_hours_can_be_set_and_confirmed(self):
        self.turn_quiet_on()
        _handled, texts = self.say("/settings")
        self.assertIn("Quiet Hours:* ON (00:00 - 23:00 UTC)", texts[0])

    def test_quiet_hours_can_be_cancelled(self):
        self.say("quiet 22-8")
        _handled, texts = self.say("No")
        self.assertIn("Cancel chesam", texts[0])
        _handled, texts = self.say("/settings")
        self.assertIn("Quiet Hours:* OFF", texts[0])

    def test_quiet_off(self):
        self.turn_quiet_on()
        self.say("quiet off")
        self.say("yes")
        _handled, texts = self.say("/settings")
        self.assertIn("Quiet Hours:* OFF", texts[0])

    def test_owner_commands_are_still_answered_in_quiet_hours(self):
        self.turn_quiet_on()
        aid = self.add_draft()
        for command in ("/status", "/list", f"/view {aid}", "/packs", "/doctor", "/sell", "/menu", "Hi",
                        "pending drafts entha", "em cheyali ippudu?"):
            self.assert_real_reply(command)

    def test_approve_still_works_in_quiet_hours(self):
        self.turn_quiet_on()
        aid = self.add_draft()
        self.say(f"/approve {aid}")
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "approved")

    def test_post_today_respects_quiet_mode(self):
        import tg_settings
        telegram_inbox.set_state(tg_settings.SETTINGS_STATE_KEY, json.dumps({"quiet_mode": True}), db_path=self.db)
        _handled, texts = self.say("/post today")
        self.assertEqual(len(texts), 1)
        self.assertIn("Quiet hours lo unnam", texts[0])
        self.assertEqual(self.files("photo"), [])

    def test_morning_setting_confirmation(self):
        self.say("morning 9")
        self.say("Yes")
        _handled, texts = self.say("/settings")
        self.assertIn("Morning Post Time:* 09:00 UTC", texts[0])


class RegressionTests(Inbox):
    """One test (or more) per bug fixed in CC1-4."""

    def test_office_request_after_office_menu_does_not_crash_or_lock(self):
        self.say("/office")                       # creates the other office_requests table layout
        handled, texts = self.say("undo")
        self.assertEqual(handled, 1)
        self.assertIn("Office lo marchutha", texts[0])
        handled, texts = self.say("theme dark")
        self.assertIn("theme dark", texts[0])
        _handled, texts = self.say("/status")      # the database is not locked
        self.assertIn("Chilli Gavva Status", texts[0])

    def test_office_request_rows_are_saved_in_both_table_layouts(self):
        telegram_inbox.store_office_request("theme", {"theme": "dark"}, db_path=self.db)
        self.say("/office")
        telegram_inbox.store_office_request("undo_last_ui_change", {}, db_path=self.db)
        conn = guardrails.connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM office_requests WHERE intent IN "
                                      "('theme', 'undo_last_ui_change')").fetchone()[0], 2)

    def test_office_request_with_strict_table(self):
        import office_requests
        conn = guardrails.connect(self.db)
        office_requests._ensure_table(conn)
        conn.commit()
        conn.close()
        telegram_inbox.store_office_request("mute", {}, db_path=self.db)
        conn = guardrails.connect(self.db)
        self.addCleanup(conn.close)
        row = conn.execute("SELECT id, kind, status, intent FROM office_requests").fetchone()
        self.assertTrue(row[0].startswith("req_"))
        self.assertEqual(row[1:], ("mute", "queued", "mute"))

    def test_office_queue_failure_is_answered_not_raised(self):
        with mock.patch.object(telegram_inbox, "store_office_request", side_effect=RuntimeError("db")):
            handled, texts = self.say("theme dark")
        self.assertEqual(handled, 1)
        self.assertIn("save kaaledu", texts[0])

    def test_doctor_is_not_answered_by_the_menu_fallback(self):
        for text in ("/doctor", "doctor"):
            _handled, texts = self.say(text)
            self.assertNotIn("ardam kaaledu", texts[0].lower())
            self.assertIn("Doctor", texts[0])

    def test_different_plugin_messages_in_one_batch_are_all_answered(self):
        handled = self.poll([self.update("robot ela undi"), self.update("em problem undi"),
                             self.update("/ideas"), self.update("/keywords")])
        self.assertEqual(handled, 4)
        texts = self.texts()
        self.assertEqual(len(texts), 4)
        self.assertIn("Chilli Gavva Status", texts[0])
        self.assertIn("Doctor", texts[1])
        self.assertIn("Keyword", texts[3])
        self.assertFalse(any("answered" in text for text in texts))

    def test_unknown_chatter_burst_still_gets_one_hint(self):
        handled = self.poll([self.update("some random text"), self.update("another random message")])
        self.assertEqual(handled, 2)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("(answered 2 messages)", self.sent[0]["text"])

    def test_pack_files_sentence_sends_files_not_pack_details(self):
        self.add_pack()
        self.say("pack 3 files pampu")
        self.assertEqual(self.files(), ["product.zip", "cover.png", "listing.txt"])

    def test_pack_sell_sentence_runs_the_sell_flow(self):
        self.add_pack()
        _handled, texts = self.say("pack 3 sell chey")
        self.assertIn("1/4 GUMROAD FIELDS", texts[1])

    def test_pack_kit_sentence_sends_the_kit(self):
        self.add_pack()
        self.say("pack 3 kit pampu")
        self.assertIn("gumroad.txt", self.files())

    def test_pack_details_sentence_still_shows_pack_details(self):
        _handled, texts = self.say("pack 3 details chupinchu")
        self.assertEqual(len(texts), 1)
        self.assertIn("#3", texts[0])
        self.assertEqual(self.files(), [])

    def test_ordinal_draft_sentence_views_that_draft(self):
        for _ in range(5):
            aid = self.add_draft()
        _handled, texts = self.say("aido draft chupinchu")
        self.assertEqual(texts, [telegram_inbox.handle_command("view", [aid], self.db)])

    def test_auto_approve_status_is_not_a_draft_approval(self):
        aid = self.add_draft()
        _handled, texts = self.say("auto approve status")
        self.assertIn("Auto-Approve Settings", texts[0])
        self.assertNotIn("Confirm: approve draft", texts[0])
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "pending")

    def test_office_status_shows_the_office(self):
        _handled, texts = self.say("office status")
        self.assertIn("Office", texts[0])
        self.assertNotIn("Chilli Gavva Status", texts[0])

    def test_slash_undo_goes_to_the_undo_plugin(self):
        _handled, texts = self.say("/undo")
        self.assertNotIn("Office lo marchutha", texts[0])
        self.assertIn("Undo", texts[0])

    def test_plain_office_words_keep_their_office_meaning(self):
        for text, word in (("undo", "undo last UI change"), ("theme dark", "theme dark"), ("mute", "mute")):
            _handled, texts = self.say(text)
            self.assertIn("Office lo marchutha", texts[0], text)
            self.assertIn(word, texts[0], text)

    def test_old_owner_sentences_still_work(self):
        self.add_draft()
        _handled, texts = self.say("list drafts")
        self.assertIn("Monthly budget sheet", texts[0])
        _handled, texts = self.say("pause all")
        self.assertIn("Confirm", texts[0])
        self.assertFalse(guardrails.is_paused(self.db))
        _handled, texts = self.say("today posts")
        self.assertEqual(texts, [telegram_inbox.today_posts_text()])

    def test_plugins_first_rules(self):
        self.assertTrue(telegram_inbox.plugins_first("pack 3 files pampu", "show_pack"))
        self.assertTrue(telegram_inbox.plugins_first("auto approve status", "approve_draft"))
        self.assertTrue(telegram_inbox.plugins_first("/undo", "undo_last_ui_change"))
        self.assertTrue(telegram_inbox.plugins_first("quiet 22-8", "mute"))
        self.assertFalse(telegram_inbox.plugins_first("undo", "undo_last_ui_change"))
        self.assertFalse(telegram_inbox.plugins_first("pack 3 details chupinchu", "show_pack"))
        self.assertFalse(telegram_inbox.plugins_first("approve 5", "approve_draft"))
        self.assertFalse(telegram_inbox.plugins_first("today posts", "post_today"))
        self.assertFalse(telegram_inbox.plugins_first("anything", "unknown"))
        self.assertFalse(telegram_inbox.plugins_first("", "show_pack"))

    def test_plugin_can_answer(self):
        for text in ("/ideas", "robot ela undi", "ready", "kit pampu", "pack 5 ki 2 sales Etsy"):
            self.assertTrue(telegram_inbox.plugin_can_answer(text), text)
        for text in ("some random text", "another random message", "", None):
            self.assertFalse(telegram_inbox.plugin_can_answer(text), text)

    def test_plugin_words_cover_every_plugin(self):
        words = telegram_inbox.plugin_words()
        for word in ("/sell", "sell", "/files", "file pampu", "office status", "auto approve", "/talk"):
            self.assertIn(word, words)

    def test_report_command_words_are_not_pack_names(self):
        for text in ("seo score", "seo check", "listing lint", "lint check", "buyer faq", "faq pampu",
                     "pack version", "version vivaralu"):
            _handled, texts = self.say(text)
            self.assertNotIn("Pack #", texts[0], text)
            self.assertIn("<pack_number>", texts[0], text)

    def test_report_pack_name_still_works(self):
        import tg_reports
        self.assertEqual(tg_reports.extract_pack_id("/seo 20-adhd-planner"), "20")
        self.assertEqual(tg_reports.extract_pack_id("/seo adhd-planner"), "adhd-planner")
        self.assertIsNone(tg_reports.extract_pack_id("seo score"))
        self.assertEqual(tg_reports.extract_pack_id("seo pack 2"), "2")

    def test_post_chey_gives_todays_posts(self):
        _handled, texts = self.say("post chey")
        _handled, expected = self.say("/post today")
        self.assertEqual(texts, expected)

    def test_bare_sale_explains_how_to_record(self):
        _handled, texts = self.say("sale")
        self.assertIn("sale pack 3 Gumroad", texts[0])
        self.assertIn("/sales", texts[0])
        self.assertIsNone(tg_sell.MONEY_RE.search(texts[0]))

    def test_sale_sentence_still_records(self):
        _handled, texts = self.say("sale pack 3 Gumroad")
        self.assertIn("Pack 3", texts[0])
        _handled, texts = self.say("/sales")
        self.assertIn("1 sale", texts[0])

    def test_help_words_show_the_menu(self):
        import tg_menu
        for text in ("ela cheyali", "ela cheyali?", "sahayam"):
            _handled, texts = self.say(text)
            self.assertEqual(texts, [tg_menu.format_grouped_menu()], text)

    def test_yes_reaches_the_settings_plugin(self):
        self.say("morning 9")
        _handled, texts = self.say("Yes")
        self.assertIn("Setting update chesam", texts[0])
        self.assertNotIn("anukunnara", texts[0])

    def test_yes_without_anything_pending_is_harmless(self):
        handled, texts = self.say("Yes")
        self.assertEqual(handled, 1)
        self.assertEqual(len(texts), 1)

    def test_typo_guess_is_still_given_when_nobody_else_answers(self):
        _handled, texts = self.say("paks")
        self.assertIn("/packs", texts[0])
        self.assertIn("anukunnara", texts[0])

    def test_quiet_wording_is_a_setting_not_office_mute(self):
        _handled, texts = self.say("quiet 22-8")
        self.assertIn("Quiet hours ON", texts[0])
        self.assertNotIn("Office lo marchutha", texts[0])

    def test_private_marketing_is_found_by_marketing_command(self):
        self.add_pack()
        self.add_private_marketing()
        _handled, texts = self.say("/marketing 3")
        self.assertNotIn("inka ledu", texts[0])
        self.assertIn("Pin title 1", texts[0])

    def test_private_marketing_pins_are_sent(self):
        self.add_pack()
        self.add_private_marketing()
        self.say("/pins 3")
        groups = [item for item in self.sent if item["kind"] == "group"]
        self.assertEqual([Path(path).name for path in groups[0]["path"]],
                         ["pin_1_a.png", "pin_2_a.png", "pin_3_a.png"])

    def test_private_marketing_landing_is_sent(self):
        self.add_pack()
        self.add_private_marketing()
        self.say("/landing 3")
        self.assertEqual(len(self.files()), 1)
        self.assertTrue(self.files()[0].endswith(".zip"))

    def test_shared_marketing_folder_still_wins(self):
        self.add_pack()
        self.add_private_marketing()
        shared = self.marketing / "3-focus-planner" / "pins"
        shared.mkdir(parents=True)
        (shared / "shared_pin.png").write_bytes(PNG)
        self.say("/pins 3")
        groups = [item for item in self.sent if item["kind"] == "group"]
        self.assertEqual([Path(path).name for path in groups[0]["path"]], ["shared_pin.png"])

    def test_single_plugin_words_run_the_command(self):
        for word, command in (("carousel", "/carousel"), ("landing page", "/landing"), ("reel", "/reel"),
                              ("launch checklist", "/launch")):
            _handled, texts = self.say(word)
            _handled, expected = self.say(command)
            self.assertEqual(texts, expected, word)

    def test_two_suggestions_question_has_no_ardam_kaaledu(self):
        _handled, texts = self.say("money vishayam")
        self.assertIn("Okati tap cheyandi", texts[0])
        self.assertNotIn("ardam kaaledu", texts[0].lower())
        self.assertEqual(self.sent[0]["keyboard"], [["/sales"], ["/menu"]])


class CleanlinessTests(Inbox):
    def test_a_full_session_writes_nothing_into_the_repo(self):
        repo_out = REPO / "exported_content"
        before = sorted(str(path) for path in repo_out.rglob("*")) if repo_out.is_dir() else []
        self.add_pack()
        self.add_draft()
        for text in ["/status", "/list", "/today", "/week", "/sell 3", "/files 3", "/versions 3", "/lint 3",
                     "/calendar", "/dashboard", "/dup", "/boards", "/posted 1", "Hi", "em cheyali ippudu?"]:
            self.say(text)
        after = sorted(str(path) for path in repo_out.rglob("*")) if repo_out.is_dir() else []
        self.assertEqual(before, after)

    def test_no_reply_ever_contains_a_token_or_money(self):
        self.add_pack()
        for text in ["/status", "/pipeline", "/health", "/logs", "/settings", "/sell 3", "/ops"]:
            self.say(text)
            for item in self.sent:
                self.assertNotIn("test-token", item["text"], text)
                self.assertIsNone(tg_sell.MONEY_RE.search(item["text"]), text)


if __name__ == "__main__":
    unittest.main()
