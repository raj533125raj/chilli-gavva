"""Tests for BINNY-V3C safe auto-approve engine and Telegram plugin.

Covers:
- Safe Auto-Decision logic (auto_approve.py):
  - Configuration retrieval and storage ('auto_approve_settings' in telegram_state)
  - Default threshold 75, configurable threshold
  - Default daily limit 2, configurable daily limit
  - Max 2 auto-approvals per day limit
  - Broken checker signal detection (last 5 drafts with identical scores block auto-approval)
  - Idea similarity check (word 5-shingles + Jaccard similarity > 0.45 against drafts approved/rejected in last 30 days)
  - Owner decision priority (never auto-decides already decided drafts)
  - 24-hour waiting rule (< 24h stays pending, >= 24h evaluates)
  - Low score (< threshold) auto-rejection with "Low score (auto)"
  - Morning digest line ('Auto-approved yesterday: …') in Tenglish house style
  - Kill switch and pause switch enforcement
  - Preserved run_auto_decisions signature and Telegram notification delivery
- Telegram plugin (tg_autoapprove.py):
  - Interface conformity (COMMANDS list and handle(text, ctx) function)
  - /autoapprove and words: status view, today's count, broken checker warning
  - 'autoapprove on' with confirmation flow (Yes / No)
  - 'autoapprove off' with confirmation flow (Yes / No)
  - 'autoapprove 80' (score threshold update) with confirmation flow
  - 'autoapprove limit 1' (daily limit update) with confirmation flow
  - Confirmation expiry (10 minutes)
  - Invalid inputs, out-of-range thresholds, non-matching text returns None
  - Strict Tenglish house style and no money mentions
"""
import datetime
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auto_approve
import guardrails
import tg_autoapprove

OWNER = "111222333"


