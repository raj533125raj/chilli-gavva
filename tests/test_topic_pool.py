"""Tests for topic_pool.py."""
import os, sys, tempfile, unittest
from unittest.mock import patch, MagicMock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
import topic_pool


class TestTopicPoolData(unittest.TestCase):
    def test_topics_list_nonempty(self):
        self.assertGreater(len(topic_pool.TOPICS), 10)

    def test_all_topics_are_strings(self):
        for t in topic_pool.TOPICS:
            self.assertIsInstance(t, str)
            self.assertGreater(len(t), 5)

    def test_no_duplicate_topics(self):
        lower = [t.lower() for t in topic_pool.TOPICS]
        self.assertEqual(len(lower), len(set(lower)))


class TestPickTopics(unittest.TestCase):
    def test_returns_n_topics(self):
        topics = topic_pool.pick_topics(n=3, db_path=":memory:")
        self.assertLessEqual(len(topics), 3)

    def test_returns_at_least_one(self):
        topics = topic_pool.pick_topics(n=1, db_path=":memory:")
        self.assertEqual(len(topics), 1)

    def test_excludes_recent(self):
        # Patch _recent_topics to return all topics
        with patch.object(topic_pool, "_recent_topics", return_value={t.lower() for t in topic_pool.TOPICS}):
            # Should still return something (reset logic)
            topics = topic_pool.pick_topics(n=1)
            self.assertEqual(len(topics), 1)

    def test_results_are_strings(self):
        for t in topic_pool.pick_topics(n=5, db_path=":memory:"):
            self.assertIsInstance(t, str)


class TestSeedIfIdle(unittest.TestCase):
    def test_does_not_seed_when_queue_full(self):
        with patch.object(topic_pool, "_pending_count", return_value=10):
            result = topic_pool.seed_if_idle()
            self.assertEqual(result, [])

    def test_seeds_when_idle(self):
        enqueued = []
        mock_wf = MagicMock()
        mock_wf.enqueue = lambda kind, payload, priority=5, db_path=None: enqueued.append(payload["topic"]) or 1
        with patch.object(topic_pool, "_pending_count", return_value=0), \
             patch.object(topic_pool, "_mark_queued", return_value=None), \
             patch.dict(sys.modules, {"workforce": mock_wf}):
            result = topic_pool.seed_if_idle()
            self.assertGreater(len(result), 0)

    def test_force_seeds_even_when_full(self):
        enqueued = []
        mock_wf = MagicMock()
        mock_wf.enqueue = lambda kind, payload, priority=5, db_path=None: enqueued.append(payload["topic"]) or 1
        with patch.object(topic_pool, "_pending_count", return_value=99), \
             patch.object(topic_pool, "_mark_queued", return_value=None), \
             patch.dict(sys.modules, {"workforce": mock_wf}):
            result = topic_pool.seed_if_idle(force=True)
            self.assertGreater(len(result), 0)

    def test_no_exception_on_workforce_failure(self):
        mock_wf = MagicMock()
        mock_wf.enqueue.side_effect = Exception("db error")
        with patch.object(topic_pool, "_pending_count", return_value=0), \
             patch.object(topic_pool, "_mark_queued", return_value=None), \
             patch.dict(sys.modules, {"workforce": mock_wf}):
            # Should not raise
            result = topic_pool.seed_if_idle()
            self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
