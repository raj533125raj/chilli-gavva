"""CCA-3: 7-day launch plan per pack (launch_plan.py) and /launch N (tg_launch plugin).

Packs and marketing folders are temp folders with small real files. Nothing touches Telegram: ctx
send functions are recorders, and the reachability tests replace telegram_inbox's send functions.
Nothing is posted anywhere by the code under test; the tests also check that it never tries.
"""
from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import launch_plan
import tg_launch

SAMPLE_CHAT_ID = int("1234" "5678")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
NOW = datetime.datetime(2026, 11, 14, 4, 0, tzinfo=datetime.timezone.utc)      # 09:30 IST, 14 Nov
LINK = "https://chilli.gumroad.com/l/focus-planner"
CAPTIONS = """Captions: Focus Planner
Copy-paste chesi meere post cheyandi. Robot edi post cheyadu.

== Pin 1: hero ==
Title: Focus Planner For Calm Days
English: Plan one calm day at a time with this printable focus planner.
Tenglish: Ee focus planner tho roju ni calm ga plan cheyandi.
Alt text: A printable focus planner page on a desk.
Hashtags: #focusplanner #dailyplanner #printable

== Pin 2: fan ==
Title: Three Pages To Plan Your Day
English: Three simple pages: priorities, habits and notes.
Tenglish: Moodu simple pages: priorities, habits, notes.
Alt text: Three planner pages fanned out.
Hashtags: #focusplanner #dailyplanner #printable
"""


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.products = self.root / "exported_content" / "products"
        self.products.mkdir(parents=True)
        self.marketing = self.root / "exported_content" / "marketing"
        self.state = {}
        self.texts, self.keyboards, self.photos, self.documents = [], [], [], []
        self.ctx = {
            "db_path": str(self.root / "robot.db"), "chat_id": str(SAMPLE_CHAT_ID), "now": NOW,
            "products_dir": str(self.products), "marketing_dir": str(self.marketing),
            "send_text": self.send_text, "send_photo": self.send_photo, "send_document": self.send_document,
            "state_get": lambda key, default=None: self.state.get(key, default),
            "state_set": lambda key, value: self.state.__setitem__(key, value),
        }
        patcher = mock.patch.dict(sys.modules, {"tg_listed": None})      # /listed is not installed by default
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- recorders --
    def send_text(self, text, keyboard=None):
        self.texts.append(text)
        self.keyboards.append(keyboard)
        return True

    def send_photo(self, path, caption=""):
        self.photos.append((path, caption))
        return True

    def send_document(self, path, caption=""):
        self.documents.append((path, caption))
        return True

    # -- fixtures --
    def make_pack(self, pack_id=3, slug="focus-planner", title="Focus Planner Printable Pack", cover=True,
                  tags="focus planner, daily planner, Canva template, printable"):
        pack = self.products / f"{pack_id}-{slug}"
        pack.mkdir(parents=True)
        with zipfile.ZipFile(pack / "product.zip", "w") as bundle:
            bundle.writestr("planner.pdf", b"%PDF-1.4")
        (pack / "listing.txt").write_text(
            f"Title: {title}\n\nDescription:\nA calm planner for busy days. Print it and write.\n\nTags: {tags}\n",
            encoding="utf-8")
        if cover:
            (pack / "cover.png").write_bytes(PNG)
        return pack

    def make_marketing(self, pack, pins=5, posts=3, stories=2, captions=True, pins_json=False, posts_json=True):
        base = self.marketing / pack.name
        (base / "pins").mkdir(parents=True, exist_ok=True)
        (base / "posts").mkdir(parents=True, exist_ok=True)
        for index in range(1, pins + 1):
            (base / "pins" / f"pin_{index}.png").write_bytes(PNG)
        post_meta = []
        for index in range(1, posts + 1):
            name = f"post_{index}_square.png"
            (base / "posts" / name).write_bytes(PNG)
            post_meta.append({"file": name, "layout": "square", "title": f"Square {index}",
                              "caption_en": f"Square post caption number {index}.", "caption_te": "Tenglish caption.",
                              "alt": f"Square image {index}"})
        for index in range(1, stories + 1):
            name = f"story_{index}_tall.png"
            (base / "posts" / name).write_bytes(PNG)
            post_meta.append({"file": name, "layout": "story", "title": f"Story {index}",
                              "caption_en": f"Story caption number {index}.", "alt": f"Story image {index}"})
        if posts_json and post_meta:
            (base / "posts" / "posts.json").write_text(json.dumps(post_meta), encoding="utf-8")
        if pins_json:
            (base / "pins" / "pins.json").write_text(json.dumps(
                [{"file": f"pin_{index}.png", "title": f"Json title {index}", "description": f"Json description {index}.",
                  "alt": f"Json alt {index}"} for index in range(1, pins + 1)]), encoding="utf-8")
        if captions:
            (base / "captions.txt").write_text(CAPTIONS, encoding="utf-8")
        return base

    def plan(self, pack=None, **marketing):
        pack = pack or self.make_pack()
        base = self.make_marketing(pack, **marketing)
        return launch_plan.build_plan(pack, base, LINK)

    def say(self, text):
        return tg_launch.handle(text, self.ctx)

    def install_listed(self, listed):
        """Stand-in for tg_listed: {pack id: {"url", "listed_ts"}}."""
        module = types.ModuleType("tg_listed")
        module.get_listed = lambda pack_id, db_path=None: listed.get(pack_id)
        patcher = mock.patch.dict(sys.modules, {"tg_listed": module})
        patcher.start()
        self.addCleanup(patcher.stop)


