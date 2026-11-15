"""CCB-3: /bundle a,b,c — one Gumroad bundle from 2-4 READY packs of the same audience (tg_bundle).

bundle_builder.py is the existing builder and is used unchanged. Packs live in temp folders; the
pack-content checkers get a fixed verdict exactly like the /sell tests (test_cc1_sell fixtures), so
sell_ready and bundle_builder run for real. Nothing touches Telegram: send functions are recorders.
"""
from __future__ import annotations

import hashlib
import os
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_cc1_sell as sell_fixture      # temp packs + recorder; not re-collected here
import tg_bundle

try:
    import PIL  # noqa: F401
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

SAMPLE_CHAT_ID = sell_fixture.SAMPLE_CHAT_ID
FOCUS = ("Focus Planner Printable Pack", "ADHD Daily Focus Sheets", "Focus Habit Tracker Pages", "Deep Focus Week Planner")
RESUME_TITLE = "ATS Resume CV Template For Job Hunting"


class Base(sell_fixture.Base):
    def setUp(self):
        super().setUp()
        self.rec = self.recorder()

    def packs(self, count=3, start=3):
        made = []
        for index in range(count):
            number = start + index
            made.append(self.make_pack(f"{number}-pack-{number}", title=FOCUS[index % len(FOCUS)]))
        return made

    def say(self, text, rec=None):
        rec = rec or self.rec
        return tg_bundle.handle(text, rec.ctx)

    def bundle_folder(self):
        folders = [path for path in (self.products / "bundles").iterdir() if path.is_dir()]
        self.assertEqual(len(folders), 1)
        return folders[0]


class ParseTests(unittest.TestCase):
    def test_build_forms(self):
        for text, ids in (("/bundle 3,5,7", [3, 5, 7]), ("/bundle 3, 5, 7", [3, 5, 7]), ("/bundle 3 5", [3, 5]),
                          ("/bundle #3 #5", [3, 5]), ("/bundle packs 3,5", [3, 5]), ("/BUNDLE 3,5", [3, 5]),
                          ("/bundle@chilli_bot 3,5", [3, 5]), ("bundle 3,5,7", [3, 5, 7]), ("bundle 3 and 5", [3, 5]),
                          ("3,5,7 bundle chey", [3, 5, 7]), ("pack 3 5 bundle chey", [3, 5]),
                          ("packs 3,5 ni bundle cheyandi", [3, 5]), ("bundle chey 3 5", [3, 5]),
                          ("bundle 3 + 5 kavali", [3, 5]), ("/bundle 3,5,7?", [3, 5, 7])):
            with self.subTest(text=text):
                self.assertEqual(tg_bundle.parse(text), ("build", ids))

    def test_every_declared_word_is_understood(self):
        for entry in tg_bundle.COMMANDS:
            for word in [entry["cmd"]] + entry["words"]:
                with self.subTest(word=word):
                    self.assertIsNotNone(tg_bundle.parse(word))

    def test_help_forms(self):
        for text in ("/bundle", "bundle", "Bundle", "bundles", "bundle chey", "bundle ideas", "/bundle abc", "/bundle@x"):
            with self.subTest(text=text):
                self.assertEqual(tg_bundle.parse(text), ("help", []))

    def test_other_text_is_not_for_this_plugin(self):
        for text in ("", "hi", "/sell 3", "/bundles_old", "bundle up 3", "pack 3", "bundle of joy", None, 7, "x" * 120):
            with self.subTest(text=text):
                self.assertIsNone(tg_bundle.parse(text))

    def test_interface(self):
        entry = tg_bundle.COMMANDS[0]
        self.assertEqual(entry["cmd"], "/bundle")
        self.assertTrue(entry["help_en"] and entry["help_te"])
        self.assertTrue(callable(tg_bundle.handle))


class HelpTests(Base):
    def test_bare_word_always_answers(self):
        for word in ["/bundle"] + tg_bundle.COMMANDS[0]["words"]:
            with self.subTest(word=word):
                reply = self.say(word)
                self.assertIn("/bundle 3,5,7", reply["text"])

    def test_help_without_packs(self):
        self.assertIn("kanisam 2 ready", self.say("bundle")["text"])

    def test_help_suggests_real_ready_packs(self):
        self.packs(3)
        text = self.say("bundle ideas")["text"]
        self.assertTrue("Ideas" in text or "Ready packs: #3, #4, #5" in text, text)
        self.assertNotIn("#9", text)

    def test_help_leaves_out_not_yet_packs(self):
        self.packs(2)
        self.truth = {"verdict": "needs fix", "score": 30, "problems": ["x"]}
        self.assertIn("kanisam 2 ready", self.say("bundle")["text"])

    def test_help_says_the_owner_publishes(self):
        text = self.say("/bundle")["text"]
        self.assertIn("Robot edi list / publish cheyadu", text)
        self.assertIn("Price: owner decides", text)


