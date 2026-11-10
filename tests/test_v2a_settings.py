"""tests/test_v2a_settings.py — Comprehensive unit tests for tg_settings.py.

60+ tests covering:
- Schema versioning, default loading, corruption recovery, db/ctx load and save
- Text parsing for quiet hours, morning post time, niches, slots, limits, reset
- Confirmations (Yes/No, synonyms, 10-minute expiry, prompt formatting)
- State setting updates (versioned JSON in telegram_state)
- Tenglish house style messages (no money mentions, no secrets)
- Plugin interface conformance (COMMANDS, handle method, context operations)
- Scheduler integration helper queries (is_niche_enabled, is_slot_enabled, is_quiet_hour, etc.)
- Scheduler run_slot integration with owner settings
"""
import datetime
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import tg_settings


class BaseSettingsTest(unittest.TestCase):
    def setUp(self):
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "RUNNER_ENVIRONMENT"}
        clean_env["CHILLI_TEST"] = "1"
        self.env_patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        self.env_patcher.start()

        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_settings.db")
        self._init_db(self.db_path)

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _init_db(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

    def _make_ctx(self, now=None):
        sent_messages = []

        def _state_get(key, default=None):
            val = tg_settings._read_state_from_db(self.db_path, key)
            return val if val is not None else default

        def _state_set(key, value):
            tg_settings._write_state_to_db(self.db_path, key, value)

        def _send_text(text, keyboard=None):
            sent_messages.append({"text": text, "keyboard": keyboard})
            return True

        return {
            "db_path": self.db_path,
            "chat_id": int("1234" "5678"),
            "now": now or datetime.datetime.now(datetime.timezone.utc),
            "send_text": _send_text,
            "state_get": _state_get,
            "state_set": _state_set,
            "sent_messages": sent_messages,
        }


class TestSettingsDataStructures(BaseSettingsTest):
    def test_default_settings_keys(self):
        defs = tg_settings.get_default_settings()
        self.assertEqual(defs["version"], 1)
        self.assertIn("quiet_hours", defs)
        self.assertIn("morning_post_hour", defs)
        self.assertIn("niches", defs)
        self.assertIn("run_slots", defs)
        self.assertIn("daily_message_limit", defs)
        self.assertIn("updated_at", defs)

    def test_default_quiet_hours(self):
        defs = tg_settings.get_default_settings()
        self.assertFalse(defs["quiet_hours"]["enabled"])
        self.assertEqual(defs["quiet_hours"]["start"], 22)
        self.assertEqual(defs["quiet_hours"]["end"], 8)

    def test_default_morning_post_hour(self):
        defs = tg_settings.get_default_settings()
        self.assertEqual(defs["morning_post_hour"], 8)

    def test_default_niches(self):
        defs = tg_settings.get_default_settings()
        self.assertTrue(defs["niches"]["niche_budget_sheets"])
        self.assertTrue(defs["niches"]["niche_freelancer_trackers"])
        self.assertTrue(defs["niches"]["niche_adhd_planners"])
        self.assertTrue(defs["niches"]["niche_planners_2027"])
        self.assertTrue(defs["niches"]["niche_resume_bundle"])
        self.assertFalse(defs["niches"]["niche_notion_templates"])

    def test_default_run_slots(self):
        defs = tg_settings.get_default_settings()
        self.assertTrue(defs["run_slots"]["night"])

    def test_default_daily_message_limit(self):
        defs = tg_settings.get_default_settings()
        self.assertEqual(defs["daily_message_limit"], 50)


class TestLoadSaveSettings(BaseSettingsTest):
    def test_load_settings_empty_db_returns_defaults(self):
        loaded = tg_settings.load_settings(self.db_path)
        self.assertEqual(loaded["version"], 1)
        self.assertEqual(loaded["morning_post_hour"], 8)

    def test_load_settings_empty_ctx_returns_defaults(self):
        ctx = self._make_ctx()
        loaded = tg_settings.load_settings(ctx)
        self.assertEqual(loaded["version"], 1)
        self.assertEqual(loaded["daily_message_limit"], 50)

    def test_save_and_load_roundtrip_db(self):
        settings = tg_settings.get_default_settings()
        settings["morning_post_hour"] = 9
        settings["daily_message_limit"] = 75
        ok = tg_settings.save_settings(settings, self.db_path)
        self.assertTrue(ok)

        loaded = tg_settings.load_settings(self.db_path)
        self.assertEqual(loaded["morning_post_hour"], 9)
        self.assertEqual(loaded["daily_message_limit"], 75)

    def test_save_and_load_roundtrip_ctx(self):
        ctx = self._make_ctx()
        settings = tg_settings.get_default_settings()
        settings["niches"]["niche_budget_sheets"] = False
        ok = tg_settings.save_settings(settings, ctx)
        self.assertTrue(ok)

        loaded = tg_settings.load_settings(ctx)
        self.assertFalse(loaded["niches"]["niche_budget_sheets"])

    def test_load_corrupted_json_returns_defaults(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT OR REPLACE INTO telegram_state (key, value) VALUES (?, ?)",
                     (tg_settings.SETTINGS_STATE_KEY, "INVALID JSON {{{"))
        conn.commit()
        conn.close()

        loaded = tg_settings.load_settings(self.db_path)
        self.assertEqual(loaded["version"], 1)
        self.assertEqual(loaded["morning_post_hour"], 8)

    def test_load_partial_dict_preserves_defaults(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT OR REPLACE INTO telegram_state (key, value) VALUES (?, ?)",
                     (tg_settings.SETTINGS_STATE_KEY, json.dumps({"morning_post_hour": 10})))
        conn.commit()
        conn.close()

        loaded = tg_settings.load_settings(self.db_path)
        self.assertEqual(loaded["morning_post_hour"], 10)
        self.assertIn("niches", loaded)
        self.assertTrue(loaded["niches"]["niche_budget_sheets"])


class TestParseSettingRequests(BaseSettingsTest):
    def test_parse_quiet_hours_range_hyphen(self):
        req = tg_settings.parse_setting_request("quiet 22-08")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "quiet_hours")
        self.assertEqual(req["value"]["start"], 22)
        self.assertEqual(req["value"]["end"], 8)
        self.assertTrue(req["value"]["enabled"])

    def test_parse_quiet_hours_range_to(self):
        req = tg_settings.parse_setting_request("quiet 23 to 07")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "quiet_hours")
        self.assertEqual(req["value"]["start"], 23)
        self.assertEqual(req["value"]["end"], 7)
        self.assertTrue(req["value"]["enabled"])

    def test_parse_quiet_hours_off(self):
        req = tg_settings.parse_setting_request("quiet off")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "quiet_hours")
        self.assertFalse(req["value"]["enabled"])

    def test_parse_quiet_hours_on_default(self):
        req = tg_settings.parse_setting_request("quiet on")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "quiet_hours")
        self.assertTrue(req["value"]["enabled"])
        self.assertEqual(req["value"]["start"], 22)
        self.assertEqual(req["value"]["end"], 8)

    def test_parse_silent_synonym(self):
        req = tg_settings.parse_setting_request("silent 21-06")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "quiet_hours")
        self.assertEqual(req["value"]["start"], 21)
        self.assertEqual(req["value"]["end"], 6)

    def test_parse_morning_post_time(self):
        req = tg_settings.parse_setting_request("morning 9")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "morning_post_hour")
        self.assertEqual(req["value"], 9)

    def test_parse_morning_post_time_full_phrase(self):
        req = tg_settings.parse_setting_request("morning post time 10")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "morning_post_hour")
        self.assertEqual(req["value"], 10)

    def test_parse_niche_budget_off(self):
        req = tg_settings.parse_setting_request("niche budget off")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "niche")
        self.assertEqual(req["target"], "niche_budget_sheets")
        self.assertFalse(req["value"])

    def test_parse_niche_adhd_on(self):
        req = tg_settings.parse_setting_request("niche adhd on")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "niche")
        self.assertEqual(req["target"], "niche_adhd_planners")
        self.assertTrue(req["value"])

    def test_parse_niche_resume_bundle_off(self):
        req = tg_settings.parse_setting_request("niche resume bundle off")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "niche")
        self.assertEqual(req["target"], "niche_resume_bundle")
        self.assertFalse(req["value"])

    def test_parse_niche_freelancer_on(self):
        req = tg_settings.parse_setting_request("niche freelancer on")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "niche")
        self.assertEqual(req["target"], "niche_freelancer_trackers")
        self.assertTrue(req["value"])

    def test_parse_niche_notion_on(self):
        req = tg_settings.parse_setting_request("niche notion on")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "niche")
        self.assertEqual(req["target"], "niche_notion_templates")
        self.assertTrue(req["value"])

    def test_parse_run_slot_night_off(self):
        req = tg_settings.parse_setting_request("night run off")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "run_slot")
        self.assertEqual(req["target"], "night")
        self.assertFalse(req["value"])

    def test_parse_run_slot_night_on(self):
        req = tg_settings.parse_setting_request("night slot on")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "run_slot")
        self.assertEqual(req["target"], "night")
        self.assertTrue(req["value"])

    def test_parse_run_slot_prefix_order(self):
        req = tg_settings.parse_setting_request("slot night off")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "run_slot")
        self.assertEqual(req["target"], "night")
        self.assertFalse(req["value"])

    def test_parse_daily_message_limit(self):
        req = tg_settings.parse_setting_request("message limit 60")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "daily_message_limit")
        self.assertEqual(req["value"], 60)

    def test_parse_daily_message_limit_full_phrase(self):
        req = tg_settings.parse_setting_request("daily message limit 40")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "daily_message_limit")
        self.assertEqual(req["value"], 40)

    def test_parse_settings_reset(self):
        req = tg_settings.parse_setting_request("settings reset")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "reset")

    def test_parse_restore_defaults(self):
        req = tg_settings.parse_setting_request("restore defaults")
        self.assertIsNotNone(req)
        self.assertEqual(req["type"], "reset")

    def test_parse_invalid_text_returns_none(self):
        self.assertIsNone(tg_settings.parse_setting_request("hello world"))
        self.assertIsNone(tg_settings.parse_setting_request("show me packs"))
        self.assertIsNone(tg_settings.parse_setting_request(""))


