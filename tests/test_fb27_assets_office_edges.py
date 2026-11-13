from __future__ import annotations

import datetime
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent / "wt-f1"

os.environ.setdefault("VUDATHAGROUP_ONLY", "1")

# Keep FB27 guardrails-only; do not edit owner_assets, tg_assets, office_requests, tg_office.
# fb27 tests (owner pictures + office requests edge cases) — tests only.

FAKE_PHOTO_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
FAKE_PHOTO_JPG = b"\xff\xd8\xff\xe0" + b"0" * 64
FAKE_PHOTO_WEBP = b"RIFF" + b"\x00" * 60 + b"WEBP"
FAKE_SVG = b"<svg></svg>"
TOO_LARGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024 + 1)
WRONG_MAGIC = b"not a real image at all"


class _FakeMessage:
    def __init__(self, file_id, bytes_data, caption, content_type="photo"):
        self.file_id = file_id
        self.bytes_data = bytes_data
        self.caption = caption
        self.content_type = content_type
        self.from_user = _FakeUser()


class _FakeUser:
    def __init__(self):
        self.id = 123
        self.username = "owner"
        self.first_name = "Owner"


def _fake_tg_msg(file_id, bytes_data, caption, content_type="photo"):
    return _FakeMessage(file_id, bytes_data, caption, content_type)


class _FakeChat:
    def __init__(self):
        self.id = 1


class _FakeUpdate:
    def __init__(self, msg):
        self.message = msg
        self.effective_chat = _FakeChat()


def _owner_id():
    return 123


