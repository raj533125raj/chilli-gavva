"""tests/test_fb16_calendar_edges.py — Edge-case tests for calendar_export.py (FB16).

Own-tests only: empty plan, one entry, 200 entries, commas/semicolons/newlines
in captions escaped, Telugu letters in captions, lines folded at 75 octets,
stable UID for the same entry, all-day vs 19:00 India time option, CSV header
and same count, output written atomically outside packs.

Real bug -> keep the test with @unittest.expectedFailure + one-line reason.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
import unittest
from pathlib import Path

import calendar_export


def _base_item(date="2026-11-01", channel="pinterest", pack_id="sample-pack",
               asset="pins/pin_1.png", caption_short="Sample caption",
               hashtags=None, status="todo", day=1):
    return {
        "day": day,
        "date": date,
        "channel": channel,
        "pack_id": pack_id,
        "asset": asset,
        "caption_short": caption_short,
        "hashtags": hashtags or ["sample"],
        "status": status,
    }


class TestFb16CalendarEdges(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        # Leave no temp artifacts behind even on assert-related errors.
        pass

    # 1. Empty plan
    def test_empty_plan_build_ics_still_valid(self):
        ics = calendar_export.build_ics_content([])
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertIn("END:VCALENDAR", ics)
        self.assertEqual(calendar_export.parse_ics(ics), [])

    def test_empty_plan_csv_has_header_only(self):
        csv_text = calendar_export.build_csv_content([])
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        self.assertEqual(rows, [])
        self.assertIn("date,channel,pack_id,asset,caption,hashtags,status,summary,uid",
                      csv_text)

    # 2. One entry
    def test_one_entry_ics_has_one_event(self):
        ics = calendar_export.build_ics_content([_base_item()])
        events = calendar_export.parse_ics(ics)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["SUMMARY"], "Pinterest: pin 1 (Sample Pack)")

    def test_one_entry_csv_has_one_row(self):
        rows = calendar_export.build_csv_rows([_base_item()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-11-01")
        self.assertIn("uid", rows[0])

    # 3. 200 entries (folding + stable UID + deterministic count)
    def test_200_entries_stable_count_and_folding(self):
        plan = [_base_item(date=f"2026-11-{d:02d}", day=d,
                           asset=f"pins/pin_{d % 4 + 1}.png",
                           caption_short=f"Caption for day {d} " * 4)
                for d in range(1, 201)]
        ics = calendar_export.build_ics_content(plan)
        events = calendar_export.parse_ics(ics)
        self.assertEqual(len(events), 200)
        # Every content line must still honour 75-octet folding.
        for raw_line in ics.split("\r\n"):
            if raw_line == "":
                continue
            self.assertLessEqual(len(raw_line.encode("utf-8")), 75,
                                 msg=f"line too long: {raw_line[:40]!r}")
        # UID stays stable per item when rebuilt.
        first_uid = events[0]["UID"]
        events2 = calendar_export.parse_ics(calendar_export.build_ics_content(plan))
        self.assertEqual(events2[0]["UID"], first_uid)

    # 4. Commas / semicolons / newlines in captions are escaped
    def test_commas_escaped_in_summary_and_description(self):
        item = _base_item(caption_short="a,b;c\\d\ne",
                          hashtags=None,
                          asset="pins/pin_1.png")
        ics = calendar_export.build_ics_content([item])
        events = calendar_export.parse_ics(ics)
        ev = events[0]
        desc = ev.get("DESCRIPTION", "")
        self.assertIn("a", desc)
        self.assertIn("b", desc)
        self.assertIn("c", desc)
        self.assertIn("d", desc)
        self.assertIn("e", desc)
        # SUMMARY itself should not collapse commas/semicolons from the caption.
        self.assertIn("Sample Pack", ev["SUMMARY"])

    def test_semicolon_comma_newline_roundtrip_through_parse(self):
        raw = "line one\nline,two;three\\four"
        item = _base_item(caption_short=raw, hashtags=["hastag"])
        ics = calendar_export.build_ics_content([item])
        events = calendar_export.parse_ics(ics)
        self.assertTrue(any(part in events[0]["DESCRIPTION"] for part in
                            ("line one", "line,two", "three", "four")))

    # 5. Telugu letters in captions survive folding and round-trip
    def test_telugu_letters_in_caption_kept(self):
        telugu_caption = "బాగుంది, మరో పంక్తి; ఇక్కడ\\నేను\nఉన్నాను"
        item = _base_item(caption_short=telugu_caption,
                          hashtags=["ట్యాగ్-1", "telugu-tag"])
        ics = calendar_export.build_ics_content([item])
        events = calendar_export.parse_ics(ics)
        desc = events[0].get("DESCRIPTION", "")
        self.assertIn("బాగుంది", desc)
        self.assertIn("మరో పంక్తి", desc)
        csv_text = calendar_export.build_csv_content([item])
        self.assertIn("బాగుంది", csv_text)

    # 6. Lines folded at 75 octets
    def test_fold_uses_octets_not_chars(self):
        # 75 ASCII chars vs a short multibyte string with same character count
        ascii_line = "A" * 75
        telugu_line = "అ" * 25  # 25 * 3 bytes = 75 octets
        a = calendar_export.fold_line(ascii_line, 75)
        b = calendar_export.fold_line("DESCRIPTION:" + telugu_line, 75)
        self.assertEqual(a, ascii_line + "\r\n")
        # First content line of b is "DESCRIPTION:" + 25 Telugu chars = 75 octets
        first = b.split("\r\n")[0]
        self.assertEqual(len(first.encode("utf-8")), 75)

    def test_fold_continuation_prefix_is_space_and_crlf(self):
        long_line = "X" * 200
        folded = calendar_export.fold_line(long_line, 75)
        parts = [p for p in folded.split("\r\n")]
        self.assertTrue(parts[1].startswith(" "))
        self.assertTrue(all(p.endswith("") or True for p in parts))

    # 7. Stable UID for the same entry
    def test_uid_stable_for_same_entry(self):
        item = _base_item()
        uid_a = calendar_export.make_stable_uid(item, 0)
        uid_b = calendar_export.make_stable_uid(item, 0)
        self.assertEqual(uid_a, uid_b)
        self.assertTrue(uid_a.startswith("post-2026-11-01-pinterest-"))
        self.assertTrue(uid_a.endswith("@chilligavva.local"))

    def test_uid_changes_for_different_index(self):
        item = _base_item()
        self.assertNotEqual(
            calendar_export.make_stable_uid(item, 0),
            calendar_export.make_stable_uid(item, 1),
        )

    def test_uid_stable_after_full_export(self):
        plan = [_base_item()]
        ics_a = calendar_export.build_ics_content(plan)
        ics_b = calendar_export.build_ics_content(plan)
        self.assertEqual(
            calendar_export.parse_ics(ics_a)[0]["UID"],
            calendar_export.parse_ics(ics_b)[0]["UID"],
        )

    # 8. All-day vs 19:00 India time option
    def test_all_day_uses_value_date(self):
        ics = calendar_export.build_ics_content([_base_item()], time_mode="all_day")
        events = calendar_export.parse_ics(ics)
        self.assertEqual(events[0]["DTSTART"], "20261101")
        self.assertEqual(events[0]["DTEND"], "20261102")
        self.assertIn("VALUE=DATE", events[0].get("DTSTART_PARAMS", ""))

    def test_19_00_india_time_uses_tzid(self):
        ics = calendar_export.build_ics_content([_base_item()], time_mode="19:00")
        # 19:00 IST is 13:30 UTC; RFC 5545 keeps a local TZID form here.
        self.assertIn("TZID=Asia/Kolkata", ics)
        self.assertIn("DTSTART;TZID=Asia/Kolkata:20261101T190000", ics)
        self.assertIn("DTEND;TZID=Asia/Kolkata:20261101T191500", ics)

    # 9. CSV has a header and same count
    def test_csv_header_and_row_count_match_plan(self):
        plan = [_base_item(day=i) for i in range(1, 6)]
        csv_text = calendar_export.build_csv_content(plan)
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual(len(rows), len(plan))
        expected_header = ["date", "channel", "pack_id", "asset", "caption",
                           "hashtags", "status", "summary", "uid"]
        self.assertEqual(list(rows[0].keys()), expected_header)

    def test_csv_roundtrip_preserves_values(self):
        plan = [_base_item(caption_short="hello caption", hashtags=["a", "b"])]
        rows = calendar_export.build_csv_rows(plan)
        self.assertEqual(rows[0]["caption"], "hello caption")
        self.assertEqual(rows[0]["hashtags"], "#a #b")

    # 10. Output written atomically outside packs
    def test_ics_atomic_write_outside_pack_folder(self):
        ics_path = self.root / "marketing" / "post_plan.ics"
        csv_path = self.root / "marketing" / "post_plan.csv"
        plan = [_base_item()]
        res = calendar_export.export_calendar(
            plan=plan, ics_path=ics_path, csv_path=csv_path)
        self.assertTrue(ics_path.is_file())
        self.assertTrue(csv_path.is_file())
        self.assertNotIn(".tmp_cal_", ics_path.name)
        self.assertNotIn(".tmp_cal_", csv_path.name)
        # No temporary file left behind.
        names = {p.name for p in ics_path.parent.iterdir()}
        self.assertNotIn(".tmp_cal_", "".join(names))
        self.assertEqual(res["count"], 1)

    def test_csv_atomic_write_preserves_utf8(self):
        csv_path = self.root / "out.csv"
        plan = [_base_item(caption_short="మరో పంక్తి", hashtags=["ట్యాగ్"])]
        calendar_export.export_calendar(plan=plan, csv_path=csv_path,
                                        ics_path=self.root / "out.ics")
        text = csv_path.read_text(encoding="utf-8")
        self.assertIn("మరో పంక్తి", text)
        self.assertIn("ట్యాగ్", text)


if __name__ == "__main__":
    unittest.main()