class TestApplySettingChanges(BaseSettingsTest):
    def test_apply_quiet_hours(self):
        defs = tg_settings.get_default_settings()
        change = {"type": "quiet_hours", "value": {"enabled": True, "start": 21, "end": 7}}
        res = tg_settings.apply_setting_change(defs, change)
        self.assertTrue(res["quiet_hours"]["enabled"])
        self.assertEqual(res["quiet_hours"]["start"], 21)
        self.assertEqual(res["quiet_hours"]["end"], 7)

    def test_apply_morning_post_hour(self):
        defs = tg_settings.get_default_settings()
        change = {"type": "morning_post_hour", "value": 7}
        res = tg_settings.apply_setting_change(defs, change)
        self.assertEqual(res["morning_post_hour"], 7)

    def test_apply_niche_toggle(self):
        defs = tg_settings.get_default_settings()
        change = {"type": "niche", "target": "niche_budget_sheets", "value": False}
        res = tg_settings.apply_setting_change(defs, change)
        self.assertFalse(res["niches"]["niche_budget_sheets"])

    def test_apply_run_slot_toggle(self):
        defs = tg_settings.get_default_settings()
        change = {"type": "run_slot", "target": "night", "value": False}
        res = tg_settings.apply_setting_change(defs, change)
        self.assertFalse(res["run_slots"]["night"])

    def test_apply_daily_message_limit(self):
        defs = tg_settings.get_default_settings()
        change = {"type": "daily_message_limit", "value": 120}
        res = tg_settings.apply_setting_change(defs, change)
        self.assertEqual(res["daily_message_limit"], 120)

    def test_apply_reset(self):
        custom = tg_settings.get_default_settings()
        custom["morning_post_hour"] = 11
        custom["niches"]["niche_budget_sheets"] = False
        res = tg_settings.apply_setting_change(custom, {"type": "reset"})
        self.assertEqual(res["morning_post_hour"], 8)
        self.assertTrue(res["niches"]["niche_budget_sheets"])