class CollectAssetsTests(Base):
    def test_real_images_are_found_by_kind(self):
        pack = self.make_pack()
        assets = launch_plan.collect_assets(self.make_marketing(pack))
        self.assertEqual([item["name"] for item in assets["pins"]], [f"pin_{index}.png" for index in range(1, 6)])
        self.assertEqual([item["name"] for item in assets["posts"]], [f"post_{index}_square.png" for index in range(1, 4)])
        self.assertEqual([item["name"] for item in assets["stories"]], ["story_1_tall.png", "story_2_tall.png"])

    def test_natural_order(self):
        pack = self.make_pack()
        assets = launch_plan.collect_assets(self.make_marketing(pack, pins=11))
        self.assertEqual([item["name"] for item in assets["pins"]][:3], ["pin_1.png", "pin_2.png", "pin_3.png"])
        self.assertEqual(assets["pins"][-1]["name"], "pin_11.png")

    def test_pin_captions_come_from_captions_txt(self):
        assets = launch_plan.collect_assets(self.make_marketing(self.make_pack()))
        self.assertEqual(assets["pins"][0]["title"], "Focus Planner For Calm Days")
        self.assertEqual(assets["pins"][0]["caption"], "Plan one calm day at a time with this printable focus planner.")
        self.assertEqual(assets["pins"][1]["caption"], "Three simple pages: priorities, habits and notes.")
        self.assertEqual(assets["pins"][2]["caption"], assets["pins"][0]["caption"])      # 2 blocks, cycled
        self.assertEqual(assets["pins"][0]["alt"], "A printable focus planner page on a desk.")

    def test_pins_json_title_wins_and_description_is_the_fallback_caption(self):
        assets = launch_plan.collect_assets(self.make_marketing(self.make_pack(), captions=False, pins_json=True))
        self.assertEqual(assets["pins"][0]["title"], "Json title 1")
        self.assertEqual(assets["pins"][0]["caption"], "Json description 1.")
        self.assertEqual(assets["pins"][0]["alt"], "Json alt 1")

    def test_post_captions_come_from_posts_json(self):
        assets = launch_plan.collect_assets(self.make_marketing(self.make_pack()))
        self.assertEqual(assets["posts"][1]["caption"], "Square post caption number 2.")
        self.assertEqual(assets["stories"][0]["caption"], "Story caption number 1.")

    def test_missing_folder_gives_empty_lists(self):
        for folder in (None, self.root / "nope"):
            assets = launch_plan.collect_assets(folder)
            self.assertEqual((assets["pins"], assets["posts"], assets["stories"]), ([], [], []))

    def test_non_images_and_json_are_not_assets(self):
        base = self.make_marketing(self.make_pack(), pins=1, posts=0, stories=0)
        (base / "pins" / "ab_titles.txt").write_text("x", encoding="utf-8")
        (base / "pins" / "pins.json").write_text("[]", encoding="utf-8")
        self.assertEqual([item["name"] for item in launch_plan.collect_assets(base)["pins"]], ["pin_1.png"])

    def test_symlinked_images_are_ignored(self):
        base = self.make_marketing(self.make_pack(), pins=1, posts=0, stories=0)
        outside = self.root / "private.png"
        outside.write_bytes(PNG)
        try:
            (base / "pins" / "pin_2.png").symlink_to(outside)
        except OSError:
            self.skipTest("symlinks not available")
        self.assertEqual([item["name"] for item in launch_plan.collect_assets(base)["pins"]], ["pin_1.png"])

    def test_broken_json_is_ignored(self):
        base = self.make_marketing(self.make_pack(), captions=False)
        (base / "posts" / "posts.json").write_text("{not json", encoding="utf-8")
        assets = launch_plan.collect_assets(base)
        self.assertEqual(len(assets["posts"]), 3)
        self.assertEqual(assets["posts"][0]["caption"], "")

    def test_landing_and_faq_flags(self):
        base = self.make_marketing(self.make_pack())
        self.assertFalse(launch_plan.collect_assets(base)["landing"])
        (base / "landing").mkdir()
        (base / "landing" / "index.html").write_text("<html></html>", encoding="utf-8")
        (base / "FAQ.md").write_text("# FAQ", encoding="utf-8")
        assets = launch_plan.collect_assets(base)
        self.assertTrue(assets["landing"] and assets["faq"])

    def test_parse_captions_file(self):
        path = self.root / "captions.txt"
        path.write_text(CAPTIONS, encoding="utf-8")
        blocks = launch_plan.parse_captions_file(path)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[1]["title"], "Three Pages To Plan Your Day")
        self.assertEqual(blocks[0]["hashtags"], "#focusplanner #dailyplanner #printable")
        self.assertEqual(launch_plan.parse_captions_file(self.root / "missing.txt"), [])


