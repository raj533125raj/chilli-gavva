"""CCD-4: a crashing tg_*.py plugin never breaks Telegram.

Fake plugins are written into a temp folder and loaded with the real discover_plugins (no other worker's plugin
file is touched). The owner gets one short Tenglish line, the other plugins still answer, and the crash goes to
the auto-fix log (plugin_crashes table + doctor log). Reachability is checked through call_plugins and
process_updates. No network.
"""
import contextlib
import datetime
import io
import os
import sqlite3
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import telegram_inbox as ti

OWNER = int("1234" "5678")
SECRET = "sk-" + "Q" * 40

CRASHER = textwrap.dedent('''
    COMMANDS = [{"cmd": "/xyz", "words": ["xyz chupinchu"], "help_en": "x", "help_te": "x"}]

    def handle(text, ctx):
        if text.startswith("/xyz") or text == "xyz chupinchu" or text == "all":
            raise ValueError("boom with key ''' + SECRET + '''")
        if text == "/quit":
            raise SystemExit(3)
        return None
''')
GOOD = textwrap.dedent('''
    COMMANDS = [{"cmd": "/good", "words": ["good"], "help_en": "g", "help_te": "g"}]

    def handle(text, ctx):
        if text in ("/good", "all", "/quit"):
            return {"text": "good answer"}
        if text == "/silent":
            ctx["send_text"]("sent by plugin")
            return {"handled": True}
        return None
''')
MENU = textwrap.dedent('''
    COMMANDS = [{"cmd": "/menu", "words": ["menu"], "help_en": "m", "help_te": "m"}]

    def handle(text, ctx):
        return {"text": "Meeru cheppindi ardam kaaledu. Try /menu"}
''')


