"""Regression tests for OCA path A (Kiro review B fixes)."""

import datetime
import json
import re
import sqlite3
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import report_pdf

NOW = datetime.datetime(2026, 11, 12, 9, 30, tzinfo=datetime.timezone.utc)


def _seed_report_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, "
                 "title TEXT, payload TEXT, status TEXT, review TEXT, decided_ts TEXT, reason TEXT)")
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, "
                 "payload TEXT, status TEXT, attempts INTEGER, agent TEXT, result TEXT, error TEXT, "
                 "priority INTEGER, finished_ts TEXT)")
    conn.execute("CREATE TABLE provider_log (day TEXT, provider TEXT, ok INTEGER DEFAULT 0, "
                 "fail INTEGER DEFAULT 0, PRIMARY KEY (day, provider))")
    conn.commit()
    conn.close()


def _text_ops(pdf_bytes):
    streams = re.findall(rb"<< /Length \d+ >>\nstream\n(.*?)\nendstream", pdf_bytes, flags=re.DOTALL)
    ops = []
    for stream in streams:
        ops.extend(re.findall(rb"\((.*?)\) Tj", stream))
    return b" ".join(ops).decode("latin-1")


class ReportPdfSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "state.db"
        self.out = self.root / "out"

    def test_scrub_replaces_telugu_with_visible_ascii(self):
        value = report_pdf._scrub("తెలుగు")
        self.assertTrue(value)
        self.assertTrue(value.isascii(), value)
        self.assertIn("?", value)

    def test_scrub_keeps_tenglish_text(self):
        line = "Ee week ki inka data ledu; robot ready ga undi."
        self.assertEqual(report_pdf._scrub(line), line)

    def test_scrub_replaces_unknown_symbols_not_drops_whole_run(self):
        value = report_pdf._scrub("A😀B")
        self.assertTrue(value.startswith("A"))
        self.assertTrue(value.endswith("B"))
        self.assertGreater(len(value), 2)

    def test_pdf_keeps_telugu_provider_as_fallback(self):
        _seed_report_db(self.db)
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO provider_log (day, provider, ok, fail) VALUES (?,?,?,?)",
                     ("2026-11-10", "తెలుగు", 1, 0))
        conn.commit()
        conn.close()
        result = report_pdf.build_weekly_report(db_path=self.db, now=NOW, out_dir=self.out)
        visible = _text_ops(Path(result["path"]).read_bytes())
        self.assertIn("??????", visible)

    def test_pdf_keeps_every_tenglish_provider_line(self):
        _seed_report_db(self.db)
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO provider_log (day, provider, ok, fail) VALUES (?,?,?,?)",
                     ("2026-11-10", "Ee week provider", 1, 0))
        conn.commit()
        conn.close()
        result = report_pdf.build_weekly_report(db_path=self.db, now=NOW, out_dir=self.out)
        self.assertIn("Ee week provider", _text_ops(Path(result["path"]).read_bytes()))

    def test_save_without_pages_raises_clear_error(self):
        pdf = report_pdf._ReportPdf()
        with self.assertRaises(ValueError):
            pdf.save(self.root / "empty.pdf")

    def test_footer_is_on_every_page(self):
        _seed_report_db(self.db)
        result = report_pdf.build_weekly_report(db_path=self.db, now=NOW, out_dir=self.out)
        data = Path(result["path"]).read_bytes()
        visible = _text_ops(data)
        for number in range(1, 7):
            self.assertIn(f"Page {number} of 6", visible)

    def test_footer_does_not_mutate_saved_page_list(self):
        pdf = report_pdf._ReportPdf()
        pdf.new_page()
        pdf.text(40, 40, "hello")
        original = pdf.pages
        footer = pdf._footer_ops(1, 1)
        self.assertIs(pdf.pages, original)
        self.assertEqual(len(pdf.pages), 1)
        self.assertTrue(footer)
        self.assertFalse(any("Page 1 of 1" in op for op in pdf.pages[0]))

    def test_missing_product_tables_are_zero_not_error(self):
        _seed_report_db(self.db)
        data = report_pdf.collect_weekly_data(db_path=self.db, now=NOW)
        self.assertEqual(data["packs_built"], 0)
        self.assertEqual(data["ready"], 0)
        self.assertEqual(data["needs_fix"], 0)

    def test_corrupt_plan_adds_warning_to_problems(self):
        _seed_report_db(self.db)
        plan = self.root / "post_plan.json"
        plan.write_text("{not json", encoding="utf-8")
        data = report_pdf.collect_weekly_data(db_path=self.db, now=NOW, plan_path=plan)
        self.assertTrue(data["plan_warning"])
        self.assertIn("Post plan", " ".join(item["problem"] for item in data["problems"]))

    def test_dict_plan_adds_warning_to_problems(self):
        _seed_report_db(self.db)
        plan = self.root / "post_plan.json"
        plan.write_text(json.dumps({"posts": []}), encoding="utf-8")
        data = report_pdf.collect_weekly_data(db_path=self.db, now=NOW, plan_path=plan)
        self.assertTrue(data["plan_warning"])

    def test_missing_plan_has_no_warning(self):
        _seed_report_db(self.db)
        data = report_pdf.collect_weekly_data(db_path=self.db, now=NOW, plan_path=self.root / "absent.json")
        self.assertIsNone(data["plan_warning"])

    def test_valid_plan_has_no_warning(self):
        _seed_report_db(self.db)
        plan = self.root / "post_plan.json"
        plan.write_text(json.dumps([{"date": "2026-11-10", "status": "done"}]), encoding="utf-8")
        data = report_pdf.collect_weekly_data(db_path=self.db, now=NOW, plan_path=plan)
        self.assertIsNone(data["plan_warning"])
        self.assertEqual(data["posts_planned"], 1)
        self.assertEqual(data["posts_done"], 1)


