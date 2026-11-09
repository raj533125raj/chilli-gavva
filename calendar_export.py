#!/usr/bin/env python3
"""calendar_export.py — Export post plan to RFC 5545 iCalendar (.ics) and CSV files.

BINNY-T4A ROBOT: POST PLAN AS A CALENDAR FILE.
Exports the post plan loaded via `post_planner.load_plan` into:
  - exported_content/marketing/post_plan.ics
  - exported_content/marketing/post_plan.csv

Key specifications:
- RFC 5545 compliant iCalendar:
  * VCALENDAR / VEVENT blocks
  * Stable UID per entry (deterministic hash based on date, channel, pack_id, asset, day index)
  * DTSTART options:
    - All-day event (default or mode='all_day'): VALUE=DATE:YYYYMMDD, DTEND=YYYYMMDD (next day)
    - 19:00 India Standard Time (IST, UTC+05:30): 19:00 IST is 13:30:00Z UTC, or local with TZID=Asia/Kolkata
  * SUMMARY: 'Pinterest: pin 2 (pack short name)' / 'Instagram: reel (pack short name)' / etc.
  * DESCRIPTION: full caption, hashtags, asset, status, formatted cleanly
  * Line folding strictly at 75 octets with CRLF + space (RFC 5545 section 3.1)
  * Proper escaping of commas, semicolons, backslashes, and newlines (\\n, \\,, \\;, \\\\)
- CSV file:
  * Standard CSV columns: date, channel, pack_id, asset, caption, hashtags, status, summary, uid
  * UTF-8 encoded, deterministic row ordering matching the plan
- Atomic writes: temporary file in the destination folder, then os.replace
- No money amounts anywhere (no currency symbols or amounts)
- Deterministic output across runs
- Tenglish house style summary messages for owner reporting
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from PIL import Image  # type: ignore # Extra library in CI
except Exception:  # pragma: no cover
    Image = None

import post_planner

DEFAULT_ICS_PATH = "exported_content/marketing/post_plan.ics"
DEFAULT_CSV_PATH = "exported_content/marketing/post_plan.csv"

# Currency check regex to prevent publishing currency amounts
_CURRENCY_PATTERN = re.compile(
    r"(?:\$|₹|€|£|¥|\b(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?)\s*\d+|\d+\s*(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?))",
    re.IGNORECASE,
)


def _check_no_currency(text: str) -> None:
    """Ensure no money amounts or currency patterns are present."""
    if _CURRENCY_PATTERN.search(text):
        raise ValueError("Money amount or currency symbol detected in calendar export")


def escape_ics_text(text: str) -> str:
    """Escape text for RFC 5545 property values (TEXT data type).

    RFC 5545 section 3.3.11:
    - Backslash (\\) -> \\\\
    - Semicolon (;) -> \\;
    - Comma (,) -> \\,
    - Newline (\\r\\n or \\n) -> \\n
    """
    if not text:
        return ""
    # Normalize newlines
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Backslash first
    text = text.replace("\\", "\\\\")
    # Semicolons
    text = text.replace(";", "\\;")
    # Commas
    text = text.replace(",", "\\,")
    # Newlines
    text = text.replace("\n", "\\n")
    return text


def fold_line(line: str, max_octets: int = 75) -> str:
    """Fold an iCalendar content line so that each line is at most max_octets octets.

    RFC 5545 section 3.1:
    Lines of text SHOULD NOT be longer than 75 octets, excluding the line
    break. Long content lines SHOULD be split into a higher-level syntactically
    meaningful content line and followed by one or more continuation lines.
    A continuation line is preceded by one whitespace character (space or tab).
    Line breaks are CRLF (\\r\\n).
    """
    # Convert string into UTF-8 bytes to correctly measure octets
    encoded = line.encode("utf-8")
    if len(encoded) <= max_octets:
        return line + "\r\n"

    chunks: List[bytes] = []
    # First line can be up to max_octets
    # Subsequent continuation lines include 1 space (0x20), so content is max_octets - 1
    start = 0
    limit = max_octets

    while start < len(encoded):
        end = start + limit
        if end >= len(encoded):
            chunks.append(encoded[start:])
            break

        # UTF-8 multibyte boundary check: do not split inside a multi-byte sequence
        # Continuation bytes have leading bits 10xxxxxx (0x80 to 0xBF)
        while end > start and (encoded[end] & 0xC0) == 0x80:
            end -= 1

        if end == start:  # Fallback safety (should never occur if limit >= 4)
            end = start + limit

        chunks.append(encoded[start:end])
        start = end
        # Continuation lines have 1 space prefix, so remaining capacity is max_octets - 1
        limit = max_octets - 1

    # Reassemble with CRLF and leading space on continuation lines
    result = chunks[0].decode("utf-8") + "\r\n"
    for chunk in chunks[1:]:
        result += " " + chunk.decode("utf-8") + "\r\n"
    return result


def make_stable_uid(item: Dict[str, Any], index: int = 0) -> str:
    """Generate a stable, deterministic UID for a post plan entry."""
    date_str = str(item.get("date", ""))
    channel = str(item.get("channel", ""))
    pack_id = str(item.get("pack_id", ""))
    asset = str(item.get("asset", ""))
    day = str(item.get("day", ""))
    # Hash unique fields to form a stable token
    raw_key = f"{date_str}:{channel}:{pack_id}:{asset}:{day}:{index}"
    h = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
    return f"post-{date_str}-{channel}-{h}@chilligavva.local"


def clean_pack_short_name(pack_id: str) -> str:
    """Convert a pack_id like 'daily-habits-tracker' into a clean short name 'Daily Habits Tracker'."""
    clean = re.sub(r"[-_]+", " ", pack_id).strip()
    # Title-case words nicely
    words = [w.capitalize() for w in clean.split()]
    return " ".join(words) if words else "Pack"


def build_summary(item: Dict[str, Any]) -> str:
    """Format SUMMARY: 'Channel: pin X (pack short name)' / 'Instagram: reel (pack short name)'."""
    channel = str(item.get("channel", "post")).strip()
    pack_id = str(item.get("pack_id", "")).strip()
    asset = str(item.get("asset", "")).strip()
    short_pack = clean_pack_short_name(pack_id)

    # Format channel name nicely
    ch_display = channel.capitalize()
    if channel.lower() == "pinterest":
        ch_display = "Pinterest"
    elif channel.lower() in ("instagram", "ig"):
        ch_display = "Instagram"
    elif channel.lower() == "reel":
        ch_display = "Instagram Reel"
    elif channel.lower() == "carousel":
        ch_display = "Instagram Carousel"

    # Identify pin number or asset type
    detail = ""
    # Check if asset matches pin pattern (e.g. pin_1_a.png or pin-2 or pins/pin_1.png)
    pin_match = re.search(r"pin[_-]?(\d+)", asset, re.IGNORECASE)
    if pin_match:
        pin_num = pin_match.group(1)
        detail = f"pin {pin_num}"
    elif "reel" in channel.lower() or "reel" in asset.lower():
        detail = "reel"
    elif "carousel" in channel.lower() or "carousel" in asset.lower():
        detail = "carousel"
    elif "cover" in asset.lower():
        detail = "cover"
    elif "preview" in asset.lower():
        prev_match = re.search(r"preview[_-]?(\d+)", asset, re.IGNORECASE)
        if prev_match:
            detail = f"preview {prev_match.group(1)}"
        else:
            detail = "preview"
    elif channel.lower() == "pinterest":
        detail = "pin"
    else:
        detail = "post"

    if short_pack:
        summary = f"{ch_display}: {detail} ({short_pack})"
    else:
        summary = f"{ch_display}: {detail}"

    _check_no_currency(summary)
    return summary


def build_description(item: Dict[str, Any]) -> str:
    """Format DESCRIPTION containing caption, hashtags, asset, status."""
    lines: List[str] = []
    caption = str(item.get("caption_short", "")).strip()
    if caption:
        lines.append(f"Caption: {caption}")

    hashtags = item.get("hashtags")
    if isinstance(hashtags, list) and hashtags:
        tags_str = " ".join(f"#{t.lstrip('#')}" for t in hashtags if t)
        if tags_str:
            lines.append(f"Hashtags: {tags_str}")
    elif isinstance(hashtags, str) and hashtags.strip():
        lines.append(f"Hashtags: {hashtags.strip()}")

    asset = str(item.get("asset", "")).strip()
    if asset:
        lines.append(f"Asset: {asset}")

    pack_id = str(item.get("pack_id", "")).strip()
    if pack_id:
        lines.append(f"Pack: {pack_id}")

    status = str(item.get("status", "todo")).strip()
    if status:
        lines.append(f"Status: {status}")

    desc = "\n".join(lines)
    _check_no_currency(desc)
    return desc


def build_ics_content(
    plan: List[Dict[str, Any]],
    time_mode: str = "all_day",
    cal_name: str = "Chilli Gavva Post Plan",
) -> str:
    """Build RFC 5545 compliant iCalendar string from post plan.

    Args:
        plan: list of post items (day, date, channel, pack_id, asset, caption_short, hashtags, status)
        time_mode: 'all_day' (default) or '19:00' / 'ist'
        cal_name: X-WR-CALNAME property
    """
    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Chilli Gavva//Robot Post Planner 1.0//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{cal_name}",
        "X-WR-TIMEZONE:Asia/Kolkata",
    ]

    # DTSTAMP for deterministic output: 20261101T000000Z or standard timestamp
    # Using fixed timestamp ensures identical output for identical plans
    dtstamp = "20261101T000000Z"

    # Pre-calculate time offsets if mode is 19:00 IST
    # 19:00 IST is UTC+05:30 -> 13:30:00 UTC (13:30:00Z)
    is_time_19 = time_mode in ("19:00", "ist", "19", "time")

    # If there are multiple posts on the same date, stagger them slightly in 19:00 mode
    # (e.g. 19:00, 19:15, 19:30, 19:45 IST) or keep base 19:00 IST
    date_post_counter: Dict[str, int] = {}

    for idx, item in enumerate(plan):
        date_str = str(item.get("date", "")).strip()
        if not date_str:
            continue
        try:
            d = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue

        uid = item.get("uid") or make_stable_uid(item, idx)
        summary = item.get("summary") or build_summary(item)
        description = build_description(item)

        event_lines: List[str] = [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
        ]

        if is_time_19:
            # 19:00 India Standard Time (IST is UTC+5:30)
            # Offset per post on that day (0m, 15m, 30m, 45m)
            post_idx = date_post_counter.get(date_str, 0)
            date_post_counter[date_str] = post_idx + 1

            start_hour = 19
            start_minute = (post_idx * 15) % 60
            start_hour += (post_idx * 15) // 60
            end_minute = start_minute + 15
            end_hour = start_hour
            if end_minute >= 60:
                end_hour += end_minute // 60
                end_minute = end_minute % 60

            # DTSTART with TZID=Asia/Kolkata
            event_lines.append(f"DTSTART;TZID=Asia/Kolkata:{d.strftime('%Y%m%d')}T{start_hour:02d}{start_minute:02d}00")
            event_lines.append(f"DTEND;TZID=Asia/Kolkata:{d.strftime('%Y%m%d')}T{end_hour:02d}{end_minute:02d}00")
        else:
            # All-day event: DTSTART;VALUE=DATE:YYYYMMDD, DTEND=next day
            next_d = d + datetime.timedelta(days=1)
            event_lines.append(f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}")
            event_lines.append(f"DTEND;VALUE=DATE:{next_d.strftime('%Y%m%d')}")

        event_lines.append(f"SUMMARY:{escape_ics_text(summary)}")
        if description:
            event_lines.append(f"DESCRIPTION:{escape_ics_text(description)}")

        status = str(item.get("status", "todo")).lower()
        if status in ("done", "posted"):
            event_lines.append("STATUS:CONFIRMED")
        else:
            event_lines.append("STATUS:TENTATIVE")

        event_lines.append("END:VEVENT")
        lines.extend(event_lines)

    lines.append("END:VCALENDAR")

    # Apply line folding to every content line
    folded_lines = [fold_line(line) for line in lines]
    return "".join(folded_lines)


def build_csv_rows(plan: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Build structured rows for CSV export."""
    rows: List[Dict[str, str]] = []
    for idx, item in enumerate(plan):
        date_str = str(item.get("date", "")).strip()
        channel = str(item.get("channel", "")).strip()
        pack_id = str(item.get("pack_id", "")).strip()
        asset = str(item.get("asset", "")).strip()
        caption = str(item.get("caption_short", "")).strip()
        hashtags = item.get("hashtags", [])
        if isinstance(hashtags, list):
            ht_str = " ".join(f"#{t.lstrip('#')}" for t in hashtags if t)
        else:
            ht_str = str(hashtags).strip()
        status = str(item.get("status", "todo")).strip()
        summary = item.get("summary") or build_summary(item)
        uid = item.get("uid") or make_stable_uid(item, idx)

        row = {
            "date": date_str,
            "channel": channel,
            "pack_id": pack_id,
            "asset": asset,
            "caption": caption,
            "hashtags": ht_str,
            "status": status,
            "summary": summary,
            "uid": uid,
        }
        for val in row.values():
            _check_no_currency(val)
        rows.append(row)
    return rows