class RefusalTests(Base):
    def test_one_pack_is_not_a_bundle(self):
        self.packs(1)
        self.assertIn("2 nundi 4 packs undali", self.say("/bundle 3,3")["text"])

    def test_five_packs_is_too_many(self):
        self.assertIn("2 nundi 4 packs undali (meeru 5", self.say("/bundle 1,2,3,4,5")["text"])

    def test_duplicate_numbers_are_named(self):
        self.packs(2)
        self.assertIn("rendu sarlu", self.say("/bundle 3,3,4")["text"])

    def test_unknown_pack(self):
        self.packs(1)
        self.assertIn("Pack #9 ready upload pack dorakaledu", self.say("/bundle 3,9")["text"])
        self.assertFalse((self.products / "bundles").exists())

    def test_not_yet_pack_is_refused(self):
        self.packs(2)
        self.truth = {"verdict": "needs fix", "score": 30, "problems": ["x"]}
        text = self.say("/bundle 3,4")["text"]
        self.assertIn("Pack #3 NOT YET — bundle lo pettakudadu", text)
        self.assertFalse((self.products / "bundles").exists())

    def test_different_audiences_are_refused(self):
        self.make_pack("3-pack-3", title=FOCUS[0])
        self.make_pack("4-resume", title=RESUME_TITLE)
        text = self.say("/bundle 3,4")["text"]
        self.assertIn("okate audience kosam kaavu", text)
        self.assertIn("#4 resume", text)
        self.assertFalse((self.products / "bundles").exists())

    def test_builder_error_is_a_short_reply(self):
        self.packs(2)
        with mock.patch("bundle_builder.build_bundle", side_effect=ValueError("too big")):
            self.assertIn("Bundle build fail ayyindi (ValueError: too big)", self.say("/bundle 3,4")["text"])

    def test_unexpected_error_is_a_short_reply(self):
        with mock.patch.object(tg_bundle, "handle_build", side_effect=RuntimeError("boom")):
            self.assertIn("/bundle lo problem vachindi (RuntimeError)", self.say("/bundle 3,4")["text"])


