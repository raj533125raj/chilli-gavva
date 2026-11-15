"""CCD-1: the same Telegram error reaches the owner at most once per UTC day.

notify.send (alerts from run_failure, autofix, workforce, control_inbox ...) and telegram_inbox.send_alert
(pipeline / auto-fix messages the owner did not ask for) count repeats instead of sending them again.
/errors shows each error of today with "(ee roju N sarlu vachindi)". Replies to the owner's own commands
are never held back. Temp database, fake Telegram, no network.
"""
import datetime
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify
import telegram_diagnostics as diagnostics
import telegram_inbox

OWNER = int("1234" "5678")
DAY1 = datetime.datetime(2026, 11, 15, 9, 0, tzinfo=datetime.timezone.utc)
DAY1_LATE = datetime.datetime(2026, 11, 15, 23, 50, tzinfo=datetime.timezone.utc)
DAY2 = datetime.datetime(2026, 11, 16, 0, 10, tzinfo=datetime.timezone.utc)
ERROR = "Control inbox: KV read failed (OSError). CF_KV_NAMESPACE_ID secret check cheyandi"


class _Resp:
    status = 200

    def __init__(self, ok=True):
        self.body = json.dumps({"ok": ok}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = str(Path(temp.name) / "robot.db")
        self.posts = []
        self.ok = True

        def urlopen(req, timeout=10):
            self.posts.append(json.loads(req.data.decode())["text"])
            return _Resp(self.ok)

        for patch in (mock.patch.object(notify, "_cfg", return_value={}),
                      mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": str(OWNER)}),
                      mock.patch("urllib.request.urlopen", side_effect=urlopen)):
            patch.start()
            self.addCleanup(patch.stop)

    def send(self, text, now=DAY1):
        return notify.send(text, db_path=self.db, now=now)


class LooksLikeErrorTests(unittest.TestCase):
    def test_errors(self):
        for text in (ERROR, "Chilli Gavva: run FAILED 3 times in a row.", "AI is down, will retry later.",
                     "Auto-fix could not complete", "GitHub Secret X tappu ga undi", "❌ build broke",
                     "Pack save kaaledu", "Traceback (most recent call last)", "/sell lo problem vachindi"):
            self.assertTrue(diagnostics.looks_like_error(text), text)

    def test_not_errors(self):
        for text in ("fixed ✓", "Pack 3 ready ✓", "Approved ✓ #4", "", None, "   ",
                     "🌶️ Chilli Gavva — 6 approvals waiting"):
            self.assertFalse(diagnostics.looks_like_error(text), text)

    def test_key_ignores_counts_spaces_case_but_not_names(self):
        k = diagnostics.error_key
        self.assertEqual(k("run FAILED 3 times\nrun 111"), k("Run  failed 4 TIMES run 222"))
        self.assertNotEqual(k("test_r22 failed"), k("test_r66 failed"))
        self.assertNotEqual(k("export failed"), k("inbox failed"))
        self.assertEqual(len(k("x")), 16)


class NotifyOnceTests(Base):
    def test_same_error_sent_once_per_day_and_counted(self):
        for _ in range(7):
            self.assertTrue(self.send(ERROR))
        self.assertEqual(self.posts, [ERROR])
        text = diagnostics.errors_today_text(self.db, now=DAY1)
        self.assertIn("(ee roju 7 sarlu vachindi)", text)
        self.assertIn("KV read failed", text)

    def test_numbers_changing_is_still_the_same_error(self):
        self.send("Chilli Gavva: run FAILED 2 times in a row.\nStep: inbox")
        self.send("Chilli Gavva: run FAILED 3 times in a row.\nStep: inbox")
        self.assertEqual(len(self.posts), 1)

    def test_different_errors_each_go_once(self):
        self.send(ERROR)
        self.send("Auto-fix failed: use the robot-fix-runbook.")
        self.send(ERROR)
        self.assertEqual(len(self.posts), 2)

    def test_next_day_sends_again_with_yesterdays_count(self):
        for _ in range(5):
            self.send(ERROR, now=DAY1)
        self.send(ERROR, now=DAY1_LATE)
        self.send(ERROR, now=DAY2)
        self.assertEqual(len(self.posts), 2)
        self.assertIn("(ninna 6 sarlu vachindi)", self.posts[1])
        self.assertIn("(ee roju 1 sarlu vachindi)", diagnostics.errors_today_text(self.db, now=DAY2))
        # yesterday's rows are cleaned away
        conn = sqlite3.connect(self.db)
        rows = conn.execute("SELECT value FROM telegram_state WHERE key LIKE 'error_once:%'").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0][0])["day"], "2026-11-16")

    def test_failed_delivery_is_tried_again(self):
        self.ok = False
        self.assertFalse(self.send(ERROR))
        self.ok = True
        self.assertTrue(self.send(ERROR))
        self.assertTrue(self.send(ERROR))
        self.assertEqual(len(self.posts), 2)          # failed one + the delivered one, no third

    def test_normal_messages_are_never_held_back(self):
        for _ in range(3):
            self.send("🌶️ Chilli Gavva — 6 approvals waiting")
            self.send("fixed ✓")
        self.assertEqual(len(self.posts), 6)

    def test_state_has_no_secret_and_no_full_text(self):
        self.send("Login error: Bearer sk-abcdefghijklmnopqrstuvwxyz1234 failed\nsecond line with detail")
        conn = sqlite3.connect(self.db)
        values = " ".join(v for (v,) in conn.execute("SELECT value FROM telegram_state").fetchall())
        conn.close()
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234", values)
        self.assertNotIn("second line", values)

    def test_storage_problem_lets_the_alert_through(self):
        with mock.patch.object(diagnostics, "_error_conn", side_effect=sqlite3.OperationalError("locked")):
            self.assertTrue(self.send(ERROR))
            self.assertTrue(self.send(ERROR))
        self.assertEqual(len(self.posts), 2)

    def test_old_callers_without_db_path_still_work(self):
        # run_failure / autofix call notify.send(message) with one argument
        import guardrails
        with mock.patch.object(guardrails, "DB_PATH", self.db):
            self.assertTrue(notify.send(ERROR))
            self.assertTrue(notify.send(ERROR))
        self.assertEqual(len(self.posts), 1)
        self.assertIn("(ee roju 2 sarlu vachindi)", diagnostics.errors_today_text(self.db))


class InboxAlertTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = str(Path(temp.name) / "robot.db")
        self.sent = []
        patch = mock.patch.object(telegram_inbox, "send_text",
                                  side_effect=lambda t, c, text, buttons=None, keyboard=None:
                                  self.sent.append((text, buttons)) or True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_send_alert_once_per_day_with_buttons(self):
        rows = [[("Retry", "af:1")]]
        for _ in range(4):
            self.assertTrue(telegram_inbox.send_alert("t", OWNER, "Auto-fix failed on step inbox", rows,
                                                      db_path=self.db, now=DAY1))
        self.assertEqual(self.sent, [("Auto-fix failed on step inbox", rows)])
        self.assertIn("(ee roju 4 sarlu vachindi)", diagnostics.errors_today_text(self.db, now=DAY1))

    def test_send_alert_passes_normal_alerts(self):
        for _ in range(2):
            telegram_inbox.send_alert("t", OWNER, "Pack 3 ready ✓", db_path=self.db, now=DAY1)
        self.assertEqual(len(self.sent), 2)

    def test_run_uses_send_alert_for_pipeline_and_autofix(self):
        source = Path(telegram_inbox.__file__).read_text(encoding="utf-8")
        self.assertIn("notify_ready(db_path, lambda text: send_alert(", source)
        self.assertIn("lambda text, buttons: send_alert(", source)


def media_brain_patch():
    """process_updates imports media_brain (-> llm_router, Python 3.10+ syntax). On an older local Python
    the text path never uses it, so an empty stand-in module is used; CI (3.11) loads the real one."""
    try:
        import media_brain  # noqa: F401
        return []
    except Exception:
        import types
        return [mock.patch.dict(sys.modules, {"media_brain": types.ModuleType("media_brain")})]


class ErrorsCommandTests(unittest.TestCase):
    """/errors is reachable through the real inbox loop; owner replies are never held back."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = str(Path(temp.name) / "robot.db")
        self.updates, self.sent, self.next_id = [], [], 500
        patches = [
            mock.patch.object(telegram_inbox, "fetch_updates", side_effect=lambda token, offset: list(self.updates)),
            mock.patch.object(telegram_inbox, "acknowledge_update", return_value=True),
            mock.patch.object(telegram_inbox, "_call", return_value={"ok": True}),
            mock.patch.object(telegram_inbox, "send_text",
                              side_effect=lambda t, c, text, buttons=None, keyboard=None:
                              self.sent.append(text) or True),
        ]
        patches += media_brain_patch()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def say(self, text):
        self.next_id += 1
        self.updates = [{"update_id": self.next_id,
                         "message": {"message_id": 1, "chat": {"id": OWNER}, "text": text}}]
        self.sent = []
        telegram_inbox.process_updates("t", OWNER, db_path=self.db)
        return self.sent

    def test_errors_command_lists_today_with_counter(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        for _ in range(7):
            telegram_inbox.send_alert("t", OWNER, ERROR, db_path=self.db, now=now)
        for text in ("/errors", "errors"):
            said = self.say(text)
            self.assertEqual(len(said), 1, text)
            self.assertIn("(ee roju 7 sarlu vachindi)", said[0])

    def test_errors_command_when_none(self):
        self.assertIn("errors levu", self.say("/errors")[0])

    def test_repeated_owner_command_always_answered(self):
        self.assertEqual(len(self.say("/view 999")), 1)
        self.assertEqual(len(self.say("/view 999")), 1)

    def test_help_mentions_errors(self):
        self.assertIn("/errors", telegram_inbox.HELP)


if __name__ == "__main__":
    unittest.main()
