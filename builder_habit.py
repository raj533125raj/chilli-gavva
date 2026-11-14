"""builder_habit.py — Habit and mood tracker printable pack.

FORMAT = 'habit_tracker'
KEYWORDS = ['habit tracker', 'mood tracker', 'habit and mood', 'streak tracker']

Builds a deterministic A4 + US Letter printable pack with a monthly habit grid
(30/31/28/29 days automatic), streak boxes, a mood legend and mood log,
reflection pages, and a yearly overview. Cover and three preview images are
drawn with Pillow only. No stock images, no currency amounts, no network.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    HAS_PILLOW = False

import pdf_design

FORMAT = "habit_tracker"
KEYWORDS = [
    "habit tracker", "mood tracker", "habit and mood", "monthly habit tracker",
    "streak tracker", "printable habit tracker",
]

DEFAULT_TITLE = "Monthly Habit & Mood Tracker"
DEFAULT_YEAR = 2027
DEFAULT_MONTH = 1
DEFAULT_HABITS = [
    "Drink water", "Move 20 minutes", "Read 10 pages",
    "Sleep on time", "No late scrolling", "Journal",
]
MOODS = [
    ("Great", "#2f7d78"), ("Good", "#6e8b75"), ("Okay", "#c9853b"),
    ("Low", "#a35c6a"), ("Tough", "#555f6b"),
]
PAPER_SIZES = {"a4": "A4", "letter": "Letter"}
REFLECTION_PROMPTS = [
    "What went well this month?",
    "What drained my energy?",
    "What is my one focus for next month?",
]


def _clean_title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    return title or DEFAULT_TITLE


def _clean_habits(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return list(DEFAULT_HABITS)
    habits: List[str] = []
    for item in value:
        text = " ".join(str(item).split())
        if text and text not in habits:
            habits.append(text)
        if len(habits) == 8:
            break
    return habits or list(DEFAULT_HABITS)


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

    try:
        month = int(spec.get("month", DEFAULT_MONTH))
    except (TypeError, ValueError):
        raise ValueError("month must be an integer")
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")

    habits = _clean_habits(spec.get("habits"))
    mood_labels = []
    raw_moods = spec.get("moods")
    if isinstance(raw_moods, (list, tuple)):
        for item in raw_moods:
            text = " ".join(str(item).split())
            if text and text not in mood_labels:
                mood_labels.append(text)
            if len(mood_labels) == 5:
                break
    if not mood_labels:
        mood_labels = [name for name, _color in MOODS]

    days_in_month = calendar.monthrange(year, month)[1]
    first_weekday = calendar.monthrange(year, month)[0]

    return {
        "title": title,
        "year": year,
        "month": month,
        "month_name": calendar.month_name[month],
        "days_in_month": days_in_month,
        "first_weekday": first_weekday,
        "habits": habits,
        "moods": mood_labels,
    }


def _safe_filename(text: str, limit: int = 40) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    slug = "-".join(part for part in slug.split("-") if part)
    return (slug[:limit].rstrip("-") or "habit-pack")


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
        raise RuntimeError("Pillow is required for habit tracker images")
    w, h = 1200, 800
    bg = "#f8f6f1"
    ink = "#1f2933"
    accent = "#2f7d78"
    soft = "#e6f2f1"
    im = Image.new("RGB", (w, h), color=bg)
    draw = ImageDraw.Draw(im)
    draw.rectangle([24, 24, w - 24, h - 24], outline=accent, width=3)
    draw.rectangle([24, 24, w - 24, 72], fill=accent)
    draw.text((48, 38), "CHILLI GAVVA · PRINTABLE HABIT PACK", fill="#ffffff", font=_font(20, True))
    title = norm["title"]
    draw.text((60, 120), title[:42], fill=ink, font=_font(42, True))
    draw.text((60, 178), f"{norm['month_name']} {norm['year']} · A4 + US Letter", fill=accent, font=_font(22))

    x0, y0, cell_w, cell_h = 60, 250, 64, 42
    draw.text((x0, y0 - 34), "HABIT GRID", fill=accent, font=_font(16, True))
    for r in range(min(6, len(norm["habits"]))):
        draw.text((x0, y0 + r * 52), norm["habits"][r][:18], fill=ink, font=_font(13))
        for c in range(10):
            draw.rectangle(
                [x0 + 210 + c * cell_w, y0 + r * 52, x0 + 210 + c * cell_w + 42, y0 + r * 52 + 34],
                outline="#b7c9c7", fill="#ffffff" if (r + c) % 4 else soft,
            )

    panel_x = 820
    draw.rounded_rectangle([panel_x, 250, panel_x + 310, 650], radius=14, fill="#ffffff", outline="#cfdedc", width=2)
    draw.text((panel_x + 24, 276), "MOOD LEGEND", fill=accent, font=_font(17, True))
    for i, (name, color) in enumerate(MOODS):
        draw.ellipse([panel_x + 28, 322 + i * 48, panel_x + 52, 346 + i * 48], fill=color)
        draw.text((panel_x + 66, 326 + i * 48), name, fill=ink, font=_font(15))
    draw.text((panel_x + 24, 578), "One box a day", fill="#5f6b73", font=_font(13))

    draw.text((60, 690), "Monthly grid · Streak boxes · Mood log · Reflection · Year overview", fill="#5f6b73", font=_font(16))
    preview_labels = ["Cover", "Monthly Tracker", "Mood Log", "Reflection & Year"]
    draw.text((880, 720), preview_labels[preview % len(preview_labels)], fill=accent, font=_font(14, True))
    im.save(str(path), format="PNG", optimize=True)


def _heading(pdf: Any, text: str, subtitle: str = "") -> None:
    pdf.text(pdf_design.MARGIN, pdf.h - 62, text, 20, bold=True, color="#1f2933")
    if subtitle:
        pdf.text(pdf_design.MARGIN, pdf.h - 84, subtitle, 10, color="#5f6b73")
    pdf.line(pdf_design.MARGIN, pdf.h - 96, pdf.w - pdf_design.MARGIN, pdf.h - 96, color="#c9d3d6")


def _title_page(pdf: Any, norm: Dict[str, Any]) -> None:
    lines, size = pdf_design.fit_text(norm["title"], 30, pdf.w - 2 * pdf_design.MARGIN, bold=True, max_lines=3, min_size=16)
    y = pdf.h / 2 + 80
    for line in lines:
        pdf.text_center(pdf.w / 2, y, line, size, bold=True, color="#1f2933")
        y -= size * 1.25
    pdf.text_center(pdf.w / 2, y - 18, f"{norm['month_name']} {norm['year']}", 18, color="#2f7d78")
    pdf.text_center(pdf.w / 2, y - 48, "Habit grid · Streak boxes · Mood log · Reflection · Year overview", 10, color="#5f6b73")
    pdf.line(pdf.w / 2 - 80, y - 76, pdf.w / 2 + 80, y - 76, color="#2f7d78", width=1.2)


def _tracker_page(pdf: Any, norm: Dict[str, Any]) -> None:
    days = norm["days_in_month"]
    habits = norm["habits"]
    _heading(pdf, f"Habit Tracker — {norm['month_name']} {norm['year']}", "Mark each day: done, rest, or skipped.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    label_w = 126
    day_w = (right - left - label_w) / days
    row_h = min(38.0, (top - 120) / max(1, len(habits)))

    pdf.text(left, top + 8, "Habit", 8, bold=True, color="#5f6b73")
    for day in range(1, days + 1):
        x = left + label_w + (day - 1) * day_w
        weekday = (norm["first_weekday"] + day - 1) % 7
        if weekday >= 5:
            pdf.fill(x, top - row_h * len(habits), day_w, row_h * len(habits), "#f1f6f6")
        pdf.text_center(x + day_w / 2, top + 8, str(day), 6.5, color="#5f6b73")

    for r, habit in enumerate(habits):
        y = top - (r + 1) * row_h
        pdf.text(left + 2, y + row_h / 2 - 4, habit[:24], 8.5, color="#1f2933")
        for day in range(1, days + 1):
            x = left + label_w + (day - 1) * day_w
            pdf.rect(x, y, day_w, row_h, color="#c9d3d6", width=0.4)

    pdf.text(left, 96, "Streak tip: colour the small boxes on the mood page for every day you keep your top habit.", 9, color="#5f6b73")


def _mood_page(pdf: Any, norm: Dict[str, Any]) -> None:
    days = norm["days_in_month"]
    _heading(pdf, "Mood Log", "Colour one mood box for each day.")
    x = pdf_design.MARGIN
    y = pdf.h - 150
    for i, (name, color) in enumerate(MOODS[:5]):
        label = norm["moods"][i] if i < len(norm["moods"]) else name
        pdf.circle(x + 8 + i * 96, y + 4, 5, fill=color)
        pdf.text(x + 18 + i * 96, y, label, 9, color="#1f2933")

    top = pdf.h - 190
    columns = 2
    rows = (days + columns - 1) // columns
    col_w = (pdf.w - 2 * pdf_design.MARGIN) / columns
    row_h = min(20.0, (top - 190) / rows)
    for index in range(days):
        col = index // rows
        row = index % rows
        cx = pdf_design.MARGIN + col * col_w
        cy = top - (row + 1) * row_h
        pdf.text(cx, cy + 4, f"{index + 1:02d}", 7, color="#5f6b73")
        for m in range(5):
            bx = cx + 24 + m * (col_w - 34) / 5
            pdf.rect(bx, cy, (col_w - 34) / 5 - 3, row_h - 2, color="#c9d3d6", width=0.5)

    pdf.text(pdf_design.MARGIN, 130, "Streak challenge — one box per day", 10, bold=True, color="#2f7d78")
    box_w = (pdf.w - 2 * pdf_design.MARGIN) / days
    for day in range(days):
        pdf.rect(pdf_design.MARGIN + day * box_w, 104, box_w - 1.5, box_w - 1.5, color="#9fb8b5", width=0.5)


def _reflection_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Monthly Reflection", "Five quiet minutes to close the month honestly.")
    y = pdf.h - 140
    for prompt in REFLECTION_PROMPTS:
        pdf.text(pdf_design.MARGIN, y, prompt, 11, bold=True, color="#1f2933")
        y -= 20
        for _ in range(7):
            pdf.line(pdf_design.MARGIN, y, pdf.w - pdf_design.MARGIN, y, color="#d7dfe1")
            y -= 25
        y -= 20


def _year_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, f"Yearly Overview — {norm['year']}", "A tiny view of every month.")
    left = pdf_design.MARGIN
    top = pdf.h - 135
    gap = 14
    col_w = (pdf.w - 2 * pdf_design.MARGIN - 3 * gap) / 4
    row_h = (top - 90 - 2 * gap) / 3
    cal = calendar.Calendar(firstweekday=0)
    for month in range(1, 13):
        col = (month - 1) % 4
        row = (month - 1) // 4
        x = left + col * (col_w + gap)
        y = top - (row + 1) * row_h - row * gap
        pdf.rect(x, y, col_w, row_h, color="#c9d3d6", width=0.6)
        pdf.text(x + 6, y + row_h - 14, calendar.month_abbr[month], 9, bold=True, color="#2f7d78")
        weeks = cal.monthdayscalendar(norm["year"], month)
        cell_w = (col_w - 12) / 7
        cell_h = (row_h - 26) / 6
        for wi, week in enumerate(weeks):
            for di, day in enumerate(week):
                if day:
                    pdf.text_center(x + 6 + di * cell_w + cell_w / 2,
                                    y + row_h - 28 - wi * cell_h, str(day), 5.2, color="#5f6b73")


def _build_pdf(path: Path, paper: str, norm: Dict[str, Any]) -> None:
    size = PAPER_SIZES[paper]
    pdf = pdf_design.Pdf(size, footer=f"{norm['title']} · {norm['month_name']} {norm['year']}", style="A")
    for page_fn in (_title_page, _tracker_page, _mood_page, _reflection_page, _year_page):
        pdf.new_page()
        page_fn(pdf, norm)
    pdf.save(str(path), title=norm["title"])


def _readme(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"{norm['title']} — {norm['month_name']} {norm['year']}",
        "=" * 52,
        "",
        "WHAT IS INSIDE",
        "- A4 PDF and US Letter PDF",
        "- Monthly habit grid with automatic day count",
        "- Streak boxes and a five-mood colour legend",
        "- Monthly reflection prompts and a yearly overview",
        "- cover.png and three preview images",
        "",
        "HOW TO PRINT",
        "1. Open the PDF for your paper size.",
        "2. Print at 100% scale (Actual Size).",
        "3. Use a pen, highlighter, or stickers for each habit.",
        "4. Reprint the monthly grid every new month.",
        "",
        "TIPS",
        "- Keep the tracker visible where the habit happens.",
        "- Mark rest days honestly; the goal is consistency, not perfection.",
        "- Use the reflection page at the end of the month.",
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
        "All layouts and text are generated by the Chilli Gavva robot.",
        "",
    ])


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    """Build the habit and mood tracker pack and return created file names."""
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for the habit tracker builder")
    norm = normalize_spec(spec)
    out_path = Path(out_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="habit_build_"))
    names: List[str] = []
    try:
        for paper in PAPER_SIZES:
            name = f"habit_tracker_{paper}.pdf"
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
            "year": norm["year"],
            "month": norm["month"],
            "month_name": norm["month_name"],
            "days_in_month": norm["days_in_month"],
            "habits": norm["habits"],
            "moods": norm["moods"],
            "paper_sizes": list(PAPER_SIZES),
            "pages_per_pdf": 5,
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
