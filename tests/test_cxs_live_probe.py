"""Live-shaped queue and cycle evidence, without modifying the real database."""
import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path
import guardrails
from tools import approval_probe


class LiveQueueTests(unittest.TestCase):
    def test_twelve_waiting_including_rule_and_four_recent_approvals(self):
        now=dt.datetime(2026,11,22,12,5,tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'queue.db'
            guardrails.connect(str(db)).close()
            ids=approval_probe.seed_queue(db,now,live=True)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0],11)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM learned_rules WHERE status='proposed'").fetchone()[0],1)
                approved=conn.execute("SELECT decided_ts FROM approvals WHERE status='approved'").fetchall()
                self.assertEqual(len(approved),4)
                for (stamp,) in approved:
                    self.assertLess(now.replace(tzinfo=None)-dt.datetime.fromisoformat(stamp),dt.timedelta(days=1))
                oldest=conn.execute("SELECT MIN(ts) FROM approvals WHERE status='pending'").fetchone()[0]
                self.assertEqual(now.replace(tzinfo=None)-dt.datetime.fromisoformat(oldest),dt.timedelta(hours=201))
            self.assertEqual(len(ids),15)
            scores=[int(row['review'].split('/100')[0].split()[-1]) for row in approval_probe.live_queue(now)]
            self.assertEqual((min(scores),max(scores)),(40,98))