class DbMaintenanceCopyGuardTests(unittest.TestCase):
    def setUp(self):
        import db_maintenance
        self.mod = db_maintenance
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state.db"
        sqlite3.connect(str(self.db)).close()

    def test_default_temp_dir_inside_repo_is_rejected(self):
        inside = self.mod.BASE_DIR / ".tmp-oca-copy-guard"
        self.assertFalse(inside.exists())
        with mock.patch.object(self.mod.tempfile, "gettempdir", return_value=str(inside)):
            with self.assertRaises(ValueError):
                self.mod.safe_check_copy(self.db)
        self.assertFalse(inside.exists())

    def test_explicit_temp_dir_inside_repo_is_rejected(self):
        with self.assertRaises(ValueError):
            self.mod.safe_check_copy(self.db, temp_dir=self.mod.BASE_DIR)

    def test_default_temp_dir_outside_repo_still_works(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        with mock.patch.object(self.mod.tempfile, "gettempdir", return_value=str(outside)):
            result = self.mod.safe_check_copy(self.db)
        self.assertTrue(result["ok"])
        self.assertTrue(result["deleted"])
        self.assertEqual(list(outside.iterdir()), [])


class SupportKitTempCleanupTests(unittest.TestCase):
    def setUp(self):
        import support_kit
        self.mod = support_kit
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "7-test-pack"
        self.root.mkdir()
        (self.root / "guide.pdf").write_bytes(b"%PDF-1.4\n")

    def test_write_if_changed_cleans_temp_when_replace_fails(self):
        target = self.root / "note.txt"
        with mock.patch.object(self.mod.os, "replace", side_effect=OSError("cross-device")):
            with self.assertRaises(OSError):
                self.mod._write_if_changed(target, b"data")
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".support-*")))

    def test_build_failure_cleans_stale_support_temp_files(self):
        support = self.root / "support"
        support.mkdir()
        stale = support / ".support-stale"
        stale.write_bytes(b"old temp")
        with mock.patch.object(self.mod, "_thank_you_pdf", side_effect=RuntimeError("no pillow")):
            with self.assertRaises(RuntimeError):
                self.mod.build(self.root)
        self.assertFalse(stale.exists())
        self.assertFalse(list(support.glob(".support-*")))

    def test_successful_build_leaves_no_support_temp_files(self):
        self.mod.build(self.root)
        self.assertFalse(list((self.root / "support").glob(".support-*")))