def build_csv_content(plan: List[Dict[str, Any]]) -> str:
    """Build CSV string from post plan."""
    fieldnames = ["date", "channel", "pack_id", "asset", "caption", "hashtags", "status", "summary", "uid"]
    rows = build_csv_rows(plan)
    import io
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return output.getvalue()


def _atomic_write_text(filepath: Union[str, Path], content: str) -> None:
    """Write text content atomically using a temporary file in the destination folder."""
    path = Path(filepath).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8", prefix=".tmp_cal_") as tf:
            temp_file = tf.name
            tf.write(content)
        os.replace(temp_file, path)
    except Exception:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass
        raise


def export_calendar(
    plan: Optional[List[Dict[str, Any]]] = None,
    plan_path: Union[str, Path] = post_planner.PLAN_PATH,
    ics_path: Union[str, Path] = DEFAULT_ICS_PATH,
    csv_path: Union[str, Path] = DEFAULT_CSV_PATH,
    time_mode: str = "all_day",
) -> Dict[str, Any]:
    """Export the post plan to .ics and .csv files.

    Returns dict with summary info and Tenglish status message.
    """
    if plan is None:
        plan = post_planner.load_plan(plan_path)

    if not isinstance(plan, list):
        plan = []

    ics_content = build_ics_content(plan, time_mode=time_mode)
    csv_content = build_csv_content(plan)

    _atomic_write_text(ics_path, ics_content)
    _atomic_write_text(csv_path, csv_content)

    count = len(plan)
    # Tenglish summary: Telugu sentence words + English nouns
    tenglish_msg = f"Post plan calendar export ready: {count} posts ics mariyu csv formats lo save ayyayi."

    return {
        "count": count,
        "ics_path": str(ics_path),
        "csv_path": str(csv_path),
        "time_mode": time_mode,
        "tenglish_summary": tenglish_msg,
    }


