"""Unit tests for chat_brain.py module."""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import chat_brain
import guardrails


class TestChatBrain(unittest.TestCase):

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()
        guardrails.connect(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_chat_memory_limit(self):
        chat_id = "12345"
        for i in range(15):
            chat_brain.add_chat_memory(chat_id, "user", f"msg {i}", db_path=self.db_path)

        mem = chat_brain.get_chat_memory(chat_id, limit=20, db_path=self.db_path)
        self.assertEqual(len(mem), 10)
        self.assertEqual(mem[0]["content"], "msg 5")
        self.assertEqual(mem[-1]["content"], "msg 14")

    def test_rule_based_classify_english_and_tenglish(self):
        # Actions
        intent, params = chat_brain.rule_based_classify("aapu work konchem sepu")
        self.assertEqual(intent, "pause")

        intent, params = chat_brain.rule_based_classify("malli start cheyi")
        self.assertEqual(intent, "resume")

        intent, params = chat_brain.rule_based_classify("approve #12 and #15")
        self.assertEqual(intent, "approve")
        self.assertEqual(params.get("ids"), [12, 15])

        intent, params = chat_brain.rule_based_classify("reject 7 draft voddu")
        self.assertEqual(intent, "reject")
        self.assertEqual(params.get("ids"), [7])

        intent, params = chat_brain.rule_based_classify("ippude run cheyi engine")
        self.assertEqual(intent, "run_now")

        # Queries
        intent, _ = chat_brain.rule_based_classify("today status ela undhi")
        self.assertEqual(intent, "status")

        intent, _ = chat_brain.rule_based_classify("list pending drafts")
        self.assertEqual(intent, "drafts_waiting")

        intent, _ = chat_brain.rule_based_classify("enduku reject chesav")
        self.assertEqual(intent, "rejected_reasons")

        intent, _ = chat_brain.rule_based_classify("which products are ready or needs fix")
        self.assertEqual(intent, "truth_check_status")

        intent, _ = chat_brain.rule_based_classify("ai health ela undhi")
        self.assertEqual(intent, "ai_health")

        intent, _ = chat_brain.rule_based_classify("github minutes count")
        self.assertEqual(intent, "github_minutes")

        intent, _ = chat_brain.rule_based_classify("last errors dhenilo vachai")
        self.assertEqual(intent, "last_errors")

        intent, _ = chat_brain.rule_based_classify("scout ideas count enni")
        self.assertEqual(intent, "scout_count")

    @patch("llm_router.chat")
    def test_classify_intent_llm_and_fallback(self, mock_llm):
        # Mock LLM success
        mock_llm.return_value = '{"intent": "status", "ids": []}'
        intent, params = chat_brain.classify_intent("status update please", db_path=self.db_path)
        self.assertEqual(intent, "status")

        # Mock LLM failure -> rule-based fallback
        mock_llm.return_value = None
        intent, params = chat_brain.classify_intent("aapu work", db_path=self.db_path)
        self.assertEqual(intent, "pause")

    def test_data_fetchers_from_fake_db(self):
        # Setup fake DB data
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO approvals (id, title, payload, status, reason) VALUES (1, 'Test Draft', 'Payload', 'rejected', 'price')")
        conn.execute("CREATE TABLE IF NOT EXISTS product_builds (approval_id INTEGER PRIMARY KEY, status TEXT, folder TEXT)")
        conn.execute("INSERT INTO product_builds (approval_id, folder, status) VALUES (1, 'exported_content/products/1-test', 'built')")
        conn.execute("CREATE TABLE IF NOT EXISTS truth_checks (approval_id INTEGER PRIMARY KEY, verdict TEXT, score INTEGER)")
        conn.execute("INSERT INTO truth_checks (approval_id, verdict, score) VALUES (1, 'ready', 90)")
        conn.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, kind TEXT, agent TEXT, error TEXT, status TEXT, finished_ts TEXT)")
        conn.execute("INSERT INTO tasks (id, kind, agent, error, status) VALUES (1, 'draft', 'Draft Worker', 'API timeout', 'failed')")
        conn.commit()
        conn.close()

        status_text = chat_brain.fetch_status(db_path=self.db_path)
        self.assertIn("Chilli Gavva Status", status_text)

        reasons_text = chat_brain.fetch_rejected_reasons(db_path=self.db_path)
        self.assertIn("Reason: price", reasons_text)

        truth_text = chat_brain.fetch_truth_check_status(db_path=self.db_path)
        self.assertIn("READY", truth_text)

        errors_text = chat_brain.fetch_last_errors(db_path=self.db_path)
        self.assertIn("API timeout", errors_text)

        ai_health_text = chat_brain.fetch_ai_health(db_path=self.db_path)
        self.assertIn("AI Health", ai_health_text)

    @patch("telegram_inbox.send_text")
    def test_action_confirmation_flow(self, mock_send_text):
        token = "fake_token"
        owner_chat_id = "12345678"

        # 1. Process "pause" action message
        card = chat_brain.process_message(token, owner_chat_id, "pause work", db_path=self.db_path)
        self.assertIn("Confirmation Required", card)
        mock_send_text.assert_called()
        args, kwargs = mock_send_text.call_args
        buttons = args[3] if len(args) > 3 else kwargs.get("buttons")
        self.assertIsNotNone(buttons)
        self.assertEqual(buttons[0][0][1], "cb:y:pause")

        # 2. Simulate user pressing 'No' button
        reply_no = chat_brain.handle_callback(token, owner_chat_id, "cb:n:pause", db_path=self.db_path)
        self.assertIn("Cancelled action 'pause'", reply_no)
        self.assertFalse(guardrails.is_paused(self.db_path))

        # 3. Simulate user pressing 'Yes' button
        reply_yes = chat_brain.handle_callback(token, owner_chat_id, "cb:y:pause", db_path=self.db_path)
        self.assertIn("Action executed: Paused", reply_yes)
        self.assertTrue(guardrails.is_paused(self.db_path))

    @patch("telegram_inbox.send_text")
    def test_owner_only_isolation_in_telegram_inbox(self, mock_send_text):
        import telegram_inbox

        token = "fake_token"
        owner_chat_id = "1001"
        other_chat_id = "9999"

        # Mock updates containing messages from both owner and non-owner chats
        updates = [
            {
                "update_id": 1,
                "message": {
                    "chat": {"id": other_chat_id},
                    "text": "status ela undhi"
                }
            },
            {
                "update_id": 2,
                "message": {
                    "chat": {"id": owner_chat_id},
                    "text": "status ela undhi"
                }
            }
        ]

        with patch("telegram_inbox.fetch_updates", return_value=updates):
            stats = telegram_inbox.new_stats()
            handled = telegram_inbox.process_updates(token, owner_chat_id, db_path=self.db_path, stats=stats)
            self.assertEqual(handled, 1)
            self.assertEqual(stats["skipped"]["not owner chat"], 1)

    def test_scrub_secrets_and_money(self):
        secret_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456"
        os.environ["TELEGRAM_BOT_TOKEN"] = secret_token
        test_str = f"Token is {secret_token} and cost is $500 USD or 50 dollars"

        scrubbed = chat_brain.scrub_secrets_and_money(test_str)
        self.assertNotIn(secret_token, scrubbed)
        self.assertNotIn("$500", scrubbed)
        self.assertNotIn("50 dollars", scrubbed)
        self.assertIn("[hidden]", scrubbed)

    def test_unknown_or_risky_request_capabilities(self):
        reply = chat_brain.fetch_capabilities()
        self.assertIn("I am Chilli Gavva Bot", reply)
        self.assertIn("QUERIES", reply)
        self.assertIn("ACTIONS", reply)


if __name__ == "__main__":
    unittest.main()
