"""Tests for telegram_inbox /marketing command (Job O12).

Covers:
- "/marketing N" parses as a command and routes to marketing_text
- Tenglish "pack N marketing" and "marketing N chupinchu" phrases
- Message lists: carousel slides, pins count + 5 A/B titles, reel frames,
  blog word count, LAUNCH.md yes/no, seo_meta yes/no
- One line "Mac lo folder: Marketing/<pack>" with no absolute paths
- Owner chat only; unknown pack gives friendly text; <= 3500 chars
- No external libraries needed (stdlib only, so CI-safe)
"""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guardrails  # noqa: E402
import telegram_inbox as ti  # noqa: E402

OWNER = "111222333"

PNG_BYTES = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
             b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
             b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

PINS_JSON_TITLES = ["Title One Guide", "Title Two Pack", "Title Three Sheets",
                    "Title Four Planner", "Title Five Bundle", "Title Six Extra"]


class MarketingBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.marketing = self.root / "exported_content" / "marketing"
        self.marketing.mkdir(parents=True, exist_ok=True)

    def make_pack(self, n=5, name=None):
        pack_dir = self.marketing / (name or str(n))
        pack_dir.mkdir(parents=True, exist_ok=True)
        return pack_dir

    def make_full_pack(self, n=5):
        """Pack folder with every marketing asset present."""
        pdir = self.make_pack(n)
        car = pdir / "carousel"
        car.mkdir()
        for i in range(1, 4):
            (car / f"slide-{i}.png").write_bytes(PNG_BYTES)
        pins = pdir / "pins"
        pins.mkdir()
        import json
        (pins / "pins.json").write_text(
            json.dumps([{"title": t} for t in PINS_JSON_TITLES]), encoding="utf-8")
        reel = pdir / "reel"
        reel.mkdir()
        for i in range(1, 3):
            (reel / f"frame-{i}.png").write_bytes(PNG_BYTES)
        (pdir / "blog.md").write_text("one two\nthree  four words\nfive", encoding="utf-8")
        (pdir / "LAUNCH.md").write_text("# Launch", encoding="utf-8")
        (pdir / "seo_meta.json").write_text("{}", encoding="utf-8")
        return pdir

    def use_cwd(self):
        """Default marketing_dir resolves against the repo cwd; run from the temp root."""
        cwd = os.getcwd()
        os.chdir(str(self.root))
        self.addCleanup(os.chdir, cwd)


class ParseTests(MarketingBase):
    def test_parse_command_slash_marketing(self):
        self.assertEqual(ti.parse_command("/marketing 5"), ("marketing", [5]))
        self.assertEqual(ti.parse_command("/MARKETING 12"), ("marketing", [12]))

    def test_parse_marketing_phrase_variants(self):
        self.assertEqual(ti.parse_marketing_phrase("/marketing 5"), 5)
        self.assertEqual(ti.parse_marketing_phrase("pack 7 marketing"), 7)
        self.assertEqual(ti.parse_marketing_phrase("marketing 3 chupinchu"), 3)
        self.assertEqual(ti.parse_marketing_phrase("PACK 2 Marketing."), 2)

    def test_parse_marketing_phrase_non_matches(self):
        self.assertIsNone(ti.parse_marketing_phrase("hello"))
        self.assertIsNone(ti.parse_marketing_phrase("pack 5 details"))
        self.assertIsNone(ti.parse_marketing_phrase("marketing tips chupinchu"))
        self.assertIsNone(ti.parse_marketing_phrase(None))

    def test_handle_command_without_number_prompts(self):
        res = ti.handle_command("marketing", [], db_path=None)
        self.assertIn("Send /marketing <pack number>", res)

    def test_missing_pack_friendly_text(self):
        res = ti.marketing_text(999, marketing_dir=str(self.marketing))
        self.assertIn("#999", res)
        self.assertIn("inka ledu", res)
        self.assertNotIn(str(self.tmp.name), res)


