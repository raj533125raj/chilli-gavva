"""builder_baby.py — Newborn Daily Routine Tracker printable pack.

FORMAT = 'baby_tracker'
KEYWORDS = ['newborn tracker', 'baby log', 'newborn routine',
            'baby feeding log', 'infant tracker', 'newborn checklist']

Builds a deterministic A4 + US Letter printable pack for newborn routines:
feeding log, sleep log, diaper log, daily routine checklist, doctor visit notes,
milestones, and a vaccine date table (dates only, no medical advice).
Cover and three preview images are drawn with Pillow only.
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
    'ink': '#1f2933', 'accent': '#b07a5b', 'muted': '#5f6b73',
    'line': '#d6ccc6', 'box': '#c9aea0', 'soft': '#fbf5f1', 'warm': '#b07a5b',
}
_COLOURS = dict(_DEFAULT_COLOURS)
_STYLE = {"name": "A"}


def _col(role: str) -> str:
    return _COLOURS[role]


def _use_theme(spec: Any) -> str:
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


FORMAT = "baby_tracker"
KEYWORDS = [
    "newborn tracker", "baby log", "newborn routine",
    "baby feeding log", "infant tracker", "newborn checklist",
    "newborn daily routine", "daily routine tracker", "newborn routine tracker",
]

DEFAULT_TITLE = "Newborn Daily Routine Tracker"
PAPER_SIZES = {"a4": "A4", "letter": "Letter"}
WEEK_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAILY_LOG_PAGES = 30
COVER_SIZE = (1600, 1067)
PREVIEW_SIZE = (1600, 1067)


def _clean_title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    return title or DEFAULT_TITLE


def normalize_spec(spec: Any) -> Dict[str, Any]:
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dictionary")
    title = _clean_title(spec.get("title", DEFAULT_TITLE))
    baby_name = " ".join(str(spec.get("baby_name", "")).split())[:60]
    caregiver = " ".join(str(spec.get("caregiver", "")).split())[:60]
    return {
        "title": title,
        "baby_name": baby_name,
        "caregiver": caregiver,
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


def _draw_mini(draw, x, y, w, h, title, accent, soft, line):
    draw.rectangle([x, y, x + w, y + h], fill="#ffffff", outline=line, width=2)
    draw.rectangle([x, y, x + w, y + 24], fill=accent)
    draw.text((x + 8, y + 5), title, fill="#ffffff", font=_font(12, True))
    for i in range(6):
        ly = y + 40 + i * ((h - 48) / 6)
        draw.line([x + 10, ly, x + w - 10, ly], fill=line, width=1)
        if i == 0:
            draw.rectangle([x + 10, y + 28, x + w - 10, ly], fill=soft, outline=line)


def _cover_image(path: Path, norm: Dict[str, Any], preview: int = 0) -> None:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for baby tracker images")
    labels = ["Cover", "Daily Log", "Weekly Summary", "Milestones & Dates"]
    kind = preview % len(labels)
    w, h = COVER_SIZE if kind == 0 else PREVIEW_SIZE
    bg = "#fdfaf8"
    ink = _col("ink")
    accent = _col("accent")
    soft = _col("soft")
    line = _col("line")
    im = Image.new("RGB", (w, h), color=bg)
    draw = ImageDraw.Draw(im)
    draw.rectangle([20, 20, w - 20, h - 20], outline=accent, width=4)
    draw.rectangle([20, 20, w - 20, 84], fill=accent)
    draw.text((44, 36), "CHILLI GAVVA · NEWBORN TRACKER", fill="#ffffff", font=_font(24, True))
    draw.text((48, 110), norm["title"][:48], fill=ink, font=_font(46, True))
    draw.text((48, 170), "24+ pages · A4 + US Letter", fill=accent, font=_font(24, True))
    draw.text((48, 206), "For caregivers who want a simple daily newborn routine log", fill=_col("muted"), font=_font(18))
    if kind == 0:
        draw.rounded_rectangle([48, 260, w - 48, h - 110], radius=18, fill="#ffffff", outline=line, width=2)
        draw.text((72, 280), "INSIDE THE PACK", fill=accent, font=_font(20, True))
        _draw_mini(draw, 72, 330, 430, 420, "Daily Log", accent, soft, line)
        _draw_mini(draw, 540, 330, 430, 420, "Weekly Summary", accent, soft, line)
        _draw_mini(draw, 1008, 330, 430, 420, "Milestones", accent, soft, line)
        draw.text((72, 780), "30 daily logs · Weekly summaries · Milestones · Doctor notes · Date table", fill=ink, font=_font(18))
    elif kind == 1:
        draw.text((48, 250), "DAILY LOG", fill=accent, font=_font(22, True))
        headers = ["Time", "Feed", "Sleep", "Diaper", "Notes"]
        widths = [140, 260, 260, 220, 620]
        x0, y0 = 48, 296
        for c, label in enumerate(headers):
            x = x0 + sum(widths[:c])
            draw.rectangle([x, y0, x + widths[c], y0 + 44], outline=line, fill=soft)
            draw.text((x + 10, y0 + 10), label, fill=accent, font=_font(16, True))
        for r in range(10):
            y = y0 + 44 + r * 58
            for c in range(len(headers)):
                x = x0 + sum(widths[:c])
                draw.rectangle([x, y, x + widths[c], y + 58], outline=line, fill="#ffffff")
    elif kind == 2:
        draw.text((48, 250), "WEEKLY SUMMARY", fill=accent, font=_font(22, True))
        draw.rounded_rectangle([48, 296, 980, h - 110], radius=16, fill="#ffffff", outline=line, width=2)
        for i, label in enumerate(["Feeding patterns", "Sleep patterns", "Diaper notes", "Questions for care team"]):
            y = 336 + i * 132
            draw.text((76, y), label, fill=accent, font=_font(18, True))
            for ln in range(2):
                draw.line([76, y + 42 + ln * 34, 940, y + 42 + ln * 34], fill=line, width=1)
        draw.rounded_rectangle([1020, 296, w - 48, h - 110], radius=16, fill=soft, outline=line, width=2)
        draw.text((1048, 326), "SUPPLIES CHECK", fill=accent, font=_font(18, True))
        for i, item in enumerate(["Diapers", "Wipes", "Bottles / feeding", "Clothes / swaddles", "Notes"]):
            y = 386 + i * 86
            draw.rectangle([1048, y, 1078, y + 30], outline=accent, width=2, fill="#ffffff")
            draw.text((1092, y + 2), item, fill=ink, font=_font(16))
    else:
        draw.text((48, 250), "MILESTONES & DATE TABLE", fill=accent, font=_font(22, True))
        draw.rounded_rectangle([48, 296, 760, h - 110], radius=16, fill="#ffffff", outline=line, width=2)
        draw.text((76, 326), "Milestones & memories", fill=accent, font=_font(18, True))
        for i in range(8):
            ly = 380 + i * 52
            draw.line([76, ly, 720, ly], fill=line, width=1)
        draw.rounded_rectangle([800, 296, w - 48, h - 110], radius=16, fill="#ffffff", outline=line, width=2)
        draw.text((828, 326), "Vaccine date table", fill=accent, font=_font(18, True))
        draw.text((828, 352), "Dates only - no medical advice", fill=_col("muted"), font=_font(14))
        for i in range(8):
            ly = 390 + i * 48
            draw.line([828, ly, w - 88, ly], fill=line, width=1)
    draw.text((48, h - 72), "30 daily logs · Weekly summaries · Milestones · Doctor notes · Date table (dates only)", fill=_col("muted"), font=_font(16))
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
    pdf.text_center(pdf.w / 2, y - 18, "Daily logs, routines, and notes", 18, color=_col("accent"))
    pdf.text_center(pdf.w / 2, y - 48, "Feeding · Sleep · Diapers · Checklist · Doctor notes · Milestones · Date table", 10, color=_col("muted"))
    pdf.line(pdf.w / 2 - 90, y - 76, pdf.w / 2 + 90, y - 76, color=_col("accent"), width=1.2)
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = y - 110
    bottom = 86
    pdf.rect(left, bottom, right - left, top - bottom, color=_col("line"), width=0.8)
    pdf.text(left + 14, top - 22, "Start here", 12, bold=True, color=_col("accent"))
    fields = ["Baby name", "Caregiver(s)", "Start date", "Important notes"]
    fy = top - 50
    gap = (top - bottom - 90) / len(fields)
    for label in fields:
        pdf.text(left + 14, fy, label, 9, color=_col("ink"))
        pdf.line(left + 130, fy - 2, right - 14, fy - 2, color=_col("line"), width=0.6)
        fy -= max(22.0, gap)
    pdf.text(left + 14, bottom + 20, "Print at 100% scale (Actual Size). Reprint daily log pages as needed.", 8.5, color=_col("muted"))


def _daily_log_page(pdf: Any, norm: Dict[str, Any], day_no: int) -> None:
    _heading(pdf, f"Daily Log — Day {day_no}", "Track feeding, sleep, diapers, and notes for one day.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    # Feeding block
    pdf.text(left, top + 2, "Feeding", 9, bold=True, color=_col("accent"))
    fh_headers = ["Time", "Type / side", "Amount / duration", "Notes"]
    fh_widths = [55, 95, 110, right - left - 260]
    fh_rows = 6
    fh_row_h = 22
    fh_y = top - 16
    pdf.fill(left, fh_y - 18, right - left, 18, _col("soft"))
    pdf.rect(left, fh_y - 18, right - left, 18, color=_col("line"), width=0.6)
    x = left
    for htxt, w in zip(fh_headers, fh_widths):
        pdf.text(x + 3, fh_y - 13, htxt, 7, bold=True, color=_col("accent"))
        x += w
    for r in range(fh_rows):
        y = fh_y - 18 - (r + 1) * fh_row_h
        pdf.rect(left, y, right - left, fh_row_h, color=_col("line"), width=0.4)
        x = left
        for w in fh_widths:
            pdf.line(x, y, x, y + fh_row_h, color=_col("line"), width=0.3)
            x += w
    # Sleep block
    sy = fh_y - 18 - fh_rows * fh_row_h - 24
    pdf.text(left, sy + 2, "Sleep", 9, bold=True, color=_col("accent"))
    sh_headers = ["Start", "Wake", "Total", "Where / notes"]
    sh_widths = [70, 70, 70, right - left - 210]
    sh_rows = 4
    sh_row_h = 22
    pdf.fill(left, sy - 18, right - left, 18, _col("soft"))
    pdf.rect(left, sy - 18, right - left, 18, color=_col("line"), width=0.6)
    x = left
    for htxt, w in zip(sh_headers, sh_widths):
        pdf.text(x + 3, sy - 13, htxt, 7, bold=True, color=_col("accent"))
        x += w
    for r in range(sh_rows):
        y = sy - 18 - (r + 1) * sh_row_h
        pdf.rect(left, y, right - left, sh_row_h, color=_col("line"), width=0.4)
        x = left
        for w in sh_widths:
            pdf.line(x, y, x, y + sh_row_h, color=_col("line"), width=0.3)
            x += w
    # Diaper block
    dy = sy - 18 - sh_rows * sh_row_h - 24
    pdf.text(left, dy + 2, "Diapers", 9, bold=True, color=_col("accent"))
    dh_headers = ["Time", "Wet", "Dirty", "Mixed", "Notes"]
    dh_widths = [60, 50, 50, 50, right - left - 210]
    dh_rows = 5
    dh_row_h = 22
    pdf.fill(left, dy - 18, right - left, 18, _col("soft"))
    pdf.rect(left, dy - 18, right - left, 18, color=_col("line"), width=0.6)
    x = left
    for htxt, w in zip(dh_headers, dh_widths):
        pdf.text(x + 3, dy - 13, htxt, 7, bold=True, color=_col("accent"))
        x += w
    for r in range(dh_rows):
        y = dy - 18 - (r + 1) * dh_row_h
        pdf.rect(left, y, right - left, dh_row_h, color=_col("line"), width=0.4)
        x = left
        for wi, w in enumerate(dh_widths):
            pdf.line(x, y, x, y + dh_row_h, color=_col("line"), width=0.3)
            if wi in (1, 2, 3):
                pdf.rect(x + w / 2 - 5, y + dh_row_h / 2 - 5, 10, 10, color=_col("line"), width=0.5)
            x += w
    # Notes fill bottom
    ny = dy - 18 - dh_rows * dh_row_h - 24
    pdf.text(left, ny + 2, "Notes / mood / supplies needed", 9, bold=True, color=_col("accent"))
    for i in range(4):
        ly = ny - 14 - i * 16
        if ly > 64:
            pdf.line(left, ly, right, ly, color=_col("line"), width=0.5)


# Backward-compatible aliases for tests/imports that may reference old names
def _feeding_log_page(pdf: Any, norm: Dict[str, Any], page_no: int) -> None:
    _daily_log_page(pdf, norm, page_no)


def _sleep_log_page(pdf: Any, norm: Dict[str, Any], page_no: int) -> None:
    _daily_log_page(pdf, norm, page_no + DAILY_LOG_PAGES)
    for r in range(16):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w


def _diaper_log_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Diaper Log", "Record diaper changes and quick notes.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    headers = ["Date", "Time", "Wet", "Dirty", "Mixed", "Notes"]
    widths = [55, 55, 45, 45, 45, right - left - 245]
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
        for wi, w in enumerate(widths):
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            if wi in (2, 3, 4):
                pdf.rect(x + w / 2 - 5, y + row_h / 2 - 5, 10, 10, color=_col("line"), width=0.5)
            x += w


def _daily_checklist_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Daily Routine Checklist", "A simple daily checklist for newborn routines.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    items = [
        "Morning feeding noted", "Daytime feedings noted", "Evening feeding noted",
        "Naps and sleep noted", "Diaper changes noted", "Tummy time",
        "Bath / hygiene", "Fresh clothes / swaddle", "Supplies restocked",
        "Important notes written down",
    ]
    row_h = (top - bottom - 22) / 7
    headers = ["Task"] + [d[:3] for d in WEEK_DAYS]
    widths = [right - left - 7 * 44] + [44] * 7
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7.5, bold=True, color=_col("accent"))
        x += w
    rows = items[:7]
    for r, item in enumerate(rows):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for wi, w in enumerate(widths):
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            if wi == 0:
                pdf.text(x + 4, y + row_h / 2 - 3, item[:45], 8, color=_col("ink"))
            else:
                pdf.rect(x + w / 2 - 5, y + row_h / 2 - 5, 10, 10, color=_col("line"), width=0.5)
            x += w
    # extra notes
    pdf.text(left, bottom + 8, "Notes: ____________________________________________________________________________________", 8, color=_col("muted"))


def _doctor_notes_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Doctor Visit Notes", "Write questions, visit notes, and follow-up dates.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    y = pdf.h - 128
    sections = [
        ("Visit date / place", 1), ("Questions to ask", 4),
        ("Notes from the visit", 5), ("Follow-up date / reminders", 2),
        ("Medication or care reminders (as given by your care team)", 3),
    ]
    for label, lines in sections:
        pdf.text(left, y, label, 9, bold=True, color=_col("accent"))
        y -= 14
        for _ in range(lines):
            pdf.line(left, y, right, y, color=_col("line"), width=0.6)
            y -= 25
        y -= 6
        if y < 90:
            break


def _milestones_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Milestones & Memories", "Record special moments and observations.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    headers = ["Date", "Age / Week", "Milestone or memory", "Notes"]
    widths = [60, 70, right - left - 60 - 70 - 160, 160]
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


def _vaccine_date_table_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Vaccine Date Table", "Dates only. No medical advice. Follow your care team's schedule.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 132
    bottom = 80
    headers = ["Vaccine / Visit name", "Recommended age window", "Date planned", "Date done", "Notes"]
    widths = [150, 120, 80, 80, right - left - 430]
    row_h = (top - bottom - 22) / 14
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7, bold=True, color=_col("accent"))
        x += w
    for r in range(14):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w
        pdf.line(left + 6, y + row_h / 2 - 4, left + 130, y + row_h / 2 - 4, color=_col("line"), width=0.4)
    pdf.text(left, 64, "This table is for date tracking only. Ask your child's care team for medical guidance.", 8, color=_col("muted"))


def _weekly_overview_page(pdf: Any, norm: Dict[str, Any], week_no: int = 1) -> None:
    _heading(pdf, f"Weekly Summary — Week {week_no}", "A quick weekly snapshot of routines, patterns, and questions.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 170
    headers = ["Day"] + ["Feeding", "Sleep", "Diapers", "Notes"]
    widths = [70, 90, 90, 70, right - left - 320]
    row_h = (top - bottom - 22) / 7
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7.5, bold=True, color=_col("accent"))
        x += w
    for r, day in enumerate(WEEK_DAYS):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w
        pdf.text(left + 4, y + row_h / 2 - 3, day, 7.5, color=_col("ink"))
    # bottom notes
    pdf.text(left, bottom - 10, "Patterns noticed / questions for care team:", 9, bold=True, color=_col("accent"))
    for i in range(5):
        ly = bottom - 26 - i * 18
        if ly > 64:
            pdf.line(left, ly, right, ly, color=_col("line"), width=0.5)


def _important_contacts_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Important Contacts & Notes", "Keep useful contacts and reminders in one place.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    y = pdf.h - 128
    sections = [
        ("Care team / clinic contacts", 4),
        ("Emergency contacts", 3),
        ("Baby supplies checklist", 4),
        ("Other notes", 5),
    ]
    for label, lines in sections:
        pdf.text(left, y, label, 9, bold=True, color=_col("accent"))
        y -= 14
        for _ in range(lines):
            pdf.line(left, y, right, y, color=_col("line"), width=0.6)
            y -= 25
        y -= 6
        if y < 90:
            break


def _build_pdf(path: Path, paper: str, norm: Dict[str, Any]) -> None:
    size = PAPER_SIZES[paper]
    pdf = pdf_design.Pdf(size, footer=f"{norm['title']} · Printable routine tracker", style=_STYLE["name"])
    pdf.new_page(); _title_page(pdf, norm)
    for day in range(1, DAILY_LOG_PAGES + 1):
        pdf.new_page(); _daily_log_page(pdf, norm, day)
    for week in range(1, 6):
        pdf.new_page(); _weekly_overview_page(pdf, norm, week)
    pdf.new_page(); _milestones_page(pdf, norm)
    pdf.new_page(); _milestones_page(pdf, norm)  # extra milestone page
    pdf.new_page(); _doctor_notes_page(pdf, norm)
    pdf.new_page(); _vaccine_date_table_page(pdf, norm)
    pdf.new_page(); _important_contacts_page(pdf, norm)
    pdf.save(str(path), title=norm["title"])


def _readme(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"{norm['title']}",
        "=" * 52,
        "",
        "WHAT IS INSIDE",
        "- A4 PDF and US Letter PDF",
        f"- {DAILY_LOG_PAGES} daily log pages (feeding, sleep, diapers, notes)",
        "- 5 weekly summary pages",
        "- 2 milestones and memories pages",
        "- Doctor visit notes page",
        "- Vaccine date table page (dates only, no medical advice)",
        "- Important contacts and notes page",
        "- cover.png and three preview images",
        "",
        "HOW TO PRINT",
        "1. Open the PDF for your paper size.",
        "2. Print at 100% scale (Actual Size).",
        "3. Reprint log pages as needed for daily use.",
        "4. Use the date table for tracking only and follow your care team for guidance.",
        "",
        "PAGE COUNT",
        f"- {1 + DAILY_LOG_PAGES + 5 + 5} pages per PDF (A4 and US Letter included)",
        "",
        "LICENSE",
        "Personal use only. Do not resell or redistribute the files.",
    ]) + "\n"


def _license(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"LICENSE — {norm['title']}",
        "",
        "This printable pack is for personal use only.",
        "You may print copies for yourself and your household.",
        "You may not resell, redistribute, share, or claim the files as your own.",
        "All layouts and text are generated for the Chilli Gavva product library.",
        "",
    ])


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for the baby tracker builder")
    norm = normalize_spec(spec)
    _use_theme(spec)
    out_path = Path(out_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="baby_build_"))
    names: List[str] = []
    try:
        for paper in PAPER_SIZES:
            name = f"baby_tracker_{paper}.pdf"
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
        page_count = 1 + DAILY_LOG_PAGES + 5 + 5
        manifest = {
            "format": FORMAT,
            "title": norm["title"],
            "baby_name": norm["baby_name"],
            "caregiver": norm["caregiver"],
            "paper_sizes": list(PAPER_SIZES),
            "pages_per_pdf": page_count,
            "daily_log_pages": DAILY_LOG_PAGES,
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
