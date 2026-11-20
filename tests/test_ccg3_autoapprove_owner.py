"""CCG-3: auto-approve follows the owner's 24-hour rule, owner-controlled.

- the daily limit is an owner setting (default stays 2); '/autoapprove limit N' (1-20) saves it
- waiting_report(): every waiting draft with hours waited and WHY it still waits; proposals apart
- '/autoapprove status' shows it in Tenglish; the bare words still reply with help
All Telegram checks go through telegram_inbox.call_plugins with fake send functions.
"""
import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

import auto_approve
import checker
import guardrails

NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
CHAT = int("1234" "5678")


def _pass(score):
    return checker.format_review(score, [], "pass")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "agent_data.db")
        guardrails.connect(self.db).close()
        self.n = 0

    def add(self, title, review, hours, kind="public_draft", status="pending", reason=None, payload=None):
        self.n += 1
        ts = (NOW - datetime.timedelta(hours=hours)).replace(tzinfo=None).isoformat()
        conn = guardrails.connect(self.db)
        cur = conn.execute("INSERT INTO approvals (ts, kind, title, payload, review, status, reason, decided_ts) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (ts, kind, title, payload or f"PRODUCT NAME: thing {self.n} words {self.n * 11} here",
                            review, status, reason,
                            NOW.isoformat() if status != "pending" else None))
        conn.commit()
        conn.close()
        return cur.lastrowid


class LimitSettingTests(Base):
    def test_default_stays_two(self):
        self.assertEqual(auto_approve.DEFAULT_DAILY_LIMIT, 2)
        self.assertEqual(auto_approve.effective_settings(self.db)["daily_limit"], 2)

    def test_owner_limit_is_used(self):
        ids = [self.add(f"Draft: [Budget Sheets Agent] sheet {i}", _pass(85), 30 + i) for i in range(4)]
        auto_approve.set_daily_limit(3, self.db)
        decided = auto_approve.auto_decide_drafts(self.db, now=NOW)
        self.assertEqual([d["id"] for d in decided], ids[:3])

    def test_limit_keeps_other_settings_and_range(self):
        settings = auto_approve.set_daily_limit(5, self.db)
        self.assertEqual(settings, {"enabled": True, "threshold": auto_approve.SCORE_THRESHOLD, "daily_limit": 5})
        for bad in (0, 21, -1):
            with self.assertRaises(ValueError):
                auto_approve.set_daily_limit(bad, self.db)
        self.assertEqual(auto_approve.effective_settings(self.db)["daily_limit"], 5)


class WaitingReportTests(Base):
    def test_every_reason(self):
        guardrails.decide(self.add("Draft: [ADHD Planners Agent] Old focus planner", _pass(80), 200,
                                   payload="PRODUCT NAME: Focus planner for adults who like calm printable pages"),
                          True, self.db)
        young = self.add("Draft: [Budget Sheets Agent] Young", _pass(90), 5)
        revise = self.add("Draft: [ADHD Planners Agent] Revise", checker.format_review(80, ["long"], "revise"), 50)
        low = self.add("Draft: [2027 Planners Agent] Low", _pass(40), 49)
        similar = self.add("Draft: [ADHD Planners Agent] Old focus planner copy", _pass(82), 48,
                           payload="PRODUCT NAME: Focus planner for adults who like calm printable pages")
        ready1 = self.add("Draft: [Budget Sheets Agent] One", _pass(85), 40)
        ready2 = self.add("Draft: [Notion Templates Agent] Two", _pass(86), 30)
        limited = self.add("Draft: [Resume Bundle Agent] Three", _pass(87), 26)
        scout = self.add("[Scout Idea] Chore chart", "Checker: 82/100 | CHECKER: PASS (rules)", 76, kind="scout_idea")
        report = auto_approve.waiting_report(self.db, now=NOW)
        why = {d["id"]: d["why"] for d in report["drafts"]}
        self.assertEqual(why, {young: "under_24h", revise: "checker_revise", low: "low_score",
                               similar: "similar", ready1: "ready", ready2: "ready", limited: "daily_limit"})
        self.assertEqual([p["id"] for p in report["proposals"]], [scout])
        hours = {d["id"]: d["hours"] for d in report["drafts"]}
        self.assertEqual((hours[young], hours[revise]), (5, 50))
        self.assertIn("19h migilindi", next(d["why_text"] for d in report["drafts"] if d["id"] == young))
        # the report is exactly what the next run does
        decided = {d["id"]: d["action"] for d in auto_approve.auto_decide_drafts(self.db, now=NOW)}
        self.assertEqual(decided, {low: "rejected", ready1: "approved", ready2: "approved"})

    def test_checker_signal_and_off(self):
        for i in range(5):
            self.add(f"Draft: [Budget Sheets Agent] same {i}", _pass(77), 30 + i)
        report = auto_approve.waiting_report(self.db, now=NOW)
        self.assertEqual({d["why"] for d in report["drafts"]}, {"checker_signal"})
        auto_approve.save_settings({"enabled": False, "threshold": 70, "daily_limit": 2}, self.db)
        self.assertEqual({d["why"] for d in auto_approve.waiting_report(self.db, now=NOW)["drafts"]}, {"off"})

    def test_text_is_tenglish_and_splits_proposals(self):
        self.add("Draft: [Budget Sheets Agent] Couple budget", _pass(84), 60)
        self.add("[Trend Proposal] Weekly planners", "Score: 90/100", 70, kind="trend_proposal")
        self.add("Proposal: retry wait", None, 50, kind="code_patch")
        text = auto_approve.waiting_report_text(self.db, now=NOW)
        self.assertIn("Product drafts waiting: 1 (oldest 60h)", text)
        self.assertIn("Proposals waiting: 2 (product drafts kaadu", text)
        self.assertIn("next run lo auto-approve avutundi", text)
        self.assertIn("Self-Improvement Proposals waiting: 0", text)
        self.assertNotRegex(text, r"[$₹€£]")