class TestConfirmationDetection(BaseSettingsTest):
    def test_confirmation_yes_synonyms(self):
        for w in ["yes", "Yes", "y", "Y", "avunu", "sare", "confirm", "ok", "OK", "okay", "ha"]:
            self.assertTrue(tg_settings.is_confirmation_reply(w), f"Failed for {w}")

    def test_confirmation_no_synonyms(self):
        for w in ["no", "No", "n", "N", "vaddu", "kaadhu", "cancel", "stop", "oddu"]:
            self.assertFalse(tg_settings.is_confirmation_reply(w), f"Failed for {w}")

    def test_non_confirmation_words(self):
        for w in ["settings", "quiet 22-08", "random text", "hello"]:
            self.assertIsNone(tg_settings.is_confirmation_reply(w), f"Failed for {w}")


class TestHelperQueries(BaseSettingsTest):
    def test_is_niche_enabled_default(self):
        self.assertTrue(tg_settings.is_niche_enabled("niche_budget_sheets", self.db_path))
        self.assertTrue(tg_settings.is_niche_enabled("budget", self.db_path))
        self.assertFalse(tg_settings.is_niche_enabled("notion", self.db_path))

    def test_is_niche_enabled_after_update(self):
        s = tg_settings.get_default_settings()
        s["niches"]["niche_budget_sheets"] = False
        tg_settings.save_settings(s, self.db_path)
        self.assertFalse(tg_settings.is_niche_enabled("niche_budget_sheets", self.db_path))
        self.assertFalse(tg_settings.is_niche_enabled("budget", self.db_path))

    def test_is_slot_enabled_default(self):
        self.assertTrue(tg_settings.is_slot_enabled("night", self.db_path))

    def test_is_slot_enabled_after_update(self):
        s = tg_settings.get_default_settings()
        s["run_slots"]["night"] = False
        tg_settings.save_settings(s, self.db_path)
        self.assertFalse(tg_settings.is_slot_enabled("night", self.db_path))

    def test_is_quiet_hour_disabled_default(self):
        self.assertFalse(tg_settings.is_quiet_hour(23, self.db_path))
        self.assertFalse(tg_settings.is_quiet_hour(3, self.db_path))

    def test_is_quiet_hour_when_enabled_midnight_crossing(self):
        s = tg_settings.get_default_settings()
        s["quiet_hours"] = {"enabled": True, "start": 22, "end": 8}
        tg_settings.save_settings(s, self.db_path)
        self.assertTrue(tg_settings.is_quiet_hour(22, self.db_path))
        self.assertTrue(tg_settings.is_quiet_hour(23, self.db_path))
        self.assertTrue(tg_settings.is_quiet_hour(0, self.db_path))
        self.assertTrue(tg_settings.is_quiet_hour(7, self.db_path))
        self.assertFalse(tg_settings.is_quiet_hour(8, self.db_path))
        self.assertFalse(tg_settings.is_quiet_hour(12, self.db_path))

    def test_is_quiet_hour_when_enabled_same_day_range(self):
        s = tg_settings.get_default_settings()
        s["quiet_hours"] = {"enabled": True, "start": 1, "end": 6}
        tg_settings.save_settings(s, self.db_path)
        self.assertTrue(tg_settings.is_quiet_hour(1, self.db_path))
        self.assertTrue(tg_settings.is_quiet_hour(5, self.db_path))
        self.assertFalse(tg_settings.is_quiet_hour(6, self.db_path))
        self.assertFalse(tg_settings.is_quiet_hour(0, self.db_path))

    def test_get_morning_post_hour(self):
        self.assertEqual(tg_settings.get_morning_post_hour(self.db_path), 8)
        s = tg_settings.get_default_settings()
        s["morning_post_hour"] = 6
        tg_settings.save_settings(s, self.db_path)
        self.assertEqual(tg_settings.get_morning_post_hour(self.db_path), 6)

    def test_get_daily_message_limit(self):
        self.assertEqual(tg_settings.get_daily_message_limit(self.db_path), 50)
        s = tg_settings.get_default_settings()
        s["daily_message_limit"] = 100
        tg_settings.save_settings(s, self.db_path)
        self.assertEqual(tg_settings.get_daily_message_limit(self.db_path), 100)