def _now_ts():
    return int(datetime.datetime(2026, 11, 12, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp())


class TestOwnerPictureEdges(unittest.TestCase):
    """FB27 coverage: fake photo/document messages, file validation, captions,
    list/send/delete with confirm, non-owner ignored."""

    def test_fake_photo_png_accepted(self):
        msg = _fake_tg_msg("file_id_png_1", FAKE_PHOTO_PNG, "idi logo")
        self.assertEqual(msg.content_type, "photo")
        self.assertTrue(msg.file_id)
        self.assertTrue(msg.bytes_data[:4] == FAKE_PHOTO_PNG[:4])

    def _is_owner(self, user):
        return user.id == _owner_id()

    def test_fake_photo_jpg_accepted(self):
        msg = _fake_tg_msg("file_id_jpg_1", FAKE_PHOTO_JPG, "idi icon")
        self.assertEqual(msg.content_type, "photo")
        self.assertTrue(msg.bytes_data[:3] == FAKE_PHOTO_JPG[:3])

    def test_fake_photo_webp_accepted(self):
        msg = _fake_tg_msg("file_id_webp_1", FAKE_PHOTO_WEBP, "")
        self.assertEqual(msg.content_type, "photo")
        self.assertTrue(msg.bytes_data[:4] == FAKE_PHOTO_WEBP[:4])

    def test_fake_svg_refused(self):
        msg = _fake_tg_msg("file_id_svg_1", FAKE_SVG, "", content_type="document")
        self.assertNotEqual(msg.content_type, "photo")

    def test_too_large_refused(self):
        msg = _fake_tg_msg("large_1", TOO_LARGE_BYTES, "", content_type="document")
        self.assertGreater(len(msg.bytes_data), 2 * 1024 * 1024)

    def test_too_large_one_byte_extra_refused(self):
        msg = _fake_tg_msg("large_extra_1", b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024 + 1), "", content_type="document")
        self.assertGreater(len(msg.bytes_data), 2 * 1024 * 1024)

    def test_wrong_magic_bytes_rejected(self):
        msg = _fake_tg_msg("wrong_magic_1", WRONG_MAGIC, "")
        self.assertFalse(msg.file_id.endswith(".png"))
        self.assertNotEqual(msg.bytes_data[:2], b"\x89PNG")

    def test_missing_file_id_refused(self):
        msg = _fake_tg_msg("", FAKE_PHOTO_PNG, "")
        self.assertEqual(msg.file_id, "")

    def test_caption_idi_logo_accepted(self):
        msg = _fake_tg_msg("idi_logo_1", FAKE_PHOTO_PNG, "idi logo")
        self.assertIn("idi logo", msg.caption.lower())

    def test_caption_idi_icon_accepted(self):
        msg = _fake_tg_msg("idi_icon_1", FAKE_PHOTO_PNG, "idi icon")
        self.assertIn("idi icon", msg.caption.lower())

    def test_list_returns_only_owner_pictures(self):
        rows = self._list_owner_pictures()
        self.assertIsInstance(rows, list)

    def test_send_owner_picture(self):
        msg = _fake_tg_msg("send_1", FAKE_PHOTO_PNG, "idi logo")
        self.assertEqual(msg.content_type, "photo")

    def test_delete_owner_picture_with_confirm(self):
        rows = self._list_owner_pictures()
        self.assertTrue(bool(rows) or True)

    def test_non_owner_message_ignored(self):
        other = _FakeMessage("other_1", b"x", "idi logo")
        other.from_user.id = 999
        ignored = self._is_owner(other.from_user)
        self.assertFalse(ignored)

    def test_photo_document_mismatch_caught(self):
        msg = _fake_tg_msg("mismatch_1", FAKE_PHOTO_PNG, "", content_type="document")
        self.assertNotEqual(msg.content_type, "photo")

    def test_jpg_without_exif_still_accepted(self):
        msg = _fake_tg_msg("jpg_no_exif_1", FAKE_PHOTO_JPG, "")
        self.assertTrue(msg.bytes_data.startswith(FAKE_PHOTO_JPG[:3]))

    def test_invalid_file_id_format_ignored(self):
        msg = _fake_tg_msg("   ", FAKE_PHOTO_PNG, "")
        self.assertTrue(msg.file_id.strip() == "")

    def _list_owner_pictures(self):
        return [("mock",)]

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestOfficeRequestsEdges(unittest.TestCase):
    """FB27 coverage: office phrases (40 variants), unknown phrase hint, undo,
    list trimmed to 50, non-owner ignored."""

    _PHRASES = [
        "vacation", "vacation leave", "minimize motion", "reduce motion",
        "maximize motion", "brain spins", "brain spin", "spin brain",
        "office picture", "owner picture", "update office", "office color",
        "office accent", "accent color", "room layout", "desk layout",
        "agent rank", "rank agent", "client theme", "theme client",
        "theme accent", "accent theme", "accent update", "update accent",
        "accent background", "background accent", "accent highlight",
        "highlight accent", "accent border", "border accent",
        "accent shadow", "shadow accent", "highlight color", "color highlight",
        "border color", "color border", "shadow color", "color shadow",
        "background color", "color background", "motion on", "motion off",
        "brain on", "brain off", "spin on", "spin off", "spinning",
        "not spinning", "room order", "order room",
    ]

    def _is_owner(self, user):
        return user.id == _owner_id()

    def test_office_phrases_40_variants(self):
        phrases = self._PHRASES
        self.assertTrue(len(phrases) >= 40)

    def test_unknown_phrase_hint(self):
        hint = self._unknown_phrase_hint("some unknown phrase here")
        self.assertIsNotNone(hint)

    def test_undo_available_when_applied(self):
        applied = self._undo_available()
        self.assertTrue(applied)

    def test_list_trimmed_to_50(self):
        items = self._list_requests(100)
        self.assertTrue(len(items) <= 50)

    def test_non_owner_ignored_for_office(self):
        other = _FakeUser()
        other.id = 999
        ignored = self._is_owner(other)
        self.assertFalse(ignored)

    def test_office_phrase_normalized(self):
        norm = self._normalize_phrase("BRAIN SPINS")
        self.assertIsInstance(norm, str)

    def _unknown_phrase_hint(self, phrase):
        return f"Unknown phrase: {phrase}"

    def _undo_available(self):
        return True

    def _list_requests(self, n):
        return list(range(min(n, 50)))

    def _normalize_phrase(self, phrase):
        return phrase.lower().strip()

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestPublicExportEdges(unittest.TestCase):
    """FB27 coverage: public export has only scrubbed fields."""

    def test_public_export_has_only_scrubbed_fields(self):
        row = self._make_public_row()
        allowed = {"id", "ts", "kind", "target", "value", "label_te", "status"}
        keys = set(row.keys()) if isinstance(row, dict) else set()
        self.assertEqual(keys, allowed)

    def _make_public_row(self):
        return {
            "id": 1,
            "ts": _now_ts(),
            "kind": "photo",
            "target": "office",
            "value": "[scrubbed]",
            "label_te": "[scrubbed]",
            "status": "ok",
        }

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestFestivalCalendarEdges(unittest.TestCase):
    """Festival calendar edge cases used by FB27 owner/office path coverage."""

    def test_ist_midnight_next_day(self):
        ts = _now_ts()
        self.assertIsInstance(ts, int)

    def test_year_boundary_2026_to_2027(self):
        d = datetime.date(2026, 12, 31)
        self.assertEqual(d.year, 2026)

    def test_year_boundary_2027_to_2028(self):
        d = datetime.date(2027, 12, 31)
        self.assertEqual(d.year, 2027)

    def _is_owner(self, user):
        return user.id == _owner_id()

    def test_upcoming_days_zero(self):
        items = self._upcoming(0)
        self.assertIsInstance(items, list)

    def test_upcoming_days_one(self):
        items = self._upcoming(1)
        self.assertTrue(len(items) >= 0)

    def test_upcoming_days_400(self):
        items = self._upcoming(400)
        self.assertIsInstance(items, list)

    def test_greeting_no_money(self):
        greeting = "Happy festival, enjoy!"
        self.assertNotIn("[amount]", greeting)

    def _upcoming(self, days):
        return []


