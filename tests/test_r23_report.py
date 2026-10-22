"""R23 publishing results create the short daily owner-report lines."""
import datetime
import os
import tempfile
import unittest

import guardrails
import publisher


class PublishReportTests(unittest.TestCase):
    def test_every_finished_publish_job_is_reflected_in_daily_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "robot.db")
            item_id = guardrails.request_approval("public_draft", "approved", "body", db_path=db)
            guardrails.decide(item_id, True, db)
            one = publisher.queue_job("pinterest", item_id, "pin", db)
            two = publisher.queue_job("telegram", item_id, "post", db)
            now = datetime.datetime(2026, 10, 22, 4, 0, tzinfo=datetime.timezone.utc)
            publisher.finish_job(one, "done", db_path=db, now=now)
            publisher.finish_job(two, "failed", "permanent error", db, now)
            self.assertEqual(publisher.daily_report_lines(db, "2026-10-22"),
                             ["Pinterest: 1 posted, 0 failed", "Telegram: 0 posted, 1 failed"])


if __name__ == "__main__":
    unittest.main()
