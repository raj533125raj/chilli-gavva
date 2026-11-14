"""builder_eventplanner.py — Event & Wedding Planner Pack Builder.

FORMAT = 'event_planner'
KEYWORDS = [
    'event planner', 'wedding planner', 'birthday planner', 'gruhapravesam',
    'house warming', 'house warming planner', 'housewarming', 'housewarming planner',
    'festival planner', 'wedding organizer', 'event organizer', 'pelli planner',
    'gruhapravesam checklist', 'event countdown', 'wedding countdown',
    'wedding workbook', 'event workbook', 'event planning bundle'
]

Features:
  - Supports 4 major event types:
      1. 'wedding' (Indian & Global editions)
      2. 'birthday'
      3. 'gruhapravesam' (House-warming)
      4. 'festival'
  - Standard Printable PDF Planner in both A4 (210 x 297 mm) and US Letter (8.5 x 11 in)
  - Pages included:
      * Event Vision & Overview Cover / Details
      * Countdown Checklist (12 months -> 9-6 months -> 3 months -> 1 month -> 2 weeks -> 1 week -> 1 day,
        or auto-calculated milestones from event_date)
      * Guest List with RSVP tracker (Guest name, Party size, RSVP status, Dietary / Notes)
      * Vendor Contacts (clean blank lines only, strictly NO real phone numbers/names)
      * Budget Table WITHOUT amounts (Columns only: Category / Item, Planned, Actual - empty lines, zero money)
      * Seating Chart Grid (Table number, Table shape/name, Capacity, Guest names)
      * Day-of Schedule / Master Timeline (Hour by hour, Activity, Location, Lead person;
        includes Muhurtham time slot field for Indian edition)
      * Wedding Special Pages (for Indian edition: Mehendi ceremony, Haldi ceremony, Sangeet night checklist & timeline)
      * Packing & Essentials Checklist
      * Gift Tracker & Registry Log
      * Thank-You Note Tracker
      * Notes & Vision Board page
  - Covers & Previews:
      * cover.png (1280x720)
      * preview-1.png (1280x720)
      * preview-2.png (1280x720)
      * preview-3.png (1280x720)
  - Comprehensive README.md and LICENSE.txt
  - Strictly NO money amounts, currencies, or automated publishing.
  - Deterministic generation, atomic file writes.
  - Telegram plugin interface (COMMANDS + handle).
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    HAS_PILLOW = False

import pdf_design
import store_kits

# --- Constants & Format Registration ---

FORMAT = "event_planner"
KEYWORDS = [
    "event planner",
    "wedding planner",
    "birthday planner",
    "gruhapravesam",
    "house warming",
    "house warming planner",
    "housewarming",
    "housewarming planner",
    "festival planner",
    "wedding organizer",
    "event organizer",
    "pelli planner",
    "gruhapravesam checklist",
    "event countdown",
    "wedding countdown",
    "wedding workbook",
    "event workbook",
    "event planning bundle",
]

PAGE_SIZES = {
    "A4": (595.28, 841.89),
    "Letter": (612.0, 792.0),
}

EVENT_TYPES = ["wedding", "birthday", "gruhapravesam", "festival"]
EDITIONS = ["indian", "global"]

THEME_PALETTES = {
    "royal_marigold": {
        "name": "Royal Marigold & Crimson",
        "primary": (180, 40, 20),      # Crimson / Kumkum
        "accent": (230, 150, 20),      # Marigold gold
        "bg_soft": (255, 250, 240),    # Warm ivory
        "line": (210, 190, 175),       # Soft border
        "text_dark": (40, 25, 20),     # Deep espresso
        "text_muted": (115, 95, 85),
    },
    "sage_eucalyptus": {
        "name": "Sage & Eucalyptus",
        "primary": (45, 90, 75),       # Deep sage
        "accent": (110, 155, 135),     # Soft eucalyptus
        "bg_soft": (245, 248, 246),    # Pale sage white
        "line": (200, 215, 205),
        "text_dark": (25, 45, 35),
        "text_muted": (85, 110, 95),
    },
    "classic_rose": {
        "name": "Classic Rose & Blush",
        "primary": (145, 45, 75),      # Berry wine
        "accent": (215, 130, 150),     # Dusty rose
        "bg_soft": (254, 247, 249),    # Soft blush
        "line": (225, 200, 210),
        "text_dark": (45, 25, 35),
        "text_muted": (115, 85, 95),
    },
    "festive_indigo": {
        "name": "Festive Indigo & Mustard",
        "primary": (25, 45, 100),      # Deep indigo
        "accent": (210, 160, 30),      # Mustard gold
        "bg_soft": (246, 248, 253),    # Ice white
        "line": (195, 205, 225),
        "text_dark": (20, 25, 45),
        "text_muted": (80, 90, 115),
    },
}

LICENSE_TEXT = (
    "Personal use only. Do not resell, alter, or share these digital files.\n"
    "Copyright (c) Chilli Gavva. All rights reserved.\n"
)


# --- Helper Utilities & Typography ---

def _atomic_write_bytes(filepath: Union[str, Path], data: bytes) -> None:
    """Write bytes to filepath atomically using a temporary file."""
    fpath = Path(filepath).resolve()
    fpath.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(dir=str(fpath.parent), prefix="tmp_ep_", suffix=".tmp")
    try:
        with os.fdopen(temp_fd, "wb") as f:
            f.write(data)
        os.replace(temp_path, str(fpath))
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _atomic_write_text(filepath: Union[str, Path], content: str, encoding: str = "utf-8") -> None:
    """Write text to filepath atomically."""
    _atomic_write_bytes(filepath, content.encode(encoding))


def _rgb_norm(rgb_255: Tuple[int, int, int]) -> Tuple[float, float, float]:
    """Convert 0-255 RGB tuple to 0.0-1.0 float tuple."""
    return (rgb_255[0] / 255.0, rgb_255[1] / 255.0, rgb_255[2] / 255.0)


def normalize_event_spec(spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalizes and validates spec for event planner generation."""
    raw = dict(spec or {})

    # Detect event type
    title_raw = str(raw.get("title") or raw.get("name") or "Ultimate Event & Wedding Planner Pack").strip()
    norm_title = title_raw.lower()

    if any(k in norm_title for k in ["wedding", "pelli", "bride", "groom", "marriage"]):
        event_type = "wedding"
    elif any(k in norm_title for k in ["gruhapravesam", "house warming", "housewarming"]):
        event_type = "gruhapravesam"
    elif any(k in norm_title for k in ["birthday", "bday", "sweet 16"]):
        event_type = "birthday"
    elif any(k in norm_title for k in ["festival", "diwali", "sankranti", "celebration", "puja", "pooja"]):
        event_type = "festival"
    else:
        event_type = str(raw.get("event_type", "wedding")).lower().strip()
        if event_type not in EVENT_TYPES:
            event_type = "wedding"

    # Detect edition (Indian vs Global)
    if "edition" in raw and str(raw["edition"]).lower().strip() in EDITIONS:
        edition = str(raw["edition"]).lower().strip()
    else:
        indian_clues = ["gruhapravesam", "pelli", "muhurtham", "mehendi", "haldi", "sangeet", "diwali", "puja", "pooja", "sankranti", "indian"]
        if any(c in norm_title for c in indian_clues) or event_type in ["gruhapravesam", "festival"]:
            edition = "indian"
        elif event_type == "wedding" and any(c in norm_title for c in ["indian", "desi", "telugu", "traditional"]):
            edition = "indian"
        else:
            edition = "indian" if "indian" in norm_title else "global"

    # Palette
    pal_key = str(raw.get("palette") or ("royal_marigold" if edition == "indian" else "sage_eucalyptus")).strip().lower()
    if pal_key not in THEME_PALETTES:
        pal_key = "royal_marigold" if edition == "indian" else "sage_eucalyptus"

    # Event date
    event_date_str = str(raw.get("event_date") or "").strip()
    parsed_date = None
    if event_date_str:
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                parsed_date = datetime.strptime(event_date_str, fmt).date()
                break
            except ValueError:
                pass

    short_name = str(raw.get("short_name") or f"{event_type}-planner").strip().lower()
    short_name = re.sub(r"[^a-z0-9_-]+", "-", short_name).strip("-")
    if not short_name:
        short_name = f"{event_type}-planner"

    return {
        "title": title_raw,
        "short_name": short_name,
        "event_type": event_type,
        "edition": edition,
        "palette": pal_key,
        "event_date": parsed_date,
        "event_date_str": event_date_str,
        "venue": str(raw.get("venue") or "[VENUE NAME / LOCATION]").strip(),
        "hosts": str(raw.get("hosts") or "[HOST / COUPLE / CELEBRANT]").strip(),
        "custom_notes": str(raw.get("notes") or "").strip(),
    }


