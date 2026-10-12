import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine  # noqa: E402
import export_status  # noqa: E402
import record_earning as re_  # noqa: E402


class EarningsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "e.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_requires_proof_and_sane_amount(self):
        for bad in [dict(amount=5, reference="", title="x"), dict(amount=5, reference="  ", title="x"),
                    dict(amount=0, reference="r", title="x"), dict(amount=-3, reference="r", title="x"),
                    dict(amount=re_.MAX_AMOUNT + 1, reference="r", title="x")]:
            with self.assertRaises(ValueError):
                re_.add(db_path=self.db, **bad)
        self.assertEqual(re_.total(self.db), 0)

    def test_duplicate_reference_rejected(self):
        re_.add(10, "INV-1", "a", db_path=self.db)
        with self.assertRaises(ValueError):
            re_.add(10, "INV-1", "again", db_path=self.db)
        self.assertEqual(re_.total(self.db), 10)

    def test_void_removes_from_totals_everywhere(self):
        a = re_.add(10, "INV-1", "a", db_path=self.db)
        re_.add(5.5, "INV-2", "b", db_path=self.db)
        self.assertEqual(re_.total(self.db), 15.5)
        self.assertEqual(export_status.build_status(self.db)["money"]["real_usd"], 15.5)
        re_.void(a, "refunded", self.db)
        self.assertEqual(re_.total(self.db), 5.5)
        self.assertEqual(export_status.build_status(self.db)["money"]["real_usd"], 5.5)
        self.assertEqual(engine.EnterpriseStorage(self.db).get_summary()["real_earnings_usd"], 5.5)
        with self.assertRaises(ValueError):
            re_.void(a, "again", self.db)
        with self.assertRaises(ValueError):
            re_.void(a + 100, "no such id", self.db)
        with self.assertRaises(ValueError):
            re_.void(a, "", self.db)

    def test_cannot_void_a_simulated_row(self):
        store = engine.EnterpriseStorage(self.db)
        store.save_campaign("n", "t", 1, 99.0, "COMPLETED")        # simulated by default
        with self.assertRaises(ValueError):
            re_.void(1, "nope", self.db)
        self.assertEqual(store.get_summary()["simulated_earnings_usd"], 99.0)

    def test_dashboard_mode_becomes_mixed(self):
        re_.add(3, "INV-9", "c", db_path=self.db)
        self.assertEqual(export_status.build_status(self.db)["mode"], "mixed")


if __name__ == "__main__":
    unittest.main()
