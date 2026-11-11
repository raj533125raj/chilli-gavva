"""seasonal_ideas.py — Telugu Festival & Seasonal Product Idea Generator.

Generates high-conviction product idea seeds for upcoming festivals and seasons
(default: next 45 days) that the Chilli Gavva product builder can build.

Key Features:
- Inspects upcoming festivals, holidays, and seasons from `festival_calendar`.
- Produces structured candidate seeds:
    * `title`: specific digital product title
    * `niche`: domain / theme (e.g. festive, academic, finance, family)
    * `format`: builder format ID ('planner', 'sheets', 'notion', 'resume', 'dated_planner')
    * `format_label`: friendly format display label (e.g. 'printable planner', 'tracker sheet', 'workbook', 'checklist')
    * `audience`: target buyer group (e.g. Telugu-speaking families, students, teachers, freelancers)
    * `why`: rationale grounded in seasonal search demand and urgency
    * `festival_id`: associated event identifier
    * `festival_name`: display name of the festival or season
    * `days_until`: days until festival/season starts
- Strict Feasibility Filter:
    * Every seed is passed through `format_registry.can_build()` before returning.
    * Only formats currently buildable by registered/built-in builders are yielded.
- Compliance:
    * Zero currency references or money figures.
    * Tenglish house style for reasons and summaries.
    * Deterministic outputs.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from typing import Any, Dict, List, Optional, Union

import festival_calendar
import format_registry

# ---------------------------------------------------------------------------
# Seed Blueprint Templates per Festival / Season
# Format IDs match format_registry registered format identifiers:
# - 'planner' (covers printable planner, workbook, checklist, journal)
# - 'sheets' (covers tracker sheet, spreadsheet, expense tracker)
# - 'dated_planner' (covers calendar planner, dated calendar)
# ---------------------------------------------------------------------------

IDEA_BLUEPRINTS: Dict[str, List[Dict[str, Any]]] = {
    "bhogi": [
        {
            "title": "Bhogi Festive Home Declutter & Cleaning Checklist",
            "niche": "Home & Lifestyle",
            "format": "planner",
            "format_label": "checklist",
            "audience": "Telugu-speaking families",
            "why": "Bhogi tradition focuses on clearing old clutter; families seek organized room-by-room cleaning checklists.",
        },
        {
            "title": "Bhogi Mantalu & Family Gathering Planner",
            "niche": "Festive Planning",
            "format": "planner",
            "format_label": "printable planner",
            "audience": "Telugu-speaking families",
            "why": "Early morning bonfire coordination and festive family breakfast schedules create high printable demand.",
        },
    ],
    "sankranti": [
        {
            "title": "Sankranti Muggulu & Traditional Recipe Planner",
            "niche": "Cultural Traditions",
            "format": "planner",
            "format_label": "printable planner",
            "audience": "Telugu-speaking families",
            "why": "3-day harvest festival demands dot-grid muggulu layouts, sweet recipe guides (ariselu, chakkara pongali), and guest lists.",
        },
        {
            "title": "Festive Sweet Order & Delivery Tracker Sheet",
            "niche": "Small Business",
            "format": "sheets",
            "format_label": "tracker sheet",
            "audience": "home bakers",
            "why": "Home bakers experience massive festive sweet orders and require simple tracking spreadsheets.",
        },
        {
            "title": "Kite Festival & Family Fun Activity Checklist",
            "niche": "Family Activities",
            "format": "planner",
            "format_label": "checklist",
            "audience": "new parents",
            "why": "Parents organizing rooftop kite flying gatherings and kid activities look for quick printable checklists.",
        },
    ],
    "kanuma": [
        {
            "title": "Kanuma Village Harvest & Livestock Care Checklist",
            "niche": "Traditional Living",
            "format": "planner",
            "format_label": "checklist",
            "audience": "Telugu-speaking families",
            "why": "Celebration of farm animals and traditional village festivities benefits from structured event checklists.",
        },
    ],
    "ugadi": [
        {
            "title": "Ugadi Telugu New Year Life Reset & Goal Workbook",
            "niche": "Personal Growth",
            "format": "planner",
            "format_label": "workbook",
            "audience": "Telugu-speaking families",
            "why": "Ugadi marks the fresh start of Telugu calendar; buyers reflect on life goals inspired by the shadruchulu philosophy.",
        },
        {
            "title": "Ugadi Panchangam Reading & Annual Habit Tracker",
            "niche": "Productivity",
            "format": "sheets",
            "format_label": "tracker sheet",
            "audience": "freelancers",
            "why": "New Year enthusiasm creates peak search interest for annual habit tracking and personal routine setup.",
        },
        {
            "title": "Ugadi Festive Lunch & Puja Preparation Checklist",
            "niche": "Festive Planning",
            "format": "planner",
            "format_label": "checklist",
            "audience": "Telugu-speaking families",
            "why": "Preparing Ugadi pachadi and festive sadhyam requires a stress-free ingredient shopping and cooking checklist.",
        },
    ],
    "sri_rama_navami": [
        {
            "title": "Sri Rama Navami Kalyanotsavam & Panakam Service Checklist",
            "niche": "Community & Devotion",
            "format": "planner",
            "format_label": "checklist",
            "audience": "Telugu-speaking families",
            "why": "Community organizers and households preparing panakam, vadapappu, and puja samagri need structured checklists.",
        },
        {
            "title": "Sri Rama Navami Bhajan & Sloka Tracker Workbook",
            "niche": "Spiritual Growth",
            "format": "planner",
            "format_label": "workbook",
            "audience": "retirees",
            "why": "Senior citizens and families practicing Navami parayana seek readable sloka trackers and devotional logs.",
        },
    ],
    "vinayaka_chavithi": [
        {
            "title": "Vinayaka Chavithi 21 Patri & Puja Samagri Checklist",
            "niche": "Festive Planning",
            "format": "planner",
            "format_label": "checklist",
            "audience": "Telugu-speaking families",
            "why": "Finding all 21 leaves (ekavimsati patri) and puja samagri is a major festive pain point resolved by a clear checklist.",
        },
        {
            "title": "Ganesh Utsav Pandal Event & Seva Volunteer Tracker",
            "niche": "Community Management",
            "format": "sheets",
            "format_label": "tracker sheet",
            "audience": "small shop owners",
            "why": "Neighborhood residential associations coordinate pandal schedules, prasadam distribution, and volunteer tasks.",
        },
        {
            "title": "Eco Friendly Ganesha Craft & Modak Recipe Planner",
            "niche": "Creative & Parenting",
            "format": "planner",
            "format_label": "printable planner",
            "audience": "new parents",
            "why": "Parents making clay Ganesha and festive sweets with children need creative activity printables.",
        },
    ],
    "bathukamma": [
        {
            "title": "Bathukamma 9-Day Floral Festival Song & Routine Guide",
            "niche": "Cultural Heritage",
            "format": "planner",
            "format_label": "printable planner",
            "audience": "Telugu-speaking families",
            "why": "Telangana's 9-day floral celebrations require daily flower gathering, song lyrics, and saddula preparations.",
        },
        {
            "title": "Bathukamma Gathering & Sare Distribution Checklist",
            "niche": "Community Events",
            "format": "planner",
            "format_label": "checklist",
            "audience": "Telugu-speaking families",
            "why": "Women organizing colony gatherings and gifting appreciate a structured daily preparation checklist.",
        },
    ],
    "dasara": [
        {
            "title": "Navaratri 9-Day Puja & Alankaram Tracker",
            "niche": "Spiritual Planning",
            "format": "planner",
            "format_label": "printable planner",
            "audience": "Telugu-speaking families",
            "why": "Devotees tracking daily Devi alankarams, naivedyams, and kumkumarchana rely on clean printable logs.",
        },
        {
            "title": "Ayudha Puja & Small Business Equipment Care Checklist",
            "niche": "Small Business",
            "format": "planner",
            "format_label": "checklist",
            "audience": "small shop owners",
            "why": "Shop owners and vehicle owners perform Ayudha Puja cleaning and maintenance on this auspicious day.",
        },
        {
            "title": "Vijaya Dasami New Venture Launch & Action Workbook",
            "niche": "Career & Enterprise",
            "format": "planner",
            "format_label": "workbook",
            "audience": "freelancers",
            "why": "Vijaya Dasami is traditionally chosen to start new learning or business ventures, driving workbook demand.",
        },
    ],
    "deepavali": [
        {
            "title": "Deepavali Complete Home Deep-Clean & Glow Checklist",
            "niche": "Home Organization",
            "format": "planner",
            "format_label": "checklist",
            "audience": "Telugu-speaking families",
            "why": "Pre-Diwali home cleaning and organization is an annual ritual creating immense printable checklist demand.",
        },
        {
            "title": "Diwali Lakshmi Puja & Sweet Box Gifting Planner",
            "niche": "Festive Planning",
            "format": "planner",
            "format_label": "printable planner",
            "audience": "Telugu-speaking families",
            "why": "Coordinating Lakshmi puja samagri, sweet distribution to neighbors, and diya arrangements needs planning.",
        },
        {
            "title": "Festive Season Hamper & Order Log Spreadsheet",
            "niche": "Small Business",
            "format": "sheets",
            "format_label": "tracker sheet",
            "audience": "home bakers",
            "why": "Peak annual confectionery and gift hamper season demands order and delivery spreadsheet trackers.",
        },
    ],
    "karthika_masam": [
        {
            "title": "Karthika Masam Somavaram Deepam & Fasting Journal",
            "niche": "Spiritual Discipline",
            "format": "planner",
            "format_label": "printable planner",
            "audience": "Telugu-speaking families",
            "why": "Month-long Karthika fasting, daily 365 vathula deepam, and Shiva temple visits call for a serene printable journal.",
        },
        {
            "title": "Vanabhojanalu Picnic & Family Gathering Checklist",
            "niche": "Family & Community",
            "format": "planner",
            "format_label": "checklist",
            "audience": "Telugu-speaking families",
            "why": "Community garden picnics and usiri vruksha puja require extensive food and transport planning.",
        },
    ],
    "christmas": [
        {
            "title": "Christmas Holiday Gift & Greeting Card Tracker",
            "niche": "Holiday Planning",
            "format": "sheets",
            "format_label": "tracker sheet",
            "audience": "Telugu-speaking families",
            "why": "Winter holiday gift planning and greeting cards require tidy recipient and shipping tracking.",
        },
        {
            "title": "Christmas Baking & Festive Dinner Party Planner",
            "niche": "Entertaining",
            "format": "planner",
            "format_label": "printable planner",
            "audience": "home bakers",
            "why": "Menu planning, grocery lists, and holiday baking schedules create high seasonal interest.",
        },
    ],
    "new_year": [
        {
            "title": "New Year Life Reset & Habit Builder Workbook",
            "niche": "Productivity & Habits",
            "format": "planner",
            "format_label": "workbook",
            "audience": "fitness beginners",
            "why": "January fresh start mindset drives worldwide demand for structured habit trackers and goal setting workbooks.",
        },
        {
            "title": "Daily Routine & Focus Planner Printable",
            "niche": "Time Management",
            "format": "planner",
            "format_label": "printable planner",
            "audience": "students",
            "why": "Students and professionals seeking organized daily time blocking start the year with printable planners.",
        },
        {
            "title": "Annual Milestone & Reading Challenge Tracker Sheet",
            "niche": "Personal Enrichment",
            "format": "sheets",
            "format_label": "tracker sheet",
            "audience": "freelancers",
            "why": "Reading challenges and annual personal goals are easiest to sustain with clean spreadsheets.",
        },
    ],
    "exam_season_india": [
        {
            "title": "Board Exam 60-Day Subject Revision Timetable",
            "niche": "Academic Study",
            "format": "sheets",
            "format_label": "tracker sheet",
            "audience": "exam takers",
            "why": "CBSE/SSC board exams require structured daily chapter breakdown and revision logs.",
        },
        {
            "title": "Active Recall & Formula Mastery Study Workbook",
            "niche": "Exam Preparation",
            "format": "planner",
            "format_label": "workbook",
            "audience": "students",
            "why": "Science and mathematics students need active recall prompts and formula review worksheets.",
        },
        {
            "title": "Exam Day Hall Ticket & Stationery Checklist",
            "niche": "Student Essentials",
            "format": "planner",
            "format_label": "checklist",
            "audience": "exam takers",
            "why": "Prevents exam morning panic by verifying hall ticket, permitted pens, calculator, and timing.",
        },
    ],
    "exam_season_us": [
        {
            "title": "College Finals Week Pomodoro Revision Planner",
            "niche": "Higher Education",
            "format": "planner",
            "format_label": "printable planner",
            "audience": "students",
            "why": "University students balancing multiple course finals need daily block scheduling and pomodoro logs.",
        },
        {
            "title": "AP Exam Syllabus Coverage & Practice Test Tracker",
            "niche": "Academic Focus",
            "format": "sheets",
            "format_label": "tracker sheet",
            "audience": "exam takers",
            "why": "High school students tracking scores across practice tests benefit from visual mastery spreadsheets.",
        },
    ],
    "back_to_school_india": [
        {
            "title": "Teacher Weekly Lesson Plan & Homework Log",
            "niche": "Teaching Resources",
            "format": "planner",
            "format_label": "printable planner",
            "audience": "teachers",
            "why": "School reopening in June brings high demand from educators for weekly lesson and homework logs.",
        },
        {
            "title": "School Reopening Uniform & Stationery Checklist",
            "niche": "School Readiness",
            "format": "planner",
            "format_label": "checklist",
            "audience": "new parents",
            "why": "Parents preparing children for the new academic term need comprehensive purchase and label checklists.",
        },
        {
            "title": "Student Weekly Timetable & Assignment Tracker Sheet",
            "niche": "Academic Routine",
            "format": "sheets",
            "format_label": "tracker sheet",
            "audience": "students",
            "why": "Managing newly announced class periods and homework assignments smoothly from week one.",
        },
    ],
    "back_to_school_us": [
        {
            "title": "Homeschool Curriculum & Daily Attendance Planner",
            "niche": "Homeschooling",
            "format": "planner",
            "format_label": "printable planner",
            "audience": "teachers",
            "why": "Fall reopening inspires homeschooling parents and educators to set up structured daily binders.",
        },
        {
            "title": "College Dorm Essentials & Class Syllabus Checklist",
            "niche": "Student Life",
            "format": "planner",
            "format_label": "checklist",
            "audience": "students",
            "why": "Freshmen moving to campus need detailed packing and first-week orientation checklists.",
        },
    ],
    "tax_season_us": [
        {
            "title": "Freelancer 1099 Expense & Mileage Tracker Sheet",
            "niche": "Small Business Finance",
            "format": "sheets",
            "format_label": "tracker sheet",
            "audience": "freelancers",
            "why": "Self-employed professionals need clear category-by-category expense and mileage records before filing.",
        },
        {
            "title": "US Tax Document Readiness & Receipt Checklist",
            "niche": "Financial Organization",
            "format": "planner",
            "format_label": "checklist",
            "audience": "freelancers",
            "why": "W2, 1099, deductible receipts, and bank statement verification checklist avoids last-minute rush.",
        },
    ],
    "tax_season_india": [
        {
            "title": "Indian Salaried ITR Deduction & Investment Checklist",
            "niche": "Personal Finance",
            "format": "planner",
            "format_label": "checklist",
            "audience": "job seekers",
            "why": "Section 80C, 80D, HRA slips, and Form 16 verification checklist ensures smooth July filing.",
        },
        {
            "title": "Small Business Professional Receipt & Income Tracker Sheet",
            "niche": "Small Business",
            "format": "sheets",
            "format_label": "tracker sheet",
            "audience": "small shop owners",
            "why": "Small traders and consultants prepare gross turnover and business expense worksheets for tax filing.",
        },
    ],
}


def get_seasonal_ideas(
    now: Optional[Union[datetime.date, datetime.datetime]] = None,
    days: int = 45,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Generate high-conviction product idea seeds for festivals and seasons in the next `days`.

    Each idea seed is guaranteed to pass `format_registry.can_build()`.

    Returns a list of dictionaries with:
      - title: Product name/concept
      - niche: Market niche
      - format: Registered format identifier (e.g. 'planner', 'sheets')
      - format_label: Display label (e.g. 'printable planner', 'tracker sheet')
      - audience: Intended audience
      - why: Tenglish-friendly rationale
      - festival_id: Triggering festival or season ID
      - festival_name: Display name
      - days_until: Days until event begins
      - buildable: Always True
    """
    events = festival_calendar.upcoming(now=now, days=days)
    seeds: List[Dict[str, Any]] = []

    seen_titles = set()

    for ev in events:
        fid = ev["id"]
        blueprints = IDEA_BLUEPRINTS.get(fid, [])

        for bp in blueprints:
            title = bp["title"]
            if title in seen_titles:
                continue

            candidate = {
                "title": title,
                "niche": bp["niche"],
                "format": bp["format"],
                "format_label": bp.get("format_label", bp["format"]),
                "audience": bp["audience"],
                "why": bp["why"],
                "festival_id": fid,
                "festival_name": ev["name"],
                "days_until": ev["days_until_start"],
            }

            # Strictly test feasibility using format_registry.can_build
            if format_registry.can_build(candidate):
                candidate["buildable"] = True
                seeds.append(candidate)
                seen_titles.add(title)

    if limit is not None and limit > 0:
        return seeds[:limit]
    return seeds