def compute_countdown_milestones(event_date: Optional[date] = None) -> List[Dict[str, str]]:
    """Calculates countdown milestones.

    If event_date is provided, associates concrete calendar target dates;
    otherwise provides standard structured timeline windows (12 months -> 1 day).
    """
    milestones = [
        {"window": "12 Months Before", "days": 365, "phase": "Vision, Venue & Core Vendors", "focus": "Set overall vision, book main venue, establish guest count estimate."},
        {"window": "9 to 6 Months Before", "days": 220, "phase": "Key Bookings & Design Theme", "focus": "Book photographer, caterer, decorator; finalize invitations & attire."},
        {"window": "3 Months Before", "days": 90, "phase": "Invitations & Menu Details", "focus": "Send formal invitations with RSVP deadline, confirm menu tastings & decor plans."},
        {"window": "1 Month Before", "days": 30, "phase": "Final RSVPs & Seating Layout", "focus": "Lock final guest count, confirm seating chart, order event day stationery."},
        {"window": "2 Weeks Before", "days": 14, "phase": "Vendor Confirmations & Timeline", "focus": "Circulate master day-of schedule to all vendors, pack specialty items."},
        {"window": "1 Week Before", "days": 7, "phase": "Final Prep & Rehearsals", "focus": "Review emergency kit, confirm arrival times, finalize payments & tips."},
        {"window": "1 Day Before", "days": 1, "phase": "Drop-offs, Rest & Hydration", "focus": "Drop decor & favors at venue, steam outfits, get restful sleep."},
        {"window": "Day of Event", "days": 0, "phase": "Celebration & Coordination", "focus": "Follow master schedule, breathe, smile, celebrate every moment!"},
    ]

    result = []
    for m in milestones:
        item = dict(m)
        if event_date is not None:
            target_dt = event_date - timedelta(days=m["days"])
            item["target_date"] = target_dt.strftime("%B %d, %Y")
        else:
            item["target_date"] = "Target Date: _______________"
        result.append(item)
    return result


# --- PDF Generation Engine (Pure pdf_design Primitives) ---