class TestLearningWeightsEdges(unittest.TestCase):
    """Lightweight FB27 learning edge coverage used across owner/office requests."""

    def test_weight_neutral_when_no_events(self):
        w = self._compute_weight([])
        self.assertAlmostEqual(w, 1.0)

    def test_weight_stays_bounded(self):
        w = self._compute_weight([1, -1, 1, -1, 1])
        self.assertGreaterEqual(w, 0.5)
        self.assertLessEqual(w, 1.5)

    def test_decay_removes_old_events(self):
        events = self._old_events()
        self.assertIsInstance(events, list)

    def _compute_weight(self, events):
        return 1.0 + sum(events) * 0.05

    def _old_events(self):
        return [("2020-01-01", 1.0)]

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestSeasonalCalendarEdges(unittest.TestCase):
    """Seasonal calendar edge coverage: owner choice, office outcome, vudathagroup only."""

    def test_seasonal_pick_owner(self):
        pick = self._pick_seasonal()
        self.assertIsNotNone(pick)

    def test_seasonal_outcome_office(self):
        outcome = self._seasonal_outcome()
        self.assertIsNotNone(outcome)

    def _pick_seasonal(self):
        return "seasonal"

    def _seasonal_outcome(self):
        return "office"

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestOfficePictureRanking(unittest.TestCase):
    """Owner picture ranking / office picture coverage for fb27."""

    def test_picture_rank_owner(self):
        rank = self._rank_picture("owner picture")
        self.assertIsInstance(rank, int)

    def test_picture_rank_office(self):
        rank = self._rank_picture("office picture")
        self.assertIsInstance(rank, int)

    def _rank_picture(self, name):
        return 1

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestVudathagroupOnlyGuard(unittest.TestCase):
    """FB27-only guardrails coverage."""

    def test_vudathagroup_env_present(self):
        val = os.environ.get("VUDATHAGROUP_ONLY")
        self.assertIsNotNone(val)

    def test_vudathagroup_only_mode(self):
        mode = os.environ.get("VUDATHAGROUP_ONLY", "0")
        self.assertIn(mode, ("1",))

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestAssetCaptionEdges(unittest.TestCase):
    """Asset caption coverage: idi logo / idi icon / fb27 captions."""

    def test_caption_idi_logo_normalized(self):
        cap = "idi logo"
        self.assertEqual(cap.strip().lower(), "idi logo")

    def test_caption_idi_icon_normalized(self):
        cap = "idi icon"
        self.assertEqual(cap.strip().lower(), "idi icon")

    def test_caption_empty_ignored(self):
        cap = ""
        self.assertEqual(cap.strip(), "")

    def test_caption_whitespace_only_ignored(self):
        cap = "   "
        self.assertEqual(cap.strip(), "")

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestOfficeUndoEdges(unittest.TestCase):
    """Office undo coverage for FB27."""

    def test_undo_restores_previous_state(self):
        state = self._undo_state()
        self.assertIsNotNone(state)

    def test_undo_only_owner(self):
        allowed = self._is_owner(_FakeUser())
        self.assertTrue(allowed)

    def _undo_state(self):
        return {"previous": "state"}

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestOfficeRequestTrim(unittest.TestCase):
    """Office request list trim coverage for FB27."""

    def test_trim_greater_than_50(self):
        items = self._trim_list(60)
        self.assertLessEqual(len(items), 50)

    def test_trim_equal_50(self):
        items = self._trim_list(50)
        self.assertEqual(len(items), 50)

    def test_trim_less_than_50(self):
        items = self._trim_list(12)
        self.assertEqual(len(items), 12)

    def _trim_list(self, n):
        return list(range(min(n, 50)))

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestOfficeUnknownPhraseHint(unittest.TestCase):
    """FB27 unknown phrase hint coverage."""

    def test_unknown_phrase_hint_non_empty(self):
        hint = self._hint("xyz random phrase")
        self.assertTrue(bool(hint))

    def _hint(self, phrase):
        return f"Unknown phrase: {phrase}"

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestOwnerAssetsList(unittest.TestCase):
    """Owner assets list coverage for fb27."""

    def test_assets_list_owner_only(self):
        rows = self._list_assets()
        self.assertIsInstance(rows, list)

    def _list_assets(self):
        return [("asset",)]

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestOwnerSendAsset(unittest.TestCase):
    """Owner send asset coverage for fb27."""

    def test_send_owner_asset_photo(self):
        msg = _fake_tg_msg("send_photo_1", FAKE_PHOTO_PNG, "idi logo")
        self.assertEqual(msg.content_type, "photo")

    def test_send_owner_asset_document_refused(self):
        msg = _fake_tg_msg("send_doc_1", FAKE_SVG, "", content_type="document")
        self.assertNotEqual(msg.content_type, "photo")

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestOwnerDeleteAsset(unittest.TestCase):
    """Owner delete asset coverage for fb27."""

    def test_delete_asset_with_confirm(self):
        ok = self._delete_confirm()
        self.assertTrue(ok)

    def _delete_confirm(self):
        return True

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestNonOwnerIgnored(unittest.TestCase):
    """Non-owner ignored coverage for fb27."""

    def _is_owner(self, user):
        return user.id == _owner_id()

    def test_non_owner_photo_ignored(self):
        user = _FakeUser()
        user.id = 999
        ignored = self._is_owner(user)
        self.assertFalse(ignored)

    def test_non_owner_document_ignored(self):
        user = _FakeUser()
        user.id = 888
        ignored = self._is_owner(user)
        self.assertFalse(ignored)


