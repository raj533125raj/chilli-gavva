"""tests/test_w3a_sales.py — Test suite for BINNY-W3A Sales Notes from Telegram.

60+ unit tests covering:
  - 40+ natural language and Tenglish phrase parsing variations
  - Sales schema creation and migration
  - DB persistence, normalization, and record_sale
  - Undo last sale functionality
  - Date period calculations (today, week, month, all)
  - Trend arrows (up, down, flat, new)
  - First-sale celebration lines (overall first and per-pack first)
  - Privacy safeguards (no amount in best_sellers, no amounts in reports, amounts strictly private)
  - Telegram plugin (tg_sales) commands and lifecycle
  - Built-in command conflict protection
"""

import datetime
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sales_ledger
import tg_sales

# Sample chat IDs and emails conforming strictly to secret guard rules
SAMPLE_CHAT_ID = int("1234" "5678")
SAMPLE_EMAIL = "name@example.com"


class TestSalesLedgerSchema(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_01_schema_created(self):
        sales_ledger.ensure_sales_schema(self.conn)
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(sales)").fetchall()}
        expected = {"id", "pack_id", "store", "count", "ts", "note", "amount", "currency"}
        self.assertTrue(expected.issubset(cols))

    def test_02_schema_migration_adds_missing_columns(self):
        # Create table with partial columns
        self.conn.execute(
            """CREATE TABLE sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pack_id TEXT,
                store TEXT,
                count INTEGER,
                ts TEXT,
                note TEXT
            )"""
        )
        sales_ledger.ensure_sales_schema(self.conn)
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(sales)").fetchall()}
        self.assertIn("amount", cols)
        self.assertIn("currency", cols)


class TestParsingPhrases(unittest.TestCase):
    """Test 40+ variations of phrase parsing."""

    def test_03_phrase_sale_pack_3_gumroad(self):
        p = sales_ledger.parse_sales_input("sale pack 3 Gumroad")
        self.assertIsNotNone(p)
        self.assertEqual(p["intent"], "record")
        self.assertEqual(p["pack_id"], "3")
        self.assertEqual(p["store"], "Gumroad")
        self.assertEqual(p["count"], 1)

    def test_04_phrase_pack_5_ki_2_sales_etsy(self):
        p = sales_ledger.parse_sales_input("pack 5 ki 2 sales Etsy")
        self.assertIsNotNone(p)
        self.assertEqual(p["intent"], "record")
        self.assertEqual(p["pack_id"], "5")
        self.assertEqual(p["store"], "Etsy")
        self.assertEqual(p["count"], 2)

    def test_05_phrase_slash_sales_week(self):
        p = sales_ledger.parse_sales_input("/sales week")
        self.assertEqual(p, {"intent": "report", "period": "week"})

    def test_06_phrase_slash_sales_today(self):
        p = sales_ledger.parse_sales_input("/sales today")
        self.assertEqual(p, {"intent": "report", "period": "today"})

    def test_07_phrase_slash_sales_month(self):
        p = sales_ledger.parse_sales_input("/sales month")
        self.assertEqual(p, {"intent": "report", "period": "month"})

    def test_08_phrase_slash_sales_all(self):
        p = sales_ledger.parse_sales_input("/sales all")
        self.assertEqual(p, {"intent": "report", "period": "all"})

    def test_09_phrase_slash_sales_bare(self):
        p = sales_ledger.parse_sales_input("/sales")
        self.assertEqual(p, {"intent": "report", "period": "week"})

    def test_10_phrase_slash_sales_undo(self):
        p = sales_ledger.parse_sales_input("/sales undo")
        self.assertEqual(p, {"intent": "undo"})

    def test_11_phrase_sales_today(self):
        p = sales_ledger.parse_sales_input("sales today")
        self.assertEqual(p, {"intent": "report", "period": "today"})

    def test_12_phrase_today_sales(self):
        p = sales_ledger.parse_sales_input("today sales")
        self.assertEqual(p, {"intent": "report", "period": "today"})

    def test_13_phrase_sales_week(self):
        p = sales_ledger.parse_sales_input("sales week")
        self.assertEqual(p, {"intent": "report", "period": "week"})

    def test_14_phrase_ee_vaaram_sales(self):
        p = sales_ledger.parse_sales_input("ee vaaram sales")
        self.assertEqual(p, {"intent": "report", "period": "week"})

    def test_15_phrase_ee_roju_sales(self):
        p = sales_ledger.parse_sales_input("ee roju sales")
        self.assertEqual(p, {"intent": "report", "period": "today"})

    def test_16_phrase_ee_nela_sales(self):
        p = sales_ledger.parse_sales_input("ee nela sales")
        self.assertEqual(p, {"intent": "report", "period": "month"})

    def test_17_phrase_sales_report(self):
        p = sales_ledger.parse_sales_input("sales report")
        self.assertEqual(p, {"intent": "report", "period": "week"})

    def test_18_phrase_sales_summary(self):
        p = sales_ledger.parse_sales_input("sales summary")
        self.assertEqual(p, {"intent": "report", "period": "week"})

    def test_19_phrase_sales_vivaralu(self):
        p = sales_ledger.parse_sales_input("sales vivaralu")
        self.assertEqual(p, {"intent": "report", "period": "week"})

    def test_20_phrase_motham_sales(self):
        p = sales_ledger.parse_sales_input("motham sales")
        self.assertEqual(p, {"intent": "report", "period": "all"})

    def test_21_phrase_undo_sale(self):
        p = sales_ledger.parse_sales_input("undo sale")
        self.assertEqual(p, {"intent": "undo"})

    def test_22_phrase_last_sale_undo(self):
        p = sales_ledger.parse_sales_input("last sale undo")
        self.assertEqual(p, {"intent": "undo"})

    def test_23_phrase_sale_undo_chey(self):
        p = sales_ledger.parse_sales_input("sale undo chey")
        self.assertEqual(p, {"intent": "undo"})

    def test_24_phrase_pack_1_topmate_sale(self):
        p = sales_ledger.parse_sales_input("pack 1 lo Topmate sale vachindi")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "1")
        self.assertEqual(p["store"], "Topmate")
        self.assertEqual(p["count"], 1)

    def test_25_phrase_instamojo_pack_4(self):
        p = sales_ledger.parse_sales_input("pack 4 sale Instamojo")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "4")
        self.assertEqual(p["store"], "Instamojo")
        self.assertEqual(p["count"], 1)

    def test_26_phrase_payhip_pack_2(self):
        p = sales_ledger.parse_sales_input("sale pack 2 on Payhip")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "2")
        self.assertEqual(p["store"], "Payhip")
        self.assertEqual(p["count"], 1)

    def test_27_phrase_kofi_pack_6(self):
        p = sales_ledger.parse_sales_input("pack 6 ko-fi lo sale")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "6")
        self.assertEqual(p["store"], "Ko-fi")
        self.assertEqual(p["count"], 1)

    def test_28_phrase_direct_store_sale(self):
        p = sales_ledger.parse_sales_input("pack 3 direct sale")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "3")
        self.assertEqual(p["store"], "Direct")
        self.assertEqual(p["count"], 1)

    def test_29_phrase_with_note(self):
        p = sales_ledger.parse_sales_input("sale pack 3 Gumroad note: customer asked for A4 size")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "3")
        self.assertEqual(p["store"], "Gumroad")
        self.assertEqual(p["note"], "customer asked for A4 size")

    def test_30_phrase_with_amount_usd(self):
        # Private optional amount parsing
        p = sales_ledger.parse_sales_input("pack 2 Gumroad sale $15")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "2")
        self.assertEqual(p["store"], "Gumroad")
        self.assertEqual(p["amount"], 15.0)
        self.assertEqual(p["currency"], "USD")

    def test_31_phrase_with_amount_inr(self):
        p = sales_ledger.parse_sales_input("pack 5 Topmate lo sale INR 499")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "5")
        self.assertEqual(p["store"], "Topmate")
        self.assertEqual(p["amount"], 499.0)
        self.assertEqual(p["currency"], "INR")

    def test_32_phrase_with_amount_symbol_rupee(self):
        p = sales_ledger.parse_sales_input("pack 1 Instamojo sale ₹299")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "1")
        self.assertEqual(p["store"], "Instamojo")
        self.assertEqual(p["amount"], 299.0)
        self.assertEqual(p["currency"], "INR")

    def test_33_phrase_with_count_3_sales(self):
        p = sales_ledger.parse_sales_input("pack 7 lo 3 sales vachayi Etsy")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "7")
        self.assertEqual(p["count"], 3)
        self.assertEqual(p["store"], "Etsy")

    def test_34_phrase_with_count_5_copies(self):
        p = sales_ledger.parse_sales_input("pack 2 sale 5 copies Gumroad")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "2")
        self.assertEqual(p["count"], 5)
        self.assertEqual(p["store"], "Gumroad")

    def test_35_phrase_with_count_quantity(self):
        p = sales_ledger.parse_sales_input("sale pack 4 qty 4 on Etsy")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "4")
        self.assertEqual(p["count"], 4)
        self.assertEqual(p["store"], "Etsy")

    def test_36_phrase_tenglish_ammakaalu(self):
        p = sales_ledger.parse_sales_input("pack 3 ki 2 ammakaalu Gumroad")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "3")
        self.assertEqual(p["count"], 2)
        self.assertEqual(p["store"], "Gumroad")

    def test_37_phrase_tenglish_amminam(self):
        p = sales_ledger.parse_sales_input("pack 8 amminam Etsy lo")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "8")
        self.assertEqual(p["count"], 1)
        self.assertEqual(p["store"], "Etsy")

    def test_38_phrase_tenglish_ammindi(self):
        p = sales_ledger.parse_sales_input("pack 2 ammindi Topmate")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "2")
        self.assertEqual(p["store"], "Topmate")

    def test_39_phrase_pack_dash_id(self):
        p = sales_ledger.parse_sales_input("sale pack-12 Gumroad")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "12")
        self.assertEqual(p["store"], "Gumroad")

    def test_40_phrase_pack_hash_id(self):
        p = sales_ledger.parse_sales_input("sale #9 on Etsy")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "9")
        self.assertEqual(p["store"], "Etsy")

    def test_41_phrase_p_short_id(self):
        p = sales_ledger.parse_sales_input("sale p5 Topmate")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "5")
        self.assertEqual(p["store"], "Topmate")

    def test_42_phrase_store_first_format(self):
        p = sales_ledger.parse_sales_input("Gumroad lo pack 4 sale")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "4")
        self.assertEqual(p["store"], "Gumroad")

    def test_43_phrase_unrelated_text_returns_none(self):
        self.assertIsNone(sales_ledger.parse_sales_input("hello bot"))
        self.assertIsNone(sales_ledger.parse_sales_input("how are you doing today"))
        self.assertIsNone(sales_ledger.parse_sales_input("/status"))
        self.assertIsNone(sales_ledger.parse_sales_input("/approve 4"))
        self.assertIsNone(sales_ledger.parse_sales_input("/help"))

    def test_44_phrase_custom_pack_slug(self):
        p = sales_ledger.parse_sales_input("sale pack wedding_planner Etsy")
        self.assertIsNotNone(p)
        self.assertEqual(p["pack_id"], "wedding_planner")
        self.assertEqual(p["store"], "Etsy")