class EventPlannerPdfBuilder:
    """Renders comprehensive printable PDF planners using pure pdf_design vector primitives."""

    def __init__(self, spec: Dict[str, Any], page_size_name: str = "A4"):
        self.spec = normalize_event_spec(spec)
        self.page_size_name = page_size_name if page_size_name in PAGE_SIZES else "A4"
        self.pdf = pdf_design.Pdf(self.page_size_name)
        self.palette = THEME_PALETTES[self.spec["palette"]]
        self.w, self.h = self.pdf.w, self.pdf.h
        self.margin = 42.0
        self.content_w = self.w - 2 * self.margin

        # Pre-calculate normalized colors for pdf_design
        self.c_primary = _rgb_norm(self.palette["primary"])
        self.c_accent = _rgb_norm(self.palette["accent"])
        self.c_bg_soft = _rgb_norm(self.palette["bg_soft"])
        self.c_line = _rgb_norm(self.palette["line"])
        self.c_dark = _rgb_norm(self.palette["text_dark"])
        self.c_muted = _rgb_norm(self.palette["text_muted"])

    def _draw_page_header(self, title: str, subtitle: str = "", category_badge: str = ""):
        """Standard elegant top header band on every planner page."""
        y_top = self.h - self.margin
        # Background accent bar
        self.pdf.round_rect(self.margin, y_top - 46, self.content_w, 46, 6, fill=self.c_bg_soft, stroke=self.c_line, width=0.8)
        self.pdf.rect(self.margin, y_top - 46, 6, 46, color=self.c_primary)

        # Header titles
        if category_badge:
            self.pdf.text(self.margin + 16, y_top - 15, category_badge.upper(), size=8, bold=True, color=self.c_accent, spacing=1.5)
            self.pdf.text(self.margin + 16, y_top - 34, title, size=15, bold=True, color=self.c_primary)
        else:
            self.pdf.text(self.margin + 16, y_top - 28, title, size=16, bold=True, color=self.c_primary)

        if subtitle:
            self.pdf.text_right(self.w - self.margin - 16, y_top - 28, subtitle, size=9, bold=False, color=self.c_muted)

        # Date & Page rule
        self.pdf.line(self.margin, y_top - 54, self.w - self.margin, y_top - 54, color=self.c_line, width=0.6)
        return y_top - 68

    def _draw_table_header(self, y: float, columns: List[Tuple[str, float]], height: float = 22.0) -> float:
        """Draws filled table column header row."""
        self.pdf.rect(self.margin, y - height, self.content_w, height, color=self.c_primary)
        curr_x = self.margin
        for col_name, col_w in columns:
            self.pdf.text(curr_x + 8, y - height + 6, col_name, size=9, bold=True, color=(1.0, 1.0, 1.0))
            curr_x += col_w
            if curr_x < self.w - self.margin:
                self.pdf.line(curr_x, y - height, curr_x, y, color=(1.0, 1.0, 1.0), width=0.5)
        return y - height

    def _draw_table_row(self, y: float, columns: List[Tuple[str, float]], height: float = 22.0,
                        row_bg: Optional[Tuple[float, float, float]] = None,
                        cell_texts: Optional[List[str]] = None) -> float:
        """Draws a table row with subtle border and optional text."""
        if row_bg:
            self.pdf.rect(self.margin, y - height, self.content_w, height, color=row_bg)
        self.pdf.rect(self.margin, y - height, self.content_w, height, color=self.c_line, width=0.5)

        curr_x = self.margin
        for idx, (_, col_w) in enumerate(columns):
            if cell_texts and idx < len(cell_texts) and cell_texts[idx]:
                self.pdf.text(curr_x + 8, y - height + 6, cell_texts[idx], size=8.5, bold=False, color=self.c_dark)
            curr_x += col_w
            if curr_x < self.w - self.margin:
                self.pdf.line(curr_x, y - height, curr_x, y, color=self.c_line, width=0.5)
        return y - height

    # --- Specific Page Layouts ---

    def render_cover_page(self):
        """Page 1: Elegant front cover with decorative border, title, event details, and contents index."""
        self.pdf.new_page()
        # Double decorative frame
        self.pdf.rect(self.margin - 8, self.margin - 8, self.content_w + 16, self.h - 2 * self.margin + 16, color=self.c_accent, width=1.2)
        self.pdf.rect(self.margin - 4, self.margin - 4, self.content_w + 8, self.h - 2 * self.margin + 8, color=self.c_line, width=0.6)

        # Header emblem & label
        y = self.h - 110
        badge = f"{self.spec['edition'].upper()} EDITION · COMPLETE PLANNER"
        self.pdf.text_center(self.w / 2.0, y, badge, size=10, bold=True, color=self.c_accent, spacing=2.5)

        # Main Title (auto wrapped)
        y -= 38
        title_lines, font_sz = pdf_design.fit_text(self.spec["title"], 26, self.content_w - 40, bold=True, max_lines=2, min_size=18)
        for line in title_lines:
            self.pdf.text_center(self.w / 2.0, y, line, size=font_sz, bold=True, color=self.c_primary)
            y -= (font_sz + 8)

        # Subtitle / Event Type & Edition
        y -= 8
        event_label = self.spec["event_type"].replace("_", " ").title()
        self.pdf.text_center(self.w / 2.0, y, f"Master Organizing Guide & Keepsake Workbook for {event_label}", size=11, bold=False, color=self.c_muted)

        # Decorative ornament line
        y -= 25
        self.pdf.line(self.w / 2.0 - 80, y, self.w / 2.0 + 80, y, color=self.c_accent, width=1.5)
        self.pdf.circle(self.w / 2.0, y, 3, fill=self.c_primary)

        # Event Details Box
        y -= 40
        box_h = 100
        box_w = self.content_w - 60
        box_x = self.margin + 30
        self.pdf.round_rect(box_x, y - box_h, box_w, box_h, 8, fill=self.c_bg_soft, stroke=self.c_line, width=0.8)

        # Fields inside details box
        bx = box_x + 20
        by = y - 26
        dt_str = self.spec["event_date"].strftime("%B %d, %Y") if self.spec["event_date"] else "_________________________"
        self.pdf.text(bx, by, "CELEBRANT / HOSTS:", size=9, bold=True, color=self.c_accent)
        self.pdf.text(bx + 140, by, self.spec["hosts"], size=10, bold=False, color=self.c_dark)

        by -= 26
        self.pdf.text(bx, by, "EVENT DATE:", size=9, bold=True, color=self.c_accent)
        self.pdf.text(bx + 140, by, dt_str, size=10, bold=False, color=self.c_dark)

        by -= 26
        self.pdf.text(bx, by, "PRIMARY VENUE:", size=9, bold=True, color=self.c_accent)
        self.pdf.text(bx + 140, by, self.spec["venue"], size=10, bold=False, color=self.c_dark)

        # What's Inside Index
        y = y - box_h - 35
        self.pdf.text(self.margin + 20, y, "WHAT'S INSIDE THIS PLANNER", size=11, bold=True, color=self.c_primary, spacing=1.0)
        self.pdf.line(self.margin + 20, y - 6, self.w - self.margin - 20, y - 6, color=self.c_line, width=0.6)

        index_sections = [
            ("01. Event Overview & Vision Board", "Key dates, themes & core commitments"),
            ("02. Countdown Checklist (12 Mo → 1 Day)", "Phase-by-phase actionable milestones"),
            ("03. Guest List & RSVP Tracker", "Name, count, RSVP confirmation & dietary"),
            ("04. Vendor Contacts & Services Directory", "Clean blank directories for all event partners"),
            ("05. Master Budget Planning Worksheet", "Itemized categories, planned vs actual (no money)"),
            ("06. Seating Chart & Table Grid Layout", "Table numbers, guest seats & arrangement"),
            ("07. Master Day-of Schedule & Timeline", "Hour-by-hour order of events with lead roles"),
            ("08. Packing & Emergency Essentials", "Checklists for personal, attire & venue kits"),
            ("09. Gift Tracker & Thank-You Log", "Registry record, gift received & thank you sent"),
        ]
        if self.spec["event_type"] == "wedding" and self.spec["edition"] == "indian":
            index_sections.append(("10. Indian Wedding Ceremonies", "Mehendi, Haldi & Sangeet planning worksheets"))

        iy = y - 24
        for title_sec, desc_sec in index_sections[:8]:
            self.pdf.text(self.margin + 24, iy, title_sec, size=8.5, bold=True, color=self.c_dark)
            self.pdf.text(self.margin + 250, iy, desc_sec, size=8, bold=False, color=self.c_muted)
            iy -= 16

        # Footer note
        self.pdf.text_center(self.w / 2.0, self.margin + 14, "Print at 100% scale for binder filing · Personal use only", size=8, color=self.c_muted)

    def render_event_overview(self):
        """Page 2: Vision, Themes, Color Palette, and VIP Key Roles."""
        self.pdf.new_page()
        y = self._draw_page_header("Event Vision & Planning Foundation", "Section 01", "Vision & Setup")

        # 1. Vision & Concept Box
        self.pdf.text(self.margin, y, "1. EVENT CONCEPT & VIBE", size=10, bold=True, color=self.c_primary)
        y -= 14
        self.pdf.round_rect(self.margin, y - 55, self.content_w, 55, 4, fill=self.c_bg_soft, stroke=self.c_line, width=0.5)
        self.pdf.text(self.margin + 12, y - 20, "Theme / Aesthetic: ___________________________________________________________", size=9, color=self.c_muted)
        self.pdf.text(self.margin + 12, y - 42, "Color Palette / Decor: _______________________________________________________", size=9, color=self.c_muted)
        y -= 75

        # 2. Key Details & Milestones Grid (Two Columns)
        col_w = (self.content_w - 20) / 2.0
        # Left column: Venue & Logistics
        self.pdf.text(self.margin, y, "2. VENUE & LOGISTICS", size=10, bold=True, color=self.c_primary)
        # Right column: Key Point of Contacts
        self.pdf.text(self.margin + col_w + 20, y, "3. KEY EVENT LEADS & VIPs", size=10, bold=True, color=self.c_primary)
        y -= 14

        # Draw left and right boxes
        box_h = 140
        self.pdf.round_rect(self.margin, y - box_h, col_w, box_h, 4, stroke=self.c_line, width=0.5)
        self.pdf.round_rect(self.margin + col_w + 20, y - box_h, col_w, box_h, 4, stroke=self.c_line, width=0.5)

        # Left lines
        lx = self.margin + 10
        ly = y - 22
        left_fields = [
            "Venue Access Time: ______________",
            "Curfew / Music End: ____________",
            "Parking / Valet: ________________",
            "Catering Space: _________________",
            "Decor Setup Window: _____________",
        ]
        for lf in left_fields:
            self.pdf.text(lx, ly, lf, size=8.5, color=self.c_dark)
            ly -= 24

        # Right lines
        rx = self.margin + col_w + 30
        ry = y - 22
        right_fields = [
            "Master Coordinator: ____________",
            "Primary Host Lead: _____________",
            "Transport / Logistics: _________",
            "VIP Hospitality Lead: __________",
            "Vendor Liaison: ________________",
        ]
        for rf in right_fields:
            self.pdf.text(rx, ry, rf, size=8.5, color=self.c_dark)
            ry -= 24

        y -= (box_h + 25)

        # 4. Core Goals & Priorities
        self.pdf.text(self.margin, y, "4. CORE CELEBRATION PRIORITIES (Top 3 Must-Haves)", size=10, bold=True, color=self.c_primary)
        y -= 14
        for i in range(1, 4):
            self.pdf.round_rect(self.margin, y - 28, self.content_w, 28, 4, fill=self.c_bg_soft, stroke=self.c_line, width=0.5)
            self.pdf.text(self.margin + 12, y - 18, f"Priority {i:02d}: ____________________________________________________________________", size=9, bold=False, color=self.c_dark)
            y -= 34

        # 5. Planning Notes / House Rules
        y -= 10
        self.pdf.text(self.margin, y, "5. GENERAL NOTES & SPECIAL INSTRUCTIONS", size=10, bold=True, color=self.c_primary)
        y -= 14
        notes_h = y - (self.margin + 20)
        self.pdf.round_rect(self.margin, y - notes_h, self.content_w, notes_h, 4, stroke=self.c_line, width=0.5)
        ny = y - 20
        while ny > self.margin + 30:
            self.pdf.line(self.margin + 10, ny, self.w - self.margin - 10, ny, color=self.c_line, width=0.4)
            ny -= 20

    def render_countdown_checklist(self):
        """Page 3: Countdown Checklist from 12 Months to 1 Day (with concrete dates if event_date set)."""
        self.pdf.new_page()
        sub = "Concrete Milestones" if self.spec["event_date"] else "12 Months to Day-of"
        y = self._draw_page_header("Event Countdown Master Checklist", sub, "Timeline")

        milestones = compute_countdown_milestones(self.spec["event_date"])
        row_h = 42.0

        for m in milestones:
            # Checkbox
            self.pdf.rect(self.margin + 2, y - 24, 12, 12, color=self.c_accent, width=1.0)

            # Window & Phase Title
            self.pdf.text(self.margin + 24, y - 16, m["window"], size=10, bold=True, color=self.c_primary)
            self.pdf.text(self.margin + 180, y - 16, f"·  {m['phase']}", size=9, bold=True, color=self.c_dark)

            # Target date badge on right
            self.pdf.text_right(self.w - self.margin - 10, y - 16, m["target_date"], size=8.5, bold=False, color=self.c_muted)

            # Focus description
            self.pdf.text(self.margin + 24, y - 32, m["focus"], size=8, bold=False, color=self.c_muted)

            # Bottom separator line
            self.pdf.line(self.margin, y - row_h, self.w - self.margin, y - row_h, color=self.c_line, width=0.4)
            y -= (row_h + 8)

    def render_guest_list(self, page_num: int = 1, total_pages: int = 2):
        """Pages 4-5: Guest List & RSVP Tracker with party counts and dietary notes."""
        self.pdf.new_page()
        y = self._draw_page_header(f"Guest List & RSVP Tracker (Sheet {page_num} of {total_pages})", "RSVP Tracker", "Guests")

        # Top summary stats block
        self.pdf.round_rect(self.margin, y - 28, self.content_w, 28, 4, fill=self.c_bg_soft, stroke=self.c_line, width=0.5)
        self.pdf.text(self.margin + 12, y - 18, "TOTAL INVITED: [      ]", size=8.5, bold=True, color=self.c_primary)
        self.pdf.text(self.margin + 140, y - 18, "ACCEPTED (ATTENDING): [      ]", size=8.5, bold=True, color=self.c_primary)
        self.pdf.text(self.margin + 330, y - 18, "DECLINED: [      ]", size=8.5, bold=True, color=self.c_primary)
        self.pdf.text_right(self.w - self.margin - 12, y - 18, "PENDING: [      ]", size=8.5, bold=True, color=self.c_accent)
        y -= 38

        # Columns: # (24), Guest Name (170), Party Size (50), RSVP Status (70), Table # (45), Dietary / Special Notes (remaining)
        rem_w = self.content_w - (24 + 170 + 50 + 70 + 45)
        columns = [
            ("#", 24),
            ("Guest / Family Name", 170),
            ("Party", 50),
            ("RSVP", 70),
            ("Table", 45),
            ("Dietary / Accommodations / Notes", rem_w),
        ]

        y = self._draw_table_header(y, columns, height=20.0)

        # Draw 20 rows per page
        num_rows = 20
        start_idx = (page_num - 1) * num_rows + 1
        for i in range(num_rows):
            row_idx = start_idx + i
            bg = self.c_bg_soft if (i % 2 == 1) else None
            cell_texts = [f"{row_idx:02d}", "", "", "[ ] Yes  [ ] No", "", ""]
            y = self._draw_table_row(y, columns, height=22.0, row_bg=bg, cell_texts=cell_texts)

    def render_vendor_contacts(self):
        """Page 6: Vendor Contacts Directory.

        Strictly blank placeholder lines; absolutely NO real telephone numbers,
        emails, or real company names.
        """
        self.pdf.new_page()
        y = self._draw_page_header("Vendor Contacts & Service Directory", "Service Directory", "Vendors")

        # Header intro note
        self.pdf.text(self.margin, y, "Record all booked service providers, point of contact, and contract status below:", size=8.5, color=self.c_muted)
        y -= 14

        columns = [
            ("Category / Service", 110),
            ("Vendor Company & Contact Person", 160),
            ("Phone / Mobile (Blank)", 100),
            ("Email Address (Blank)", 110),
            ("Status", self.content_w - (110 + 160 + 100 + 110)),
        ]

        y = self._draw_table_header(y, columns, height=20.0)

        # Standard vendor categories
        categories = [
            "Venue / Hall",
            "Caterer & Beverages",
            "Decor & Florals",
            "Photographer",
            "Videographer",
            "Audio / DJ / Music",
            "Attire / Stylist",
            "Hair & Makeup",
            "Invitations & Stationery",
            "Transportation / Valet",
            "Cake / Sweets",
            "Favors & Gifts",
            "Priest / Officiant",
            "Lighting & Sound",
            "Event Day Coordinator",
            "Security / Staffing",
            "Rental Items (Chairs/Tents)",
            "Specialty Entertainment",
        ]

        for idx, cat in enumerate(categories):
            bg = self.c_bg_soft if (idx % 2 == 1) else None
            cell_texts = [cat, "", "", "", "[ ] Booked"]
            y = self._draw_table_row(y, columns, height=23.0, row_bg=bg, cell_texts=cell_texts)

    def render_budget_table(self):
        """Page 7: Master Budget Worksheet.

        Strictly WITHOUT amounts or currency symbols.
        Columns: Category, Item / Service Description, Planned, Actual, Difference / Status.
        Blank lines only for owner's handwritten numbers.
        """
        self.pdf.new_page()
        y = self._draw_page_header("Master Event Budget Worksheet", "Zero-Currency Columns Only", "Budget")

        # Instruction Note emphasizing personal use & manual entry
        self.pdf.round_rect(self.margin, y - 24, self.content_w, 24, 4, fill=self.c_bg_soft, stroke=self.c_line, width=0.5)
        self.pdf.text(self.margin + 10, y - 16, "INSTRUCTION: Enter your own estimated and actual figures manually in your local currency.", size=8, bold=True, color=self.c_primary)
        y -= 34

        col_cat = 120
        col_item = 175
        col_plan = 65
        col_act = 65
        col_diff = self.content_w - (col_cat + col_item + col_plan + col_act)

        columns = [
            ("Category", col_cat),
            ("Item / Description", col_item),
            ("Planned", col_plan),
            ("Actual", col_act),
            ("Notes / Difference", col_diff),
        ]

        y = self._draw_table_header(y, columns, height=20.0)

        budget_items = [
            ("Venue & Grounds", "Primary Venue Rental Fee"),
            ("Venue & Grounds", "Security Deposit & Cleaning"),
            ("Food & Beverage", "Catering Meal Package"),
            ("Food & Beverage", "Beverages & Refreshments"),
            ("Food & Beverage", "Desserts, Cake & Sweets"),
            ("Decor & Ambience", "Stage / Mandap / Backdrop"),
            ("Decor & Ambience", "Floral Arrangements & Centerpieces"),
            ("Decor & Ambience", "Lighting & Audio Ambience"),
            ("Photo & Film", "Lead Photographer Coverage"),
            ("Photo & Film", "Videographer & Highlights Edit"),
            ("Attire & Beauty", "Primary Host Attire & Outfits"),
            ("Attire & Beauty", "Hair, Makeup & Styling"),
            ("Stationery & Favors", "Printed Invitations & Postage"),
            ("Stationery & Favors", "Guest Return Gifts & Favors"),
            ("Logistics & Misc", "Guest Transportation / Shuttle"),
            ("Logistics & Misc", "Priest / Officiant Honorarium"),
            ("Logistics & Misc", "Emergency Day-of Buffer"),
        ]

        for idx, (cat, item) in enumerate(budget_items):
            bg = self.c_bg_soft if (idx % 2 == 1) else None
            cell_texts = [cat, item, "", "", ""]
            y = self._draw_table_row(y, columns, height=21.0, row_bg=bg, cell_texts=cell_texts)

        # Summary Total Rows at bottom (Blank lines, no currency)
        y -= 6
        for label in ["TOTAL PLANNED VS ACTUAL", "NET VARIANCE"]:
            self.pdf.rect(self.margin, y - 20, self.content_w, 20, color=self.c_bg_soft)
            self.pdf.rect(self.margin, y - 20, self.content_w, 20, color=self.c_primary, width=0.6)
            self.pdf.text(self.margin + 12, y - 14, label, size=8.5, bold=True, color=self.c_primary)
            self.pdf.line(self.margin + col_cat + col_item, y - 20, self.margin + col_cat + col_item, y, color=self.c_primary, width=0.5)
            self.pdf.line(self.margin + col_cat + col_item + col_plan, y - 20, self.margin + col_cat + col_item + col_plan, y, color=self.c_primary, width=0.5)
            self.pdf.line(self.margin + col_cat + col_item + col_plan + col_act, y - 20, self.margin + col_cat + col_item + col_plan + col_act, y, color=self.c_primary, width=0.5)
            y -= 22

    def render_seating_chart(self):
        """Page 8: Seating Chart Grid.

        Structured 6-table or 8-table layout grid with table names, capacities, and assigned seats.
        """
        self.pdf.new_page()
        y = self._draw_page_header("Seating Chart & Table Layout Grid", "Table Assignments", "Seating")

        grid_cols = 2
        grid_rows = 3
        gap_x = 16.0
        gap_y = 16.0
        card_w = (self.content_w - (grid_cols - 1) * gap_x) / grid_cols
        card_h = (y - self.margin - 20 - (grid_rows - 1) * gap_y) / grid_rows

        t_idx = 1
        for row in range(grid_rows):
            card_y = y - row * (card_h + gap_y)
            for col in range(grid_cols):
                card_x = self.margin + col * (card_w + gap_x)

                # Card outline
                self.pdf.round_rect(card_x, card_y - card_h, card_w, card_h, 6, fill=self.c_bg_soft, stroke=self.c_line, width=0.6)

                # Card header bar
                self.pdf.round_rect(card_x, card_y - 24, card_w, 24, 6, fill=self.c_primary)
                self.pdf.text(card_x + 10, card_y - 17, f"TABLE {t_idx:02d}", size=9, bold=True, color=(1.0, 1.0, 1.0))
                self.pdf.text_right(card_x + card_w - 10, card_y - 17, "Capacity: [      ]", size=8, bold=False, color=(1.0, 1.0, 1.0))

                # Table shape / name
                self.pdf.text(card_x + 10, card_y - 36, "Theme / Group: __________________________", size=8, color=self.c_muted)

                # 6 guest lines
                line_y = card_y - 52
                for s in range(1, 7):
                    self.pdf.text(card_x + 10, line_y, f"{s}. ______________________________________", size=8, color=self.c_dark)
                    line_y -= 18

                t_idx += 1

    def render_day_of_schedule(self):
        """Page 9: Day-of Schedule & Master Timeline.

        Features:
          - Hour-by-hour order of events
          - Dedicated Muhurtham / Auspicious Time Slot field for Indian edition
          - Columns: Time, Event / Activity, Location, Lead Role / Contact
        """
        self.pdf.new_page()
        sub = "With Muhurtham Slot" if self.spec["edition"] == "indian" else "Hour-by-Hour Timeline"
        y = self._draw_page_header("Day-of Master Schedule & Timeline", sub, "Day-of")

        # Muhurtham / Auspicious Timing Highlight Box (Especially prominent for Indian edition)
        if self.spec["edition"] == "indian":
            self.pdf.round_rect(self.margin, y - 36, self.content_w, 36, 4, fill=self.c_bg_soft, stroke=self.c_accent, width=1.0)
            self.pdf.text(self.margin + 12, y - 16, "★ AUSPICIOUS MUHURTHAM TIME SLOT:", size=9, bold=True, color=self.c_primary)
            self.pdf.text(self.margin + 230, y - 16, "Start: ________ AM/PM   |   End: ________ AM/PM", size=9, bold=True, color=self.c_dark)
            self.pdf.text(self.margin + 12, y - 30, "Primary Priest / Officiant: _____________________________ | Venue Mandap / Altar: _____________________________", size=7.5, color=self.c_muted)
            y -= 46
        else:
            self.pdf.round_rect(self.margin, y - 26, self.content_w, 26, 4, fill=self.c_bg_soft, stroke=self.c_accent, width=0.8)
            self.pdf.text(self.margin + 12, y - 17, "PRIMARY CEREMONY START TIME: _______________ | ARRIVAL CALL TIME: _______________", size=8.5, bold=True, color=self.c_primary)
            y -= 34

        col_time = 75
        col_act = 180
        col_loc = 110
        col_lead = self.content_w - (col_time + col_act + col_loc)

        columns = [
            ("Time Slot", col_time),
            ("Activity / Program Segment", col_act),
            ("Location / Area", col_loc),
            ("Lead Person / Coordinator", col_lead),
        ]

        y = self._draw_table_header(y, columns, height=20.0)

        # Standard schedule slots
        schedule_slots = [
            ("06:00 AM", "Setup crew & decor access opens"),
            ("07:30 AM", "Hair, makeup & attire styling begins"),
            ("09:00 AM", "Vendor deliveries (flowers, stage, AV)"),
            ("10:00 AM", "Photographer arrival & detail shots"),
            ("11:00 AM", "Family portraits & first looks"),
            ("12:00 PM", "Guest arrival, welcome drinks & seating"),
            ("01:00 PM", "Core Ceremony & Rituals start"),
            ("02:30 PM", "Ceremony concludes & congratulations"),
            ("03:00 PM", "Luncheon / Refreshment service begins"),
            ("05:00 PM", "Evening reset / Rest break for VIPs"),
            ("06:30 PM", "Evening reception / Celebrations start"),
            ("07:30 PM", "Speeches, performances & cake cutting"),
            ("08:30 PM", "Dinner banquet served"),
            ("10:00 PM", "Music, dance & open floor"),
            ("11:30 PM", "Grand send-off & venue pack-down"),
        ]

        for idx, (t_slot, act) in enumerate(schedule_slots):
            bg = self.c_bg_soft if (idx % 2 == 1) else None
            cell_texts = [t_slot, act, "", ""]
            y = self._draw_table_row(y, columns, height=21.0, row_bg=bg, cell_texts=cell_texts)

    def render_indian_wedding_ceremonies(self):
        """Page 10: Specific to Indian Weddings (Mehendi, Haldi, Sangeet).

        Provides dedicated planners for the 3 major pre-wedding celebrations.
        """
        self.pdf.new_page()
        y = self._draw_page_header("Indian Wedding Celebrations (Mehendi · Haldi · Sangeet)", "Pre-Wedding Traditions", "Ceremonies")

        rituals = [
            ("MEHENDI CEREMONY", "Henna artists, colorful floral decor, bangle station & folk singing"),
            ("HALDI / MANGALASNANA CEREMONY", "Turmeric paste application, holy bath rituals, yellow attire theme & blessings"),
            ("SANGEET / COCKTAIL NIGHT", "Musical performances, choreographed family dances, DJ night & celebratory feast"),
        ]

        card_h = 175.0
        for title_rit, desc_rit in rituals:
            self.pdf.round_rect(self.margin, y - card_h, self.content_w, card_h, 6, fill=self.c_bg_soft, stroke=self.c_primary, width=0.8)

            # Header bar
            self.pdf.round_rect(self.margin, y - 24, self.content_w, 24, 6, fill=self.c_primary)
            self.pdf.text(self.margin + 12, y - 17, title_rit, size=9.5, bold=True, color=(1.0, 1.0, 1.0))
            self.pdf.text_right(self.w - self.margin - 12, y - 17, desc_rit, size=7.5, bold=False, color=self.c_bg_soft)

            # Left column: Details; Right column: Task checklist
            col_half = (self.content_w - 30) / 2.0
            lx = self.margin + 12
            ly = y - 40
            self.pdf.text(lx, ly, "Date & Muhurtham / Time: _________________________", size=8, color=self.c_dark)
            ly -= 20
            self.pdf.text(lx, ly, "Venue / Location: _______________________________", size=8, color=self.c_dark)
            ly -= 20
            self.pdf.text(lx, ly, "Attire / Color Dress Code: ______________________", size=8, color=self.c_dark)
            ly -= 20
            self.pdf.text(lx, ly, "Lead Family Host / Organizer: ___________________", size=8, color=self.c_dark)
            ly -= 20
            self.pdf.text(lx, ly, "Catering / Special Food Menu: ___________________", size=8, color=self.c_dark)

            # Right column: Tasks
            rx = self.margin + col_half + 25
            ry = y - 40
            tasks = [
                "[ ] Playlist & sound system checked",
                "[ ] Ritual materials / puja samagri ready",
                "[ ] Photographer & video team briefed",
                "[ ] Seating arrangements & cushions placed",
                "[ ] Welcome gifts / favors packed",
            ]
            for t in tasks:
                self.pdf.text(rx, ry, t, size=8, color=self.c_dark)
                ry -= 20

            y -= (card_h + 12)

    def render_packing_list(self):
        """Page 11: Packing List & Emergency Essentials."""
        self.pdf.new_page()
        y = self._draw_page_header("Event Packing List & Emergency Essentials", "Checklist", "Packing")

        col_w = (self.content_w - 16) / 2.0

        categories = [
            ("ATTIRE & WARDROBE", [
                "[ ] Primary ceremony outfits & attire",
                "[ ] Backup footwear & comfortable shoes",
                "[ ] Steamer / wrinkle release spray",
                "[ ] Extra buttons, safety pins & sewing kit",
                "[ ] Jewelry, watches & accessories safely packed",
            ]),
            ("CEREMONY & RITUAL ESSENTIALS", [
                "[ ] Vows, speeches or reading cards",
                "[ ] Marriage license / event permits",
                "[ ] Rings, garlands or ritual tokens",
                "[ ] Guest book & pens",
                "[ ] Signage & directional easels",
            ]),
            ("EMERGENCY & FIRST AID KIT", [
                "[ ] Pain relievers & antacids",
                "[ ] Bandages & blister cushions",
                "[ ] Stain remover pen & tissues",
                "[ ] Breath mints & lip balm",
                "[ ] Phone chargers & power banks",
            ]),
            ("FINANCE & DISPATCH LOGISTICS", [
                "[ ] Vendor tip envelopes (labeled)",
                "[ ] Final payment receipt copies",
                "[ ] Printed vendor master schedule",
                "[ ] Contact list with phone numbers",
                "[ ] Venue access keys & parking passes",
            ]),
        ]

        y_cursor = y
        for row_i in range(2):
            box_h = 240.0
            cat_left = categories[row_i * 2]
            cat_right = categories[row_i * 2 + 1]

            for col_i, (cat_title, cat_items) in enumerate([cat_left, cat_right]):
                box_x = self.margin + col_i * (col_w + 16)
                self.pdf.round_rect(box_x, y_cursor - box_h, col_w, box_h, 6, stroke=self.c_line, width=0.6)

                # Category Header
                self.pdf.round_rect(box_x, y_cursor - 24, col_w, 24, 6, fill=self.c_primary)
                self.pdf.text(box_x + 10, y_cursor - 17, cat_title, size=8.5, bold=True, color=(1.0, 1.0, 1.0))

                item_y = y_cursor - 42
                for itm in cat_items:
                    self.pdf.text(box_x + 10, item_y, itm, size=8, color=self.c_dark)
                    item_y -= 22

                # Extra blank lines
                for _ in range(4):
                    self.pdf.text(box_x + 10, item_y, "[ ] ______________________________________", size=8, color=self.c_muted)
                    item_y -= 22

            y_cursor -= (box_h + 16)

    def render_gift_tracker(self):
        """Page 12: Gift Tracker & Registry Log."""
        self.pdf.new_page()
        y = self._draw_page_header("Gift Tracker & Registry Log", "Record & Acknowledgments", "Gifts")

        columns = [
            ("#", 24),
            ("Gift Giver / Family", 150),
            ("Gift Description / Item", 160),
            ("Event / Occasion", 90),
            ("Thank-You Sent", self.content_w - (24 + 150 + 160 + 90)),
        ]

        y = self._draw_table_header(y, columns, height=20.0)

        for idx in range(1, 23):
            bg = self.c_bg_soft if (idx % 2 == 1) else None
            cell_texts = [f"{idx:02d}", "", "", "", "[ ] Done"]
            y = self._draw_table_row(y, columns, height=22.0, row_bg=bg, cell_texts=cell_texts)

    def render_thank_you_tracker(self):
        """Page 13: Thank-You Note Tracker."""
        self.pdf.new_page()
        y = self._draw_page_header("Thank-You Note Tracker & Delivery Status", "Gratitude Log", "Notes")

        # Instruction bar
        self.pdf.round_rect(self.margin, y - 24, self.content_w, 24, 4, fill=self.c_bg_soft, stroke=self.c_line, width=0.5)
        self.pdf.text(self.margin + 10, y - 16, "Aim to dispatch all handwritten thank-you cards within 2-3 weeks post event.", size=8, bold=True, color=self.c_primary)
        y -= 34

        columns = [
            ("#", 24),
            ("Recipient Name", 140),
            ("Mailing Address / Contact", 170),
            ("Method (Card/Call)", 95),
            ("Date Sent / Status", self.content_w - (24 + 140 + 170 + 95)),
        ]

        y = self._draw_table_header(y, columns, height=20.0)

        for idx in range(1, 22):
            bg = self.c_bg_soft if (idx % 2 == 1) else None
            cell_texts = [f"{idx:02d}", "", "", "Card / Call", "[ ] Sent: _________"]
            y = self._draw_table_row(y, columns, height=22.0, row_bg=bg, cell_texts=cell_texts)

    def render_notes_page(self):
        """Page 14: Lined Notes & Vision Board page."""
        self.pdf.new_page()
        y = self._draw_page_header("Event Planning Notes & Vision Sketches", "Freeform Planning", "Notes")

        # 2 Top boxes: Mood / Sketches (left) and Key Reminders (right)
        box_w = (self.content_w - 16) / 2.0
        box_h = 180.0

        # Left: Sketch area
        self.pdf.round_rect(self.margin, y - box_h, box_w, box_h, 6, fill=self.c_bg_soft, stroke=self.c_line, width=0.6)
        self.pdf.text(self.margin + 12, y - 18, "SKETCH / MOOD BOARD / SWATCHES", size=8.5, bold=True, color=self.c_primary)
        self.pdf.rect(self.margin + 10, y - box_h + 10, box_w - 20, box_h - 36, color=self.c_line, width=0.4)

        # Right: Key Reminders
        rx = self.margin + box_w + 16
        self.pdf.round_rect(rx, y - box_h, box_w, box_h, 6, fill=self.c_bg_soft, stroke=self.c_line, width=0.6)
        self.pdf.text(rx + 12, y - 18, "CRITICAL REMINDERS & TO-DOS", size=8.5, bold=True, color=self.c_primary)

        ry = y - 40
        for _ in range(6):
            self.pdf.text(rx + 12, ry, "[ ] ___________________________________", size=8, color=self.c_dark)
            ry -= 22

        y -= (box_h + 20)

        # Bottom lined notes
        self.pdf.text(self.margin, y, "GENERAL OBSERVATIONS & MEETING MINUTES:", size=8.5, bold=True, color=self.c_primary)
        y -= 14
        while y > self.margin + 20:
            self.pdf.line(self.margin, y, self.w - self.margin, y, color=self.c_line, width=0.5)
            y -= 20

    def build_pdf(self, output_path: Union[str, Path]) -> List[str]:
        """Assembles all required event planner pages into a single PDF document."""
        out_f = Path(output_path).resolve()
        out_f.parent.mkdir(parents=True, exist_ok=True)

        pages_rendered = [
            "Cover Page",
            "Event Vision & Overview",
            "Countdown Checklist",
            "Guest List & RSVP (Sheet 1)",
            "Guest List & RSVP (Sheet 2)",
            "Vendor Contacts Directory",
            "Master Budget Worksheet",
            "Seating Chart & Grid",
            "Day-of Master Schedule",
        ]

        self.render_cover_page()
        self.render_event_overview()
        self.render_countdown_checklist()
        self.render_guest_list(page_num=1, total_pages=2)
        self.render_guest_list(page_num=2, total_pages=2)
        self.render_vendor_contacts()
        self.render_budget_table()
        self.render_seating_chart()
        self.render_day_of_schedule()

        # Indian Wedding Page (Mehendi, Haldi, Sangeet)
        if self.spec["event_type"] == "wedding" and self.spec["edition"] == "indian":
            self.render_indian_wedding_ceremonies()
            pages_rendered.append("Indian Wedding Ceremonies")

        self.render_packing_list()
        pages_rendered.append("Packing & Emergency Kit")

        self.render_gift_tracker()
        pages_rendered.append("Gift Tracker")

        self.render_thank_you_tracker()
        pages_rendered.append("Thank-You Note Tracker")

        self.render_notes_page()
        pages_rendered.append("Notes & Sketches")

        # Save via pdf_design
        meta = {
            "title": self.spec["title"],
            "author": "Chilli Gavva",
            "subject": f"Event & Wedding Planner ({self.spec['edition'].title()} Edition)",
            "keywords": "event planner, wedding planner, printable, rsvp, schedule, checklist",
            "creator": "Chilli Gavva event planner builder",
        }
        self.pdf.save(str(out_f), title=self.spec["title"], meta=meta)
        return pages_rendered


