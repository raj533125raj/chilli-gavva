"""R23 publishing switches are local, off first, and controlled by owner pause."""
import os
import tempfile
import unittest

import publisher
import telegram_inbox


class PublishSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "robot.db")
        self.addCleanup(self.tmp.cleanup)

    def test_all_publish_types_start_off_and_global_stop_wins(self):
        for publish_type in publisher.PUBLISH_TYPES:
            self.assertFalse(publisher.enabled(publish_type, self.db))
            self.assertFalse(publisher.can_publish(publish_type, self.db))
            publisher.set_enabled(publish_type, True, self.db)
            self.assertTrue(publisher.can_publish(publish_type, self.db))
        publisher.set_global_stop(True, self.db)
        self.assertTrue(publisher.globally_stopped(self.db))
        self.assertFalse(publisher.can_publish("pinterest", self.db))

    def test_owner_pause_and_resume_stop_and_restart_publishing(self):
        publisher.set_enabled("telegram", True, self.db)
        telegram_inbox.handle_command("pause", [], self.db)
        self.assertTrue(publisher.globally_stopped(self.db))
        self.assertFalse(publisher.can_publish("telegram", self.db))
        telegram_inbox.handle_command("resume", [], self.db)
        self.assertFalse(publisher.globally_stopped(self.db))
        self.assertTrue(publisher.can_publish("telegram", self.db))


if __name__ == "__main__":
    unittest.main()
