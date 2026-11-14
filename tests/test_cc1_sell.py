"""CC1-2: /sell N — everything to list pack N on Gumroad in one go (tg_sell plugin).

Packs live in temp folders. The pack-content checkers (truth_check, product_qa_deep) are given a
fixed verdict, exactly like the existing /ready tests, because the fixture PDFs are tiny samples;
sell_ready, store_kits and the plugin itself run for real. Nothing touches Telegram: the ctx send
functions are recorders, and the reachability tests replace telegram_inbox's send functions.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sell_ready
import store_kits
import tg_sell

SAMPLE_CHAT_ID = int("1234" "5678")
DESCRIPTION = (
    "A clean, minimal focus planner designed to help you stay organized without overwhelming you. "
    "Includes daily priority blocks, habit trackers, and notes sections. Print on standard A4 or "
    "US Letter paper for best results. Personal use licence included. Just print and write. "
    "Each page keeps wide margins and calm spacing so the layout stays easy to read at a glance."
)
TAGS = ["focus planner", "daily planner", "printable page", "desk pad", "calm routine", "habit tracker",
        "study timer", "weekly menu", "mindful notes", "goal grid", "brain dump", "weekly audit", "one page day"]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


class Recorder:
    """ctx with recording send functions (no Telegram)."""

    def __init__(self, products, marketing=None, document_ok=True, photo_ok=True):
        self.texts, self.keyboards, self.documents, self.photos, self.state = [], [], [], [], {}
        self.order = []
        self.document_ok, self.photo_ok = document_ok, photo_ok
        self.ctx = {
            "db_path": None, "chat_id": str(SAMPLE_CHAT_ID), "products_dir": str(products),
            "marketing_dir": str(marketing or (Path(products).parent / "no-marketing")),
            "send_text": self.send_text, "send_document": self.send_document, "send_photo": self.send_photo,
            "send_media_group": lambda paths, caption="": True,
            "state_get": lambda key, default=None: self.state.get(key, default),
            "state_set": lambda key, value: self.state.__setitem__(key, value),
        }

    def send_text(self, text, keyboard=None):
        self.texts.append(text)
        self.keyboards.append(keyboard)
        self.order.append("text")
        return True

    def send_document(self, path, caption=""):
        self.documents.append((path, caption))
        self.order.append("document")
        return self.document_ok

    def send_photo(self, path, caption=""):
        self.photos.append((path, caption))
        self.order.append("photo")
        return self.photo_ok

    @property
    def all_text(self):
        return "\n".join(self.texts)


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.products = self.root / "exported_content" / "products"
        self.products.mkdir(parents=True)
        self.marketing = self.root / "exported_content" / "marketing"
        self.truth = {"verdict": "ready", "score": 100, "problems": []}
        self.qa = {"verdict": "ready", "score": 100, "problems": []}
        for patch in (mock.patch("truth_check.check", side_effect=lambda *a, **k: dict(self.truth)),
                      mock.patch("product_qa_deep.check_folder", side_effect=lambda *a, **k: dict(self.qa))):
            patch.start()
            self.addCleanup(patch.stop)

    def make_pack(self, name="1-focus-planner", title="Focus Planner Printable Pack", previews=2,
                  description=DESCRIPTION, upload=False, zip_file=True, marketing=True, cover=True):
        pack = (self.products / "upload" / name) if upload else (self.products / name)
        pack.mkdir(parents=True)
        if zip_file:
            with zipfile.ZipFile(pack / "product.zip", "w") as bundle:
                bundle.writestr("planner.pdf", b"%PDF-1.4 test\n%%EOF")
                bundle.writestr("README.md", "Read me")
                bundle.writestr("LICENSE.txt", "Personal Licence")
        (pack / "listing.txt").write_text(
            f"Title: {title}\n\nDescription:\n{description}\n\nTags: {', '.join(TAGS)}\n\n"
            "What you get:\n- A4 PDF file\n- US Letter PDF file\n- Readme guide\n\nTheme: Calm Sage\n",
            encoding="utf-8")
        (pack / "checklist.txt").write_text("Checklist:\n- [ ] Upload product.zip\n- [ ] Price: owner decides\n"
                                            "- Theme: Calm Sage\n", encoding="utf-8")
        (pack / "LICENSE.txt").write_text("Personal use licence only.\n", encoding="utf-8")
        if cover:
            (pack / "cover.png").write_bytes(PNG)
        for index in range(1, previews + 1):
            (pack / f"preview-{index}.png").write_bytes(PNG)
        kits = pack / "store-kits"
        kits.mkdir()
        (kits / "gumroad.txt").write_text("kit", encoding="utf-8")
        if marketing:
            self.make_marketing(name)
        return pack

    def make_marketing(self, name):
        target = self.marketing / name
        for folder, file_name in (("carousel", "slide-1.png"), ("pins", "pin_1.png"), ("reel", "frame-1.png"),
                                  ("covers", "cover_1.png")):
            (target / folder).mkdir(parents=True, exist_ok=True)
            (target / folder / file_name).write_bytes(PNG)
        (target / "blog.md").write_text("Blog", encoding="utf-8")
        (target / "LAUNCH.md").write_text("Launch", encoding="utf-8")

    def recorder(self, **options):
        return Recorder(self.products, self.marketing, **options)

    def sell(self, text="/sell 1", **options):
        rec = self.recorder(**options)
        return rec, tg_sell.handle(text, rec.ctx)


class InterfaceTests(unittest.TestCase):
    def test_commands_entry(self):
        self.assertEqual(len(tg_sell.COMMANDS), 1)
        entry = tg_sell.COMMANDS[0]
        self.assertEqual(entry["cmd"], "/sell")
        self.assertIn("sell", entry["words"])
        self.assertTrue(entry["help_en"])
        self.assertTrue(entry["help_te"])

    def test_help_has_no_money(self):
        for entry in tg_sell.COMMANDS:
            self.assertIsNone(tg_sell.MONEY_RE.search(entry["help_en"] + entry["help_te"]))

    def test_module_never_imports_telegram_inbox(self):
        source = Path(tg_sell.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import telegram_inbox", source)
        self.assertNotIn("from telegram_inbox", source)
        self.assertNotIn("api.telegram.org", source)
        self.assertNotIn("urllib", source)

    def test_other_text_returns_none(self):
        for text in ("", "   ", "hi", "/status", "/ready 2", "/files 3", "/pack 3", "/sales", "sales",
                     "sell ready", "sellable", "pack 3 files pampu", "/sellx 3", "/sell abc",
                     "sell cheyadam ela nerchukovali ani oka pedda question adugutunna", "approve 3",
                     "I want to sell my old bike in the market today", None, 5):
            self.assertIsNone(tg_sell.handle(text, {}), repr(text))


class ParseTests(unittest.TestCase):
    def test_sell_with_number(self):
        for text in ("/sell 3", "/sell #3", "/sell pack 3", "/SELL 3", "/sell@chilli_bot 3", "sell 3",
                     "Sell pack 3", "sell #3", "pack 3 sell chey", "pack 3 sell", "3 sell chey",
                     "pack 3 ni sell chey", "pack 3 sell cheyali", "sell 3 chey", "pack 3 gumroad lo pettu",
                     "3 gumroad lo pettu", "pack 3 ni gumroad lo pettu", "gumroad lo pettu 3",
                     "pack 3 ammu", "pack 3 sell chey?", "  /sell   3  ", "pack 3 gumroad lo sell chey"):
            self.assertEqual(tg_sell.parse(text), ("sell", 3), text)

    def test_two_digit_number(self):
        self.assertEqual(tg_sell.parse("/sell 27"), ("sell", 27))

    def test_sell_alone_is_list(self):
        for text in ("/sell", "sell", "Sell", "/sell@chilli_bot", "sell chey", "sell cheyali", "sell list",
                     "sell packs", "em sell cheyali", "em sell cheyali?", "gumroad lo pettu", "gumroad listing"):
            self.assertEqual(tg_sell.parse(text), ("list", None), text)

    def test_not_for_this_plugin(self):
        for text in ("/sells", "/sell 3 4", "sell ready", "sellable", "resell", "sell abc", "/ready 3", "",
                     "pack 3 ready aa", "sales 3", "x" * 100):
            self.assertIsNone(tg_sell.parse(text), text)


class NotFoundAndListTests(Base):
    def test_unknown_pack(self):
        self.make_pack()
        rec, reply = self.sell("/sell 9")
        self.assertIn("Pack #9", reply["text"])
        self.assertIn("/sell", reply["text"])
        self.assertEqual(rec.documents, [])
        self.assertEqual(rec.texts, [])

    def test_no_packs_at_all(self):
        rec, reply = self.sell("/sell")
        self.assertIn("inka levu", reply["text"])
        self.assertEqual(rec.documents, [])

    def test_missing_products_folder(self):
        rec = Recorder(self.root / "nowhere")
        self.assertIn("inka levu", tg_sell.handle("/sell", rec.ctx)["text"])
        self.assertIn("dorakaledu", tg_sell.handle("/sell 1", rec.ctx)["text"])

    def test_list_shows_sellable_and_almost(self):
        self.make_pack("1-focus-planner")
        self.make_pack("2-budget-sheet", title="Budget Sheet", marketing=False)
        rec, reply = self.sell("/sell")
        self.assertEqual(reply, {"handled": True})
        self.assertIn("🟢 /sell 1 — Focus Planner Printable Pack (SELLABLE", rec.texts[0])
        self.assertIn("🟢 /sell 2 — Budget Sheet (SELLABLE", rec.texts[0])
        self.assertEqual(rec.keyboards[0], [["/sell 1", "/sell 2"]])
        self.assertEqual(rec.documents, [])
        self.assertEqual(rec.photos, [])

    def test_list_hides_not_yet_packs_but_counts_them(self):
        self.make_pack("1-focus-planner")
        self.make_pack("2-broken", zip_file=False)   # not an upload pack at all
        self.make_pack("3-no-truth")
        real = sell_ready.evaluate_pack

        def evaluate(path, base=None):
            result = real(path, base)
            if Path(path).name == "3-no-truth":
                result["status"] = sell_ready.STATUS_NOT_YET
            return result

        with mock.patch.object(sell_ready, "evaluate_pack", side_effect=evaluate):
            rec, _reply = self.sell("sell")
        self.assertIn("/sell 1", rec.texts[0])
        self.assertNotIn("/sell 3", rec.texts[0])
        self.assertIn("Inko 1 packs ki fixes kavali", rec.texts[0])

    def test_list_when_nothing_is_sellable(self):
        self.truth = {"verdict": "needs fix", "score": 40, "problems": ["Missing page"]}
        self.make_pack()
        rec, reply = self.sell("/sell")
        self.assertIn("ready ga unna pack ledu", reply["text"])
        self.assertIn("/ready", reply["text"])
        self.assertEqual(rec.texts, [])

    def test_list_without_send_text_returns_text(self):
        self.make_pack()
        rec = self.recorder()
        rec.ctx.pop("send_text")
        reply = tg_sell.handle("/sell", rec.ctx)
        self.assertIn("/sell 1", reply["text"])

    def test_list_keyboard_rows_of_three(self):
        for number in range(1, 6):
            self.make_pack(f"{number}-pack-{number}", title=f"Pack {number}")
        rec, _reply = self.sell("/sell")
        self.assertEqual(rec.keyboards[0], [["/sell 1", "/sell 2", "/sell 3"], ["/sell 4", "/sell 5"]])

    def test_list_finds_packs_in_upload_folder(self):
        self.make_pack("4-upload-pack", upload=True, title="Upload Pack")
        rec, _reply = self.sell("/sell")
        self.assertIn("/sell 4 — Upload Pack", rec.texts[0])


class NotYetTests(Base):
    def setUp(self):
        super().setUp()
        self.truth = {"verdict": "needs fix", "score": 40, "problems": ["Missing promised page"]}
        self.make_pack()

    def test_not_yet_lists_fixes_and_stops(self):
        rec, reply = self.sell()
        self.assertIn("🔴 Pack #1 — NOT YET", reply["text"])
        self.assertIn("Truth check", reply["text"])
        self.assertIn("1. ", reply["text"])
        self.assertEqual(rec.texts, [])
        self.assertEqual(rec.documents, [])
        self.assertEqual(rec.photos, [])

    def test_not_yet_has_no_gumroad_fields_or_checklist(self):
        _rec, reply = self.sell()
        self.assertNotIn("GUMROAD FIELDS", reply["text"])
        self.assertNotIn("CHECKLIST", reply["text"])
        self.assertIn("Gumroad lo pettakandi", reply["text"])

    def test_not_yet_points_to_next_step(self):
        _rec, reply = self.sell()
        self.assertIn("/sell 1", reply["text"])
        self.assertIn("/ready 1", reply["text"])

    def test_money_in_listing_is_not_yet(self):
        self.truth = {"verdict": "ready", "score": 100, "problems": []}
        self.make_pack("2-money", description=DESCRIPTION + " Only costs $10 on sale.")
        rec, reply = self.sell("/sell 2")
        self.assertIn("NOT YET", reply["text"])
        self.assertNotIn("$10", reply["text"])
        self.assertEqual(rec.documents, [])


class SellFlowTests(Base):
    def test_sellable_flow_sends_all_four_parts_in_order(self):
        self.make_pack()
        rec, reply = self.sell()
        self.assertEqual(reply, {"handled": True})
        self.assertEqual(rec.order, ["text", "text", "document", "document", "photo", "photo", "photo",
                                     "text", "text"])
        self.assertIn("🟢 Pack #1 — SELLABLE", rec.texts[0])
        self.assertIn("1/4 GUMROAD FIELDS", rec.texts[1])
        self.assertIn("4/4 GUMROAD CHECKLIST", rec.texts[2])
        self.assertIn("2 product files, 3 images pampanu", rec.texts[3])

    def test_verdict_is_on_top(self):
        self.make_pack()
        rec, _reply = self.sell()
        self.assertTrue(rec.texts[0].startswith("🟢 Pack #1 — SELLABLE (Score "))
        self.assertIn("Title: Focus Planner Printable Pack", rec.texts[0])

    def test_gumroad_fields_come_from_the_real_kit(self):
        pack = self.make_pack()
        rec, _reply = self.sell()
        fields = rec.texts[1]
        kit = store_kits.generate_kit_text("gumroad", pack, store_kits.parse_listing_file(pack / "listing.txt"))
        self.assertIn("=== GUMROAD LISTING KIT ===", fields)
        self.assertIn("Title: Focus Planner Printable Pack", fields)
        self.assertIn(DESCRIPTION, fields)
        tag_line = next(line for line in kit.splitlines() if line.startswith("focus planner, "))
        self.assertIn(tag_line, fields)
        self.assertEqual(len(tag_line.split(", ")), 10)
        self.assertIn("Category: Digital Products", fields)

    def test_price_is_owner_decides_and_no_amounts(self):
        self.make_pack()
        rec, _reply = self.sell()
        self.assertIn("Price: owner decides", rec.texts[1])
        self.assertIsNone(tg_sell.MONEY_RE.search(rec.all_text))
        for _path, caption in rec.documents + rec.photos:
            self.assertIsNone(tg_sell.MONEY_RE.search(caption))

    def test_product_files_are_documents(self):
        pack = self.make_pack()
        rec, _reply = self.sell()
        self.assertEqual([Path(path).name for path, _ in rec.documents], ["product.zip", "LICENSE.txt"])
        self.assertEqual(rec.documents[0][0], str(pack / "product.zip"))
        self.assertIn("2/4 Pack #1 file 1/2: product.zip", rec.documents[0][1])
        for path, _caption in rec.documents:
            self.assertTrue(Path(path).is_file())

    def test_owner_notes_are_not_sent_as_buyer_files(self):
        self.make_pack()
        rec, _reply = self.sell()
        names = [Path(path).name for path, _ in rec.documents]
        self.assertNotIn("checklist.txt", names)
        self.assertNotIn("listing.txt", names)
        self.assertNotIn("gumroad.txt", names)

    def test_cover_first_then_previews(self):
        self.make_pack(previews=3)
        rec, _reply = self.sell()
        self.assertEqual([Path(path).name for path, _ in rec.photos],
                         ["cover.png", "preview-1.png", "preview-2.png", "preview-3.png"])
        self.assertIn("Cover image", rec.photos[0][1])
        self.assertIn("Preview 1", rec.photos[1][1])

    def test_previews_in_natural_order(self):
        self.make_pack(previews=11)
        rec, _reply = self.sell()
        names = [Path(path).name for path, _ in rec.photos]
        self.assertEqual(len(names), tg_sell.MAX_IMAGES)
        self.assertEqual(names[:3], ["cover.png", "preview-1.png", "preview-2.png"])
        self.assertEqual(names[-1], "preview-9.png")

    def test_checklist_is_tenglish_click_by_click_with_real_names(self):
        self.make_pack()
        rec, _reply = self.sell()
        checklist = rec.texts[2]
        self.assertIn("gumroad.com", checklist)
        self.assertIn("'New product'", checklist)
        self.assertIn("product.zip, LICENSE.txt", checklist)
        self.assertIn("cover.png", checklist)
        self.assertIn("preview-1.png, preview-2.png", checklist)
        self.assertIn("click cheyandi", checklist)
        self.assertIn("Robot emi publish cheyadu", checklist)
        self.assertGreaterEqual(checklist.count("[ ] "), 12)

    def test_checklist_steps_are_numbered_in_order(self):
        text = tg_sell.checklist_text(4, [Path("product.zip")], [Path("cover.png")])
        numbers = [int(line.split(".")[0].replace("[ ] ", "")) for line in text.splitlines() if line.startswith("[ ] ")]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
        self.assertIn("Pack #4", text)
        self.assertNotIn("previews kuda", text)

    def test_almost_pack_still_gets_everything_with_fixes_on_top(self):
        self.make_pack(marketing=False)
        rec, reply = self.sell()
        self.assertEqual(reply, {"handled": True})
        # Missing marketing files are only a suggestion: the pack is SELLABLE (Kiro review #8),
        # and the suggestions are still shown on top.
        self.assertIn("🟢 Pack #1 — SELLABLE", rec.texts[0])
        self.assertIn("Optional ga ivi kuda chudandi", rec.texts[0])
        self.assertIn("- Missing", rec.texts[0])
        self.assertIn("1/4 GUMROAD FIELDS", rec.texts[1])
        self.assertEqual(len(rec.documents), 2)
        self.assertEqual(len(rec.photos), 3)

    def test_pack_in_upload_folder(self):
        self.make_pack("5-upload-pack", upload=True, title="Upload Pack")
        rec, reply = self.sell("/sell 5")
        self.assertEqual(reply, {"handled": True})
        self.assertIn("Title: Upload Pack", rec.texts[1])
        self.assertIn(os.path.join("upload", "5-upload-pack", "product.zip"), rec.documents[0][0])

    def test_tenglish_phrases_run_the_same_flow(self):
        self.make_pack("3-focus-planner")
        for text in ("pack 3 sell chey", "sell 3", "pack 3 gumroad lo pettu", "3 sell cheyali"):
            rec, reply = self.sell(text)
            self.assertEqual(reply, {"handled": True}, text)
            self.assertEqual(len(rec.documents), 2, text)

    def test_last_pack_is_remembered(self):
        self.make_pack()
        rec, _reply = self.sell()
        self.assertEqual(rec.state[tg_sell.STATE_KEY_LAST_SELL_PACK], 1)

    def test_pack_folder_is_not_changed(self):
        pack = self.make_pack()
        before = sorted((p.relative_to(pack).as_posix(), p.read_bytes()) for p in pack.rglob("*") if p.is_file())
        self.sell()
        after = sorted((p.relative_to(pack).as_posix(), p.read_bytes()) for p in pack.rglob("*") if p.is_file())
        self.assertEqual(before, after)

    def test_without_send_text_everything_text_comes_back(self):
        self.make_pack()
        rec = self.recorder()
        rec.ctx.pop("send_text")
        reply = tg_sell.handle("/sell 1", rec.ctx)
        self.assertIn("SELLABLE", reply["text"])
        self.assertIn("1/4 GUMROAD FIELDS", reply["text"])
        self.assertIn("4/4 GUMROAD CHECKLIST", reply["text"])

    def test_private_marketing_folder_from_marketing_auto_is_used(self):
        pack = self.make_pack(marketing=False)
        private = self.products / "marketing" / pack.name
        for folder in ("carousel", "pins", "reel", "covers"):
            (private / folder).mkdir(parents=True)
            (private / folder / "a.png").write_bytes(PNG)
        (private / "blog.md").write_text("b", encoding="utf-8")
        (private / "LAUNCH.md").write_text("l", encoding="utf-8")
        rec = self.recorder()
        rec.ctx.pop("marketing_dir")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CHILLI_MARKETING_DIR", None)
            self.assertEqual(tg_sell.marketing_base(pack, rec.ctx), self.products / "marketing")
            tg_sell.handle("/sell 1", rec.ctx)
        self.assertIn("SELLABLE", rec.texts[0])


class ProblemTests(Base):
    def test_big_file_gets_a_note_not_an_upload(self):
        self.make_pack()
        with mock.patch.object(tg_sell, "MAX_FILE_BYTES", 50):
            rec, _reply = self.sell()
        self.assertEqual([Path(path).name for path, _ in rec.documents], ["LICENSE.txt"])
        self.assertIn("product.zip: chala pedda file", rec.texts[-1])
        self.assertIn("1 product files", rec.texts[-1])

    def test_failed_document_send_is_reported(self):
        self.make_pack()
        rec, _reply = self.sell(document_ok=False)
        self.assertIn("0 product files", rec.texts[-1])
        self.assertIn("product.zip: Telegram ki pampadam fail ayyindi", rec.texts[-1])

    def test_photo_failure_falls_back_to_document(self):
        self.make_pack(previews=0)
        rec, _reply = self.sell(photo_ok=False)
        self.assertEqual(len(rec.photos), 1)
        self.assertEqual([Path(path).name for path, _ in rec.documents], ["product.zip", "LICENSE.txt", "cover.png"])
        self.assertIn("1 images pampanu", rec.texts[-1])

    def test_missing_cover_is_said_plainly(self):
        self.make_pack(cover=False, previews=0)
        real = sell_ready.evaluate_pack
        with mock.patch.object(sell_ready, "evaluate_pack",
                               side_effect=lambda p, b=None: dict(real(p, b), status=sell_ready.STATUS_ALMOST)):
            rec, _reply = self.sell()
        self.assertEqual(rec.photos, [])
        self.assertIn("Cover image ledu", rec.texts[-1])

    def test_symlinked_file_is_never_sent(self):
        pack = self.make_pack()
        secret = self.root / "secret.pdf"
        secret.write_bytes(b"private")
        try:
            (pack / "bonus.pdf").symlink_to(secret)
        except OSError:
            self.skipTest("symlinks unavailable")
        rec, _reply = self.sell()
        self.assertNotIn("bonus.pdf", [Path(path).name for path, _ in rec.documents])

    def test_extra_pdf_in_pack_is_sent(self):
        pack = self.make_pack()
        (pack / "bonus-guide.pdf").write_bytes(b"%PDF")
        rec, _reply = self.sell()
        self.assertEqual([Path(path).name for path, _ in rec.documents],
                         ["product.zip", "LICENSE.txt", "bonus-guide.pdf"])

    def test_fields_failure_is_a_clear_message(self):
        self.make_pack()
        with mock.patch.object(store_kits, "generate_kit_text", side_effect=ValueError("bad listing")):
            rec, reply = self.sell()
        self.assertIn("Gumroad fields tayaru cheyalekapoya", reply["text"])
        self.assertEqual(rec.documents, [])

    def test_unexpected_error_never_raises(self):
        self.make_pack()
        with mock.patch.object(sell_ready, "evaluate_pack", side_effect=RuntimeError("boom")):
            _rec, reply = self.sell()
        self.assertIn("/sell lo problem vachindi (RuntimeError)", reply["text"])

    def test_long_description_is_split_into_safe_messages(self):
        self.make_pack(description=("Clear planner pages for calm days. " * 220).strip())
        rec, reply = self.sell()
        self.assertEqual(reply, {"handled": True})
        self.assertGreater(len(rec.texts), 4)
        self.assertTrue(all(len(text) <= tg_sell.MAX_TEXT for text in rec.texts))


class HelperTests(unittest.TestCase):
    def test_chunks_short_text(self):
        self.assertEqual(tg_sell.chunks("hello\nworld"), ["hello\nworld"])

    def test_chunks_split_at_lines(self):
        text = "\n".join(f"line {n}" for n in range(100))
        parts = tg_sell.chunks(text, limit=60)
        self.assertTrue(all(len(part) <= 60 for part in parts))
        self.assertEqual("\n".join(parts), text)

    def test_chunks_very_long_line(self):
        parts = tg_sell.chunks("x" * 250, limit=100)
        self.assertEqual([len(part) for part in parts], [100, 100, 50])

    def test_chunks_empty(self):
        self.assertEqual(tg_sell.chunks("  \n "), [])

    def test_clean_removes_money(self):
        self.assertEqual(tg_sell.clean("only $10 or 5 USD or Rs. 99"), "only [amount] or [amount] or [amount]")
        self.assertEqual(tg_sell.clean("30 days, 10 tags"), "30 days, 10 tags")

    def test_products_dir_priority(self):
        with mock.patch.dict(os.environ, {"CHILLI_PRODUCTS_DIR": "/tmp/from-env"}):
            self.assertEqual(tg_sell.products_dir({"products_dir": "/tmp/from-ctx"}), Path("/tmp/from-ctx").resolve())
            self.assertEqual(tg_sell.products_dir({}), Path("/tmp/from-env").resolve())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CHILLI_PRODUCTS_DIR", None)
            self.assertEqual(tg_sell.products_dir(None),
                             Path(tg_sell.__file__).resolve().parent / "exported_content" / "products")

    def test_verdict_text_for_each_status(self):
        base = {"title": "T", "score": 70, "fixes": ["Fix A", "Fix B"]}
        self.assertIn("🔴 Pack #2 — NOT YET", tg_sell.verdict_text(2, dict(base, status=sell_ready.STATUS_NOT_YET)))
        self.assertIn("1. Fix A", tg_sell.verdict_text(2, dict(base, status=sell_ready.STATUS_NOT_YET)))
        self.assertIn("- Fix A", tg_sell.verdict_text(2, dict(base, status=sell_ready.STATUS_ALMOST)))
        self.assertIn("Anni checks pass", tg_sell.verdict_text(2, dict(base, status=sell_ready.STATUS_SELLABLE, fixes=[])))

    def test_list_text_empty(self):
        text, ids = tg_sell.list_text([])
        self.assertEqual(ids, [])
        self.assertIn("inka levu", text)


class ReachabilityTests(Base):
    """The plugin must be reachable through telegram_inbox.call_plugins, with fake send functions."""

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
            mock.patch.object(telegram_inbox, "send_document",
                              side_effect=lambda token, chat, path, caption="":
                              self.documents.append(path) or True),
            mock.patch.object(telegram_inbox, "send_photo",
                              side_effect=lambda token, chat, path, caption="":
                              self.photos.append(path) or True),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, text):
        return self.inbox.call_plugins(text, "test-token", SAMPLE_CHAT_ID, db_path=self.db)

    def test_plugin_is_discovered(self):
        self.assertIn("tg_sell", [plugin.__name__ for plugin in self.inbox.discover_plugins()])

    def test_sell_n_through_call_plugins(self):
        self.make_pack()
        handled, reply = self.call("/sell 1")
        self.assertTrue(handled)
        self.assertEqual(reply, {"handled": True})
        self.assertIn("🟢 Pack #1 — SELLABLE", self.texts[0])
        self.assertIn("1/4 GUMROAD FIELDS", self.texts[1])
        self.assertEqual([Path(path).name for path in self.documents], ["product.zip", "LICENSE.txt"])
        self.assertEqual([Path(path).name for path in self.photos], ["cover.png", "preview-1.png", "preview-2.png"])
        self.assertIn("4/4 GUMROAD CHECKLIST", self.texts[2])

    def test_sell_alone_through_call_plugins(self):
        self.make_pack()
        handled, reply = self.call("/sell")
        self.assertTrue(handled)
        self.assertEqual(reply, {"handled": True})
        self.assertIn("/sell 1 — Focus Planner Printable Pack", self.texts[0])

    def test_tenglish_sell_through_call_plugins(self):
        self.make_pack("3-focus-planner")
        handled, _reply = self.call("pack 3 sell chey")
        self.assertTrue(handled)
        self.assertEqual(len(self.documents), 2)

    def test_not_yet_through_call_plugins(self):
        self.truth = {"verdict": "needs fix", "score": 30, "problems": ["x"]}
        self.make_pack()
        handled, reply = self.call("/sell 1")
        self.assertTrue(handled)
        self.assertIn("NOT YET", reply["text"])
        self.assertEqual(self.documents, [])

    def test_state_is_saved_in_the_test_database(self):
        self.make_pack()
        self.call("/sell 1")
        self.assertEqual(int(self.inbox.get_state(tg_sell.STATE_KEY_LAST_SELL_PACK, db_path=self.db)), 1)

    def test_other_commands_still_go_to_their_own_plugins(self):
        self.make_pack()
        handled, reply = self.call("/ready")
        self.assertTrue(handled)
        self.assertNotIn("Gumroad", reply.get("text", ""))     # answered by tg_ready, not by tg_sell
        self.assertEqual(self.texts, [])
        self.assertEqual(self.documents, [])


if __name__ == "__main__":
    unittest.main()
