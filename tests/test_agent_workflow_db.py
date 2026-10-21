"""The cloud workflow: stops without DB_KEY, never commits the plaintext database, never prints the key."""
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(REPO, ".github", "workflows", "agent.yml")


def workflow_text():
    with open(WORKFLOW, encoding="utf-8") as f:
        return f.read()


class AgentWorkflowDbTests(unittest.TestCase):
    def test_first_step_stops_without_db_key(self):
        text = workflow_text()
        first_step = text.index("- name: Check the database key")
        checkout = text.index("uses: actions/checkout")
        self.assertLess(first_step, checkout, "the key check runs before the checkout and every other step")
        block = text[first_step:checkout]
        self.assertIn('if [ -z "$DB_KEY" ]; then', block)
        self.assertIn("DB_KEY not set", block)
        self.assertIn("exit 1", block)

    def test_database_is_opened_before_it_is_used(self):
        text = workflow_text()
        opened = text.index("scripts/db_crypt.py decrypt")
        unpacked = text.index("scripts/db_crypt.py unpack-products")
        self.assertGreater(unpacked, opened)
        self.assertLess(unpacked, text.index("- name: AI health check"))
        self.assertLess(opened, text.index("- name: Run tests first"))
        self.assertLess(opened, text.index("- name: Run one agent cycle"))

    def test_product_bundle_is_packed_before_save_and_only_bundle_is_staged(self):
        text = workflow_text()
        packed = text.index("- name: Pack private products")
        saved = text.index("- name: Save state")
        self.assertLess(packed, saved)
        pack_step = text[packed:saved]
        self.assertIn("DB_KEY: ${{ secrets.DB_KEY }}", pack_step)
        save = text[saved:]
        loop = re.search(r"for p in ([^;]+); do", save).group(1).split()
        state = re.search(r'STATE="([^"]+)"', save).group(1).split()
        self.assertIn("products.tar.gz.enc", loop)
        self.assertIn("products.tar.gz.enc", state)
        self.assertNotIn("exported_content", loop)
        self.assertNotIn("exported_content", state)

    def test_product_unpack_has_key_and_workflow_does_not_echo_it(self):
        text = workflow_text()
        step = text[text.index("- name: Unpack private products"):text.index("- name: AI health check")]
        self.assertIn("DB_KEY: ${{ secrets.DB_KEY }}", step)
        self.assertIn("scripts/db_crypt.py unpack-products", step)
        self.assertNotIn("set -x", text)
        for line in text.splitlines():
            if re.search(r"\becho\b|\bprintf\b", line):
                self.assertNotRegex(line, r"\$\{?DB_KEY|secrets\.DB_KEY")

    def test_workflow_never_commits_the_plaintext_database(self):
        text = workflow_text()
        self.assertIsNone(re.search(r"git add[^\n]*\bagent_data\.db(?!\.enc)\b", text), "git add must not name agent_data.db")
        save = text[text.index("- name: Save state"):]
        loop = re.search(r"for p in ([^;]+); do", save).group(1).split()
        self.assertIn("agent_data.db.enc", loop)
        self.assertNotIn("agent_data.db", loop)
        state = re.search(r'STATE="([^"]+)"', save).group(1).split()
        self.assertIn("agent_data.db.enc", state)
        self.assertNotIn("agent_data.db", state)

    def test_encrypt_runs_before_the_database_is_added(self):
        save = workflow_text()[workflow_text().index("- name: Save state"):]
        self.assertLess(save.index("scripts/db_crypt.py encrypt"), save.index("for p in agent_data.db.enc"))

    def test_db_key_is_never_echoed(self):
        for line in workflow_text().split("\n"):
            if re.search(r"\becho\b|\bprintf\b", line):
                self.assertFalse(re.search(r"\$\{?DB_KEY|secrets\.DB_KEY", line), f"no key value in: {line.strip()}")
        self.assertNotIn("set -x", workflow_text())

    def test_tests_restore_the_database_by_decrypting_not_by_git_checkout(self):
        text = workflow_text()
        tests_block = text[text.index("- name: Run tests first"):text.index("- name: Run scheduled recipe")]
        self.assertIn("scripts/db_crypt.py decrypt", tests_block)
        self.assertNotIn("git checkout -- agent_data.db", tests_block)
        self.assertIn("git checkout -- web/data/status.json", tests_block)


if __name__ == "__main__":
    unittest.main()