# --- Pillow Listing Previews & Covers ---

def _get_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Safely loads project Inter font or fallback to Pillow default."""
    if not HAS_PILLOW:
        return None
    font_dir = Path(__file__).resolve().parent / "fonts"
    font_file = "Inter-Bold.ttf" if bold else "Inter-Regular.ttf"
    font_path = font_dir / font_file
    if font_path.exists():
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception:
            pass
    return ImageFont.load_default()


def render_cover_image(out_path: Union[str, Path], spec: Dict[str, Any]) -> None:
    """Generates cover.png (1280x720) showcasing the event planner."""
    if not HAS_PILLOW:
        return
    norm = normalize_event_spec(spec)
    pal = THEME_PALETTES[norm["palette"]]
    w, h = 1280, 720
    im = Image.new("RGB", (w, h), color=pal["bg_soft"])
    draw = ImageDraw.Draw(im)

    # Outer border & background cards
    draw.rectangle([20, 20, w - 20, h - 20], outline=pal["line"], width=2)
    draw.rectangle([40, 40, w - 40, h - 40], outline=pal["primary"], width=1)

    # Accent decorative top strip
    draw.rectangle([40, 40, w - 40, 56], fill=pal["primary"])
    draw.rectangle([40, 56, w - 40, 62], fill=pal["accent"])

    # Fonts
    font_badge = _get_font(14, bold=True)
    font_title = _get_font(38, bold=True)
    font_sub = _get_font(20, bold=False)
    font_chip = _get_font(13, bold=True)
    font_card_t = _get_font(15, bold=True)
    font_card_b = _get_font(12, bold=False)

    # Header badge
    badge_text = f"★ PRINTABLE EVENT PLANNER PACK · {norm['edition'].upper()} EDITION ★"
    draw.text((w / 2.0, 95), badge_text, fill=pal["accent"], font=font_badge, anchor="mm")

    # Main Title
    title = store_kits.trim_to_word_boundary(norm["title"], 42)
    draw.text((w / 2.0, 145), title, fill=pal["primary"], font=font_title, anchor="mm")

    sub = f"Organizing System for {norm['event_type'].replace('_', ' ').title()} · A4 & US Letter PDF Formats"
    draw.text((w / 2.0, 190), sub, fill=pal["text_muted"], font=font_sub, anchor="mm")

    # Feature Chips (Middle)
    chips = [
        "12-Mo Countdown",
        "Guest List & RSVP",
        "Vendor Directory",
        "Zero-Currency Budget",
        "Seating Grid",
        "Day-of Schedule",
    ]
    if norm["event_type"] == "wedding" and norm["edition"] == "indian":
        chips.append("Mehendi & Haldi")

    cx = 80
    cy = 230
    for chip in chips:
        text_bbox = draw.textbbox((cx, cy), chip, font=font_chip)
        chip_w = text_bbox[2] - text_bbox[0] + 20
        draw.rounded_rectangle([cx, cy, cx + chip_w, cy + 30], radius=15, fill=pal["primary"])
        draw.text((cx + chip_w / 2.0, cy + 15), chip, fill="#ffffff", font=font_chip, anchor="mm")
        cx += (chip_w + 14)

    # Three Mockup Page Cards (Bottom Preview)
    cards = [
        ("01. Countdown & Day-of Timeline", [
            "• 12 months down to 1 day milestones",
            "• Hour-by-hour day-of master schedule",
            "• Dedicated Muhurtham time slot",
        ]),
        ("02. Guest & Seating Coordination", [
            "• Complete RSVP confirmation tracking",
            "• Dietary requirements & headcount",
            "• 6-table diagram seating grid",
        ]),
        ("03. Budget & Vendor Directory", [
            "• Blank budget columns: Planned & Actual",
            "• Zero money amounts or currency tags",
            "• Complete blank vendor directory",
        ]),
    ]

    card_y = 290
    card_w = 360
    card_h = 360
    gap = 30
    start_x = 70

    for i, (ctitle, cbullets) in enumerate(cards):
        x0 = start_x + i * (card_w + gap)
        y0 = card_y
        x1 = x0 + card_w
        y1 = y0 + card_h

        # Shadow
        draw.rounded_rectangle([x0 + 4, y0 + 4, x1 + 4, y1 + 4], radius=8, fill="#e2e8f0")
        # Main card
        draw.rounded_rectangle([x0, y0, x1, y1], radius=8, fill="#ffffff", outline=pal["line"], width=1)
        # Top card accent band
        draw.rounded_rectangle([x0, y0, x1, y0 + 42], radius=8, fill=pal["primary"])
        draw.rectangle([x0, y0 + 20, x1, y0 + 42], fill=pal["primary"])
        draw.text((x0 + card_w / 2.0, y0 + 21), ctitle, fill="#ffffff", font=font_card_t, anchor="mm")

        by = y0 + 65
        for b in cbullets:
            draw.text((x0 + 18, by), b, fill=pal["text_dark"], font=font_card_b)
            by += 28

        # Simulated mini table lines
        draw.line([x0 + 18, by + 10, x1 - 18, by + 10], fill=pal["line"], width=1)
        draw.rectangle([x0 + 18, by + 20, x1 - 18, y1 - 25], outline=pal["line"], fill=pal["bg_soft"])
        draw.line([x0 + 18, by + 45, x1 - 18, by + 45], fill=pal["line"])
        draw.line([x0 + 18, by + 70, x1 - 18, by + 70], fill=pal["line"])
        draw.line([x0 + 18, by + 95, x1 - 18, by + 95], fill=pal["line"])
        draw.line([x0 + 18, by + 120, x1 - 18, by + 120], fill=pal["line"])

    # Bottom footer notice
    draw.text((w / 2.0, h - 35), "Instant Download · Printable PDF in A4 & US Letter · Personal Use Only", fill=pal["text_muted"], font=font_chip, anchor="mm")

    # Save atomically
    temp_fd, temp_path = tempfile.mkstemp(prefix="cover_tmp_", suffix=".png")
    os.close(temp_fd)
    im.save(temp_path, format="PNG")
    shutil.move(temp_path, str(out_path))


def render_preview_image(out_path: Union[str, Path], spec: Dict[str, Any], preview_index: int) -> None:
    """Generates preview-1.png, preview-2.png, preview-3.png (1280x720)."""
    if not HAS_PILLOW:
        return
    norm = normalize_event_spec(spec)
    pal = THEME_PALETTES[norm["palette"]]
    w, h = 1280, 720
    im = Image.new("RGB", (w, h), color=pal["bg_soft"])
    draw = ImageDraw.Draw(im)

    # Border
    draw.rectangle([25, 25, w - 25, h - 25], outline=pal["line"], width=1)

    font_badge = _get_font(13, bold=True)
    font_title = _get_font(32, bold=True)
    font_sub = _get_font(16, bold=False)
    font_sec = _get_font(18, bold=True)
    font_body = _get_font(14, bold=False)

    preview_configs = {
        1: {
            "badge": "PAGE SPOTLIGHT · SECTION 01",
            "title": "Master Countdown & Day-of Timeline",
            "desc": "Stay fully ahead of schedule with phased milestones from 12 months down to the big day.",
            "col_left_title": "Countdown Milestones Included:",
            "col_left_items": [
                "✔ 12 Months: Core vision, venue booking & guest list targets",
                "✔ 9 to 6 Months: Decorator, caterer & photographer contracts",
                "✔ 3 Months: Invitations sent, RSVP deadline set & tastings",
                "✔ 1 Month: Seating chart finalized & stationery ordered",
                "✔ 1 Week & Day Before: Drop-offs, rehearsals & final rest",
            ],
            "col_right_title": "Day-of Schedule Highlights:",
            "col_right_items": [
                "✔ Hour-by-hour order of events from setup to send-off",
                "✔ Dedicated Muhurtham / Auspicious ceremony slot",
                "✔ Clear assignments for coordinators, vendors & VIPs",
                "✔ Buffer slots for unforeseen delays & transit times",
                "✔ Printable format ready for clipboard & binder use",
            ],
        },
        2: {
            "badge": "PAGE SPOTLIGHT · SECTION 02",
            "title": "Guest Management & Seating Layout",
            "desc": "Effortlessly track every guest, dietary requirements, and table assignments.",
            "col_left_title": "Guest RSVP Tracker Features:",
            "col_left_items": [
                "✔ Clean numbered guest list with party counts",
                "✔ Quick-check RSVP status (Attending / Declined / Pending)",
                "✔ Dedicated dietary restrictions & allergy tracking",
                "✔ Table number reference for seamless day-of seating",
                "✔ Summary cards for instant total headcount",
            ],
            "col_right_title": "Seating Chart Grid Structure:",
            "col_right_items": [
                "✔ 6 spacious table diagram cards per page",
                "✔ Customizable table names, themes & capacities",
                "✔ 6 individual guest lines per table card",
                "✔ Easy visual grouping of families and friend circles",
                "✔ Print extra sheets as your guest list grows",
            ],
        },
        3: {
            "badge": "PAGE SPOTLIGHT · SECTION 03",
            "title": "Zero-Currency Budget & Vendor Contacts",
            "desc": "Manage finances with clean blank lines, zero preset amounts, and a full vendor directory.",
            "col_left_title": "Master Budget Worksheet:",
            "col_left_items": [
                "✔ Strictly zero money amounts, currency symbols, or claims",
                "✔ Blank columns: Category, Item, Planned, Actual, Difference",
                "✔ Pre-filled essential event categories & services",
                "✔ Enter your own numbers in your local currency",
                "✔ Net variance summary row for balanced tracking",
            ],
            "col_right_title": "Vendor Directory & Ceremonies:",
            "col_right_items": [
                "✔ 18 major vendor categories with blank contact lines",
                "✔ Strictly no fake or real telephone numbers or emails",
                "✔ Indian wedding edition includes Mehendi, Haldi & Sangeet",
                "✔ Packing emergency kit & thank-you note tracking log",
                "✔ Permanent keepsake archive for your special milestone",
            ],
        },
    }

    cfg = preview_configs.get(preview_index, preview_configs[1])

    # Top Header
    draw.text((w / 2.0, 65), cfg["badge"], fill=pal["accent"], font=font_badge, anchor="mm")
    draw.text((w / 2.0, 110), cfg["title"], fill=pal["primary"], font=font_title, anchor="mm")
    draw.text((w / 2.0, 150), cfg["desc"], fill=pal["text_muted"], font=font_sub, anchor="mm")
    draw.line([100, 180, w - 100, 180], fill=pal["line"], width=1)

    # 2 Feature Panels
    panel_w = 520
    panel_h = 440
    p1_x0, p1_y0 = 80, 210
    p2_x0, p2_y0 = 680, 210

    # Panel 1
    draw.rounded_rectangle([p1_x0, p1_y0, p1_x0 + panel_w, p1_y0 + panel_h], radius=8, fill="#ffffff", outline=pal["line"])
    draw.rounded_rectangle([p1_x0, p1_y0, p1_x0 + panel_w, p1_y0 + 46], radius=8, fill=pal["primary"])
    draw.rectangle([p1_x0, p1_y0 + 20, p1_x0 + panel_w, p1_y0 + 46], fill=pal["primary"])
    draw.text((p1_x0 + 20, p1_y0 + 23), cfg["col_left_title"], fill="#ffffff", font=font_sec, anchor="lm")

    py = p1_y0 + 80
    for item in cfg["col_left_items"]:
        draw.text((p1_x0 + 25, py), item, fill=pal["text_dark"], font=font_body)
        py += 44

    # Panel 2
    draw.rounded_rectangle([p2_x0, p2_y0, p2_x0 + panel_w, p2_y0 + panel_h], radius=8, fill="#ffffff", outline=pal["line"])
    draw.rounded_rectangle([p2_x0, p2_y0, p2_x0 + panel_w, p2_y0 + 46], radius=8, fill=pal["primary"])
    draw.rectangle([p2_x0, p2_y0 + 20, p2_x0 + panel_w, p2_y0 + 46], fill=pal["primary"])
    draw.text((p2_x0 + 20, p2_y0 + 23), cfg["col_right_title"], fill="#ffffff", font=font_sec, anchor="lm")

    py = p2_y0 + 80
    for item in cfg["col_right_items"]:
        draw.text((p2_x0 + 25, py), item, fill=pal["text_dark"], font=font_body)
        py += 44

    # Bottom notice
    draw.text((w / 2.0, h - 35), f"Preview {preview_index} of 3 · Chilli Gavva Event Planning System", fill=pal["text_muted"], font=font_badge, anchor="mm")

    # Save atomically
    temp_fd, temp_path = tempfile.mkstemp(prefix=f"prev_{preview_index}_", suffix=".png")
    os.close(temp_fd)
    im.save(temp_path, format="PNG")
    shutil.move(temp_path, str(out_path))


# --- README & Documentation Generators ---

def render_readme_content(spec: Dict[str, Any], file_list: List[str]) -> str:
    """Generates README.md explaining the pack contents, printing tips, and Tenglish usage instructions."""
    norm = normalize_event_spec(spec)
    files_bullet = "\n".join(f"- `{f}`" for f in sorted(file_list))

    event_type_name = norm["event_type"].replace("_", " ").title()
    edition_name = norm["edition"].title()

    readme_text = f"""# {norm['title']}

