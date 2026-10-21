import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import telegram_inbox as inbox  # noqa: E402


class ApprovalLinkVectorTests(unittest.TestCase):
    def test_shared_vectors(self):
        path = os.path.join(os.path.dirname(__file__), "fixtures", "approval_links.json")
        with open(path, encoding="utf-8") as fixture:
            vectors = json.load(fixture)
        self.assertGreaterEqual(len(vectors), 8)
        for vector in vectors:
            with self.subTest(request_id=vector["requestId"]):
                parsed = inbox.parse_start_payload("/start " + vector["payload"])
                self.assertEqual(parsed is not None, vector["valid"])
                if vector["valid"]:
                    self.assertEqual(parsed, vector["requestId"])

    def test_64_and_65_character_payload_boundaries(self):
        path = os.path.join(os.path.dirname(__file__), "fixtures", "approval_links.json")
        with open(path, encoding="utf-8") as fixture:
            vectors = json.load(fixture)
        by_length = {len(item["payload"]): item for item in vectors}
        self.assertIn(64, by_length)
        self.assertFalse(by_length[64]["valid"])
        self.assertIn(65, by_length)
        self.assertFalse(by_length[65]["valid"])


if __name__ == "__main__":
    unittest.main()