class BuildPlanTests(Base):
    def test_seven_days_one_post_each(self):
        plan = self.plan()
        self.assertEqual([day["day"] for day in plan["days"]], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(len(plan["days"]), launch_plan.DAYS)

    def test_week_shape_pins_post_story(self):
        plan = self.plan()
        self.assertEqual([day["kind"] for day in plan["days"]], ["pin", "pin", "post", "pin", "story", "pin", "pin"])
        self.assertEqual([day["asset_name"] for day in plan["days"]],
                         ["pin_1.png", "pin_2.png", "post_1_square.png", "pin_3.png", "story_1_tall.png",
                          "pin_4.png", "pin_5.png"])

    def test_every_named_file_exists(self):
        for day in self.plan()["days"]:
            self.assertTrue(Path(day["asset"]).is_file(), day["asset_name"])

    def test_no_image_is_used_twice_while_others_are_unused(self):
        names = [day["asset_name"] for day in self.plan()["days"]]
        self.assertEqual(len(names), len(set(names)))

    def test_only_pins_fills_every_day_with_pins(self):
        plan = self.plan(pins=7, posts=0, stories=0)
        self.assertEqual([day["asset_name"] for day in plan["days"]], [f"pin_{index}.png" for index in range(1, 8)])
        self.assertEqual(plan["days"][2]["kind"], "pin")
        self.assertEqual(plan["missing"], ["square posts / stories"])

    def test_few_images_are_reused_with_a_note(self):
        plan = self.plan(pins=2, posts=0, stories=0)
        names = [day["asset_name"] for day in plan["days"]]
        self.assertEqual(set(names), {"pin_1.png", "pin_2.png"})
        self.assertEqual(plan["days"][0]["note"], "")
        self.assertIn("mundu vadinde", plan["days"][2]["note"])
        self.assertLessEqual(max(names.count(name) for name in set(names)) - min(names.count(name) for name in set(names)), 1)

    def test_no_marketing_uses_the_pack_cover(self):
        pack = self.make_pack()
        plan = launch_plan.build_plan(pack, None, LINK)
        self.assertEqual({day["asset_name"] for day in plan["days"]}, {"cover.png"})
        self.assertIn("Marketing images levu", plan["days"][0]["note"])
        self.assertEqual(plan["missing"], ["Pins", "square posts / stories"])

    def test_no_marketing_and_no_cover_is_a_text_post(self):
        pack = self.make_pack(cover=False)
        plan = launch_plan.build_plan(pack, None, LINK)
        self.assertEqual({day["kind"] for day in plan["days"]}, {"text"})
        self.assertTrue(all(day["asset"] is None for day in plan["days"]))
        self.assertIn("text post", plan["days"][0]["note"])

    def test_caption_has_link_and_hashtags(self):
        caption = self.plan()["days"][0]["caption"]
        self.assertTrue(caption.startswith("Plan one calm day at a time with this printable focus planner."))
        self.assertIn(f"Link: {LINK}", caption)
        self.assertIn("#focusplanner #dailyplanner #printable", caption)

    def test_caption_without_link_has_the_placeholder(self):
        pack = self.make_pack()
        plan = launch_plan.build_plan(pack, self.make_marketing(pack), None)
        self.assertIn(f"Link: {launch_plan.LINK_PLACEHOLDER}", plan["days"][0]["caption"])
        self.assertEqual(plan["store_url"], "")

    def test_caption_falls_back_to_the_listing(self):
        plan = self.plan(captions=False, posts_json=False)
        self.assertTrue(plan["days"][0]["caption"].startswith(
            "Focus Planner Printable Pack. A calm planner for busy days."))

    def test_hashtags_from_listing_tags_have_no_brands(self):
        plan = self.plan(captions=False, posts_json=False)
        self.assertIn("#focusplanner #dailyplanner #printable", plan["days"][0]["caption"])
        self.assertNotIn("canva", plan["days"][0]["caption"].lower())

    def test_caption_with_money_is_replaced(self):
        pack = self.make_pack()
        base = self.make_marketing(pack)
        (base / "captions.txt").write_text(CAPTIONS.replace("Plan one calm day at a time", "Only $5 today, plan one day"),
                                           encoding="utf-8")
        caption = launch_plan.build_plan(pack, base, LINK)["days"][0]["caption"]
        self.assertNotIn("$5", caption)
        self.assertTrue(caption.startswith("Focus Planner Printable Pack."))

    def test_pack_id_comes_from_the_folder_name(self):
        self.assertEqual(self.plan()["pack_id"], 3)
        self.assertEqual(launch_plan.build_plan(self.make_pack(pack_id=12, slug="x"), None, None, pack_id=99)["pack_id"], 99)

    def test_counts(self):
        self.assertEqual(self.plan()["counts"], {"pins": 5, "posts": 3, "stories": 2})

    def test_plan_is_the_same_twice(self):
        pack = self.make_pack()
        base = self.make_marketing(pack)
        self.assertEqual(launch_plan.build_plan(pack, base, LINK), launch_plan.build_plan(pack, base, LINK))

    def test_plan_never_writes_into_pack_or_marketing(self):
        pack = self.make_pack()
        base = self.make_marketing(pack)
        before = sorted(str(path) for path in self.root.rglob("*"))
        plan = launch_plan.build_plan(pack, base, LINK)
        launch_plan.overview_text(plan)
        for day in range(1, 8):
            launch_plan.day_card(plan, day)
        self.assertEqual(sorted(str(path) for path in self.root.rglob("*")), before)

    def test_listing_facts(self):
        facts = launch_plan.listing_facts(self.make_pack())
        self.assertEqual(facts["title"], "Focus Planner Printable Pack")
        self.assertEqual(facts["sentence"], "A calm planner for busy days.")
        self.assertEqual(facts["tags"][0], "focus planner")
        self.assertEqual(launch_plan.listing_facts(self.root / "missing")["title"], "missing")

    def test_hashtags_from(self):
        self.assertEqual(launch_plan.hashtags_from(["Focus Planner", "daily-planner", "Canva template", "focus planner"]),
                         "#focusplanner #dailyplanner")
        self.assertEqual(len(launch_plan.hashtags_from([f"tag {index}" for index in range(20)]).split()), 8)
        self.assertEqual(launch_plan.hashtags_from([], "#one #two #Pinterest"), "#one #two")


class TextTests(Base):
    def test_overview_lists_all_seven_days(self):
        text = launch_plan.overview_text(self.plan())
        for day in range(1, 8):
            self.assertIn(f"Day {day} —", text)
        self.assertIn("🚀 Pack #3 — 7 rojula launch plan", text)
        self.assertIn("Robot edi post cheyadu", text)
        self.assertIn("5 Pins, 3 posts, 2 stories", text)

    def test_overview_marks_today(self):
        text = launch_plan.overview_text(self.plan(), today_day=3)
        self.assertIn("👉 Day 3 —", text)
        self.assertIn("• Day 2 —", text)

    def test_overview_says_what_is_missing(self):
        pack = self.make_pack()
        text = launch_plan.overview_text(launch_plan.build_plan(pack, None, None))
        self.assertIn("Levu: Pins, square posts / stories. Images kosam /marketing 3 chudandi.", text)
        self.assertIn("inka ivvaledu", text)

    def test_day_card_for_a_pin(self):
        card = launch_plan.day_card(self.plan(), 1)
        self.assertIn("📅 Pack #3 — Day 1/7: Launch day", card)
        self.assertIn("Em post cheyali: Pinterest Pin", card)
        self.assertIn("Image: pin_1.png", card)
        self.assertIn("TITLE (copy cheyandi):\nFocus Planner For Calm Days", card)
        self.assertIn("CAPTION (copy cheyandi):\nPlan one calm day at a time", card)
        self.assertIn("ALT TEXT:\nA printable focus planner page on a desk.", card)
        self.assertIn("[ ] 1. Pinterest lo 'Create' > 'Create Pin' click cheyandi.", card)
        self.assertIn("[ ] 4. Board select chesi 'Publish' meere click cheyandi.", card)
        self.assertIn("[ ] 5. Gumroad product page open chesi", card)

    def test_day_card_for_a_square_post(self):
        card = launch_plan.day_card(self.plan(), 3)
        self.assertIn("Em post cheyali: Square post (Instagram / Facebook)", card)
        self.assertIn("Square post caption number 1.", card)
        self.assertIn("bio lo leda first comment lo", card)
        self.assertNotIn("TITLE (copy cheyandi)", card)

    def test_day_card_for_a_story(self):
        card = launch_plan.day_card(self.plan(), 5)
        self.assertIn("Story (Instagram / WhatsApp)", card)
        self.assertIn("Link sticker add chesi", card)

    def test_day_seven_asks_for_a_review(self):
        self.assertIn("ekkuva clicks vachayo chusi", launch_plan.day_card(self.plan(), 7))

    def test_card_without_link_tells_how_to_add_it(self):
        pack = self.make_pack()
        card = launch_plan.day_card(launch_plan.build_plan(pack, self.make_marketing(pack), None), 2)
        self.assertIn("/listed 3 <link>", card)

    def test_every_card_says_the_robot_does_not_post(self):
        plan = self.plan()
        for day in range(1, 8):
            with self.subTest(day=day):
                card = launch_plan.day_card(plan, day)
                self.assertIn("Robot edi post cheyadu", card)
                self.assertIn("meere click cheyandi", card)
                self.assertLess(len(card), 3500)

    def test_day_out_of_range_is_an_error(self):
        for day in (0, 8, -1):
            with self.assertRaises(ValueError):
                launch_plan.day_card(self.plan(pack=self.make_pack(pack_id=20 + abs(day), slug=f"p{abs(day)}")), day)

    def test_no_money_anywhere(self):
        import store_kits
        plan = self.plan()
        for text in [launch_plan.overview_text(plan)] + [launch_plan.day_card(plan, day) for day in range(1, 8)]:
            self.assertIsNone(store_kits.check_money(text))

    def test_clean_replaces_money(self):
        self.assertEqual(launch_plan.clean("Only $5 or 300 rupees"), "Only [amount] or [amount]")

    def test_day_number(self):
        start = datetime.date(2026, 11, 14)
        self.assertEqual(launch_plan.day_number(start, start), 1)
        self.assertEqual(launch_plan.day_number(start, datetime.date(2026, 11, 20)), 7)
        self.assertEqual(launch_plan.day_number(start, datetime.date(2026, 11, 21)), 8)
        self.assertEqual(launch_plan.day_number(start, datetime.date(2026, 11, 13)), 0)

    def test_cli_prints_overview_and_day(self):
        pack = self.make_pack()
        base = self.make_marketing(pack)
        for argv, expected in (([str(pack), "--marketing", str(base)], "7 rojula launch plan"),
                               ([str(pack), "--marketing", str(base), "--day", "2", "--link", LINK], "Day 2/7")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(launch_plan.main(argv), 0)
            self.assertIn(expected, out.getvalue())

    def test_cli_errors(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(launch_plan.main([str(self.root / "nope")]), 1)
            self.assertEqual(launch_plan.main([str(self.make_pack()), "--day", "9"]), 2)


class ParseTests(unittest.TestCase):
    def test_plan_forms(self):
        for text in ("/launch 3", "/launch #3", "/launch pack 3", "/LAUNCH 3", "/launch@chilli_bot 3", "launch plan 3",
                     "launch week 3", "7 day plan 3", "pack 3 launch plan", "3 launch plan", "pack 3 launch plan chupinchu",
                     "3 ki launch plan kavali", "/launch 3?"):
            with self.subTest(text=text):
                self.assertEqual(tg_launch.parse(text), ("plan", 3, None))

    def test_day_forms(self):
        for text in ("/launch 3 day 2", "/launch 3 2", "/launch 3 day2", "launch plan 3 day 2", "pack 3 launch day 2",
                     "3 launch plan day 2"):
            with self.subTest(text=text):
                self.assertEqual(tg_launch.parse(text), ("day", 3, 2))

    def test_restart_forms(self):
        for word in ("restart", "reset", "malli", "start"):
            with self.subTest(word=word):
                self.assertEqual(tg_launch.parse(f"/launch 3 {word}"), ("restart", 3, None))

    def test_list_forms(self):
        for text in ("/launch", "launch plan", "launch week", "7 day plan", "/launch@chilli_bot", " /launch "):
            with self.subTest(text=text):
                self.assertEqual(tg_launch.parse(text), ("list", None, None))

    def test_old_checklist_words_are_left_to_tg_files(self):
        for text in ("launch checklist 3", "launch 3 pampu", "launch pampu", "launch checklist", "launch 3", "launch"):
            with self.subTest(text=text):
                self.assertIsNone(tg_launch.parse(text))

    def test_other_text_is_not_for_this_plugin(self):
        for text in ("", "hi", "/sell 3", "/launchpad 3", "/launch abc", "pack 3", "launch plan abc", None, 5, "x" * 200):
            with self.subTest(text=text):
                self.assertIsNone(tg_launch.parse(text))

    def test_interface(self):
        self.assertEqual(tg_launch.COMMANDS[0]["cmd"], "/launch")
        self.assertTrue(tg_launch.COMMANDS[0]["help_en"] and tg_launch.COMMANDS[0]["help_te"])
        self.assertGreater(tg_launch.PRIORITY, 0)


class PluginTests(Base):
    def test_launch_n_sends_overview_image_and_todays_card(self):
        pack = self.make_pack()
        self.make_marketing(pack)
        self.assertEqual(self.say("/launch 3"), {"handled": True})
        self.assertIn("7 rojula launch plan", self.texts[0])
        self.assertIn("Launch start: 2026-11-14", self.texts[0])
        self.assertIn("Ee roju: Day 1/7.", self.texts[0])
        self.assertEqual([(Path(path).name, caption) for path, caption in self.photos],
                         [("pin_1.png", "Pack #3 Day 1 image: pin_1.png")])
        self.assertIn("Day 1/7: Launch day", self.texts[1])
        self.assertEqual(self.keyboards[1], [["/launch 3 day 2"], ["/launch 3"]])

    def test_first_use_saves_the_start_date(self):
        self.make_marketing(self.make_pack())
        self.say("/launch 3")
        self.assertEqual(self.state["launch_start:3"], "2026-11-14")

    def test_three_days_later_it_is_day_four(self):
        self.make_marketing(self.make_pack())
        self.say("/launch 3")
        self.ctx["now"] = NOW + datetime.timedelta(days=3)
        self.texts.clear()
        self.photos.clear()
        self.keyboards.clear()
        self.say("/launch 3")
        self.assertIn("Ee roju: Day 4/7.", self.texts[0])
        self.assertIn("👉 Day 4 —", self.texts[0])
        self.assertEqual(Path(self.photos[0][0]).name, "pin_3.png")
        self.assertEqual(self.keyboards[1], [["/launch 3 day 3", "/launch 3 day 5"], ["/launch 3"]])

    def test_day_changes_at_midnight_india_time(self):
        self.make_marketing(self.make_pack())
        self.ctx["now"] = datetime.datetime(2026, 11, 14, 18, 0, tzinfo=datetime.timezone.utc)   # 23:30 IST
        self.say("/launch 3")
        self.assertEqual(self.state["launch_start:3"], "2026-11-14")
        self.ctx["now"] = datetime.datetime(2026, 11, 14, 19, 0, tzinfo=datetime.timezone.utc)   # 00:30 IST next day
        self.texts.clear()
        self.say("/launch 3")
        self.assertIn("Ee roju: Day 2/7.", self.texts[0])

    def test_after_the_week_it_says_so_and_sends_no_image(self):
        self.make_marketing(self.make_pack())
        self.say("/launch 3")
        self.ctx["now"] = NOW + datetime.timedelta(days=9)
        self.photos.clear()
        reply = self.say("/launch 3")
        self.assertIn("Launch week aipoyindi (Day 10)", reply["text"])
        self.assertIn("/launch 3 restart", reply["text"])
        self.assertEqual(self.photos, [])

    def test_restart_sets_day_one_to_today(self):
        self.make_marketing(self.make_pack())
        self.say("/launch 3")
        self.ctx["now"] = NOW + datetime.timedelta(days=9)
        reply = self.say("/launch 3 restart")
        self.assertIn("Day 1 = 2026-11-23", reply["text"])
        self.texts.clear()
        self.say("/launch 3")
        self.assertIn("Ee roju: Day 1/7.", self.texts[0])

    def test_day_k_sends_that_day(self):
        self.make_marketing(self.make_pack())
        self.assertEqual(self.say("/launch 3 day 5"), {"handled": True})
        self.assertEqual(Path(self.photos[0][0]).name, "story_1_tall.png")
        self.assertIn("Day 5/7: Story reminder", self.texts[0])
        self.assertNotIn("launch_start:3", self.state)            # looking at a day does not start the week

    def test_day_out_of_range(self):
        self.make_marketing(self.make_pack())
        for day in (8, 12):
            self.assertIn("Day 1 nundi 7 varaku matrame", self.say(f"/launch 3 day {day}")["text"])

    def test_unknown_pack(self):
        for text in ("/launch 44", "/launch 44 day 2", "/launch 44 restart"):
            with self.subTest(text=text):
                self.assertIn("Pack #44 ready upload pack dorakaledu", self.say(text)["text"])

    def test_no_marketing_folder_uses_cover_and_says_what_is_missing(self):
        self.make_pack()
        self.say("/launch 3")
        self.assertIn("Levu: Pins, square posts / stories.", self.texts[0])
        self.assertEqual(Path(self.photos[0][0]).name, "cover.png")

    def test_text_only_day_sends_no_photo(self):
        self.make_pack(cover=False)
        self.say("/launch 3")
        self.assertEqual(self.photos, [])
        self.assertIn("Em post cheyali: Text post", self.texts[1])

    def test_without_send_functions_everything_comes_back_as_text(self):
        self.make_marketing(self.make_pack())
        reply = tg_launch.handle("/launch 3", {"products_dir": str(self.products), "marketing_dir": str(self.marketing),
                                               "now": NOW})
        self.assertIn("7 rojula launch plan", reply["text"])
        self.assertIn("Day 1/7: Launch day", reply["text"])

    def test_photo_failure_falls_back_to_document(self):
        self.make_marketing(self.make_pack())
        self.ctx["send_photo"] = lambda path, caption="": False
        self.say("/launch 3 day 1")
        self.assertEqual(Path(self.documents[0][0]).name, "pin_1.png")

    def test_private_marketing_folder_is_found(self):
        pack = self.make_pack()
        private = self.products / "marketing" / pack.name / "pins"
        private.mkdir(parents=True)
        (private / "pin_1.png").write_bytes(PNG)
        self.say("/launch 3 day 1")
        self.assertEqual(Path(self.photos[0][0]).parent, private.resolve())

    def test_link_and_start_come_from_listed_when_installed(self):
        self.make_marketing(self.make_pack())
        self.install_listed({3: {"id": 3, "url": LINK, "listed_ts": "2026-11-12T08:00:00Z"}})
        self.say("/launch 3")
        self.assertIn(f"Gumroad link: {LINK}", self.texts[0])
        self.assertIn("Launch start: 2026-11-12", self.texts[0])
        self.assertIn("Ee roju: Day 3/7.", self.texts[0])
        self.assertIn(f"Link: {LINK}", self.texts[1])
        self.assertNotIn("/listed 3 <link>", self.texts[0])

    def test_without_listed_the_owner_is_told_how_to_add_the_link(self):
        self.make_marketing(self.make_pack())
        self.say("/launch 3")
        self.assertIn("/listed 3 <link>", self.texts[0])

    def test_old_checklist_file_is_mentioned_when_it_exists(self):
        base = self.make_marketing(self.make_pack())
        (base / "LAUNCH.md").write_text("# Launch", encoding="utf-8")
        self.say("/launch 3")
        self.assertIn("launch checklist 3", self.texts[0])

    def test_list_shows_each_pack_and_its_state(self):
        self.make_marketing(self.make_pack())
        self.make_pack(pack_id=5, slug="budget-sheet", title="Budget Sheet")
        self.say("/launch 3")
        self.texts.clear()
        self.keyboards.clear()
        self.assertEqual(self.say("/launch"), {"handled": True})
        self.assertIn("#3 Focus Planner Printable Pack — Day 1/7 — ee roju post: /launch 3", self.texts[0])
        self.assertIn("#5 Budget Sheet — inka list cheyaledu (/sell 5); plan chudadaniki: /launch 5", self.texts[0])
        self.assertEqual(self.keyboards[0], [["/launch 3", "/launch 5"]])

    def test_list_marks_listed_and_finished_packs(self):
        self.make_pack()
        self.make_pack(pack_id=5, slug="budget-sheet", title="Budget Sheet")
        self.install_listed({5: {"id": 5, "url": LINK, "listed_ts": "2026-10-01T08:00:00Z"}})
        self.state["launch_start:3"] = "2099-01-01"
        self.say("/launch")
        self.assertIn("#5 Budget Sheet — launch week aipoyindi", self.texts[0])
        self.assertIn("#3 Focus Planner Printable Pack — inka list cheyaledu", self.texts[0])

    def test_list_without_packs(self):
        self.assertIn("Ready upload packs inka levu", self.say("/launch")["text"])

    def test_launch_status(self):
        self.make_pack()
        self.assertEqual(tg_launch.launch_status(3, self.ctx), ("not_started", None))
        self.state["launch_start:3"] = "2026-11-12"
        self.assertEqual(tg_launch.launch_status(3, self.ctx), ("running", 3))
        self.state["launch_start:3"] = "2026-11-01"
        self.assertEqual(tg_launch.launch_status(3, self.ctx), ("done", 14))

    def test_asset_outside_marketing_and_products_is_never_sent(self):
        outside = self.root / "private.png"
        outside.write_bytes(PNG)
        self.assertIsNone(tg_launch._safe_asset(outside, 3, self.ctx))
        self.assertIsNone(tg_launch._safe_asset(None, 3, self.ctx))
        self.assertIsNone(tg_launch._safe_asset(self.root / "missing.png", 3, self.ctx))

    def test_other_text_returns_none(self):
        for text in ("hello", "/sell 3", "launch checklist 3", None):
            self.assertIsNone(tg_launch.handle(text, self.ctx))

    def test_problem_gives_a_short_reply_not_a_crash(self):
        with mock.patch.object(tg_launch, "handle_plan", side_effect=RuntimeError("boom")):
            self.assertIn("/launch lo problem vachindi (RuntimeError)", self.say("/launch 3")["text"])

    def test_nothing_is_posted_only_owner_chat_callbacks_are_used(self):
        self.make_marketing(self.make_pack())
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            self.say("/launch 3")
            self.say("/launch 3 day 7")
            self.say("/launch")
        self.assertTrue(self.texts)

    def test_no_money_in_anything_sent(self):
        import store_kits
        self.make_marketing(self.make_pack())
        self.say("/launch 3")
        self.say("/launch")
        for text in self.texts + [caption for _, caption in self.photos]:
            self.assertIsNone(store_kits.check_money(text))


class ReachabilityTests(Base):
    """Through telegram_inbox.call_plugins and process_updates with fake send functions."""

    def setUp(self):
        super().setUp()
        import telegram_inbox
        self.inbox = telegram_inbox
        self.db = str(self.root / "robot.db")
        patches = (
            mock.patch.dict(os.environ, {"CHILLI_PRODUCTS_DIR": str(self.products),
                                         "CHILLI_MARKETING_DIR": str(self.marketing)}),
            mock.patch.object(telegram_inbox, "send_text",
                              side_effect=lambda token, chat, text, buttons=None, keyboard=None:
                              self.texts.append(text) or True),
            mock.patch.object(telegram_inbox, "_send_text_kw",
                              side_effect=lambda token, chat, text, keyboard=None: self.texts.append(text) or True),
            mock.patch.object(telegram_inbox, "send_photo",
                              side_effect=lambda token, chat, path, caption="": self.photos.append((path, caption)) or True),
            mock.patch.object(telegram_inbox, "send_document",
                              side_effect=lambda token, chat, path, caption="": self.documents.append((path, caption)) or True),
            mock.patch.object(telegram_inbox, "acknowledge_update", return_value=True),
            mock.patch.object(telegram_inbox, "_call", return_value={"ok": True}),
            mock.patch.object(telegram_inbox, "edit_text", return_value=True),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, text):
        return self.inbox.call_plugins(text, "test-token", SAMPLE_CHAT_ID, db_path=self.db, now=NOW)

    def test_plugin_is_discovered(self):
        self.assertIn("tg_launch", [plugin.__name__ for plugin in self.inbox.discover_plugins()])

    def test_launch_n_through_call_plugins_shows_the_plan_not_the_old_document(self):
        base = self.make_marketing(self.make_pack())
        (base / "LAUNCH.md").write_text("# Launch", encoding="utf-8")
        handled, reply = self.call("/launch 3")
        self.assertTrue(handled)
        self.assertEqual(reply, {"handled": True})
        self.assertIn("7 rojula launch plan", self.texts[0])
        self.assertEqual(Path(self.photos[0][0]).name, "pin_1.png")
        self.assertEqual(self.documents, [])

    def test_state_is_saved_in_the_test_database(self):
        self.make_marketing(self.make_pack())
        self.call("/launch 3")
        self.assertEqual(self.inbox.get_state("launch_start:3", db_path=self.db), "2026-11-14")

    def test_day_through_call_plugins(self):
        self.make_marketing(self.make_pack())
        handled, _ = self.call("/launch 3 day 3")
        self.assertTrue(handled)
        self.assertEqual(Path(self.photos[0][0]).name, "post_1_square.png")

    def test_tenglish_through_call_plugins(self):
        self.make_marketing(self.make_pack())
        handled, _ = self.call("pack 3 launch plan")
        self.assertTrue(handled)
        self.assertIn("7 rojula launch plan", self.texts[0])

    def test_list_through_call_plugins(self):
        self.make_pack()
        handled, _ = self.call("/launch")
        self.assertTrue(handled)
        self.assertIn("7 rojula launch plan — packs:", self.texts[0])

    def test_old_checklist_words_still_reach_tg_files(self):
        base = self.make_marketing(self.make_pack())
        (base / "LAUNCH.md").write_text("# Launch", encoding="utf-8")
        handled, _ = self.call("launch checklist 3")
        self.assertTrue(handled)
        self.assertEqual([Path(path).name for path, _ in self.documents], ["LAUNCH.md"])
        self.assertFalse(any("7 rojula launch plan" in text for text in self.texts))

    def test_priority_puts_tg_launch_before_tg_files(self):
        import tg_files
        self.assertGreater(tg_launch.PRIORITY, getattr(tg_files, "PRIORITY", 0))

    def test_through_process_updates_owner_only(self):
        self.make_marketing(self.make_pack())
        updates = [{"update_id": 1, "message": {"chat": {"id": SAMPLE_CHAT_ID}, "text": "/launch 3"}},
                   {"update_id": 2, "message": {"chat": {"id": int("9999" "0000")}, "text": "/launch 3 restart"}}]
        with mock.patch.object(self.inbox, "fetch_updates", return_value=updates):
            self.inbox.process_updates("test-token", SAMPLE_CHAT_ID, db_path=self.db)
        self.assertTrue(any("7 rojula launch plan" in text for text in self.texts))
        self.assertEqual(len(self.photos), 1)


if __name__ == "__main__":
    unittest.main()
