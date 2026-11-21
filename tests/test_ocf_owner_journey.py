"""Owner journey end to end: /list, /approve, build, /sell, /listed, /today."""
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["CHILLI_TEST"] = "1"
os.environ["DB_KEY"] = ""

import guardrails
import marketing_auto
import mockups
import product_pipeline
import notify
import post_planner
import product_builder
import product_council
import product_pipeline
import rest_window
import sell_ready
import store_kits
import telegram_inbox
import truth_check
from test_launch_pack import fake_spec

try:
    from test_occ_sell_flow_e2e import build_pack as build_format_pack
    HAS_IMAGES = True
except Exception:
    build_format_pack = None
    HAS_IMAGES = False

FALLBACK = "ardam kaaledu"


class Recorder:
    def __init__(self):
        self.texts = []
        self.files = []
        self.photos = []
        self.edits = []
        self.acks = []
        self.calls = []
        self.updates = []
        self.update_id = 5000

    def text(self, token, chat_id, text, buttons=None, keyboard=None):
        self.texts.append(text)
        return True

    def document(self, token, chat_id, path, caption=""):
        self.files.append((str(path), caption))
        return True

    def photo(self, token, chat_id, path, caption=""):
        self.photos.append((str(path), caption))
        return True

    def media_group(self, token, chat_id, paths, caption=""):
        self.files.extend((str(path), caption) for path in paths)
        return True

    def edit(self, token, chat_id, message_id, text, buttons=None, keyboard=None):
        self.edits.append(text)
        return True

    def ack(self, token, update):
        self.acks.append(update)

    def call(self, method, data):
        self.calls.append((method, data))
        return {"ok": True}

    def fetch(self, token, offset=None, timeout=0):
        return list(self.updates)

    def add(self, update):
        update["update_id"] = self.update_id
        self.update_id += 1
        self.updates = [update]
        self.texts = []
        self.files = []
        self.photos = []
        self.acks = []
        self.calls = []

    def message(self, text):
        self.add({"message": {"chat": {"id": 7}, "from": {"id": 7}, "text": text}})

    def press(self, data):
        self.add({"callback_query": {"id": "cb", "from": {"id": 7},
                                     "message": {"chat": {"id": 7}, "message_id": 9},
                                     "data": data}})

    @property
    def reply(self):
        return "\n".join(self.texts)

    @property
    def all_text(self):
        return self.reply


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, old)
        self.root = Path(self.tmp.name)
        self.products = self.root / "exported_content" / "products"
        self.marketing = self.root / "exported_content" / "marketing"
        self.products.mkdir(parents=True)
        self.marketing.mkdir(parents=True)
        self.db = str(self.root / "agent_data.db")
        self.rec = Recorder()

        env = {"CHILLI_TEST": "1", "DB_KEY": "",
               "CHILLI_PRODUCTS_DIR": str(self.products),
               "CHILLI_MARKETING_DIR": str(self.marketing)}
        patches = [
            mock.patch.dict(os.environ, env),
            mock.patch.object(product_builder, "PRODUCTS_DIR", str(self.products)),
            mock.patch.object(post_planner, "PLAN_PATH", str(self.root / "exported_content" / "post_plan.json")),
            mock.patch.object(guardrails, "is_killed", return_value=False),
            mock.patch.object(rest_window, "in_rest_window", return_value=False),
            mock.patch.object(notify, "send", return_value=False),
            mock.patch.object(telegram_inbox, "fetch_updates", side_effect=self.rec.fetch),
            mock.patch.object(telegram_inbox, "acknowledge_update", side_effect=self.rec.ack),
            mock.patch.object(telegram_inbox, "_call", side_effect=self.rec.call),
            mock.patch.object(telegram_inbox, "send_text", side_effect=self.rec.text),
            mock.patch.object(telegram_inbox, "edit_text", side_effect=self.rec.edit),
            mock.patch.object(telegram_inbox, "send_document", side_effect=self.rec.document),
            mock.patch.object(telegram_inbox, "send_photo", side_effect=self.rec.photo),
            mock.patch.object(telegram_inbox, "send_media_group", side_effect=self.rec.media_group),
            mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")),
            mock.patch.object(socket.socket, "connect_ex", side_effect=AssertionError("network forbidden")),
            mock.patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def say(self, text):
        self.rec.message(text)
        handled = telegram_inbox.process_updates("token", 7, self.db)
        self.assertEqual(handled, 1, f"{text!r} was not handled")
        self.assertNotIn(FALLBACK, self.rec.all_text)
        return self.rec.reply

    def press(self, data):
        self.rec.press(data)
        handled = telegram_inbox.process_updates("token", 7, self.db)
        self.assertNotIn(FALLBACK, self.rec.all_text)
        return handled, self.rec.reply

    def add_draft(self, name="Daily Focus Planner"):
        payload = ("PRODUCT NAME: " + name + "\nBUYER: adults\n"
                   "WHAT'S INSIDE: printable daily plan.\nHOW TO USE: Print.\n"
                   "LISTING DESCRIPTION: A simple daily focus planner for personal use.\n"
                   "SEARCH TAGS: planner, printable, focus, daily, notes\n"
                   "PRICE: USD 8\nSELLING PLATFORM: Gumroad\n")
        aid = guardrails.request_approval("public_draft", f"Draft: [ADHD Planners Agent] {name}",
                                          payload, None, self.db)
        product_council.log_debate(aid, 1, "council_summary", "PASS", "x",
                                   conviction=8, db_path=self.db)
        guardrails.set_review(aid, "score 90/100 | Checker: 90/100 | CHECKER: PASS", self.db)
        return aid

    def approve(self, aid):
        text = self.say(f"/approve {aid}")
        self.assertIn("Approved", text)
        self.assertIn(f"#{aid}", text)
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "approved")

    def build(self, aid):
        result = product_pipeline.run_pending(self.db, str(self.products), fake_spec, max_builds=1)
        self.assertEqual(len(result), 1, result)
        self.assertIn(result[0]["status"], {"prepared", "ready"}, result)
        folder = result[0]["folder"]
        report = product_builder.truth_check_folder(aid, folder, self.db, str(self.products),
                                                     notify_fn=lambda text: False)
        self.assertEqual(report.get("verdict"), "ready", report)
        self._complete_upload_pack(self.products / folder, folder)
        self.run_marketing_step(aid, folder)
        return folder

    def run_marketing_step(self, aid, folder):
        """The robot's marketing step after a pack is READY: Pins / carousel for /today.

        Encryption (DB_KEY) cannot run in tests, so the READY receipt is written the same way
        mark_encrypted writes it, then marketing_auto.run_ready makes the files exactly as the cycle does.
        """
        digest = product_pipeline._inventory(str(self.products / folder))[1]
        conn = product_pipeline.connect(self.db)
        conn.execute("INSERT INTO product_deliveries (approval_id, status, reason, folder, files, digest, "
                     "notified, lease_until) VALUES (?, 'ready', '', ?, '[]', ?, 0, 0) "
                     "ON CONFLICT(approval_id) DO UPDATE SET status='ready', reason='', folder=excluded.folder, "
                     "digest=excluded.digest, lease_until=0", (aid, folder, digest))
        conn.commit()
        conn.close()
        marketing_auto.run_ready(self.db, str(self.products))
        self.assertTrue((marketing_auto.marketing_root(self.products) / folder).is_dir(),
                        "marketing step made no files for the READY pack")

    def _complete_upload_pack(self, source, folder):
        pack = self.products / "upload" / folder
        self.assertTrue(pack.is_dir(), pack)
        for name in ("listing.md", "README.md", "LICENSE.txt", "cover.png"):
            src = source / name
            if src.exists() and not (pack / name).exists():
                shutil.copy2(src, pack / name)
        for src in source.iterdir():
            if src.is_file() and (src.suffix.lower() in {".pdf", ".xlsx", ".docx"}
                                  or src.name.startswith("preview-")):
                shutil.copy2(src, pack / src.name)
        store_kits.build(pack)
        checked = truth_check.check(str(pack))
        self.assertEqual(checked["verdict"], "ready", checked)


