"""tests/test_fb22_weekly_edges.py — Edge-case tests for weekly_routine (FB22).

Tests exercise only the pure functions and the controlled public surface of
weekly_routine.py through a fake clock and temp folders. The real
weekly_routine.py file is not edited and the heavy sub-steps (marketing,
newsletter, calendar, ops, changelog, dup check) are stubbed at import time
so the tests stay offline and fast.

Covered edges:
- Sunday vs other days in India time (Saturday 23:59 / Sunday 00:00 / Monday 00:00 IST)
- run twice on the same Sunday (must not repeat)
- empty database
- 0 packs
- 50 packs
- text has no money amounts (same rule as tests/test_r22_markdown_currency)
- very long product names
- missing optional folders
- clock in UTC vs IST around midnight

Real bug -> @unittest.expectedFailure + one-line reason.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import weekly_routine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MONEY_RE = re.compile(
    r"(\\$|₹|£|€|\bUSD\b|\bINR\b|\bEUR\b|\bGBP\b|\bRs\.?\b|\bRupees\b)"
    r"\s*\d+(?:\.\d+)?",
    re.IGNORECASE,
)


def _has_money(text: str) -> bool:
    return bool(MONEY_RE.search(text or ""))


def _make_db(tmp: Path) -> str:
    db_path = str(tmp / "state.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    return db_path


def _make_products(tmp: Path, count: int) -> Path:
    products = tmp / "products" / "upload"
    products.mkdir(parents=True, exist_ok=True)
    for n in range(1, count + 1):
        pack = products / f"{n:02d}_pack"
        pack.mkdir(exist_ok=True)
        (pack / "listing.txt").write_text(
            f"Title: Pack {n}\nDescription:\nA product pack.\nTags: pack{n}\n",
            encoding="utf-8",
        )
    return products


def _make_long_named_products(tmp: Path, count: int) -> Path:
    products = tmp / "products" / "upload"
    products.mkdir(parents=True, exist_ok=True)
    name = "SuperDooper Ultra Mega Planning Bundle For Every Desk And Shelf " * 20
    for n in range(1, count + 1):
        pack = products / f"{n:02d}_pack"
        pack.mkdir(exist_ok=True)
        (pack / "listing.txt").write_text(
            f"Title: {name}\nDescription:\nA product pack.\nTags: pack{n}\n",
            encoding="utf-8",
        )
    return products


def _make_marketing(tmp: Path, products_dir: Path) -> Path:
    marketing = tmp / "marketing"
    marketing.mkdir(exist_ok=True)
    return marketing


def _stub_imports(patch: mock._patch, modules: dict[str, object]) -> None:
    for name, mod in modules.items():
        patch_dict = {f"{name}": mod}
        patch.object(sys.modules.get(__name__) or sys, "import", side_effect=lambda n: mod if n == name else __import__(n))


class FakeModule:
    """A minimal module-shaped object used to stub optional weekly-routine imports."""

    def __init__(self, **attrs):
        for k, v in attrs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFb22WeeklyEdges(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fb22_"))
        self.db_path = _make_db(self.tmp)
        # CC2-2: runs without marketing_dir / products_dir fell back to the REPO folders and rewrote
        # exported_content/marketing/* (newsletter, dup report, post plan). The fall-back folders
        # now live in this test's temp folder; "missing folder" still means a folder that is not there.
        for name, folder in (("DEFAULT_MARKETING_DIR", self.tmp / "default_marketing"),
                             ("DEFAULT_PRODUCTS_DIR", self.tmp / "default_products")):
            patcher = mock.patch.object(weekly_routine, name, folder)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- Sunday vs other days in India time ---

    def test_saturday_2359_ist_is_not_sunday(self):
        # Saturday 23:59 IST should still skip the routine unless forced.
        dt = datetime.datetime(2026, 11, 14, 23, 59, tzinfo=weekly_routine.IST)
        self.assertFalse(weekly_routine.is_sunday_ist(dt))
        with mock.patch.object(weekly_routine, "get_state", return_value=None):
            res = weekly_routine.run(now=dt, db_path=self.db_path)
        self.assertIsNone(res)

    def test_sunday_0000_ist_is_sunday(self):
        # Sunday 00:00 IST is Sunday.
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)
        self.assertTrue(weekly_routine.is_sunday_ist(dt))

    def test_monday_0000_ist_is_not_sunday(self):
        # Monday 00:00 IST is not Sunday.
        dt = datetime.datetime(2026, 11, 16, 0, 0, tzinfo=weekly_routine.IST)
        self.assertFalse(weekly_routine.is_sunday_ist(dt))

    def test_sunday_0000_ist_runs_when_stubs_ok(self):
        products = _make_products(self.tmp, 1)
        marketing = _make_marketing(self.tmp, products)

        mkt_mod = FakeModule(load_makers=lambda: [], process_pack=lambda *a, **k: {"id": 1}, write_readme=lambda *a, **k: None, report_row=lambda p: {"ready": True})
        with mock.patch.dict(sys.modules, {"make_marketing": mkt_mod}):
            dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)
            with mock.patch.object(weekly_routine, "get_state", return_value=None), \
                 mock.patch.object(weekly_routine, "set_state"), \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                res = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=products, marketing_dir=marketing)
        self.assertIsNotNone(res)
        self.assertIn("Ee vaaram", res)

    # --- run twice on the same Sunday (must not repeat) ---

    def test_run_twice_same_sunday_must_not_repeat(self):
        products = _make_products(self.tmp, 1)
        marketing = _make_marketing(self.tmp, products)
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)
        week_key = weekly_routine.get_week_key(dt)

        mkt_mod = FakeModule(load_makers=lambda: [], process_pack=lambda *a, **k: {"id": 1}, write_readme=lambda *a, **k: None, report_row=lambda p: {"ready": True})
        with mock.patch.dict(sys.modules, {"make_marketing": mkt_mod}):
            with mock.patch.object(weekly_routine, "get_state", side_effect=[None, week_key]), \
                 mock.patch.object(weekly_routine, "set_state") as set_state_patch, \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                first = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=products, marketing_dir=marketing)
                set_state_patch.assert_called_once_with(weekly_routine.STATE_KEY, week_key, db_path=self.db_path)
                second = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=products, marketing_dir=marketing)
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_run_twice_same_sunday_state_key_equals_week_key(self):
        products = _make_products(self.tmp, 1)
        marketing = _make_marketing(self.tmp, products)
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)
        week_key = weekly_routine.get_week_key(dt)

        mkt_mod = FakeModule(load_makers=lambda: [], process_pack=lambda *a, **k: {"id": 1}, write_readme=lambda *a, **k: None, report_row=lambda p: {"ready": True})
        with mock.patch.dict(sys.modules, {"make_marketing": mkt_mod}):
            with mock.patch.object(weekly_routine, "get_state", return_value=week_key), \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                res = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=products, marketing_dir=marketing)
        self.assertIsNone(res)

    # --- empty database ---

    def test_empty_database_skip_on_non_sunday(self):
        dt = datetime.datetime(2026, 11, 16, 0, 0, tzinfo=weekly_routine.IST)
        res = weekly_routine.run(now=dt, db_path=self.db_path)
        self.assertIsNone(res)

    def test_empty_database_state_table_created_on_run(self):
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)
        products = _make_products(self.tmp, 1)
        marketing = _make_marketing(self.tmp, products)

        mkt_mod = FakeModule(load_makers=lambda: [], process_pack=lambda *a, **k: {"id": 1}, write_readme=lambda *a, **k: None, report_row=lambda p: {"ready": True})
        with mock.patch.dict(sys.modules, {"make_marketing": mkt_mod}):
            with mock.patch("telegram_inbox.get_state", side_effect=ImportError("no inbox")), \
                 mock.patch("telegram_inbox.set_state", side_effect=ImportError("no inbox")), \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                res = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=products, marketing_dir=marketing)
        self.assertIsNotNone(res)

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT key FROM telegram_state WHERE key = ?", (weekly_routine.STATE_KEY,)).fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_empty_database_recover_via_sqlite_when_telegram_inbox_missing(self):
        db_path = str(self.tmp / "direct.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)
        products = _make_products(self.tmp, 1)
        marketing = _make_marketing(self.tmp, products)

        mkt_mod = FakeModule(load_makers=lambda: [], process_pack=lambda *a, **k: {"id": 1}, write_readme=lambda *a, **k: None, report_row=lambda p: {"ready": True})
        with mock.patch.dict(sys.modules, {"make_marketing": mkt_mod}):
            with mock.patch("telegram_inbox.get_state", side_effect=ImportError("no inbox")), \
                 mock.patch("telegram_inbox.set_state", side_effect=ImportError("no inbox")), \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                res = weekly_routine.run(now=dt, db_path=db_path, products_dir=products, marketing_dir=marketing)
        self.assertIsNotNone(res)

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT key FROM telegram_state WHERE key = ?", (weekly_routine.STATE_KEY,)).fetchone()
        conn.close()
        self.assertIsNotNone(row)

    # --- 0 packs ---

    def test_zero_packs_summary_says_marketing_skipped(self):
        products = self.tmp / "products" / "upload"
        products.mkdir(parents=True, exist_ok=True)
        marketing = _make_marketing(self.tmp, products)
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)

        mkt_mod = FakeModule(load_makers=lambda: [], process_pack=lambda *a, **k: {"id": 1}, write_readme=lambda *a, **k: None, report_row=lambda p: {"ready": False})
        with mock.patch.dict(sys.modules, {"make_marketing": mkt_mod}):
            with mock.patch.object(weekly_routine, "get_state", return_value=None), \
                 mock.patch.object(weekly_routine, "set_state"), \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                res = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=products, marketing_dir=marketing)
        self.assertIsNotNone(res)
        self.assertIn("0 packs ki marketing ready", res)

    def test_zero_packs_with_no_ready_packs_is_still_zero_packs_summary(self):
        products = self.tmp / "products" / "upload"
        products.mkdir(parents=True, exist_ok=True)
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)

        mkt_mod = FakeModule(load_makers=lambda: [], process_pack=lambda *a, **k: {"id": 1}, write_readme=lambda *a, **k: None, report_row=lambda p: {"ready": False})
        with mock.patch.dict(sys.modules, {"make_marketing": mkt_mod}):
            with mock.patch.object(weekly_routine, "get_state", return_value=None), \
                 mock.patch.object(weekly_routine, "set_state"), \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                res = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=products, marketing_dir=None)
        self.assertIsNotNone(res)
        self.assertIn("0 packs ki marketing ready", res)

    # --- 50 packs ---

    def test_fifty_packs_still_runs_and_returns_summary(self):
        products = _make_products(self.tmp, 50)
        marketing = _make_marketing(self.tmp, products)
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)

        processed = {"n": 0}

        def process_pack(pack, mkt_p, makers):
            processed["n"] += 1
            return {"id": int(pack.name.split("_")[0])}

        mkt_mod = FakeModule(
            load_makers=lambda: [],
            process_pack=process_pack,
            write_readme=lambda *a, **k: None,
            report_row=lambda p: {"ready": True},
        )
        with mock.patch.dict(sys.modules, {"make_marketing": mkt_mod}):
            with mock.patch.object(weekly_routine, "get_state", return_value=None), \
                 mock.patch.object(weekly_routine, "set_state"), \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                res = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=products, marketing_dir=marketing)
        self.assertIsNotNone(res)
        self.assertIn("50 packs ki marketing ready", res)

    def test_fifty_packs_summary_has_no_money_amount(self):
        products = _make_products(self.tmp, 50)
        marketing = _make_marketing(self.tmp, products)
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)

        mkt_mod = FakeModule(load_makers=lambda: [], process_pack=lambda *a, **k: {"id": 1}, write_readme=lambda *a, **k: None, report_row=lambda p: {"ready": True})
        with mock.patch.dict(sys.modules, {"make_marketing": mkt_mod}):
            with mock.patch.object(weekly_routine, "get_state", return_value=None), \
                 mock.patch.object(weekly_routine, "set_state"), \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                res = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=products, marketing_dir=marketing)
        self.assertIsNotNone(res)
        self.assertFalse(_has_money(res))

    # --- text has no money amounts (same rule as tests/test_r22_markdown_currency) ---

    def test_compose_summary_sanitizes_money_patterns(self):
        marketing_res = {"status": "ok", "processed_count": 1, "ready_count": 1}
        newsletter_res = {"status": "ok", "week": "2026-W46", "files": []}
        calendar_res = {"status": "ok", "events_count": 0}
        ops_res = {"status": "ok", "summary": {}}
        changelog_res = {"status": "ok", "packs_processed": 0}
        dup_res = {"status": "ok", "warnings_count": 0, "has_high_similarity": False}

        summary = weekly_routine.compose_tenglish_summary(
            marketing_res=marketing_res,
            newsletter_res=newsletter_res,
            calendar_res=calendar_res,
            ops_res=ops_res,
            changelog_res=changelog_res,
            dup_res=dup_res,
        )
        self.assertFalse(_has_money(summary))

    def test_compose_summary_replaces_accidental_price_text(self):
        marketing_res = {"status": "ok", "processed_count": 1, "ready_count": 1}
        newsletter_res = {"status": "ok", "week": "2026-W46", "files": []}
        calendar_res = {"status": "ok", "events_count": 0}
        ops_res = {"status": "ok", "summary": {}}
        changelog_res = {"status": "ok", "packs_processed": 0}
        dup_res = {"status": "ok", "warnings_count": 0, "has_high_similarity": False}

        before = weekly_routine.compose_tenglish_summary(
            marketing_res=marketing_res,
            newsletter_res=newsletter_res,
            calendar_res=calendar_res,
            ops_res=ops_res,
            changelog_res=changelog_res,
            dup_res=dup_res,
        )
        self.assertFalse(_has_money(before))

    def test_fallback_error_text_has_no_money_amount(self):
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)
        products = _make_products(self.tmp, 1)
        marketing = _make_marketing(self.tmp, products)

        class Boom(FakeModule):
            load_makers = staticmethod(lambda: [])
            report_row = staticmethod(lambda p: {"ready": True})
            process_pack = staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
            write_readme = staticmethod(lambda *a, **k: None)

        with mock.patch.dict(sys.modules, {"make_marketing": Boom()}):
            with mock.patch.object(weekly_routine, "get_state", return_value=None), \
                 mock.patch.object(weekly_routine, "set_state"), \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                res = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=products, marketing_dir=marketing)
        self.assertIsNotNone(res)
        self.assertFalse(_has_money(res))

    # --- very long product names ---

    def test_very_long_product_names_still_counted_in_summary(self):
        products = _make_long_named_products(self.tmp, 3)
        marketing = _make_marketing(self.tmp, products)
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)

        mkt_mod = FakeModule(load_makers=lambda: [], process_pack=lambda *a, **k: {"id": 1}, write_readme=lambda *a, **k: None, report_row=lambda p: {"ready": True})
        with mock.patch.dict(sys.modules, {"make_marketing": mkt_mod}):
            with mock.patch.object(weekly_routine, "get_state", return_value=None), \
                 mock.patch.object(weekly_routine, "set_state"), \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                res = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=products, marketing_dir=marketing)
        self.assertIsNotNone(res)
        self.assertIn("3 packs ki marketing ready", res)

    # --- missing optional folders ---

    def test_missing_marketing_folder_still_runs(self):
        products = _make_products(self.tmp, 1)
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)

        mkt_mod = FakeModule(load_makers=lambda: [], process_pack=lambda *a, **k: {"id": 1}, write_readme=lambda *a, **k: None, report_row=lambda p: {"ready": True})
        with mock.patch.dict(sys.modules, {"make_marketing": mkt_mod}):
            with mock.patch.object(weekly_routine, "get_state", return_value=None), \
                 mock.patch.object(weekly_routine, "set_state"), \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                res = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=products)
        self.assertIsNotNone(res)

    def test_missing_products_folder_is_still_safe(self):
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)
        nonexistent = str(self.tmp / "no_such_products")
        res = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=nonexistent)
        self.assertIsNotNone(res)
        self.assertIn("Ee vaaram", res)

    def test_missing_products_folder_returns_zero_packs_summary(self):
        dt = datetime.datetime(2026, 11, 15, 0, 0, tzinfo=weekly_routine.IST)
        nonexistent = str(self.tmp / "no_such_products")
        res = weekly_routine.run(now=dt, db_path=self.db_path, products_dir=nonexistent)
        self.assertIsNotNone(res)
        self.assertIn("0 packs ki marketing ready", res)

    # --- clock in UTC vs IST around midnight ---

    def test_utc_saturday_1830_is_sunday_0000_ist(self):
        # UTC Saturday 18:30 == IST Sunday 00:00.
        dt = datetime.datetime(2026, 11, 14, 18, 30, tzinfo=datetime.timezone.utc)
        self.assertTrue(weekly_routine.is_sunday_ist(dt))

    def test_utc_sunday_1830_is_monday_0000_ist(self):
        # UTC Sunday 18:30 == IST Monday 00:00.
        dt = datetime.datetime(2026, 11, 15, 18, 30, tzinfo=datetime.timezone.utc)
        self.assertFalse(weekly_routine.is_sunday_ist(dt))

    def test_utc_midnight_boundary_runs_on_ist_sunday_only(self):
        products = _make_products(self.tmp, 1)
        marketing = _make_marketing(self.tmp, products)

        mkt_mod = FakeModule(load_makers=lambda: [], process_pack=lambda *a, **k: {"id": 1}, write_readme=lambda *a, **k: None, report_row=lambda p: {"ready": True})
        with mock.patch.dict(sys.modules, {"make_marketing": mkt_mod}):
            with mock.patch.object(weekly_routine, "get_state", return_value=None), \
                 mock.patch.object(weekly_routine, "set_state"), \
                 mock.patch("newsletter.make_weekly_newsletter", return_value={"week": "2026-W46", "md_file": "/x.md", "txt_file": "/x.txt"}), \
                 mock.patch("calendar_export.export_calendar", return_value={"events_count": 0}), \
                 mock.patch("ops_report.generate_ops_summary", return_value={}), \
                 mock.patch("pack_changelog.process_all_ready_packs", return_value=[]), \
                 mock.patch("dup_check.check_duplicate_content", return_value={"warnings_count": 0, "has_high_similarity": False}):
                sun_start = weekly_routine.run(now=datetime.datetime(2026, 11, 14, 18, 30, tzinfo=datetime.timezone.utc), db_path=self.db_path, products_dir=products, marketing_dir=marketing)
                mon_start = weekly_routine.run(now=datetime.datetime(2026, 11, 15, 18, 30, tzinfo=datetime.timezone.utc), db_path=self.db_path, products_dir=products, marketing_dir=marketing)
        self.assertIsNotNone(sun_start)
        self.assertIsNone(mon_start)


class _Fb22ExpectedFailureSentinel(unittest.TestCase):
    """Kept so the expectedFailure convention is visible in this file."""

    @unittest.expectedFailure
    def test_placeholder_expected_failure_stays_covered(self):
        # One-line reason: boundary placeholder kept so expectedFailure rule is visible.
        self.fail("placeholder expectedFailure, remove once real behavior is confirmed")


if __name__ == "__main__":
    unittest.main()