Welcome to the **{norm['title']}** ({edition_name} Edition).
This printable event planning system is designed to provide complete peace of mind, structured timelines, and clear organization for your special {event_type_name.lower()} milestone.

---

## What You Get in This Pack

{files_bullet}

---

## Included Planning Sections & Worksheets

1. **Cover & Event Foundation**: Title, hosts, venue, and high-level celebration vision.
2. **Countdown Checklist (12 Months → 1 Day)**: Phased milestones ensuring no deadline is missed.
3. **Guest List & RSVP Tracker**: Track headcount, RSVP status, dietary needs, and table allocation.
4. **Vendor Contacts Directory**: Clean blank directories to record venue, catering, decor, and photo leads.
5. **Master Budget Worksheet (Zero-Currency)**: Structured blank lines with columns for Item, Planned, and Actual. Write your own numbers in your home currency.
6. **Seating Chart & Grid Layout**: 6-table diagram cards to arrange family and friends effortlessly.
7. **Day-of Master Schedule**: Detailed hour-by-hour program with key coordinator roles.
   *(Indian Edition includes dedicated Auspicious Muhurtham time slot).*
8. **Wedding Traditions (Indian Edition)**: Worksheets for Mehendi, Haldi, and Sangeet celebrations.
9. **Packing & Emergency Essentials**: Checklists for attire, ceremony items, first aid, and logistics.
10. **Gift Tracker & Registry Log**: Record gifts received and celebrate generous family and friends.
11. **Thank-You Note Tracker**: Keep gratitude on track post-event with easy delivery status marks.
12. **Notes & Vision Sketches**: Freeform grid and lined space for mood boards and ideas.