class TestPluginHandleFlow(BaseSettingsTest):
    def test_handle_settings_slash_command(self):
        ctx = self._make_ctx()
        res = tg_settings.handle("/settings", ctx)
        self.assertTrue(res.get("handled") or "text" in res)
        self.assertGreater(len(ctx["sent_messages"]), 0)
        text = ctx["sent_messages"][0]["text"]
        self.assertIn("Robot Settings", text)
        self.assertIn("Quiet Hours", text)
        self.assertIn("Morning Post Time", text)

    def test_handle_settings_plain_word(self):
        ctx = self._make_ctx()
        res = tg_settings.handle("settings", ctx)
        self.assertTrue(res.get("handled") or "text" in res)
        self.assertIn("Robot Settings", ctx["sent_messages"][0]["text"])

    def test_handle_unrelated_command_returns_none(self):
        ctx = self._make_ctx()
        res = tg_settings.handle("/packs", ctx)
        self.assertIsNone(res)
        self.assertEqual(len(ctx["sent_messages"]), 0)

    def test_handle_setting_change_initiates_confirmation(self):
        ctx = self._make_ctx()
        res = tg_settings.handle("quiet 22-08", ctx)
        self.assertTrue(res.get("handled") or "text" in res)
        self.assertGreater(len(ctx["sent_messages"]), 0)
        sent = ctx["sent_messages"][0]
        self.assertIn("Setting Change Confirmation", sent["text"])
        self.assertIn("Quiet hours ON cheyali", sent["text"])
        self.assertIsNotNone(sent.get("keyboard"))

        pending = tg_settings._get_pending_change(ctx)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["type"], "quiet_hours")

    def test_handle_confirmation_yes_applies_setting(self):
        ctx = self._make_ctx()
        tg_settings.handle("niche budget off", ctx)
        self.assertEqual(len(ctx["sent_messages"]), 1)

        res2 = tg_settings.handle("Yes", ctx)
        self.assertTrue(res2.get("handled") or "text" in res2)
        self.assertEqual(len(ctx["sent_messages"]), 2)
        sent2 = ctx["sent_messages"][1]
        self.assertIn("Setting update chesam", sent2["text"])

        settings = tg_settings.load_settings(ctx)
        self.assertFalse(settings["niches"]["niche_budget_sheets"])
        self.assertIsNone(tg_settings._get_pending_change(ctx))

    def test_handle_confirmation_no_cancels_change(self):
        ctx = self._make_ctx()
        tg_settings.handle("morning 11", ctx)
        self.assertEqual(len(ctx["sent_messages"]), 1)

        res2 = tg_settings.handle("No", ctx)
        self.assertTrue(res2.get("handled") or "text" in res2)
        self.assertEqual(len(ctx["sent_messages"]), 2)
        sent2 = ctx["sent_messages"][1]
        self.assertIn("Cancel chesam", sent2["text"])

        settings = tg_settings.load_settings(ctx)
        self.assertEqual(settings["morning_post_hour"], 8)
        self.assertIsNone(tg_settings._get_pending_change(ctx))

    def test_handle_confirmation_telugu_yes(self):
        ctx = self._make_ctx()
        tg_settings.handle("message limit 80", ctx)
        res2 = tg_settings.handle("avunu", ctx)
        self.assertTrue(res2.get("handled") or "text" in res2)
        settings = tg_settings.load_settings(ctx)
        self.assertEqual(settings["daily_message_limit"], 80)

    def test_handle_confirmation_telugu_no(self):
        ctx = self._make_ctx()
        tg_settings.handle("night run off", ctx)
        res2 = tg_settings.handle("vaddu", ctx)
        self.assertTrue(res2.get("handled") or "text" in res2)
        settings = tg_settings.load_settings(ctx)
        self.assertTrue(settings["run_slots"]["night"])

    def test_handle_confirmation_expired_after_10_minutes(self):
        t0 = datetime.datetime(2026, 11, 9, 10, 0, 0, tzinfo=datetime.timezone.utc)
        ctx = self._make_ctx(now=t0)
        tg_settings.handle("quiet 21-07", ctx)

        t1 = t0 + datetime.timedelta(minutes=11)
        ctx["now"] = t1
        res = tg_settings.handle("Yes", ctx)
        self.assertTrue(res.get("handled") or "text" in res)
        sent = ctx["sent_messages"][-1]["text"]
        self.assertIn("expire ayipoyindi", sent)

        settings = tg_settings.load_settings(ctx)
        self.assertFalse(settings["quiet_hours"]["enabled"])

    def test_handle_new_command_overrides_pending(self):
        ctx = self._make_ctx()
        tg_settings.handle("morning 9", ctx)
        pending1 = tg_settings._get_pending_change(ctx)
        self.assertEqual(pending1["type"], "morning_post_hour")

        tg_settings.handle("quiet 22-08", ctx)
        pending2 = tg_settings._get_pending_change(ctx)
        self.assertEqual(pending2["type"], "quiet_hours")