class SafeAutoApproveTests(unittest.TestCase):
    def setUp(self):
        # Clear CI environment variables
        self.env_patcher = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
            "CHILLI_TEST": "1",
        }, clear=False)
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "auto.db")

        # Fixed fake clock: 2026-11-05 12:00:00 UTC
        self.fixed_now = datetime.datetime(2026, 11, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)

        self.sent = []
        p_send = mock.patch(
            "telegram_inbox.send_text",
            side_effect=lambda tok, chat, text, *a, **k: self.sent.append((chat, text)) or True,
        )
        p_send.start()
        self.addCleanup(p_send.stop)

    def add_draft(
        self,
        hours_ago=25,
        score=75,
        title="Digital Planner Printable",
        payload="Standard daily planner template for work",
        status="pending",
        reason=None,
        decided_hours_ago=None,
    ):
        ts = (self.fixed_now - datetime.timedelta(hours=hours_ago)).isoformat()
        decided_ts = (
            (self.fixed_now - datetime.timedelta(hours=decided_hours_ago)).isoformat()
            if decided_hours_ago is not None
            else None
        )
        conn = guardrails.connect(self.db)
        review = f"score {score}/100 | Checker: {score}/100" if score is not None else ""
        cur = conn.execute(
            "INSERT INTO approvals (ts, kind, title, payload, status, review, decided_ts, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, "public_draft", title, payload, status, review, decided_ts, reason),
        )
        conn.commit()
        aid = cur.lastrowid
        conn.close()
        return aid

    # -------------------------------------------------------------------------
    # 1-10: Settings storage and retrieval
    # -------------------------------------------------------------------------
    def test_01_default_settings(self):
        settings = auto_approve.get_settings(self.db)
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["threshold"], 75)
        self.assertEqual(settings["daily_limit"], 2)

    def test_02_save_and_retrieve_settings(self):
        ok = auto_approve.save_settings({"enabled": False, "threshold": 80, "daily_limit": 3}, self.db)
        self.assertTrue(ok)
        settings = auto_approve.get_settings(self.db)
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["threshold"], 80)
        self.assertEqual(settings["daily_limit"], 3)

    def test_03_settings_clamped_threshold(self):
        auto_approve.save_settings({"enabled": True, "threshold": 150, "daily_limit": 5}, self.db)
        settings = auto_approve.get_settings(self.db)
        self.assertEqual(settings["threshold"], 100)

        auto_approve.save_settings({"enabled": True, "threshold": -10, "daily_limit": 5}, self.db)
        settings2 = auto_approve.get_settings(self.db)
        self.assertEqual(settings2["threshold"], 0)

    def test_04_settings_clamped_daily_limit(self):
        auto_approve.save_settings({"enabled": True, "threshold": 75, "daily_limit": -2}, self.db)
        settings = auto_approve.get_settings(self.db)
        self.assertEqual(settings["daily_limit"], 0)

    def test_05_corrupt_settings_falls_back_to_defaults(self):
        conn = auto_approve._state_conn(self.db)
        conn.execute(
            "INSERT INTO telegram_state (key, value) VALUES (?, ?)",
            (auto_approve.STATE_SETTINGS_KEY, "invalid-json{{"),
        )
        conn.commit()
        conn.close()
        settings = auto_approve.get_settings(self.db)
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["threshold"], 75)
        self.assertEqual(settings["daily_limit"], 2)

    def test_06_tokenize_words_handles_punctuation(self):
        words = auto_approve.tokenize_words("Hello, World! This is a test: 123.")
        self.assertEqual(words, ["hello", "world", "this", "is", "a", "test", "123"])

    def test_07_tokenize_words_empty_string(self):
        self.assertEqual(auto_approve.tokenize_words(""), [])
        self.assertEqual(auto_approve.tokenize_words(None), [])

    def test_08_get_word_shingles_standard_k5(self):
        words = ["one", "two", "three", "four", "five", "six"]
        shingles = auto_approve.get_word_shingles(words, k=5)
        self.assertEqual(len(shingles), 2)
        self.assertIn("one two three four five", shingles)
        self.assertIn("two three four five six", shingles)

    def test_09_get_word_shingles_short_words_fallback(self):
        words = ["short", "title"]
        shingles = auto_approve.get_word_shingles(words, k=5)
        self.assertEqual(shingles, {"short title"})

    def test_10_jaccard_similarity_calculation(self):
        set_a = {"alpha", "beta", "gamma"}
        set_b = {"beta", "gamma", "delta"}
        # Intersection: 2, Union: 4 -> 0.5
        sim = auto_approve.jaccard_similarity(set_a, set_b)
        self.assertAlmostEqual(sim, 0.5)
        self.assertEqual(auto_approve.jaccard_similarity(set(), set_b), 0.0)

    # -------------------------------------------------------------------------
    # 11-20: Broken checker signal detection
    # -------------------------------------------------------------------------
    def test_11_broken_checker_signal_less_than_5_scores(self):
        for i in range(4):
            self.add_draft(score=80)
        self.assertFalse(auto_approve.has_broken_checker_signal(self.db))

    def test_12_broken_checker_signal_5_identical_scores(self):
        for i in range(5):
            self.add_draft(score=80)
        self.assertTrue(auto_approve.has_broken_checker_signal(self.db))

    def test_13_broken_checker_signal_different_scores_is_false(self):
        self.add_draft(score=80)
        self.add_draft(score=80)
        self.add_draft(score=85)
        self.add_draft(score=80)
        self.add_draft(score=80)
        self.assertFalse(auto_approve.has_broken_checker_signal(self.db))

    def test_14_broken_checker_blocks_auto_approval(self):
        # Add 5 drafts with identical scores 80
        for i in range(5):
            self.add_draft(hours_ago=30, score=80, title=f"Old Draft {i}")
        auto_approve.save_settings({"enabled": True, "threshold": 75, "daily_limit": 2}, self.db)

        # Now evaluate auto decisions
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        # None should be auto-approved because broken checker signal triggered
        approved = [d for d in decisions if d["action"] == "approved"]
        self.assertEqual(len(approved), 0)

    def test_15_broken_checker_empty_db_returns_false(self):
        self.assertFalse(auto_approve.has_broken_checker_signal(self.db))

    def test_16_draft_score_parsing_standard(self):
        item = {"review": "Score: 82/100 | Strong clarity"}
        self.assertEqual(auto_approve.get_draft_score(item, self.db), 82)

    def test_17_draft_score_parsing_fallback(self):
        item = {"review": "Reviewer verdict 77/100"}
        self.assertEqual(auto_approve.get_draft_score(item, self.db), 77)

    def test_18_draft_score_missing_returns_zero(self):
        item = {"review": "No score here"}
        self.assertEqual(auto_approve.get_draft_score(item, self.db), 0)

    def test_19_draft_score_from_product_council_fallback(self):
        with mock.patch("product_council.get_council_summary", return_value={"conviction": 8.5}):
            item = {"id": 42, "review": ""}
            self.assertEqual(auto_approve.get_draft_score(item, self.db), 85)

    def test_20_broken_checker_ignores_drafts_without_score(self):
        for i in range(5):
            self.add_draft(score=80)
        # Add a draft with no review
        self.add_draft(score=None)
        # Still finds the 5 scored drafts with identical score 80
        self.assertTrue(auto_approve.has_broken_checker_signal(self.db))

    # -------------------------------------------------------------------------
    # 21-30: Similarity check against 30-day approved/rejected drafts
    # -------------------------------------------------------------------------
    def test_21_similarity_check_empty_history(self):
        is_sim, score, match_id = auto_approve.is_similar_to_recent_draft(
            "Minimalist habit tracker printable daily sheet", db_path=self.db, now=self.fixed_now
        )
        self.assertFalse(is_sim)
        self.assertEqual(score, 0.0)

    def test_22_similarity_check_distinct_ideas_passes(self):
        self.add_draft(
            hours_ago=100,
            status="approved",
            title="ADHD Student Planner Printable",
            payload="Study schedule tracker with hourly blocks",
        )
        is_sim, sim_val, match_id = auto_approve.is_similar_to_recent_draft(
            "Recipe book binder kitchen meal prep tracker", db_path=self.db, now=self.fixed_now
        )
        self.assertFalse(is_sim)
        self.assertTrue(sim_val < 0.45)

    def test_23_similarity_check_high_overlap_fails(self):
        aid = self.add_draft(
            hours_ago=100,
            status="approved",
            title="Minimalist Budget Planner Spreadsheet Printable for Beginners",
            payload="Track your monthly expenses income and savings with clean layout",
        )
        candidate = (
            "Minimalist Budget Planner Spreadsheet Printable for Beginners "
            "Track your monthly expenses income and savings with clean aesthetic"
        )
        is_sim, sim_val, match_id = auto_approve.is_similar_to_recent_draft(
            candidate, db_path=self.db, now=self.fixed_now
        )
        self.assertTrue(is_sim)
        self.assertGreater(sim_val, 0.45)
        self.assertEqual(match_id, aid)

    def test_24_similarity_check_ignores_pending_drafts(self):
        # A pending draft (not approved or rejected) should not block
        self.add_draft(
            hours_ago=10,
            status="pending",
            title="Duplicate Title Test One Two Three Four Five",
            payload="Identical text body one two three four five",
        )
        is_sim, sim_val, match_id = auto_approve.is_similar_to_recent_draft(
            "Duplicate Title Test One Two Three Four Five Identical text body one two three four five",
            db_path=self.db,
            now=self.fixed_now,
        )
        self.assertFalse(is_sim)

    def test_25_similarity_check_ignores_drafts_older_than_30_days(self):
        # Draft approved 35 days ago (840 hours)
        self.add_draft(
            hours_ago=840,
            status="approved",
            decided_hours_ago=840,
            title="Old Duplicate Title Test One Two Three Four Five",
            payload="Identical text body one two three four five",
        )
        is_sim, sim_val, match_id = auto_approve.is_similar_to_recent_draft(
            "Old Duplicate Title Test One Two Three Four Five Identical text body one two three four five",
            db_path=self.db,
            now=self.fixed_now,
        )
        self.assertFalse(is_sim)

    def test_26_similarity_check_triggers_on_rejected_draft_in_30_days(self):
        aid = self.add_draft(
            hours_ago=48,
            status="rejected",
            decided_hours_ago=24,
            title="Rejected Bad Idea One Two Three Four Five Six",
            payload="Identical bad payload one two three four five six",
        )
        candidate = "Rejected Bad Idea One Two Three Four Five Six Identical bad payload one two three four five six"
        is_sim, sim_val, match_id = auto_approve.is_similar_to_recent_draft(
            candidate, db_path=self.db, now=self.fixed_now
        )
        self.assertTrue(is_sim)
        self.assertEqual(match_id, aid)

    def test_27_similar_draft_is_blocked_from_auto_approval(self):
        # Approved draft exists
        self.add_draft(
            hours_ago=48,
            status="approved",
            decided_hours_ago=20,
            title="Clean Daily Planner Morning Routine Checklist",
            payload="Hourly focus sheet for daily habit tracking",
        )
        # New draft candidate with score 85 (> 75)
        aid_cand = self.add_draft(
            hours_ago=26,
            score=85,
            title="Clean Daily Planner Morning Routine Checklist",
            payload="Hourly focus sheet for daily habit tracking with minimal lines",
        )
        auto_approve.save_settings({"enabled": True, "threshold": 75, "daily_limit": 2}, self.db)

        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        # Should NOT be auto-approved due to similarity > 0.45
        self.assertEqual(len(decisions), 0)
        item = guardrails.get_approval(aid_cand, self.db)
        self.assertEqual(item["status"], "pending")

    def test_28_similarity_ignores_self_id(self):
        aid = self.add_draft(
            hours_ago=2,
            status="approved",
            title="Self Reference Planner One Two Three Four Five",
        )
        is_sim, sim_val, match_id = auto_approve.is_similar_to_recent_draft(
            "Self Reference Planner One Two Three Four Five",
            candidate_id=aid,
            db_path=self.db,
            now=self.fixed_now,
        )
        self.assertFalse(is_sim)

    def test_29_similarity_handles_empty_candidate(self):
        is_sim, sim_val, match_id = auto_approve.is_similar_to_recent_draft("", db_path=self.db, now=self.fixed_now)
        self.assertFalse(is_sim)
        self.assertEqual(sim_val, 0.0)

    def test_30_similarity_handles_none_candidate(self):
        is_sim, sim_val, match_id = auto_approve.is_similar_to_recent_draft(None, db_path=self.db, now=self.fixed_now)
        self.assertFalse(is_sim)

    # -------------------------------------------------------------------------
    # 31-40: Daily limit enforcement & Morning digest
    # -------------------------------------------------------------------------
    def test_31_max_2_auto_approvals_per_day_enforced(self):
        auto_approve.save_settings({"enabled": True, "threshold": 75, "daily_limit": 2}, self.db)
        # Add 3 distinct drafts with high scores waiting > 24h
        aid1 = self.add_draft(hours_ago=26, score=85, title="Unique Draft Alpha AAA")
        aid2 = self.add_draft(hours_ago=27, score=80, title="Unique Draft Beta BBB")
        aid3 = self.add_draft(hours_ago=28, score=90, title="Unique Draft Gamma CCC")

        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        approved = [d for d in decisions if d["action"] == "approved"]
        self.assertEqual(len(approved), 2)
        self.assertEqual(approved[0]["id"], aid1)
        self.assertEqual(approved[1]["id"], aid2)

        # 3rd draft remains pending
        item3 = guardrails.get_approval(aid3, self.db)
        self.assertEqual(item3["status"], "pending")

    def test_32_custom_daily_limit_1_enforced(self):
        auto_approve.save_settings({"enabled": True, "threshold": 75, "daily_limit": 1}, self.db)
        aid1 = self.add_draft(hours_ago=26, score=85, title="Unique Draft One 111")
        aid2 = self.add_draft(hours_ago=27, score=80, title="Unique Draft Two 222")

        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        approved = [d for d in decisions if d["action"] == "approved"]
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["id"], aid1)

    def test_33_daily_limit_zero_blocks_all_auto_approvals(self):
        auto_approve.save_settings({"enabled": True, "threshold": 75, "daily_limit": 0}, self.db)
        self.add_draft(hours_ago=26, score=95, title="Unique Draft Super High")
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        approved = [d for d in decisions if d["action"] == "approved"]
        self.assertEqual(len(approved), 0)

    def test_34_count_auto_approvals_today(self):
        self.assertEqual(auto_approve.count_auto_approvals_today(self.db, now=self.fixed_now), 0)
        self.add_draft(
            hours_ago=2,
            status="approved",
            reason="auto-approved",
            decided_hours_ago=2,
        )
        self.assertEqual(auto_approve.count_auto_approvals_today(self.db, now=self.fixed_now), 1)

    def test_35_morning_digest_line_when_none_yesterday(self):
        digest = auto_approve.morning_digest_line(self.db, now=self.fixed_now)
        self.assertIn("Auto-approved yesterday: None", digest)

    def test_36_morning_digest_line_with_yesterday_approvals(self):
        # Decided 24 hours ago (which is yesterday relative to fixed_now)
        aid = self.add_draft(
            hours_ago=28,
            title="Finance Tracker Printable",
            status="approved",
            reason="auto-approved",
            decided_hours_ago=24,
        )
        digest = auto_approve.morning_digest_line(self.db, now=self.fixed_now)
        self.assertIn("Auto-approved yesterday: 1 drafts", digest)
        self.assertIn(f"#{aid} Finance Tracker Printable", digest)

    def test_37_morning_digest_no_currency_symbols(self):
        aid = self.add_draft(
            hours_ago=28,
            title="Finance Budget Sheet",
            status="approved",
            reason="auto-approved",
            decided_hours_ago=24,
        )
        digest = auto_approve.morning_digest_line(self.db, now=self.fixed_now)
        for sym in ("$", "₹", "€", "£", "USD", "INR"):
            self.assertNotIn(sym, digest)

    def test_38_low_score_auto_rejected_with_reason(self):
        auto_approve.save_settings({"enabled": True, "threshold": 75, "daily_limit": 2}, self.db)
        aid = self.add_draft(hours_ago=26, score=74, title="Under Threshold Template")
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["action"], "rejected")
        item = guardrails.get_approval(aid, self.db)
        self.assertEqual(item["status"], "rejected")
        conn = guardrails.connect(self.db)
        reason = conn.execute("SELECT reason FROM approvals WHERE id = ?", (aid,)).fetchone()[0]
        conn.close()
        self.assertEqual(reason, "Low score (auto)")

    def test_39_disabled_setting_skips_auto_approvals(self):
        auto_approve.save_settings({"enabled": False, "threshold": 75, "daily_limit": 2}, self.db)
        aid = self.add_draft(hours_ago=26, score=90, title="High Score Draft")
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions), 0)
        item = guardrails.get_approval(aid, self.db)
        self.assertEqual(item["status"], "pending")

    def test_40_disabled_setting_still_auto_rejects_low_score(self):
        auto_approve.save_settings({"enabled": False, "threshold": 75, "daily_limit": 2}, self.db)
        aid = self.add_draft(hours_ago=26, score=50, title="Very Poor Draft")
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["action"], "rejected")

    # -------------------------------------------------------------------------
    # 41-50: Telegram Plugin (tg_autoapprove) commands & flows
    # -------------------------------------------------------------------------
    def test_41_plugin_commands_structure(self):
        self.assertTrue(hasattr(tg_autoapprove, "COMMANDS"))
        self.assertIsInstance(tg_autoapprove.COMMANDS, list)
        cmd = tg_autoapprove.COMMANDS[0]
        self.assertEqual(cmd["cmd"], "/autoapprove")
        self.assertIn("autoapprove", cmd["words"])

    def test_42_plugin_show_status(self):
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": lambda t, keyboard=None: self.sent.append(t),
            "state_get": lambda k, d=None: None,
            "state_set": lambda k, v: None,
        }
        res = tg_autoapprove.handle("/autoapprove", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertTrue(len(self.sent) > 0)
        self.assertIn("Auto-Approve Settings", self.sent[-1])
        self.assertIn("75/100", self.sent[-1])

    def test_43_plugin_autoapprove_off_confirm_flow(self):
        state = {}
        sent_messages = []
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": lambda t, keyboard=None: sent_messages.append((t, keyboard)),
            "state_get": lambda k, d=None: state.get(k, d),
            "state_set": lambda k, v: state.__setitem__(k, v),
        }
        # Step 1: Request 'autoapprove off'
        res1 = tg_autoapprove.handle("autoapprove off", ctx)
        self.assertEqual(res1, {"handled": True})
        self.assertIn("Confirm cheyandi", sent_messages[-1][0])
        self.assertEqual(sent_messages[-1][1], [["Yes", "No"]])
        self.assertTrue(state.get("auto_approve_pending_confirm"))

        # Step 2: Confirm with 'Yes'
        res2 = tg_autoapprove.handle("Yes", ctx)
        self.assertEqual(res2, {"handled": True})
        self.assertIn("marcham", sent_messages[-1][0])
        settings = tg_autoapprove._load_settings(ctx)
        self.assertFalse(settings["enabled"])

    def test_44_plugin_autoapprove_on_confirm_flow(self):
        # Initially disabled
        auto_approve.save_settings({"enabled": False, "threshold": 75, "daily_limit": 2}, self.db)
        state = {"auto_approve_settings": json.dumps({"enabled": False, "threshold": 75, "daily_limit": 2})}
        sent_messages = []
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": lambda t, keyboard=None: sent_messages.append((t, keyboard)),
            "state_get": lambda k, d=None: state.get(k, d),
            "state_set": lambda k, v: state.__setitem__(k, v),
        }
        # Request 'autoapprove on'
        res1 = tg_autoapprove.handle("autoapprove on", ctx)
        self.assertEqual(res1, {"handled": True})
        self.assertIn("Confirm cheyandi", sent_messages[-1][0])

        # Confirm with 'avunu' (Tenglish yes)
        res2 = tg_autoapprove.handle("avunu", ctx)
        self.assertEqual(res2, {"handled": True})
        self.assertIn("marcham", sent_messages[-1][0])
        settings = tg_autoapprove._load_settings(ctx)
        self.assertTrue(settings["enabled"])

    def test_45_plugin_autoapprove_threshold_change(self):
        state = {}
        sent_messages = []
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": lambda t, keyboard=None: sent_messages.append((t, keyboard)),
            "state_get": lambda k, d=None: state.get(k, d),
            "state_set": lambda k, v: state.__setitem__(k, v),
        }
        # Request 'autoapprove 80'
        res1 = tg_autoapprove.handle("autoapprove 80", ctx)
        self.assertEqual(res1, {"handled": True})
        self.assertIn("Score threshold: 80/100", sent_messages[-1][0])

        # Confirm with 'sare'
        res2 = tg_autoapprove.handle("sare", ctx)
        self.assertEqual(res2, {"handled": True})
        settings = tg_autoapprove._load_settings(ctx)
        self.assertEqual(settings["threshold"], 80)

    def test_46_plugin_autoapprove_limit_change(self):
        state = {}
        sent_messages = []
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": lambda t, keyboard=None: sent_messages.append((t, keyboard)),
            "state_get": lambda k, d=None: state.get(k, d),
            "state_set": lambda k, v: state.__setitem__(k, v),
        }
        # Request 'autoapprove limit 1'
        res1 = tg_autoapprove.handle("autoapprove limit 1", ctx)
        self.assertEqual(res1, {"handled": True})
        self.assertIn("Daily limit: 1", sent_messages[-1][0])

        # Confirm with 'ok'
        res2 = tg_autoapprove.handle("ok", ctx)
        self.assertEqual(res2, {"handled": True})
        settings = tg_autoapprove._load_settings(ctx)
        self.assertEqual(settings["daily_limit"], 1)

    def test_47_plugin_cancel_confirmation(self):
        state = {}
        sent_messages = []
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": lambda t, keyboard=None: sent_messages.append((t, keyboard)),
            "state_get": lambda k, d=None: state.get(k, d),
            "state_set": lambda k, v: state.__setitem__(k, v),
        }
        tg_autoapprove.handle("autoapprove 85", ctx)
        # Cancel with 'No'
        res = tg_autoapprove.handle("No", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("cancel chesam", sent_messages[-1][0])
        # Setting remains default
        settings = tg_autoapprove._load_settings(ctx)
        self.assertEqual(settings["threshold"], 75)

    def test_48_plugin_cancel_with_tenglish_vaddu(self):
        state = {}
        sent_messages = []
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": lambda t, keyboard=None: sent_messages.append((t, keyboard)),
            "state_get": lambda k, d=None: state.get(k, d),
            "state_set": lambda k, v: state.__setitem__(k, v),
        }
        tg_autoapprove.handle("autoapprove 90", ctx)
        res = tg_autoapprove.handle("vaddu", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("cancel chesam", sent_messages[-1][0])

    def test_49_plugin_confirmation_expiry_after_10_minutes(self):
        state = {}
        sent_messages = []
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": lambda t, keyboard=None: sent_messages.append((t, keyboard)),
            "state_get": lambda k, d=None: state.get(k, d),
            "state_set": lambda k, v: state.__setitem__(k, v),
        }
        tg_autoapprove.handle("autoapprove 85", ctx)

        # 11 minutes later: owner sends 'Yes'
        ctx["now"] = self.fixed_now + datetime.timedelta(minutes=11)
        res = tg_autoapprove.handle("Yes", ctx)
        # Should not be handled as a confirmation (expired)
        self.assertIsNone(res)

    def test_50_plugin_unrelated_text_returns_none(self):
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": lambda t, keyboard=None: None,
            "state_get": lambda k, d=None: None,
            "state_set": lambda k, v: None,
        }
        self.assertIsNone(tg_autoapprove.handle("hello there", ctx))
        self.assertIsNone(tg_autoapprove.handle("/status", ctx))

    # -------------------------------------------------------------------------
    # 51-65: Edge cases, Safety & End-to-end integration
    # -------------------------------------------------------------------------
    def test_51_run_auto_decisions_owner_notification(self):
        auto_approve.save_settings({"enabled": True, "threshold": 75, "daily_limit": 2}, self.db)
        aid = self.add_draft(hours_ago=26, score=88, title="Productivity Template")
        count = auto_approve.run_auto_decisions(self.db, token="tok", owner_chat_id=OWNER, now=self.fixed_now)
        self.assertEqual(count, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn(f"Draft #{aid} auto-approved", self.sent[0][1])

    def test_52_owner_previous_decision_prevents_auto_decision(self):
        aid = self.add_draft(hours_ago=26, score=90, title="Owner Already Handled")
        # Owner manually rejected
        guardrails.decide(aid, False, self.db, reason="price")
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions), 0)

    def test_53_pause_switch_stops_auto_decide(self):
        self.add_draft(hours_ago=26, score=95)
        guardrails.set_paused(True, self.db)
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions), 0)

    def test_54_kill_switch_stops_auto_decide(self):
        self.add_draft(hours_ago=26, score=95)
        with mock.patch("guardrails.is_killed", return_value=True):
            decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
            self.assertEqual(len(decisions), 0)

    def test_55_draft_waiting_under_24h_is_skipped(self):
        self.add_draft(hours_ago=23, score=95)
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        self.assertEqual(len(decisions), 0)

    def test_56_broken_checker_with_different_scores_allows_auto_approve(self):
        # 5 drafts with diverse scores
        for sc in (70, 75, 80, 85, 90):
            self.add_draft(score=sc)
        auto_approve.save_settings({"enabled": True, "threshold": 75, "daily_limit": 2}, self.db)

        # Target draft waiting 26 hours
        aid = self.add_draft(hours_ago=26, score=85, title="Legitimate Approved Draft")
        decisions = auto_approve.auto_decide_drafts(self.db, now=self.fixed_now)
        approved = [d for d in decisions if d["id"] == aid]
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["action"], "approved")

    def test_57_autoapprove_already_on_message(self):
        state = {}
        sent = []
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": lambda t, keyboard=None: sent.append(t),
            "state_get": lambda k, d=None: state.get(k, d),
            "state_set": lambda k, v: state.__setitem__(k, v),
        }
        res = tg_autoapprove.handle("autoapprove on", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("already ON", sent[-1])

    def test_58_autoapprove_already_off_message(self):
        auto_approve.save_settings({"enabled": False, "threshold": 75, "daily_limit": 2}, self.db)
        state = {"auto_approve_settings": json.dumps({"enabled": False, "threshold": 75, "daily_limit": 2})}
        sent = []
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": lambda t, keyboard=None: sent.append(t),
            "state_get": lambda k, d=None: state.get(k, d),
            "state_set": lambda k, v: state.__setitem__(k, v),
        }
        res = tg_autoapprove.handle("autoapprove off", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("already OFF", sent[-1])

    def test_59_autoapprove_invalid_threshold_message(self):
        sent = []
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": lambda t, keyboard=None: sent.append(t),
            "state_get": lambda k, d=None: None,
            "state_set": lambda k, v: None,
        }
        res = tg_autoapprove.handle("autoapprove 150", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("0 nundi 100", sent[-1])

    def test_60_no_money_mentions_in_tg_autoapprove_or_auto_approve(self):
        import inspect
        src_auto = inspect.getsource(auto_approve)
        src_tg = inspect.getsource(tg_autoapprove)
        # Currency symbols and money terms (excluding regex end-of-string '$')
        for sym in ("₹", "€", "£", "USD", "INR", "dollars", "rupees", "cents"):
            self.assertNotIn(sym, src_auto)
            self.assertNotIn(sym, src_tg)

    def test_61_tenglish_phrasing_in_tg_autoapprove(self):
        status_text = tg_autoapprove._format_status_text({"db_path": self.db, "state_get": lambda k, d=None: None})
        self.assertIn("nadustondi", status_text)
        self.assertIn("Settings", status_text)

    def test_62_today_auto_approved_counts_only_approved_with_auto_reason(self):
        # A draft approved by human (reason is None or not 'auto-approved')
        self.add_draft(hours_ago=2, status="approved", reason="human-approved", decided_hours_ago=2)
        count = auto_approve.count_auto_approvals_today(self.db, now=self.fixed_now)
        self.assertEqual(count, 0)

    def test_63_similarity_check_respects_custom_threshold(self):
        self.add_draft(
            hours_ago=48,
            status="approved",
            title="Weekly Meal Planner Grocery List Printable",
            payload="Easy dinner ideas weekly schedule monday",
        )
        candidate = "Weekly Meal Planner Grocery List Printable Easy dinner ideas weekly schedule sunday"
        is_sim_strict, val_strict, _ = auto_approve.is_similar_to_recent_draft(
            candidate, db_path=self.db, now=self.fixed_now, threshold=0.5
        )
        self.assertTrue(is_sim_strict)

        is_sim_loose, val_loose, _ = auto_approve.is_similar_to_recent_draft(
            candidate, db_path=self.db, now=self.fixed_now, threshold=0.9
        )
        self.assertFalse(is_sim_loose)

    def test_64_broken_checker_window_configurable(self):
        # 3 identical scores
        for _ in range(3):
            self.add_draft(score=99)
        self.assertFalse(auto_approve.has_broken_checker_signal(self.db, check_window=5))
        self.assertTrue(auto_approve.has_broken_checker_signal(self.db, check_window=3))

    def test_65_handle_returns_text_when_send_text_is_none(self):
        ctx = {
            "db_path": self.db,
            "chat_id": OWNER,
            "now": self.fixed_now,
            "send_text": None,
            "state_get": lambda k, d=None: None,
            "state_set": lambda k, v: None,
        }
        res = tg_autoapprove.handle("/autoapprove", ctx)
        self.assertIsInstance(res, dict)
        self.assertIn("text", res)
        self.assertIn("Auto-Approve Settings", res["text"])


if __name__ == "__main__":
    unittest.main()
