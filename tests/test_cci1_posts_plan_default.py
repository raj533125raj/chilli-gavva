"""CCI-1: tg_posts reads the post plan path at call time.

The plan path used to be a default argument, bound when tg_posts was imported. A test (or any code) that
patched post_planner.PLAN_PATH later was ignored, so /posted read a stale path. Now the default is resolved
on each call.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import post_planner
import tg_posts


class PlanPathAtCallTimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plan_path = Path(self.tmp.name) / "post_plan.json"
        today = tg_posts._get_ist_datetime(None).date().isoformat()
        row = {"post_id": "p1", "day": 1, "date": today, "status": "todo", "channel": "pinterest",
               "pack_id": "8-x", "asset": "pin-1.png", "caption_short": "Pin", "hashtags": []}
        self.plan_path.write_text(json.dumps([row]), encoding="utf-8")
        patch = mock.patch.object(post_planner, "PLAN_PATH", str(self.plan_path))
        patch.start()
        self.addCleanup(patch.stop)

    def test_posted_reads_the_patched_plan_path(self):
        reply = tg_posts.handle_posted_n(1, {})
        self.assertNotIn("ledu", reply["text"])
        self.assertIn("Post 1 done", reply["text"])
        saved = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["status"], "done")

    def test_explicit_plan_path_still_wins(self):
        other = Path(self.tmp.name) / "other.json"
        other.write_text("[]", encoding="utf-8")
        reply = tg_posts.handle_posted_n(1, {}, plan_path=str(other))
        self.assertIn("ledu", reply["text"])


if __name__ == "__main__":
    unittest.main()
