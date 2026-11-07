"""Unit tests for BINNY-KH ROBOT: Post Planner in Telegram + Morning Message.

Tests cover:
- /today slash command
- Plain text / Tenglish commands: "today posts", "ee roju posts", "eroju posts"
- "posted 2", "post chesa 3", "post chesanu 1"
- Slash command /posted N
- Bad N: missing N, N=0, out of range N, negative N
- No ready packs (empty plan / empty products directory)
- Atomic plan loading and saving
- Morning message sent once per day after 08:00 IST
- Morning message not sent twice on the same day
- Morning message never sent at night (22:00 - 08:00 IST)
- Non-owner chat ignored
- Scrubbing: no money amounts, <= 1200 chars, no file paths outside the pack
- Full _run integration verifying morning message delivery
"""
import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import post_planner
import telegram_inbox as ti

OWNER = "111222333"
IST = ZoneInfo("Asia/Kolkata")

PNG_BYTES = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
             b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
             b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def _upd(uid, text, chat=OWNER):
    return {"update_id": uid, "message": {"chat": {"id": int(chat)}, "text": text}}


class KhPostTelegramTests(unittest.TestCase):
    def setUp(self):
        # Clear all GITHUB_* and RUNNER_ENVIRONMENT per instructions
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        env_patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "kh_test.db")
        self.plan_path = os.path.join(self.tmp.name, "post_plan.json")
        self.products_dir = os.path.join(self.tmp.name, "exported_content", "products", "upload")
        os.makedirs(self.products_dir, exist_ok=True)

        self.sent = []
        self.edited = []
        self.calls = []

        def fake_send(tok, chat, text, buttons=None):
            self.sent.append((str(chat), text, buttons))
            return True

        def fake_edit(tok, chat, msg_id, text, buttons=None):
            self.edited.append((str(chat), msg_id, text, buttons))
            return True

        def fake_call(tok, method, params, *a, **kw):
            self.calls.append((method, params))
            return {"ok": True}

        p_send = mock.patch.object(ti, "send_text", side_effect=fake_send)
        p_edit = mock.patch.object(ti, "edit_text", side_effect=fake_edit)
        p_call = mock.patch.object(ti, "_call", side_effect=fake_call)

        p_send.start()
        p_edit.start()
        p_call.start()

        self.addCleanup(p_send.stop)
        self.addCleanup(p_edit.stop)
        self.addCleanup(p_call.stop)

    def _create_pack(self, pack_id: str, title: str, tags: list[str], images: list[str]) -> Path:
        pdir = Path(self.products_dir) / pack_id
        pdir.mkdir(parents=True, exist_ok=True)
        listing = (
            f"Title: {title}\n\n"
            f"Description:\nClean routine planner.\n\n"
            f"Tags: {', '.join(tags)}\n\n"
            f"What you get:\nPDF file\n"
        )
        (pdir / "listing.txt").write_text(listing, encoding="utf-8")
        for img in images:
            ipath = pdir / img
            ipath.parent.mkdir(parents=True, exist_ok=True)
            ipath.write_bytes(PNG_BYTES)
        return pdir

    def _setup_sample_plan(self, date_str: str) -> list[dict]:
        self._create_pack("pack-habit", "Daily Habit Planner", ["habits", "routine"], ["cover.png", "pin_1.png"])
        self._create_pack("pack-budget", "Minimal Monthly Budget", ["budget", "finance"], ["cover.png", "pin_2.png"])
        plan = post_planner.build_plan(self.products_dir, start_date=date_str, days=7)
        post_planner.save_plan(plan, self.plan_path)
        return plan

    def run_updates(self, updates):
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            return ti.process_updates("tok", OWNER, self.db)

    # 1. /today slash command with plan
    def test_01_today_slash_command(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "/today")])
        self.assertEqual(len(self.sent), 1)
        chat, text, _ = self.sent[0]
        self.assertEqual(chat, OWNER)
        self.assertIn("Today post (3 pins, 1 Instagram):", text)
        self.assertIn("1) pack-", text)
        self.assertIn("Reply: posted 1", text)
        self.assertLessEqual(len(text), 1200)

    # 2. "ee roju posts" Tenglish
    def test_02_ee_roju_posts_tenglish(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "ee roju posts")])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Today post (3 pins, 1 Instagram):", self.sent[0][1])

    # 3. "eroju posts" Tenglish variant
    def test_03_eroju_posts_tenglish(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "eroju posts")])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Today post", self.sent[0][1])

    # 4. "today posts" English
    def test_04_today_posts_english(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "today posts")])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Today post", self.sent[0][1])

    # 5. "posted 2" English
    def test_05_posted_2_english(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "posted 2")])
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0][1]
        self.assertIn("Sare, post 2 done ✓", text)
        self.assertIn("This week: 1 of", text)

        # Verify plan in file updated
        plan = post_planner.load_plan(self.plan_path)
        day_posts = [p for p in plan if p["date"] == today_str]
        self.assertEqual(day_posts[1]["status"], "done")
        self.assertEqual(day_posts[0]["status"], "todo")

    # 6. "post chesa 3" Tenglish
    def test_06_post_chesa_3_tenglish(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "post chesa 3")])
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0][1]
        self.assertIn("Sare, post 3 done ✓", text)
        self.assertIn("This week: 1 of", text)

    # 7. "post chesanu 1" Tenglish variant
    def test_07_post_chesanu_1_tenglish(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "post chesanu okati")])
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0][1]
        self.assertIn("Sare, post 1 done ✓", text)

    # 8. Slash command /posted 1
    def test_08_slash_posted_command(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "/posted 1")])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Sare, post 1 done ✓", self.sent[0][1])

    # 9. Bad N: missing number in /posted
    def test_09_bad_n_missing_in_slash_command(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "/posted")])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Please specify a valid post number", self.sent[0][1])

    # 10. Bad N: missing number in plain text "posted"
    def test_10_bad_n_missing_in_plain_text(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "posted")])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Please specify a valid post number", self.sent[0][1])

    # 11. Bad N: zero "posted 0"
    def test_11_bad_n_zero(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "posted 0")])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Please specify a valid post number", self.sent[0][1])

    # 12. Bad N: out of range "posted 99"
    def test_12_bad_n_out_of_range(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "posted 99")])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Could not mark post 99 as done", self.sent[0][1])

    # 13. Bad N: negative in slash command /posted -5
    def test_13_bad_n_negative(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            # Parse command returns positive ints, but test mark_post_done_text directly with -5
            reply = ti.mark_post_done_text(-5, plan_path=self.plan_path)
            self.assertIn("Please specify a valid post number", reply)

    # 14. No ready packs: reply post_planner clear empty message for /today
    def test_14_no_ready_packs_slash_today(self):
        # Empty products directory and no existing plan
        empty_dir = os.path.join(self.tmp.name, "empty_products")
        os.makedirs(empty_dir, exist_ok=True)
        missing_plan = os.path.join(self.tmp.name, "nonexistent_plan.json")
        with mock.patch.object(post_planner, "PLAN_PATH", missing_plan):
            reply = ti.today_posts_text(plan_path=missing_plan, products_dir=empty_dir)
            today_str = datetime.date.today().isoformat()
            self.assertEqual(reply, f"No posts scheduled for {today_str}.")

    # 15. No ready packs: /posted 1 fails gracefully
    def test_15_no_ready_packs_posted_fails_gracefully(self):
        empty_dir = os.path.join(self.tmp.name, "empty_products")
        os.makedirs(empty_dir, exist_ok=True)
        missing_plan = os.path.join(self.tmp.name, "nonexistent_plan.json")
        reply = ti.mark_post_done_text(1, plan_path=missing_plan, products_dir=empty_dir)
        self.assertIn("Could not mark post 1 as done", reply)

    # 16. Non-owner message is ignored for /today
    def test_16_non_owner_ignored_today(self):
        self.run_updates([_upd(1, "/today", chat="99999999")])
        self.assertEqual(len(self.sent), 0)

    # 17. Non-owner message is ignored for "posted 1"
    def test_17_non_owner_ignored_posted(self):
        self.run_updates([_upd(1, "posted 1", chat="99999999")])
        self.assertEqual(len(self.sent), 0)

    # 18. Morning send once after 08:00 IST
    def test_18_morning_message_sent_after_0800_ist(self):
        today_str = "2026-11-10"
        self._setup_sample_plan(today_str)
        # 08:30 IST on 2026-11-10 -> 03:00 UTC
        dt = datetime.datetime(2026, 11, 10, 8, 30, tzinfo=IST)
        sent = ti.send_morning_post_plan("tok", OWNER, db_path=self.db, now=dt,
                                         plan_path=self.plan_path, products_dir=self.products_dir)
        self.assertTrue(sent)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], OWNER)
        self.assertIn("Today post", self.sent[0][1])
        # Saved state in telegram_state
        last_date = ti.get_state("last_morning_post_date", None, self.db)
        self.assertEqual(last_date, "2026-11-10")

    # 19. Morning message not sent twice on same day
    def test_19_morning_message_not_sent_twice_same_day(self):
        today_str = "2026-11-10"
        self._setup_sample_plan(today_str)
        dt1 = datetime.datetime(2026, 11, 10, 8, 30, tzinfo=IST)
        sent1 = ti.send_morning_post_plan("tok", OWNER, db_path=self.db, now=dt1,
                                          plan_path=self.plan_path, products_dir=self.products_dir)
        self.assertTrue(sent1)

        # Later that morning, e.g. 10:00 IST
        dt2 = datetime.datetime(2026, 11, 10, 10, 0, tzinfo=IST)
        sent2 = ti.send_morning_post_plan("tok", OWNER, db_path=self.db, now=dt2,
                                          plan_path=self.plan_path, products_dir=self.products_dir)
        self.assertFalse(sent2)
        self.assertEqual(len(self.sent), 1)

    # 20. Morning message never sent at night before 08:00 IST (e.g. 05:00 IST)
    def test_20_morning_message_never_sent_before_0800_ist(self):
        dt_early = datetime.datetime(2026, 11, 10, 7, 59, tzinfo=IST)
        sent = ti.send_morning_post_plan("tok", OWNER, db_path=self.db, now=dt_early,
                                         plan_path=self.plan_path, products_dir=self.products_dir)
        self.assertFalse(sent)
        self.assertEqual(len(self.sent), 0)
        self.assertIsNone(ti.get_state("last_morning_post_date", None, self.db))

    # 21. Morning message never sent at night after 22:00 IST (e.g. 22:30 IST)
    def test_21_morning_message_never_sent_after_2200_ist(self):
        dt_late = datetime.datetime(2026, 11, 10, 22, 15, tzinfo=IST)
        sent = ti.send_morning_post_plan("tok", OWNER, db_path=self.db, now=dt_late,
                                         plan_path=self.plan_path, products_dir=self.products_dir)
        self.assertFalse(sent)
        self.assertEqual(len(self.sent), 0)

    # 22. Morning message next day runs again
    def test_22_morning_message_next_day_runs(self):
        self._setup_sample_plan("2026-11-10")
        dt_day1 = datetime.datetime(2026, 11, 10, 9, 0, tzinfo=IST)
        sent1 = ti.send_morning_post_plan("tok", OWNER, db_path=self.db, now=dt_day1,
                                          plan_path=self.plan_path, products_dir=self.products_dir)
        self.assertTrue(sent1)

        dt_day2 = datetime.datetime(2026, 11, 11, 9, 0, tzinfo=IST)
        sent2 = ti.send_morning_post_plan("tok", OWNER, db_path=self.db, now=dt_day2,
                                          plan_path=self.plan_path, products_dir=self.products_dir)
        self.assertTrue(sent2)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(ti.get_state("last_morning_post_date", None, self.db), "2026-11-11")

    # 23. Constraints: no money patterns, length <= 1200 chars, clean paths
    def test_23_message_scrubbed_and_length_bounded(self):
        self._create_pack("pack-money", "Planner $50 USD Deal", ["budget", "$10 save"], ["cover.png"])
        plan = post_planner.build_plan(self.products_dir, start_date="2026-11-10", days=2)
        post_planner.save_plan(plan, self.plan_path)

        text = ti.today_posts_text(plan_path=self.plan_path, date="2026-11-10")
        self.assertNotIn("$50", text)
        self.assertNotIn("USD", text)
        self.assertNotIn("$10", text)
        self.assertNotIn(self.tmp.name, text)
        self.assertLessEqual(len(text), 1200)

    # 24. Multiple marks increment week summary done count
    def test_24_week_summary_increments_with_multiple_posts(self):
        today_str = datetime.date.today().isoformat()
        self._setup_sample_plan(today_str)
        with mock.patch.object(post_planner, "PLAN_PATH", self.plan_path):
            self.run_updates([_upd(1, "posted 1")])
            self.run_updates([_upd(2, "posted 2")])
        self.assertEqual(len(self.sent), 2)
        self.assertIn("1 of", self.sent[0][1])
        self.assertIn("2 of", self.sent[1][1])

    # 25. Help command includes /today and /posted N
    def test_25_help_includes_today_and_posted(self):
        self.run_updates([_upd(1, "/help")])
        self.assertEqual(len(self.sent), 1)
        help_text = self.sent[0][1]
        self.assertIn("/today", help_text)
        self.assertIn("/posted", help_text)

    # 26. Full _run integration executes morning message when in morning window
    def test_26_full_run_integration_morning_post_plan(self):
        dt = datetime.datetime(2026, 11, 10, 9, 30, tzinfo=IST)
        with mock.patch("notify._telegram_configured", return_value=(True, "tok", OWNER)), \
             mock.patch.object(ti, "send_morning_post_plan", return_value=True) as mock_morning, \
             mock.patch.object(ti, "process_updates", return_value=0), \
             mock.patch.object(ti, "alert_new_drafts", return_value=0), \
             mock.patch.object(ti, "remind_waiting_drafts", return_value=False), \
             mock.patch("daily_report.maybe_send", return_value=False):
            res = ti._run(db_path=self.db)
            self.assertTrue(mock_morning.called)
            self.assertTrue(res.get("morning_post_plan"))


if __name__ == "__main__":
    unittest.main()
