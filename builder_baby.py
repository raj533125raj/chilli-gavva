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


def _cover_image(path: Path, norm: Dict[str, Any], preview: int = 0) -> None:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for baby tracker images")
    w, h = 1200, 800
    bg = "#fdfaf8"
    ink = _col("ink")
    accent = _col("accent")
    soft = _col("soft")
    line = _col("line")
    im = Image.new("RGB", (w, h), color=bg)
    draw = ImageDraw.Draw(im)
    draw.rectangle([24, 24, w - 24, h - 24], outline=accent, width=3)
    draw.rectangle([24, 24, w - 24, 72], fill=accent)
    draw.text((48, 38), "CHILLI GAVVA · NEWBORN TRACKER", fill="#ffffff", font=_font(20, True))
    draw.text((60, 120), norm["title"][:42], fill=ink, font=_font(42, True))
    draw.text((60, 178), "A4 + US Letter · Daily routine pages", fill=accent, font=_font(22))
    labels = ["Cover", "Feeding & Sleep Log", "Diaper & Checklist", "Milestones & Dates"]
    kind = preview % len(labels)

    if kind == 0:
        draw.text((60, 240), "DAILY ROUTINE", fill=accent, font=_font(16, True))
        cards = ["Feeding log", "Sleep log", "Diaper log", "Routine checklist", "Doctor notes", "Milestones"]
        for i, label in enumerate(cards):
            col = i % 3
            row = i // 3
            x = 60 + col * 360
            y = 280 + row * 170
            draw.rounded_rectangle([x, y, x + 320, y + 130], radius=12, fill="#ffffff", outline=line, width=2)
            draw.text((x + 18, y + 16), label, fill=accent, font=_font(15, True))
            for ln in range(3):
                draw.line([x + 18, y + 52 + ln * 22, x + 290, y + 52 + ln * 22], fill=line, width=1)
    elif kind == 1:
        draw.text((60, 240), "FEEDING & SLEEP LOG", fill=accent, font=_font(16, True))
        headers = ["Time", "Feed type", "Amount / Side", "Sleep start", "Sleep end", "Notes"]
        widths = [90, 130, 150, 130, 130, 250]
        x0, y0 = 60, 280
        for c, label in enumerate(headers):
            x = x0 + sum(widths[:c])
            draw.rectangle([x, y0, x + widths[c], y0 + 34], outline=line, fill=soft)
            draw.text((x + 8, y0 + 8), label[:14], fill=accent, font=_font(11, True))
        for r in range(7):
            y = y0 + 34 + r * 46
            for c in range(len(headers)):
                x = x0 + sum(widths[:c])
                draw.rectangle([x, y, x + widths[c], y + 46], outline=line, fill="#ffffff")
    elif kind == 2:
        draw.text((60, 240), "DIAPER LOG & DAILY CHECKLIST", fill=accent, font=_font(16, True))
        draw.rounded_rectangle([60, 280, 560, 620], radius=12, fill="#ffffff", outline=line, width=2)
        draw.text((84, 300), "Diaper Log", fill=accent, font=_font(15, True))
        for r in range(6):
            y = 344 + r * 40
            draw.line([84, y, 530, y], fill=line, width=1)
        draw.rounded_rectangle([600, 280, 1130, 620], radius=12, fill=soft, outline=line, width=2)
        draw.text((624, 300), "Daily Checklist", fill=accent, font=_font(15, True))
        for i, item in enumerate(["Feeding times noted", "Sleep noted", "Diapers noted", "Tummy time", "Supplies ready"]):
            y = 350 + i * 48
            draw.rectangle([624, y, 648, y + 24], outline=accent, width=2, fill="#ffffff")
            draw.text((660, y + 2), item, fill=ink, font=_font(13))
    else:
        draw.text((60, 240), "MILESTONES & DATE TABLE", fill=accent, font=_font(16, True))
        draw.rounded_rectangle([60, 280, 560, 620], radius=12, fill="#ffffff", outline=line, width=2)
        draw.text((84, 300), "Milestones", fill=accent, font=_font(15, True))
        for r in range(6):
            y = 344 + r * 40
            draw.line([84, y, 530, y], fill=line, width=1)
        draw.rounded_rectangle([600, 280, 1130, 620], radius=12, fill="#ffffff", outline=line, width=2)
        draw.text((624, 300), "Vaccine Date Table", fill=accent, font=_font(15, True))
        draw.text((624, 330), "Dates only - no medical advice", fill=_col("muted"), font=_font(11))
        for r in range(5):
            y = 360 + r * 44
            draw.line([624, y, 1100, y], fill=line, width=1)

    draw.text((60, 700), "Feeding · Sleep · Diapers · Daily checklist · Doctor notes · Milestones · Date table", fill=_col("muted"), font=_font(15))
    draw.text((880, 720), labels[kind], fill=accent, font=_font(14, True))
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


def _feeding_log_page(pdf: Any, norm: Dict[str, Any], page_no: int) -> None:
    _heading(pdf, f"Feeding Log — Page {page_no}", "Record feeding time, type, amount or side, and notes.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    headers = ["Date", "Time", "Feed type", "Amount / Side", "Duration", "Notes"]
    widths = [55, 50, 80, 90, 60, right - left - 335]
    row_h = (top - bottom - 22) / 16
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7.5, bold=True, color=_col("accent"))
        x += w
    for r in range(16):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w


def _sleep_log_page(pdf: Any, norm: Dict[str, Any], page_no: int) -> None:
    _heading(pdf, f"Sleep Log — Page {page_no}", "Track sleep start, wake time, total time, and notes.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    headers = ["Date", "Sleep start", "Wake time", "Total time", "Where", "Notes"]
    widths = [55, 75, 75, 70, 70, right - left - 345]
    row_h = (top - bottom - 22) / 16
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7.5, bold=True, color=_col("accent"))
        x += w
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
            y -= 20
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


def _weekly_overview_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Weekly Overview", "A quick weekly snapshot of routines and notes.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
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
            y -= 20
        y -= 6
        if y < 90:
            break


def _build_pdf(path: Path, paper: str, norm: Dict[str, Any]) -> None:
    size = PAPER_SIZES[paper]
    pdf = pdf_design.Pdf(size, footer=f"{norm['title']} · Printable routine tracker", style=_STYLE["name"])
    pdf.new_page(); _title_page(pdf, norm)
    pdf.new_page(); _feeding_log_page(pdf, norm, 1)
    pdf.new_page(); _feeding_log_page(pdf, norm, 2)
    pdf.new_page(); _sleep_log_page(pdf, norm, 1)
    pdf.new_page(); _sleep_log_page(pdf, norm, 2)
    pdf.new_page(); _diaper_log_page(pdf, norm)
    pdf.new_page(); _daily_checklist_page(pdf, norm)
    pdf.new_page(); _weekly_overview_page(pdf, norm)
    pdf.new_page(); _doctor_notes_page(pdf, norm)
    pdf.new_page(); _milestones_page(pdf, norm)
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
        "- 2 feeding log pages (time, type, amount/side)",
        "- 2 sleep log pages",
        "- Diaper log page",
        "- Daily routine checklist page",
        "- Weekly overview page",
        "- Doctor visit notes page",
        "- Milestones and memories page",
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
        "- 12 pages per PDF (A4 and US Letter included)",
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
        manifest = {
            "format": FORMAT,
            "title": norm["title"],
            "baby_name": norm["baby_name"],
            "caregiver": norm["caregiver"],
            "paper_sizes": list(PAPER_SIZES),
            "pages_per_pdf": 12,
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
