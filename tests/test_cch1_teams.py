"""CCH-1: /teams report — real numbers per team, 0 when a team made nothing, never a made-up number.
Telegram checks go through telegram_inbox.call_plugins with fake send functions."""
import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import guardrails
import teams_report

NOW = datetime.datetime(2026, 11, 20, 9, 0, 0)
CHAT = int("1234" "5678")


def _iso(dt):
    return dt.isoformat(timespec="seconds")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.db = str(root / "agent_data.db")
        self.products = root / "products"
        self.plan = root / "post_plan.json"
        self.products.mkdir()
        guardrails.connect(self.db).close()
        for name, value in (("PRODUCTS_DIR", self.products), ("PLAN_PATH", self.plan)):
            patcher = mock.patch.object(teams_report, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- fixtures -----------------------------------------------------------------
    def sql(self, statement, args=()):
        conn = guardrails.connect(self.db)
        conn.execute(statement, args)
        conn.commit()
        conn.close()

    def ready_pack(self, aid, folder):
        import product_pipeline
        product_pipeline.connect(self.db).close()
        self.sql("INSERT INTO approvals (id, ts, kind, title, status, decided_ts) VALUES (?, ?, ?, ?, ?, ?)",
                 (aid, _iso(NOW - datetime.timedelta(days=2)), "public_draft", folder, "approved", _iso(NOW)))
        self.sql("INSERT INTO product_deliveries (approval_id, status, folder) VALUES (?, 'ready', ?)", (aid, folder))

    def pins(self, folder, n=5, ok=True, made=None, drop_file=False):
        out = self.products / "marketing" / folder / "pinterest"
        out.mkdir(parents=True, exist_ok=True)
        pins = []
        for i in range(1, n + 1):
            (out / f"pin-{i}.png").write_bytes(b"png")
            pins.append({"file": f"pin-{i}.png", "title": f"t{i}", "description": "d"})
        if drop_file:
            (out / "pin-1.png").unlink()
        (out / "pins.json").write_text(json.dumps({"made_at": _iso(made or NOW), "check": {"ok": ok},
                                                   "pins": pins}))

    def carousel(self, folder, slides=6, ok=True, made=None):
        out = self.products / "marketing" / folder / "carousel"
        out.mkdir(parents=True, exist_ok=True)
        rows = []
        for i in range(1, slides + 1):
            (out / f"slide-{i}.png").write_bytes(b"png")
            rows.append({"file": f"slide-{i}.png", "alt": "a"})
        (out / "carousel.json").write_text(json.dumps({"made_at": _iso(made or NOW), "check": {"ok": ok},
                                                       "slides": rows}))

    def write_plan(self, rows):
        self.plan.write_text(json.dumps(rows))

    def stats(self):
        return teams_report.team_stats(db_path=self.db, now=NOW)


class EmptyTests(Base):
    def test_no_output_shows_zero_and_no_percent(self):
        stats = self.stats()
        self.assertEqual(set(stats), set(teams_report.TEAMS))
        for team, s in stats.items():
            with self.subTest(team=team):
                self.assertIsNone(s["percent"])
                self.assertTrue(all(v == 0 for v in s["made"].values()), s["made"])
                self.assertTrue(s["next"])
        text = teams_report.report_text(stats)
        for label in ("Pinterest: -", "Instagram: -", "Marketing: -", "Product: -", "Research: -"):
            self.assertIn(label, text)
        self.assertIn("0 Pins made", text)
        self.assertNotRegex(text, r"[$₹€£]")


class CountTests(Base):
    def test_pins_and_carousels_only_when_ready(self):
        self.ready_pack(9001, "9001-focus")
        self.ready_pack(9002, "9002-budget")
        self.pins("9001-focus")
        self.carousel("9001-focus", slides=7)
        self.pins("9002-budget", ok=False)            # checker failed: not ready
        self.pins("9003-old", made=NOW - datetime.timedelta(days=9))   # ready, but older than 7 days
        self.carousel("9004-broken")
        (self.products / "marketing" / "9004-broken" / "carousel" / "slide-2.png").unlink()  # missing slide
        self.pins("9005-gone", drop_file=True)
        stats = self.stats()
        self.assertEqual(stats["pinterest"]["made"], {"pins": 5, "packs_with_pins": 2})
        self.assertEqual(stats["pinterest"]["percent"], 50)           # 1 of 2 READY packs
        self.assertIn("1 READY pack(s) ki inka ready Pins levu", stats["pinterest"]["next"])
        self.assertEqual(stats["instagram"]["made"], {"carousels": 1, "slides": 7, "packs_with_carousel": 1})
        self.assertEqual(stats["instagram"]["percent"], 50)

    def test_plan_counts_planned_posted_waiting(self):
        today = NOW.date()
        day = lambda n: (today + datetime.timedelta(days=n)).isoformat()
        self.write_plan([
            {"day": 1, "date": day(-3), "channel": "pinterest", "status": "done", "posted_at": _iso(NOW)},
            {"day": 1, "date": day(-3), "channel": "carousel", "status": "todo"},
            {"day": 2, "date": day(0), "channel": "pinterest", "status": "todo"},
            {"day": 2, "date": day(0), "channel": "carousel", "status": "done"},
            {"day": 3, "date": day(1), "channel": "pinterest", "status": "todo"},
            {"day": 3, "date": day(1), "channel": "carousel", "status": "todo"},
            {"day": 0, "date": day(-20), "channel": "pinterest", "status": "done"},   # too old to count
        ])
        stats = self.stats()
        self.assertEqual((stats["pinterest"]["planned"], stats["pinterest"]["posted"]), (2, 1))
        self.assertEqual(stats["pinterest"]["waiting"], {"to_post_today": 1, "overdue": 0})
        self.assertEqual(stats["instagram"]["waiting"], {"to_post_today": 0, "overdue": 1})
        m = stats["marketing"]
        self.assertEqual(m["made"]["posts_planned"], 3)
        self.assertEqual(m["made"]["posts_posted"], 2)
        self.assertEqual(m["percent"], 50)                 # 2 of 4 posts due this week are done
        self.assertIn("Ee roju 1 post(s) undi", m["next"])

    def test_product_and_research_from_db(self):
        self.sql("CREATE TABLE IF NOT EXISTS product_builds (approval_id INTEGER PRIMARY KEY, status TEXT, "
                 "tries INTEGER DEFAULT 0, folder TEXT, note TEXT, ts TEXT)")
        for aid, status, days in ((1, "built", 1), (2, "built", 3), (3, "needs_fix", 2), (4, "built", 10)):
            self.sql("INSERT INTO product_builds (approval_id, status, folder, note, ts) VALUES (?, ?, '', '', ?)",
                     (aid, status, _iso(NOW - datetime.timedelta(days=days))))
        self.sql("INSERT INTO approvals (ts, kind, title, status) VALUES (?, 'public_draft', 'd', 'pending')",
                 (_iso(NOW),))
        self.sql("INSERT INTO approvals (ts, kind, title, status) VALUES (?, 'scout_idea', 'i', 'pending')",
                 (_iso(NOW),))
        self.sql("INSERT INTO approvals (ts, kind, title, status, decided_ts) VALUES (?, 'scout_idea', 'j', "
                 "'approved', ?)", (_iso(NOW), _iso(NOW)))
        self.sql("INSERT INTO approvals (ts, kind, title, status, decided_ts) VALUES (?, 'trend', 'k', "
                 "'rejected', ?)", (_iso(NOW), _iso(NOW)))
        import scout
        scout.connect(self.db).close()
        for days in (1, 2, 9):
            self.sql("INSERT INTO scout_ideas (ts, idea) VALUES (?, 'x')",
                     (_iso(NOW - datetime.timedelta(days=days)),))
        stats = self.stats()
        self.assertEqual(stats["product"]["made"], {"packs_built": 2, "drafts_made": 1, "needs_fix": 1})
        self.assertEqual(stats["product"]["percent"], 67)
        self.assertIn("1 product draft(s) meeru chudali", stats["product"]["next"])
        self.assertEqual(stats["research"]["made"], {"ideas": 2, "proposals": 3})
        self.assertEqual(stats["research"]["waiting"], {"proposals": 1})
        self.assertEqual(stats["research"]["percent"], 50)


class TelegramTests(Base):
    def setUp(self):
        super().setUp()
        import telegram_inbox
        self.inbox = telegram_inbox
        self.texts = []
        for patch in (
            mock.patch.object(telegram_inbox, "send_text",
                              side_effect=lambda token, chat, text, buttons=None, keyboard=None:
                              self.texts.append(text) or True),
            mock.patch.object(telegram_inbox, "_send_text_kw",
                              side_effect=lambda token, chat, text, keyboard=None: self.texts.append(text) or True),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, text):
        handled, reply = self.inbox.call_plugins(text, "test-token", CHAT, db_path=self.db, now=NOW)
        self.assertTrue(handled, text)
        return (reply or {}).get("text") or (self.texts[-1] if self.texts else "")

    def test_slash_teams_shows_every_team(self):
        self.ready_pack(9001, "9001-focus")
        self.pins("9001-focus")
        text = self.call("/teams")
        self.assertIn("Teams report (last 7 rojulu)", text)
        self.assertIn("Pinterest: 100% (READY packs with ready Pins)", text)
        self.assertIn("5 Pins made", text)
        for label in ("Instagram:", "Marketing:", "Product:", "Research:", "Next step:"):
            self.assertIn(label, text)

    def test_one_team(self):
        text = self.call("/teams insta")
        self.assertIn("Instagram:", text)
        self.assertNotIn("Pinterest:", text)
        self.assertIn("ane team ledu", self.call("/teams cooking"))

    def test_bare_words_reply_with_help(self):
        for word in ("teams", "team report", "Teams Chupinchu"):
            with self.subTest(word=word):
                text = self.call(word)
                self.assertIn("/teams pampandi", text)
                self.assertNotIn("Teams report (last", text)

    def test_other_text_is_not_ours(self):
        import tg_teams
        self.assertIsNone(tg_teams.handle("post today", {}))
        self.assertIsNone(tg_teams.handle("/teamsx", {}))


if __name__ == "__main__":
    unittest.main()