class TestFormattingAndHouseStyle(BaseSettingsTest):
    def test_format_settings_contains_no_money_or_currency(self):
        s = tg_settings.get_default_settings()
        formatted = tg_settings.format_settings_text(s)
        currency_amount_regex = re.compile(
            r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
            re.IGNORECASE,
        )
        self.assertIsNone(currency_amount_regex.search(formatted))
        for sym in ["$", "€", "₹"]:
            self.assertNotIn(sym, formatted)

    def test_format_settings_shows_all_niches(self):
        s = tg_settings.get_default_settings()
        formatted = tg_settings.format_settings_text(s)
        for key in tg_settings.NICHE_DISPLAY_NAMES.keys():
            self.assertIn(key, formatted)

    def test_format_settings_shows_quiet_hours_on(self):
        s = tg_settings.get_default_settings()
        s["quiet_hours"] = {"enabled": True, "start": 23, "end": 7}
        formatted = tg_settings.format_settings_text(s)
        self.assertIn("Quiet Hours:* ON", formatted)
        self.assertIn("23:00 - 07:00 UTC", formatted)

    def test_commands_list_conforms_to_plugin_spec(self):
        self.assertIsInstance(tg_settings.COMMANDS, list)
        self.assertGreater(len(tg_settings.COMMANDS), 0)
        cmd = tg_settings.COMMANDS[0]
        self.assertIn("cmd", cmd)
        self.assertIn("words", cmd)
        self.assertIn("help_en", cmd)
        self.assertIn("help_te", cmd)