def media_brain_patch():
    try:
        import media_brain  # noqa: F401
        return []
    except Exception:
        return [mock.patch.dict(sys.modules, {"media_brain": types.ModuleType("media_brain")})]


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = str(self.root / "robot.db")
        self.sent = []
        for name, source in (("tg_aa_crasher.py", CRASHER), ("tg_bb_good.py", GOOD), ("tg_zz_menu.py", MENU)):
            (self.root / name).write_text(source, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.plugins = ti.discover_plugins(repo_root=str(self.root))
        self.assertEqual([p.__name__ for p in self.plugins], ["tg_aa_crasher", "tg_bb_good", "tg_zz_menu"])
        patch = mock.patch.object(ti, "send_text", side_effect=lambda t, c, text, buttons=None, keyboard=None:
                                  self.sent.append(text) or True)
        patch.start()
        self.addCleanup(patch.stop)

    def call(self, text, plugins=None):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = ti.call_plugins(text, "123:abc", OWNER, db_path=self.db,
                                     plugins=self.plugins if plugins is None else plugins)
        self.err = err.getvalue()
        return result


class CrashTests(Base):
    def test_owner_gets_one_short_line(self):
        handled, res = self.call("/xyz 3")
        self.assertTrue(handled)
        self.assertEqual(res, {"text": "/xyz lo problem vachindi, robot fix chestundi."})

    def test_plain_words_use_the_plugins_command(self):
        handled, res = self.call("xyz chupinchu")
        self.assertEqual(res["text"], "/xyz lo problem vachindi, robot fix chestundi.")

    def test_other_plugins_still_answer(self):
        handled, res = self.call("/good")
        self.assertEqual(res, {"text": "good answer"})       # crasher returned None: nothing to report

    def test_crash_then_other_plugin_answers_with_note_once_a_day(self):
        handled, res = self.call("all")
        self.assertEqual(res, {"text": "good answer"})          # the other plugin's reply is unchanged
        self.assertEqual(self.sent, ["/xyz lo problem vachindi, robot fix chestundi."])
        handled, res = self.call("all")
        self.assertEqual(res, {"text": "good answer"})
        self.assertEqual(len(self.sent), 1)                      # crash line once a day
        self.assertEqual(len(ti.plugin_crashes(self.db)), 2)     # every crash is still logged

    def test_old_stderr_line_is_kept_and_scrubbed(self):
        self.call("/xyz")
        self.assertIn("Plugin error in tg_aa_crasher: boom with key", self.err)
        self.assertNotIn(SECRET, self.err)

    def test_crash_beats_menu_guess(self):
        crasher, _good, menu = self.plugins
        handled, res = self.call("/xyz", plugins=[crasher, menu])
        self.assertEqual(res["text"], "/xyz lo problem vachindi, robot fix chestundi.")

    def test_system_exit_in_plugin_does_not_stop_the_inbox(self):
        handled, res = self.call("/quit")
        self.assertTrue(handled)
        self.assertTrue(res["text"].startswith("good answer"))

    def test_no_details_or_secret_reach_the_owner(self):
        _, res = self.call("/xyz")
        self.assertNotIn("boom", res["text"])
        self.assertNotIn(SECRET, res["text"])
        self.assertNotIn("Traceback", res["text"])

    def test_crash_goes_to_the_autofix_log(self):
        self.call("/xyz 3")
        crashes = ti.plugin_crashes(self.db)
        self.assertEqual(len(crashes), 1)
        crash = crashes[0]
        self.assertEqual(crash["plugin"], "tg_aa_crasher")
        self.assertEqual(crash["command"], "/xyz")
        self.assertEqual(crash["error_class"], "ValueError")
        self.assertRegex(crash["location"], r"^tg_aa_crasher\.py:\d+$")
        self.assertEqual(crash["status"], "needs_autofix")
        self.assertIn("boom", crash["error_message"])
        self.assertNotIn(SECRET, crash["error_message"])
        self.assertIn("Telegram plugin crash: tg_aa_crasher.handle (ValueError) at tg_aa_crasher.py:", self.err)
        self.assertNotIn(SECRET, self.err)

    def test_log_is_bounded(self):
        crasher = self.plugins[0]
        with contextlib.redirect_stderr(io.StringIO()):
            for _ in range(205):
                ti.record_plugin_crash(crasher, "/xyz", ValueError("x"), db_path=self.db)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM plugin_crashes").fetchone()[0], 200)
        conn.close()

    def test_handled_only_reply_gets_note_as_its_own_message(self):
        crasher, good, _menu = self.plugins
        always = types.ModuleType("tg_always_crash")
        always.handle = lambda text, ctx: 1 / 0
        handled, res = self.call("/silent", plugins=[always, good])
        self.assertEqual(res, {"handled": True})
        self.assertIn("sent by plugin", self.sent)
        self.assertIn("/always_crash lo problem vachindi, robot fix chestundi.", self.sent)

    def test_doctor_log_gets_the_case_when_doctor_loads(self):
        try:
            import doctor
        except Exception:
            self.skipTest("doctor.py needs Python 3.10+ (CI runs it)")
        self.call("/xyz")
        conn = doctor.connect(self.db)
        rows = conn.execute("SELECT agent, category, status FROM doctor_cases").fetchall()
        conn.close()
        self.assertIn(("telegram_plugin", "plugin_crash", "needs_autofix"), [tuple(r) for r in rows])


class ThroughTheInboxTests(Base):
    """The same through process_updates: the crash line reaches the owner and the next message still works."""

    def setUp(self):
        super().setUp()
        self.updates = []
        patches = [mock.patch.object(ti, "discover_plugins", return_value=self.plugins),
                   mock.patch.object(ti, "fetch_updates", side_effect=lambda token, offset: list(self.updates)),
                   mock.patch.object(ti, "acknowledge_update", return_value=True),
                   mock.patch.object(ti, "_call", return_value={"ok": True})] + media_brain_patch()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_crash_and_next_message_in_one_batch(self):
        self.updates = [{"update_id": i, "message": {"message_id": i, "chat": {"id": OWNER}, "text": t}}
                        for i, t in ((1, "/xyz 2"), (2, "/good"))]
        with contextlib.redirect_stderr(io.StringIO()):
            handled = ti.process_updates("123:abc", OWNER, db_path=self.db)
        self.assertEqual(handled, 2)
        self.assertEqual(self.sent, ["/xyz lo problem vachindi, robot fix chestundi.", "good answer"])
        self.assertEqual(len(ti.plugin_crashes(self.db)), 1)

    def test_real_plugin_still_answers_next_to_a_crasher(self):
        import tg_bundle
        crasher = self.plugins[0]
        handled, res = self.call("/bundle", plugins=[crasher, tg_bundle])
        self.assertTrue(handled)
        self.assertTrue(res.get("text"))
        self.assertNotIn("problem vachindi", res["text"])


if __name__ == "__main__":
    unittest.main()