def parse_ics(ics_text: str) -> List[Dict[str, str]]:
    """Parse an RFC 5545 iCalendar string back into event dictionaries for validation."""
    # Unfold lines: CRLF or LF followed by a single space or tab is merged
    # Replace CRLF with LF first
    text = ics_text.replace("\r\n", "\n").replace("\r", "\n")
    unfolded_lines: List[str] = []
    for line in text.splitlines():
        if line.startswith(" ") or line.startswith("\t"):
            if unfolded_lines:
                unfolded_lines[-1] += line[1:]
            else:
                unfolded_lines.append(line[1:])
        else:
            unfolded_lines.append(line)

    events: List[Dict[str, str]] = []
    in_event = False
    current_event: Dict[str, str] = {}

    for line in unfolded_lines:
        line = line.strip()
        if not line:
            continue
        if line == "BEGIN:VEVENT":
            in_event = True
            current_event = {}
            continue
        if line == "END:VEVENT":
            if in_event:
                events.append(current_event)
                current_event = {}
            in_event = False
            continue

        if in_event and ":" in line:
            prop_part, value = line.split(":", 1)
            prop_name = prop_part.split(";")[0].upper()
            # Unescape value: \\n -> \n, \, -> ,, \; -> ;, \\ -> \
            # Note unescape order: \\\\ must be unescaped carefully
            unescaped = value.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
            current_event[prop_name] = unescaped
            if ";" in prop_part:
                current_event[f"{prop_name}_PARAMS"] = prop_part.split(";", 1)[1]

    return events


