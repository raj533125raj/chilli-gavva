"""R22 public-status privacy checks (all data is local and temporary)."""
import json
import os
import tempfile
import unittest

import export_status
import guardrails


class PublicDraftTitleTests(unittest.TestCase):
    def test_pending_draft_title_is_anonymous_in_written_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "robot.db")
            path = os.path.join(tmp, "status.json")
            title = "Draft: [ADHD Planners Agent] Travel itinerary planner for busy families"
            approval_id = guardrails.request_approval("public_draft", title, "private draft", db_path=db)

            export_status.write_status(path, db)
            with open(path, encoding="utf-8") as status_file:
                status = json.load(status_file)
            blob = json.dumps(status)

            self.assertNotIn(title, blob)
            self.assertEqual(status["pending_approvals"], [{
                "id": approval_id, "ts": status["pending_approvals"][0]["ts"],
                "kind": "public_draft", "title": f"Draft #{approval_id}",
            }])


if __name__ == "__main__":
    unittest.main()
