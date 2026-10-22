"""R23 jobs only use owner-approved items and their own product pins."""
import os
import tempfile
import unittest

import guardrails
import publisher


class ApprovedOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "robot.db")
        self.products = os.path.join(self.tmp.name, "products")
        self.addCleanup(self.tmp.cleanup)

    def approval(self, approved=False):
        item_id = guardrails.request_approval("public_draft", "draft", "body", db_path=self.db)
        if approved:
            guardrails.decide(item_id, True, self.db)
        return item_id

    def test_unapproved_items_cannot_be_queued(self):
        item_id = self.approval()
        self.assertIsNone(publisher.queue_job("gumroad", item_id, "listing", self.db))
        self.assertEqual(publisher.jobs(self.db), [])

    def test_pins_must_belong_to_an_approved_built_product(self):
        item_id = self.approval(True)
        folder = f"{item_id}-planner"
        pins = os.path.join(self.products, folder, "pins")
        os.makedirs(pins)
        conn = publisher.connect(self.db)
        conn.execute("CREATE TABLE IF NOT EXISTS product_builds (approval_id INTEGER PRIMARY KEY, status TEXT, folder TEXT)")
        conn.execute("INSERT INTO product_builds (approval_id, status, folder) VALUES (?, 'built', ?)", (item_id, folder))
        conn.commit()
        conn.close()
        self.assertEqual(publisher.approved_product_pins(item_id, self.products, self.db), pins)
        self.assertIsInstance(publisher.queue_product_pins(item_id, self.products, self.db), int)
        pending = self.approval()
        self.assertIsNone(publisher.queue_product_pins(pending, self.products, self.db))


if __name__ == "__main__":
    unittest.main()
