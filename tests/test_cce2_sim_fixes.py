"""CCE-2: one test per root cause the day simulator (tools/robot_day_sim.py) found."""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import guardrails


class FreshDatabaseMarketingTests(unittest.TestCase):
    """Sim: 'Marketing agency skipped: OperationalError' at marketing_agency.py:92 on the first run."""

    def test_fresh_database_has_no_ready_products_and_no_crash(self):
        import marketing_agency
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "agent_data.db")
            guardrails.connect(db).close()
            self.assertEqual(marketing_agency._get_ready_products(db_path=db), [])
            conn = sqlite3.connect(db)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            conn.close()
            self.assertNotIn("product_builds", tables)   # reading never creates another module's table

    def test_built_products_are_still_found(self):
        import marketing_agency
        import product_builder
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "agent_data.db")
            aid = guardrails.request_approval("public_draft", "Draft: [Budget Sheets Agent] Sheet", "body",
                                              db_path=db)
            guardrails.decide(aid, True, db)
            product_builder._record(db, aid, "built", f"{aid}-sheet", "ready")
            self.assertEqual([p["id"] for p in marketing_agency._get_ready_products(db_path=db)], [aid])


if __name__ == "__main__":
    unittest.main()
