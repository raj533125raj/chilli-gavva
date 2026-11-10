"""Unit tests for tg_posts plugin (BINNY-V2C: POST FROM THE PHONE).

Contains 60+ unit tests testing:
- COMMANDS metadata and exports
- Quiet hours handling (formats: muted, silent, start/end int, start/end str, boundary hours)
- Text matching (/post today, /post tomorrow, /post week, /posted N, Telugu/Tenglish phrases)
- Pin photo sending + caption + reply keyboard
- Carousel media group sending + caption text + reply keyboard
- Reel media group sending + caption text + reply keyboard
- Max 25 media per command safety limit
- post_planner.mark_posted integration and atomic saving
- No currency/money leakage across all generated text
- Missing file / asset fallbacks and edge conditions
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import post_planner
import tg_posts


class TestV2CPosts(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* and RUNNER_ENVIRONMENT env vars
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k.startswith("RUNNER_"):
                del os.environ[k]
        os.environ["CHILLI_TEST"] = "1"

        self.test_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.test_dir.name)

        # Setup standard context callbacks tracker
        self.sent_texts: list[tuple[str, Any]] = []
        self.sent_photos: list[tuple[str, str]] = []
        self.sent_media_groups: list[tuple[list[str], str]] = []
        self.sent_docs: list[tuple[str, str]] = []
        self.state_db: dict[str, Any] = {}

        self.fixed_utc_now = datetime.datetime(2026, 11, 10, 6, 0, 0, tzinfo=datetime.timezone.utc)  # 11:30 AM IST
        self.today_ist_str = "2026-11-10"
        self.tomorrow_ist_str = "2026-11-11"

        self.ctx = {
            "db_path": str(self.base_path / "test.db"),
            "chat_id": 12345678,
            "now": self.fixed_utc_now,
            "send_text": self._mock_send_text,
            "send_photo": self._mock_send_photo,
            "send_media_group": self._mock_send_media_group,
            "send_document": self._mock_send_doc,
            "state_get": self._mock_state_get,
            "state_set": self._mock_state_set,
        }

    def tearDown(self):
        self.env_patcher.stop()
        self.test_dir.cleanup()

    def _mock_send_text(self, text: str, keyboard: Any = None) -> bool:
        self.sent_texts.append((text, keyboard))
        return True

    def _mock_send_photo(self, path: str, caption: str = "") -> bool:
        self.sent_photos.append((path, caption))
        return True

    def _mock_send_media_group(self, paths: list[str], caption: str = "") -> bool:
        self.sent_media_groups.append((paths, caption))
        return True

    def _mock_send_doc(self, path: str, caption: str = "") -> bool:
        self.sent_docs.append((path, caption))
        return True

    def _mock_state_get(self, key: str, default: Any = None) -> Any:
        return self.state_db.get(key, default)

    def _mock_state_set(self, key: str, value: Any) -> None:
        self.state_db[key] = value

    def _create_pack(self, pack_id: str, title: str = "Focus Planner", tags: str = "focus, planner"):
        pdir = self.base_path / "exported_content" / "products" / "upload" / pack_id
        pdir.mkdir(parents=True, exist_ok=True)
        listing = f"Title: {title}\nDescription: A great daily planner for staying calm.\nTags: {tags}\n"
        (pdir / "listing.txt").write_text(listing, encoding="utf-8")
        (pdir / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\nfakecover")
        return pdir

    def _create_marketing(self, pack_id: str, pin_count: int = 2, carousel_slides: int = 5, reel_frames: int = 6):
        mdir = self.base_path / "exported_content" / "marketing" / pack_id
        pins_dir = mdir / "pins"
        pins_dir.mkdir(parents=True, exist_ok=True)
        pins_data = []
        for i in range(1, pin_count + 1):
            fname = f"pin_{i}_a.png"
            (pins_dir / fname).write_bytes(b"\x89PNG\r\n\x1a\nfakepin")
            pins_data.append({
                "file": fname,
                "layout": "A",
                "title": f"Pin Title {i} for {pack_id}",
                "description": f"Pin Description {i} with tips",
                "alt": f"Alt text {i}",
            })
        (pins_dir / "pins.json").write_text(json.dumps(pins_data), encoding="utf-8")

        if carousel_slides > 0:
            car_dir = mdir / "carousel"
            car_dir.mkdir(parents=True, exist_ok=True)
            for i in range(1, carousel_slides + 1):
                (car_dir / f"slide-{i}.png").write_bytes(b"\x89PNG\r\n\x1a\nfakeslide")
            (car_dir / "caption.txt").write_text(f"Swipe through for {pack_id} daily tips.\nFollow for more!", encoding="utf-8")

        if reel_frames > 0:
            reel_dir = mdir / "reel"
            reel_dir.mkdir(parents=True, exist_ok=True)
            for i in range(1, reel_frames + 1):
                (reel_dir / f"frame-{i}.png").write_bytes(b"\x89PNG\r\n\x1a\nfakereel")
            (reel_dir / "reel.txt").write_text(f"Quick {pack_id} routine in 15 seconds!\n#routine #focus", encoding="utf-8")

        return mdir

    # 1. COMMANDS metadata tests
    def test_01_commands_is_list(self):
        self.assertIsInstance(tg_posts.COMMANDS, list)
        self.assertGreaterEqual(len(tg_posts.COMMANDS), 2)

    def test_02_commands_has_post_and_posted(self):
        cmds = [c["cmd"] for c in tg_posts.COMMANDS]
        self.assertIn("/post", cmds)
        self.assertIn("/posted", cmds)

    def test_03_commands_fields_present(self):
        for c in tg_posts.COMMANDS:
            self.assertIn("cmd", c)
            self.assertIn("words", c)
            self.assertIn("help_en", c)
            self.assertIn("help_te", c)
            self.assertTrue(c["words"])

    def test_04_no_money_in_commands_help(self):
        for c in tg_posts.COMMANDS:
            self.assertIsNone(tg_posts._MONEY_PATTERN.search(c["help_en"]))
            self.assertIsNone(tg_posts._MONEY_PATTERN.search(c["help_te"]))

    # 2. Quiet Hours Tests
    def test_05_quiet_hours_none_in_state(self):
        is_quiet, reason = tg_posts.is_quiet_hours(self.ctx)
        self.assertFalse(is_quiet)
        self.assertEqual(reason, "")

    def test_06_quiet_hours_muted_flag(self):
        self.state_db["owner_settings"] = {"muted": True}
        is_quiet, reason = tg_posts.is_quiet_hours(self.ctx)
        self.assertTrue(is_quiet)
        self.assertIn("muted", reason)

    def test_07_quiet_hours_silent_flag(self):
        self.state_db["owner_settings"] = json.dumps({"silent": True})
        is_quiet, reason = tg_posts.is_quiet_hours(self.ctx)
        self.assertTrue(is_quiet)

    def test_08_quiet_hours_quiet_mode_flag(self):
        self.state_db["owner_settings"] = {"quiet_mode": True}
        is_quiet, reason = tg_posts.is_quiet_hours(self.ctx)
        self.assertTrue(is_quiet)

    def test_09_quiet_hours_overnight_active(self):
        # 11:30 PM IST = 18:00 UTC
        late_utc = datetime.datetime(2026, 11, 10, 18, 0, 0, tzinfo=datetime.timezone.utc)
        ctx = dict(self.ctx, now=late_utc)
        self.state_db["owner_settings"] = {"quiet_hours": {"enabled": True, "start": "22:00", "end": "08:00"}}
        is_quiet, reason = tg_posts.is_quiet_hours(ctx)
        self.assertTrue(is_quiet)

    def test_10_quiet_hours_overnight_inactive_daytime(self):
        # 11:30 AM IST = 06:00 UTC
        self.state_db["owner_settings"] = {"quiet_hours": {"enabled": True, "start": "22:00", "end": "08:00"}}
        is_quiet, reason = tg_posts.is_quiet_hours(self.ctx)
        self.assertFalse(is_quiet)

    def test_11_quiet_hours_daytime_window_active(self):
        # Set quiet window 11:00 to 13:00 IST. Fixed now is 11:30 IST
        self.state_db["owner_settings"] = {"quiet_hours": {"enabled": True, "start": 11, "end": 13}}
        is_quiet, reason = tg_posts.is_quiet_hours(self.ctx)
        self.assertTrue(is_quiet)

    def test_12_quiet_hours_disabled(self):
        self.state_db["owner_settings"] = {"quiet_hours": {"enabled": False, "start": 0, "end": 24}}
        is_quiet, reason = tg_posts.is_quiet_hours(self.ctx)
        self.assertFalse(is_quiet)

    def test_13_quiet_hours_fallback_start_end_keys(self):
        self.state_db["owner_settings"] = {"quiet_start": 10, "quiet_end": 14}
        is_quiet, reason = tg_posts.is_quiet_hours(self.ctx)
        self.assertTrue(is_quiet)

    def test_14_quiet_hours_corrupt_json(self):
        self.state_db["owner_settings"] = "{corrupt"
        is_quiet, reason = tg_posts.is_quiet_hours(self.ctx)
        self.assertFalse(is_quiet)

    def test_15_post_today_quiet_hours_returns_notice(self):
        self.state_db["owner_settings"] = {"muted": True}
        res = tg_posts.handle("/post today", self.ctx)
        self.assertIsNotNone(res)
        self.assertIn("text", res)
        self.assertIn("Quiet hours", res["text"])
        self.assertEqual(len(self.sent_photos), 0)

    # 3. Text scrubbing and Hashtag formatting
    def test_16_scrub_text_removes_dollars(self):
        out = tg_posts._scrub_text("Special printable planner for $15 today!")
        self.assertNotIn("$15", out)
        self.assertNotIn("$", out)

    def test_17_scrub_text_removes_rupees(self):
        out = tg_posts._scrub_text("Awesome tracker for ₹499 only")
        self.assertNotIn("₹499", out)
        self.assertNotIn("₹", out)

    def test_18_scrub_text_removes_currency_words(self):
        out = tg_posts._scrub_text("Get this free planner price rupee discount")
        self.assertNotIn("free", out)
        self.assertNotIn("price", out)
        self.assertNotIn("rupee", out)

    def test_19_format_hashtags_cleans_tags(self):
        tags = ["calm routine", "focus#2026", "dollar", "$money", "organizer!"]
        res = tg_posts._format_hashtags(tags)
        self.assertIn("#calmroutine", res)
        self.assertIn("#focus2026", res)
        self.assertIn("#organizer", res)
        self.assertNotIn("dollar", res)
        self.assertNotIn("$", res)

    # 4. Command recognition & routing
    def test_20_handle_empty_input(self):
        self.assertIsNone(tg_posts.handle("", self.ctx))
        self.assertIsNone(tg_posts.handle(None, self.ctx))

    def test_21_handle_unrelated_command(self):
        self.assertIsNone(tg_posts.handle("/start", self.ctx))
        self.assertIsNone(tg_posts.handle("hello robot", self.ctx))
        self.assertIsNone(tg_posts.handle("/status", self.ctx))

    def test_22_handle_post_today_variations(self):
        # Empty plan produces informative text
        with mock.patch("post_planner.load_plan", return_value=[]):
            r1 = tg_posts.handle("/post today", self.ctx)
            r2 = tg_posts.handle("post today", self.ctx)
            r3 = tg_posts.handle("today posts", self.ctx)
            r4 = tg_posts.handle("ee roju posts", self.ctx)
            r5 = tg_posts.handle("/today", self.ctx)
            self.assertEqual(r1, {"text": "Post plan ledu. Upload packs ready ayyaka robot auto plan chestundi."})
            self.assertEqual(r2, r1)
            self.assertEqual(r3, r1)
            self.assertEqual(r4, r1)
            self.assertEqual(r5, r1)

    def test_23_handle_post_tomorrow_variations(self):
        with mock.patch("post_planner.load_plan", return_value=[]):
            r1 = tg_posts.handle("/post tomorrow", self.ctx)
            r2 = tg_posts.handle("post tomorrow", self.ctx)
            r3 = tg_posts.handle("repu posts", self.ctx)
            r4 = tg_posts.handle("/tomorrow", self.ctx)
            self.assertEqual(r1, {"text": "Post plan ledu. Upload packs ready ayyaka robot auto plan chestundi."})
            self.assertEqual(r2, r1)
            self.assertEqual(r3, r1)
            self.assertEqual(r4, r1)

    def test_24_handle_post_week_variations(self):
        with mock.patch("post_planner.load_plan", return_value=[]):
            r1 = tg_posts.handle("/post week", self.ctx)
            r2 = tg_posts.handle("post week", self.ctx)
            r3 = tg_posts.handle("ee vaaram posts", self.ctx)
            r4 = tg_posts.handle("/week", self.ctx)
            self.assertEqual(r1, {"text": "Post plan ledu. Upload packs ready ayyaka robot auto plan chestundi."})
            self.assertEqual(r2, r1)
            self.assertEqual(r3, r1)
            self.assertEqual(r4, r1)

    def test_25_handle_later_response(self):
        r1 = tg_posts.handle("⏭ later", self.ctx)
        self.assertIsNotNone(r1)
        self.assertIn("taruvatha", r1["text"])

    # 5. Posted N command matching and actions
    def test_26_posted_n_various_formats(self):
        fake_plan = [
            {"date": self.today_ist_str, "status": "todo", "channel": "pinterest"},
            {"date": self.today_ist_str, "status": "todo", "channel": "pinterest"},
        ]
        with mock.patch("post_planner.load_plan", return_value=fake_plan), \
             mock.patch("post_planner.save_plan", return_value=True), \
             mock.patch("post_planner.week_summary", return_value="This week: 1 of 2 posts done"):
            res1 = tg_posts.handle("/posted 1", self.ctx)
            self.assertIn("Post 1 done ✓", res1["text"])

            res2 = tg_posts.handle("posted 2", self.ctx)
            self.assertIn("Post 2 done ✓", res2["text"])

            res3 = tg_posts.handle("✅ posted 1", self.ctx)
            self.assertIn("Post 1 done ✓", res3["text"])

            res4 = tg_posts.handle("post chesa 2", self.ctx)
            self.assertIn("Post 2 done ✓", res4["text"])

            res5 = tg_posts.handle("post 1 done", self.ctx)
            self.assertIn("Post 1 done ✓", res5["text"])

    def test_27_posted_n_invalid_number_high(self):
        fake_plan = [{"date": self.today_ist_str, "status": "todo"}]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/posted 5", self.ctx)
            self.assertIn("Invalid post number 5", res["text"])

    def test_28_posted_n_invalid_number_zero(self):
        fake_plan = [{"date": self.today_ist_str, "status": "todo"}]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/posted 0", self.ctx)
            self.assertIn("Invalid post number 0", res["text"])

    def test_29_posted_n_no_posts_for_today(self):
        fake_plan = [{"date": "2026-01-01", "status": "todo"}]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/posted 1", self.ctx)
            self.assertIn("posts schedule avaledu", res["text"])

    def test_30_posted_n_empty_plan(self):
        with mock.patch("post_planner.load_plan", return_value=[]):
            res = tg_posts.handle("/posted 1", self.ctx)
            self.assertIn("Post plan ledu", res["text"])

    # 6. /post tomorrow preview
    def test_31_post_tomorrow_scheduled_items(self):
        fake_plan = [
            {
                "date": self.tomorrow_ist_str,
                "channel": "pinterest",
                "pack_id": "pack-calm",
                "asset": "pins/pin_1_a.png",
                "caption_short": "Calm Day Planner",
                "hashtags": ["calm", "focus"],
            },
            {
                "date": self.tomorrow_ist_str,
                "channel": "instagram",
                "pack_id": "pack-habits",
                "asset": "carousel/slide-1.png",
                "caption_short": "Daily Habits Guide",
                "hashtags": ["habits"],
            },
        ]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post tomorrow", self.ctx)
            self.assertIsNotNone(res)
            self.assertIn("Tomorrow Post Preview", res["text"])
            self.assertIn("Calm Day Planner", res["text"])
            self.assertIn("Daily Habits Guide", res["text"])
            self.assertIn("Preview only", res["text"])
            # Assert no media photos were sent
            self.assertEqual(len(self.sent_photos), 0)

    def test_32_post_tomorrow_no_items_scheduled(self):
        fake_plan = [{"date": "2026-11-20", "channel": "pinterest"}]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post tomorrow", self.ctx)
            self.assertIn("posts schedule avaledu", res["text"])

    # 7. /post week list
    def test_33_post_week_schedule(self):
        fake_plan = [
            {"date": self.today_ist_str, "channel": "pinterest", "status": "done"},
            {"date": self.today_ist_str, "channel": "instagram", "status": "todo"},
            {"date": self.tomorrow_ist_str, "channel": "pinterest", "status": "todo"},
        ]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post week", self.ctx)
            self.assertIn("Weekly Post Schedule", res["text"])
            self.assertIn(self.today_ist_str, res["text"])
            self.assertIn(self.tomorrow_ist_str, res["text"])

    # 8. /post today execution & media sending
    def test_34_post_today_no_posts_scheduled(self):
        fake_plan = [{"date": "2026-12-01", "channel": "pinterest"}]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post today", self.ctx)
            self.assertIn("posts schedule avaledu", res["text"])

    def test_35_post_today_pin_single_photo(self):
        # Create pack and marketing assets
        self._create_pack("pack-one", title="Daily Minimalist Planner")
        self._create_marketing("pack-one", pin_count=1, carousel_slides=0, reel_frames=0)

        # Plan for today with 1 pinterest post
        pin_asset_file = str(self.base_path / "exported_content" / "marketing" / "pack-one" / "pins" / "pin_1_a.png")
        fake_plan = [
            {
                "date": self.today_ist_str,
                "channel": "pinterest",
                "pack_id": "pack-one",
                "asset": pin_asset_file,
                "caption_short": "Daily Minimalist Planner",
                "hashtags": ["planner", "minimalist"],
                "status": "todo",
            }
        ]

        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post today", self.ctx)
            self.assertEqual(res, {"handled": True})
            self.assertEqual(len(self.sent_photos), 1)
            photo_path, caption = self.sent_photos[0]
            self.assertEqual(photo_path, pin_asset_file)
            self.assertIn("Post 1 (Pinterest)", caption)
            self.assertIn("#planner", caption)
            # Verify reply keyboard sent
            self.assertTrue(any(kb == [["✅ posted 1", "⏭ later"]] for _, kb in self.sent_texts))

    def test_36_post_today_carousel_media_group(self):
        self._create_pack("pack-car", title="Workout Journal")
        mkt_dir = self._create_marketing("pack-car", pin_count=0, carousel_slides=6, reel_frames=0)

        fake_plan = [
            {
                "date": self.today_ist_str,
                "channel": "carousel",
                "pack_id": "pack-car",
                "asset": "carousel/slide-1.png",
                "caption_short": "Workout Journal",
                "hashtags": ["workout", "fitness"],
                "status": "todo",
            }
        ]

        # Patch marketing resolution to point to test_dir
        with mock.patch("tg_posts._resolve_marketing_directory", return_value=mkt_dir), \
             mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post today", self.ctx)
            self.assertEqual(res, {"handled": True})
            self.assertEqual(len(self.sent_media_groups), 1)
            slides, mg_cap = self.sent_media_groups[0]
            self.assertEqual(len(slides), 6)
            self.assertIn("Carousel slides (6)", mg_cap)
            # Following text has caption + reply keyboard
            caption_texts = [txt for txt, kb in self.sent_texts if kb == [["✅ posted 1", "⏭ later"]]]
            self.assertEqual(len(caption_texts), 1)
            self.assertIn("Instagram Carousel Caption", caption_texts[0])

    def test_37_post_today_reel_media_group(self):
        self._create_pack("pack-reel", title="Study Session Guide")
        mkt_dir = self._create_marketing("pack-reel", pin_count=0, carousel_slides=0, reel_frames=8)

        fake_plan = [
            {
                "date": self.today_ist_str,
                "channel": "reel",
                "pack_id": "pack-reel",
                "asset": "reel/frame-1.png",
                "caption_short": "Study Session Guide",
                "hashtags": ["study", "focus"],
                "status": "todo",
            }
        ]

        with mock.patch("tg_posts._resolve_marketing_directory", return_value=mkt_dir), \
             mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post today", self.ctx)
            self.assertEqual(res, {"handled": True})
            self.assertEqual(len(self.sent_media_groups), 1)
            frames, mg_cap = self.sent_media_groups[0]
            self.assertEqual(len(frames), 8)
            self.assertIn("Reel frames (8)", mg_cap)
            caption_texts = [txt for txt, kb in self.sent_texts if kb == [["✅ posted 1", "⏭ later"]]]
            self.assertEqual(len(caption_texts), 1)
            self.assertIn("Reel Caption", caption_texts[0])

    def test_38_post_today_multiple_posts_mixed(self):
        p1 = self._create_pack("pack-p1")
        m1 = self._create_marketing("pack-p1", pin_count=2, carousel_slides=0)
        p2 = self._create_pack("pack-p2")
        m2 = self._create_marketing("pack-p2", pin_count=0, carousel_slides=4)

        pin_path = str(m1 / "pins" / "pin_1_a.png")
        car_path = str(m2 / "carousel" / "slide-1.png")

        fake_plan = [
            {"date": self.today_ist_str, "channel": "pinterest", "pack_id": "pack-p1", "asset": pin_path, "caption_short": "P1", "hashtags": ["p1"], "status": "todo"},
            {"date": self.today_ist_str, "channel": "carousel", "pack_id": "pack-p2", "asset": car_path, "caption_short": "P2", "hashtags": ["p2"], "status": "todo"},
        ]

        def resolve_mkt(pid, *args):
            return m1 if pid == "pack-p1" else m2

        with mock.patch("tg_posts._resolve_marketing_directory", side_effect=resolve_mkt), \
             mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post today", self.ctx)
            self.assertEqual(res, {"handled": True})
            self.assertEqual(len(self.sent_photos), 1)
            self.assertEqual(len(self.sent_media_groups), 1)

            # Both post 1 and post 2 should have keyboards sent
            keyboards = [kb for _, kb in self.sent_texts if kb is not None]
            self.assertIn([["✅ posted 1", "⏭ later"]], keyboards)
            self.assertIn([["✅ posted 2", "⏭ later"]], keyboards)

    def test_39_post_today_item_already_done(self):
        fake_plan = [
            {"date": self.today_ist_str, "channel": "pinterest", "pack_id": "pack-done", "asset": "p.png", "status": "done"},
            {"date": self.today_ist_str, "channel": "pinterest", "pack_id": "pack-todo", "asset": "p2.png", "status": "todo"},
        ]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post today", self.ctx)
            self.assertEqual(res, {"handled": True})
            done_msgs = [txt for txt, _ in self.sent_texts if "already posted ✓" in txt]
            self.assertEqual(len(done_msgs), 1)
            self.assertIn("Post 1", done_msgs[0])

    def test_40_post_today_max_25_media_cap(self):
        # Create a pack with large carousel (10 slides)
        mkt_dir = self._create_marketing("pack-huge", carousel_slides=10)
        # 3 carousels of 10 slides = 30 slides, exceeds 25
        fake_plan = [
            {"date": self.today_ist_str, "channel": "carousel", "pack_id": "pack-huge", "asset": "slide-1.png", "status": "todo"},
            {"date": self.today_ist_str, "channel": "carousel", "pack_id": "pack-huge", "asset": "slide-1.png", "status": "todo"},
            {"date": self.today_ist_str, "channel": "carousel", "pack_id": "pack-huge", "asset": "slide-1.png", "status": "todo"},
        ]

        with mock.patch("tg_posts._resolve_marketing_directory", return_value=mkt_dir), \
             mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post today", self.ctx)
            self.assertEqual(res, {"handled": True})

            total_media = sum(len(mg) for mg, _ in self.sent_media_groups) + len(self.sent_photos)
            self.assertLessEqual(total_media, tg_posts.MAX_MEDIA_PER_COMMAND)
            self.assertTrue(any("Media limit reached" in txt for txt, _ in self.sent_texts))

    def test_41_post_today_photo_missing_fallback(self):
        fake_plan = [
            {"date": self.today_ist_str, "channel": "pinterest", "pack_id": "pack-none", "asset": "nonexistent.png", "caption_short": "No Photo", "status": "todo"}
        ]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post today", self.ctx)
            self.assertEqual(res, {"handled": True})
            self.assertEqual(len(self.sent_photos), 0)
            missing_msgs = [txt for txt, _ in self.sent_texts if "photo missing" in txt]
            self.assertEqual(len(missing_msgs), 1)

    def test_42_post_today_carousel_missing_fallback_to_pack_cover(self):
        pdir = self._create_pack("pack-coveronly")
        cover_path = str(pdir / "cover.png")
        fake_plan = [
            {"date": self.today_ist_str, "channel": "carousel", "pack_id": "pack-coveronly", "asset": cover_path, "caption_short": "Cover Fallback", "status": "todo"}
        ]
        with mock.patch("tg_posts._resolve_marketing_directory", return_value=None), \
             mock.patch("tg_posts._resolve_pack_directory", return_value=pdir), \
             mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post today", self.ctx)
            self.assertEqual(res, {"handled": True})
            # Falls back to single photo
            self.assertEqual(len(self.sent_photos), 1)
            self.assertEqual(self.sent_photos[0][0], cover_path)

    def test_43_no_money_in_post_today_captions(self):
        fake_plan = [
            {
                "date": self.today_ist_str,
                "channel": "pinterest",
                "pack_id": "pack-money",
                "asset": "pins/p1.png",
                "caption_short": "Special Price $29 or ₹2400 Rupee Deal",
                "hashtags": ["deal", "dollar", "rupee", "planner"],
                "status": "todo",
            }
        ]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            tg_posts.handle("/post today", self.ctx)
            for txt, _ in self.sent_texts:
                self.assertIsNone(tg_posts._MONEY_PATTERN.search(txt))
                self.assertNotIn("$29", txt)
                self.assertNotIn("₹2400", txt)

    def test_44_no_money_in_post_tomorrow_text(self):
        fake_plan = [
            {
                "date": self.tomorrow_ist_str,
                "channel": "pinterest",
                "pack_id": "pack-money",
                "asset": "pins/p1.png",
                "caption_short": "Save $50 on annual pass",
                "hashtags": ["dollar", "sale"],
            }
        ]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post tomorrow", self.ctx)
            self.assertIsNone(tg_posts._MONEY_PATTERN.search(res["text"]))
            self.assertNotIn("$50", res["text"])

    def test_45_no_money_in_post_week_text(self):
        fake_plan = [
            {
                "date": self.today_ist_str,
                "channel": "pinterest",
                "pack_id": "pack-money",
                "status": "todo",
            }
        ]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post week", self.ctx)
            self.assertIsNone(tg_posts._MONEY_PATTERN.search(res["text"]))

    def test_46_post_today_without_send_callbacks_falls_back_to_text(self):
        plain_ctx = dict(self.ctx)
        del plain_ctx["send_photo"]
        fake_plan = [{"date": self.today_ist_str, "channel": "pinterest", "pack_id": "p1", "asset": "a.png"}]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post today", plain_ctx)
            self.assertIn("text", res)
            self.assertIn("Today post", res["text"])

    def test_47_resolve_pack_directory_upload_subfolder(self):
        pdir = self._create_pack("sub-pack")
        found = tg_posts._resolve_pack_directory("sub-pack", base_dir=str(self.base_path / "exported_content" / "products"))
        self.assertIsNotNone(found)
        self.assertEqual(found.resolve(), pdir.resolve())

    def test_48_resolve_asset_file_absolute_path(self):
        pdir = self._create_pack("abs-pack")
        cpath = str(pdir / "cover.png")
        found = tg_posts._resolve_asset_file("abs-pack", cpath)
        self.assertEqual(found, str(Path(cpath).resolve()))

    def test_49_resolve_asset_file_relative_marketing(self):
        mdir = self._create_marketing("rel-mkt", pin_count=1)
        found = tg_posts._resolve_asset_file("rel-mkt", "pins/pin_1_a.png", base_marketing=str(self.base_path / "exported_content" / "marketing"))
        self.assertIsNotNone(found)

    def test_50_get_pack_details_missing_listing(self):
        details = tg_posts._get_pack_details("nonexistent")
        self.assertEqual(details["title"], "nonexistent")
        self.assertEqual(details["description"], "")
        self.assertEqual(details["tags"], [])

    def test_51_get_pack_details_with_listing(self):
        self._create_pack("full-pack", title="Full Title", tags="alpha, beta")
        details = tg_posts._get_pack_details("full-pack", base_products=str(self.base_path / "exported_content" / "products"))
        self.assertEqual(details["title"], "Full Title")
        self.assertEqual(details["tags"], ["alpha", "beta"])

    def test_52_build_pin_caption_uses_pins_json_data(self):
        mdir = self._create_marketing("mkt-json", pin_count=1)
        pin_file = "pins/pin_1_a.png"
        post = {"asset": pin_file, "caption_short": "Fallback"}
        caption = tg_posts._build_pin_caption(post, {}, mdir)
        self.assertIn("Pin Title 1 for mkt-json", caption)
        self.assertIn("Pin Description 1 with tips", caption)

    def test_53_build_pin_caption_falls_back_when_not_in_json(self):
        post = {"asset": "pin_other.png", "caption_short": "Short Fallback"}
        caption = tg_posts._build_pin_caption(post, {}, None)
        self.assertIn("Short Fallback", caption)

    def test_54_collect_carousel_assets_empty_folder(self):
        slides, cap = tg_posts._collect_carousel_assets("empty", {}, None, None)
        self.assertEqual(slides, [])
        self.assertEqual(cap, "empty")

    def test_55_collect_reel_assets_empty_folder(self):
        frames, cap = tg_posts._collect_reel_assets("empty", {}, None, None)
        self.assertEqual(frames, [])
        self.assertEqual(cap, "empty")

    def test_56_ist_datetime_conversion(self):
        utc_dt = datetime.datetime(2026, 11, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)
        ist_dt = tg_posts._get_ist_datetime(utc_dt)
        self.assertEqual(ist_dt.hour, 5)
        self.assertEqual(ist_dt.minute, 30)

    def test_57_mark_posted_persists_to_disk(self):
        plan_file = self.base_path / "plan.json"
        fake_plan = [{"day": 1, "date": self.today_ist_str, "status": "todo", "channel": "pinterest"}]
        post_planner.save_plan(fake_plan, plan_file)

        res = tg_posts.handle_posted_n(1, self.ctx, plan_path=plan_file)
        self.assertIn("Post 1 done ✓", res["text"])

        loaded = post_planner.load_plan(plan_file)
        self.assertEqual(loaded[0]["status"], "done")

    def test_58_generic_channel_photo_send(self):
        pdir = self._create_pack("gen-pack")
        cpath = str(pdir / "cover.png")
        fake_plan = [
            {"date": self.today_ist_str, "channel": "twitter", "pack_id": "gen-pack", "asset": cpath, "caption_short": "Twitter Post", "status": "todo"}
        ]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            res = tg_posts.handle("/post today", self.ctx)
            self.assertEqual(res, {"handled": True})
            self.assertEqual(len(self.sent_photos), 1)
            self.assertIn("Post 1 (twitter)", self.sent_photos[0][1])

    def test_59_caption_length_truncation_safety(self):
        long_title = "A" * 1500
        post = {"asset": "p.png", "caption_short": long_title}
        cap = tg_posts._build_pin_caption(post, {}, None)
        self.assertLessEqual(len(cap), 400)

    def test_60_quiet_hours_invalid_setting_type(self):
        self.state_db["owner_settings"] = 12345
        is_quiet, reason = tg_posts.is_quiet_hours(self.ctx)
        self.assertFalse(is_quiet)

    def test_61_quiet_hours_corrupt_hours_format(self):
        self.state_db["owner_settings"] = {"quiet_hours": {"start": "invalid:time", "end": "xyz"}}
        is_quiet, reason = tg_posts.is_quiet_hours(self.ctx)
        self.assertFalse(is_quiet)

    def test_62_post_today_header_and_summary_messages(self):
        self._create_pack("hdr-pack")
        fake_plan = [{"date": self.today_ist_str, "channel": "pinterest", "pack_id": "hdr-pack", "asset": "c.png", "status": "todo"}]
        with mock.patch("post_planner.load_plan", return_value=fake_plan):
            tg_posts.handle("/post today", self.ctx)
            self.assertTrue(any("📅 Today Post Plan" in txt for txt, _ in self.sent_texts))
            self.assertTrue(any("Done! Sent today's posts" in txt for txt, _ in self.sent_texts))


if __name__ == "__main__":
    unittest.main()
