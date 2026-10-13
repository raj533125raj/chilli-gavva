"""Tests for agent_memory.py"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agent_memory


def _db():
    return tempfile.mktemp(suffix="_mem_test.db")


class TestAddAndRecall(unittest.TestCase):
    def test_add_returns_id(self):
        db = _db()
        rid = agent_memory.add_fact("ceo", "topic", "fishing articles do well", db_path=db)
        self.assertIsInstance(rid, int)
        self.assertGreater(rid, 0)

    def test_recall_by_tag(self):
        db = _db()
        agent_memory.add_fact("worker", "content", "short titles convert better", db_path=db)
        rows = agent_memory.recall("content", db_path=db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fact"], "short titles convert better")

    def test_recall_empty_tag(self):
        db = _db()
        agent_memory.add_fact("worker", "content", "some fact", db_path=db)
        rows = agent_memory.recall("nonexistent", db_path=db)
        self.assertEqual(rows, [])

    def test_duplicate_reinforces(self):
        db = _db()
        f = "fishing articles perform well in monsoon season"
        rid1 = agent_memory.add_fact("ceo", "topic", f, db_path=db)
        rid2 = agent_memory.add_fact("ceo", "topic", f, db_path=db)
        self.assertEqual(rid1, rid2)
        rows = agent_memory.recall("topic", db_path=db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reinforced"], 1)

    def test_different_tags_separate(self):
        db = _db()
        agent_memory.add_fact("a", "tagA", "fact about A", db_path=db)
        agent_memory.add_fact("b", "tagB", "fact about B", db_path=db)
        self.assertEqual(len(agent_memory.recall("tagA", db_path=db)), 1)
        self.assertEqual(len(agent_memory.recall("tagB", db_path=db)), 1)

    def test_multiple_facts_same_tag(self):
        db = _db()
        for i in range(3):
            agent_memory.add_fact("worker", "content", f"unique fact number {i}", db_path=db)
        rows = agent_memory.recall("content", limit=10, db_path=db)
        self.assertEqual(len(rows), 3)


class TestSearch(unittest.TestCase):
    def test_search_finds_by_keyword(self):
        db = _db()
        agent_memory.add_fact("ceo", "topic", "monsoon season is good for fishing", db_path=db)
        agent_memory.add_fact("ceo", "topic", "summer is good for agriculture", db_path=db)
        results = agent_memory.search("monsoon", db_path=db)
        self.assertEqual(len(results), 1)
        self.assertIn("monsoon", results[0]["fact"])

    def test_search_by_tag(self):
        db = _db()
        agent_memory.add_fact("worker", "fishing-tips", "nets work better at dawn", db_path=db)
        results = agent_memory.search("fishing-tips", db_path=db)
        self.assertEqual(len(results), 1)

    def test_search_no_match(self):
        db = _db()
        agent_memory.add_fact("worker", "content", "some fact", db_path=db)
        results = agent_memory.search("xyzzy_never_matches", db_path=db)
        self.assertEqual(results, [])


class TestFactsForPrompt(unittest.TestCase):
    def test_returns_empty_when_no_facts(self):
        db = _db()
        result = agent_memory.facts_for_prompt("nonexistent", db_path=db)
        self.assertEqual(result, "")

    def test_returns_formatted_string(self):
        db = _db()
        agent_memory.add_fact("ceo", "content", "keep headlines under 60 chars", db_path=db)
        result = agent_memory.facts_for_prompt("content", db_path=db)
        self.assertIn("[Memory: content]", result)
        self.assertIn("keep headlines under 60 chars", result)

    def test_bullet_format(self):
        db = _db()
        agent_memory.add_fact("ceo", "style", "fact one", db_path=db)
        result = agent_memory.facts_for_prompt("style", db_path=db)
        self.assertIn("- fact one", result)


class TestListRecent(unittest.TestCase):
    def test_list_recent(self):
        db = _db()
        for i in range(5):
            agent_memory.add_fact("a", "tag", f"fact {i}", db_path=db)
        rows = agent_memory.list_recent(3, db_path=db)
        self.assertEqual(len(rows), 3)

    def test_list_empty(self):
        db = _db()
        self.assertEqual(agent_memory.list_recent(db_path=db), [])