class TelegramTests(Base):
    """Through telegram_inbox.call_plugins with fake send functions."""

    def setUp(self):
        super().setUp()
        import telegram_inbox
        self.inbox = telegram_inbox
        self.texts = []
        patches = (
            mock.patch.object(telegram_inbox, "send_text",
                              side_effect=lambda token, chat, text, buttons=None, keyboard=None:
                              self.texts.append(text) or True),
            mock.patch.object(telegram_inbox, "_send_text_kw",
                              side_effect=lambda token, chat, text, keyboard=None: self.texts.append(text) or True),
            mock.patch.object(telegram_inbox, "_call", return_value={"ok": True}),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, text):
        handled, reply = self.inbox.call_plugins(text, "test-token", CHAT, db_path=self.db)
        self.assertTrue(handled, text)
        return (reply or {}).get("text") or (self.texts[-1] if self.texts else "")

    def test_status_shows_the_report(self):
        self.add("Draft: [Budget Sheets Agent] Couple budget", _pass(84), 60)
        self.add("[Scout Idea] Chore chart", "Checker: 82/100 | CHECKER: PASS (rules)", 76, kind="scout_idea")
        for word in ("/autoapprove status", "autoapprove status", "auto approve status"):
            with self.subTest(word=word):
                text = self.call(word)
                self.assertIn("Product drafts waiting: 1 (oldest 60h)", text)
                self.assertIn("Proposals waiting: 1", text)

    def test_limit_saves_the_owner_number(self):
        text = self.call("/autoapprove limit 4")
        self.assertIn("limit ippudu 4", text)
        self.assertEqual(auto_approve.effective_settings(self.db)["daily_limit"], 4)
        conn = guardrails.connect(self.db)
        stored = json.loads(conn.execute("SELECT value FROM telegram_state WHERE key=?",
                                         (auto_approve.STATE_SETTINGS_KEY,)).fetchone()[0])
        conn.close()
        self.assertEqual(stored["daily_limit"], 4)
        self.assertIn("daily limit 4", self.call("/autoapprove status"))

    def test_limit_out_of_range(self):
        for bad in ("/autoapprove limit 0", "/autoapprove limit 21", "/autoapprove limit lots"):
            with self.subTest(bad=bad):
                self.assertIn("1 nundi 20 madhya", self.call(bad))
        self.assertEqual(auto_approve.effective_settings(self.db)["daily_limit"], 2)
        self.assertIn("1 nundi 20 madhya", self.call("autoapprove limit 50"))

    def test_bare_words_reply_with_help(self):
        for word in ("/autoapprove", "autoapprove", "auto approve"):
            with self.subTest(word=word):
                text = self.call(word)
                self.assertIn("/autoapprove status", text)
                self.assertIn("/autoapprove limit N", text)


if __name__ == "__main__":
    unittest.main()