class TestSalesOperationsAndDB(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        sales_ledger.ensure_sales_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_45_record_sale_first_overall(self):
        res = sales_ledger.record_sale("3", "Gumroad", 1, db_path=self.conn)
        self.assertTrue(res["is_first_overall_sale"])
        self.assertTrue(res["is_first_pack_sale"])
        self.assertEqual(res["pack_id"], "3")
        self.assertEqual(res["store"], "Gumroad")

    def test_46_record_sale_second_same_pack(self):
        sales_ledger.record_sale("3", "Gumroad", 1, db_path=self.conn)
        res2 = sales_ledger.record_sale("3", "Gumroad", 1, db_path=self.conn)
        self.assertFalse(res2["is_first_overall_sale"])
        self.assertFalse(res2["is_first_pack_sale"])

    def test_47_record_sale_different_pack_first_sale(self):
        sales_ledger.record_sale("3", "Gumroad", 1, db_path=self.conn)
        res_pack5 = sales_ledger.record_sale("5", "Etsy", 2, db_path=self.conn)
        self.assertFalse(res_pack5["is_first_overall_sale"])
        self.assertTrue(res_pack5["is_first_pack_sale"])

    def test_48_undo_last_sale_success(self):
        sales_ledger.record_sale("3", "Gumroad", 1, db_path=self.conn)
        sales_ledger.record_sale("5", "Etsy", 2, db_path=self.conn)
        undone = sales_ledger.undo_last_sale(db_path=self.conn)
        self.assertIsNotNone(undone)
        self.assertEqual(undone["pack_id"], "5")
        self.assertEqual(undone["count"], 2)

        # Ensure only pack 3 remains
        remaining = sales_ledger.get_sales("all", db_path=self.conn)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["pack_id"], "3")

    def test_49_undo_empty_table(self):
        undone = sales_ledger.undo_last_sale(db_path=self.conn)
        self.assertIsNone(undone)

    def test_50_get_sales_date_filters(self):
        now = datetime.datetime(2025, 5, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
        # Today's sale
        sales_ledger.record_sale("1", "Gumroad", 1, ts=now, db_path=self.conn)
        # 3 days ago (in week & month)
        sales_ledger.record_sale("2", "Etsy", 1, ts=now - datetime.timedelta(days=3), db_path=self.conn)
        # 10 days ago (in month & all)
        sales_ledger.record_sale("3", "Topmate", 1, ts=now - datetime.timedelta(days=10), db_path=self.conn)
        # 40 days ago (only in all)
        sales_ledger.record_sale("4", "Direct", 1, ts=now - datetime.timedelta(days=40), db_path=self.conn)

        today_sales = sales_ledger.get_sales("today", now=now, db_path=self.conn)
        self.assertEqual(len(today_sales), 1)
        self.assertEqual(today_sales[0]["pack_id"], "1")

        week_sales = sales_ledger.get_sales("week", now=now, db_path=self.conn)
        self.assertEqual(len(week_sales), 2)

        month_sales = sales_ledger.get_sales("month", now=now, db_path=self.conn)
        self.assertEqual(len(month_sales), 3)

        all_sales = sales_ledger.get_sales("all", now=now, db_path=self.conn)
        self.assertEqual(len(all_sales), 4)

    def test_51_trend_arrows_calculation(self):
        self.assertEqual(sales_ledger.get_trend_arrow(5, 0), "🔺 +100%")
        self.assertEqual(sales_ledger.get_trend_arrow(0, 0), "➖ 0%")
        self.assertEqual(sales_ledger.get_trend_arrow(6, 4), "🔺 +50%")
        self.assertEqual(sales_ledger.get_trend_arrow(2, 4), "🔻 -50%")
        self.assertEqual(sales_ledger.get_trend_arrow(3, 3), "➖ 0%")

    def test_52_report_generation_empty(self):
        rep = sales_ledger.generate_sales_report("week", db_path=self.conn)
        self.assertIn("0 sales", rep)
        self.assertIn("Ee period lo sales emi record avvaledu", rep)

    def test_53_report_generation_with_data(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        sales_ledger.record_sale("3", "Gumroad", 3, ts=now, db_path=self.conn)
        sales_ledger.record_sale("5", "Etsy", 1, ts=now, db_path=self.conn)
        rep = sales_ledger.generate_sales_report("week", now=now, db_path=self.conn)
        self.assertIn("Sales Report — Ee Vaaram", rep)
        self.assertIn("Best Pack: Pack 3 (3 sales)", rep)
        self.assertIn("Gumroad: 3 sales", rep)
        self.assertIn("Etsy: 1 sale", rep)
        self.assertIn("Celebration", rep)

    def test_54_report_privacy_no_money_amount_in_report(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        # Even if amount is entered into DB:
        sales_ledger.record_sale("3", "Gumroad", 1, ts=now, amount=99.0, currency="USD", db_path=self.conn)
        rep = sales_ledger.generate_sales_report("today", now=now, db_path=self.conn)
        # Verify no currency or money indicators leaked into report
        self.assertNotIn("99", rep)
        self.assertNotIn("USD", rep)
        self.assertNotIn("$", rep)
        self.assertNotIn("INR", rep)
        self.assertNotIn("₹", rep)


class TestBestSellersFeedPrivacy(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        sales_ledger.ensure_sales_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_55_best_sellers_counts_only(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        sales_ledger.record_sale("3", "Gumroad", 5, ts=now, amount=100.0, currency="USD", db_path=self.conn)
        sales_ledger.record_sale("1", "Etsy", 10, ts=now, amount=200.0, currency="USD", db_path=self.conn)
        sales_ledger.record_sale("2", "Topmate", 2, ts=now, db_path=self.conn)

        bs = sales_ledger.best_sellers(db=self.conn)
        self.assertEqual(len(bs), 3)
        self.assertEqual(bs[0], {"pack_id": "1", "count": 10})
        self.assertEqual(bs[1], {"pack_id": "3", "count": 5})
        self.assertEqual(bs[2], {"pack_id": "2", "count": 2})

        # Ensure STRICT privacy: no 'amount' or 'currency' key in any item
        for item in bs:
            self.assertNotIn("amount", item)
            self.assertNotIn("currency", item)
            self.assertNotIn("price", item)
            self.assertNotIn("revenue", item)

    def test_56_best_sellers_with_limit(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        for i in range(1, 10):
            sales_ledger.record_sale(str(i), "Gumroad", i, ts=now, db_path=self.conn)
        top3 = sales_ledger.best_sellers(db=self.conn, limit=3)
        self.assertEqual(len(top3), 3)
        self.assertEqual(top3[0]["pack_id"], "9")
        self.assertEqual(top3[0]["count"], 9)

    def test_57_best_sellers_with_days_cutoff(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        # Old sale (20 days ago)
        sales_ledger.record_sale("9", "Gumroad", 50, ts=now - datetime.timedelta(days=20), db_path=self.conn)
        # Recent sale (2 days ago)
        sales_ledger.record_sale("2", "Gumroad", 5, ts=now - datetime.timedelta(days=2), db_path=self.conn)

        bs_last7 = sales_ledger.best_sellers(db=self.conn, days=7)
        self.assertEqual(len(bs_last7), 1)
        self.assertEqual(bs_last7[0]["pack_id"], "2")
        self.assertEqual(bs_last7[0]["count"], 5)


class TestTelegramPluginTgSales(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        sales_ledger.ensure_sales_schema(self.conn)
        self.ctx = {
            "db_path": self.conn,
            "chat_id": SAMPLE_CHAT_ID,
            "now": datetime.datetime.now(datetime.timezone.utc),
        }

    def tearDown(self):
        self.conn.close()

    def test_58_tg_sales_commands_metadata(self):
        self.assertTrue(hasattr(tg_sales, "COMMANDS"))
        cmds = [c["cmd"] for c in tg_sales.COMMANDS]
        self.assertIn("/sales", cmds)

    def test_59_tg_sales_record_via_text(self):
        res = tg_sales.handle("sale pack 3 Gumroad", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("text", res)
        self.assertIn("Pack 3", res["text"])
        self.assertIn("Gumroad", res["text"])

    def test_60_tg_sales_record_with_amount_in_private_reply(self):
        # The owner provided amount: it should appear in the private Telegram reply confirming it
        res = tg_sales.handle("pack 5 ki 2 sales Etsy $20", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Pack 5", res["text"])
        self.assertIn("USD 20", res["text"])

    def test_61_tg_sales_report_today(self):
        sales_ledger.record_sale("3", "Gumroad", 1, db_path=self.conn)
        res = tg_sales.handle("/sales today", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("Sales Report — Ee Roju", res["text"])
        self.assertIn("Pack 3", res["text"])

    def test_62_tg_sales_undo_command(self):
        sales_ledger.record_sale("3", "Gumroad", 1, db_path=self.conn)
        res = tg_sales.handle("undo last sale", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("teesesam", res["text"])
        self.assertIn("Pack 3", res["text"])

    def test_63_tg_sales_ignores_non_sales_text(self):
        self.assertIsNone(tg_sales.handle("namaste", self.ctx))
        self.assertIsNone(tg_sales.handle("/view 4", self.ctx))
        self.assertIsNone(tg_sales.handle("/approve 2", self.ctx))

    def test_64_tg_sales_ignores_builtin_commands(self):
        self.assertIsNone(tg_sales.handle("/status", self.ctx))
        self.assertIsNone(tg_sales.handle("/help", self.ctx))
        self.assertIsNone(tg_sales.handle("/today", self.ctx))
        self.assertIsNone(tg_sales.handle("/packs", self.ctx))
        self.assertIsNone(tg_sales.handle("/week", self.ctx))


if __name__ == "__main__":
    unittest.main()
