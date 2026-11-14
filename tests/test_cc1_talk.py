"""CC1-3: talk to the robot in plain Tenglish (talk_intents + tg_talk).

Three layers are tested:
  1. talk_intents.detect on 250+ phrases (numbers in digits, Telugu words, English words).
  2. tg_talk.handle: the reply must be the REAL output of the real command (compared with a direct
     call of that command), low confidence -> 2 suggestions keyboard, slash/greetings untouched.
  3. End to end through telegram_inbox.process_updates with fake updates and fake send functions.

Everything runs in a temp folder (the working directory is moved there, because several old
commands write under a relative exported_content/ path). No Telegram, no network, no AI.
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daily_report
import guardrails
import talk_intents as ti
import telegram_inbox
import tg_files
import tg_sell
import tg_talk

OWNER = int("1234" "5678")
STRANGER = int("8765" "4321")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

# ---- phrase table: (what the owner types, the command that must be understood) ----------------
HAND_WRITTEN = [
    # the five examples from the job sheet
    ("ee roju em jarigindi", "daily"), ("pending drafts entha", "/list"), ("pack 3 files pampu", "/files 3"),
    ("moodo pack sell chey", "/sell 3"), ("em cheyali ippudu?", "next"),
    # daily report
    ("ee roju em jarigindi?", "daily"), ("ivala em jarigindi", "daily"), ("today em jarigindi", "daily"),
    ("em jarigindi ee roju", "daily"), ("ee roju em chesav", "daily"), ("ee roju report ivvu", "daily"),
    ("today report", "daily"), ("what happened today", "daily"), ("em jarigindi", "daily"),
    ("nedu em jarigindi cheppu", "daily"), ("robot ee roju em chesindi", "daily"), ("eeroju report chupinchu", "daily"),
    # week
    ("ee vaaram ela undi", "/week"), ("ee vaaram em jarigindi", "/week"), ("this week summary", "/week"),
    ("ee vaaram report", "/week"), ("weekly report chupinchu", "/week"), ("ee week ela undi", "/week"),
    ("vaaram summary ivvu", "/week"), ("what happened this week", "/week"),
    # drafts list
    ("pending drafts enni", "/list"), ("drafts enni unnayi", "/list"), ("enni drafts unnayi", "/list"),
    ("drafts chupinchu", "/list"), ("pending drafts chupinchu", "/list"), ("waiting drafts em unnayi", "/list"),
    ("how many drafts", "/list"), ("draft list ivvu", "/list"), ("pending em unnayi", "/list"),
    ("approval kosam em unnayi", "/list"), ("show pending drafts", "/list"), ("drafts entha mandi unnayi", "/list"),
    ("pending enni", "/list"), ("migilina drafts chupinchu", "/list"),
    # view one draft
    ("draft 5 chupinchu", "/view 5"), ("aido draft chupinchu", "/view 5"), ("draft 12 chudali", "/view 12"),
    ("draft moodu details", "/view 3"), ("show draft 7", "/view 7"), ("draft 9 lo em undi", "/view 9"),
    ("rendo draft chupinchu", "/view 2"), ("draft number 4 chupinchu", "/view 4"),
    # decide
    ("drafts okkokkati ga chupinchu", "/decide"), ("drafts decide cheddam", "/decide"),
    ("drafts one by one chupinchu", "/decide"),
    # packs
    ("packs enni unnayi", "/packs"), ("enni packs unnayi", "/packs"), ("pack list ivvu", "/packs"),
    ("packs chupinchu", "/packs"), ("how many packs", "/packs"), ("products enni unnayi", "/packs"),
    # one pack
    ("pack 3 details", "/pack 3"), ("rendo pack details chupinchu", "/pack 2"), ("pack 3 lo em undi", "/pack 3"),
    ("pack 6 chupinchu", "/pack 6"), ("show pack 8", "/pack 8"), ("pack nalugu details cheppu", "/pack 4"),
    ("aaro pack vivaralu", "/pack 6"),
    # files and marketing assets
    ("pack 3 files kavali", "/files 3"), ("3 pack files pampu", "/files 3"), ("files 3 send chey", "/files 3"),
    ("naku pack 5 files pampinchu", "/files 5"), ("send files of pack 2", "/files 2"),
    ("pack 4 pins pampu", "/pins 4"), ("pins 4 pampinchu", "/pins 4"), ("pack 2 covers pampu", "/covers 2"),
    ("cover 2 chupinchu", "/covers 2"), ("pack 3 carousel pampu", "/carousel 3"), ("slides 3 pampu", "/carousel 3"),
    ("pack 5 reel pampu", "/reel 5"), ("pack 6 blog pampu", "/blog 6"), ("pack 7 landing page pampu", "/landing 7"),
    ("pack 8 launch checklist pampu", "/launch 8"), ("pack 2 kit pampu", "/kit 2"), ("store kit 2 pampu", "/kit 2"),
    ("pack 9 listing kit kavali", "/kit 9"), ("pack 3 marketing chupinchu", "/marketing 3"),
    ("pack 3 marketing files chupinchu", "/marketing 3"), ("marketing 4 chupinchu", "/marketing 4"),
    # sell / ready
    ("pack 3 sell chey", "/sell 3"), ("pack 3 ni sell cheyali", "/sell 3"), ("sell pack 5", "/sell 5"),
    ("pack 2 gumroad lo pettu", "/sell 2"), ("rendo pack ammali", "/sell 2"), ("em sell cheyali", "/sell"),
    ("sell cheyadaniki em ready ga undi", "/sell"), ("gumroad lo em pettali", "/sell"),
    ("edi sell cheyali ippudu", "/sell"), ("pack nalugu ni gumroad lo pettu", "/sell 4"),
    ("ready packs enni", "/ready"), ("pack 3 ready aa", "/ready 3"), ("ready list chupinchu", "/ready"),
    ("em ready ga undi", "/ready"), ("sellable packs enni", "/ready"),
    # sales
    ("ee roju sales entha", "/sales today"), ("sales entha", "/sales"), ("ee vaaram sales", "/sales week"),
    ("ee nela sales entha", "/sales month"), ("dabbu entha vachindi", "/sales"), ("sales report ivvu", "/sales"),
    ("today sales chupinchu", "/sales today"), ("enni orders vachayi", "/sales"), ("how much money came", "/sales"),
    ("ammakalu ela unnayi", "/sales"),
    # posts
    ("ee roju em post cheyali", "/today"), ("ee roju posts enti", "/today"), ("today posts chupinchu", "/today"),
    ("em post cheyali", "/today"), ("ivala posts", "/today"), ("repu posts enti", "/post tomorrow"),
    ("tomorrow posts chupinchu", "/post tomorrow"), ("ee vaaram posts", "/post week"),
    ("this week posts chupinchu", "/post week"), ("rendo post chesanu", "/posted 2"),
    ("post 1 ayipoyindi", "/posted 1"), ("moodo post done", "/posted 3"), ("post 4 chesa", "/posted 4"),
    # status / pipeline / problems
    ("status enti", "/status"), ("robot ela undi", "/status"), ("status cheppu", "/status"),
    ("robot status", "/status"), ("system ela undi", "/status"), ("paristhithi enti", "/status"),
    ("robot pani chestunda", "/status"), ("pipeline ela undi", "/pipeline"), ("pipeline chupinchu", "/pipeline"),
    ("progress entha", "/pipeline"), ("em problem undi", "/doctor"), ("edo problem undi", "/doctor"),
    ("errors unnaya", "/doctor"), ("problems em unnayi", "/doctor"), ("doctor report", "/doctor"),
    ("em fail ayyindi", "/doctor"), ("samasya enti", "/doctor"), ("health check chey", "/health"),
    ("system health chupinchu", "/health"), ("logs chupinchu", "/logs"), ("last run em ayyindi", "/logs"),
    # reports
    ("ideas chupinchu", "/ideas"), ("kotha ideas em unnayi", "/ideas"), ("keywords cheppu", "/keywords"),
    ("top keywords chupinchu", "/keywords"), ("pack 5 seo", "/seo 5"), ("seo score 3", "/seo 3"),
    ("pack 2 lint check", "/lint 2"), ("faq 4", "/faq 4"), ("pack 4 faq chupinchu", "/faq 4"),
    ("pack 3 version enti", "/versions 3"), ("duplicates check chey", "/dup"), ("boards chupinchu", "/boards"),
    ("pinterest boards plan", "/boards"), ("calendar pampu", "/calendar"), ("ops report", "/ops"),
    ("dashboard pampu", "/dashboard"),
    # settings and other plugins
    ("settings chupinchu", "/settings"), ("robot settings", "/settings"), ("auto approve status", "/autoapprove"),
    ("autoapprove settings", "/autoapprove"), ("na assets chupinchu", "/assets"), ("logos chupinchu", "/assets"),
    ("office chupinchu", "/office"), ("rules chupinchu", "/rules"), ("cubes chupinchu", "/cubes"),
    # what next
    ("em cheyali ippudu", "next"), ("ippudu em cheyali", "next"), ("next enti", "next"), ("what next", "next"),
    ("nenu ippudu em cheyali", "next"), ("taruvata em cheyali", "next"), ("next step enti", "next"),
    ("what should i do now", "next"), ("em cheyali", "next"), ("ippudu enti", "next"),
    # one-letter typos still understood
    ("satus enti", "/status"), ("pending drafs entha", "/list"), ("pack 3 filles pampu", "/files 3"),
    ("setings chupinchu", "/settings"), ("keywrds cheppu", "/keywords"),
]

TELUGU_CARDINAL = ["okati", "rendu", "moodu", "nalugu", "aidu", "aaru", "edu", "enimidi", "tommidi", "padi",
                   "padakondu", "pannendu", "padamoodu", "padnalugu", "padihenu", "padaharu", "padihedu",
                   "paddenimidi", "pandommidi", "iravai"]
TELUGU_ORDINAL = ["okato", "rendo", "moodo", "nalugo", "aido", "aaro", "edo", "enimido", "tommido", "pado",
                  "padakondo", "pannendo", "padamoodo", "padnalugo", "padiheno", "padaharo", "padihedo",
                  "paddenimido", "pandommido", "iravayyo"]
ENGLISH_CARDINAL = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                    "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
                    "twenty"]
ENGLISH_ORDINAL = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
                   "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth",
                   "eighteenth", "nineteenth", "twentieth"]
ASSETS = [("files", "/files"), ("covers", "/covers"), ("pins", "/pins"), ("carousel", "/carousel"),
          ("reel", "/reel"), ("blog", "/blog"), ("landing", "/landing"), ("launch", "/launch"), ("kit", "/kit")]


def generated_phrases():
    phrases = []
    for index, word in enumerate(TELUGU_CARDINAL, 1):
        phrases.append((f"pack {word} files pampu", f"/files {index}"))
    for index, word in enumerate(TELUGU_ORDINAL, 1):
        phrases.append((f"{word} pack sell chey", f"/sell {index}"))
    for index, word in enumerate(ENGLISH_CARDINAL, 1):
        phrases.append((f"pack {word} kit pampu", f"/kit {index}"))
    for index, word in enumerate(ENGLISH_ORDINAL, 1):
        phrases.append((f"{word} pack details chupinchu", f"/pack {index}"))
    for word, command in ASSETS:
        phrases.append((f"pack 3 {word} pampu", f"{command} 3"))
        phrases.append((f"moodo pack {word} pampinchu", f"{command} 3"))
        phrases.append((f"{word} seven send chey", f"{command} 7"))
        phrases.append((f"pack padakondu {word} kavali", f"{command} 11"))
    return phrases


PHRASES = HAND_WRITTEN + generated_phrases()

NOT_FOR_TALK = ["hi", "Hi", "hello", "namaste", "hii", "hey", "good morning", "thanks", "thank you", "ok", "menu",
                "help", "options", "commands", "naku help kavali", "ela cheyali", "", "   ", "/status", "/list",
                "/sell 3", "/files 3", "/talkx", "how are you", "what is the weather like in hyderabad",
                "nenu bayataki veltunna", "mee peru enti", "asdf qwer zxcv", "12345", "😀", "x" * 200]

RISKY = [("draft 5 approve chey", "/approve 5"), ("approve 7", "/approve 7"), ("draft 3 reject chey", "/reject 3"),
         ("5 vaddu", "/reject 5"), ("robot ni aapu", "/pause"), ("pause chey", "/pause"),
         ("robot ni resume chey", "/resume"), ("malli modalupettu", "/resume")]


class NumberTests(unittest.TestCase):
    def test_digits(self):
        self.assertEqual(ti.extract_numbers("pack 3 files"), [3])
        self.assertEqual(ti.extract_numbers("pack #12"), [12])
        self.assertEqual(ti.extract_numbers("3rd pack"), [3])

    def test_two_numbers_in_order(self):
        self.assertEqual(ti.extract_numbers("pack 3 title 2"), [3, 2])

    def test_telugu_cardinals_one_to_twenty(self):
        for index, word in enumerate(TELUGU_CARDINAL, 1):
            self.assertEqual(ti.extract_numbers(f"pack {word}"), [index], word)

    def test_telugu_ordinals_one_to_twenty(self):
        for index, word in enumerate(TELUGU_ORDINAL, 1):
            self.assertEqual(ti.extract_numbers(f"{word} pack"), [index], word)

    def test_english_words_one_to_twenty(self):
        for index, word in enumerate(ENGLISH_CARDINAL, 1):
            self.assertEqual(ti.extract_numbers(f"pack {word}"), [index], word)
        for index, word in enumerate(ENGLISH_ORDINAL, 1):
            self.assertEqual(ti.extract_numbers(f"{word} pack"), [index], word)

    def test_spelling_variants(self):
        for word, value in (("mudu", 3), ("naalugu", 4), ("yedu", 7), ("thommidi", 9), ("moodava", 3),
                            ("rendava", 2), ("modati", 1)):
            self.assertEqual(ti.extract_numbers(f"pack {word} files"), [value], word)

    def test_everyday_words_are_not_numbers(self):
        self.assertEqual(ti.extract_numbers("edo problem undi"), [])
        self.assertEqual(ti.extract_numbers("oka pack sell chey"), [])
        self.assertEqual(ti.extract_numbers("one more thing"), [])
        self.assertEqual(ti.extract_numbers("padi mandi unnaru"), [])

    def test_ambiguous_words_count_next_to_pack(self):
        self.assertEqual(ti.extract_numbers("edo pack files pampu"), [7])
        self.assertEqual(ti.extract_numbers("pack one files"), [1])
        self.assertEqual(ti.extract_numbers("pado pack"), [10])

    def test_no_numbers(self):
        self.assertEqual(ti.extract_numbers("status enti"), [])
        self.assertEqual(ti.extract_numbers(None), [])


class PhraseTableTests(unittest.TestCase):
    def test_table_has_250_plus_distinct_phrases(self):
        self.assertGreaterEqual(len({phrase.lower() for phrase, _ in PHRASES}), 250)

    def test_every_phrase_is_understood_and_would_run(self):
        wrong = []
        for phrase, command in PHRASES:
            found = ti.detect(phrase)
            if not found or found["command"] != command or not ti.should_run(found):
                wrong.append((phrase, command, found and (found["command"], found["confidence"])))
        self.assertEqual(wrong, [])

    def test_hand_written_phrases(self):
        for phrase, command in HAND_WRITTEN:
            with self.subTest(phrase=phrase):
                found = ti.detect(phrase)
                self.assertIsNotNone(found)
                self.assertEqual(found["command"], command)
                self.assertGreaterEqual(found["confidence"], ti.RUN_CONFIDENCE)

    def test_case_and_punctuation_do_not_matter(self):
        for phrase, command in (("PACK 3 FILES PAMPU", "/files 3"), ("Pending drafts entha???", "/list"),
                                ("  ee  roju   em jarigindi  ", "daily"), ("Em cheyali, ippudu?!", "next"),
                                ("pack-3 files pampu.", "/files 3")):
            self.assertEqual(ti.detect(phrase)["command"], command, phrase)

    def test_every_intent_is_reached_by_some_phrase(self):
        reached = {ti.detect(phrase)["intent"] for phrase, _ in PHRASES + RISKY}
        self.assertEqual(sorted(set(ti.INTENT_BY_NAME) - reached), [])

    def test_not_for_talk(self):
        for text in NOT_FOR_TALK:
            self.assertIsNone(ti.detect(text), repr(text))

    def test_non_text(self):
        for value in (None, 5, ["pack"], {"a": 1}):
            self.assertIsNone(ti.detect(value))

    def test_slash_text_is_never_understood_here(self):
        for text in ("/status", "/sell 3", "/anything at all", " /list"):
            self.assertIsNone(ti.detect(text))

    def test_risky_commands_are_understood_but_never_run(self):
        for phrase, command in RISKY:
            found = ti.detect(phrase)
            self.assertEqual(found["command"], command, phrase)
            self.assertTrue(found["risky"], phrase)
            self.assertFalse(ti.should_run(found), phrase)

    def test_low_confidence_is_not_run(self):
        for phrase in ("pack 3", "pending", "drafts", "ready", "money vishayam", "week"):
            found = ti.detect(phrase)
            self.assertIsNotNone(found, phrase)
            self.assertLess(found["confidence"], ti.RUN_CONFIDENCE, phrase)
            self.assertFalse(ti.should_run(found), phrase)

    def test_typo_lowers_confidence_a_little(self):
        clean, typo = ti.detect("status enti"), ti.detect("satus enti")
        self.assertEqual(typo["command"], clean["command"])
        self.assertTrue(typo["typo"])
        self.assertLess(typo["confidence"], clean["confidence"])

    def test_alternatives_are_other_commands(self):
        found = ti.detect("pack 3 files pampu")
        self.assertEqual(found["command"], "/files 3")
        self.assertIn("/pack 3", [item["command"] for item in found["alternatives"]])
        self.assertNotIn("/files 3", [item["command"] for item in found["alternatives"]])

    def test_specific_wins_over_general(self):
        self.assertEqual(ti.detect("pack 3 marketing files chupinchu")["command"], "/marketing 3")
        self.assertEqual(ti.detect("ee vaaram posts")["command"], "/post week")
        self.assertEqual(ti.detect("ee roju em post cheyali")["command"], "/today")
        self.assertEqual(ti.detect("em sell cheyali")["command"], "/sell")

    def test_sales_period(self):
        self.assertEqual(ti.detect("sales")["command"], "/sales")
        self.assertEqual(ti.detect("ee roju sales")["command"], "/sales today")
        self.assertEqual(ti.detect("ee vaaram sales entha")["command"], "/sales week")
        self.assertEqual(ti.detect("ee nela sales")["command"], "/sales month")

    def test_command_without_number_when_none_given(self):
        self.assertEqual(ti.detect("files pampu")["command"], "/files")
        self.assertEqual(ti.detect("kit pampu")["command"], "/kit")

    def test_suggestions_are_exactly_two_distinct_commands(self):
        for phrase in ("pack 3", "pending", "ready", "money vishayam", "draft 5 approve chey", "robot ni aapu"):
            options = ti.suggestions(ti.detect(phrase))
            self.assertEqual(len(options), 2, phrase)
            self.assertNotEqual(options[0]["command"], options[1]["command"], phrase)
            for option in options:
                self.assertTrue(option["command"].startswith("/"), phrase)
                self.assertTrue(option["help"], phrase)

    def test_risky_suggestion_second_option_is_safe(self):
        self.assertEqual([o["command"] for o in ti.suggestions(ti.detect("robot ni aapu"))], ["/pause", "/status"])
        self.assertEqual([o["command"] for o in ti.suggestions(ti.detect("approve chey"))], ["/approve", "/list"])

    def test_intent_table_is_well_formed(self):
        names = [intent["name"] for intent in ti.INTENTS]
        self.assertEqual(len(names), len(set(names)))
        for intent in ti.INTENTS:
            self.assertTrue(intent["help"])
            for needed, confidence in intent["patterns"]:
                self.assertTrue(set(needed) <= ti.TOKENS, (intent["name"], needed))
                self.assertTrue(0 < confidence < 1)

    def test_one_edit_apart(self):
        self.assertTrue(ti._one_edit_apart("status", "satus"))
        self.assertTrue(ti._one_edit_apart("status", "statsu"))
        self.assertTrue(ti._one_edit_apart("status", "statuss"))
        self.assertTrue(ti._one_edit_apart("status", "statis"))
        self.assertFalse(ti._one_edit_apart("status", "status"))
        self.assertFalse(ti._one_edit_apart("status", "stts"))


class TalkBase(unittest.TestCase):
    """Temp folder as working directory, temp database, recording ctx."""

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
        self.texts, self.keyboards, self.documents, self.photos = [], [], [], []
        patches = [
            mock.patch.dict(os.environ, {"CHILLI_PRODUCTS_DIR": str(self.products),
                                         "CHILLI_MARKETING_DIR": str(self.marketing)}),
            mock.patch("truth_check.check", return_value={"verdict": "ready", "score": 100, "problems": []}),
            mock.patch("product_qa_deep.check_folder", return_value={"verdict": "ready", "score": 100, "problems": []}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.ctx = {
            "db_path": self.db, "chat_id": str(OWNER), "now": datetime.datetime(2026, 11, 14, 6, 0, tzinfo=datetime.timezone.utc),
            "send_text": self._send_text,
            "send_document": lambda path, caption="": self.documents.append(path) or True,
            "send_photo": lambda path, caption="": self.photos.append(path) or True,
            "send_media_group": lambda paths, caption="": True,
            "state_get": lambda key, default=None: telegram_inbox.get_state(key, default=default, db_path=self.db),
            "state_set": lambda key, value: telegram_inbox.set_state(key, value, db_path=self.db),
        }

    def _send_text(self, text, keyboard=None):
        self.texts.append(text)
        self.keyboards.append(keyboard)
        return True

    def add_draft(self, title="Draft: [Budget Sheets Agent] Monthly budget sheet"):
        return guardrails.request_approval("public_draft", title, "PRODUCT NAME: Budget sheet\n", None, self.db)

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


class TalkPluginTests(TalkBase):
    def test_commands_entry_and_interface(self):
        entry = tg_talk.COMMANDS[0]
        self.assertEqual(entry["cmd"], "/talk")
        self.assertTrue(entry["help_en"] and entry["help_te"] and entry["words"])
        source = Path(tg_talk.__file__).read_text(encoding="utf-8")
        top_level = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        self.assertFalse([line for line in top_level if "telegram_inbox" in line])
        self.assertNotIn("api.telegram.org", source)
        self.assertNotIn("chat_intents", source)
        self.assertNotIn("tg_chat", source)

    def test_slash_commands_are_never_answered(self):
        import glob
        commands = ["/" + name for name in telegram_inbox._COMMANDS.split("|")]
        for path in glob.glob(str(Path(tg_talk.__file__).parent / "tg_*.py")):
            module = __import__(Path(path).stem)
            commands += [entry["cmd"] for entry in getattr(module, "COMMANDS", []) if entry["cmd"] != "/talk"]
        self.assertGreater(len(commands), 50)
        for command in commands:
            self.assertIsNone(tg_talk.handle(command, self.ctx), command)
            self.assertIsNone(tg_talk.handle(command + " 3", self.ctx), command)
        self.assertEqual(self.texts, [])

    def test_greetings_menu_and_chatter_return_none(self):
        for text in NOT_FOR_TALK:
            self.assertIsNone(tg_talk.handle(text, self.ctx), repr(text))
        self.assertIsNone(tg_talk.handle(None, self.ctx))

    def test_talk_command_shows_live_examples(self):
        reply = tg_talk.handle("/talk", self.ctx)
        self.assertIn("pack 3 files pampu  ->  /files 3", reply["text"])
        self.assertIn("moodo pack sell chey  ->  /sell 3", reply["text"])
        self.assertIn("pending drafts entha  ->  /list", reply["text"])
        self.assertEqual(tg_talk.handle("maatladu", self.ctx), reply)

    def test_pending_drafts_is_the_real_list_output(self):
        self.add_draft()
        reply = tg_talk.handle("pending drafts entha", self.ctx)
        self.assertEqual(reply["text"], telegram_inbox.handle_command("list", [], self.db))
        self.assertIn("Monthly budget sheet", reply["text"])

    def test_list_output_changes_with_the_database(self):
        empty = tg_talk.handle("drafts enni unnayi", self.ctx)["text"]
        self.add_draft()
        self.add_draft("Draft: [ADHD Planners Agent] Focus planner")
        full = tg_talk.handle("drafts enni unnayi", self.ctx)["text"]
        self.assertNotEqual(empty, full)
        self.assertIn("Focus planner", full)

    def test_daily_report_is_the_real_report(self):
        reply = tg_talk.handle("ee roju em jarigindi", self.ctx)
        self.assertEqual(reply["text"], daily_report.report_text(db_path=self.db))
        self.assertIn("daily report", reply["text"])

    def test_status_is_the_real_status(self):
        with mock.patch.object(telegram_inbox, "status_text", wraps=telegram_inbox.status_text) as status:
            reply = tg_talk.handle("robot ela undi", self.ctx)
        status.assert_called_once_with(self.db)
        self.assertIn("Chilli Gavva Status", reply["text"])

    def test_view_draft_with_telugu_ordinal(self):
        aid = self.add_draft()
        word = TELUGU_ORDINAL[aid - 1]
        reply = tg_talk.handle(f"{word} draft chupinchu", self.ctx)
        self.assertEqual(reply["text"], telegram_inbox.handle_command("view", [aid], self.db))
        self.assertIn("Budget sheet", reply["text"])

    def test_files_goes_through_tg_files_with_the_number(self):
        with mock.patch.object(tg_files, "handle", wraps=tg_files.handle) as files:
            reply = tg_talk.handle("pack moodu files pampu", self.ctx)
        files.assert_called_once()
        self.assertEqual(files.call_args[0][0], "/files 3")
        self.assertEqual(reply, tg_files.handle("/files 3", self.ctx))

    def test_files_really_sends_the_pack_documents(self):
        pack = self.add_pack()
        reply = tg_talk.handle("pack 3 files pampu", dict(self.ctx, products_dir=str(self.products)))
        self.assertEqual(reply, {"handled": True})
        self.assertEqual([Path(path).name for path in self.documents], ["product.zip", "cover.png", "listing.txt"])
        self.assertEqual(self.documents[0], str(pack / "product.zip"))

    def test_sell_with_telugu_number_runs_tg_sell(self):
        self.add_pack()
        with mock.patch.object(tg_sell, "handle", wraps=tg_sell.handle) as sell:
            reply = tg_talk.handle("moodo pack sell chey", self.ctx)
        self.assertEqual(sell.call_args[0][0], "/sell 3")
        self.assertEqual(reply, {"handled": True})
        self.assertIn("Pack #3", self.texts[0])
        self.assertIn("1/4 GUMROAD FIELDS", self.texts[1])
        self.assertEqual([Path(path).name for path in self.documents], ["product.zip", "LICENSE.txt"])
        self.assertEqual([Path(path).name for path in self.photos], ["cover.png", "preview-1.png"])

    def test_sell_missing_pack_is_tg_sells_own_answer(self):
        reply = tg_talk.handle("pack 9 sell chey", self.ctx)
        self.assertEqual(reply, tg_sell.handle("/sell 9", self.ctx))
        self.assertIn("Pack #9", reply["text"])

    def test_sales_today_is_the_real_sales_report(self):
        import tg_sales
        reply = tg_talk.handle("ee roju sales entha", self.ctx)
        self.assertEqual(reply, tg_sales.handle("/sales today", self.ctx))
        self.assertIn("Sales Report", reply["text"])

    def test_doctor_pipeline_week_are_real_outputs(self):
        for phrase, command in (("em problem undi", "doctor"), ("pipeline ela undi", "pipeline"),
                                ("ee vaaram ela undi", "week")):
            reply = tg_talk.handle(phrase, self.ctx)
            self.assertEqual(reply["text"], telegram_inbox.handle_command(command, [], self.db), phrase)
            self.assertTrue(reply["text"].strip())

    def test_today_posts_is_real_output(self):
        reply = tg_talk.handle("ee roju em post cheyali", self.ctx)
        self.assertEqual(reply["text"], telegram_inbox.handle_command("today", [], self.db))

    def test_every_safe_intent_runs_real_code_and_answers(self):
        for intent in ti.INTENTS:
            if intent.get("risky"):
                continue
            command = intent["command"].replace("{n}", "3").replace("{period}", "today").strip()
            self.texts.clear()
            reply = tg_talk.run_command(command, self.ctx)
            self.assertIsInstance(reply, dict, command)
            said = str(reply.get("text", "")) + "\n".join(self.texts)
            self.assertTrue(said.strip() or self.documents, command)     # /calendar answers with a file
            self.assertNotIn("ardam kaaledu", said.lower(), command)

    def test_plugin_lookup_finds_the_owner_of_each_command(self):
        self.assertEqual(tg_talk.plugin_for("/files").__name__, "tg_files")
        self.assertEqual(tg_talk.plugin_for("/sell").__name__, "tg_sell")
        self.assertEqual(tg_talk.plugin_for("/SALES").__name__, "tg_sales")
        self.assertIsNone(tg_talk.plugin_for("/nope"))
        self.assertIsNone(tg_talk.plugin_for("/talk"))

    def test_unknown_command_gives_none(self):
        self.assertIsNone(tg_talk.run_command("/nope 3", self.ctx))

    def test_next_step_with_waiting_drafts(self):
        self.add_draft()
        reply = tg_talk.handle("em cheyali ippudu?", self.ctx)
        self.assertIn("1 drafts mee approval kosam wait chestunnayi", reply["text"])
        self.assertIn(telegram_inbox.handle_command("list", [], self.db), reply["text"])

    def test_next_step_with_sellable_pack(self):
        self.add_pack()
        reply = tg_talk.handle("ippudu em cheyali", self.ctx)
        self.assertIn("Gumroad lo pettochu", reply["text"])
        self.assertIn("/sell 3 — Focus Planner Printable Pack", reply["text"])
        self.assertEqual(self.texts, [])           # quoted, not sent twice

    def test_next_step_with_nothing_waiting(self):
        reply = tg_talk.handle("next enti", self.ctx)
        self.assertIn("drafts levu", reply["text"])
        self.assertIn(telegram_inbox.handle_command("today", [], self.db), reply["text"])

    def test_low_confidence_sends_two_button_keyboard(self):
        reply = tg_talk.handle("pack 3", self.ctx)
        self.assertEqual(reply, {"handled": True})
        self.assertEqual(len(self.texts), 1)
        self.assertEqual(len(self.keyboards[0]), 2)
        self.assertEqual(self.keyboards[0][0], ["/pack 3"])
        self.assertIn("1. /pack 3", self.texts[0])
        self.assertIn("tap cheyandi", self.texts[0])
        self.assertEqual(self.documents, [])

    def test_low_confidence_without_send_text_returns_keyboard(self):
        ctx = dict(self.ctx)
        ctx.pop("send_text")
        reply = tg_talk.handle("pending", ctx)
        self.assertEqual(len(reply["keyboard"]), 2)
        self.assertEqual(reply["keyboard"][0], ["/list"])
        self.assertIn("/list", reply["text"])

    def test_suggestion_buttons_are_real_commands(self):
        tg_talk.handle("ready", self.ctx)
        for row in self.keyboards[0]:
            command = row[0]
            self.assertTrue(command.startswith("/"))
            builtin = telegram_inbox.parse_command(command)[0]
            self.assertTrue(builtin or tg_talk.plugin_for(command.split()[0]), command)

    def test_risky_talk_is_never_executed(self):
        aid = self.add_draft()
        for phrase in (f"draft {aid} approve chey", f"draft {aid} reject chey", "robot ni aapu"):
            self.texts.clear()
            self.keyboards.clear()
            reply = tg_talk.handle(phrase, self.ctx)
            self.assertEqual(reply, {"handled": True}, phrase)
            self.assertIn("pedda action", self.texts[0], phrase)
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "pending")
        self.assertFalse(guardrails.is_paused(self.db))

    def test_risky_keyboard_offers_exact_command(self):
        tg_talk.handle("draft 5 approve chey", self.ctx)
        self.assertEqual(self.keyboards[0][0], ["/approve 5"])

    def test_command_error_is_reported_not_raised(self):
        with mock.patch.object(tg_talk, "run_command", side_effect=RuntimeError("boom")):
            reply = tg_talk.handle("status enti", self.ctx)
        self.assertIn("problem vachindi (RuntimeError)", reply["text"])
        self.assertIn("/status", reply["text"])

    def test_empty_command_output_falls_back_to_suggestions(self):
        with mock.patch.object(tg_talk, "run_command", return_value=None):
            reply = tg_talk.handle("status enti", self.ctx)
        self.assertEqual(reply, {"handled": True})
        self.assertEqual(self.keyboards[0][0], ["/status"])

    def test_no_money_in_plugin_texts(self):
        tg_talk.handle("pack 3", self.ctx)
        tg_talk.handle("robot ni aapu", self.ctx)
        for text in self.texts + [tg_talk.examples_text()]:
            self.assertIsNone(tg_sell.MONEY_RE.search(text))

    def test_number_words_reach_the_command(self):
        self.add_pack("11-budget-sheet", title="Budget Sheet")
        reply = tg_talk.handle("padakondo pack sell chey", self.ctx)
        self.assertEqual(reply, {"handled": True})
        self.assertIn("Pack #11", self.texts[0])


class PerIntentTests(TalkBase):
    """One test per intent (added below): its phrases are understood and its command really answers."""


def _intent_test(name):
    def test(self):
        intent = ti.INTENT_BY_NAME[name]
        phrases = [phrase for phrase, _ in PHRASES + RISKY if ti.detect(phrase)["intent"] == name]
        self.assertTrue(phrases, f"no phrase reaches intent {name}")
        for phrase in phrases[:6]:
            found = ti.detect(phrase)
            self.assertEqual(found["command"].split()[0], intent["command"].split()[0], phrase)
            self.texts.clear()
            self.keyboards.clear()
            del self.documents[:]
            reply = tg_talk.handle(phrase, self.ctx)
            self.assertIsInstance(reply, dict, phrase)
            said = str(reply.get("text", "")) + "\n".join(self.texts)
            self.assertTrue(said.strip() or self.documents, phrase)
            self.assertNotIn("ardam kaaledu", said.lower(), phrase)
            if intent.get("risky"):
                self.assertEqual(self.keyboards[0][0], [found["command"]], phrase)
            else:
                self.assertNotIn("Okati tap cheyandi", said, phrase)      # it ran; it did not ask
    test.__name__ = f"test_intent_{name}"
    return test


for _intent in ti.INTENTS:
    setattr(PerIntentTests, f"test_intent_{_intent['name']}", _intent_test(_intent["name"]))


class CallPluginsTests(TalkBase):
    """Reachability: telegram_inbox.call_plugins with fake send functions."""

    def setUp(self):
        super().setUp()
        for name, sink in (("send_text", None), ("send_document", self.documents), ("send_photo", self.photos)):
            if name == "send_text":
                side = lambda token, chat, text, buttons=None, keyboard=None: self._send_text(text, keyboard)
            else:
                side = (lambda target: lambda token, chat, path, caption="": target.append(path) or True)(sink)
            patch = mock.patch.object(telegram_inbox, name, side_effect=side)
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, text):
        return telegram_inbox.call_plugins(text, "test-token", OWNER, db_path=self.db)

    def test_plugin_is_discovered_last(self):
        names = [plugin.__name__ for plugin in telegram_inbox.discover_plugins()]
        self.assertIn("tg_talk", names)
        self.assertGreater(names.index("tg_talk"), names.index("tg_menu"))
        self.assertGreater(names.index("tg_talk"), names.index("tg_sell"))

    def test_tenglish_sell_through_call_plugins(self):
        self.add_pack()
        handled, reply = self.call("moodo pack sell chey")
        self.assertTrue(handled)
        self.assertEqual(reply, {"handled": True})
        self.assertIn("1/4 GUMROAD FIELDS", self.texts[1])
        self.assertEqual(len(self.documents), 2)

    def test_what_now_through_call_plugins(self):
        self.add_draft()
        handled, reply = self.call("em cheyali ippudu?")
        self.assertTrue(handled)
        self.assertIn("approval kosam wait chestunnayi", reply["text"])

    def test_robot_ela_undi_through_call_plugins(self):
        handled, reply = self.call("robot ela undi")
        self.assertTrue(handled)
        self.assertEqual(reply["text"], telegram_inbox.status_text(self.db))

    def test_hi_stays_with_tg_menu(self):
        handled, reply = self.call("Hi")
        self.assertTrue(handled)
        self.assertIn("Namaste", reply["text"])

    def test_menu_stays_with_tg_menu(self):
        import tg_menu
        handled, reply = self.call("menu")
        self.assertTrue(handled)
        self.assertEqual(reply["text"], tg_menu.format_grouped_menu())

    def test_chatter_is_not_handled_by_talk(self):
        with mock.patch.object(tg_talk, "run_command") as run:
            handled, _reply = self.call("nenu bayataki veltunna")
        run.assert_not_called()
        self.assertFalse(handled)

    def test_low_confidence_keyboard_through_call_plugins(self):
        handled, reply = self.call("money vishayam")
        self.assertTrue(handled)
        self.assertEqual(reply, {"handled": True})
        self.assertEqual(self.keyboards[-1], [["/sales"], ["/menu"]])


class EndToEndTests(TalkBase):
    """Fake Telegram updates -> telegram_inbox.process_updates -> real replies."""

    def setUp(self):
        super().setUp()
        self.updates = []
        self.next_id = 100
        self.sent = []       # (kind, payload, keyboard)

        def send_text(token, chat, text, buttons=None, keyboard=None):
            self.sent.append(("text", text, keyboard))
            return True

        patches = [
            mock.patch.object(telegram_inbox, "fetch_updates", side_effect=lambda token, offset: list(self.updates)),
            mock.patch.object(telegram_inbox, "acknowledge_update", return_value=True),
            mock.patch.object(telegram_inbox, "_call", return_value={"ok": True}),
            mock.patch.object(telegram_inbox, "send_text", side_effect=send_text),
            mock.patch.object(telegram_inbox, "send_document",
                              side_effect=lambda token, chat, path, caption="": self.sent.append(("document", path, None)) or True),
            mock.patch.object(telegram_inbox, "send_photo",
                              side_effect=lambda token, chat, path, caption="": self.sent.append(("photo", path, None)) or True),
            mock.patch.object(telegram_inbox, "send_media_group",
                              side_effect=lambda token, chat, paths, caption="": self.sent.append(("group", paths, None)) or True),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def say(self, text, chat=OWNER):
        self.updates = [{"update_id": self.next_id, "message": {"message_id": self.next_id, "chat": {"id": chat},
                                                                "text": text}}]
        self.next_id += 1
        self.sent = []
        handled = telegram_inbox.process_updates("test-token", OWNER, db_path=self.db)
        return handled, [payload for kind, payload, _ in self.sent if kind == "text"]

    def test_moodo_pack_sell_chey(self):
        self.add_pack()
        handled, texts = self.say("moodo pack sell chey")
        self.assertEqual(handled, 1)
        self.assertIn("Pack #3", texts[0])
        self.assertIn("1/4 GUMROAD FIELDS", texts[1])
        self.assertIn("4/4 GUMROAD CHECKLIST", texts[2])
        kinds = [kind for kind, _, _ in self.sent]
        self.assertEqual(kinds.count("document"), 2)
        self.assertEqual(kinds.count("photo"), 2)

    def test_em_cheyali_ippudu(self):
        self.add_draft()
        _handled, texts = self.say("em cheyali ippudu?")
        self.assertEqual(len(texts), 1)
        self.assertIn("1 drafts mee approval kosam wait chestunnayi", texts[0])
        self.assertIn("Monthly budget sheet", texts[0])

    def test_ee_roju_em_jarigindi(self):
        _handled, texts = self.say("ee roju em jarigindi")
        self.assertEqual(texts, [daily_report.report_text(db_path=self.db)])

    def test_pending_drafts_entha(self):
        self.add_draft()
        _handled, texts = self.say("pending drafts entha")
        self.assertEqual(texts[0], telegram_inbox.handle_command("list", [], self.db))
        self.assertIn("Monthly budget sheet", "\n".join(texts))

    def test_robot_ela_undi(self):
        _handled, texts = self.say("robot ela undi")
        self.assertEqual(texts, [telegram_inbox.status_text(self.db)])

    def test_ee_roju_sales_entha(self):
        _handled, texts = self.say("ee roju sales entha")
        self.assertEqual(len(texts), 1)
        self.assertIn("Sales Report", texts[0])
        self.assertIn("Today", texts[0])

    def test_em_problem_undi(self):
        _handled, texts = self.say("em problem undi")
        self.assertEqual(texts, [telegram_inbox.handle_command("doctor", [], self.db)])

    def test_ee_vaaram_ela_undi(self):
        _handled, texts = self.say("ee vaaram ela undi")
        self.assertEqual(len(texts), 1)
        self.assertIn("Ee Vaaram Summary", texts[0])

    def test_telugu_number_words_one_to_five_end_to_end(self):
        for index, word in enumerate(TELUGU_ORDINAL[:5], 1):
            _handled, texts = self.say(f"{word} pack sell chey")
            self.assertEqual(len(texts), 1, word)
            self.assertIn(f"Pack #{index} ready upload pack dorakaledu", texts[0], word)

    def test_low_confidence_gets_two_suggestions_keyboard(self):
        handled, texts = self.say("money vishayam")
        self.assertEqual(handled, 1)
        self.assertEqual(len(texts), 1)
        self.assertIn("Okati tap cheyandi", texts[0])
        self.assertEqual(self.sent[0][2], [["/sales"], ["/menu"]])

    def test_tapping_a_suggestion_runs_the_real_command(self):
        self.say("money vishayam")
        _handled, texts = self.say("/sales")
        self.assertEqual(len(texts), 1)
        self.assertIn("Sales Report", texts[0])

    def test_risky_talk_only_offers_the_command(self):
        aid = self.add_draft()
        self.say("robot ni aapeyi")
        self.assertFalse(guardrails.is_paused(self.db))
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "pending")

    def test_builtin_slash_commands_are_not_touched_by_talk(self):
        with mock.patch.object(tg_talk, "run_command") as run, \
                mock.patch.object(tg_talk, "suggestion_reply") as suggest:
            for command in ("/status", "/list", "/pipeline", "/doctor", "/packs", "/week", "/help"):
                handled, texts = self.say(command)
                self.assertEqual(handled, 1, command)
                self.assertTrue(texts and texts[0].strip(), command)
        run.assert_not_called()
        suggest.assert_not_called()

    def test_hi_is_answered_by_the_menu(self):
        with mock.patch.object(tg_talk, "run_command") as run:
            _handled, texts = self.say("Hi")
        run.assert_not_called()
        self.assertIn("Namaste", texts[0])

    def test_unknown_chatter_keeps_the_normal_hint(self):
        _handled, texts = self.say("nenu bayataki veltunna")
        self.assertEqual(len(texts), 1)
        self.assertIn("help", texts[0].lower())

    def test_stranger_gets_nothing(self):
        handled, texts = self.say("moodo pack sell chey", chat=STRANGER)
        self.assertEqual(handled, 0)
        self.assertEqual(self.sent, [])

    def test_same_sentence_twice_in_one_batch_is_answered_once(self):
        self.updates = [{"update_id": 500 + n, "message": {"message_id": n, "chat": {"id": OWNER},
                                                           "text": "robot ela undi"}} for n in range(2)]
        self.sent = []
        handled = telegram_inbox.process_updates("test-token", OWNER, db_path=self.db)
        self.assertEqual(handled, 2)
        texts = [payload for kind, payload, _ in self.sent if kind == "text"]
        self.assertEqual(len(texts), 1)
        self.assertIn("Chilli Gavva Status", texts[0])
        self.assertIn("(answered 2 messages)", texts[0])

    def test_nothing_is_written_outside_the_temp_folder(self):
        repo = Path(tg_talk.__file__).resolve().parent / "exported_content"
        before = sorted(str(path) for path in repo.rglob("*")) if repo.is_dir() else []
        self.add_pack()
        for text in ("moodo pack sell chey", "em cheyali ippudu?", "ee roju em post cheyali", "robot ela undi"):
            self.say(text)
        after = sorted(str(path) for path in repo.rglob("*")) if repo.is_dir() else []
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
