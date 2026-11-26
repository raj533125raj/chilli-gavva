"""CCA-4: "Ee roju ammadaniki em cheyali" — the daily sell card (talk_intents / tg_talk / morning message).

Packs live in temp folders and the robot database is a temp file. /listed (tg_listed) is not on
this branch yet, so a small stand-in module in sys.modules plays it; tg_launch is the real one.
Nothing touches Telegram: telegram_inbox.send_text is a recorder.
"""
from __future__ import annotations

import datetime
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import guardrails
import talk_intents
import telegram_inbox
import test_cc1_sell as sell_fixture      # temp packs with a fixed truth verdict; not re-collected here
import tg_settings
import tg_talk

SAMPLE_CHAT_ID = sell_fixture.SAMPLE_CHAT_ID
LINK = "https://chilli.gumroad.com/l/focus-planner"


def ist(hour, minute=0, day=14):
    """A moment given in India time, as an aware UTC datetime (November 2026)."""
    india = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    return datetime.datetime(2026, 11, day, hour, minute, tzinfo=india).astimezone(datetime.timezone.utc)


class Base(sell_fixture.Base):
    def setUp(self):
        super().setUp()
        self.db = str(self.root / "robot.db")
        self.listed = {}                      # pack id -> {"id", "url", "listed_ts"}
        module = types.ModuleType("tg_listed")
        module.listed_ids = lambda db_path=None: sorted(self.listed)
        module.get_listed = lambda pack_id, db_path=None: self.listed.get(pack_id)
        self.sent = []
        self.send_ok = True
        patches = (
            mock.patch.dict(sys.modules, {"tg_listed": module}),
            mock.patch.dict(os.environ, {"CHILLI_PRODUCTS_DIR": str(self.products),
                                         "CHILLI_MARKETING_DIR": str(self.marketing)}),
            mock.patch.object(telegram_inbox, "send_text", side_effect=self._send_text),
            mock.patch.object(telegram_inbox, "_send_text_kw",
                              side_effect=lambda token, chat, text, keyboard=None: self._send_text(token, chat, text,
                                                                                                    keyboard=keyboard)),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.now = ist(9)
        self.state = {}
        self.texts, self.keyboards = [], []
        self.ctx = {"db_path": self.db, "chat_id": str(SAMPLE_CHAT_ID), "now": self.now,
                    "products_dir": str(self.products), "marketing_dir": str(self.marketing),
                    "send_text": self._ctx_send,
                    "state_get": lambda key, default=None: self.state.get(key, default),
                    "state_set": lambda key, value: self.state.__setitem__(key, value)}

    def _send_text(self, token, chat, text, buttons=None, keyboard=None):
        self.sent.append({"chat": chat, "text": text, "keyboard": keyboard})
        return self.send_ok

    def _ctx_send(self, text, keyboard=None):
        self.texts.append(text)
        self.keyboards.append(keyboard)
        return True

    def list_pack(self, pack_id, listed_ts="2026-11-13T05:00:00Z"):
        self.listed[pack_id] = {"id": pack_id, "url": LINK, "listed_ts": listed_ts}

    def add_draft(self, title="Draft: Budget sheet (v1, llm)"):
        return guardrails.request_approval("public_draft", title, "draft text", db_path=self.db)

    def data(self):
        return tg_talk.sell_card_data(self.ctx)

    def card(self):
        return tg_talk.sell_card_text(self.data(), self.ctx)

    def morning(self, now=None):
        return telegram_inbox.send_morning_sell_card("test-token", SAMPLE_CHAT_ID, db_path=self.db,
                                                     now=self.now if now is None else now)


class IntentTests(unittest.TestCase):
    def test_sell_card_phrases(self):
        for phrase in ("ee roju ammadaniki em cheyali", "Ee roju ammadaniki em cheyali?", "ee roju sell ki em cheyali",
                       "ivala ammadaniki em cheyali", "today sell ki em cheyali", "sell card", "sell card chupinchu",
                       "ee roju em ammali", "what to sell today", "ee roju sell cheyadaniki em cheyali"):
            with self.subTest(phrase=phrase):
                found = talk_intents.detect(phrase)
                self.assertEqual(found["command"], "sellcard")
                self.assertTrue(talk_intents.should_run(found))

    def test_other_sell_phrases_keep_their_command(self):
        for phrase, command in (("pack 3 sell chey", "/sell 3"), ("em sell cheyali", "/sell"),
                                ("ee roju pack 3 sell chey", "/sell 3"), ("ee roju sales entha", "/sales today"),
                                ("em cheyali ippudu", "next"), ("ee roju em jarigindi", "daily"),
                                ("rendo pack ammali", "/sell 2")):
            with self.subTest(phrase=phrase):
                self.assertEqual(talk_intents.detect(phrase)["command"], command)

    def test_intent_is_not_risky(self):
        self.assertFalse(talk_intents.INTENT_BY_NAME["sellcard"].get("risky"))

    def test_talk_examples_show_the_card_phrase(self):
        self.assertIn("ee roju ammadaniki em cheyali  ->  ee roju sell card", tg_talk.examples_text())


class CardDataTests(Base):
    def test_sellable_pack_not_listed_is_to_list(self):
        self.make_pack("3-focus-planner")
        data = self.data()
        self.assertEqual(data["to_list"], [{"id": 3, "title": "Focus Planner Printable Pack for Daily Planning", "status": "SELLABLE"}])
        self.assertEqual(data["listed"], [])
        self.assertTrue(data["has_work"])

    def test_not_yet_pack_is_left_out(self):
        self.truth = {"verdict": "needs fix", "score": 30, "problems": ["x"]}
        self.make_pack("3-focus-planner")
        data = self.data()
        self.assertEqual(data["to_list"], [])
        self.assertFalse(data["has_work"])

    def test_listed_pack_moves_to_listed(self):
        self.make_pack("3-focus-planner")
        self.list_pack(3)
        data = self.data()
        self.assertEqual(data["to_list"], [])
        self.assertEqual(data["listed"], [{"id": 3, "title": "Focus Planner Printable Pack for Daily Planning", "launch": "running", "day": 2}])

    def test_launch_day_follows_the_listed_date(self):
        self.make_pack("3-focus-planner")
        self.list_pack(3, "2026-11-10T05:00:00Z")
        self.assertEqual(self.data()["listed"][0]["day"], 5)

    def test_launch_week_over_is_done_and_not_work(self):
        self.make_pack("3-focus-planner")
        self.list_pack(3, "2026-10-01T05:00:00Z")
        data = self.data()
        self.assertEqual(data["listed"][0]["launch"], "done")
        self.assertFalse(data["has_work"])

    def test_saved_launch_start_wins_over_the_listed_date(self):
        self.make_pack("3-focus-planner")
        self.list_pack(3, "2026-10-01T05:00:00Z")
        self.state["launch_start:3"] = "2026-11-14"
        self.assertEqual((self.data()["listed"][0]["launch"], self.data()["listed"][0]["day"]), ("running", 1))

    def test_drafts_are_counted_from_the_real_queue(self):
        first = self.add_draft()
        self.add_draft()
        self.assertEqual(self.data()["drafts"], 2)
        guardrails.decide(first, True, db_path=self.db)
        self.assertEqual(self.data()["drafts"], 1)
        self.assertTrue(self.data()["has_work"])

    def test_nothing_at_all(self):
        self.assertEqual(self.data(), {"to_list": [], "listed": [], "drafts": 0, "has_work": False})

    def test_mixed_packs(self):
        self.make_pack("3-focus-planner")
        self.make_pack("5-budget-sheet", title="Budget Sheet")
        self.make_pack("7-habit-tracker", title="Habit Tracker")
        self.list_pack(5)
        data = self.data()
        self.assertEqual([item["id"] for item in data["to_list"]], [3, 7])
        self.assertEqual([item["id"] for item in data["listed"]], [5])

    def test_without_the_listed_plugin_every_ready_pack_is_to_list(self):
        self.make_pack("3-focus-planner")
        self.list_pack(3)
        with mock.patch.dict(sys.modules, {"tg_listed": None}):
            data = self.data()
        self.assertEqual([item["id"] for item in data["to_list"]], [3])
        self.assertEqual(data["listed"], [])

    def test_broken_pack_evaluation_does_not_break_the_card(self):
        self.make_pack("3-focus-planner")
        with mock.patch("sell_ready.evaluate_pack", side_effect=RuntimeError("boom")):
            self.assertEqual(self.data()["to_list"], [])

    def test_data_never_changes_state_or_files(self):
        self.make_pack("3-focus-planner")
        self.list_pack(3)
        before = sorted(str(path) for path in self.root.rglob("*") if path.name != "robot.db")
        self.data()
        self.assertEqual(self.state, {})
        self.assertEqual(sorted(str(path) for path in self.root.rglob("*") if path.name != "robot.db"), before)


class CardTextTests(Base):
    def test_full_card(self):
        self.make_pack("3-focus-planner")
        self.make_pack("5-budget-sheet", title="Budget Sheet")
        self.list_pack(5)
        self.add_draft()
        text = self.card()
        self.assertEqual(text.splitlines()[0], "🛒 Ee roju ammadaniki em cheyali (14 Nov)")
        self.assertIn("1. Gumroad lo pettali — 1 pack(s) ready, inka list cheyaledu:", text)
        self.assertIn("   🟢 /sell 3 — Focus Planner Printable Pack for Daily Planning", text)
        self.assertIn("2. Listed packs — ee roju post (1):", text)
        self.assertIn("   📌 /launch 5 — Budget Sheet - Digital Download and Personal Use Guide (Day 2/7)", text)
        self.assertIn("3. Drafts waiting: 1 — /list", text)
        self.assertTrue(text.splitlines()[-1].startswith("Next: /listnext"))

    def test_empty_card_says_so_plainly(self):
        text = self.card()
        self.assertIn("1. Gumroad lo pettadaniki kotha ready pack ledu.", text)
        self.assertIn("2. Listed packs inka levu. Gumroad lo pettaka: /listed N <link>.", text)
        self.assertIn("3. Drafts waiting: 0.", text)

    def test_listed_pack_without_started_launch(self):
        self.make_pack("3-focus-planner")
        self.list_pack(3, "2099-01-01T00:00:00Z")
        self.assertIn("/launch 3 — Focus Planner Printable Pack for Daily Planning (launch plan start cheyandi)", self.card())

    def test_finished_launch_points_to_today(self):
        self.make_pack("3-focus-planner")
        self.list_pack(3, "2026-10-01T05:00:00Z")
        self.assertIn("2. Listed packs: 1 (launch week aipoyindi) — roju post kosam /today.", self.card())

    def test_almost_pack_has_a_yellow_mark(self):
        data = {"to_list": [{"id": 4, "title": "Study Planner", "status": "ALMOST"}], "listed": [], "drafts": 0}
        self.assertIn("🟡 /sell 4 — Study Planner", tg_talk.sell_card_text(data, self.ctx))

    def test_card_is_short_with_many_packs(self):
        data = {"to_list": [{"id": index, "title": "Pack " + "x" * 80, "status": "SELLABLE"} for index in range(1, 13)],
                "listed": [{"id": 50 + index, "title": "Listed " + "y" * 80, "launch": "running", "day": 2}
                           for index in range(9)], "drafts": 14}
        text = tg_talk.sell_card_text(data, self.ctx)
        self.assertIn("... inko 9 (/sell)", text)
        self.assertEqual(text.count("/sell "), 3)
        self.assertEqual(text.count("/launch "), 3)
        self.assertLess(len(text), 1200)
        self.assertLessEqual(len(text.splitlines()), 18)

    def test_date_is_india_time(self):
        self.ctx["now"] = datetime.datetime(2026, 11, 14, 20, 0, tzinfo=datetime.timezone.utc)   # 01:30 IST, 15 Nov
        self.assertIn("(15 Nov)", self.card())

    def test_no_money_in_the_card(self):
        import store_kits
        self.make_pack("3-focus-planner")
        self.add_draft()
        self.assertIsNone(store_kits.check_money(self.card()))


class TalkTests(Base):
    def test_plain_question_sends_the_card_with_buttons(self):
        self.make_pack("3-focus-planner")
        self.make_pack("5-budget-sheet", title="Budget Sheet")
        self.list_pack(5)
        self.add_draft()
        reply = tg_talk.handle("ee roju ammadaniki em cheyali", self.ctx)
        self.assertEqual(reply, {"handled": True})
        self.assertIn("/sell 3 — Focus Planner Printable Pack for Daily Planning", self.texts[0])
        self.assertEqual(self.keyboards[0], [["/listnext", "/sell 3", "/launch 5"], ["/list"]])

    def test_nothing_to_do_is_a_plain_text_reply(self):
        reply = tg_talk.handle("ee roju ammadaniki em cheyali", self.ctx)
        self.assertIn("kotha ready pack ledu", reply["text"])
        self.assertEqual(self.texts, [])

    def test_without_send_text_the_card_comes_back_as_text(self):
        self.make_pack("3-focus-planner")
        ctx = {key: value for key, value in self.ctx.items() if key != "send_text"}
        self.assertIn("/sell 3", tg_talk.handle("sell card", ctx)["text"])

    def test_buttons_are_at_most_three_per_row(self):
        for number in (3, 4, 5, 6):
            self.make_pack(f"{number}-pack-{number}", title=f"Pack {number}")
        self.add_draft()
        tg_talk.handle("sell card", self.ctx)
        self.assertEqual(self.keyboards[0], [["/listnext", "/sell 3", "/sell 4"], ["/list"]])

    def test_through_call_plugins(self):
        self.make_pack("3-focus-planner")
        handled, reply = telegram_inbox.call_plugins("Ee roju ammadaniki em cheyali?", "test-token", SAMPLE_CHAT_ID,
                                                     db_path=self.db, now=self.now)
        self.assertTrue(handled)
        self.assertEqual(reply, {"handled": True})
        self.assertIn("🛒 Ee roju ammadaniki em cheyali (14 Nov)", self.sent[0]["text"])
        self.assertEqual(self.sent[0]["keyboard"], [["/listnext", "/sell 3"]])

    def test_through_process_updates_owner_only(self):
        self.make_pack("3-focus-planner")
        updates = [{"update_id": 1, "message": {"chat": {"id": int("9999" "0000")}, "text": "sell card"}},
                   {"update_id": 2, "message": {"chat": {"id": SAMPLE_CHAT_ID}, "text": "sell card"}}]
        with mock.patch.object(telegram_inbox, "fetch_updates", return_value=updates), \
                mock.patch.object(telegram_inbox, "acknowledge_update", return_value=True), \
                mock.patch.object(telegram_inbox, "_call", return_value={"ok": True}):
            telegram_inbox.process_updates("test-token", SAMPLE_CHAT_ID, db_path=self.db)
        cards = [item for item in self.sent if "Ee roju ammadaniki" in item["text"]]
        self.assertEqual(len(cards), 1)
        self.assertEqual(str(cards[0]["chat"]), str(SAMPLE_CHAT_ID))

    def test_asking_never_starts_a_launch_or_lists_anything(self):
        self.make_pack("3-focus-planner")
        tg_talk.handle("sell card", self.ctx)
        self.assertEqual(self.state, {})
        self.assertEqual(self.listed, {})


class MorningCardTests(Base):
    def setUp(self):
        super().setUp()
        self.make_pack("3-focus-planner")

    def test_sent_once_after_eight_india_time(self):
        self.assertTrue(self.morning(ist(8, 5)))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(str(self.sent[0]["chat"]), str(SAMPLE_CHAT_ID))
        self.assertIn("🛒 Ee roju ammadaniki em cheyali (14 Nov)", self.sent[0]["text"])
        self.assertIn("/sell 3", self.sent[0]["text"])

    def test_not_before_eight(self):
        for hour in (0, 3, 6, 7):
            with self.subTest(hour=hour):
                self.assertFalse(self.morning(ist(hour, 59)))
        self.assertEqual(self.sent, [])

    def test_not_at_night(self):
        for hour in (22, 23):
            with self.subTest(hour=hour):
                self.assertFalse(self.morning(ist(hour)))
        self.assertEqual(self.sent, [])

    def test_never_twice_the_same_day(self):
        self.assertTrue(self.morning(ist(9)))
        for hour in (9, 12, 18, 21):
            self.assertFalse(self.morning(ist(hour, 30)))
        self.assertEqual(len(self.sent), 1)

    def test_again_the_next_morning(self):
        self.assertTrue(self.morning(ist(9, day=14)))
        self.assertFalse(self.morning(ist(7, day=15)))
        self.assertTrue(self.morning(ist(8, day=15)))
        self.assertIn("(15 Nov)", self.sent[1]["text"])

    def test_late_first_run_still_sends_that_day(self):
        self.assertTrue(self.morning(ist(16)))

    def test_quiet_hours_are_respected(self):
        settings = tg_settings.load_settings(self.db)
        settings["quiet_hours"] = {"enabled": True, "start": 21, "end": 11}
        self.assertTrue(tg_settings.save_settings(settings, self.db))
        self.assertFalse(self.morning(ist(9)))
        self.assertFalse(self.morning(ist(10, 59)))
        self.assertEqual(self.sent, [])
        self.assertTrue(self.morning(ist(11)))                    # first run after quiet hours end

    def test_quiet_hours_switched_off_do_not_block(self):
        settings = tg_settings.load_settings(self.db)
        settings["quiet_hours"] = {"enabled": False, "start": 0, "end": 23}
        tg_settings.save_settings(settings, self.db)
        self.assertTrue(self.morning(ist(9)))

    def test_owner_morning_hour_setting_is_respected(self):
        settings = tg_settings.load_settings(self.db)
        settings["morning_post_hour"] = 10
        tg_settings.save_settings(settings, self.db)
        self.assertFalse(self.morning(ist(9, 30)))
        self.assertTrue(self.morning(ist(10)))

    def test_nothing_to_do_sends_nothing_and_waits_for_tomorrow(self):
        self.truth = {"verdict": "needs fix", "score": 30, "problems": ["x"]}
        self.assertFalse(self.morning(ist(9)))
        self.assertEqual(self.sent, [])
        self.assertEqual(telegram_inbox.get_state(telegram_inbox.MORNING_SELL_CARD_STATE, None, self.db), "2026-11-14")
        self.truth = {"verdict": "ready", "score": 100, "problems": []}
        self.assertFalse(self.morning(ist(12)))                   # already looked today
        self.assertTrue(self.morning(ist(9, day=15)))

    def test_failed_send_is_retried_on_the_next_run(self):
        self.send_ok = False
        self.assertFalse(self.morning(ist(9)))
        self.assertIsNone(telegram_inbox.get_state(telegram_inbox.MORNING_SELL_CARD_STATE, None, self.db))
        self.send_ok = True
        self.assertTrue(self.morning(ist(9, 20)))

    def test_drafts_alone_are_enough_for_a_card(self):
        self.truth = {"verdict": "needs fix", "score": 30, "problems": ["x"]}
        self.add_draft()
        self.assertTrue(self.morning(ist(9)))
        self.assertIn("3. Drafts waiting: 1 — /list", self.sent[0]["text"])

    def test_listed_pack_gets_its_launch_line(self):
        self.list_pack(3)
        self.assertTrue(self.morning(ist(9)))
        self.assertIn("/launch 3 — Focus Planner Printable Pack for Daily Planning (Day 2/7)", self.sent[0]["text"])

    def test_only_one_text_goes_out_and_nothing_else(self):
        with mock.patch.object(telegram_inbox, "send_document") as document, \
                mock.patch.object(telegram_inbox, "send_photo") as photo, \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            self.assertTrue(self.morning(ist(9)))
        document.assert_not_called()
        photo.assert_not_called()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.listed, {})                          # nothing was marked listed

    def test_timestamp_and_naive_datetime_are_accepted(self):
        self.assertTrue(self.morning(ist(9).timestamp()))
        self.assertFalse(self.morning(datetime.datetime(2026, 11, 14, 4, 0)))    # naive = UTC, same day

    def test_the_card_is_short(self):
        self.morning(ist(9))
        self.assertLess(len(self.sent[0]["text"]), 600)

    def test_inbox_cycle_calls_it_only_outside_test_runs(self):
        source = Path(telegram_inbox.__file__).read_text(encoding="utf-8")
        self.assertIn('else send_morning_sell_card(token, chat_id, db_path=db_path))', source)
        self.assertIn('"morning_sell_card": sell_card_sent', source)


if __name__ == "__main__":
    unittest.main()
