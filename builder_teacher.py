"""builder_teacher.py — Teacher Lesson Plan Organizer printable pack.

FORMAT = 'teacher_planner'
KEYWORDS = ['lesson plan', 'teacher planner', 'lesson plan organizer',
            'classroom planner', 'teacher organizer']

Builds a deterministic A4 + US Letter printable pack for teachers:
year overview, weekly lesson plan (5 days x periods), unit plan,
class roster and attendance, grade tracker, parent contact log, and
substitute notes. Cover and three preview images are drawn with Pillow only.
No stock images, no currency amounts, no network.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Union

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    HAS_PILLOW = False

import pdf_design

_DEFAULT_COLOURS = {
    'ink': '#1f2933', 'accent': '#2b6cb0', 'muted': '#5f6b73',
    'line': '#c9d3d6', 'box': '#9fb8c8', 'soft': '#eef4fb', 'warm': '#2b6cb0',
}
_COLOURS = dict(_DEFAULT_COLOURS)
_STYLE = {"name": "A"}


def _col(role: str) -> str:
    return _COLOURS[role]


def _use_theme(spec: Any) -> str:
    """Load the theme for this spec into the PDF colours."""
    _COLOURS.clear()
    _COLOURS.update(_DEFAULT_COLOURS)
    _STYLE["name"] = "A"
    try:
        import pdf_themes
        pdf_themes.register_themes_in_pdf_design()
        theme = pdf_themes.get_theme_for_spec(dict(spec or {}))
    except Exception:
        return _STYLE["name"]
    tok = theme["tokens"]
    _COLOURS.update(
        ink=tok["ink"], accent=tok["accent"], muted=tok["muted"], line=tok["line"],
        box=tok.get("border", tok["line"]),
        soft=tok.get("weekend") or tok.get("surface_soft", "#f4f4f4"),
        warm=tok.get("accent_light", tok["accent"]),
    )
    _STYLE["name"] = theme["slug"]
    return _STYLE["name"]


FORMAT = "teacher_planner"
KEYWORDS = [
    "lesson plan", "teacher planner", "lesson plan organizer",
    "classroom planner", "teacher organizer",
]

DEFAULT_TITLE = "Teacher Lesson Plan Organizer"
DEFAULT_YEAR = 2027
DEFAULT_SUBJECTS = ["Reading", "Writing", "Math", "Science", "Social Studies", "Art"]
DEFAULT_PERIODS = ["Period 1", "Period 2", "Period 3", "Period 4", "Period 5"]
WEEK_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
PAPER_SIZES = {"a4": "A4", "letter": "Letter"}
MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
WEEKLY_PAGES = 40



def _clean_title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    return title or DEFAULT_TITLE


def _clean_list(value: Any, default: List[str], limit: int) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return list(default)
    items: List[str] = []
    for item in value:
        text = " ".join(str(item).split())
        if text and text not in items:
            items.append(text)
        if len(items) == limit:
            break
    return items or list(default)


def normalize_spec(spec: Any) -> Dict[str, Any]:
    """Validate the spec and return a deterministic normalized dictionary."""
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dictionary")

    title = _clean_title(spec.get("title", DEFAULT_TITLE))
    try:
        year = int(spec.get("year", DEFAULT_YEAR))
    except (TypeError, ValueError):
        raise ValueError("year must be an integer")
    if not 1900 <= year <= 2200:
        raise ValueError("year must be between 1900 and 2200")

    subjects = _clean_list(spec.get("subjects"), DEFAULT_SUBJECTS, 8)
    periods = _clean_list(spec.get("periods"), DEFAULT_PERIODS, 8)
    teacher_name = " ".join(str(spec.get("teacher_name", "")).split())[:60]
    grade = " ".join(str(spec.get("grade", "")).split())[:40]

    return {
        "title": title,
        "year": year,
        "subjects": subjects,
        "periods": periods,
        "teacher_name": teacher_name,
        "grade": grade,
    }


def _font(size: int, bold: bool = False):
    try:
        import mockups
        face = mockups.font("bold" if bold else "regular", size)
        if face is not None:
            return face
    except Exception:
        pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


PREVIEW_SIZE = (1600, 1067)
COVER_SIZE = (1600, 1067)


def _draw_mini_page(draw, x, y, w, h, kind: str, ink, accent, soft, line, bg="#ffffff"):
    draw.rectangle([x, y, x + w, y + h], fill=bg, outline=line, width=2)
    draw.rectangle([x, y, x + w, y + max(18, h // 12)], fill=accent)
    draw.text((x + 8, y + 4), kind[:28], fill="#ffffff", font=_font(max(10, h // 20), True))
    body_top = y + max(22, h // 10)
    if kind == "Weekly Plan":
        cols = 6
        rows = 6
        cw = (w - 16) / cols
        rh = (h - (body_top - y) - 12) / rows
        for r in range(rows):
            for c in range(cols):
                cx = x + 8 + c * cw
                cy = body_top + r * rh
                fill = soft if r == 0 or c == 0 else "#ffffff"
                draw.rectangle([cx, cy, cx + cw, cy + rh], outline=line, fill=fill)
    elif kind == "Year Overview":
        for i in range(12):
            cc = i % 4
            rr = i // 4
            bx = x + 8 + cc * ((w - 16) / 4)
            by = body_top + rr * ((h - (body_top - y) - 12) / 3)
            bw = (w - 24) / 4
            bh = (h - (body_top - y) - 20) / 3
            draw.rectangle([bx, by, bx + bw, by + bh], outline=line, fill=soft)
    elif kind == "Grade Tracker":
        for r in range(8):
            ry = body_top + r * ((h - (body_top - y) - 12) / 8)
            draw.line([x + 8, ry, x + w - 8, ry], fill=line, width=1)
            if r == 0:
                draw.rectangle([x + 8, ry, x + w - 8, ry + (h - (body_top - y) - 12) / 8], fill=soft, outline=line)
    else:
        for r in range(6):
            ry = body_top + 8 + r * ((h - (body_top - y) - 24) / 6)
            draw.line([x + 12, ry, x + w - 12, ry], fill=line, width=1)


def _cover_image(path: Path, norm: Dict[str, Any], preview: int = 0) -> None:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for teacher planner images")
    labels = ["Cover", "Weekly Lesson Plan", "Roster & Grades", "Substitute Notes"]
    kind = preview % len(labels)
    w, h = (COVER_SIZE if kind == 0 else PREVIEW_SIZE)
    bg = "#f7f9fc"
    ink = _col("ink")
    accent = _col("accent")
    soft = _col("soft")
    line = _col("line")
    im = Image.new("RGB", (w, h), color=bg)
    draw = ImageDraw.Draw(im)
    draw.rectangle([20, 20, w - 20, h - 20], outline=accent, width=4)
    draw.rectangle([20, 20, w - 20, 84], fill=accent)
    draw.text((44, 36), "CHILLI GAVVA · TEACHER PLANNER", fill="#ffffff", font=_font(24, True))
    draw.text((48, 110), norm["title"][:48], fill=ink, font=_font(46, True))
    draw.text((48, 170), f"{norm['year']} · 24+ pages · A4 + US Letter", fill=accent, font=_font(24, True))
    draw.text((48, 206), "For teachers who want clear, printable lesson planning pages", fill=_col("muted"), font=_font(18))

    if kind == 0:
        # Styled cover with mini-collage of 3 real pages
        collage_y = 260
        draw.rounded_rectangle([48, collage_y, w - 48, h - 110], radius=18, fill="#ffffff", outline=line, width=2)
        draw.text((72, collage_y + 18), "INSIDE THE PACK", fill=accent, font=_font(20, True))
        mini_specs = [("Weekly Plan", 72, collage_y + 62, 430, 420), ("Year Overview", 540, collage_y + 62, 430, 420), ("Grade Tracker", 1008, collage_y + 62, 430, 420)]
        for mk, mx, my, mw, mh in mini_specs:
            _draw_mini_page(draw, mx, my, mw, mh, mk, ink, accent, soft, line)
            draw.text((mx + 8, my + mh + 10), mk, fill=accent, font=_font(16, True))
        draw.text((72, h - 150), "Weekly lesson pages · Unit plans · Roster · Grades · Parent log · Sub notes", fill=ink, font=_font(18))
    elif kind == 1:
        # Larger real page: weekly lesson plan
        draw.text((48, 250), "WEEKLY LESSON PLAN", fill=accent, font=_font(22, True))
        x0, y0 = 48, 296
        cols = ["Period"] + WEEK_DAYS
        cw = [150, 250, 250, 250, 250, 250]
        for c, label in enumerate(cols):
            x = x0 + sum(cw[:c])
            draw.rectangle([x, y0, x + cw[c], y0 + 44], outline=line, fill=soft)
            draw.text((x + 10, y0 + 10), label[:12], fill=accent, font=_font(16, True))
        for r in range(8):
            y = y0 + 44 + r * 72
            for c in range(len(cols)):
                x = x0 + sum(cw[:c])
                draw.rectangle([x, y, x + cw[c], y + 72], outline=line, fill="#ffffff")
            draw.text((x0 + 10, y + 24), f"P{r + 1}", fill=ink, font=_font(16))
            for c in range(1, len(cols)):
                x = x0 + sum(cw[:c])
                draw.line([x + 10, y + 36, x + cw[c] - 10, y + 36], fill=line, width=1)
    elif kind == 2:
        draw.text((48, 250), "ROSTER & GRADE TRACKER", fill=accent, font=_font(22, True))
        headers = ["Student", "Mon", "Tue", "Wed", "Thu", "Fri", "Grade", "Notes"]
        x0, y0 = 48, 296
        widths = [320, 120, 120, 120, 120, 120, 140, 440]
        for c, label in enumerate(headers):
            x = x0 + sum(widths[:c])
            draw.rectangle([x, y0, x + widths[c], y0 + 44], outline=line, fill=soft)
            draw.text((x + 10, y0 + 10), label, fill=accent, font=_font(16, True))
        for r in range(10):
            y = y0 + 44 + r * 56
            for c in range(len(headers)):
                x = x0 + sum(widths[:c])
                draw.rectangle([x, y, x + widths[c], y + 56], outline=line, fill="#ffffff")
            draw.line([x0 + 14, y + 28, x0 + 280, y + 28], fill=line, width=1)
    else:
        draw.text((48, 250), "SUBSTITUTE TEACHER NOTES", fill=accent, font=_font(22, True))
        draw.rounded_rectangle([48, 296, 980, h - 110], radius=16, fill="#ffffff", outline=line, width=2)
        for i, label in enumerate(["Class routine", "Emergency contacts", "Today's plan", "Materials needed", "Important notes"]):
            y = 336 + i * 108
            draw.text((76, y), label, fill=accent, font=_font(18, True))
            draw.line([76, y + 40, 940, y + 40], fill=line, width=1)
            draw.line([76, y + 74, 940, y + 74], fill=line, width=1)
        draw.rounded_rectangle([1020, 296, w - 48, h - 110], radius=16, fill=soft, outline=line, width=2)
        draw.text((1048, 326), "QUICK CHECKLIST", fill=accent, font=_font(18, True))
        for i, item in enumerate(["Attendance done", "Work collected", "Materials ready", "Note left for teacher", "Room locked"]):
            y = 386 + i * 72
            draw.rectangle([1048, y, 1078, y + 30], outline=accent, width=2, fill="#ffffff")
            draw.text((1092, y + 2), item, fill=ink, font=_font(16))

    draw.text((48, h - 72), "Year overview · 40 weekly lesson pages · Unit plans · Roster · Grades · Parent log · Sub notes", fill=_col("muted"), font=_font(16))
    draw.text((w - 260, h - 72), labels[kind], fill=accent, font=_font(18, True))
    im.save(str(path), format="PNG", optimize=True)


def _heading(pdf: Any, text: str, subtitle: str = "") -> None:
    pdf.text(pdf_design.MARGIN, pdf.h - 62, text, 20, bold=True, color=_col("ink"))
    if subtitle:
        pdf.text(pdf_design.MARGIN, pdf.h - 84, subtitle, 10, color=_col("muted"))
    pdf.line(pdf_design.MARGIN, pdf.h - 96, pdf.w - pdf_design.MARGIN, pdf.h - 96, color=_col("line"))


def _title_page(pdf: Any, norm: Dict[str, Any]) -> None:
    lines, size = pdf_design.fit_text(norm["title"], 30, pdf.w - 2 * pdf_design.MARGIN, bold=True, max_lines=3, min_size=16)
    y = pdf.h * 0.66
    for line in lines:
        pdf.text_center(pdf.w / 2, y, line, size, bold=True, color=_col("ink"))
        y -= size * 1.25
    pdf.text_center(pdf.w / 2, y - 18, f"School Year {norm['year']}", 18, color=_col("accent"))
    pdf.text_center(pdf.w / 2, y - 48, "Year overview · Weekly plans · Unit plan · Roster · Grades · Parent log · Sub notes", 10, color=_col("muted"))
    pdf.line(pdf.w / 2 - 90, y - 76, pdf.w / 2 + 90, y - 76, color=_col("accent"), width=1.2)
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = y - 110
    bottom = 86
    pdf.rect(left, bottom, right - left, top - bottom, color=_col("line"), width=0.8)
    pdf.text(left + 14, top - 22, "Start here", 12, bold=True, color=_col("accent"))
    fields = ["Teacher name", "Grade / class", "Room / subject", "School year focus"]
    fy = top - 50
    gap = (top - bottom - 90) / len(fields)
    for label in fields:
        pdf.text(left + 14, fy, label, 9, color=_col("ink"))
        pdf.line(left + 140, fy - 2, right - 14, fy - 2, color=_col("line"), width=0.6)
        fy -= max(22.0, gap)
    pdf.text(left + 14, bottom + 20, "Print at 100% scale (Actual Size). Use one weekly plan page per week.", 8.5, color=_col("muted"))


def _year_overview_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, f"Year Overview — {norm['year']}", "Key dates, themes, and reminders for each month.")
    left = pdf_design.MARGIN
    top = pdf.h - 124
    gap = 10
    cols = 3
    rows = 4
    col_w = (pdf.w - 2 * pdf_design.MARGIN - (cols - 1) * gap) / cols
    row_h = (top - 90 - (rows - 1) * gap) / rows
    for i, month in enumerate(MONTHS):
        c = i % cols
        r = i // cols
        x = left + c * (col_w + gap)
        y = top - (r + 1) * row_h - r * gap
        pdf.rect(x, y, col_w, row_h, color=_col("line"), width=0.7)
        pdf.fill(x, y + row_h - 18, col_w, 18, _col("soft"))
        pdf.text(x + 6, y + row_h - 13, month, 9, bold=True, color=_col("accent"))
        for ln in range(3):
            ly = y + row_h - 34 - ln * 18
            pdf.line(x + 6, ly, x + col_w - 6, ly, color=_col("line"), width=0.5)


def _weekly_lesson_page(pdf: Any, norm: Dict[str, Any], week_no: int) -> None:
    _heading(pdf, f"Weekly Lesson Plan — Week {week_no}", "Plan five days across class periods. Use one page per week.")
    pdf.text(pdf_design.MARGIN, pdf.h - 114, "Week of: ____________________", 9, color=_col("muted"))
    pdf.text(pdf.w / 2, pdf.h - 114, "Focus / theme: ______________________________", 9, color=_col("muted"))

    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 136
    bottom = 96
    cols = ["Period"] + WEEK_DAYS
    p_w = 64
    day_w = (right - left - p_w) / 5
    row_h = (top - bottom - 22) / len(norm["periods"])
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    pdf.text(left + 4, top - 15, "Period", 8, bold=True, color=_col("accent"))
    for i, day in enumerate(WEEK_DAYS):
        x = left + p_w + i * day_w
        pdf.text(x + 4, top - 15, day, 8, bold=True, color=_col("accent"))
    for r, period in enumerate(norm["periods"]):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.6)
        pdf.line(left + p_w, y, left + p_w, y + row_h, color=_col("line"), width=0.6)
        pdf.text(left + 4, y + row_h / 2 - 4, period[:10], 7.5, color=_col("ink"))
        for i in range(5):
            x = left + p_w + i * day_w
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.5)
            # light writing lines inside cells
            for ln in (0.33, 0.66):
                ly = y + row_h * ln
                pdf.line(x + 4, ly, x + day_w - 4, ly, color=_col("line"), width=0.3)
    # bottom notes area fills remaining space
    pdf.text(left, bottom - 10, "Notes / homework / reminders:", 8, bold=True, color=_col("accent"))
    for ln in range(3):
        ly = bottom - 24 - ln * 14
        if ly > 58:
            pdf.line(left, ly, right, ly, color=_col("line"), width=0.5)


def _unit_plan_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Unit Plan", "Map one unit from goals to assessment.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    y = pdf.h - 128
    fields = [
        ("Unit title", 1), ("Subject / grade", 1), ("Dates", 1),
        ("Learning goals", 3), ("Key activities", 4), ("Materials", 2),
        ("Assessment / check for understanding", 3),
    ]
    for label, lines in fields:
        pdf.text(left, y, label, 9, bold=True, color=_col("accent"))
        y -= 14
        for _ in range(lines):
            pdf.line(left, y, right, y, color=_col("line"), width=0.6)
            y -= 20
        y -= 8
        if y < 100:
            break


def _roster_attendance_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Class Roster & Attendance", "Track student attendance for one week.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    headers = ["Student name"] + [d[:3] for d in WEEK_DAYS] + ["Notes"]
    widths = [180] + [48] * 5 + [right - left - 180 - 48 * 5]
    row_h = (top - bottom - 22) / 18
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7.5, bold=True, color=_col("accent"))
        x += w
    for r in range(18):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w
        pdf.line(left + 6, y + row_h / 2 - 4, left + 150, y + row_h / 2 - 4, color=_col("line"), width=0.4)


def _grade_tracker_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Grade Tracker", "Record assignments and scores for each student.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    headers = ["Student name", "A1", "A2", "A3", "A4", "A5", "A6", "Total", "Notes"]
    widths = [150, 45, 45, 45, 45, 45, 45, 50, right - left - 150 - 45 * 6 - 50]
    row_h = (top - bottom - 22) / 18
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7.5, bold=True, color=_col("accent"))
        x += w
    for r in range(18):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w
        pdf.line(left + 6, y + row_h / 2 - 4, left + 130, y + row_h / 2 - 4, color=_col("line"), width=0.4)


def _parent_contact_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Parent Contact Log", "Keep a clear record of family communication.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    headers = ["Date", "Student", "Parent / Guardian", "Reason", "Follow-up"]
    widths = [60, 110, 120, right - left - 60 - 110 - 120 - 110, 110]
    row_h = (top - bottom - 22) / 14
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7.5, bold=True, color=_col("accent"))
        x += w
    for r in range(14):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w


def _substitute_notes_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Substitute Teacher Notes", "Everything a substitute needs for a smooth day.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    y = pdf.h - 128
    sections = [
        ("Class routine and schedule", 4),
        ("Helpful students / class jobs", 3),
        ("Emergency procedures and contacts", 3),
        ("Today's lesson plan", 5),
        ("End-of-day notes for the teacher", 4),
    ]
    for label, lines in sections:
        pdf.text(left, y, label, 9, bold=True, color=_col("accent"))
        y -= 14
        for _ in range(lines):
            pdf.line(left, y, right, y, color=_col("line"), width=0.6)
            y -= 20
        y -= 6
        if y < 90:
            break


def _student_notes_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Student Notes & Observations", "Keep quick notes for individual student support.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    headers = ["Student", "Strengths", "Needs support with", "Next step"]
    widths = [120, right - left - 120 - 150 - 110, 150, 110]
    row_h = (top - bottom - 22) / 14
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7.5, bold=True, color=_col("accent"))
        x += w
    for r in range(14):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w
        pdf.line(left + 6, y + row_h / 2 - 4, left + 105, y + row_h / 2 - 4, color=_col("line"), width=0.4)


def _unit_plan_page(pdf: Any, norm: Dict[str, Any], page_no: int = 1) -> None:
    label = "Unit Plan" if page_no == 1 else f"Unit Plan — Page {page_no}"
    _heading(pdf, label, "Map one unit from goals to assessment.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    y = pdf.h - 128
    fields = [
        ("Unit title / subject", 1), ("Dates / term focus", 1),
        ("Learning goals", 3), ("Key activities and lessons", 5),
        ("Materials / resources", 3), ("Assessment / check for understanding", 3),
        ("Reflection / next steps", 2),
    ]
    for label, lines in fields:
        pdf.text(left, y, label, 9, bold=True, color=_col("accent"))
        y -= 14
        for _ in range(lines):
            pdf.line(left, y, right, y, color=_col("line"), width=0.6)
            y -= 18
        y -= 6
        if y < 90:
            break


def _build_pdf(path: Path, paper: str, norm: Dict[str, Any]) -> None:
    size = PAPER_SIZES[paper]
    pdf = pdf_design.Pdf(size, footer=f"{norm['title']} · {norm['year']}", style=_STYLE["name"])
    pdf.new_page(); _title_page(pdf, norm)
    pdf.new_page(); _year_overview_page(pdf, norm)
    for week in range(1, WEEKLY_PAGES + 1):
        pdf.new_page(); _weekly_lesson_page(pdf, norm, week)
    pdf.new_page(); _unit_plan_page(pdf, norm, 1)
    pdf.new_page(); _unit_plan_page(pdf, norm, 2)
    pdf.new_page(); _roster_attendance_page(pdf, norm)
    pdf.new_page(); _grade_tracker_page(pdf, norm)
    pdf.new_page(); _parent_contact_page(pdf, norm)
    pdf.new_page(); _substitute_notes_page(pdf, norm)
    pdf.new_page(); _student_notes_page(pdf, norm)
    pdf.save(str(path), title=norm["title"])


def _readme(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"{norm['title']} — {norm['year']}",
        "=" * 52,
        "",
        "WHAT IS INSIDE",
        "- A4 PDF and US Letter PDF",
        "- Year overview page",
        f"- {WEEKLY_PAGES} undated weekly lesson plan pages (5 days x periods)",
        "- 2 unit plan pages",
        "- Class roster and attendance page",
        "- Grade tracker page",
        "- Parent contact log page",
        "- Substitute teacher notes page",
        "- Student notes and observations page",
        "- cover.png and three preview images",
        "",
        "HOW TO PRINT",
        "1. Open the PDF for your paper size.",
        "2. Print at 100% scale (Actual Size).",
        "3. Use one weekly lesson plan page per week.",
        "4. Keep the substitute notes page ready in your classroom folder.",
        "",
        "PAGE COUNT",
        f"- {2 + WEEKLY_PAGES + 7} pages per PDF (A4 and US Letter included)",
        "",
        "LICENSE",
        "Personal use only. Do not resell or redistribute the files.",
    ]) + "\n"


def _license(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"LICENSE — {norm['title']}",
        "",
        "This printable pack is for personal use only.",
        "You may print copies for yourself and your classroom planning.",
        "You may not resell, redistribute, share, or claim the files as your own.",
        "All layouts and text are generated for the Chilli Gavva product library.",
        "",
    ])


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    """Build the teacher lesson plan organizer pack and return created file names."""
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for the teacher planner builder")
    norm = normalize_spec(spec)
    _use_theme(spec)
    out_path = Path(out_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="teacher_build_"))
    names: List[str] = []
    try:
        for paper in PAPER_SIZES:
            name = f"teacher_planner_{paper}.pdf"
            _build_pdf(temp_dir / name, paper, norm)
            names.append(name)

        cover_name = "cover.png"
        _cover_image(temp_dir / cover_name, norm, preview=0)
        names.append(cover_name)
        for index in range(1, 4):
            name = f"preview_{index}.png"
            _cover_image(temp_dir / name, norm, preview=index)
            names.append(name)

        names.extend(["README.txt", "LICENSE.txt"])
        _write_text(temp_dir / "README.txt", _readme(norm))
        _write_text(temp_dir / "LICENSE.txt", _license(norm))

        page_count = 2 + WEEKLY_PAGES + 7
        manifest = {
            "format": FORMAT,
            "title": norm["title"],
            "year": norm["year"],
            "subjects": norm["subjects"],
            "periods": norm["periods"],
            "paper_sizes": list(PAPER_SIZES),
            "pages_per_pdf": page_count,
            "weekly_pages": WEEKLY_PAGES,
            "files": sorted(names + ["manifest.json"]),
        }
        _write_text(temp_dir / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        names.append("manifest.json")

        out_path.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(temp_dir / name, out_path / name)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return sorted(names)


def _content_digest(norm: Dict[str, Any]) -> str:
    payload = json.dumps(norm, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