class TestSchedulerSettingsIntegration(BaseSettingsTest):
    def test_run_slot_skips_when_slot_disabled_in_settings(self):
        import scheduler
        settings = tg_settings.get_default_settings()
        settings["run_slots"]["night"] = False
        tg_settings.save_settings(settings, self.db_path)

        slot = {"name": "night", "hour": 21, "recipe": "niche_budget_sheets"}
        res = scheduler.run_slot(slot, dry_run=True, db_path=self.db_path)
        self.assertTrue(res.get("skipped"))
        self.assertIn("run slot 'night' is disabled in owner settings", res.get("reason", ""))

    def test_run_slot_skips_when_niche_disabled_in_settings(self):
        import scheduler
        settings = tg_settings.get_default_settings()
        settings["niches"]["niche_budget_sheets"] = False
        tg_settings.save_settings(settings, self.db_path)

        slot = {"name": "night", "hour": 21, "recipe": "niche_budget_sheets"}
        res = scheduler.run_slot(slot, dry_run=True, db_path=self.db_path)
        self.assertTrue(res.get("skipped"))
        self.assertIn("niche 'niche_budget_sheets' is disabled in owner settings", res.get("reason", ""))

    def test_run_slot_runs_when_slot_and_niche_enabled(self):
        import scheduler, recipe_runner
        settings = tg_settings.get_default_settings()
        settings["run_slots"]["night"] = True
        settings["niches"]["niche_budget_sheets"] = True
        tg_settings.save_settings(settings, self.db_path)

        slot = {"name": "night", "hour": 21, "recipe": "niche_budget_sheets"}
        orig_run_recipe = recipe_runner.run_recipe
        recipe_runner.run_recipe = lambda path, db_path=None, dry_run=False: {"enqueued": ["task1"], "errors": []}
        try:
            res = scheduler.run_slot(slot, dry_run=True, db_path=self.db_path)
            self.assertFalse(res.get("skipped", False))
            self.assertEqual(res.get("enqueued"), ["task1"])
        finally:
            recipe_runner.run_recipe = orig_run_recipe


if __name__ == "__main__":
    unittest.main()
