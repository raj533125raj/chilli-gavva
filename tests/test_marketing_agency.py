"""Tests for Marketing Agency module: multi-channel kit generator for approved ready products."""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import guardrails
import marketing_agency
import export_status


class MarketingAgencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "marketing_agency_test.db")
        marketing_agency.connect(self.db).close()

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_ready_product(self, approval_id=1, title="[Budget Sheets Agent] Ultimate Freelance Expense Tracker", payload="PRODUCT NAME: Tracker"):
        conn = guardrails.connect(self.db)
        conn.execute("INSERT INTO approvals (id, ts, kind, title, payload, status) VALUES (?, '2026-10-24T10:00:00', 'public_draft', ?, ?, 'approved')",
                     (approval_id, title, payload))
        conn.execute("CREATE TABLE IF NOT EXISTS product_builds (approval_id INTEGER PRIMARY KEY, status TEXT, tries INTEGER, folder TEXT, note TEXT, ts TEXT)")
        conn.execute("INSERT INTO product_builds (approval_id, status, tries, folder, note, ts) VALUES (?, 'built', 1, ?, 'built ok', '2026-10-24T10:05:00')",
                     (approval_id, f"{approval_id}-tracker"))
        conn.commit()
        conn.close()

    def test_get_ready_products(self):
        self._seed_ready_product(1, "Product 1")
        products = marketing_agency._get_ready_products(self.db)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["id"], 1)
        self.assertEqual(products[0]["title"], "Product 1")

    def test_rule_based_fallbacks_for_all_channels(self):
        for ch in marketing_agency.CHANNELS:
            draft = marketing_agency.generate_channel_draft(ch, "Simple Planner", "Planner Details", db_path=self.db, llm_fn=lambda p, **kw: None)
            self.assertTrue(isinstance(draft, str))
            self.assertGreater(len(draft), 30)

        # Verify Pinterest fallback format
        pin_draft = marketing_agency._pinterest_fallback("Planner Title", "Details")
        self.assertIn("Pin 1 Title:", pin_draft)
        self.assertIn("Pin 5 Description:", pin_draft)
        self.assertIn("Board Name:", pin_draft)

        # Verify Instagram fallback format
        ig_draft = marketing_agency._instagram_fallback("Planner Title", "Details")
        self.assertIn("Caption 1:", ig_draft)
        self.assertIn("Hashtags:", ig_draft)
        self.assertIn("Slide 1:", ig_draft)

        # Verify YouTube Shorts fallback format
        yt_draft = marketing_agency._youtube_fallback("Planner Title", "Details")
        self.assertIn("Script 1 (Under 45s):", yt_draft)
        self.assertIn("[Hook 0-5s]:", yt_draft)
        self.assertIn("[CTA 35-45s]:", yt_draft)

        # Verify Telegram fallback format
        tg_draft = marketing_agency._telegram_fallback("Planner Title", "Details")
        self.assertIn("Key Features:", tg_draft)

        # Verify SEO fallback format
        seo_draft = marketing_agency._seo_fallback("Planner Title", "Details")
        self.assertIn("SEO Title:", seo_draft)
        self.assertIn("13 Tags:", seo_draft)
        self.assertIn("First 160 Characters Snippet:", seo_draft)

        # Verify 14-day calendar fallback format
        cal_draft = marketing_agency._calendar_fallback("Planner Title", "Details")
        self.assertIn("Day 1", cal_draft)
        self.assertIn("Day 14", cal_draft)

    def test_checker_gate_integration(self):
        # High scoring mock LLM draft
        good_llm = lambda p, **kw: (
            "Pin 1 Title: Clean Organization Planner\n"
            "Pin 1 Description: Organize your life step by step.\n"
            "Pin 2 Title: Daily Focus Board\n"
            "Pin 2 Description: Ideal for freelancers and students.\n"
            "Pin 3 Title: Master Your Goals\n"
            "Pin 3 Description: Ready-made layout for daily clarity.\n"
            "Pin 4 Title: Simple Habit Tracker\n"
            "Pin 4 Description: Track daily habits easily.\n"
            "Pin 5 Title: Clean Layout Guide\n"
            "Pin 5 Description: Practical templates built for focus.\n"
            "Keywords: productivity, planner\n"
            "Board Name: Planners"
        )
        draft = marketing_agency.generate_channel_draft("pinterest", "Budget Planner", "Details", db_path=self.db, llm_fn=good_llm)
        self.assertIn("Pin 1 Title:", draft)

    def test_daily_ai_cap_respect(self):
        with mock.patch("guardrails.can_call_llm", return_value=False):
            draft = marketing_agency.generate_channel_draft("instagram", "Budget Planner", "Details", db_path=self.db)
            self.assertIn("Caption 1:", draft)

    def test_database_storage_and_kit_retrieval(self):
        self._seed_ready_product(10, "Product 10")
        kit = marketing_agency.generate_full_kit(10, "Product 10", "Product Details", db_path=self.db, llm_fn=lambda p, **kw: None)
        row_id = marketing_agency.save_kit(kit, db_path=self.db)
        self.assertGreater(row_id, 0)

        saved = marketing_agency.get_kit(10, db_path=self.db)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["product_title"], "Product 10")
        self.assertEqual(saved["status"], "draft")
        self.assertIn("Pin 1 Title:", saved["pinterest_draft"])

        kits_list = marketing_agency.list_kits(db_path=self.db)
        self.assertEqual(len(kits_list), 1)

    def test_get_marketing_kits_count(self):
        counts = marketing_agency.get_marketing_kits_count(db_path=self.db)
        self.assertEqual(counts, {"total": 0, "draft": 0, "approved": 0})

        self._seed_ready_product(5, "Product 5")
        kit = marketing_agency.generate_full_kit(5, "Product 5", "Details", db_path=self.db, llm_fn=lambda p, **kw: None)
        marketing_agency.save_kit(kit, db_path=self.db)

        counts = marketing_agency.get_marketing_kits_count(db_path=self.db)
        self.assertEqual(counts, {"total": 1, "draft": 1, "approved": 0})

        kit["status"] = "approved"
        marketing_agency.save_kit(kit, db_path=self.db)

        counts = marketing_agency.get_marketing_kits_count(db_path=self.db)
        self.assertEqual(counts, {"total": 1, "draft": 0, "approved": 1})

    def test_telegram_card_and_callbacks(self):
        kit = {
            "approval_id": 42,
            "product_title": "Ultimate Planner",
            "status": "draft",
            "pinterest_draft": "Pin 1",
            "instagram_draft": "Cap 1",
            "youtube_draft": "Script 1",
            "telegram_draft": "Post 1",
            "seo_draft": "Title 1",
            "calendar_draft": "Day 1"
        }
        marketing_agency.save_kit(kit, db_path=self.db)

        card_text, buttons = marketing_agency.format_telegram_card(kit)
        self.assertIn("Marketing Launch Kit (DRAFT)", card_text)
        self.assertIn("Product #42", card_text)
        self.assertIn("DRAFTS ONLY", card_text)
        self.assertEqual(buttons[0][0][1], "mkt:a:42")

        # Test callback approve
        res_approve = marketing_agency.handle_callback("token", "123", "mkt:a:42", db_path=self.db)
        self.assertIn("Approved marketing kit", res_approve)
        saved = marketing_agency.get_kit(42, db_path=self.db)
        self.assertEqual(saved["status"], "approved")

        # Test callback redo channel
        res_redo = marketing_agency.handle_callback("token", "123", "mkt:r:42:pinterest", db_path=self.db)
        self.assertIn("Redid Pinterest draft", res_redo)
        saved2 = marketing_agency.get_kit(42, db_path=self.db)
        self.assertEqual(saved2["status"], "draft")

    def test_no_posting_code_and_no_money_amounts(self):
        # Test money sanitization in text
        dirty_text = "Get this template for $7 USD or 15 dollars today!"
        clean = marketing_agency._clean_no_money(dirty_text)
        self.assertNotIn("$", clean)
        self.assertNotIn("USD", clean)
        self.assertNotIn("15 dollars", clean)

        # Confirm there are no posting endpoints or requests in marketing_agency
        with open(os.path.join(os.path.dirname(__file__), "..", "marketing_agency.py"), encoding="utf-8") as f:
            code = f.read()

        self.assertNotIn("requests.post", code)
        self.assertNotIn("urllib.request.urlopen", code)
        self.assertNotIn("post_to_pinterest", code)
        self.assertNotIn("post_to_instagram", code)

    def test_process_ready_products(self):
        self._seed_ready_product(1, "Prod 1")
        self._seed_ready_product(2, "Prod 2")

        sent_cards = []
        def mock_send(text, buttons):
            sent_cards.append((text, buttons))

        processed = marketing_agency.process_ready_products(db_path=self.db, send_card_fn=mock_send, llm_fn=lambda p, **kw: None)
        self.assertEqual(len(processed), 2)
        self.assertEqual(len(sent_cards), 2)

        # Running again should skip already processed
        processed_again = marketing_agency.process_ready_products(db_path=self.db, send_card_fn=mock_send, llm_fn=lambda p, **kw: None)
        self.assertEqual(len(processed_again), 0)

    def test_export_status_includes_marketing_kits_count(self):
        output = os.path.join(self.tmp.name, "status.json")
        export_status.write_status(output, self.db)
        with open(output, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("marketing_kits_count", data)
        self.assertEqual(data["marketing_kits_count"], {"total": 0, "draft": 0, "approved": 0})


if __name__ == "__main__":
    unittest.main()
