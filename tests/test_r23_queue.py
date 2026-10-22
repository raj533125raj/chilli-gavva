"""R23 publish queue tests use only a temporary database."""
import datetime
import os
import tempfile
import unittest

import publisher


class PublishQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "robot.db")
        self.addCleanup(self.tmp.cleanup)

    def test_queue_has_required_fields_and_never_duplicates_item_type(self):
        created = datetime.datetime(2026, 10, 22, 4, 5, tzinfo=datetime.timezone.utc)
        job_id = publisher.queue_job("pinterest", 7, "products/7/pins/one.png", self.db, created)
        self.assertIsInstance(job_id, int)
        self.assertIsNone(publisher.queue_job("pinterest", 7, "products/7/pins/two.png", self.db, created))
        self.assertIsInstance(publisher.queue_job("telegram", 7, "products/7/post.txt", self.db, created), int)
        job = publisher.jobs(self.db)[0]
        self.assertEqual(set(job), {"id", "type", "item_id", "payload_path", "status", "attempts", "last_error",
                                    "created_ts", "finished_ts"})
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["created_ts"], "2026-10-22T04:05:00Z")

    def test_finished_job_records_safe_result_and_utc_time(self):
        job_id = publisher.queue_job("gumroad", 4, "products/4/listing.txt", self.db)
        finished = datetime.datetime(2026, 10, 22, 4, 6, tzinfo=datetime.timezone.utc)
        self.assertTrue(publisher.finish_job(job_id, "failed", "temporary error", self.db, finished))
        job = publisher.jobs(self.db)[0]
        self.assertEqual((job["status"], job["attempts"], job["last_error"], job["finished_ts"]),
                         ("failed", 1, "temporary error", "2026-10-22T04:06:00Z"))


if __name__ == "__main__":
    unittest.main()