class SeoZipSafetyTests(unittest.TestCase):
    def setUp(self):
        import seo_helper
        self.mod = seo_helper
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.zip_path = Path(self.tmp.name) / "product.zip"

    def _make_zip(self):
        with zipfile.ZipFile(self.zip_path, "w") as archive:
            archive.writestr("safe.txt", "safe text")
            archive.writestr("../evil.txt", "evil")
            archive.writestr("/absolute.txt", "evil")
            archive.writestr(r"foo\..\..\etc\passwd.txt", "evil")
            archive.writestr(r"C:\secret.txt", "evil")
            archive.writestr("C:/secret.txt", "evil")
            link = zipfile.ZipInfo("link.txt")
            link.external_attr = (0o120777 << 16)
            archive.writestr(link, "/etc/passwd")

    def test_zip_texts_skips_absolute_drive_and_traversal_names(self):
        self._make_zip()
        texts = self.mod._zip_texts(self.zip_path)
        self.assertEqual(set(texts), {"safe.txt"})

    def test_zip_path_traversal_with_backslashes_is_blocked(self):
        self._make_zip()
        self.assertNotIn(r"foo\..\..\etc\passwd.txt", self.mod._zip_texts(self.zip_path))

    def test_zip_symlink_member_is_blocked(self):
        self._make_zip()
        self.assertNotIn("link.txt", self.mod._zip_texts(self.zip_path))

    def test_safe_zip_member_accepts_normal_nested_name(self):
        info = zipfile.ZipInfo("docs/readme.md")
        self.assertTrue(self.mod._safe_zip_member(info))


class SeoTitleVariantSafetyTests(unittest.TestCase):
    def setUp(self):
        import seo_helper
        self.mod = seo_helper

    def test_impossible_filtering_still_returns_a_safe_title(self):
        with mock.patch.object(self.mod, "_fit", return_value="Etsy"):
            variants = self.mod._title_variants_for("Etsy", "etsy", 140)
        self.assertTrue(variants)
        self.assertFalse(self.mod._contains_banned(variants[0]))

    def test_title_variants_are_ten_for_normal_input(self):
        result = self.mod.build_title_variants("Printable Habit Tracker", "etsy")
        self.assertEqual(len(result["variants"]), 10)

    def test_title_variants_never_loop_forever_on_banned_input(self):
        with mock.patch.object(self.mod, "_fit", return_value="Etsy"):
            variants = self.mod._title_variants_for("Etsy", "etsy", 140)
        self.assertLessEqual(len(variants), 10)


class SeoPluginErrorTests(unittest.TestCase):
    def setUp(self):
        import tg_seo
        self.mod = tg_seo
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_document_build_error_returns_owner_message(self):
        with mock.patch.object(self.mod.seo_helper, "build_for_pack", return_value={"summary": "ok"}), \
             mock.patch.object(self.mod.seo_helper, "build_seo_document", side_effect=RuntimeError("bad doc")):
            result = self.mod.handle("/seokit 3", {"chat_id": "1", "send_document": lambda *a, **k: None})
        self.assertIn("SEO kit error", result["text"])

    def test_send_error_still_deletes_temp_folder(self):
        import seo_helper
        pack = Path(self.tmp.name) / "products" / "upload" / "3-demo"
        pack.mkdir(parents=True)
        (pack / "README.md").write_text("Title: Demo Planner\n", encoding="utf-8")
        seen = {}
        temp_root = Path(self.tmp.name) / "temp-root"
        temp_root.mkdir()

        def fake_mkdtemp(prefix="", dir=None):
            path = temp_root / "seo-temp"
            path.mkdir()
            return str(path)

        def send(path, caption=""):
            seen["path"] = Path(path)
            self.assertTrue(seen["path"].is_file())
            raise OSError("telegram down")

        with mock.patch.object(self.mod.tempfile, "mkdtemp", side_effect=fake_mkdtemp):
            with self.assertRaises(OSError):
                self.mod.handle("/seokit 3", {"chat_id": "1", "products_dir": str(Path(self.tmp.name) / "products"),
                                              "send_document": send})
        self.assertFalse(seen["path"].exists())
        self.assertFalse(seen["path"].parent.exists())