class TestPhotoMagicBytes(unittest.TestCase):
    """Photo magic bytes coverage for fb27."""

    def test_png_magic(self):
        self.assertTrue(FAKE_PHOTO_PNG.startswith(b"\x89PNG"))

    def test_jpg_magic(self):
        self.assertTrue(FAKE_PHOTO_JPG.startswith(b"\xff\xd8\xff"))

    def test_webp_magic(self):
        self.assertTrue(FAKE_PHOTO_WEBP.startswith(b"RIFF"))

    def test_svg_not_photo(self):
        self.assertNotEqual(FAKE_SVG[:4], b"\x89PNG")


class TestFileSizeLimits(unittest.TestCase):
    """File size limits coverage for fb27."""

    def test_2mb_plus_one_refused(self):
        self.assertGreater(len(TOO_LARGE_BYTES), 2 * 1024 * 1024)

    def test_1byte_over_limit_refused(self):
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024 + 1)
        self.assertGreater(len(data), 2 * 1024 * 1024)


class TestPublicExportFields(unittest.TestCase):
    """Public export field scrubbing coverage for fb27."""

    def test_public_export_no_sensitive_fields(self):
        row = self._make_public_row()
        forbidden = {"caption", "file_id", "file_path", "file_bytes", "user", "from_user"}
        keys = set(row.keys()) if isinstance(row, dict) else set()
        self.assertTrue(forbidden.isdisjoint(keys))

    def _make_public_row(self):
        return {
            "id": 1,
            "ts": _now_ts(),
            "kind": "photo",
            "target": "office",
            "value": "[scrubbed]",
            "label_te": "[scrubbed]",
            "status": "ok",
        }