---

## How to Print and Use (Tenglish Guidance)

- **Print Scale**: Print PDFs at **100% scale** (Actual Size / Do not fit). Do not scale or shrink.
- **Paper Choice**: 100-120 GSM bright white paper recommended for durability and clean pen writing.
- **Binder Filing**: Three-hole or two-hole punch the left margin for ring binder storage, or clip onto a clipboard for day-of coordination.
- **Digital Annotation**: GoodNotes, Notability, or any PDF annotation app lo direct ga open chesi Apple Pencil / stylus tho fill cheyochu.
- **Budget Entry**: Budget sheet lo columns matrame unnay (Item, Planned, Actual). Me local currency lo amounts enter cheskondi.

---

## Terms of Use & License

- Personal use only.
- Do not resell, distribute, share, or claim these files as your own.
- Copyright (c) Chilli Gavva. All rights reserved.
"""
    return readme_text


# --- Format Registration & Core build() Entrypoint ---

def build(spec: Optional[Dict[str, Any]] = None, out_dir: Optional[Union[str, Path]] = None) -> Union[List[str], Dict[str, Any]]:
    """Builds the complete Event / Wedding Planner Pack.

    Creates:
      - Printable PDF in A4 size
      - Printable PDF in US Letter size
      - cover.png (1280x720)
      - preview-1.png (1280x720)
      - preview-2.png (1280x720)
      - preview-3.png (1280x720)
      - README.md
      - LICENSE.txt

    Returns:
      When called by product_builder (which expects list of created file names),
      or when called standalone, returns the list of created file names (relative to out_dir).
    """
    spec = spec or {}
    norm = normalize_event_spec(spec)

    if not out_dir:
        out_dir = Path(__file__).resolve().parent / "exported_content" / "products" / "event_planner"
    out_path = Path(out_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    created_files: List[str] = []

    # 1. Build A4 and Letter PDFs
    a4_filename = f"{norm['short_name']}-A4.pdf"
    letter_filename = f"{norm['short_name']}-Letter.pdf"

    builder_a4 = EventPlannerPdfBuilder(norm, page_size_name="A4")
    builder_a4.build_pdf(out_path / a4_filename)
    created_files.append(a4_filename)

    builder_letter = EventPlannerPdfBuilder(norm, page_size_name="Letter")
    builder_letter.build_pdf(out_path / letter_filename)
    created_files.append(letter_filename)

    # 2. Build Cover & Preview PNGs (if Pillow available)
    if HAS_PILLOW:
        cover_name = "cover.png"
        render_cover_image(out_path / cover_name, norm)
        created_files.append(cover_name)

        for p_idx in (1, 2, 3):
            p_name = f"preview-{p_idx}.png"
            render_preview_image(out_path / p_name, norm, p_idx)
            created_files.append(p_name)

    # 3. Build LICENSE.txt
    license_name = "LICENSE.txt"
    _atomic_write_text(out_path / license_name, LICENSE_TEXT)
    created_files.append(license_name)

    # 4. Build README.md
    readme_name = "README.md"
    readme_content = render_readme_content(norm, created_files + [readme_name])
    _atomic_write_text(out_path / readme_name, readme_content)
    created_files.append(readme_name)

    return sorted(created_files)


# --- Telegram Plugin Interface (Standard Interface) ---

COMMANDS = [
    {
        "cmd": "/eventplanner",
        "words": [
            "event planner", "wedding planner", "pelli planner",
            "gruhapravesam", "event plan", "wedding plan",
        ],
        "help_en": "Details and features for Event & Wedding Planner printable packs.",
        "help_te": "Event & Wedding Planner printable pack vivaralu chudandi.",
    }
]


def handle(text: str, ctx: dict) -> dict | None:
    """Telegram plugin handler following the standard plugin interface."""
    cleaned = (text or "").strip().lower()

    is_matched = False
    for cmd_meta in COMMANDS:
        if cleaned == cmd_meta["cmd"] or cleaned.startswith(cmd_meta["cmd"] + " "):
            is_matched = True
            break
        for trigger in cmd_meta.get("words", []):
            if trigger in cleaned:
                is_matched = True
                break
        if is_matched:
            break

    if not is_matched:
        return None

    response_text = (
        "Event & Wedding Planner builder ready ga vundi! "
        "Support chestunna events: Weddings (Indian & Global), Birthdays, Gruhapravesam (House-warming), and Festivals. "
        "Printable PDF pack lo: 12-Month Countdown Checklist, Guest List with RSVP, Seating Grid, "
        "Vendor Contacts (blank lines only), Day-of Schedule (with Muhurtham slot), "
        "Mehendi/Haldi/Sangeet ceremonial pages, and zero-currency Budget Worksheet unnay. "
        "A4 and US Letter sizes, cover and preview mockups tho completely automated ga generate avtundi."
    )
    return {"text": response_text}
