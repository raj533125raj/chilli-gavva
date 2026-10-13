"""Tests for new guardrails features: CHILLI_TEST isolation, approve-above, score parsing."""
import os
import sqlite3
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


class TestChilliTestFlag(unittest.TestCase):
    """record_call() must be a no-op when CHILLI_TEST=1."""

    def test_record_call_noop_in_test_mode(self):
        import importlib, guardrails
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        original = os.environ.get("CHILLI_TEST")
        try:
            guardrails.connect(tmp.name).close()
            os.environ["CHILLI_TEST"] = "1"
            importlib.reload(guardrails)
            before = guardrails.calls_today(tmp.name)
            guardrails.record_call(tmp.name)
            guardrails.record_call(tmp.name)
            after = guardrails.calls_today(tmp.name)
            self.assertEqual(before, after, "record_call() must not write when CHILLI_TEST=1")
        finally:
            if original is None:
                os.environ.pop("CHILLI_TEST", None)
            else:
                os.environ["CHILLI_TEST"] = original
            importlib.reload(guardrails)
            os.unlink(tmp.name)

    def test_record_call_works_without_flag(self):
        import importlib, guardrails
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        original = os.environ.pop("CHILLI_TEST", None)
        try:
            importlib.reload(guardrails)
            guardrails.connect(tmp.name).close()
            before = guardrails.calls_today(tmp.name)
            guardrails.record_call(tmp.name)
            after = guardrails.calls_today(tmp.name)
            self.assertEqual(after, before + 1)
        finally:
            if original is not None:
                os.environ["CHILLI_TEST"] = original
            importlib.reload(guardrails)
            os.unlink(tmp.name)


class TestApproveAbove(unittest.TestCase):
    """approve_above() must bulk-approve items whose score meets the threshold."""

    def setUp(self):
        import guardrails
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db = tmp.name
        guardrails.connect(self.db).close()

    def tearDown(self):
        os.unlink(self.db)

    def _add(self, review):
        import guardrails
        return guardrails.request_approval("public_draft", "Test", "{}", review=review, db_path=self.db)

    def test_approve_above_approves_high_scores(self):
        import guardrails
        id_high = self._add("score 85/100: great content")
        id_low  = self._add("score 55/100: too short")
        approved = guardrails.approve_above(75, self.db)
        self.assertIn(id_high, approved)
        self.assertNotIn(id_low, approved)

    def test_approve_above_skips_no_score(self):
        import guardrails
        id_noscore = self._add("looks good overall")
        id_scored  = self._add("score 90/100: excellent")
        approved = guardrails.approve_above(75, self.db)
        self.assertNotIn(id_noscore, approved)
        self.assertIn(id_scored, approved)

    def test_approve_above_returns_empty_when_none_qualify(self):
        import guardrails
        self._add("score 40/100: too short")
        approved = guardrails.approve_above(75, self.db)
        self.assertEqual(approved, [])

    def test_approved_items_change_status(self):
        import guardrails
        aid = self._add("score 80/100: solid")
        guardrails.approve_above(70, self.db)
        item = guardrails.get_approval(aid, self.db)
        self.assertEqual(item["status"], "approved")


class TestParseScore(unittest.TestCase):
    """_parse_score must correctly extract numeric score from review strings."""

    def test_standard_format(self):
        import guardrails
        self.assertEqual(guardrails._parse_score("score 78/100"), 78)

    def test_with_prefix(self):
        import guardrails
        self.assertEqual(guardrails._parse_score("Good score 80/100: nice"), 80)

    def test_no_score_returns_none(self):
        import guardrails
        self.assertIsNone(guardrails._parse_score("looks good overall"))

    def test_none_input(self):
        import guardrails
        self.assertIsNone(guardrails._parse_score(None))


if __name__ == "__main__":
    unittest.main()
