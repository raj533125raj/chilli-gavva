"""Tests for secret_guard.py. Every value below is FAKE and made up for these tests."""
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import secret_guard as sg  # noqa: E402

FAKE = {
    "Groq API key": "key = gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "Google API key": "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
    "GitHub token": "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7",
    "OpenRouter/OpenAI key": "sk-or-v1-" + "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6",
    "Telegram bot token": "123456789:" + "AAHfake_fake_fake_fake_fake_fake_fake",
    "private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "password or secret setting": 'password = "' + "hunter2hunter2hunter2" + '"',
    "Telegram chat id": '"telegram_chat_id": "' + "5550001112" + '"',
    "personal email": "write to " + "anna.k.fake" + "@gmail.com",
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        env = mock.patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path


class FindTests(Base):
    def test_every_kind_is_found(self):
        for kind, text in FAKE.items():
            with self.subTest(kind=kind):
                self.assertIn(kind, [k for k, _ in sg.scan_text("x\n" + text, [])])

    def test_safe_text_is_clean(self):
        for text in ("Contact: name@example.com", "bot noreply@anthropic.com", "token = os.environ['X']",
                     "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", "chat_id = owner_chat_id", "price 14 USD",
                     "telegram_chat_id: YOUR_CHAT_ID", "41898282+github-actions[bot]@users.noreply.github.com"):
            with self.subTest(text=text):
                self.assertEqual(sg.scan_text(text, []), [])

    def test_output_never_contains_the_value(self):
        self.write("leak.md", "\n".join(FAKE.values()))
        findings = sg.scan(root=self.root, allow=[])
        printed = str(findings)
        self.assertEqual(len({k for _, _, k in findings}), len(FAKE))
        for value in ("A1b2C3d4", "hunter2", "5550001112", "anna.k.fake", "AAHfake"):
            self.assertNotIn(value, printed)

    def test_exact_secret_values_from_the_cloud(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "4242424242", "GROQ_API_KEY": "plain-value-123"}):
            self.write("notes.txt", "the owner is 4242424242 ok\nkey plain-value-123\nnumber 42424242420 fine")
            kinds = [(w, k) for _, w, k in sg.scan(root=self.root, allow=[])]
        self.assertEqual(kinds, [("line 1", "exact value of TELEGRAM_CHAT_ID"), ("line 2", "exact value of GROQ_API_KEY")])

    def test_database_rows_and_zip_files_are_scanned(self):
        db = os.path.join(self.root, "agent_data.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE approvals (id INTEGER, payload TEXT)")
        conn.execute("INSERT INTO approvals VALUES (1, 'clean'), (2, ?)", (FAKE["personal email"],))
        conn.commit()
        conn.close()
        with zipfile.ZipFile(os.path.join(self.root, "p.xlsx"), "w") as z:
            z.writestr("xl/sheet1.xml", "<c>" + FAKE["Groq API key"] + "</c>")
        found = sg.scan(root=self.root, allow=[])
        self.assertIn(("agent_data.db", "table approvals, row 2", "personal email"), found)
        self.assertIn(("p.xlsx", "xl/sheet1.xml line 1", "Groq API key"), found)

    def test_images_are_skipped(self):
        self.write("cover.png", FAKE["Groq API key"])
        self.assertEqual(sg.scan(root=self.root, allow=[]), [])


class AllowAndExitTests(Base):
    def test_allow_list_but_never_real_values(self):
        self.write("tests/test_x.py", FAKE["Groq API key"])
        self.write(".secret_guard_allow", "tests/test_x.py | *\n")
        self.assertEqual(sg.scan(root=self.root), [])
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2"}):
            self.assertEqual([k for _, _, k in sg.scan(root=self.root)], ["exact value of GROQ_API_KEY"])

    def test_main_blocks_with_exit_1_and_names_kind_only(self):
        self.write("leak.md", FAKE["GitHub token"])
        out = []
        with mock.patch.object(sg, "BASE_DIR", self.root), mock.patch("builtins.print", side_effect=out.append):
            code = sg.main(["leak.md"])
        self.assertEqual(code, 1)
        self.assertIn("  leak.md (line 1): GitHub token", out)
        self.assertNotIn("A1b2", "".join(out))

    def test_repository_itself_is_clean(self):
        """The real repository must pass its own guard (test fakes are on the allow list)."""
        self.assertEqual(sg.scan(root=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), [])


class CleanAiTextTests(unittest.TestCase):
    def test_ai_text_is_cleaned_before_saving(self):
        text = "Sample client: " + "anna.k.fake" + "@gmail.com, key " + FAKE["GitHub token"] + ", docs name@example.com"
        out = sg.clean_ai_text(text)
        self.assertNotIn("anna.k.fake", out)
        self.assertNotIn("ghp_", out)
        self.assertIn("name@example.com", out)
        self.assertEqual(sg.scan_text(out, []), [])

    def test_router_cleans_answers(self):
        import llm_router
        with mock.patch.object(llm_router.guardrails, "can_call_llm", lambda *a, **k: True), \
                mock.patch.object(llm_router, "in_cooldown", lambda *a, **k: False), \
                mock.patch.object(llm_router, "_groq", lambda p, size=None: "Email " + "bo.fake" + "@yahoo.com"):
            self.assertEqual(llm_router.chat("p"), "Email name@example.com")

    def test_workflow_blocks_save_and_reports_every_run(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".github", "workflows", "agent.yml")) as f:
            wf = f.read()
        save = wf.split("- name: Save state")[1]
        self.assertLess(save.index("python3 secret_guard.py"), save.index("git add"))
        self.assertIn("- name: Secret guard (report)", wf)


if __name__ == "__main__":
    unittest.main()
