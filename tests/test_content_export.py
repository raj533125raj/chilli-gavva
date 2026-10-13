"""Tests for content_export.py."""
import importlib, json, os, shutil, sys, tempfile, unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


class TestSlug(unittest.TestCase):
    def test_basic(self):
        import content_export
        self.assertEqual(content_export._slug("Hello World"), "hello-world")

    def test_special_chars(self):
        import content_export
        self.assertEqual(content_export._slug("Fish & Chips!"), "fish-chips")

    def test_truncate(self):
        import content_export
        s = content_export._slug("a" * 100, maxlen=10)
        self.assertLessEqual(len(s), 10)


class TestExportApproved(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        import content_export
        self._orig_dir = content_export.CONTENT_DIR
        content_export.CONTENT_DIR = os.path.join(self.tmp, "content")
        # Create a fresh DB using guardrails schema
        import guardrails
        self.db = os.path.join(self.tmp, "test.db")
        guardrails.connect(self.db).close()

    def tearDown(self):
        import content_export
        content_export.CONTENT_DIR = self._orig_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _insert_approval(self, title="Test article", kind="research"):
        import guardrails
        conn = guardrails.connect(self.db)
        payload = json.dumps({"topic": "shrimp prices"})
        conn.execute("""INSERT INTO approvals (ts, kind, title, payload, status, review)
                        VALUES (?,?,?,?,?,?)""",
                     ("2026-10-13T10:00:00", kind, title, payload, "approved", "Good score 80/100"))
        approval_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit(); conn.close()
        return approval_id

    def test_export_creates_file(self):
        import content_export
        self._insert_approval()
        paths = content_export.export_approved(db_path=self.db)
        self.assertEqual(len(paths), 1)
        self.assertTrue(os.path.exists(paths[0]))

    def test_exported_file_has_title(self):
        import content_export
        self._insert_approval(title="Shrimp Price Update")
        paths = content_export.export_approved(db_path=self.db)
        with open(paths[0]) as f:
            content = f.read()
        self.assertIn("Shrimp Price Update", content)

    def test_no_double_export(self):
        import content_export
        self._insert_approval()
        paths1 = content_export.export_approved(db_path=self.db)
        paths2 = content_export.export_approved(db_path=self.db)
        self.assertEqual(len(paths1), 1)
        self.assertEqual(len(paths2), 0)

    def test_overwrite_flag(self):
        import content_export
        self._insert_approval()
        content_export.export_approved(db_path=self.db)
        paths2 = content_export.export_approved(db_path=self.db, overwrite=True)
        self.assertEqual(len(paths2), 1)

    def test_empty_db_returns_empty(self):
        import content_export
        paths = content_export.export_approved(db_path=self.db)
        self.assertEqual(paths, [])

    def test_list_exported(self):
        import content_export
        self._insert_approval()
        content_export.export_approved(db_path=self.db)
        files = content_export.list_exported()
        self.assertEqual(len(files), 1)
        self.assertIn("file", files[0])
        self.assertIn("bytes", files[0])

    def test_file_contains_topic(self):
        import content_export
        self._insert_approval()
        paths = content_export.export_approved(db_path=self.db)
        with open(paths[0]) as f:
            txt = f.read()
        self.assertIn("shrimp prices", txt)

    def test_filename_includes_date(self):
        import content_export
        self._insert_approval()
        paths = content_export.export_approved(db_path=self.db)
        self.assertIn("2026-10-13", os.path.basename(paths[0]))


if __name__ == "__main__":
    unittest.main()