def summary_te(now: Optional[Union[datetime.date, datetime.datetime]] = None, days: int = 45) -> str:
    """Return a brief owner-facing Tenglish summary of upcoming seasonal ideas."""
    ideas = get_seasonal_ideas(now=now, days=days)
    if not ideas:
        return f"Next {days} days lo active festivals leka seasons levu; evergreen products focus cheyochu."

    events = festival_calendar.upcoming(now=now, days=days)
    ev_names = ", ".join(dict.fromkeys(e["name"] for e in events))

    lines = [
        f"Next {days} days lo {len(events)} events unnayi ({ev_names}).",
        f"Motham {len(ideas)} buildable seasonal product seeds ready ga unnai:",
    ]
    for i, idea in enumerate(ideas[:5], 1):
        lines.append(f"{i}. [{idea['format_label']}] {idea['title']} ({idea['audience']})")

    if len(ideas) > 5:
        lines.append(f"... mariyu inka {len(ideas) - 5} ideas available.")

    return "\n".join(lines)


def main():
    """CLI helper to inspect upcoming festivals and seasonal ideas."""
    parser = argparse.ArgumentParser(description="Generate seasonal product idea seeds.")
    parser.add_argument("--days", type=int, default=45, help="Lookahead window in days (default: 45)")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of returned seeds")
    args = parser.parse_args()

    ideas = get_seasonal_ideas(days=args.days, limit=args.limit)

    if args.json:
        print(json.dumps(ideas, indent=2, ensure_ascii=False))
    else:
        print(summary_te(days=args.days))
        print("\nAll Ideas:")
        for idx, item in enumerate(ideas, 1):
            print(f"{idx}. {item['title']}")
            print(f"   Festival: {item['festival_name']} (starts in {item['days_until']} days)")
            print(f"   Format: {item['format']} ({item['format_label']}) | Audience: {item['audience']} | Niche: {item['niche']}")
            print(f"   Why: {item['why']}\n")


if __name__ == "__main__":
    main()