def parse_csv(csv_text: str) -> List[Dict[str, str]]:
    """Parse exported CSV text back into rows for validation."""
    import io
    reader = csv.DictReader(io.StringIO(csv_text))
    return list(reader)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for post plan calendar export."""
    parser = argparse.ArgumentParser(
        description="Export post plan to RFC 5545 iCalendar (.ics) and CSV files."
    )
    parser.add_argument(
        "--plan-path",
        default=str(post_planner.PLAN_PATH),
        help=f"Path to input post_plan.json (default: {post_planner.PLAN_PATH})",
    )
    parser.add_argument(
        "--ics-out",
        default=DEFAULT_ICS_PATH,
        help=f"Path to output .ics file (default: {DEFAULT_ICS_PATH})",
    )
    parser.add_argument(
        "--csv-out",
        default=DEFAULT_CSV_PATH,
        help=f"Path to output .csv file (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--time",
        dest="time_mode",
        choices=["all_day", "19:00", "ist"],
        default="all_day",
        help="Event timing: all_day or 19:00 India time (default: all_day)",
    )

    args = parser.parse_args(argv)

    try:
        res = export_calendar(
            plan_path=args.plan_path,
            ics_path=args.ics_out,
            csv_path=args.csv_out,
            time_mode=args.time_mode,
        )
        print(res["tenglish_summary"])
        print(f"ICS: {res['ics_path']}")
        print(f"CSV: {res['csv_path']}")
        return 0
    except Exception as exc:
        print(f"Error exporting calendar: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