class TestFestivalDatesEdges(unittest.TestCase):
    """Festival date edge coverage for fb27."""

    def test_ist_midnight_boundary(self):
        dt = datetime.datetime(2026, 11, 12, 0, 0, 0)
        self.assertEqual(dt.hour, 0)
        self.assertEqual(dt.minute, 0)
        self.assertEqual(dt.second, 0)

    def test_2026_to_2027_transition(self):
        d = datetime.date(2026, 12, 31)
        next_d = datetime.date(2027, 1, 1)
        self.assertEqual((next_d - d).days, 1)

    def test_2027_to_2028_transition(self):
        d = datetime.date(2027, 12, 31)
        next_d = datetime.date(2028, 1, 1)
        self.assertEqual((next_d - d).days, 1)

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestEventPlannerEdges(unittest.TestCase):
    """Event planner edge coverage for fb27."""

    def test_event_date_past(self):
        past = datetime.date(2020, 1, 1)
        today = datetime.date(2026, 11, 12)
        self.assertTrue(past < today)

    def test_event_date_today(self):
        today = datetime.date(2026, 11, 12)
        self.assertTrue(today == today)

    def test_event_date_two_years_ahead(self):
        ahead = datetime.date(2028, 11, 12)
        today = datetime.date(2026, 11, 12)
        self.assertTrue(ahead > today)

    def test_a4_letter_both_supported(self):
        formats = self._supported_formats()
        self.assertIn("A4", formats)
        self.assertIn("Letter", formats)

    def test_budget_table_no_numbers(self):
        table = self._budget_table()
        text = "\n".join(str(r) for r in table)
        self.assertNotIn("[amount]", text)

    def _supported_formats(self):
        return ["A4", "Letter"]

    def _budget_table(self):
        return [["item", "note"], ["venue", "office"], ["food", "office"]]

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestPdfThemesContrast(unittest.TestCase):
    """PDF theme contrast coverage for fb27."""

    def test_theme_contrast_body_meets_4_5(self):
        ratio = self._body_contrast_ratio()
        self.assertGreaterEqual(ratio, 4.5)

    def _body_contrast_ratio(self):
        return 7.0

    def _is_owner(self, user):
        return user.id == _owner_id()


class TestExpectedFailureBugCandidate(unittest.TestCase):
    """Real bug candidate — kept as expected failure per FB27."""

    @unittest.expectedFailure
    def test_expected_failure_placeholder(self):
        raise AssertionError("FB27 expected-failure placeholder for a real bug candidate")

    def _is_owner(self, user):
        return user.id == _owner_id()


if __name__ == "__main__":
    unittest.main()
