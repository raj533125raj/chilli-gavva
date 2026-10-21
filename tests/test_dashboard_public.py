"""The public dashboard page: its script parses, and it does not read money or logs that the public file leaves out."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "web", "index.html")


def inline_script():
    with open(INDEX, encoding="utf-8") as f:
        return "\n;\n".join(re.findall(r"<script[^>]*>(.*?)</script>", f.read(), re.S))


class DashboardPublicTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_inline_script_parses(self):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(inline_script())
            path = f.name
        try:
            result = subprocess.run(["node", "--check", path], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            os.unlink(path)

    def test_money_is_read_only_when_present(self):
        script = inline_script()
        self.assertIn("s.money ? money(s.money.real_usd)", script)
        self.assertNotIn("money(s.money.real_usd);", script.replace("s.money ? money(s.money.real_usd)", ""))

    def test_missing_sections_show_a_plain_note(self):
        script = inline_script()
        for name in ("prompts", "logs", "validation", "services", "tools-built"):
            self.assertIn(name, script)
        self.assertIn("Not in the public snapshot.", script)


if __name__ == "__main__":
    unittest.main()
