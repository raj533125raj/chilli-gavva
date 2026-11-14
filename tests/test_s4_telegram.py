"""Unit tests for BINNY-S4 ROBOT: Telegram for the Owner (Jobs A, B, C).

Tests Job A:
- /packs default shows up to 10 packs
- /packs pagination: /packs 1, /packs 2
- /packs page hint when more pages exist
- /packs out of range page clamped
- /packs with no ready packs
- /pack N shows files with names and sizes
- /pack N shows Deep QA verdict and score
- /pack N shows marketing files readiness (carousel, pins, reel, blog)
- /pack N with carousel present shows carousel=yes
- /pack N with pins present shows pins=yes
- /pack N with reel present shows reel=yes
- /pack N with blog present shows blog=yes
- /pack N with listing_lint result if available
- /pack N plain text <= 3500 chars
- /pack N refuses money amounts in listing.txt
- /pack N for missing pack gives friendly message
- /pack without number prompts owner

Tests Job C:
- /week empty returns week summary
- /week counts drafts within current week (from Monday)
- /week includes upload packs ready count
- /week includes post_planner week_summary
- /week includes A/B title leaders
- /week handle_command integration
- /week process_updates integration
- "ee vaaram" plain-text trigger
- "eevaaram" single-word trigger
- "this week" trigger
- Output <= 3500 chars, no money amounts
- Duplicate week updates in burst deduplicated
"""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import telegram_inbox as ti
import guardrails

PNG_BYTES = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
             b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
             b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


# CC2-2: commands under test fall back to the relative path "exported_content/post_plan.json" and
# created that file in the repo. This module now runs from a temp folder, so nothing lands there.
_ORIGINAL_CWD = os.getcwd()
_SCRATCH_CWD = None


def setUpModule():
    global _SCRATCH_CWD
    _SCRATCH_CWD = tempfile.TemporaryDirectory()
    os.chdir(_SCRATCH_CWD.name)


def tearDownModule():
    os.chdir(_ORIGINAL_CWD)
    if _SCRATCH_CWD is not None:
        _SCRATCH_CWD.cleanup()