class SeoCandidateFileCapTests(unittest.TestCase):
    def test_support_folder_is_capped_at_twenty_text_files(self):
        import seo_helper
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "1-pack"
            support = root / "support"
            support.mkdir(parents=True)
            for index in range(30):
                (support / f"note-{index:02d}.md").write_text("x", encoding="utf-8")
            files = seo_helper._candidate_files(root)
        self.assertEqual(len(files), seo_helper.MAX_SUBDIR_FILES)
        self.assertEqual(len({path.name for path in files}), seo_helper.MAX_SUBDIR_FILES)

    def test_content_folder_is_capped_at_twenty_text_files(self):
        import seo_helper
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2-pack"
            content = root / "content"
            content.mkdir(parents=True)
            for index in range(25):
                (content / f"page-{index:02d}.txt").write_text("x", encoding="utf-8")
            files = seo_helper._candidate_files(root)
        self.assertEqual(len(files), seo_helper.MAX_SUBDIR_FILES)


class SupportKitSymlinkTests(unittest.TestCase):
    def setUp(self):
        import support_kit
        self.mod = support_kit
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "8-symlink-pack"
        self.root.mkdir()
        (self.root / "guide.pdf").write_bytes(b"%PDF-1.4\n")

    def test_symlinked_directory_is_not_scanned(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "outside-secret.txt").write_text("secret", encoding="utf-8")
        try:
            os.symlink(outside, self.root / "linked-folder", target_is_directory=True)
        except OSError:
            self.skipTest("symlinks are not available")
        names = [item["name"] for item in self.mod.pack_files(self.root)]
        self.assertNotIn("outside-secret.txt", names)

    def test_symlinked_file_is_not_listed(self):
        outside = Path(self.tmp.name) / "outside-image.png"
        outside.write_bytes(b"not an image")
        try:
            os.symlink(outside, self.root / "linked-image.png")
        except OSError:
            self.skipTest("symlinks are not available")
        names = [item["name"] for item in self.mod.pack_files(self.root)]
        self.assertNotIn("linked-image.png", names)

    def test_normal_nested_files_are_still_found(self):
        nested = self.root / "content" / "pages"
        nested.mkdir(parents=True)
        (nested / "page.pdf").write_bytes(b"%PDF-1.4\n")
        names = [item["name"] for item in self.mod.pack_files(self.root)]
        self.assertIn("page.pdf", names)

    def test_refund_template_does_not_put_placeholder_in_price_sentence(self):
        self.mod.build(self.root)
        text = (self.root / "support" / "SUPPORT-REPLIES.md").read_text(encoding="utf-8")
        flat = " ".join(text.split())
        self.assertNotIn("The listing shows [amount] as the price", flat)
        self.assertIn("See the store listing for the price", flat)
        self.assertIn("[amount]", text)


class DbMaintenanceProtectedSalesTests(unittest.TestCase):
    def setUp(self):
        import db_maintenance
        self.mod = db_maintenance
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "sales.db"
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE sales_ledger (id INTEGER PRIMARY KEY, ts TEXT, amount TEXT)")
        conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, ts TEXT, note TEXT)")
        conn.execute("INSERT INTO sales_ledger (ts, amount) VALUES ('2020-01-01T00:00:00', '[amount]')")
        conn.execute("INSERT INTO sales (ts, note) VALUES ('2020-01-01T00:00:00', 'old sale')")
        conn.commit()
        conn.close()

    def test_sales_ledger_is_protected_from_custom_prune(self):
        result = self.mod.prune(self.db, now=NOW, log_days=0, task_days=0,
                                log_rules=(("sales_ledger", "ts"),))
        self.assertEqual(result["skipped"].get("sales_ledger"), "kept forever")
        conn = sqlite3.connect(str(self.db))
        count = conn.execute("SELECT COUNT(*) FROM sales_ledger").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_sales_table_is_protected_from_custom_prune(self):
        result = self.mod.prune(self.db, now=NOW, log_days=0, task_days=0,
                                log_rules=(("sales", "ts"),))
        self.assertEqual(result["skipped"].get("sales"), "kept forever")
        conn = sqlite3.connect(str(self.db))
        count = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_protected_tables_include_sales_names(self):
        self.assertIn("sales", self.mod.PROTECTED_TABLES)
        self.assertIn("sales_ledger", self.mod.PROTECTED_TABLES)
        self.assertIn("approvals", self.mod.PROTECTED_TABLES)