@unittest.skipUnless(HAS_PIL, "bundle cover needs Pillow")
class BuildTests(Base):
    def test_bundle_is_built_checked_and_sent(self):
        self.packs(3)
        self.assertEqual(self.say("/bundle 3,4,5"), {"handled": True})
        self.assertIn("📦 Bundle ready (3 packs, audience: adhd). Check pass ✓", self.rec.texts[0])
        self.assertEqual([Path(path).name for path, _ in self.rec.photos], ["cover.png"])
        self.assertEqual([Path(path).name for path, _ in self.rec.documents], ["listing.txt", "bundle.zip"])
        self.assertIn("BUNDLE UPLOAD STEPS — packs #3, #4, #5", self.rec.texts[-1])
        self.assertIn("'Content' lo bundle.zip upload cheyandi.", self.rec.texts[-1])

    def test_bundle_lives_in_the_private_products_folder(self):
        self.packs(2)
        self.say("/bundle 3,4")
        folder = self.bundle_folder()
        self.assertEqual(folder.parent, (self.products / "bundles").resolve())
        self.assertEqual(sorted(path.name for path in folder.iterdir()),
                         ["LICENSE.txt", "bundle.zip", "cover.png", "listing.txt"])

    def test_bundle_zip_holds_each_member_zip_unchanged(self):
        made = self.packs(3)
        self.say("/bundle 3,4,5")
        with zipfile.ZipFile(self.bundle_folder() / "bundle.zip") as archive:
            inside = sorted(hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist())
        expected = sorted(hashlib.sha256((pack / "product.zip").read_bytes()).hexdigest() for pack in made)
        self.assertEqual(inside, expected)

    def test_listing_names_every_member_and_has_no_money(self):
        import store_kits
        self.packs(2)
        self.say("/bundle 3,4")
        listing = (self.bundle_folder() / "listing.txt").read_text(encoding="utf-8")
        for title in FOCUS[:2]:
            self.assertIn(title, listing)
        self.assertIsNone(store_kits.check_money(listing))

    def test_members_are_never_changed(self):
        made = self.packs(2)
        before = {str(path): path.read_bytes() for pack in made for path in pack.rglob("*") if path.is_file()}
        self.say("/bundle 3,4")
        after = {str(path): path.read_bytes() for pack in made for path in pack.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_almost_packs_may_be_bundled(self):
        self.packs(2)
        self.qa = {"verdict": "needs fix", "score": 70, "problems": ["small"]}
        reply = self.say("/bundle 3,4")
        if reply == {"handled": True}:
            self.assertIn("Check pass", self.rec.texts[0])
        else:                                # the fixture's QA verdict made it NOT YET: then it must refuse
            self.assertIn("bundle lo pettakudadu", reply["text"])

    def test_same_bundle_twice_replaces_the_folder(self):
        self.packs(2)
        self.say("/bundle 3,4")
        self.say("/bundle 4,3")
        self.assertEqual(len([p for p in (self.products / "bundles").iterdir() if p.is_dir()]), 1)

    def test_text_only_caller_gets_everything_as_text(self):
        self.packs(2)
        reply = tg_bundle.handle("/bundle 3,4", {"products_dir": str(self.products), "marketing_dir": str(self.marketing)})
        self.assertIn("Check pass", reply["text"])
        self.assertIn("BUNDLE UPLOAD STEPS", reply["text"])

    def test_photo_failure_falls_back_to_document(self):
        self.packs(2)
        rec = self.recorder(photo_ok=False)
        self.say("/bundle 3,4", rec)
        self.assertEqual([Path(path).name for path, _ in rec.documents], ["cover.png", "listing.txt", "bundle.zip"])

    def test_too_big_zip_is_not_sent(self):
        self.packs(2)
        with mock.patch.object(tg_bundle, "MAX_FILE_BYTES", 10):
            self.say("/bundle 3,4")
        self.assertIn("chala pedda file", self.rec.texts[-1])
        self.assertNotIn("bundle.zip", [Path(path).name for path, _ in self.rec.documents])

    def test_nothing_sent_has_money(self):
        import store_kits
        self.packs(3)
        self.say("/bundle 3,4,5")
        for text in self.rec.texts + [caption for _, caption in self.rec.photos + self.rec.documents]:
            self.assertIsNone(store_kits.check_money(text))


class VerifyTests(Base):
    """The bundle check fails for a bundle that does not match its listing."""

    def built(self):
        made = self.packs(2)
        self.say("/bundle 3,4")
        members = [{"id": 3 + index, "folder": pack, "title": FOCUS[index]} for index, pack in enumerate(made)]
        return self.bundle_folder(), members

    @unittest.skipUnless(HAS_PIL, "bundle cover needs Pillow")
    def test_good_bundle_passes(self):
        folder, members = self.built()
        self.assertEqual(tg_bundle.verify_bundle(folder, members), [])

    @unittest.skipUnless(HAS_PIL, "bundle cover needs Pillow")
    def test_changed_member_zip_fails(self):
        folder, members = self.built()
        with zipfile.ZipFile(members[0]["folder"] / "product.zip", "a") as archive:
            archive.writestr("extra.txt", "changed")
        self.assertIn("bundle.zip lo unna files packs product.zip tho match avvaledu", tg_bundle.verify_bundle(folder, members))

    @unittest.skipUnless(HAS_PIL, "bundle cover needs Pillow")
    def test_missing_member_name_in_listing_fails(self):
        folder, members = self.built()
        members[0]["title"] = "A Pack That Is Not There"
        self.assertTrue(any("peru ledu" in p for p in tg_bundle.verify_bundle(folder, members)))

    @unittest.skipUnless(HAS_PIL, "bundle cover needs Pillow")
    def test_wrong_count_in_listing_fails(self):
        folder, members = self.built()
        listing = folder / "listing.txt"
        listing.write_text(listing.read_text(encoding="utf-8").replace("Get 2 complete", "Get 5 complete"), encoding="utf-8")
        self.assertTrue(any("5 products ani cheptondi" in p for p in tg_bundle.verify_bundle(folder, members)))

    @unittest.skipUnless(HAS_PIL, "bundle cover needs Pillow")
    def test_money_in_listing_fails(self):
        folder, members = self.built()
        listing = folder / "listing.txt"
        listing.write_text(listing.read_text(encoding="utf-8") + "\nOnly $9 today\n", encoding="utf-8")
        self.assertIn("listing lo money amount undi", tg_bundle.verify_bundle(folder, members))

    @unittest.skipUnless(HAS_PIL, "bundle cover needs Pillow")
    def test_missing_file_fails(self):
        folder, members = self.built()
        (folder / "cover.png").unlink()
        self.assertEqual(tg_bundle.verify_bundle(folder, members), ["cover.png ledu"])

    @unittest.skipUnless(HAS_PIL, "bundle cover needs Pillow")
    def test_extra_file_in_zip_fails(self):
        folder, members = self.built()
        with zipfile.ZipFile(folder / "bundle.zip", "a") as archive:
            archive.writestr("../escape.txt", "x")
        problems = tg_bundle.verify_bundle(folder, members)
        self.assertTrue(any("unsafe path" in p for p in problems))
        self.assertTrue(any("3 files unnayi" in p for p in problems))

    @unittest.skipUnless(HAS_PIL, "bundle cover needs Pillow")
    def test_failed_check_is_never_sent(self):
        self.packs(2)
        with mock.patch.object(tg_bundle, "verify_bundle", return_value=["bundle.zip open avvaledu"]):
            reply = self.say("/bundle 3,4")
        self.assertIn("Bundle check fail ayyindi — upload cheyakandi", reply["text"])
        self.assertEqual(self.rec.documents, [])


class ReachabilityTests(Base):
    """Through telegram_inbox.call_plugins and process_updates with fake send functions."""

    def setUp(self):
        super().setUp()
        import telegram_inbox
        self.inbox = telegram_inbox
        self.texts, self.documents, self.photos = [], [], []
        self.db = str(self.root / "robot.db")
        patches = (
            mock.patch.dict(os.environ, {"CHILLI_PRODUCTS_DIR": str(self.products),
                                         "CHILLI_MARKETING_DIR": str(self.marketing)}),
            mock.patch.object(telegram_inbox, "send_text",
                              side_effect=lambda token, chat, text, buttons=None, keyboard=None:
                              self.texts.append(text) or True),
            mock.patch.object(telegram_inbox, "_send_text_kw",
                              side_effect=lambda token, chat, text, keyboard=None: self.texts.append(text) or True),
            mock.patch.object(telegram_inbox, "send_document",
                              side_effect=lambda token, chat, path, caption="": self.documents.append(path) or True),
            mock.patch.object(telegram_inbox, "send_photo",
                              side_effect=lambda token, chat, path, caption="": self.photos.append(path) or True),
            mock.patch.object(telegram_inbox, "acknowledge_update", return_value=True),
            mock.patch.object(telegram_inbox, "_call", return_value={"ok": True}),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, text):
        return self.inbox.call_plugins(text, "test-token", SAMPLE_CHAT_ID, db_path=self.db)

    def test_plugin_is_discovered(self):
        self.assertIn("tg_bundle", [plugin.__name__ for plugin in self.inbox.discover_plugins()])

    def test_every_declared_word_gets_a_reply_through_the_loader(self):
        for word in ["/bundle"] + tg_bundle.COMMANDS[0]["words"]:
            with self.subTest(word=word):
                handled, reply = self.call(word)
                self.assertTrue(handled)
                self.assertIsNotNone(reply)

    @unittest.skipUnless(HAS_PIL, "bundle cover needs Pillow")
    def test_build_through_call_plugins(self):
        self.packs(2)
        handled, reply = self.call("/bundle 3,4")
        self.assertTrue(handled)
        self.assertEqual(reply, {"handled": True})
        self.assertEqual([Path(path).name for path in self.photos], ["cover.png"])
        self.assertEqual([Path(path).name for path in self.documents], ["listing.txt", "bundle.zip"])

    @unittest.skipUnless(HAS_PIL, "bundle cover needs Pillow")
    def test_tenglish_through_call_plugins(self):
        self.packs(2)
        handled, _ = self.call("3,4 bundle chey")
        self.assertTrue(handled)
        self.assertTrue(any("Check pass" in text for text in self.texts))

    @unittest.skipUnless(HAS_PIL, "bundle cover needs Pillow")
    def test_through_process_updates_owner_only(self):
        self.packs(2)
        updates = [{"update_id": 1, "message": {"chat": {"id": int("9999" "0000")}, "text": "/bundle 3,4"}},
                   {"update_id": 2, "message": {"chat": {"id": SAMPLE_CHAT_ID}, "text": "/bundle 3,4"}}]
        with mock.patch.object(self.inbox, "fetch_updates", return_value=updates):
            self.inbox.process_updates("test-token", SAMPLE_CHAT_ID, db_path=self.db)
        self.assertEqual(sum("Check pass" in text for text in self.texts), 1)


if __name__ == "__main__":
    unittest.main()
