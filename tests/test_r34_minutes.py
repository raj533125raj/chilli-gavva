"""Actions minutes stay unknown when the robot has no local persisted source."""
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import export_status


class ActionsMinutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "minutes.db")
        self.output = os.path.join(self.tmp.name, "web", "data", "status.json")
        self.legacy = {"generated_at": "2026-10-23T12:00:00Z", "llm": {"calls_today": 0, "cap": 20}}
        self.enterContext(mock.patch.object(export_status, "build_status", return_value=self.legacy))
        self.remote = self.enterContext(mock.patch("always_on.status", side_effect=AssertionError("no remote lookup")))
        self.network = self.enterContext(mock.patch("socket.socket.connect", side_effect=AssertionError("offline test")))

    def tearDown(self):
        self.remote.assert_not_called()
        self.network.assert_not_called()

    def read_export(self):
        export_status.write_status(self.output, self.db)
        with open(self.output, encoding="utf-8") as handle:
            return json.load(handle)

    def test_no_local_record_means_null_usage_and_limit_not_zero_or_a_plan_guess(self):
        result = self.read_export()
        self.assertEqual(result["minutes"], {"used_this_month": None, "limit": None})
        self.assertEqual(set(result["minutes"]), {"used_this_month", "limit"})
        self.assertFalse(os.path.exists(self.db))

    def test_llm_call_counts_are_not_github_actions_minutes(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE usage_log (day TEXT PRIMARY KEY, llm_calls INTEGER)")
            conn.execute("INSERT INTO usage_log VALUES ('2026-10-23', 456)")
            conn.execute("CREATE TABLE provider_log (day TEXT, provider TEXT, ok INTEGER, fail INTEGER)")
            conn.execute("INSERT INTO provider_log VALUES ('2026-10-23', 'Groq', 123, 4)")
        self.legacy["llm"]["calls_today"] = 456
        result = self.read_export()
        self.assertEqual(result["llm"]["calls_today"], 456)
        self.assertEqual(result["minutes"], {"used_this_month": None, "limit": None})

    def test_new_export_fields_do_not_mutate_or_replace_the_legacy_snapshot(self):
        before = json.loads(json.dumps(self.legacy))
        result = self.read_export()
        self.assertEqual(self.legacy, before)
        self.assertEqual({key: result[key] for key in before}, before)
        self.assertEqual(set(result) - set(before), {"ai_health", "minutes", "scout_ideas_count", "trend_items_count", "media_items_count", "marketing_kits_count"})


if __name__ == "__main__":
    unittest.main()