class S4TelegramJobATests(unittest.TestCase):
    def setUp(self):
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        self.env_patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.db = str(self.root / "test_s4.db")
        self.products_dir = self.root / "products"
        self.marketing_dir = self.root / "marketing"
        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

        guardrails.connect(self.db)

    def _create_pack(self, aid, slug="sample-pack", title="Sample Pack"):
        pdir = self.products_dir / "upload" / f"{aid}-{slug}"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "product.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        (pdir / "checklist.txt").write_text("ok checklist", encoding="utf-8")
        listing_content = f"Title: {title}\nDescription:\nGood product description.\n\nTags:\ntag1, tag2\n\nWhat you get:\n- 1 guide\n"
        (pdir / "listing.txt").write_text(listing_content, encoding="utf-8")
        (pdir / "cover.png").write_bytes(PNG_BYTES)
        return pdir

    def test_01_packs_empty(self):
        res = ti.ready_packs_text(products_dir=str(self.products_dir))
        self.assertEqual(res, "No ready upload packs found.")

    def test_02_packs_single_page(self):
        for i in range(1, 4):
            self._create_pack(i, f"pack-{i}", f"Pack {i} Title")
        res = ti.ready_packs_text(page=1, products_dir=str(self.products_dir))
        self.assertIn("Ready Upload Packs (1/1):", res)
        self.assertIn("#1 Pack 1 Title", res)
        self.assertIn("#2 Pack 2 Title", res)
        self.assertIn("#3 Pack 3 Title", res)
        self.assertNotIn("Send /packs 2", res)

    def test_03_packs_pagination_first_page_has_next_hint(self):
        for i in range(1, 15):
            self._create_pack(i, f"pack-{i}", f"Pack {i} Title")
        res = ti.ready_packs_text(page=1, products_dir=str(self.products_dir))
        self.assertIn("Ready Upload Packs (1/2):", res)
        self.assertIn("#1 Pack 1 Title", res)
        self.assertIn("#10 Pack 10 Title", res)
        self.assertNotIn("#11 Pack 11 Title", res)
        self.assertIn("Send /packs 2 for next page.", res)

    def test_04_packs_pagination_second_page(self):
        for i in range(1, 15):
            self._create_pack(i, f"pack-{i}", f"Pack {i} Title")
        res = ti.ready_packs_text(page=2, products_dir=str(self.products_dir))
        self.assertIn("Ready Upload Packs (2/2):", res)
        self.assertNotIn("#10 Pack 10 Title", res)
        self.assertIn("#11 Pack 11 Title", res)
        self.assertIn("#14 Pack 14 Title", res)
        self.assertNotIn("Send /packs 3", res)

    def test_05_packs_page_out_of_bounds_clamped(self):
        for i in range(1, 5):
            self._create_pack(i)
        res_high = ti.ready_packs_text(page=99, products_dir=str(self.products_dir))
        self.assertIn("Ready Upload Packs (1/1):", res_high)
        res_low = ti.ready_packs_text(page=-5, products_dir=str(self.products_dir))
        self.assertIn("Ready Upload Packs (1/1):", res_low)

    def test_06_packs_page_invalid_string(self):
        self._create_pack(1)
        res = ti.ready_packs_text(page="invalid", products_dir=str(self.products_dir))
        self.assertIn("Ready Upload Packs (1/1):", res)

    def test_07_packs_three_pages(self):
        for i in range(1, 25):
            self._create_pack(i, f"pack-{i}", f"Pack {i}")
        res_p2 = ti.ready_packs_text(page=2, products_dir=str(self.products_dir))
        self.assertIn("Ready Upload Packs (2/3):", res_p2)
        self.assertIn("Send /packs 3 for next page.", res_p2)

    def test_08_handle_command_packs_default(self):
        self._create_pack(1)
        with mock.patch("product_builder.PRODUCTS_DIR", str(self.products_dir)):
            res = ti.handle_command("packs", [], db_path=self.db)
            self.assertIn("Ready Upload Packs (1/1):", res)

    def test_09_handle_command_packs_page_param(self):
        for i in range(1, 15):
            self._create_pack(i)
        with mock.patch("product_builder.PRODUCTS_DIR", str(self.products_dir)):
            res = ti.handle_command("packs", [2], db_path=self.db)
            self.assertIn("Ready Upload Packs (2/2):", res)

    def test_10_pack_detail_not_found(self):
        res = ti.pack_detail_text(999, products_dir=str(self.products_dir))
        self.assertIn("No ready upload pack found for #999.", res)

    def test_11_pack_detail_without_id_prompt(self):
        res = ti.handle_command("pack", [], db_path=self.db)
        self.assertIn("Send /pack <number> to view pack details.", res)

    def test_12_pack_detail_shows_files_and_sizes(self):
        pdir = self._create_pack(42, "focus", "Focus Planner")
        res = ti.pack_detail_text(42, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
        self.assertIn("Pack #42: Focus Planner", res)
        self.assertIn("Files:", res)
        # file names are shown without "name.ext" paths (privacy rule from JX): "product (zip, 5.1 KB)"
        self.assertIn("checklist (txt", res)
        self.assertIn("listing (txt", res)
        self.assertIn("product (zip", res)
        self.assertIn("cover (png", res)
        self.assertNotIn(".zip", res)
        self.assertIn("B)", res)
        # the copy-paste listing text comes first
        self.assertIn("Title: Focus Planner", res)

    def test_13_pack_detail_deep_qa_verdict(self):
        self._create_pack(7, "qa-pack", "QA Pack")
        with mock.patch("product_qa_deep.check_folder", return_value={"verdict": "ready", "score": 95}):
            res = ti.pack_detail_text(7, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
            self.assertIn("Deep QA: ready (score: 95)", res)

    def test_14_pack_detail_deep_qa_exception_handled(self):
        self._create_pack(8, "qa-err", "QA Err Pack")
        with mock.patch("product_qa_deep.check_folder", side_effect=RuntimeError("QA broke")):
            res = ti.pack_detail_text(8, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
            self.assertIn("Deep QA: check skipped", res)

    def test_15_pack_detail_marketing_all_no_by_default(self):
        self._create_pack(9, "no-mkt", "No Mkt")
        res = ti.pack_detail_text(9, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
        self.assertIn("Marketing: carousel=no, pins=no, reel=no, blog=no", res)

    def test_16_pack_detail_marketing_carousel_yes(self):
        pdir = self._create_pack(10, "mkt-car", "Mkt Carousel")
        car_dir = self.marketing_dir / pdir.name / "carousel"
        car_dir.mkdir(parents=True)
        (car_dir / "slide-1.png").write_bytes(PNG_BYTES)
        res = ti.pack_detail_text(10, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
        self.assertIn("carousel=yes", res)
        self.assertIn("pins=no", res)

    def test_17_pack_detail_marketing_pins_yes(self):
        pdir = self._create_pack(11, "mkt-pins", "Mkt Pins")
        pins_dir = self.marketing_dir / pdir.name / "pins"
        pins_dir.mkdir(parents=True)
        (pins_dir / "pins.json").write_text("{}", encoding="utf-8")
        res = ti.pack_detail_text(11, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
        self.assertIn("pins=yes", res)

    def test_18_pack_detail_marketing_reel_yes(self):
        pdir = self._create_pack(12, "mkt-reel", "Mkt Reel")
        reel_dir = self.marketing_dir / pdir.name / "reel"
        reel_dir.mkdir(parents=True)
        (reel_dir / "reel.txt").write_text("caption", encoding="utf-8")
        res = ti.pack_detail_text(12, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
        self.assertIn("reel=yes", res)

    def test_19_pack_detail_marketing_blog_yes(self):
        pdir = self._create_pack(13, "mkt-blog", "Mkt Blog")
        mkt_pdir = self.marketing_dir / pdir.name
        mkt_pdir.mkdir(parents=True)
        (mkt_pdir / "blog.md").write_text("# Blog Post", encoding="utf-8")
        res = ti.pack_detail_text(13, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
        self.assertIn("blog=yes", res)

    def test_20_pack_detail_marketing_all_yes(self):
        pdir = self._create_pack(14, "mkt-all", "Mkt All")
        mkt_pdir = self.marketing_dir / pdir.name
        (mkt_pdir / "carousel").mkdir(parents=True)
        (mkt_pdir / "carousel" / "slide-1.png").write_bytes(PNG_BYTES)
        (mkt_pdir / "pins").mkdir(parents=True)
        (mkt_pdir / "pins" / "pins.json").write_text("{}", encoding="utf-8")
        (mkt_pdir / "reel").mkdir(parents=True)
        (mkt_pdir / "reel" / "reel.txt").write_text("caption", encoding="utf-8")
        (mkt_pdir / "blog.md").write_text("# Article", encoding="utf-8")
        res = ti.pack_detail_text(14, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
        self.assertIn("Marketing: carousel=yes, pins=yes, reel=yes, blog=yes", res)

    def test_21_pack_detail_listing_lint_guarded_when_absent(self):
        self._create_pack(15, "lint-pack", "Lint Pack")
        with mock.patch.dict("sys.modules", {"listing_lint": None}):
            res = ti.pack_detail_text(15, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
        self.assertNotIn("Listing lint:", res)

    def test_22_pack_detail_listing_lint_executed_when_present(self):
        self._create_pack(16, "lint-pres", "Lint Pres")
        fake_lint = mock.MagicMock()
        fake_lint.lint_listing.return_value = "pass (score 100)"
        with mock.patch.dict("sys.modules", {"listing_lint": fake_lint}):
            res = ti.pack_detail_text(16, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
            self.assertIn("Listing lint: pass (score 100)", res)

    def test_23_pack_detail_refuses_money(self):
        pdir = self._create_pack(17, "money-pack", "Money Pack")
        (pdir / "listing.txt").write_text("Title: Money Pack\nPrice: $29 USD\n", encoding="utf-8")
        res = ti.pack_detail_text(17, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
        self.assertIn("owner must set price", res)
        self.assertNotIn("$29", res)

    def test_24_pack_detail_max_length_bounded(self):
        pdir = self._create_pack(18, "big-pack", "Big Pack")
        for i in range(100):
            (pdir / f"extra_file_{i:03d}.txt").write_text("data", encoding="utf-8")
        res = ti.pack_detail_text(18, products_dir=str(self.products_dir), marketing_dir=str(self.marketing_dir))
        self.assertLessEqual(len(res), 3500)

    def test_25_format_size_helper(self):
        self.assertEqual(ti._format_size(100), "100 B")
        self.assertEqual(ti._format_size(2048), "2.0 KB")
        self.assertEqual(ti._format_size(5 * 1024 * 1024), "5.0 MB")


class S4TelegramJobCTests(unittest.TestCase):
    def setUp(self):
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        self.env_patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.db = str(self.root / "test_week.db")
        self.plan_path = str(self.root / "post_plan.json")
        self.products_dir = self.root / "products"
        self.products_dir.mkdir(parents=True, exist_ok=True)

        guardrails.connect(self.db)

    def _create_pack(self, aid, slug="sample-pack", title="Sample Pack"):
        pdir = self.products_dir / "upload" / f"{aid}-{slug}"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "product.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        (pdir / "checklist.txt").write_text("ok checklist", encoding="utf-8")
        listing_content = f"Title: {title}\nDescription:\nGood product description.\n\nTags:\ntag1, tag2\n\nWhat you get:\n- 1 guide\n"
        (pdir / "listing.txt").write_text(listing_content, encoding="utf-8")
        (pdir / "cover.png").write_bytes(PNG_BYTES)
        return pdir

    def test_26_week_summary_format_empty(self):
        res = ti.week_summary_text(db_path=self.db, plan_path=self.plan_path, products_dir=str(self.products_dir), date="2026-11-04")
        self.assertIn("Ee Vaaram Summary (This Week)", res)
        self.assertIn("Drafts*: 0 made, 0 approved, 0 rejected", res)
        self.assertIn("Upload packs ready*: 0 packs", res)
        self.assertIn("This week: 0 of 0 posts done", res)
        self.assertIn("A/B Title Leaders: Inka clicks record avaledu.", res)

    def test_27_week_summary_counts_drafts_within_week(self):
        conn = guardrails.connect(self.db)
        conn.execute("INSERT INTO approvals (ts, status, decided_ts) VALUES ('2026-11-03T10:00:00', 'approved', '2026-11-03T11:00:00')")
        conn.execute("INSERT INTO approvals (ts, status, decided_ts) VALUES ('2026-11-04T10:00:00', 'rejected', '2026-11-04T11:00:00')")
        conn.execute("INSERT INTO approvals (ts, status, decided_ts) VALUES ('2026-11-05T10:00:00', 'pending', NULL)")
        conn.execute("INSERT INTO approvals (ts, status, decided_ts) VALUES ('2026-10-25T10:00:00', 'approved', '2026-10-25T11:00:00')")
        conn.commit()
        conn.close()

        res = ti.week_summary_text(db_path=self.db, plan_path=self.plan_path, products_dir=str(self.products_dir), date="2026-11-04")
        self.assertIn("Drafts*: 3 made, 1 approved, 1 rejected", res)

    def test_28_week_summary_includes_packs_ready(self):
        self._create_pack(1, "pack-a", "Pack A")
        self._create_pack(2, "pack-b", "Pack B")
        res = ti.week_summary_text(db_path=self.db, plan_path=self.plan_path, products_dir=str(self.products_dir), date="2026-11-04")
        self.assertIn("Upload packs ready*: 2 packs", res)

    def test_29_week_summary_includes_post_planner_status(self):
        import json
        plan = [
            {"day": 1, "date": "2026-11-02", "status": "done"},
            {"day": 2, "date": "2026-11-03", "status": "done"},
            {"day": 3, "date": "2026-11-04", "status": "planned"},
        ]
        with open(self.plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f)

        res = ti.week_summary_text(db_path=self.db, plan_path=self.plan_path, products_dir=str(self.products_dir), date="2026-11-04")
        self.assertIn("This week: 2 of 3 posts done", res)

    def test_30_week_summary_includes_ab_leaders(self):
        import ab_results
        ab_results.record_clicks("5", 2, 88, db_path=self.db)
        res = ti.week_summary_text(db_path=self.db, plan_path=self.plan_path, products_dir=str(self.products_dir), date="2026-11-04")
        self.assertIn("A/B Title Leaders:", res)
        self.assertIn("Pack #5: #2", res)
        self.assertIn("88 clicks 👑", res)

    def test_31_handle_command_week(self):
        res = ti.handle_command("week", [], db_path=self.db)
        self.assertIn("Ee Vaaram Summary (This Week)", res)

    def test_32_process_updates_slash_week(self):
        token = "123:TOKEN"
        owner = "111222333"
        updates = [{"update_id": 1, "message": {"chat": {"id": int(owner)}, "text": "/week"}}]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(len(sent), 1)
        self.assertIn("Ee Vaaram Summary (This Week)", sent[0])

    def test_33_process_updates_tenglish_ee_vaaram(self):
        token = "123:TOKEN"
        owner = "111222333"
        updates = [{"update_id": 2, "message": {"chat": {"id": int(owner)}, "text": "ee vaaram"}}]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(len(sent), 1)
        self.assertIn("Ee Vaaram Summary (This Week)", sent[0])

    def test_34_process_updates_tenglish_eevaaram_single_word(self):
        token = "123:TOKEN"
        owner = "111222333"
        updates = [{"update_id": 3, "message": {"chat": {"id": int(owner)}, "text": "eevaaram"}}]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(len(sent), 1)
        self.assertIn("Ee Vaaram Summary (This Week)", sent[0])

    def test_35_process_updates_this_week_phrase(self):
        token = "123:TOKEN"
        owner = "111222333"
        updates = [{"update_id": 4, "message": {"chat": {"id": int(owner)}, "text": "this week"}}]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(len(sent), 1)
        self.assertIn("Ee Vaaram Summary (This Week)", sent[0])

    def test_36_week_summary_output_length_and_no_money(self):
        res = ti.week_summary_text(db_path=self.db)
        self.assertLessEqual(len(res), 3500)
        self.assertNotIn("$", res)
        self.assertNotIn("USD", res)

    def test_37_process_updates_tenglish_ab_set_integration(self):
        token = "123:TOKEN"
        owner = "111222333"
        updates = [{"update_id": 5, "message": {"chat": {"id": int(owner)}, "text": "ab 2 lo title 3 ki 95 clicks"}}]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(len(sent), 1)
        self.assertIn("Sare! Pack #2 Title #3 ki 95 clicks save chesa.", sent[0])

    def test_38_process_updates_tenglish_ab_view_integration(self):
        token = "123:TOKEN"
        owner = "111222333"
        import ab_results
        ab_results.record_clicks("2", 3, 95, db_path=self.db)
        updates = [{"update_id": 6, "message": {"chat": {"id": int(owner)}, "text": "ab 2"}}]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(len(sent), 1)
        self.assertIn("A/B Title Results for Pack #2:", sent[0])

    def test_39_process_updates_slash_ab_set_integration(self):
        token = "123:TOKEN"
        owner = "111222333"
        updates = [{"update_id": 7, "message": {"chat": {"id": int(owner)}, "text": "/ab 9 1 50"}}]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(len(sent), 1)
        self.assertIn("Sare! Pack #9 Title #1 ki 50 clicks save chesa.", sent[0])

    def test_40_process_updates_slash_ab_view_integration(self):
        token = "123:TOKEN"
        owner = "111222333"
        updates = [{"update_id": 8, "message": {"chat": {"id": int(owner)}, "text": "/ab 9"}}]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(len(sent), 1)
        self.assertIn("A/B Title Results for Pack #9:", sent[0])

    def test_41_week_summary_date_object_handling(self):
        import datetime
        dt = datetime.date(2026, 11, 4)
        res = ti.week_summary_text(db_path=self.db, plan_path=self.plan_path, products_dir=str(self.products_dir), date=dt)
        self.assertIn("Ee Vaaram Summary (This Week)", res)

    def test_42_week_summary_datetime_object_handling(self):
        import datetime
        dt = datetime.datetime(2026, 11, 4, 15, 30, 0)
        res = ti.week_summary_text(db_path=self.db, plan_path=self.plan_path, products_dir=str(self.products_dir), date=dt)
        self.assertIn("Ee Vaaram Summary (This Week)", res)

    def test_43_process_updates_non_owner_ignored_for_week(self):
        token = "123:TOKEN"
        owner = "111222333"
        attacker = "999888777"
        updates = [{"update_id": 9, "message": {"chat": {"id": int(attacker)}, "text": "/week"}}]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            handled = ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(handled, 0)
        self.assertEqual(sent, [])

    def test_44_process_updates_non_owner_ignored_for_ab(self):
        token = "123:TOKEN"
        owner = "111222333"
        attacker = "999888777"
        updates = [{"update_id": 10, "message": {"chat": {"id": int(attacker)}, "text": "ab 1 title 2 100"}}]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            handled = ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(handled, 0)
        self.assertEqual(sent, [])

    def test_45_week_summary_no_plan_file_handles_gracefully(self):
        res = ti.week_summary_text(db_path=self.db, plan_path=str(self.root / "nonexistent.json"), products_dir=str(self.products_dir))
        self.assertIn("Ee Vaaram Summary (This Week)", res)

    def test_46_week_summary_multiple_ab_leaders_formatting(self):
        import ab_results
        ab_results.record_clicks("1", 1, 10, db_path=self.db)
        ab_results.record_clicks("2", 3, 20, db_path=self.db)
        ab_results.record_clicks("3", 5, 30, db_path=self.db)
        res = ti.week_summary_text(db_path=self.db, plan_path=self.plan_path, products_dir=str(self.products_dir), date="2026-11-04")
        self.assertIn("Pack #1:", res)
        self.assertIn("Pack #2:", res)
        self.assertIn("Pack #3:", res)

    def test_47_week_summary_scrubbing_applied(self):
        res = ti.week_summary_text(db_path=self.db)
        self.assertNotIn("ghp_", res)

    def test_48_duplicate_week_updates_in_burst_deduplicated(self):
        token = "123:TOKEN"
        owner = "111222333"
        updates = [
            {"update_id": 11, "message": {"chat": {"id": int(owner)}, "text": "/week"}},
            {"update_id": 12, "message": {"chat": {"id": int(owner)}, "text": "/week"}},
        ]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(len(sent), 1)
        self.assertIn("(answered 2 messages)", sent[0])

    def test_49_duplicate_ab_updates_in_burst_deduplicated(self):
        token = "123:TOKEN"
        owner = "111222333"
        updates = [
            {"update_id": 13, "message": {"chat": {"id": int(owner)}, "text": "ab 5"}},
            {"update_id": 14, "message": {"chat": {"id": int(owner)}, "text": "ab 5"}},
        ]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(len(sent), 1)
        self.assertIn("(answered 2 messages)", sent[0])

    def test_50_duplicate_tenglish_ee_vaaram_burst_deduplicated(self):
        token = "123:TOKEN"
        owner = "111222333"
        updates = [
            {"update_id": 15, "message": {"chat": {"id": int(owner)}, "text": "ee vaaram"}},
            {"update_id": 16, "message": {"chat": {"id": int(owner)}, "text": "ee vaaram"}},
        ]
        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append(text)):
            ti.process_updates(token, owner, db_path=self.db)

        self.assertEqual(len(sent), 1)
        self.assertIn("(answered 2 messages)", sent[0])


if __name__ == "__main__":
    unittest.main()