@unittest.skipUnless(mockups.available(), "needs Pillow and poppler")
class OwnerJourneyTests(Harness):
    def test_list_approve_build_sell_listed_today(self):
        aid = self.add_draft()

        listed = self.say("/list")
        self.assertIn(f"#{aid}", listed)
        self.assertIn("/approve", listed)
        self.assertIn("draft", listed.lower())

        self.approve(aid)
        folder = self.build(aid)

        sell = self.say(f"/sell {aid}")
        self.assertIn(f"#{aid}", sell)
        self.assertIn("SELLABLE", sell)
        self.assertIn("GUMROAD FIELDS", sell)
        self.assertIn("GUMROAD CHECKLIST", sell)
        self.assertTrue(self.rec.files or self.rec.photos, "sell sent no product files or images")

        listed = self.say(f"/listed {aid} https://example.gumroad.com/l/x")
        self.assertIn(f"#{aid}", listed)
        self.assertIn("https://example.gumroad.com/l/x", listed)
        self.assertIn("/status", listed)

        today = self.say("/today")
        self.assertIn("Today post", today)
        self.assertIn(folder, today)
        self.assertIn("posted 1", today)

    @unittest.expectedFailure
    def test_approve_button_accepts_approve_id(self):
        aid = self.add_draft()
        self.press(f"approve:{aid}")
        status = guardrails.get_approval(aid, self.db)["status"]
        self.assertEqual(status, "approved",
                         "telegram_inbox.handle_callback only understands a:<id>; "
                         "the owner button data approve:<id> is ignored (Claude Code is fixing buttons)")