class MarketingTextTests(MarketingBase):
    def test_full_pack_lists_everything(self):
        pdir = self.make_full_pack(5)
        res = ti.marketing_text(5, marketing_dir=str(self.marketing))
        self.assertIn("Marketing pack #5 (5):", res)
        self.assertIn("Carousel: 3 slides", res)
        self.assertIn("Pins:", res)
        self.assertIn("Reel frames: 2", res)
        self.assertIn("Blog: 6 words", res)
        self.assertIn("LAUNCH.md: yes", res)
        self.assertIn("seo_meta: yes", res)
        self.assertIn(f"Mac lo folder: Marketing/{pdir.name}", res)
        for title in PINS_JSON_TITLES[:5]:
            self.assertIn(title, res)
        self.assertNotIn("Title Six Extra", res)
        self.assertLessEqual(len(res), 3500)

    def test_pins_titles_fallback_from_ab_titles_txt(self):
        pdir = self.make_pack(8)
        ab = pdir / "pins" / "ab_titles.txt"
        ab.parent.mkdir()
        ab.write_text("# comment line\n1. First Title\n2. Second Title\n3. Third Title\n",
                      encoding="utf-8")
        res = ti.marketing_text(8, marketing_dir=str(self.marketing))
        self.assertIn("Pins:", res)
        self.assertIn("1. First Title", res)
        self.assertIn("2. Second Title", res)
        self.assertIn("3. Third Title", res)
        self.assertNotIn("comment line", res)

    def test_prefix_named_pack_found(self):
        for name in ("not-fake", "5-planner"):
            pdir = self.make_pack(name=name)
        (pdir / "blog.md").write_text("a b c", encoding="utf-8")
        res = ti.marketing_text(5, marketing_dir=str(self.marketing))
        self.assertIn("Marketing pack #5 (5-planner):", res)
        self.assertIn("Blog: 3 words", res)
        self.assertNotIn("not-fake", res)

    def test_empty_pack_minimal_fields(self):
        self.make_pack(6)
        res = ti.marketing_text(6, marketing_dir=str(self.marketing))
        self.assertIn("Carousel: 0 slides", res)
        self.assertIn("Pins: 0", res)
        self.assertIn("Reel frames: 0", res)
        self.assertIn("Blog: 0 words", res)
        self.assertIn("LAUNCH.md: no", res)
        self.assertIn("seo_meta: no", res)
        self.assertIn("Mac lo folder: Marketing/6", res)

    def test_no_absolute_paths_in_output(self):
        self.make_full_pack(5)
        res = ti.marketing_text(5, marketing_dir=str(self.marketing))
        self.assertNotIn(str(self.tmp.name), res)
        self.assertNotIn("exported_content/marketing", res)

    def test_scrub_applied_to_titles(self):
        pdir = self.make_pack(4)
        pins = pdir / "pins"
        pins.mkdir()
        import json
        (pins / "pins.json").write_text(json.dumps(
            [{"title": "secret owner@example.com title"}]), encoding="utf-8")
        res = ti.marketing_text(4, marketing_dir=str(self.marketing))
        self.assertNotIn("owner@example.com", res)

    def test_no_money_in_output(self):
        pdir = self.make_full_pack(5)
        import json
        (pdir / "pins" / "pins.json").write_text(json.dumps(
            [{"title": f"Great title {i}"} for i in range(1, 6)]), encoding="utf-8")
        res = ti.marketing_text(5, marketing_dir=str(self.marketing))
        self.assertNotIn("$", res[1:] if res.startswith("#") else res)


class HandleCommandTests(MarketingBase):
    def test_handle_command_routes_to_marketing_text(self):
        self.make_full_pack(5)
        self.use_cwd()
        res = ti.handle_command("marketing", [5], db_path=None)
        self.assertIn("Marketing pack #5 (5):", res)
        self.assertIn("Carousel: 3 slides", res)

    def test_help_mentions_marketing(self):
        res = ti.handle_command("help", [], db_path=None)
        self.assertIn("/marketing", res)


class _FakeUpdatesBase(unittest.TestCase):
    """Like tests/test_telegram_inbox.py: Telegram API is fully stubbed.

    Expects MarketingBase.setUp to have run first (uses its temp root).
    """

    def setUp(self):
        self.db = os.path.join(self.tmp.name, "t.db")
        guardrails.connect(self.db).close()
        self.sent = []

        def fake_send(tok, chat, text, buttons=None):
            self.sent.append((chat, text))
            return True
        p1 = mock.patch.object(ti, "send_text", side_effect=fake_send)
        p1.start()
        self.addCleanup(p1.stop)
        p2 = mock.patch.object(ti, "_call", return_value={"ok": True, "result": []})
        p2.start()
        self.addCleanup(p2.stop)
        # media_brain imports llm_router, which needs a modern Python at import
        # time; stub it because these tests only use plain text messages.
        p3 = mock.patch.dict(sys.modules, {"media_brain": mock.Mock()})
        p3.start()
        self.addCleanup(p3.stop)
        self.use_cwd()

    @staticmethod
    def _upd(uid, text, chat=OWNER):
        return {"update_id": uid, "message": {"chat": {"id": int(chat)}, "text": text}}

    def run_updates(self, updates):
        stats = ti.new_stats()
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            with mock.patch.object(ti, "acknowledge_update", return_value=None):
                ti.process_updates("tok", OWNER, self.db, stats)


class TelegramIntegrationTests(MarketingBase, _FakeUpdatesBase):
    def setUp(self):
        MarketingBase.setUp(self)
        _FakeUpdatesBase.setUp(self)

    def test_tenglish_pack_marketing(self):
        self.make_full_pack(5)
        self.run_updates([self._upd(1, "pack 5 marketing")])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Carousel: 3 slides", self.sent[0][1])
        self.assertIn("Mac lo folder: Marketing/5", self.sent[0][1])

    def test_tenglish_marketing_chupinchu(self):
        self.make_full_pack(3)
        self.run_updates([self._upd(1, "marketing 3 chupinchu")])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Marketing pack #3 (3):", self.sent[0][1])

    def test_owner_chat_only(self):
        self.make_full_pack(5)
        self.run_updates([self._upd(1, "pack 5 marketing", chat=999888777)])
        self.assertEqual(self.sent, [])

    def test_show_pack_still_beats_no_match(self):
        """'pack 5 details' still routes to owner_commands show_pack, not marketing."""
        self.make_full_pack(5)
        self.run_updates([self._upd(1, "pack 5 details")])
        if self.sent:
            self.assertFalse(("Marketing pack #5" in self.sent[0][1]))


if __name__ == "__main__":
    unittest.main()
