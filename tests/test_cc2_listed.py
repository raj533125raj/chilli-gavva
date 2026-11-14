"""CC2-3: /listed N <link> — remember what is already on Gumroad (tg_listed plugin).

Every test uses its own temp database. Nothing touches Telegram: the reachability tests replace
telegram_inbox's send function with a recorder. No link is ever opened.
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guardrails
import tg_listed

SAMPLE_CHAT_ID = int("1234" "5678")
LINK = "https://chilli.gumroad.com/l/focus-planner"
LINK2 = "https://gumroad.com/l/abc123"
NOW = datetime.datetime(2026, 11, 14, 9, 30, tzinfo=datetime.timezone.utc)


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.db = str(self.root / "robot.db")
        self.ctx = {"db_path": self.db, "chat_id": str(SAMPLE_CHAT_ID), "now": NOW}

    def add(self, title="Draft: Focus Planner (v1, llm)", payload="Title: Focus Planner\nA calm planner.",
            approve=True, kind="public_draft"):
        approval_id = guardrails.request_approval(kind, title, payload, db_path=self.db)
        if approve:
            guardrails.decide(approval_id, True, db_path=self.db)
        return approval_id

    def payload(self, approval_id):
        return guardrails.get_approval(approval_id, self.db)["payload"]

    def say(self, text):
        reply = tg_listed.handle(text, self.ctx)
        return None if reply is None else reply["text"]


class ValidateUrlTests(unittest.TestCase):
    GOOD = {
        "https://gumroad.com/l/abc123": "https://gumroad.com/l/abc123",
        "https://chilli.gumroad.com/l/focus-planner": "https://chilli.gumroad.com/l/focus-planner",
        "https://CHILLI.GUMROAD.COM/l/Focus_Planner": "https://chilli.gumroad.com/l/Focus_Planner",
        "https://app.gumroad.com/l/x": "https://app.gumroad.com/l/x",
        "https://chilli.gumroad.com/l/abc/": "https://chilli.gumroad.com/l/abc",
        "https://chilli.gumroad.com/l/abc?layout=profile": "https://chilli.gumroad.com/l/abc",
        "https://chilli.gumroad.com/l/abc#top": "https://chilli.gumroad.com/l/abc",
        "  https://chilli.gumroad.com/l/abc  ": "https://chilli.gumroad.com/l/abc",
        "<https://chilli.gumroad.com/l/abc>": "https://chilli.gumroad.com/l/abc",
        "HTTPS://gumroad.com/l/abc": "https://gumroad.com/l/abc",
    }
    BAD = [
        "http://chilli.gumroad.com/l/abc",            # not https
        "ftp://chilli.gumroad.com/l/abc",
        "chilli.gumroad.com/l/abc",                   # no scheme
        "https://gumroad.com.evil.example/l/abc",     # look-alike domain
        "https://evilgumroad.com/l/abc",
        "https://gumroad.co/l/abc",
        "https://example.com/l/abc",
        "https://example.com/?next=https://gumroad.com/l/abc",
        "https://user:pw" + "@" + "gumroad.com/l/abc",  # user info
        "https://gumroad.com@example.com/l/abc",
        "https://gumroad.com:8443/l/abc",             # port
        "https://gumroad.com/",                       # not a product page
        "https://gumroad.com/dashboard",
        "https://gumroad.com/l/",
        "https://gumroad.com/l/abc/extra",
        "https://gumroad.com/l/ab c",
        "https://gumroad.com/l/abc\\x",
        "https://a.b.gumroad.com/l/abc",              # only one shop label
        "https://-bad.gumroad.com/l/abc",
        "https://gumroad.com/l/" + "a" * 90,          # product name too long
        "https://gumroad.com/l/abc" + "?" + "q" * 300,  # link too long
        "javascript:alert(1)",
        "https://gumroad.com/l/ప్యాక్",                  # non-ASCII
        "",
        "   ",
    ]

    def test_good_links_are_cleaned(self):
        for raw, expected in self.GOOD.items():
            with self.subTest(raw=raw):
                self.assertEqual(tg_listed.validate_url(raw), (expected, ""))

    def test_bad_links_are_refused_with_a_reason(self):
        for raw in self.BAD:
            with self.subTest(raw=raw):
                clean, reason = tg_listed.validate_url(raw)
                self.assertIsNone(clean)
                self.assertTrue(reason.strip())

    def test_non_text_is_refused(self):
        for raw in (None, 5, ["https://gumroad.com/l/abc"], b"https://gumroad.com/l/abc"):
            with self.subTest(raw=raw):
                self.assertIsNone(tg_listed.validate_url(raw)[0])

    def test_http_reason_mentions_https(self):
        self.assertIn("https://", tg_listed.validate_url("http://gumroad.com/l/abc")[1])

    def test_other_domain_reason_mentions_gumroad(self):
        self.assertIn("Gumroad", tg_listed.validate_url("https://example.com/l/abc")[1])

    def test_not_a_product_page_reason_mentions_the_path(self):
        self.assertIn("/l/", tg_listed.validate_url("https://gumroad.com/dashboard")[1])

    def test_clean_link_has_no_query_or_fragment(self):
        clean, _ = tg_listed.validate_url("https://gumroad.com/l/abc?a=1&b=2#x")
        self.assertNotIn("?", clean)
        self.assertNotIn("#", clean)


class ParseTests(unittest.TestCase):
    def test_list_forms(self):
        for text in ("/listed", "listed", "Listed", "listed packs", "LISTED PACKS", "listed list",
                     "gumroad lo unnavi", "listed?", " /listed ", "/listed@chilli_bot"):
            with self.subTest(text=text):
                self.assertEqual(tg_listed.parse(text), ("list", None, ""))

    def test_save_forms(self):
        for text in (f"/listed 3 {LINK}", f"listed 3 {LINK}", f"/listed #3 {LINK}", f"/listed pack 3 {LINK}",
                     f"pack 3 listed {LINK}", f"/LISTED 3 {LINK}", f"/listed@chilli_bot 3 {LINK}",
                     f"/listed 3\n{LINK}", f"/listed   3    {LINK}"):
            with self.subTest(text=text):
                self.assertEqual(tg_listed.parse(text), ("save", 3, LINK))

    def test_show_forms(self):
        for text in ("/listed 3", "listed 3", "/listed #3", "pack 3 listed", "/listed pack 3"):
            with self.subTest(text=text):
                self.assertEqual(tg_listed.parse(text), ("show", 3, ""))

    def test_remove_forms(self):
        for word in ("remove", "delete", "clear", "undo", "teesey", "vaddu", "REMOVE", "remove."):
            with self.subTest(word=word):
                self.assertEqual(tg_listed.parse(f"/listed 3 {word}"), ("remove", 3, ""))

    def test_two_extra_words_ask_for_the_format(self):
        self.assertEqual(tg_listed.parse(f"/listed 3 {LINK} please")[0], "help")
        self.assertEqual(tg_listed.parse("/listed abc")[0], "help")
        self.assertEqual(tg_listed.parse(f"/listed {LINK}")[0], "help")

    def test_other_text_is_not_for_this_plugin(self):
        for text in ("", "   ", "hi", "/list", "/list 3", "/listing", "/listedx 3", "unlisted 3", "status",
                     "pack 3", "/sell 3", "/ready 3", "sale 3", "is it listed", "listed ga unda", None, 7,
                     "x" * 500):
            with self.subTest(text=text):
                self.assertIsNone(tg_listed.parse(text))

    def test_pack_number_is_never_a_huge_number(self):
        self.assertIsNone(tg_listed.parse("listed 12345678901 " + LINK))


class SaveTests(Base):
    def test_save_and_read_back(self):
        pack = self.add()
        result = tg_listed.set_listed(pack, LINK, db_path=self.db, now=NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["url"], LINK)
        self.assertIsNone(result["old_url"])
        self.assertEqual(result["title"], "Focus Planner")
        self.assertEqual(tg_listed.get_listed(pack, self.db),
                         {"id": pack, "url": LINK, "listed_ts": "2026-11-14T09:30:00Z", "title": "Focus Planner"})
        self.assertTrue(tg_listed.is_listed(pack, self.db))

    def test_row_is_really_in_the_database(self):
        pack = self.add()
        tg_listed.set_listed(pack, LINK, db_path=self.db, now=NOW)
        conn = sqlite3.connect(self.db)
        rows = conn.execute("SELECT approval_id, store_url, listed_ts FROM listed_packs").fetchall()
        conn.close()
        self.assertEqual(rows, [(pack, LINK, "2026-11-14T09:30:00Z")])

    def test_link_is_stored_cleaned(self):
        pack = self.add()
        tg_listed.set_listed(pack, LINK + "?layout=profile#x", db_path=self.db)
        self.assertEqual(tg_listed.get_listed(pack, self.db)["url"], LINK)

    def test_second_link_replaces_the_first(self):
        pack = self.add()
        tg_listed.set_listed(pack, LINK, db_path=self.db)
        result = tg_listed.set_listed(pack, LINK2, db_path=self.db)
        self.assertEqual(result["old_url"], LINK)
        self.assertEqual([item["url"] for item in tg_listed.all_listed(self.db)], [LINK2])

    def test_unknown_pack_is_refused(self):
        self.assertEqual(tg_listed.set_listed(99, LINK, db_path=self.db)["reason"], "not_found")
        self.assertEqual(tg_listed.all_listed(self.db), [])

    def test_pending_draft_is_refused(self):
        pack = self.add(approve=False)
        result = tg_listed.set_listed(pack, LINK, db_path=self.db)
        self.assertEqual((result["ok"], result["reason"], result["status"]), (False, "not_approved", "pending"))
        self.assertFalse(tg_listed.is_listed(pack, self.db))

    def test_rejected_draft_is_refused(self):
        pack = self.add(approve=False)
        guardrails.decide(pack, False, db_path=self.db)
        self.assertEqual(tg_listed.set_listed(pack, LINK, db_path=self.db)["reason"], "not_approved")

    def test_other_kind_of_approval_is_refused(self):
        pack = self.add(kind="code_patch")
        self.assertEqual(tg_listed.set_listed(pack, LINK, db_path=self.db)["reason"], "not_approved")

    def test_bad_link_is_refused_and_nothing_is_saved(self):
        pack = self.add()
        result = tg_listed.set_listed(pack, "http://chilli.gumroad.com/l/abc", db_path=self.db)
        self.assertFalse(result["ok"])
        self.assertEqual(tg_listed.all_listed(self.db), [])

    def test_plain_text_payload_is_never_rewritten(self):
        pack = self.add(payload="Title: Focus Planner\nA calm planner with {braces} inside.")
        before = self.payload(pack)
        result = tg_listed.set_listed(pack, LINK, db_path=self.db)
        self.assertFalse(result["payload_updated"])
        self.assertEqual(self.payload(pack), before)

    def test_broken_json_payload_is_never_rewritten(self):
        pack = self.add(payload="{not json at all")
        tg_listed.set_listed(pack, LINK, db_path=self.db)
        self.assertEqual(self.payload(pack), "{not json at all")

    def test_json_payload_gets_store_url_and_keeps_other_fields(self):
        pack = self.add(payload=json.dumps({"topic": "focus planner", "pages": 12}))
        result = tg_listed.set_listed(pack, LINK, db_path=self.db)
        self.assertTrue(result["payload_updated"])
        self.assertEqual(json.loads(self.payload(pack)), {"topic": "focus planner", "pages": 12, "store_url": LINK})

    def test_public_export_shows_json_payload_pack_as_listed(self):
        import public_export
        pack = self.add(payload=json.dumps({"topic": "focus planner"}))
        products = public_export.get_public_products(self.db, out_dir=str(self.root / "no-products"))
        self.assertEqual([(p["id"], p["listing_status"]) for p in products], [(pack, "draft")])
        tg_listed.set_listed(pack, LINK, db_path=self.db)
        products = public_export.get_public_products(self.db, out_dir=str(self.root / "no-products"))
        self.assertEqual([(p["id"], p["listing_status"], p["store_url"]) for p in products], [(pack, "listed", LINK)])

    def test_all_listed_is_sorted_by_pack_number(self):
        first, second, third = self.add(), self.add(title="Draft: Budget Sheet (v2, llm)"), self.add()
        tg_listed.set_listed(third, LINK, db_path=self.db)
        tg_listed.set_listed(first, LINK2, db_path=self.db)
        self.assertEqual(tg_listed.listed_ids(self.db), [first, third])
        self.assertFalse(tg_listed.is_listed(second, self.db))

    def test_naive_now_is_treated_as_utc(self):
        pack = self.add()
        tg_listed.set_listed(pack, LINK, db_path=self.db, now=datetime.datetime(2026, 1, 2, 3, 4, 5))
        self.assertEqual(tg_listed.get_listed(pack, self.db)["listed_ts"], "2026-01-02T03:04:05Z")

    def test_clean_title(self):
        self.assertEqual(tg_listed.clean_title("Draft: ADHD planner (v3, llm)"), "ADHD planner")
        self.assertEqual(tg_listed.clean_title("Budget Sheet"), "Budget Sheet")
        self.assertEqual(tg_listed.clean_title(None), "Pack")
        self.assertEqual(len(tg_listed.clean_title("x" * 300)), 80)


class RemoveTests(Base):
    def test_remove_returns_the_link(self):
        pack = self.add()
        tg_listed.set_listed(pack, LINK, db_path=self.db)
        self.assertEqual(tg_listed.remove_listed(pack, self.db), LINK)
        self.assertFalse(tg_listed.is_listed(pack, self.db))

    def test_remove_when_not_listed(self):
        self.assertIsNone(tg_listed.remove_listed(self.add(), self.db))
        self.assertIsNone(tg_listed.remove_listed(4242, self.db))

    def test_remove_takes_store_url_out_of_a_json_payload(self):
        pack = self.add(payload=json.dumps({"topic": "focus"}))
        tg_listed.set_listed(pack, LINK, db_path=self.db)
        tg_listed.remove_listed(pack, self.db)
        self.assertEqual(json.loads(self.payload(pack)), {"topic": "focus"})

    def test_remove_keeps_a_different_store_url_someone_else_wrote(self):
        pack = self.add(payload=json.dumps({"topic": "focus"}))
        tg_listed.set_listed(pack, LINK, db_path=self.db)
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE approvals SET payload = ? WHERE id = ?",
                     (json.dumps({"topic": "focus", "store_url": LINK2}), pack))
        conn.commit()
        conn.close()
        tg_listed.remove_listed(pack, self.db)
        self.assertEqual(json.loads(self.payload(pack))["store_url"], LINK2)

    def test_remove_leaves_plain_payload_alone(self):
        pack = self.add()
        before = self.payload(pack)
        tg_listed.set_listed(pack, LINK, db_path=self.db)
        tg_listed.remove_listed(pack, self.db)
        self.assertEqual(self.payload(pack), before)


class ReplyTests(Base):
    def test_save_reply(self):
        pack = self.add()
        text = self.say(f"/listed {pack} {LINK}")
        self.assertIn(f"✅ Pack #{pack} (Focus Planner) Gumroad lo listed", text)
        self.assertIn(LINK, text)
        self.assertIn("/status", text)
        self.assertTrue(tg_listed.is_listed(pack, self.db))

    def test_save_uses_ctx_now(self):
        pack = self.add()
        self.say(f"/listed {pack} {LINK}")
        self.assertEqual(tg_listed.get_listed(pack, self.db)["listed_ts"], "2026-11-14T09:30:00Z")

    def test_save_reply_mentions_the_replaced_link(self):
        pack = self.add()
        self.say(f"/listed {pack} {LINK}")
        text = self.say(f"/listed {pack} {LINK2}")
        self.assertIn(f"Paatha link teesesanu: {LINK}", text)

    def test_same_link_again_is_not_called_a_replacement(self):
        pack = self.add()
        self.say(f"/listed {pack} {LINK}")
        text = self.say(f"/listed {pack} {LINK}")
        self.assertNotIn("Paatha link", text)
        self.assertIn("mundu kuda undi", text)

    def test_tenglish_form_saves(self):
        pack = self.add()
        self.assertIn("listed ani gurthu", self.say(f"pack {pack} listed {LINK}"))

    def test_bad_link_reply_explains_and_shows_the_format(self):
        pack = self.add()
        text = self.say(f"/listed {pack} http://chilli.gumroad.com/l/abc")
        self.assertIn("Link save cheyaledu", text)
        self.assertIn("https://", text)
        self.assertIn(tg_listed.EXAMPLE, text)
        self.assertFalse(tg_listed.is_listed(pack, self.db))

    def test_other_shop_link_is_refused(self):
        pack = self.add()
        self.assertIn("Gumroad link kaadu", self.say(f"/listed {pack} https://etsy.example/l/abc"))

    def test_unknown_pack_reply(self):
        self.assertIn("Pack #77 dorakaledu", self.say(f"/listed 77 {LINK}"))

    def test_pending_pack_reply(self):
        pack = self.add(approve=False)
        text = self.say(f"/listed {pack} {LINK}")
        self.assertIn("approved Draft kaadu", text)
        self.assertIn("pending", text)

    def test_empty_list_reply_teaches_the_command(self):
        text = self.say("/listed")
        self.assertIn("Inka e pack", text)
        self.assertIn(tg_listed.EXAMPLE, text)

    def test_list_reply_shows_every_pack_with_link_and_date(self):
        first, second = self.add(), self.add(title="Draft: Budget Sheet (v2, llm)")
        self.say(f"/listed {first} {LINK}")
        self.say(f"/listed {second} {LINK2}")
        text = self.say("listed packs")
        self.assertIn("Gumroad lo listed packs (2):", text)
        self.assertIn(f"#{first} — Focus Planner", text)
        self.assertIn(f"#{second} — Budget Sheet", text)
        self.assertIn(LINK, text)
        self.assertIn(LINK2, text)
        self.assertIn("(listed 2026-11-14)", text)
        self.assertLess(text.index(f"#{first} —"), text.index(f"#{second} —"))

    def test_show_reply(self):
        pack = self.add()
        self.say(f"/listed {pack} {LINK}")
        text = self.say(f"/listed {pack}")
        self.assertIn(f"Pack #{pack} — Focus Planner", text)
        self.assertIn(f"Gumroad link: {LINK}", text)

    def test_show_reply_when_not_listed(self):
        pack = self.add()
        self.assertIn(f"/listed {pack} <Gumroad link>", self.say(f"/listed {pack}"))

    def test_remove_reply(self):
        pack = self.add()
        self.say(f"/listed {pack} {LINK}")
        text = self.say(f"/listed {pack} remove")
        self.assertIn("listed link teesesanu", text)
        self.assertIn(LINK, text)
        self.assertIn("Inka e pack", self.say("/listed"))

    def test_remove_reply_when_not_listed(self):
        self.assertIn("listed list lo ledu", self.say(f"/listed {self.add()} remove"))

    def test_format_help_reply(self):
        text = self.say(f"/listed {LINK}")
        self.assertIn(tg_listed.EXAMPLE, text)

    def test_other_text_returns_none(self):
        for text in ("hi", "/list", "/status", "pack 3", None):
            with self.subTest(text=text):
                self.assertIsNone(tg_listed.handle(text, self.ctx))

    def test_ctx_may_be_missing(self):
        self.assertIsNone(tg_listed.handle("hello", None))

    def test_database_problem_gives_a_short_reply_not_a_crash(self):
        with mock.patch.object(tg_listed, "list_text", side_effect=sqlite3.OperationalError("locked")):
            text = self.say("/listed")
        self.assertIn("/listed lo problem vachindi (OperationalError)", text)

    def test_replies_have_no_money_amounts(self):
        import store_kits
        pack = self.add()
        texts = [self.say(f"/listed {pack} {LINK}"), self.say("/listed"), self.say(f"/listed {pack}"),
                 self.say(f"/listed {pack} remove"), self.say("/listed"), self.say("/listed 3 a b")]
        for text in texts:
            self.assertIsNone(store_kits.check_money(text))

    def test_thirty_one_packs_list_is_cut(self):
        for index in range(31):
            pack = self.add(title=f"Draft: Pack {index} (v1, llm)")
            tg_listed.set_listed(pack, f"https://chilli.gumroad.com/l/p{index}", db_path=self.db, now=NOW)
        text = self.say("/listed")
        self.assertIn("(31)", text)
        self.assertIn("inko 1 packs", text)
        self.assertLess(len(text), 3900)


class StatusAndReadyLineTests(Base):
    def test_lines_are_empty_when_nothing_is_listed(self):
        pack = self.add()
        self.assertEqual(tg_listed.status_line(self.db), "")
        self.assertEqual(tg_listed.ready_note(pack, self.db), "")
        self.assertEqual(tg_listed.ready_summary_note(self.db), "")

    def test_status_line(self):
        first, second = self.add(), self.add()
        tg_listed.set_listed(first, LINK, db_path=self.db)
        tg_listed.set_listed(second, LINK2, db_path=self.db)
        self.assertEqual(tg_listed.status_line(self.db),
                         f"Gumroad lo listed: 2 packs (#{first}, #{second}) — /listed")

    def test_status_line_is_short_with_many_packs(self):
        for index in range(13):
            tg_listed.set_listed(self.add(), f"https://chilli.gumroad.com/l/p{index}", db_path=self.db)
        line = tg_listed.status_line(self.db)
        self.assertIn("13 packs", line)
        self.assertIn("+3", line)
        self.assertNotIn("\n", line)

    def test_ready_note(self):
        pack = self.add()
        tg_listed.set_listed(pack, LINK, db_path=self.db)
        self.assertEqual(tg_listed.ready_note(pack, self.db),
                         f"✅ LISTED — Pack #{pack} already Gumroad lo undi: {LINK}")
        self.assertEqual(tg_listed.ready_note(pack + 1, self.db), "")

    def test_ready_summary_note(self):
        pack = self.add()
        tg_listed.set_listed(pack, LINK, db_path=self.db)
        self.assertEqual(tg_listed.ready_summary_note(self.db), f"✅ Already Gumroad lo listed: #{pack} (/listed)")


class ReachabilityTests(Base):
    """/listed through the real inbox code (call_plugins, /status, /ready) with fake send functions."""

    def setUp(self):
        super().setUp()
        import telegram_inbox
        self.inbox = telegram_inbox
        self.texts = []
        self.products = self.root / "exported_content" / "products"
        self.products.mkdir(parents=True)
        # /ready looks for packs below the relative folder exported_content/products
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.root)
        patches = (
            mock.patch.dict(os.environ, {"CHILLI_PRODUCTS_DIR": str(self.products),
                                         "CHILLI_MARKETING_DIR": str(self.root / "marketing")}),
            mock.patch.object(telegram_inbox, "send_text",
                              side_effect=lambda token, chat, text, buttons=None, keyboard=None:
                              self.texts.append(text) or True),
            mock.patch.object(telegram_inbox, "_send_text_kw",
                              side_effect=lambda token, chat, text, keyboard=None: self.texts.append(text) or True),
            mock.patch.object(telegram_inbox, "acknowledge_update", return_value=True),
            mock.patch.object(telegram_inbox, "_call", return_value={"ok": True}),
            mock.patch.object(telegram_inbox, "edit_text", return_value=True),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, text):
        return self.inbox.call_plugins(text, "test-token", SAMPLE_CHAT_ID, db_path=self.db, now=NOW)

    def make_pack(self, pack_id):
        import zipfile
        folder = self.products / "upload" / f"{pack_id}-focus-planner"
        folder.mkdir(parents=True)
        (folder / "listing.txt").write_text(
            "Title: Focus Planner Printable Pack\n\nDescription:\nA calm daily planner page set.\n\n"
            "Tags: focus planner, daily planner, printable page\n", encoding="utf-8")
        with zipfile.ZipFile(folder / "product.zip", "w") as archive:
            archive.writestr("planner.pdf", b"%PDF-1.4 sample")
        return folder

    def test_plugin_is_discovered(self):
        self.assertIn("tg_listed", [plugin.__name__ for plugin in self.inbox.discover_plugins()])

    def test_listed_command_is_declared_for_the_menu(self):
        self.assertIn("/listed", self.inbox.plugin_words())

    def test_save_through_call_plugins(self):
        pack = self.add()
        handled, reply = self.call(f"/listed {pack} {LINK}")
        self.assertTrue(handled)
        self.assertIn("listed ani gurthu pettukunna", reply["text"])
        self.assertEqual(tg_listed.get_listed(pack, self.db)["listed_ts"], "2026-11-14T09:30:00Z")

    def test_list_through_call_plugins(self):
        pack = self.add()
        self.call(f"/listed {pack} {LINK}")
        handled, reply = self.call("/listed")
        self.assertTrue(handled)
        self.assertIn(f"#{pack} — Focus Planner", reply["text"])

    def test_plain_word_through_call_plugins(self):
        handled, reply = self.call("listed packs")
        self.assertTrue(handled)
        self.assertIn("Inka e pack", reply["text"])

    def test_bad_link_through_call_plugins_saves_nothing(self):
        pack = self.add()
        handled, reply = self.call(f"/listed {pack} https://example.com/l/abc")
        self.assertTrue(handled)
        self.assertIn("Link save cheyaledu", reply["text"])
        self.assertEqual(tg_listed.all_listed(self.db), [])

    def test_through_process_updates_owner_only(self):
        pack = self.add()
        updates = [{"update_id": 1, "message": {"chat": {"id": SAMPLE_CHAT_ID}, "text": f"/listed {pack} {LINK}"}},
                   {"update_id": 2, "message": {"chat": {"id": int("9999" "0000")}, "text": f"/listed {pack} {LINK2}"}}]
        import media_brain
        with mock.patch.object(self.inbox, "fetch_updates", return_value=updates), \
                mock.patch.object(media_brain, "process_url") as fetch_link:
            self.inbox.process_updates("test-token", SAMPLE_CHAT_ID, db_path=self.db)
        fetch_link.assert_not_called()                                           # the link is never opened
        self.assertEqual(tg_listed.get_listed(pack, self.db)["url"], LINK)     # the stranger's link is ignored
        self.assertTrue(any("listed ani gurthu pettukunna" in text for text in self.texts))

    def test_tenglish_sentence_with_link_through_process_updates(self):
        import media_brain
        pack = self.add()
        updates = [{"update_id": 5, "message": {"chat": {"id": SAMPLE_CHAT_ID}, "text": f"pack {pack} listed {LINK}"}}]
        with mock.patch.object(self.inbox, "fetch_updates", return_value=updates), \
                mock.patch.object(media_brain, "process_url") as fetch_link:
            self.inbox.process_updates("test-token", SAMPLE_CHAT_ID, db_path=self.db)
        fetch_link.assert_not_called()
        self.assertTrue(tg_listed.is_listed(pack, self.db))

    def test_a_bare_shared_link_still_goes_to_the_media_brain(self):
        import media_brain
        updates = [{"update_id": 6, "message": {"chat": {"id": SAMPLE_CHAT_ID}, "text": "https://example.com/article"}}]
        with mock.patch.object(self.inbox, "fetch_updates", return_value=updates), \
                mock.patch.object(media_brain, "process_url", return_value=("saved", None, None)) as fetch_link:
            self.inbox.process_updates("test-token", SAMPLE_CHAT_ID, db_path=self.db)
        fetch_link.assert_called_once()
        self.assertEqual(tg_listed.all_listed(self.db), [])

    def test_link_belongs_to_command(self):
        self.assertTrue(self.inbox._link_belongs_to_command(f"/listed 3 {LINK}"))
        self.assertTrue(self.inbox._link_belongs_to_command(f"pack 3 listed {LINK}"))
        self.assertFalse(self.inbox._link_belongs_to_command(LINK))
        self.assertFalse(self.inbox._link_belongs_to_command(f"chudu {LINK}"))

    def test_status_shows_listed_packs(self):
        pack = self.add()
        self.assertNotIn("Gumroad lo listed", self.inbox.status_text(self.db))
        self.call(f"/listed {pack} {LINK}")
        self.assertIn(f"Gumroad lo listed: 1 packs (#{pack}) — /listed", self.inbox.status_text(self.db))
        self.assertIn("Gumroad lo listed: 1 packs", self.inbox.handle_command("status", [], self.db))

    def test_status_drops_the_line_after_remove(self):
        pack = self.add()
        self.call(f"/listed {pack} {LINK}")
        self.call(f"/listed {pack} remove")
        self.assertNotIn("Gumroad lo listed", self.inbox.status_text(self.db))

    def test_ready_card_shows_listed(self):
        pack = self.add()
        self.make_pack(pack)
        self.call(f"/ready {pack}")
        self.assertNotIn("LISTED", self.texts[-1])
        self.call(f"/listed {pack} {LINK}")
        handled, _reply = self.call(f"/ready {pack}")
        self.assertTrue(handled)
        self.assertIn(f"✅ LISTED — Pack #{pack} already Gumroad lo undi: {LINK}", self.texts[-1])

    def test_ready_list_shows_listed(self):
        pack = self.add()
        self.make_pack(pack)
        _handled, reply = self.call("/ready")
        self.assertNotIn("listed", reply["text"].lower())
        self.call(f"/listed {pack} {LINK}")
        _handled, reply = self.call("/ready")
        self.assertIn(f"✅ Already Gumroad lo listed: #{pack} (/listed)", reply["text"])


if __name__ == "__main__":
    unittest.main()
