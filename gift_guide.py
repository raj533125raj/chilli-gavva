#!/usr/bin/env python3
"""gift_guide.py — Seasonal collections / gift guides from tags and keywords.

Generates seasonal collection guides for 6 defined seasons:
  - Diwali
  - New Year
  - Back to school
  - Exam season
  - Wedding season
  - Summer holidays

Each season has:
  - A fixed date window in a data table (e.g. month-day start to end)
  - Curated keyword and tag triggers
  - Title and curated intro of 80–120 words
  - 13 clean, relevant tags

Outputs:
  exported_content/marketing/collections/<season_slug>.md

Content rules:
  - Writes only for packs that really match above a scoring threshold (score >= 2).
  - Lists short names of matching ready packs.
  - Exactly 13 tags per guide.
  - Word count of intro strictly between 80 and 120 words.
  - No money amounts ($ / USD / INR etc.) or brand names.
  - Nothing auto-posts or publishes.
  - Atomic writes, deterministic output.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

import listing_fields

# Money patterns
MONEY_PATTERNS = listing_fields.MONEY_PATTERNS

# Disallowed brand words
BRAND_WORDS = re.compile(
    r"\b(canva|etsy|gumroad|payhip|notion|goodnotes|excel|microsoft|google|amazon|ipad|apple)\b",
    re.IGNORECASE,
)

# Seasonal Definitions Table
# Each season has:
#   name: Display name
#   slug: File name slug (e.g., diwali -> diwali.md)
#   start_month_day: (month, day)
#   end_month_day: (month, day)
#   keywords: set of trigger keywords/tags (lowercased)
#   intro: Carefully tuned intro text having 80 to 120 words without money/brands
SEASONS: Dict[str, Dict[str, Any]] = {
    "diwali": {
        "name": "Diwali Celebration & Festive Home Collection",
        "slug": "diwali",
        "start_date": (10, 1),
        "end_date": (11, 20),
        "keywords": {
            "diwali", "deepavali", "festival", "festive", "puja", "pooja",
            "celebration", "rangoli", "mithai", "gift", "gifting", "family",
            "lights", "autumn", "holiday", "tradition", "indian", "prayer",
            "feast", "gathering", "decor", "home decor", "cleaning", "blessing"
        },
        "intro": (
            "The festive season of lights brings warmth, joyful family gatherings, "
            "and mindful reflection into our homes. Preparing for festivals requires "
            "thoughtful organization, from festive meal preparations and sweet sharing "
            "to home cleaning routines and welcoming loved ones. Our festive collection "
            "brings together intentional printable trackers, festive planning templates, "
            "and celebration checklists designed to bring effortless calm to your holiday "
            "preparations. Whether you are coordinating traditional puja ceremonies, "
            "organizing family gift exchanges, or keeping daily festive tasks completely "
            "streamlined, these thoughtfully crafted printables help you stay present "
            "and savor every joyous moment with your cherished family members."
        ),
        "tags": [
            "diwali planner", "festive printables", "holiday checklist", "festival organization",
            "family celebration", "diwali tracker", "puja planner", "home organization",
            "festive gifts", "tradition journal", "seasonal planner", "gathering checklist", "celebration guide"
        ],
    },
    "new_year": {
        "name": "New Year Goals & Fresh Start Collection",
        "slug": "new-year",
        "start_date": (12, 1),
        "end_date": (1, 31),
        "keywords": {
            "new year", "goals", "resolutions", "habits", "habit", "fresh start",
            "january", "annual", "planner", "routine", "productivity", "mindset",
            "vision", "tracking", "review", "weekly", "daily", "focus", "discipline",
            "growth", "self care", "wellness", "intentions", "reflection"
        },
        "intro": (
            "Stepping into a fresh calendar year presents the perfect opportunity to reflect, "
            "reset, and gently realign your daily habits with your biggest aspirations. "
            "Sustainable personal growth does not require overwhelming overhauls, but rather "
            "consistent daily routines, mindful tracking, and clear intentional priorities. "
            "This curated New Year collection gathers our most effective goal-setting workbooks, "
            "minimalist habit trackers, and structured planning templates to support your journey. "
            "Designed to encourage gradual progress and daily clarity, each printable provides "
            "the quiet focus needed to build lasting momentum without burnout. Transform your "
            "annual resolutions into meaningful everyday practices starting right from day one."
        ),
        "tags": [
            "new year goals", "resolution planner", "habit tracker", "goal setting",
            "fresh start printable", "annual review", "daily routine", "productivity planner",
            "mindful habits", "life reset", "focus journal", "yearly planning", "self improvement"
        ],
    },
    "back_to_school": {
        "name": "Back to School & Academic Essentials Collection",
        "slug": "back-to-school",
        "start_date": (6, 1),
        "end_date": (8, 31),
        "keywords": {
            "school", "back to school", "academic", "student", "college", "semester",
            "class", "homework", "study", "teacher", "syllabus", "grades",
            "curriculum", "campus", "education", "lesson", "backpack", "coursework",
            "timetable", "schedule", "subject", "assignment", "kindergarten"
        },
        "intro": (
            "Returning to school or university marks the start of an exciting academic "
            "chapter filled with new subjects, fresh routines, and meaningful milestones. "
            "Staying on top of class timetables, daily assignments, reading schedules, and "
            "extracurricular commitments is much smoother with well-structured organization. "
            "Our back to school collection offers purposeful printable student planners, "
            "comprehensive homework logs, and subject organizers tailored for academic success. "
            "Each layout is cleanly formatted to reduce academic overwhelm and keep students "
            "calmly ahead of deadlines. Equip your study space with practical tools that build "
            "strong study habits, disciplined focus, and balanced productivity throughout the school year."
        ),
        "tags": [
            "back to school", "student planner", "academic tracker", "homework log",
            "study schedule", "college essentials", "class organization", "lesson planner",
            "printable syllabus", "subject organizer", "semester planner", "school routine", "study habits"
        ],
    },
    "exam_season": {
        "name": "Exam Season Revision & Focus Collection",
        "slug": "exam-season",
        "start_date": (2, 1),
        "end_date": (4, 30),
        "keywords": {
            "exam", "exams", "revision", "test", "finals", "midterms", "study",
            "flashcards", "memorization", "pomodoro", "focus", "deep work",
            "preparation", "test prep", "papers", "marks", "mock test", "syllabus tracker",
            "subject review", "cheat sheet", "formula", "cramming", "summary"
        },
        "intro": (
            "Approaching examination periods can feel demanding, but a clear revision strategy "
            "transforms anxiety into structured confidence and peak academic performance. "
            "Effective exam preparation depends on breaking comprehensive syllabi into manageable "
            "daily review targets, active recall sessions, and timed practice tests. This specialized "
            "exam collection features focused revision timetables, topic mastery trackers, and "
            "pomodoro study logs designed for deep uninterrupted work. By organizing revision blocks "
            "and monitoring subject confidence visually, students can optimize retention and maintain "
            "healthy study boundaries. Step into your exam hall fully prepared, grounded, and confident "
            "in your thorough preparation."
        ),
        "tags": [
            "exam revision", "study tracker", "revision timetable", "finals prep",
            "exam planner", "study session", "active recall", "syllabus checklist",
            "test prep printable", "pomodoro tracker", "academic focus", "study routine", "revision tracker"
        ],
    },
    "wedding_season": {
        "name": "Wedding Season Planning & Celebration Collection",
        "slug": "wedding-season",
        "start_date": (11, 1),
        "end_date": (2, 28),
        "keywords": {
            "wedding", "bridal", "bride", "groom", "ceremony", "reception",
            "guest list", "invitation", "venue", "catering", "vows", "trousseau",
            "sangeet", "mehendi", "engagement", "honeymoon", "celebration", "marriage",
            "wedding party", "seating", "timeline", "event planning", "anniversary"
        },
        "intro": (
            "Planning a wedding celebration is an unforgettable milestone rich with joyful "
            "anticipation, cherished traditions, and intricate logistical details. From finalizing "
            "guest lists and coordinating event vendors to curating memorable ceremonies and reception "
            "timelines, organized planning makes all the difference. Our bridal and wedding collection "
            "features elegant printable wedding binders, ceremony checklists, and vendor coordination "
            "worksheets. Created to ease wedding planning stress, these gentle guides allow couples "
            "and families to navigate every stage with complete confidence and grace. Keep every detail "
            "perfectly coordinated so you can relax and truly celebrate your love story with family."
        ),
        "tags": [
            "wedding planner", "bridal checklist", "wedding organization", "ceremony timeline",
            "guest list tracker", "wedding binder", "reception planner", "event coordination",
            "bridal printable", "wedding countdown", "marriage prep", "vendor checklist", "celebration planner"
        ],
    },
    "summer_holidays": {
        "name": "Summer Holidays & Travel Adventure Collection",
        "slug": "summer-holidays",
        "start_date": (4, 15),
        "end_date": (7, 15),
        "keywords": {
            "summer", "holiday", "holidays", "travel", "vacation", "trip", "packing",
            "itinerary", "camp", "outdoor", "adventure", "sunshine", "kids activities",
            "road trip", "beach", "getaway", "bucket list", "luggage", "leisure",
            "relaxation", "family travel", "summer camp", "wanderlust"
        },
        "intro": (
            "Sunny summer months inspire a welcome pause from routine, inviting outdoor adventures, "
            "family road trips, and refreshing vacation travel. Thorough travel preparation turns "
            "journey planning into a seamless delight, ensuring packing lists, daily itineraries, "
            "and activity schedules remain stress-free. Our summer holiday collection gathers comprehensive "
            "travel organizers, vacation packing checklists, and fun summer activity planners for all ages. "
            "Whether you are planning a relaxing coastal getaway, an adventurous mountain expedition, "
            "or playful warm-weather activities at home, these printables keep your travel plans effortless. "
            "Spend less time worrying over logistics and more time creating unforgettable sunny summer memories."
        ),
        "tags": [
            "summer planner", "travel checklist", "vacation organizer", "packing list",
            "holiday printable", "summer bucket list", "trip itinerary", "family travel",
            "adventure journal", "kids summer", "summer activities", "travel planner", "holiday schedule"
        ],
    },
}


def sanitize_text(text: str) -> str:
    """Ensure no money amounts or brand names appear in text."""
    for pat in MONEY_PATTERNS:
        text = pat.sub("", text)
    text = BRAND_WORDS.sub("", text)
    return text


def word_count(text: str) -> int:
    """Return word count of text."""
    return len(re.findall(r"\b\w+\b", text))


def extract_pack_tokens(pack_dir: Path) -> Tuple[str, Set[str]]:
    """Extract short name and a set of lowercased tokens from pack listing and title."""
    listing_path = pack_dir / "listing.txt"
    title = pack_dir.name
    tags_text = ""
    desc_text = ""

    if listing_path.is_file():
        text = listing_path.read_text(encoding="utf-8")
        title_val = listing_fields._section(text, "Title")
        if title_val:
            title = title_val
        tags_text = listing_fields._section(text, "Tags")
        desc_text = listing_fields._section(text, "Description")

    # Clean short name (<= 40 chars, neat)
    short_name = listing_fields._cut_title(title, 40)

    # Tokenize title, tags, description, folder name
    combined = f"{title} {tags_text} {desc_text} {pack_dir.name}".lower()
    raw_words = re.findall(r"[a-z0-9]+", combined)
    tokens: Set[str] = set(raw_words)

    # Also include 2-word phrases for matching compound keywords like 'back to school'
    for phrase in ["new year", "back to school", "home decor", "guest list", "summer camp", "road trip", "self care"]:
        if phrase in combined:
            tokens.add(phrase)

    return short_name, tokens


def score_pack_for_season(pack_tokens: Set[str], season_key: str) -> int:
    """Calculate match score for a pack against a season's keywords.

    Each keyword hit adds points. Score threshold is >= 2.
    """
    season_data = SEASONS.get(season_key)
    if not season_data:
        return 0

    keywords = season_data["keywords"]
    score = 0
    for kw in keywords:
        if kw in pack_tokens:
            score += 1
        elif " " in kw and any(kw in t for t in pack_tokens):
            score += 1
    return score


def find_matching_packs(
    ready_packs: List[Path],
    season_key: str,
    threshold: int = 2,
) -> List[Tuple[str, Path, int]]:
    """Return matching packs (short_name, pack_dir, score) with score >= threshold."""
    matches: List[Tuple[str, Path, int]] = []
    for pack in ready_packs:
        short_name, tokens = extract_pack_tokens(pack)
        s = score_pack_for_season(tokens, season_key)
        if s >= threshold:
            matches.append((short_name, pack, s))

    # Sort descending by score, then alphabetical by short name
    matches.sort(key=lambda item: (-item[2], item[0].lower()))
    return matches


def is_in_season_window(season_key: str, target_date: Optional[date] = None) -> bool:
    """Check if target_date falls within the fixed date window for season_key."""
    season_data = SEASONS.get(season_key)
    if not season_data:
        return False
    if target_date is None:
        target_date = date.today()

    start_m, start_d = season_data["start_date"]
    end_m, end_d = season_data["end_date"]

    month = target_date.month
    day = target_date.day

    cur_tuple = (month, day)
    start_tuple = (start_m, start_d)
    end_tuple = (end_m, end_d)

    # Check if window wraps around year end (e.g. Dec to Jan, Nov to Feb)
    if start_tuple <= end_tuple:
        return start_tuple <= cur_tuple <= end_tuple
    else:
        return cur_tuple >= start_tuple or cur_tuple <= end_tuple


def generate_season_collection_markdown(
    season_key: str,
    matching_packs: List[Tuple[str, Path, int]],
) -> str:
    """Build the Markdown document for the seasonal collection."""
    season_data = SEASONS[season_key]
    title = season_data["name"]
    intro = season_data["intro"]
    tags = season_data["tags"][:13]

    # Validate intro word count
    wc = word_count(intro)
    assert 80 <= wc <= 120, f"Intro word count {wc} out of range (80-120 words)"
    assert len(tags) == 13, f"Must have exactly 13 tags, found {len(tags)}"

    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(intro.strip())
    lines.append("")
    lines.append("## Featured Digital Guides & Planners")
    lines.append("")

    for short_name, pack_dir, _score in matching_packs:
        lines.append(f"- **{short_name}** (`{pack_dir.name}`)")

    lines.append("")
    lines.append("## Collection Tags")
    lines.append("")
    lines.append(", ".join(tags))
    lines.append("")

    content = "\n".join(lines)

    # Assert safety: no money
    for pat in MONEY_PATTERNS:
        assert not pat.search(content), "Money amount found in seasonal collection guide"
    assert not BRAND_WORDS.search(content), "Brand name found in seasonal collection guide"

    return content


def write_seasonal_collection(
    season_key: str,
    matching_packs: List[Tuple[str, Path, int]],
    out_dir: Path | str = "exported_content/marketing/collections",
) -> Optional[Path]:
    """Write seasonal guide to out_dir/<season_slug>.md atomically."""
    if not matching_packs:
        return None

    season_data = SEASONS.get(season_key)
    if not season_data:
        return None

    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    slug = season_data["slug"]
    target_file = target_dir / f"{slug}.md"

    content = generate_season_collection_markdown(season_key, matching_packs)

    # Atomic write via temporary file
    fd, tmp_file = tempfile.mkstemp(dir=target_dir, prefix=f".coll_{slug}_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_file, target_file)

    return target_file


def generate_all_seasonal_collections(
    products_dir: Path | str = "exported_content/products",
    out_dir: Path | str = "exported_content/marketing/collections",
    threshold: int = 2,
    only_active_window: bool = False,
    current_date: Optional[date] = None,
) -> Dict[str, Path]:
    """Generate collections for all seasons with matching ready packs."""
    # Discover ready packs
    import launch_checklist
    ready_packs = launch_checklist.discover_ready_packs(products_dir)

    results: Dict[str, Path] = {}
    for season_key in sorted(SEASONS.keys()):
        if only_active_window and not is_in_season_window(season_key, current_date):
            continue

        matches = find_matching_packs(ready_packs, season_key, threshold=threshold)
        if matches:
            out_path = write_seasonal_collection(season_key, matches, out_dir=out_dir)
            if out_path:
                results[season_key] = out_path

    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate seasonal collections / gift guides from pack tags and keywords."
    )
    parser.add_argument(
        "--products-dir",
        default="exported_content/products",
        help="Products directory (defaults to exported_content/products).",
    )
    parser.add_argument(
        "--out-dir",
        default="exported_content/marketing/collections",
        help="Output directory (defaults to exported_content/marketing/collections).",
    )
    parser.add_argument(
        "--season",
        choices=list(SEASONS.keys()),
        help="Generate only for a specific season.",
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Generate only for seasons currently within their active date window.",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=2,
        help="Minimum keyword match score threshold (default 2).",
    )

    args = parser.parse_args(argv)

    import launch_checklist
    ready_packs = launch_checklist.discover_ready_packs(args.products_dir)

    seasons_to_run = [args.season] if args.season else sorted(SEASONS.keys())
    count = 0

    for skey in seasons_to_run:
        if args.active_only and not is_in_season_window(skey):
            continue
        matches = find_matching_packs(ready_packs, skey, threshold=args.threshold)
        if matches:
            p = write_seasonal_collection(skey, matches, out_dir=args.out_dir)
            if p:
                print(f"Generated collection for {skey}: {p} ({len(matches)} pack(s))")
                count += 1
        else:
            print(f"No qualifying packs for season {skey} (threshold >= {args.threshold}).")

    print(f"Completed: generated {count} seasonal collection guide(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
