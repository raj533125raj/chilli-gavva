"""tests/test_v1c_keyboard.py - Comprehensive tests for tg_keyboard reply-keyboard plugin.

Covers:
- Plugin interface (COMMANDS metadata, handle signature, return shapes)
- /decide command and trigger words
- Reply keyboard generation ('✅ <id>', '❌ <id>', '👁 <id>', '⏭ Next', '🛑 Stop')
- Card formatting with checker score & 3-line summary
- Approval flow & idempotency
- Rejection flow & one-tap reason keyboard
- Custom rejection reasons & cancel rejection
- Next draft navigation & wrapping
- Plain text shortcuts ('approve 91', '91 ok', '91 vaddu', 'reject 91')
- 10-minute idle session timeout
- '🛑 Stop' removing keyboard
- Empty queue handling & keyboard removal
- Audit logging in state
- Owner-only checks
- Robustness against malformed inputs and non-matching messages
"""

import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import guardrails
import tg_keyboard


class TestV1CKeyboardPlugin(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* and runner env
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        clean_env["CHILLI_TEST"] = "1"
        patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.db_path = os.path.join(self.tmp_dir.name, "test_agent_data.db")

        # Initialize test database tables
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                kind TEXT,
                title TEXT,
                payload TEXT,
                status TEXT DEFAULT 'pending',
                review TEXT,
                decided_ts TEXT,
                reason TEXT
            )"""
        )
        conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

        self.sent_messages = []
        self.sent_keyboards = []
        self.state_store = {}
        self.now = datetime.datetime(2025, 5, 10, 12, 0, 0)
        self.owner_chat_id = "12345"

        # Mock notify owner chat
        notify_patcher = mock.patch("notify._telegram_configured", return_value=(True, "fake_tok", self.owner_chat_id))
        notify_patcher.start()
        self.addCleanup(notify_patcher.stop)

    def _make_ctx(self, chat_id=None, now=None):
        def send_text(text, keyboard=None):
            self.sent_messages.append(text)
            self.sent_keyboards.append(keyboard)
            return True

        def state_get(key, default=None):
            return self.state_store.get(key, default)

        def state_set(key, value):
            self.state_store[key] = value

        return {
            "db_path": self.db_path,
            "chat_id": chat_id or self.owner_chat_id,
            "now": now or self.now,
            "send_text": send_text,
            "send_document": lambda *a, **kw: True,
            "send_photo": lambda *a, **kw: True,
            "send_media_group": lambda *a, **kw: True,
            "state_get": state_get,
            "state_set": state_set,
        }

    def _create_draft(self, title="Test Draft", review="score 85/100\n- Great concept\n- Fits seasonal trend", status="pending"):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO approvals (ts, kind, title, payload, status, review) VALUES (?, ?, ?, ?, ?, ?)",
            (self.now.isoformat(), "digital_pack", title, "Draft content details...", status, review),
        )
        conn.commit()
        draft_id = cur.lastrowid
        conn.close()
        return draft_id

    def _get_approval_reason(self, draft_id):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT reason FROM approvals WHERE id = ?", (draft_id,)).fetchone()
        conn.close()
        return row[0] if row else None

    # 1. Plugin Interface tests
    def test_plugin_metadata(self):
        self.assertTrue(hasattr(tg_keyboard, "COMMANDS"))
        self.assertIsInstance(tg_keyboard.COMMANDS, list)
        self.assertGreater(len(tg_keyboard.COMMANDS), 0)
        cmd_def = tg_keyboard.COMMANDS[0]
        self.assertEqual(cmd_def["cmd"], "/decide")
        self.assertIn("decide", cmd_def["words"])
        self.assertIn("help_en", cmd_def)
        self.assertIn("help_te", cmd_def)

    def test_handle_function_signature(self):
        self.assertTrue(callable(tg_keyboard.handle))

    def test_handle_non_string_returns_none(self):
        ctx = self._make_ctx()
        self.assertIsNone(tg_keyboard.handle(None, ctx))
        self.assertIsNone(tg_keyboard.handle(1234, ctx))
        self.assertIsNone(tg_keyboard.handle(["list"], ctx))

    def test_handle_empty_string_returns_none(self):
        ctx = self._make_ctx()
        self.assertIsNone(tg_keyboard.handle("", ctx))
        self.assertIsNone(tg_keyboard.handle("   ", ctx))

    def test_handle_unrelated_text_returns_none(self):
        ctx = self._make_ctx()
        self.assertIsNone(tg_keyboard.handle("Hello world", ctx))
        self.assertIsNone(tg_keyboard.handle("/status", ctx))
        self.assertIsNone(tg_keyboard.handle("weather today", ctx))

    # 2. Non-owner rejection
    def test_non_owner_ignored(self):
        ctx = self._make_ctx(chat_id="99" + "99999")
        res = tg_keyboard.handle("/decide", ctx)
        self.assertIsNone(res)
        self.assertEqual(len(self.sent_messages), 0)

    # 3. Empty pending queue
    def test_decide_empty_queue(self):
        ctx = self._make_ctx()
        res = tg_keyboard.handle("/decide", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn("No drafts waiting", self.sent_messages[0])
        self.assertEqual(self.sent_keyboards[0], {"remove_keyboard": True})

    # 4. /decide command starts decision mode
    def test_decide_with_pending_drafts(self):
        d1 = self._create_draft("Daily Habit Tracker")
        d2 = self._create_draft("Weekly Budget Sheet")
        ctx = self._make_ctx()
        res = tg_keyboard.handle("/decide", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn("Drafts decision mode on:", self.sent_messages[0])
        self.assertIn(f"#{d1} Daily Habit Tracker", self.sent_messages[0])
        self.assertIn("Checker: 85/100", self.sent_messages[0])
        # Keyboard check
        kb = self.sent_keyboards[0]
        self.assertEqual(kb, [
            [f"✅ {d1}", f"❌ {d1}", f"👁 {d1}"],
            ["⏭ Next", "🛑 Stop"],
        ])
        # State updated
        active_sess = json.loads(self.state_store[tg_keyboard.KEY_ACTIVE_SESSION])
        self.assertEqual(active_sess["current_draft_id"], d1)

    # 5. Trigger words for decide
    def test_decide_trigger_words(self):
        words = ["decide", "nirnayinchi", "drafts decide", "decide drafts", "/decide"]
        for word in words:
            self.sent_messages.clear()
            self.sent_keyboards.clear()
            self.state_store.clear()
            # Clear pending table
            conn = sqlite3.connect(self.db_path)
            conn.execute("DELETE FROM approvals")
            conn.commit()
            conn.close()

            d = self._create_draft("Yoga Journal")
            ctx = self._make_ctx()
            res = tg_keyboard.handle(word, ctx)
            self.assertEqual(res, {"handled": True})
            self.assertEqual(len(self.sent_messages), 1)
            self.assertIn(f"#{d} Yoga Journal", self.sent_messages[0])

    # 6. '👁 <id>' View draft details
    def test_view_draft_button(self):
        d = self._create_draft("Student Study Planner")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        res = tg_keyboard.handle(f"👁 {d}", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn(f"#{d}", self.sent_messages[0])
        self.assertIn("Draft content details...", self.sent_messages[0])
        # Keyboard remains attached
        self.assertEqual(self.sent_keyboards[0], [
            [f"✅ {d}", f"❌ {d}", f"👁 {d}"],
            ["⏭ Next", "🛑 Stop"],
        ])

    # 7. '✅ <id>' Approve draft button
    def test_approve_button_advances_to_next(self):
        d1 = self._create_draft("Draft 1")
        d2 = self._create_draft("Draft 2")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        res = tg_keyboard.handle(f"✅ {d1}", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn(f"Approved ✓ #{d1}", self.sent_messages[0])
        self.assertIn(f"#{d2} Draft 2", self.sent_messages[0])
        # Keyboard now set for d2
        self.assertEqual(self.sent_keyboards[0], [
            [f"✅ {d2}", f"❌ {d2}", f"👁 {d2}"],
            ["⏭ Next", "🛑 Stop"],
        ])
        # Verify in DB
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "approved")

    # 8. Approve the last remaining draft finishes session
    def test_approve_last_draft_finishes_queue(self):
        d1 = self._create_draft("Final Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        res = tg_keyboard.handle(f"✅ {d1}", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn(f"Approved ✓ #{d1}", self.sent_messages[0])
        self.assertIn("Waiting queue empty ga undi. ✅", self.sent_messages[0])
        self.assertEqual(self.sent_keyboards[0], {"remove_keyboard": True})
        self.assertEqual(self.state_store.get(tg_keyboard.KEY_ACTIVE_SESSION), "")

    # 9. '❌ <id>' Reject draft button asks for reason
    def test_reject_button_prompts_reasons(self):
        d1 = self._create_draft("Draft to Reject")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        res = tg_keyboard.handle(f"❌ {d1}", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn(f"Draft #{d1} ni reject chestunnaru. Reason emiti?", self.sent_messages[0])
        kb = self.sent_keyboards[0]
        self.assertEqual(kb, [
            ["Too generic", "Duplicate"],
            ["Weak niche", "Promises too much"],
            ["Other", f"↩️ Cancel #{d1}"],
        ])
        # State stores rejecting draft
        rej_state = json.loads(self.state_store[tg_keyboard.KEY_REJECTING_DRAFT])
        self.assertEqual(rej_state["draft_id"], d1)

    # 10. Selecting a reject reason completes rejection and shows next draft
    def test_select_reject_reason(self):
        d1 = self._create_draft("Reject Me")
        d2 = self._create_draft("Next In Line")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        tg_keyboard.handle(f"❌ {d1}", ctx)
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        res = tg_keyboard.handle("Too generic", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn(f"Rejected: #{d1} (Too generic)", self.sent_messages[0])
        self.assertIn(f"#{d2} Next In Line", self.sent_messages[0])
        # Keyboard points to d2
        self.assertEqual(self.sent_keyboards[0], [
            [f"✅ {d2}", f"❌ {d2}", f"👁 {d2}"],
            ["⏭ Next", "🛑 Stop"],
        ])
        # DB updated
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "rejected")
        self.assertEqual(self._get_approval_reason(d1), "Too generic")

    # 11. Reject with "Duplicate"
    def test_select_reject_duplicate(self):
        d1 = self._create_draft("Dup Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        tg_keyboard.handle(f"❌ {d1}", ctx)
        self.sent_messages.clear()
        res = tg_keyboard.handle("Duplicate", ctx)
        self.assertEqual(res, {"handled": True})
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "rejected")
        self.assertEqual(self._get_approval_reason(d1), "Duplicate")

    # 12. Reject with "Weak niche"
    def test_select_reject_weak_niche(self):
        d1 = self._create_draft("Weak Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        tg_keyboard.handle(f"❌ {d1}", ctx)
        res = tg_keyboard.handle("Weak niche", ctx)
        self.assertEqual(res, {"handled": True})
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "rejected")
        self.assertEqual(self._get_approval_reason(d1), "Weak niche")

    # 13. Reject with "Promises too much"
    def test_select_reject_promises_too_much(self):
        d1 = self._create_draft("Overpromise Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        tg_keyboard.handle(f"❌ {d1}", ctx)
        res = tg_keyboard.handle("Promises too much", ctx)
        self.assertEqual(res, {"handled": True})
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "rejected")
        self.assertEqual(self._get_approval_reason(d1), "Promises too much")

    # 14. Reject with "Other"
    def test_select_reject_other(self):
        d1 = self._create_draft("Other Reason Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        tg_keyboard.handle(f"❌ {d1}", ctx)
        res = tg_keyboard.handle("Other", ctx)
        self.assertEqual(res, {"handled": True})
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "rejected")
        self.assertEqual(self._get_approval_reason(d1), "Other")

    # 15. Custom reject reason text
    def test_custom_reject_reason(self):
        d1 = self._create_draft("Custom Reason Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        tg_keyboard.handle(f"❌ {d1}", ctx)
        res = tg_keyboard.handle("not fitting Q4 seasonal themes", ctx)
        self.assertEqual(res, {"handled": True})
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "rejected")

    # 16. Cancel rejection button
    def test_cancel_rejection(self):
        d1 = self._create_draft("Do Not Reject")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        tg_keyboard.handle(f"❌ {d1}", ctx)
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        res = tg_keyboard.handle(f"↩️ Cancel #{d1}", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn("Reject cancel aindi.", self.sent_messages[0])
        self.assertIn(f"#{d1} Do Not Reject", self.sent_messages[0])
        # Keyboard restored to d1 decide
        self.assertEqual(self.sent_keyboards[0], [
            [f"✅ {d1}", f"❌ {d1}", f"👁 {d1}"],
            ["⏭ Next", "🛑 Stop"],
        ])
        # Verify still pending
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "pending")

    # 17. '⏭ Next' navigation skips to next draft
    def test_next_button_navigation(self):
        d1 = self._create_draft("Draft Alpha")
        d2 = self._create_draft("Draft Beta")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        res = tg_keyboard.handle("⏭ Next", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn("Next draft:", self.sent_messages[0])
        self.assertIn(f"#{d2} Draft Beta", self.sent_messages[0])
        self.assertEqual(self.sent_keyboards[0], [
            [f"✅ {d2}", f"❌ {d2}", f"👁 {d2}"],
            ["⏭ Next", "🛑 Stop"],
        ])

    # 18. '⏭ Next' at end of list wraps around
    def test_next_button_wraps_around(self):
        d1 = self._create_draft("Draft One")
        d2 = self._create_draft("Draft Two")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)  # on d1
        tg_keyboard.handle("⏭ Next", ctx)  # on d2
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        res = tg_keyboard.handle("⏭ Next", ctx)  # wraps to d1
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"#{d1} Draft One", self.sent_messages[0])

    # 19. '🛑 Stop' stops decide session and removes keyboard
    def test_stop_button(self):
        d1 = self._create_draft("Draft Stop")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        res = tg_keyboard.handle("🛑 Stop", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn("Decide session aagipoindi. Keyboard removed. 🛑", self.sent_messages[0])
        self.assertEqual(self.sent_keyboards[0], {"remove_keyboard": True})
        self.assertEqual(self.state_store.get(tg_keyboard.KEY_ACTIVE_SESSION), "")

    # 20. Alternative stop words
    def test_alternative_stop_words(self):
        for stop_word in ["/stop", "stop decide", "decide stop"]:
            d1 = self._create_draft("Draft Test Stop")
            ctx = self._make_ctx()
            tg_keyboard.handle("/decide", ctx)
            self.sent_messages.clear()
            self.sent_keyboards.clear()

            res = tg_keyboard.handle(stop_word, ctx)
            self.assertEqual(res, {"handled": True})
            self.assertIn("Decide session aagipoindi.", self.sent_messages[0])
            self.assertEqual(self.sent_keyboards[0], {"remove_keyboard": True})

    # 21. Plain text shortcut: 'approve 91'
    def test_plain_approve_num(self):
        d1 = self._create_draft("Plain Approve Draft")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"approve {d1}", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn(f"Approved ✓ #{d1}", self.sent_messages[0])
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "approved")

    # 22. Plain text shortcut: '91 ok'
    def test_plain_num_ok(self):
        d1 = self._create_draft("Num OK Draft")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"{d1} ok", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"Approved ✓ #{d1}", self.sent_messages[0])
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "approved")

    # 23. Plain text shortcut: '91 approve'
    def test_plain_num_approve(self):
        d1 = self._create_draft("Num Approve Draft")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"{d1} approve", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"Approved ✓ #{d1}", self.sent_messages[0])

    # 24. Plain text shortcut: 'ok 91'
    def test_plain_ok_num(self):
        d1 = self._create_draft("OK Num Draft")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"ok {d1}", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"Approved ✓ #{d1}", self.sent_messages[0])

    # 25. Plain text shortcut: '91 vaddu'
    def test_plain_num_vaddu_prompts_reason(self):
        d1 = self._create_draft("Num Vaddu Draft")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"{d1} vaddu", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"Draft #{d1} ni reject chestunnaru. Reason emiti?", self.sent_messages[0])
        self.assertEqual(self.sent_keyboards[0], [
            ["Too generic", "Duplicate"],
            ["Weak niche", "Promises too much"],
            ["Other", f"↩️ Cancel #{d1}"],
        ])

    # 26. Plain text shortcut: 'vaddu 91'
    def test_plain_vaddu_num_prompts_reason(self):
        d1 = self._create_draft("Vaddu Num Draft")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"vaddu {d1}", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"Draft #{d1} ni reject chestunnaru.", self.sent_messages[0])

    # 27. Plain text shortcut: 'reject 91' (no reason -> prompts reason)
    def test_plain_reject_num_no_reason(self):
        d1 = self._create_draft("Reject Num Draft")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"reject {d1}", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"Draft #{d1} ni reject chestunnaru.", self.sent_messages[0])

    # 28. Plain text shortcut: 'reject 91 too generic'
    def test_plain_reject_num_with_reason(self):
        d1 = self._create_draft("Reject Num Reason Draft")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"reject {d1} too generic", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"Rejected: #{d1} (Too generic)", self.sent_messages[0])
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "rejected")

    # 29. Plain text shortcut: '91 reject duplicate'
    def test_plain_num_reject_with_reason(self):
        d1 = self._create_draft("Num Reject Reason Draft")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"{d1} reject duplicate", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"Rejected: #{d1} (Duplicate)", self.sent_messages[0])
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "rejected")

    # 30. Telugu verb shortcut: 'aamodinchu 91'
    def test_telugu_aamodinchu_shortcut(self):
        d1 = self._create_draft("Telugu Aamodinchu")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"aamodinchu {d1}", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"Approved ✓ #{d1}", self.sent_messages[0])

    # 31. Telugu verb shortcut: '91 tirooskarinchu'
    def test_telugu_tirooskarinchu_shortcut(self):
        d1 = self._create_draft("Telugu Tirooskarinchu")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"{d1} tirooskarinchu weak niche", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"Rejected: #{d1} (Weak niche)", self.sent_messages[0])

    # 32. Idempotency: approving already approved draft
    def test_idempotent_approve(self):
        d1 = self._create_draft("Idempotent Approve Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle(f"approve {d1}", ctx)
        self.sent_messages.clear()

        res = tg_keyboard.handle(f"approve {d1}", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("already approved", self.sent_messages[0])

    # 33. Idempotency: rejecting already rejected draft
    def test_idempotent_reject(self):
        d1 = self._create_draft("Idempotent Reject Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle(f"reject {d1} duplicate", ctx)
        self.sent_messages.clear()

        res = tg_keyboard.handle(f"reject {d1} duplicate", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("already rejected", self.sent_messages[0].lower())

    # 34. Trying to reject an already approved draft
    def test_cannot_reject_approved_draft(self):
        d1 = self._create_draft("Approved Draft", status="approved")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"reject {d1} other", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("cannot reject", self.sent_messages[0].lower())

    # 35. Trying to approve an already rejected draft
    def test_cannot_approve_rejected_draft(self):
        d1 = self._create_draft("Rejected Draft", status="rejected")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"approve {d1}", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("cannot approve", self.sent_messages[0].lower())

    # 36. Approving non-existent draft
    def test_approve_nonexistent_draft(self):
        ctx = self._make_ctx()
        res = tg_keyboard.handle("approve 9999", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("not found", self.sent_messages[0].lower())

    # 37. Rejecting non-existent draft
    def test_reject_nonexistent_draft(self):
        ctx = self._make_ctx()
        res = tg_keyboard.handle("reject 9999 other", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("not found", self.sent_messages[0].lower())

    # 38. 10-minute idle session timeout
    def test_idle_timeout_expires_session(self):
        d1 = self._create_draft("Timeout Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        # Simulate 11 minutes later
        later = self.now + datetime.timedelta(minutes=11)
        later_ctx = self._make_ctx(now=later)

        # Trigger check with a message
        res = tg_keyboard.handle("hello", later_ctx)
        # Should detect timeout, send timeout message + remove keyboard, and return None for 'hello'
        self.assertIsNone(res)
        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn("Decide session timeout aindi (10 min idle)", self.sent_messages[0])
        self.assertEqual(self.sent_keyboards[0], {"remove_keyboard": True})
        self.assertEqual(self.state_store.get(tg_keyboard.KEY_ACTIVE_SESSION), "")

    # 39. Session not expired within 9 minutes
    def test_session_valid_under_10_minutes(self):
        d1 = self._create_draft("Draft Alpha")
        d2 = self._create_draft("Draft Beta")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        # 8 minutes later
        later = self.now + datetime.timedelta(minutes=8)
        later_ctx = self._make_ctx(now=later)
        res = tg_keyboard.handle("⏭ Next", later_ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"#{d2} Draft Beta", self.sent_messages[0])

    # 40. Audit logging on approve
    def test_audit_log_on_approve(self):
        d1 = self._create_draft("Audit Approve Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle(f"approve {d1}", ctx)

        logs = json.loads(self.state_store[tg_keyboard.KEY_AUDIT_LOG])
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["action"], "approved")
        self.assertEqual(logs[0]["draft_id"], d1)
        self.assertEqual(logs[0]["chat_id"], self.owner_chat_id)

    # 41. Audit logging on reject
    def test_audit_log_on_reject(self):
        d1 = self._create_draft("Audit Reject Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle(f"reject {d1} Too generic", ctx)

        logs = json.loads(self.state_store[tg_keyboard.KEY_AUDIT_LOG])
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["action"], "rejected")
        self.assertEqual(logs[0]["draft_id"], d1)
        self.assertEqual(logs[0]["reason"], "Too generic")

    # 42. Audit log capping at 500 entries
    def test_audit_log_capping(self):
        initial_logs = [{"action": "approved", "draft_id": i} for i in range(505)]
        self.state_store[tg_keyboard.KEY_AUDIT_LOG] = json.dumps(initial_logs)
        d1 = self._create_draft("Cap Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle(f"approve {d1}", ctx)

        logs = json.loads(self.state_store[tg_keyboard.KEY_AUDIT_LOG])
        self.assertEqual(len(logs), 500)
        self.assertEqual(logs[-1]["draft_id"], d1)

    # 43. Plain command inside active session advances keyboard
    def test_plain_approve_inside_session_advances_keyboard(self):
        d1 = self._create_draft("Sess Draft 1")
        d2 = self._create_draft("Sess Draft 2")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        res = tg_keyboard.handle(f"{d1} ok", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"Approved ✓ #{d1}", self.sent_messages[0])
        self.assertIn(f"#{d2} Sess Draft 2", self.sent_messages[0])
        self.assertEqual(self.sent_keyboards[0], [
            [f"✅ {d2}", f"❌ {d2}", f"👁 {d2}"],
            ["⏭ Next", "🛑 Stop"],
        ])

    # 44. Plain reject with reason inside active session advances keyboard
    def test_plain_reject_inside_session_advances_keyboard(self):
        d1 = self._create_draft("Sess Rej 1")
        d2 = self._create_draft("Sess Rej 2")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        self.sent_messages.clear()
        self.sent_keyboards.clear()

        res = tg_keyboard.handle(f"reject {d1} bad niche", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"Rejected: #{d1} (bad niche)", self.sent_messages[0])
        self.assertIn(f"#{d2} Sess Rej 2", self.sent_messages[0])
        self.assertEqual(self.sent_keyboards[0], [
            [f"✅ {d2}", f"❌ {d2}", f"👁 {d2}"],
            ["⏭ Next", "🛑 Stop"],
        ])

    # 45. Reject reason flow cancel keyword 'cancel'
    def test_reject_cancel_word(self):
        d1 = self._create_draft("Cancel Me")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        tg_keyboard.handle(f"❌ {d1}", ctx)
        self.sent_messages.clear()

        res = tg_keyboard.handle("cancel", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("Reject cancel aindi.", self.sent_messages[0])

    # 46. Reject reason flow cancel keyword 'raddu'
    def test_reject_raddu_word(self):
        d1 = self._create_draft("Raddu Me")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        tg_keyboard.handle(f"❌ {d1}", ctx)
        self.sent_messages.clear()

        res = tg_keyboard.handle("raddu", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("Reject cancel aindi.", self.sent_messages[0])

    # 47. Reject reason flow cancel keyword 'back'
    def test_reject_back_word(self):
        d1 = self._create_draft("Back Me")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        tg_keyboard.handle(f"❌ {d1}", ctx)
        self.sent_messages.clear()

        res = tg_keyboard.handle("back", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("Reject cancel aindi.", self.sent_messages[0])

    # 48. Rejection reason case insensitivity
    def test_reject_reason_case_insensitive(self):
        d1 = self._create_draft("Case Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        tg_keyboard.handle(f"❌ {d1}", ctx)
        res = tg_keyboard.handle("too GENERIC", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertEqual(self._get_approval_reason(d1), "Too generic")

    # 49. Reject reason matches guardrails.REJECT_REASONS
    def test_reject_reason_all_options(self):
        for reason in tg_keyboard.REJECT_REASON_OPTIONS:
            d = self._create_draft(f"Test {reason}")
            ctx = self._make_ctx()
            tg_keyboard.handle(f"reject {d} {reason}", ctx)
            appr = guardrails.get_approval(d, self.db_path)
            self.assertEqual(appr["status"], "rejected")

    # 50. Draft card formatting with checker review
    def test_draft_card_formatting(self):
        review = "Checker score 92/100\n- High clarity\n- Low competition"
        d = self._create_draft("Formatting Test", review=review)
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        msg = self.sent_messages[0]
        self.assertIn(f"#{d} Formatting Test", msg)
        self.assertIn("Checker: 92/100", msg)

    # 51. Draft card formatting without checker review
    def test_draft_card_formatting_no_review(self):
        d = self._create_draft("No Review Test", review="")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        msg = self.sent_messages[0]
        self.assertIn(f"#{d} No Review Test", msg)

    # 52. Lazy import of telegram_inbox
    def test_lazy_import_no_import_time_dependency(self):
        # Ensure tg_keyboard does not import telegram_inbox at import time
        import sys
        # tg_keyboard should already be imported, but check if we reload it in isolation
        self.assertNotIn("telegram_inbox", tg_keyboard.__dict__)

    # 53. Handling whitespace in commands
    def test_whitespace_handling(self):
        d1 = self._create_draft("Whitespace Draft")
        ctx = self._make_ctx()
        res = tg_keyboard.handle(f"   approve    {d1}   ", ctx)
        self.assertEqual(res, {"handled": True})
        appr = guardrails.get_approval(d1, self.db_path)
        self.assertEqual(appr["status"], "approved")

    # 54. Multiple decide starts resets to oldest draft
    def test_multiple_decide_starts(self):
        d1 = self._create_draft("First Draft")
        d2 = self._create_draft("Second Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        tg_keyboard.handle("⏭ Next", ctx)  # now on d2
        self.sent_messages.clear()

        # Restart decide
        tg_keyboard.handle("/decide", ctx)
        self.assertIn(f"#{d1} First Draft", self.sent_messages[0])

    # 55. 'tarwatha' as Telugu word for next
    def test_tarwatha_next_word(self):
        d1 = self._create_draft("Draft A")
        d2 = self._create_draft("Draft B")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        self.sent_messages.clear()

        res = tg_keyboard.handle("tarwatha", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"#{d2} Draft B", self.sent_messages[0])

    # 56. No pending drafts during next navigation
    def test_next_with_empty_queue(self):
        ctx = self._make_ctx()
        res = tg_keyboard.handle("⏭ Next", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("Waiting drafts em levu. ✅", self.sent_messages[0])
        self.assertEqual(self.sent_keyboards[0], {"remove_keyboard": True})

    # 57. Single draft next wraps to itself
    def test_single_draft_next_cycles(self):
        d1 = self._create_draft("Lone Draft")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        self.sent_messages.clear()

        res = tg_keyboard.handle("⏭ Next", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn(f"#{d1} Lone Draft", self.sent_messages[0])

    # 58. View text for non-existent draft
    def test_view_nonexistent_draft(self):
        ctx = self._make_ctx()
        res = tg_keyboard.handle("👁 98765", ctx)
        self.assertEqual(res, {"handled": True})
        self.assertIn("Draft #98765 not found.", self.sent_messages[0])

    # 59. Double reject key pressed simultaneously
    def test_double_reject_key(self):
        d1 = self._create_draft("Draft Double Rej")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        res1 = tg_keyboard.handle(f"❌ {d1}", ctx)
        self.assertEqual(res1, {"handled": True})
        # If user taps ❌ {d1} again while in reason mode:
        res2 = tg_keyboard.handle(f"❌ {d1}", ctx)
        self.assertEqual(res2, {"handled": True})
        self.assertIn(f"Draft #{d1} ni reject chestunnaru.", self.sent_messages[-1])

    # 60. Double approve key pressed simultaneously
    def test_double_approve_key(self):
        d1 = self._create_draft("Draft Double App")
        ctx = self._make_ctx()
        tg_keyboard.handle("/decide", ctx)
        res1 = tg_keyboard.handle(f"✅ {d1}", ctx)
        self.assertEqual(res1, {"handled": True})
        # Second press
        res2 = tg_keyboard.handle(f"✅ {d1}", ctx)
        self.assertEqual(res2, {"handled": True})
        self.assertIn("already approved", self.sent_messages[-1])

    # 61. Check UTC handling in _get_now_dt with timezone-aware datetime
    def test_timezone_aware_now(self):
        now_aware = datetime.datetime(2025, 5, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)
        ctx = self._make_ctx(now=now_aware)
        dt = tg_keyboard._get_now_dt(ctx)
        self.assertIsNone(dt.tzinfo)
        self.assertEqual(dt.hour, 12)

    # 62. Plain number with invalid suffix is ignored
    def test_invalid_number_command_ignored(self):
        ctx = self._make_ctx()
        self.assertIsNone(tg_keyboard.handle("91 price high", ctx))
        self.assertIsNone(tg_keyboard.handle("91 hello world", ctx))
        self.assertIsNone(tg_keyboard.handle("approve without id", ctx))


if __name__ == "__main__":
    unittest.main()
