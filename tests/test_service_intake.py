"""Tests for service_intake.py — income stream D intake pipeline."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import service_intake


class TestValidation(unittest.TestCase):
    def test_valid_request(self):
        self.assertEqual(service_intake.validate_request("bio", "John Doe", "CEO of Fishery"), [])

    def test_unknown_type(self):
        errs = service_intake.validate_request("invoice", "title", "brief")
        self.assertTrue(any("unknown" in e for e in errs))

    def test_empty_title(self):
        errs = service_intake.validate_request("bio", "", "brief")
        self.assertTrue(any("title" in e for e in errs))

    def test_title_too_long(self):
        errs = service_intake.validate_request("bio", "x" * 121, "brief")
        self.assertTrue(any("title" in e for e in errs))

    def test_empty_brief(self):
        errs = service_intake.validate_request("bio", "title", "")
        self.assertTrue(any("brief" in e for e in errs))

    def test_brief_too_long(self):
        errs = service_intake.validate_request("bio", "title", "x" * 2001)
        self.assertTrue(any("brief" in e for e in errs))

    def test_all_allowed_types(self):
        for kind in service_intake.ALLOWED_TYPES:
            errs = service_intake.validate_request(kind, "Title", "Brief text")
            self.assertEqual(errs, [], msg=f"{kind} should be allowed")


class TestAddAndList(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "test.db")

    def test_add_returns_id(self):
        rid, errs = service_intake.add_request("bio", "Test Bio", "About someone", db_path=self.db)
        self.assertIsNone(None if errs else None)  # no errors
        self.assertEqual(errs, [])
        self.assertIsNotNone(rid)
        self.assertGreater(rid, 0)

    def test_add_invalid_returns_errors(self):
        rid, errs = service_intake.add_request("unknown", "t", "b", db_path=self.db)
        self.assertIsNone(rid)
        self.assertTrue(len(errs) > 0)

    def test_list_empty(self):
        reqs = service_intake.list_requests(db_path=self.db)
        self.assertEqual(reqs, [])

    def test_list_after_add(self):
        service_intake.add_request("bio", "T1", "B1", db_path=self.db)
        service_intake.add_request("prompt", "T2", "B2", db_path=self.db)
        reqs = service_intake.list_requests(db_path=self.db)
        self.assertEqual(len(reqs), 2)

    def test_list_by_status(self):
        service_intake.add_request("bio", "T1", "B1", db_path=self.db)
        open_reqs = service_intake.list_requests(db_path=self.db, status="open")
        self.assertEqual(len(open_reqs), 1)
        approved_reqs = service_intake.list_requests(db_path=self.db, status="approved")
        self.assertEqual(len(approved_reqs), 0)

    def test_get_request(self):
        rid, _ = service_intake.add_request("summary", "Doc Title", "Summarise this", db_path=self.db)
        req = service_intake.get_request(rid, db_path=self.db)
        self.assertIsNotNone(req)
        self.assertEqual(req["title"], "Doc Title")
        self.assertEqual(req["kind"], "summary")
        self.assertEqual(req["status"], service_intake.STATUS_OPEN)

    def test_get_nonexistent(self):
        req = service_intake.get_request(9999, db_path=self.db)
        self.assertIsNone(req)

    def test_add_queues_draft_task(self):
        import workforce
        rid, _ = service_intake.add_request("caption", "Product", "A shrimp product", db_path=self.db)
        conn = workforce.connect(self.db)
        row = conn.execute("SELECT kind, payload FROM tasks WHERE status='queued'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "draft")
        import json
        payload = json.loads(row[1])
        self.assertEqual(payload["service_request_id"], rid)

    def test_draft_task_priority_high(self):
        import workforce
        service_intake.add_request("bio", "Test", "Brief", db_path=self.db)
        conn = workforce.connect(self.db)
        row = conn.execute("SELECT priority FROM tasks WHERE kind='draft'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertLessEqual(row[0], 3)  # priority 2 = high (lower number = higher priority)


class TestCancelAndExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "test.db")
        self.exports = os.path.join(self.tmp, "exports")

    def test_cancel_open(self):
        rid, _ = service_intake.add_request("outline", "Blog", "About fish", db_path=self.db)
        ok, msg = service_intake.cancel_request(rid, "changed mind", db_path=self.db)
        self.assertTrue(ok)
        req = service_intake.get_request(rid, db_path=self.db)
        self.assertEqual(req["status"], service_intake.STATUS_CANCELLED)

    def test_cancel_nonexistent(self):
        ok, msg = service_intake.cancel_request(9999, db_path=self.db)
        self.assertFalse(ok)
        self.assertIn("not found", msg)

    def test_cannot_export_unapproved(self):
        rid, _ = service_intake.add_request("bio", "Test", "Brief", db_path=self.db)
        path, err = service_intake.export_approved(rid, db_path=self.db, exports_dir=self.exports)
        self.assertIsNone(path)
        self.assertIsNotNone(err)

    def test_cannot_cancel_cancelled(self):
        rid, _ = service_intake.add_request("bio", "T", "B", db_path=self.db)
        service_intake.cancel_request(rid, db_path=self.db)
        ok, _ = service_intake.cancel_request(rid, db_path=self.db)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
