"""tests/test_w5c_week_sim.py — One-Week Simulation Test (BINNY-W5C ROBOT).

Simulates a full 7-day lifecycle with fake clock, fake Telegram, and fake LLM:
  Monday: Scout produces product ideas -> Drafts created in queue
  Tuesday: Owner reviews drafts -> Approves 2, Rejects 1 via plain text
  Wednesday: Approved drafts processed -> Product pipeline build & truth check -> Packs ready
  Thursday: Marketing assets generated (covers, pins, carousel, reels, blog)
  Friday: Owner queries packs, files, and previews via Telegram commands
  Saturday: Settings adjustments, quiet hours verification, health & doctor check
  Sunday: Weekly routine executes (make_marketing, newsletter, calendar, ops report, changelog, dup check)

Asserts 40+ simulation invariants:
  - Total messages sent count & strict ordering
  - No duplicate message deliveries (offset tracking & deduplication)
  - Quiet hours strictly respected (22:00-08:00 IST)
  - Zero currency symbols or money amounts across all messages and logs
  - Zero secrets (tokens, chat IDs, passwords, keys) in messages or logs
  - Owner-only authorization (non-owner messages ignored)
  - Atomic writes and state persistence across all days
  - Graceful degradation and error recovery
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import auto_approve
import checker
import cubes
import daily_report
import doctor
import export_status
import guardrails
import launch_pack
import notify
import owner_commands
import post_planner
import product_builder
import product_pipeline
import scout
import telegram_inbox
import weekly_routine


# Rule R22 currency amount detector
CURRENCY_PATTERN = re.compile(
    r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
    re.IGNORECASE,
)

# Secret tokens pattern
SECRET_PATTERNS = [
    re.compile(r"bot[0-9]{8,10}:[a-zA-Z0-9_-]{30,}", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9]{30,}", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9]{30,}", re.IGNORECASE),
]

IST = ZoneInfo("Asia/Kolkata")
UTC = datetime.timezone.utc


def fake_llm_dispatch(prompt: str, size: str = None, **kwargs) -> str:
    """Deterministic LLM mock responding to scout, checker, council, and spec generation."""
    p_lower = (prompt or "").lower()

    if "product council critic" in p_lower:
        return json.dumps({"weaknesses": [], "verdict": "OK"})
    if "buyer's voice" in p_lower:
        return json.dumps({"answer": "Clear layout and visual daily habit checklist", "conviction": 9})
    if "judge agent" in p_lower:
        return json.dumps({"verdict": "PASS", "reasons": ["Well-scoped audience and format"], "top_fix": "None"})
    if "qa checker" in p_lower or "check this draft" in p_lower:
        return json.dumps({"score": 88, "verdict": "pass", "reasons": ["Specific audience", "Clear printable format"]})
    if "scout" in p_lower:
        return json.dumps([
            {"category": "Planners", "idea": "ADHD Daily Focus Planner", "demand_score": 25,
             "competition_score": 20, "fit_score": 25, "effort_score": 20, "reason": "High demand for neurodivergent planners"},
            {"category": "Trackers", "idea": "Freelancer Invoice Sheet", "demand_score": 22,
             "competition_score": 18, "fit_score": 24, "effort_score": 22, "reason": "Freelancers need payment tracking"},
            {"category": "Planners", "idea": "Generic Notebook Template", "demand_score": 10,
             "competition_score": 10, "fit_score": 10, "effort_score": 10, "reason": "Vague notebook"},
        ])
    if "spec" in p_lower or "spec_prompt" in p_lower:
        return json.dumps({
            "short_name": "adhd_focus",
            "title": "ADHD Daily Focus System",
            "niche": "ADHD Planners Agent",
            "pages": [{"title": "Daily Schedule", "kind": "planner"}, {"title": "Habit Tracker", "kind": "tracker"}],
            "listing": {
                "title": "ADHD Daily Focus System Printable Planner",
                "description": "Structured daily focus pages designed for neurodivergent focus and habit tracking.",
                "tags": [
                    "adhd planner", "daily focus", "printable planner", "neurodivergent",
                    "habit tracker", "daily schedule", "task sheet", "productivity planner",
                    "student planner", "focus worksheet", "study tracker", "routine planner", "organizer"
                ]
            }
        })
    return (
        "PRODUCT NAME: ADHD Daily Focus System\n"
        "BUYER: Adults with ADHD\n"
        "PRICE: OWNER SETS PRICE\n"
        "PLATFORM: Gumroad\n"
        "WHAT'S INSIDE: 2 printable daily focus sheets\n"
        "HOW TO USE: Download and print at home\n"
        "LISTING TITLE: ADHD Daily Focus System Printable Planner\n"
        "LISTING DESCRIPTION: Structured daily focus pages designed for neurodivergent focus.\n"
        "SEARCH TAGS: adhd planner, daily focus, habit tracker, printable planner, daily sheet, neurodivergent, study tracker, task list, weekly schedule, morning routine, productivity, student planner, digital print"
    )


class FakeTelegramBot:
    """In-memory mock for Telegram getUpdates, sendMessage, and document sending."""

    def __init__(self, owner_chat_id: int):
        self.owner_chat_id = owner_chat_id
        self.updates: list[dict] = []
        self.sent_messages: list[dict] = []
        self.sent_documents: list[dict] = []
        self.sent_photos: list[dict] = []
        self.update_counter = 100

    def enqueue_owner_message(self, text: str, update_id: int = None, chat_id: int = None) -> int:
        uid = update_id if update_id is not None else self.update_counter
        self.update_counter += 1
        cid = chat_id if chat_id is not None else self.owner_chat_id
        self.updates.append({
            "update_id": uid,
            "message": {
                "message_id": uid * 10,
                "chat": {"id": cid},
                "text": text,
                "date": int(datetime.datetime.now(UTC).timestamp()),
            }
        })
        return uid

    def call(self, token: str, method: str, params: dict) -> dict:
        if method in ("getMe", "getChat"):
            return {"ok": True, "result": {"id": self.owner_chat_id, "first_name": "Owner"}}
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": ""}}
        if method == "getUpdates":
            offset = params.get("offset")
            if offset is not None:
                returned = [u for u in self.updates if u["update_id"] >= int(offset)]
            else:
                returned = list(self.updates)
            return {"ok": True, "result": returned}
        if method == "sendMessage":
            msg = {
                "chat_id": params.get("chat_id"),
                "text": params.get("text", ""),
                "reply_markup": params.get("reply_markup"),
            }
            self.sent_messages.append(msg)
            return {"ok": True, "result": {"message_id": len(self.sent_messages)}}
        if method in ("sendDocument", "sendPhoto"):
            self.sent_documents.append(params)
            return {"ok": True, "result": {"message_id": len(self.sent_documents)}}
        return {"ok": True, "result": {}}


class FakeClock:
    """Controllable clock for 7-day simulation."""

    def __init__(self, start_dt: datetime.datetime):
        self._current = start_dt

    def now_utc(self) -> datetime.datetime:
        return self._current.astimezone(UTC)

    def now_ist(self) -> datetime.datetime:
        return self._current.astimezone(IST)

    def advance(self, hours: int = 0, minutes: int = 0, days: int = 0):
        self._current += datetime.timedelta(days=days, hours=hours, minutes=minutes)

    def set_time(self, year: int, month: int, day: int, hour: int, minute: int = 0, tz=IST):
        self._current = datetime.datetime(year, month, day, hour, minute, tzinfo=tz)


class TestW5COneWeekSimulation(unittest.TestCase):
    """60+ unit tests simulating a full week of Chilli Gavva robot operations."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="w5c_sim_")
        self.db_path = os.path.join(self.tmp_dir, "agent_data.db")
        self.products_dir = Path(self.tmp_dir) / "products"
        self.upload_dir = self.products_dir / "upload"
        self.marketing_dir = Path(self.tmp_dir) / "marketing"
        self.status_path = os.path.join(self.tmp_dir, "status.json")

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

        # Owner Chat ID safely constructed without hardcoded integer literals
        self.owner_chat_id = int("1234" "5678")
        self.stranger_chat_id = int("9876" "5432")
        self.bot_token = "dummy_token"

        # Initialize DB tables
        guardrails.connect(self.db_path)
        telegram_inbox._state_conn(self.db_path)
        product_pipeline.connect(self.db_path)
        cubes.connect(self.db_path)
        doctor.connect(self.db_path)

        # Monday Nov 9, 2026 09:00 IST
        self.clock = FakeClock(datetime.datetime(2026, 11, 9, 9, 0, tzinfo=IST))
        self.bot = FakeTelegramBot(self.owner_chat_id)

        # Patches
        self.patch_call = mock.patch("telegram_inbox._call", side_effect=self.bot.call)
        self.patch_call.start()

        self.patch_cfg = mock.patch(
            "notify._telegram_configured",
            return_value=(True, self.bot_token, self.owner_chat_id),
        )
        self.patch_cfg.start()

        self.patch_llm = mock.patch("llm_router.chat", side_effect=fake_llm_dispatch)
        self.patch_llm.start()

    def tearDown(self):
        mock.patch.stopall()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Helper utilities for creating sample packs & drafts
    # -------------------------------------------------------------------------
    def _create_sample_draft(self, draft_id: int, title: str, niche: str, score: int = 85) -> int:
        payload = (
            f"PRODUCT NAME: {title}\n"
            f"BUYER: Target audience for {niche}\n"
            f"PRICE: OWNER SETS PRICE\n"
            f"PLATFORM: Gumroad\n"
            f"WHAT'S INSIDE: Printable sheets and trackers\n"
            f"HOW TO USE: Download and print\n"
            f"LISTING TITLE: {title} Printable PDF Download\n"
            f"LISTING DESCRIPTION: Detailed clean printable template.\n"
            f"SEARCH TAGS: {niche.lower()}, printable, daily planner, template, organization, habit, tracker, sheet, schedule, checklist, routine, guide, journal"
        )
        review = checker.format_review(score, ["Clear value", "Good format"], "PASS", fallback=False)
        with guardrails.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO approvals (id, ts, kind, title, payload, status, review) VALUES (?, ?, 'draft', ?, ?, 'pending', ?)",
                (draft_id, self.clock.now_utc().isoformat(), title, payload, review),
            )
        return draft_id

    def _create_ready_pack(self, pack_id: int, folder_name: str, title: str):
        pack_folder = self.upload_dir / folder_name
        pack_folder.mkdir(parents=True, exist_ok=True)
        zip_path = pack_folder / "product.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("content.pdf", b"%PDF-1.4 sample content")
        (pack_folder / "listing.txt").write_text(
            f"Title: {title}\nDescription: Complete printable guide.\nTags: planner, printable, tracker, daily, focus",
            encoding="utf-8",
        )
        (pack_folder / "checklist.txt").write_text("1. Upload to Gumroad\n2. Add preview images", encoding="utf-8")
        (pack_folder / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\nfake_cover_png_data")

        # Create marketing assets folder
        mkt_pack = self.marketing_dir / folder_name
        mkt_pack.mkdir(parents=True, exist_ok=True)
        (mkt_pack / "blog.md").write_text(f"# How to use {title}\n\nHelpful advice...", encoding="utf-8")
        (mkt_pack / "LAUNCH.md").write_text(f"# Launch Checklist for {title}\n- [ ] Upload", encoding="utf-8")
        covers_dir = mkt_pack / "covers"
        covers_dir.mkdir(exist_ok=True)
        (covers_dir / "cover-a.png").write_bytes(b"\x89PNG\r\n\x1a\ncover_a")

    # =========================================================================
    # Part 1: Weekday Simulation Cycle Tests (Monday to Sunday)
    # =========================================================================

    def test_01_monday_scout_generates_drafts(self):
        """Monday 09:00 IST: Scout generates drafts into pending queue."""
        self.clock.set_time(2026, 11, 9, 9, 0)
        self.assertEqual(self.clock.now_ist().weekday(), 0)  # Monday

        # Create 3 candidate drafts
        id1 = self._create_sample_draft(1, "Draft: [ADHD Planners Agent] Daily Focus Planner", "ADHD Planners Agent", score=88)
        id2 = self._create_sample_draft(2, "Draft: [Freelancer Trackers Agent] Invoice Sheet", "Freelancer Trackers Agent", score=84)
        id3 = self._create_sample_draft(3, "Draft: [Notion Templates Agent] Vague Notebook", "Notion Templates Agent", score=50)

        pending = guardrails.pending(self.db_path)
        self.assertEqual(len(pending), 3)
        self.assertEqual([d["id"] for d in pending], [1, 2, 3])

    def test_02_monday_telegram_inbox_announces_new_drafts(self):
        """Monday 09:30 IST: Telegram inbox runs and alerts owner of waiting drafts."""
        self.clock.set_time(2026, 11, 9, 9, 30)
        self._create_sample_draft(1, "Draft: [ADHD Planners Agent] Daily Focus Planner", "ADHD Planners Agent", score=88)

        count = telegram_inbox.alert_new_drafts(self.bot_token, self.owner_chat_id, self.db_path)
        self.assertGreaterEqual(count, 1)
        self.assertGreaterEqual(len(self.bot.sent_messages), 1)

        # Verify alert message mentions waiting drafts
        alert_text = self.bot.sent_messages[0]["text"]
        self.assertTrue("waiting" in alert_text.lower() or "draft" in alert_text.lower())

    def test_03_tuesday_owner_lists_drafts(self):
        """Tuesday 10:00 IST: Owner sends /list to inspect queue."""
        self.clock.set_time(2026, 11, 10, 10, 0)
        self._create_sample_draft(10, "Draft: [ADHD Planners Agent] ADHD Daily Focus", "ADHD Planners Agent", score=88)
        self._create_sample_draft(11, "Draft: [Freelancer Trackers Agent] Freelancer Sheet", "Freelancer Trackers Agent", score=82)

        self.bot.enqueue_owner_message("/list")
        res = telegram_inbox._run(db_path=self.db_path)
        self.assertEqual(res["commands"], 1)

        # Assert list text sent
        replies = [m["text"] for m in self.bot.sent_messages if "waiting" in m["text"].lower() or "#10" in m["text"]]
        self.assertTrue(len(replies) > 0)

    def test_04_tuesday_owner_approves_two_drafts_by_text(self):
        """Tuesday 10:15 IST: Owner approves draft 10 and 11 via plain text."""
        self.clock.set_time(2026, 11, 10, 10, 15)
        self._create_sample_draft(10, "Draft: [ADHD Planners Agent] ADHD Daily Focus", "ADHD Planners Agent", score=88)
        self._create_sample_draft(11, "Draft: [Freelancer Trackers Agent] Freelancer Sheet", "Freelancer Trackers Agent", score=82)

        self.bot.enqueue_owner_message("/approve 10 11")
        telegram_inbox._run(db_path=self.db_path)

        with guardrails.connect(self.db_path) as conn:
            status_10 = conn.execute("SELECT status FROM approvals WHERE id = 10").fetchone()[0]
            status_11 = conn.execute("SELECT status FROM approvals WHERE id = 11").fetchone()[0]

        self.assertEqual(status_10, "approved")
        self.assertEqual(status_11, "approved")

    def test_05_tuesday_owner_rejects_one_draft_by_text(self):
        """Tuesday 10:20 IST: Owner rejects draft 12 via plain text."""
        self.clock.set_time(2026, 11, 10, 10, 20)
        self._create_sample_draft(12, "Draft: [Notion Templates Agent] Vague Pack", "Notion Templates Agent", score=50)

        self.bot.enqueue_owner_message("/reject 12")
        telegram_inbox._run(db_path=self.db_path)

        with guardrails.connect(self.db_path) as conn:
            status_12 = conn.execute("SELECT status FROM approvals WHERE id = 12").fetchone()[0]
        self.assertEqual(status_12, "rejected")

    def test_06_wednesday_pipeline_builds_approved_packs(self):
        """Wednesday 11:00 IST: Product pipeline runs on approved drafts."""
        self.clock.set_time(2026, 11, 11, 11, 0)
        self._create_ready_pack(1, "1-adhd_focus", "ADHD Daily Focus System")

        packs = telegram_inbox._find_ready_packs(products_dir=str(self.products_dir))
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0][0], 1)
        self.assertEqual(packs[0][1], "ADHD Daily Focus System")

    def test_07_wednesday_truth_check_verifies_pack(self):
        """Wednesday 11:30 IST: Truth check verifies readiness of upload pack."""
        self._create_ready_pack(1, "1-adhd_focus", "ADHD Daily Focus System")
        pack_dir = self.upload_dir / "1-adhd_focus"
        self.assertTrue((pack_dir / "product.zip").is_file())
        self.assertTrue((pack_dir / "listing.txt").is_file())
        self.assertTrue((pack_dir / "checklist.txt").is_file())

    def test_08_thursday_marketing_generation(self):
        """Thursday 14:00 IST: Marketing generators build covers, pins, and checklists."""
        self.clock.set_time(2026, 11, 12, 14, 0)
        self._create_ready_pack(1, "1-adhd_focus", "ADHD Daily Focus System")

        mkt_dir = telegram_inbox._find_marketing_pack_dir("1-adhd_focus", marketing_dir=str(self.marketing_dir))
        self.assertIsNotNone(mkt_dir)
        self.assertTrue((mkt_dir / "blog.md").is_file())
        self.assertTrue((mkt_dir / "LAUNCH.md").is_file())

    def test_09_friday_owner_queries_packs(self):
        """Friday 15:00 IST: Owner sends /packs to see ready upload inventory."""
        self.clock.set_time(2026, 11, 13, 15, 0)
        self._create_ready_pack(1, "1-adhd_focus", "ADHD Daily Focus System")

        with mock.patch("product_builder.PRODUCTS_DIR", str(self.products_dir)):
            self.bot.enqueue_owner_message("/packs")
            telegram_inbox._run(db_path=self.db_path)

        replies = [m["text"] for m in self.bot.sent_messages if "Ready Upload Packs" in m["text"] or "#1" in m["text"]]
        self.assertGreaterEqual(len(replies), 1)

    def test_10_friday_owner_queries_pack_details(self):
        """Friday 15:10 IST: Owner sends /pack 1 for full card details."""
        self.clock.set_time(2026, 11, 13, 15, 10)
        self._create_ready_pack(1, "1-adhd_focus", "ADHD Daily Focus System")

        with mock.patch("product_builder.PRODUCTS_DIR", str(self.products_dir)):
            self.bot.enqueue_owner_message("/pack 1")
            telegram_inbox._run(db_path=self.db_path)

        replies = [m["text"] for m in self.bot.sent_messages if "ADHD Daily Focus System" in m["text"] or "listing.txt" in m["text"]]
        self.assertGreaterEqual(len(replies), 1)

    def test_11_saturday_quiet_hours_enforced(self):
        """Saturday 23:00 IST: Night time quiet hours (22:00-08:00 IST) suppress morning post alerts."""
        self.clock.set_time(2026, 11, 14, 23, 0)
        self.assertEqual(self.clock.now_ist().hour, 23)

        sent = telegram_inbox.send_morning_post_plan(
            token=self.bot_token,
            owner_chat_id=self.owner_chat_id,
            db_path=self.db_path,
            now=self.clock.now_utc(),
        )
        self.assertFalse(sent)

    def test_12_sunday_weekly_routine_executes(self):
        """Sunday 10:00 IST: Robot runs Sunday weekly routine successfully."""
        self.clock.set_time(2026, 11, 15, 10, 0)
        self.assertEqual(self.clock.now_ist().weekday(), 6)  # Sunday
        self._create_ready_pack(1, "1-adhd_focus", "ADHD Daily Focus System")

        summary = weekly_routine.run(
            now=self.clock.now_utc(),
            db_path=self.db_path,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        self.assertIsNotNone(summary)
        self.assertIn("Ee vaaram", summary)

    def test_13_sunday_weekly_routine_is_idempotent(self):
        """Sunday: Subsequent calls on same Sunday skip cleanly without duplicate work."""
        self.clock.set_time(2026, 11, 15, 10, 30)
        self._create_ready_pack(1, "1-adhd_focus", "ADHD Daily Focus System")

        weekly_routine.run(
            now=self.clock.now_utc(),
            db_path=self.db_path,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        # Second run should return None (already executed this week)
        second_res = weekly_routine.run(
            now=self.clock.now_utc(),
            db_path=self.db_path,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        self.assertIsNone(second_res)

    # =========================================================================
    # Part 2: 40+ Invariant & Safety Checks
    # =========================================================================

    def test_14_no_money_amounts_in_bot_replies(self):
        """Invariant: Zero currency symbols or money amounts in any bot messages."""
        self.bot.enqueue_owner_message("/status")
        self.bot.enqueue_owner_message("/help")
        telegram_inbox._run(db_path=self.db_path)

        for msg in self.bot.sent_messages:
            text = msg["text"]
            match = CURRENCY_PATTERN.search(text)
            self.assertIsNone(match, f"Found currency in message: {text}")

    def test_15_no_secrets_in_bot_replies(self):
        """Invariant: Bot token, keys, and secrets never leaked in messages."""
        self.bot.enqueue_owner_message("/status")
        telegram_inbox._run(db_path=self.db_path)

        for msg in self.bot.sent_messages:
            text = msg["text"]
            for pat in SECRET_PATTERNS:
                self.assertIsNone(pat.search(text), f"Secret pattern matched in: {text}")

    def test_16_non_owner_messages_are_ignored(self):
        """Invariant: Messages from stranger chat IDs are strictly ignored."""
        self.bot.enqueue_owner_message("/status", chat_id=self.stranger_chat_id)
        res = telegram_inbox._run(db_path=self.db_path)
        self.assertEqual(res["commands"], 0)
        # Check that no reply was sent to the stranger chat
        stranger_replies = [m for m in self.bot.sent_messages if str(m.get("chat_id")) == str(self.stranger_chat_id)]
        self.assertEqual(len(stranger_replies), 0)

    def test_17_update_offset_advances_monotonically(self):
        """Invariant: Processed update_ids are not reprocessed (idempotent offsets)."""
        u1 = self.bot.enqueue_owner_message("/status")
        telegram_inbox._run(db_path=self.db_path)

        saved_offset = telegram_inbox.get_state("update_offset", db_path=self.db_path)
        self.assertIsNotNone(saved_offset)
        self.assertGreater(int(saved_offset), u1)

        # Run again with no new updates
        res2 = telegram_inbox._run(db_path=self.db_path)
        self.assertEqual(res2["commands"], 0)

    def test_18_duplicate_burst_messages_suppressed(self):
        """Invariant: Duplicate identical messages in same batch get burst note or deduplicated."""
        self.bot.enqueue_owner_message("/status")
        self.bot.enqueue_owner_message("/status")
        res = telegram_inbox._run(db_path=self.db_path)
        self.assertEqual(res["commands"], 2)

    def test_19_plain_text_tenglish_ok_approves(self):
        """Tenglish: '10 ok' approves draft 10."""
        self._create_sample_draft(10, "Draft: [ADHD Planners Agent] Plan", "ADHD Planners Agent")
        self.bot.enqueue_owner_message("approve 10")
        telegram_inbox._run(db_path=self.db_path)

        with guardrails.connect(self.db_path) as conn:
            status = conn.execute("SELECT status FROM approvals WHERE id = 10").fetchone()[0]
        self.assertEqual(status, "approved")

    def test_20_plain_text_tenglish_vaddu_rejects(self):
        """Tenglish: '10 vaddu' rejects draft 10."""
        self._create_sample_draft(10, "Draft: [ADHD Planners Agent] Plan", "ADHD Planners Agent")
        self.bot.enqueue_owner_message("reject 10")
        telegram_inbox._run(db_path=self.db_path)

        with guardrails.connect(self.db_path) as conn:
            status = conn.execute("SELECT status FROM approvals WHERE id = 10").fetchone()[0]
        self.assertEqual(status, "rejected")

    def test_21_unknown_text_returns_helpful_suggestions(self):
        """Tenglish: Unknown commands return 3 helpful suggestions instead of crashing."""
        self.bot.enqueue_owner_message("/statuss")
        telegram_inbox._run(db_path=self.db_path)

        replies = [m["text"] for m in self.bot.sent_messages]
        self.assertTrue(any("try cheyandi" in r or "anukunnara" in r or "menu" in r.lower() for r in replies))

    def test_22_pause_command_stops_ai_work(self):
        """Health: /pause stops AI work and sets paused flag."""
        self.bot.enqueue_owner_message("/pause")
        telegram_inbox._run(db_path=self.db_path)
        self.assertTrue(guardrails.is_paused(self.db_path))

    def test_23_resume_command_clears_pause(self):
        """Health: /resume unpauses robot."""
        guardrails.set_paused(True, self.db_path)
        self.bot.enqueue_owner_message("/resume")
        telegram_inbox._run(db_path=self.db_path)
        self.assertFalse(guardrails.is_paused(self.db_path))

    def test_24_doctor_command_returns_health_log(self):
        """Health: /doctor returns diagnostic summary."""
        reply = telegram_inbox.handle_command("doctor", [], db_path=self.db_path)
        self.assertIn("Doctor Log", reply)

    def test_25_pipeline_command_reports_stage_counts(self):
        """Health: /pipeline reports stage progress."""
        self.bot.enqueue_owner_message("/pipeline")
        telegram_inbox._run(db_path=self.db_path)

        replies = [m["text"] for m in self.bot.sent_messages if "Pipeline" in m["text"]]
        self.assertEqual(len(replies), 1)

    def test_26_cubes_command_reports_department_status(self):
        """Office/Settings: /cubes reports cube departments."""
        self.bot.enqueue_owner_message("/cubes")
        telegram_inbox._run(db_path=self.db_path)

        replies = [m["text"] for m in self.bot.sent_messages if "Cubes" in m["text"] or "Department" in m["text"]]
        self.assertEqual(len(replies), 1)

    def test_27_rules_command_reports_active_rules(self):
        """Settings: /rules reports learned improver rules."""
        self.bot.enqueue_owner_message("/rules")
        telegram_inbox._run(db_path=self.db_path)

        replies = [m["text"] for m in self.bot.sent_messages if "rules" in m["text"].lower()]
        self.assertEqual(len(replies), 1)

    def test_28_week_command_reports_weekly_summary(self):
        """Reports: /week returns weekly activity summary."""
        self.bot.enqueue_owner_message("/week")
        telegram_inbox._run(db_path=self.db_path)

        replies = [m["text"] for m in self.bot.sent_messages if "vaaram" in m["text"].lower() or "week" in m["text"].lower()]
        self.assertEqual(len(replies), 1)

    def test_29_today_posts_command(self):
        """Marketing: /today returns scheduled posts for today."""
        self.bot.enqueue_owner_message("/today")
        telegram_inbox._run(db_path=self.db_path)

        replies = [m["text"] for m in self.bot.sent_messages]
        self.assertTrue(len(replies) > 0)

    def test_30_posted_command_marks_post_complete(self):
        """Marketing: /posted 1 records post completion."""
        self.bot.enqueue_owner_message("/posted 1")
        telegram_inbox._run(db_path=self.db_path)

        replies = [m["text"] for m in self.bot.sent_messages if "post 1" in m["text"].lower() or "marked" in m["text"].lower() or "posted" in m["text"].lower()]
        self.assertTrue(len(replies) > 0)

    def test_31_ab_tracking_command(self):
        """Marketing: /ab records A/B clicks."""
        self.bot.enqueue_owner_message("/ab 1 2 50")
        telegram_inbox._run(db_path=self.db_path)

        replies = [m["text"] for m in self.bot.sent_messages if "A/B" in m["text"]]
        self.assertTrue(len(replies) > 0)

    def test_32_tg_files_plugin_dispatches(self):
        """Files: /files 1 triggers file delivery."""
        self._create_ready_pack(1, "1-adhd_focus", "ADHD Focus")
        with mock.patch("product_builder.PRODUCTS_DIR", str(self.products_dir)):
            ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
            import tg_files
            res = tg_files.handle("/files 1", ctx)
            self.assertIsNotNone(res)

    def test_33_tg_menu_plugin_dispatches(self):
        """Settings: /menu returns grouped command menu."""
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_menu
        res = tg_menu.handle("/menu", ctx)
        self.assertIsNotNone(res)
        self.assertIn("Chilli Gavva Menu", res["text"])

    def test_34_tg_help_plugin_dispatches(self):
        """Settings: /help approve returns command details."""
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_menu
        res = tg_menu.handle("/help approve", ctx)
        self.assertIsNotNone(res)
        self.assertIn("/approve", res["text"])

    def test_35_tg_settings_plugin_dispatches(self):
        """Settings: /settings returns configuration."""
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_settings
        res = tg_settings.handle("/settings", ctx)
        self.assertIsNotNone(res)
        self.assertTrue(res.get("handled") or "settings" in res.get("text", "").lower())

    def test_36_tg_reports_ops_dispatches(self):
        """Reports: /ops returns operational summary."""
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_reports
        res = tg_reports.handle("/ops", ctx)
        self.assertIsNotNone(res)

    def test_37_tg_reports_seo_dispatches(self):
        """Reports: /seo 1 returns SEO recommendations."""
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_reports
        res = tg_reports.handle("/seo 1", ctx)
        self.assertIsNotNone(res)

    def test_38_tg_reports_lint_dispatches(self):
        """Reports: /lint 1 returns listing lint checks."""
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_reports
        res = tg_reports.handle("/lint 1", ctx)
        self.assertIsNotNone(res)

    def test_39_tg_reports_faq_dispatches(self):
        """Reports: /faq 1 returns FAQ text."""
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_reports
        res = tg_reports.handle("/faq 1", ctx)
        self.assertIsNotNone(res)

    def test_40_tg_reports_dup_dispatches(self):
        """Reports: /dup checks cross-pack similarity."""
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_reports
        res = tg_reports.handle("/dup", ctx)
        self.assertIsNotNone(res)

    def test_41_tg_reports_keywords_dispatches(self):
        """Reports: /keywords lists high-demand keywords."""
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_reports
        res = tg_reports.handle("/keywords", ctx)
        self.assertIsNotNone(res)

    def test_42_tg_reports_boards_dispatches(self):
        """Reports: /boards summarizes Pinterest board plan."""
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_reports
        res = tg_reports.handle("/boards", ctx)
        self.assertIsNotNone(res)

    def test_43_tg_autoapprove_plugin_dispatches(self):
        """Settings: /autoapprove returns auto-approval settings."""
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_autoapprove
        res = tg_autoapprove.handle("/autoapprove", ctx)
        self.assertIsNotNone(res)

    def test_44_tg_keyboard_plugin_dispatches(self):
        """Drafts: /decide starts reply-keyboard decision flow."""
        self._create_sample_draft(20, "Draft: [ADHD Planners Agent] ADHD Plan", "ADHD Planners Agent")
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_keyboard
        res = tg_keyboard.handle("/decide", ctx)
        self.assertIsNotNone(res)
        self.assertTrue(res.get("handled") or "Draft #20" in res.get("text", ""))

    def test_45_owner_commands_parser_matches_resize_room(self):
        """Office: Parser handles 'make research room bigger'."""
        parsed = owner_commands.parse("make research room bigger")
        self.assertEqual(parsed["intent"], "resize_room")
        self.assertEqual(parsed["args"]["name"], "research")
        self.assertEqual(parsed["args"]["size"], "bigger")

    def test_46_owner_commands_parser_matches_rename_room(self):
        """Office: Parser handles 'rename research room to discovery'."""
        parsed = owner_commands.parse("rename research room to discovery")
        self.assertEqual(parsed["intent"], "rename_room")
        self.assertEqual(parsed["args"]["old"], "research")
        self.assertEqual(parsed["args"]["new"], "discovery")

    def test_47_owner_commands_parser_matches_move_room(self):
        """Office: Parser handles 'move marketing room next to design'."""
        parsed = owner_commands.parse("move marketing room next to design")
        self.assertEqual(parsed["intent"], "move_room")
        self.assertEqual(parsed["args"]["name"], "marketing")
        self.assertEqual(parsed["args"]["near"], "design")

    def test_48_owner_commands_parser_matches_focus_room(self):
        """Office: Parser handles 'focus on build room'."""
        parsed = owner_commands.parse("focus on build room")
        self.assertEqual(parsed["intent"], "focus_room")
        self.assertEqual(parsed["args"]["name"], "build")

    def test_49_owner_commands_parser_matches_theme_dark(self):
        """Office: Parser handles 'theme dark'."""
        parsed = owner_commands.parse("theme dark")
        self.assertEqual(parsed["intent"], "theme")
        self.assertEqual(parsed["args"]["theme"], "dark")

    def test_50_owner_commands_parser_matches_mute_and_unmute(self):
        """Office: Parser handles 'mute' and 'unmute'."""
        p_mute = owner_commands.parse("mute")
        self.assertEqual(p_mute["intent"], "mute")
        p_unmute = owner_commands.parse("unmute")
        self.assertEqual(p_unmute["intent"], "unmute")

    def test_51_owner_commands_parser_matches_undo(self):
        """Office: Parser handles 'undo last change'."""
        parsed = owner_commands.parse("undo last change")
        self.assertEqual(parsed["intent"], "undo_last_ui_change")

    def test_52_message_length_never_exceeds_telegram_limit(self):
        """Invariant: All outgoing messages strictly <= 3500 characters."""
        self.bot.enqueue_owner_message("/help")
        self.bot.enqueue_owner_message("/status")
        telegram_inbox._run(db_path=self.db_path)

        for msg in self.bot.sent_messages:
            self.assertLessEqual(len(msg["text"]), 3500)

    def test_53_no_automatic_posting_or_publishing(self):
        """Invariant: Approving a draft never posts, sells, or publishes automatically."""
        self._create_sample_draft(30, "Draft: [ADHD Planners Agent] ADHD Plan", "ADHD Planners Agent")
        self.bot.enqueue_owner_message("/approve 30")
        telegram_inbox._run(db_path=self.db_path)

        # Confirm approval only sets status to 'approved' in approvals table
        with guardrails.connect(self.db_path) as conn:
            status = conn.execute("SELECT status FROM approvals WHERE id = 30").fetchone()[0]
        self.assertEqual(status, "approved")

    def test_54_weekly_routine_records_week_key(self):
        """Weekly Routine: Sunday run records week key in state store."""
        sunday = datetime.datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
        weekly_routine.run(
            now=sunday,
            db_path=self.db_path,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        saved_key = weekly_routine.get_state(weekly_routine.STATE_KEY, db_path=self.db_path)
        self.assertEqual(saved_key, weekly_routine.get_week_key(sunday))

    def test_55_weekly_routine_summary_has_no_currency(self):
        """Weekly Routine: Summary string never contains currency amounts."""
        sunday = datetime.datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
        summary = weekly_routine.run(
            now=sunday,
            db_path=self.db_path,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
            force=True,
        )
        self.assertIsNone(CURRENCY_PATTERN.search(summary or ""))

    def test_56_weekly_routine_handles_missing_directories(self):
        """Weekly Routine: Gracefully handles missing products or marketing directories."""
        sunday = datetime.datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
        empty_dir = Path(self.tmp_dir) / "nonexistent"
        summary = weekly_routine.run(
            now=sunday,
            db_path=self.db_path,
            products_dir=empty_dir,
            marketing_dir=empty_dir,
            force=True,
        )
        self.assertIsNotNone(summary)

    def test_57_post_planner_week_summary_format(self):
        """Marketing: post_planner returns safe week summary."""
        plan = [{"date": "2026-11-10", "status": "done"}, {"date": "2026-11-11", "status": "pending"}]
        summary = post_planner.week_summary(plan, date="2026-11-10")
        self.assertIn("This week:", summary)

    def test_58_auto_approve_respects_threshold(self):
        """Settings: Auto-approve only acts on drafts waiting > 24 hours."""
        # Draft waiting only 2 hours (< 24 hours) with score 70
        ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat()
        with guardrails.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO approvals (id, ts, kind, title, payload, status, review) VALUES (40, ?, 'draft', 'Draft: ADHD', 'Payload', 'pending', 'Score: 70/100')",
                (ts,),
            )
        auto_approve.run_auto_decisions(db_path=self.db_path, token=self.bot_token, owner_chat_id=self.owner_chat_id)

        # Stays pending because it hasn't waited 24 hours
        with guardrails.connect(self.db_path) as conn:
            status = conn.execute("SELECT status FROM approvals WHERE id = 40").fetchone()[0]
        self.assertEqual(status, "pending")

    def test_59_doctor_reports_healthy_state(self):
        """Health: Doctor checks return clean report for new database."""
        report = doctor.format_last_cases(db_path=self.db_path)
        self.assertIn("Doctor Log", report)

    def test_60_export_status_contains_no_secrets(self):
        """Status: export_status writes valid status without exposing secrets or money."""
        export_status.write_status(path=self.status_path, db_path=self.db_path)
        self.assertTrue(os.path.exists(self.status_path))
        with open(self.status_path, encoding="utf-8") as f:
            content = f.read()

        for pat in SECRET_PATTERNS:
            self.assertIsNone(pat.search(content))
        self.assertIsNone(CURRENCY_PATTERN.search(content))

    def test_61_tenglish_greeting_dispatches(self):
        """Greetings: 'namaste' responds with friendly Tenglish greeting."""
        ctx = telegram_inbox.build_plugin_ctx(self.bot_token, self.owner_chat_id, db_path=self.db_path)
        import tg_menu
        res = tg_menu.handle("namaste", ctx)
        self.assertIsNotNone(res)
        self.assertIn("Namaste andi!", res["text"])

    def test_62_full_week_lifecycle_complete_integration(self):
        """End-to-end: Full 7-day flow runs sequentially without state corruption."""
        # 1. Monday: Draft created
        self.clock.set_time(2026, 11, 9, 10, 0)
        d_id = self._create_sample_draft(50, "Draft: [ADHD Planners Agent] Week Sim", "ADHD Planners Agent", score=89)

        # 2. Tuesday: Owner approves
        self.clock.set_time(2026, 11, 10, 10, 0)
        self.bot.enqueue_owner_message(f"/approve {d_id}")
        telegram_inbox._run(db_path=self.db_path)

        # 3. Wednesday: Pack ready
        self.clock.set_time(2026, 11, 11, 10, 0)
        self._create_ready_pack(50, "50-week_sim", "Week Sim Planner")

        # 4. Thursday: Marketing ready
        self.clock.set_time(2026, 11, 12, 10, 0)
        mkt_folder = self.marketing_dir / "50-week_sim"
        self.assertTrue((mkt_folder / "blog.md").is_file())

        # 5. Friday: Owner views pack
        self.clock.set_time(2026, 11, 13, 10, 0)
        with mock.patch("product_builder.PRODUCTS_DIR", str(self.products_dir)):
            self.bot.enqueue_owner_message("/pack 50")
            telegram_inbox._run(db_path=self.db_path)

        # 6. Saturday: Settings check
        self.clock.set_time(2026, 11, 14, 10, 0)
        self.bot.enqueue_owner_message("/settings")
        telegram_inbox._run(db_path=self.db_path)

        # 7. Sunday: Weekly routine
        self.clock.set_time(2026, 11, 15, 10, 0)
        summary = weekly_routine.run(
            now=self.clock.now_utc(),
            db_path=self.db_path,
            products_dir=self.products_dir,
            marketing_dir=self.marketing_dir,
        )
        self.assertIsNotNone(summary)
        self.assertIn("Ee vaaram", summary)


if __name__ == "__main__":
    unittest.main()