class SeoPackNumberTests(unittest.TestCase):
    def setUp(self):
        import tg_seo
        self.mod = tg_seo

    def test_missing_pack_number_is_none(self):
        self.assertIsNone(self.mod.parse_pack_number("/seokit"))
        self.assertIsNone(self.mod.parse_pack_number("seo kit"))

    def test_zero_pack_number_still_asks_for_a_pack(self):
        self.assertEqual(self.mod.parse_pack_number("/seokit 0"), 0)
        result = self.mod.handle("/seokit 0", {"chat_id": "1"})
        self.assertIn("Pack number", result["text"])

    def test_missing_pack_number_still_asks_for_a_pack(self):
        result = self.mod.handle("/seokit", {"chat_id": "1"})
        self.assertIn("Pack number", result["text"])

    def test_other_text_is_still_ignored(self):
        self.assertIsNone(self.mod.handle("hello", {"chat_id": "1"}))


class DbcareFreeFormTests(unittest.TestCase):
    def setUp(self):
        import tg_dbcare
        self.mod = tg_dbcare

    def test_clean_phrase_with_extra_words_is_clean(self):
        self.assertEqual(self.mod.parse_action("clean now please"), "clean")
        self.assertEqual(self.mod.parse_action("clean chey bro"), "clean")

    def test_copy_phrase_with_extra_words_is_copy(self):
        self.assertEqual(self.mod.parse_action("check copy now"), "copy")
        self.assertEqual(self.mod.parse_action("safe copy check please"), "copy")

    def test_report_phrase_with_extra_words_is_report(self):
        self.assertEqual(self.mod.parse_action("db health please"), "report")
        self.assertEqual(self.mod.parse_action("/dbcare status now"), "report")

    def test_unknown_free_form_is_ignored(self):
        self.assertIsNone(self.mod.parse_action("clean the kitchen please"))
        self.assertIsNone(self.mod.parse_action("copy that file"))

    def test_owner_check_fails_closed_on_notify_error(self):
        with mock.patch("notify._telegram_configured", side_effect=RuntimeError("broken")):
            self.assertFalse(self.mod._is_owner_chat({"chat_id": "1"}))

    def test_owner_check_rejects_other_chat(self):
        with mock.patch("notify._telegram_configured", return_value=(True, "token", "999")):
            self.assertFalse(self.mod._is_owner_chat({"chat_id": "1"}))


if __name__ == "__main__":
    unittest.main()


class ReportPdfPluginTempTests(unittest.TestCase):
    def setUp(self):
        import tg_reportpdf
        self.plugin = tg_reportpdf

    def test_report_pdf_temp_file_is_deleted_after_send(self):
        sent = {}

        def build(_db_path=None, _now=None, out_dir=None, **_kwargs):
            path = Path(out_dir) / "weekly-report-2026-W46.pdf"
            path.write_bytes(b"%PDF-1.4 test")
            return {"path": str(path), "data": {"week_key": "2026-W46"}}

        def send(path, caption=""):
            sent["path"] = Path(path)
            sent["caption"] = caption

        from unittest import mock
        with mock.patch.object(self.plugin.report_pdf, "build_weekly_report", side_effect=build):
            result = self.plugin.handle("/reportpdf", {"send_document": send, "db_path": "x"})
        self.assertEqual(result, {"handled": True})
        self.assertFalse(sent["path"].exists())
        self.assertFalse(sent["path"].parent.exists())

    def test_report_pdf_temp_file_is_deleted_when_send_fails(self):
        seen = {}

        def build(_db_path=None, _now=None, out_dir=None, **_kwargs):
            path = Path(out_dir) / "weekly-report-2026-W46.pdf"
            path.write_bytes(b"%PDF-1.4 test")
            return {"path": str(path), "data": {"week_key": "2026-W46"}}

        def send(path, caption=""):
            seen["path"] = Path(path)
            raise OSError("telegram down")

        from unittest import mock
        with mock.patch.object(self.plugin.report_pdf, "build_weekly_report", side_effect=build):
            with self.assertRaises(OSError):
                self.plugin.handle("/reportpdf", {"send_document": send, "db_path": "x"})
        self.assertFalse(seen["path"].exists())
        self.assertFalse(seen["path"].parent.exists())

    def test_report_pdf_build_failure_does_not_leave_temp(self):
        from unittest import mock
        with mock.patch.object(self.plugin.report_pdf, "build_weekly_report", side_effect=RuntimeError("boom")):
            result = self.plugin.handle("/reportpdf", {"send_document": lambda *a, **k: None, "db_path": "x"})
        self.assertIn("fail", result["text"].lower())