@unittest.skipUnless(HAS_IMAGES and mockups.available(), "needs Pillow, poppler and the OCC pack builder")
class FormatJourneyTests(Harness):
    def build_format(self, kind):
        result = build_format_pack(kind, self.root / "exported_content")
        aid = result["approval_id"]
        folder = result["pack"].name
        title = folder
        conn = guardrails.connect(self.db)
        conn.execute("INSERT OR REPLACE INTO approvals "
                     "(id, ts, kind, title, payload, status, review) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (aid, time.strftime("%Y-%m-%dT%H:%M:%S"), "public_draft",
                      f"Draft: {title}", "PRODUCT NAME: " + title, "pending", ""))
        conn.commit()
        conn.close()
        listed = self.say("/list")
        self.assertIn(f"#{aid}", listed)
        approved = self.say(f"/approve {aid}")
        self.assertIn(f"#{aid}", approved)
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "approved")
        self.run_marketing_step(aid, folder)
        return aid, folder

    def test_planner_sell_listed_today(self):
        aid, folder = self.build_format("planner")
        sell = self.say(f"/sell {aid}")
        self.assertIn(f"#{aid}", sell)
        self.assertIn("SELLABLE", sell)
        self.assertIn("GUMROAD FIELDS", sell)
        self.assertTrue(self.rec.files or self.rec.photos, "sell sent no product files or images")
        listed = self.say(f"/listed {aid} https://example.gumroad.com/l/planner")
        self.assertIn("https://example.gumroad.com/l/planner", listed)
        self.assertIn("/status", listed)
        today = self.say("/today")
        self.assertIn("Today post", today)
        self.assertIn(folder, today)
        self.assertIn("posted 1", today)

    def _assert_sell_explains_fix(self, kind):
        aid, _folder = self.build_format(kind)
        sell = self.say(f"/sell {aid}")
        self.assertIn(f"#{aid}", sell)
        self.assertIn("NOT YET", sell)
        self.assertTrue("A4" in sell or "Letter" in sell or "truth" in sell.lower(), sell)

    def _assert_listed_and_today(self, kind):
        aid, folder = self.build_format(kind)
        listed = self.say(f"/listed {aid} https://example.gumroad.com/l/{kind}")
        self.assertIn(f"#{aid}", listed)
        self.assertIn(f"https://example.gumroad.com/l/{kind}", listed)
        self.assertIn("/status", listed)
        today = self.say("/today")
        self.assertIn("Today post", today)
        self.assertIn(folder, today)
        self.assertIn("posted 1", today)

    def test_habit_tracker_sell_reaches_gumroad_fields(self):
        # Claude Code CCC-1 made the habit tracker sellable, so /sell now shows the Gumroad fields.
        aid, _folder = self.build_format("habit_tracker")
        sell = self.say(f"/sell {aid}")
        self.assertIn(f"#{aid}", sell)
        self.assertNotIn("NOT YET", sell)
        self.assertIn("GUMROAD FIELDS", sell)

    def test_habit_tracker_listed_and_today(self):
        self._assert_listed_and_today("habit_tracker")

    def test_wall_art_sell_reaches_gumroad_fields(self):
        # Claude Code CCC-7 made the wall art sellable, so /sell now shows the Gumroad fields.
        aid, _folder = self.build_format("wall_art")
        sell = self.say(f"/sell {aid}")
        self.assertIn(f"#{aid}", sell)
        self.assertNotIn("NOT YET", sell)
        self.assertIn("GUMROAD FIELDS", sell)

    def test_wall_art_listed_and_today(self):
        self._assert_listed_and_today("wall_art")


if __name__ == "__main__":
    unittest.main()
