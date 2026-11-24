"""The November backlog cannot be assigned invented outage/rejection counts."""
import tempfile
import unittest
from pathlib import Path
import guardrails
import product_pipeline
import product_council
from tools import council_audit


class CouncilAudit(unittest.TestCase):
    def test_twenty_legacy_failures_are_honestly_unclassified(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / 'robot.db')
            for i in range(20):
                aid = guardrails.request_approval('public_draft', 'Resume bundle', 'Approved content', db_path=db)
                with guardrails.connect(db) as conn:
                    conn.execute("UPDATE approvals SET status='approved' WHERE id=?", (aid,))
                with product_pipeline.connect(db) as conn:
                    conn.execute("INSERT INTO product_deliveries (approval_id,status,reason) VALUES (?, 'retry', ?)",
                                 (aid, 'council: Council did not pass the approved draft'))
                product_council.log_debate(aid, 1, 'council_summary', 'NEEDS YOU',
                                          'Buyer conviction below threshold', db_path=db)
            self.assertEqual(council_audit.counts(db), {'legacy outcome lacks AI evidence': 20})

    def test_missing_database_is_not_created(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'missing.db'
            with self.assertRaises(sqlite3.Error):
                council_audit.counts(path)
            self.assertFalse(path.exists())
