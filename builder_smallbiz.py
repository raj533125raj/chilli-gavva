"""Printable home-business order and inventory log pack."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Union

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None
    HAS_PILLOW = False

import pdf_design

FORMAT = "small_business_log"
KEYWORDS = [
    "order log", "order tracker", "small business planner", "bakery order",
    "inventory log", "home business planner", "customer order form",
]

DEFAULT_TITLE = "Home Business Order & Inventory Log"
PAPER_SIZES = {"a4": "A4", "letter": "Letter"}

_COLORS = {
    "ink": "#23343d", "accent": "#287d78", "muted": "#66777d",
    "line": "#b9cbd0", "soft": "#edf6f5", "warm": "#d39a55",
}


def normalize_spec(spec: Any) -> Dict[str, Any]:
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dictionary")
    title = " ".join(str(spec.get("title") or DEFAULT_TITLE).split())
    business = " ".join(str(spec.get("business") or "My Home Business").split())
    return {"title": title or DEFAULT_TITLE, "business": business or "My Home Business"}


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
        raise RuntimeError("Pillow is required for small business log images")
    w, h = 1200, 800
    im = Image.new("RGB", (w, h), "#f7faf9")
    draw = ImageDraw.Draw(im)
    accent = "#287d78"
    draw.rectangle([24, 24, w - 24, h - 24], outline=accent, width=5)

    if preview == 0:
        draw.rectangle([24, 24, w - 24, 110], fill=accent)
        draw.text((48, 48), "CHILLI GAVVA · HOME BUSINESS PACK", fill="#ffffff", font=_font(22, True))
        draw.text((60, 145), norm["title"][:46], fill="#23343d", font=_font(40, True))
        draw.text((60, 198), norm["business"][:40], fill=accent, font=_font(24))
        draw.text((60, 238), "35 printable pages · A4 + US Letter", fill="#66777d", font=_font(18, True))
        page_specs = [
            ("ORDER FORM", ["Customer", "Item", "Qty", "Due"]),
            ("INVENTORY", ["Item", "Opening", "Used", "Left"]),
            ("MONTHLY REVIEW", ["Orders", "Customers", "Packed", "Issues"]),
        ]
        left, top = 60, 300
        for i, (label, headers) in enumerate(page_specs):
            x = left + i * 360
            draw.rounded_rectangle([x, top, x + 320, 690], radius=16, fill="#ffffff", outline="#b9cbd0", width=3)
            draw.rectangle([x + 18, top + 18, x + 302, top + 72], fill="#edf6f5")
            draw.text((x + 32, top + 34), label, fill=accent, font=_font(18, True))
            for r in range(7):
                yy = top + 100 + r * 56
                draw.line((x + 24, yy, x + 296, yy), fill="#b9cbd0", width=2)
            for c, header in enumerate(headers):
                hx = x + 26 + c * 70
                draw.text((hx, top + 82), header[:8], fill="#66777d", font=_font(10, True))
        draw.text((60, 724), "Real order, stock, customer and monthly review pages", fill=accent, font=_font(16, True))
    elif preview == 1:
        draw.rectangle([24, 24, w - 24, 100], fill=accent)
        draw.text((55, 50), "CUSTOMER ORDER FORM · REAL PAGE", fill="#ffffff", font=_font(24, True))
        draw.text((55, 130), "Order details", fill="#23343d", font=_font(30, True))
        draw.text((55, 170), "Complete one row for each new order.", fill="#66777d", font=_font(18))
        left, top = 55, 220
        headers = ["Date", "Customer", "Item", "Qty", "Due", "Status"]
        widths = [150, 220, 260, 110, 180, 170]
        x = left
        for i, header in enumerate(headers):
            draw.rectangle([x, top, x + widths[i], top + 55], fill="#edf6f5", outline="#b9cbd0")
            draw.text((x + 12, top + 18), header, fill=accent, font=_font(15, True))
            x += widths[i]
        x = left
        for r in range(8):
            y = top + 55 + r * 52
            x = left
            for i, width in enumerate(widths):
                draw.rectangle([x, y, x + width, y + 52], outline="#b9cbd0")
                x += width
    elif preview == 2:
        draw.rectangle([24, 24, w - 24, 100], fill="#287d78")
        draw.text((55, 50), "INVENTORY LOG · REAL PAGE", fill="#ffffff", font=_font(24, True))
        draw.text((55, 130), "Stock count", fill="#23343d", font=_font(30, True))
        left, top = 55, 220
        headers = ["Item", "Opening", "Added", "Used", "Closing", "Check"]
        widths = [260, 150, 150, 150, 150, 180]
        x = left
        for i, header in enumerate(headers):
            draw.rectangle([x, top, x + widths[i], top + 55], fill="#edf6f5", outline="#b9cbd0")
            draw.text((x + 12, top + 18), header, fill=accent, font=_font(15, True))
            x += widths[i]
        for r in range(8):
            y = top + 55 + r * 52
            x = left
            for width in widths:
                draw.rectangle([x, y, x + width, y + 52], outline="#b9cbd0")
                x += width
    else:
        draw.rectangle([24, 24, w - 24, 100], fill="#287d78")
        draw.text((55, 50), "MONTHLY REVIEW · REAL PAGE", fill="#ffffff", font=_font(24, True))
        draw.text((55, 130), "Count this month", fill="#23343d", font=_font(30, True))
        labels = ["Orders received", "Orders completed", "Open orders", "New customers",
                  "Repeat customers", "Items packed", "Suppliers contacted", "Issues resolved"]
        for i, label in enumerate(labels):
            col, row = i % 2, i // 2
            x, y = 55 + col * 550, 210 + row * 125
            draw.rounded_rectangle([x, y, x + 500, y + 105], radius=12, fill="#ffffff", outline="#b9cbd0", width=3)
            draw.text((x + 18, y + 18), label, fill="#23343d", font=_font(17, True))
            draw.rectangle([x + 370, y + 18, x + 470, y + 82], fill="#edf6f5", outline="#b9cbd0")
    im.save(str(path), format="PNG", optimize=True)


def _heading(pdf: Any, title: str, subtitle: str = "") -> None:
    pdf.text(pdf_design.MARGIN, pdf.h - 62, title, 18, bold=True, color=_COLORS["ink"])
    if subtitle:
        pdf.text(pdf_design.MARGIN, pdf.h - 84, subtitle, 9, color=_COLORS["muted"])
    pdf.line(pdf_design.MARGIN, pdf.h - 96, pdf.w - pdf_design.MARGIN, pdf.h - 96, color=_COLORS["line"])


def _lined_box(pdf: Any, x: float, y: float, w: float, h: float, title: str = "") -> None:
    pdf.rect(x, y, w, h, color=_COLORS["line"], width=0.7)
    if title:
        pdf.text(x + 8, y + h - 18, title, 9, bold=True, color=_COLORS["accent"])
    for yy in range(1, max(1, int(h // 24))):
        line_y = y + h - 24 * yy
        if line_y > y + 8:
            pdf.line(x + 8, line_y, x + w - 8, line_y, color=_COLORS["line"], width=0.35)


def _cover(pdf: Any, norm: Dict[str, Any]) -> None:
    pdf.fill(pdf_design.MARGIN, pdf.h - 150, pdf.w - 2 * pdf_design.MARGIN, 85, _COLORS["soft"])
    lines, size = pdf_design.fit_text(norm["title"], 28, pdf.w - 2 * pdf_design.MARGIN - 40,
                                      bold=True, max_lines=3, min_size=18)
    y = pdf.h - 165
    for line in lines:
        pdf.text_center(pdf.w / 2, y, line, size, bold=True, color=_COLORS["ink"])
        y -= size * 1.25
    pdf.text_center(pdf.w / 2, y - 12, norm["business"], 15, color=_COLORS["accent"])
    pdf.text_center(pdf.w / 2, 170, "Order form · Customer list · Inventory · Suppliers · Weekly and monthly review", 9,
                    color=_COLORS["muted"])


def _start(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Start Here", "Set up the pack once, then use the log pages weekly.")
    y = pdf.h - 130
    fields = ["Business name", "Main products or services", "Usual order days", "Supplies check day"]
    for label in fields:
        pdf.text(pdf_design.MARGIN, y, label, 10, bold=True, color=_COLORS["ink"])
        pdf.line(pdf_design.MARGIN + 175, y - 2, pdf.w - pdf_design.MARGIN, y - 2, color=_COLORS["line"])
        y -= 52
    pdf.text(pdf_design.MARGIN, y - 10, "Weekly workflow", 11, bold=True, color=_COLORS["accent"])
    y -= 38
    for step in ("1. Record each new order.", "2. Update inventory after packing.", "3. Check supplier needs.",
                 "4. Review open orders and follow-ups.", "5. Add a count to the monthly review."):
        pdf.text(pdf_design.MARGIN + 12, y, step, 10, color=_COLORS["ink"])
        y -= 46


def _table_page(pdf: Any, title: str, subtitle: str, headers: List[str], rows: int = 18) -> None:
    _heading(pdf, title, subtitle)
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 125
    col_w = (right - left) / len(headers)
    for i, header in enumerate(headers):
        x = left + i * col_w
        pdf.fill(x, top - 24, col_w, 24, _COLORS["soft"])
        pdf.text(x + 5, top - 16, header[:18], 7.5, bold=True, color=_COLORS["accent"])
        pdf.line(x, top - 24, x, top - 24 - rows * 30, color=_COLORS["line"], width=0.5)
    pdf.line(right, top - 24, right, top - 24 - rows * 30, color=_COLORS["line"], width=0.5)
    row_h = 30
    for r in range(rows):
        y = top - 24 - (r + 1) * row_h
        pdf.line(left, y, right, y, color=_COLORS["line"], width=0.4)


def _monthly_review(pdf: Any) -> None:
    _heading(pdf, "Monthly Review", "Counts only. Leave every value as a number, with no currency amount.")
    y = pdf.h - 140
    items = [
        ("Orders received", "Count"), ("Orders completed", "Count"), ("Open orders", "Count"),
        ("New customers", "Count"), ("Repeat customers", "Count"), ("Items packed", "Count"),
        ("Suppliers contacted", "Count"), ("Issues resolved", "Count"),
    ]
    for i, (label, unit) in enumerate(items):
        col = i % 2
        row = i // 2
        x = pdf_design.MARGIN + col * ((pdf.w - 2 * pdf_design.MARGIN) / 2)
        yy = y - row * 145
        _lined_box(pdf, x + 8, yy - 105, (pdf.w - 2 * pdf_design.MARGIN) / 2 - 24, 105, label)
        pdf.text(x + 18, yy - 49, unit, 8, color=_COLORS["muted"])


PAGES = [
    ("Cover", "Cover"),
    ("Start Here", "Set up"),
    ("Customer Order Form", "Order details"),
    ("Order Tracker", "Open and completed orders"),
    ("Customer List", "Customer contacts"),
    ("Inventory Log", "Stock counts"),
    ("Supplier List", "Supply contacts"),
    ("Weekly To-Do", "This week's actions"),
    ("Packing & Dispatch", "Packing steps"),
    ("Follow-Up Log", "Customer and supplier follow-ups"),
    ("Monthly Review", "Monthly counts"),
    ("Weekly Stock Check", "Stock review"),
    ("Notes & Improvements", "Ideas and fixes"),
    ("End of Month Checklist", "Close the month"),
]


def _build_pdf(path: Path, paper: str, norm: Dict[str, Any]) -> None:
    pdf = pdf_design.Pdf(PAPER_SIZES[paper], footer=f"{norm['title']} · {PAPER_SIZES[paper]} · for personal use", style="A")
    pdf.new_page()
    _cover(pdf, norm)
    pdf.new_page()
    _start(pdf, norm)
    for index in range(1, 11):
        pdf.new_page()
        _table_page(pdf, f"Customer Order Form {index}", "Record one order in each row.", ["Date", "Customer", "Item", "Qty", "Due", "Status"], 15)
    for index in range(1, 4):
        pdf.new_page()
        _table_page(pdf, f"Inventory Log {index}", "Record stock movement and closing count.", ["Item", "Opening", "Added", "Used", "Closing", "Check"], 15)
    for index in range(1, 4):
        pdf.new_page()
        _table_page(pdf, f"Customer List {index}", "Use one row per customer.", ["Name", "Contact", "First order", "Repeat?", "Notes"], 15)
    pdf.new_page()
    _table_page(pdf, "Supplier List", "Keep supplier details and recheck dates.", ["Supplier", "Product", "Contact", "Lead time", "Next check"], 15)
    for index in range(1, 5):
        pdf.new_page()
        _table_page(pdf, f"Weekly Planner {index}", "Plan the week, then mark each task complete.", ["Day", "Task", "Owner", "Done", "Next step"], 16)
    for index in range(1, 13):
        pdf.new_page()
        _heading(pdf, f"Monthly Review {index}", "Counts only. Leave every value as a number, with no currency amount.")
        y = pdf.h - 140
        items = [("Orders received", "Count"), ("Orders completed", "Count"), ("Open orders", "Count"),
                 ("New customers", "Count"), ("Repeat customers", "Count"), ("Items packed", "Count"),
                 ("Suppliers contacted", "Count"), ("Issues resolved", "Count")]
        for i, (label, unit) in enumerate(items):
            col, row = i % 2, i // 2
            x = pdf_design.MARGIN + col * ((pdf.w - 2 * pdf_design.MARGIN) / 2)
            yy = y - row * 145
            _lined_box(pdf, x + 8, yy - 105, (pdf.w - 2 * pdf_design.MARGIN) / 2 - 24, 105, label)
            pdf.text(x + 18, yy - 49, unit, 8, color=_COLORS["muted"])
    pdf.save(str(path), title=norm["title"])


def _readme(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"{norm['title']}",
        "=" * 52,
        "",
        "WHAT YOU GET",
        "- A4 PDF and US Letter PDF",
        "- 35 printable pages",
        "- Cover, order form, order tracker, customer list",
        "- Inventory and supplier logs",
        "- Weekly and monthly planning pages",
        "- Cover.png and three preview images",
        "",
        "HOW TO PRINT",
        "1. Open the PDF matching your paper.",
        "2. Print at 100% scale (Actual Size).",
        "3. Use one pack for one month or quarter.",
        "4. Keep completed pages for your records.",
        "",
        "LICENSE",
        "Personal use only. Do not resell or redistribute the files.",
    ]) + "\n"


def _license(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"LICENSE - {norm['title']}",
        "",
        "This printable pack is for personal use only.",
        "You may print copies for yourself and your household.",
        "You may not resell, redistribute, share, or claim the files as your own.",
        "",
    ])


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for small business log images")
    norm = normalize_spec(spec)
    out_path = Path(out_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="smallbiz_build_"))
    names: List[str] = []
    try:
        for paper in PAPER_SIZES:
            name = f"small_business_log_{paper}.pdf"
            _build_pdf(temp_dir / name, paper, norm)
            names.append(name)
        names.extend(["cover.png", "preview_1.png", "preview_2.png", "preview_3.png"])
        _cover_image(temp_dir / "cover.png", norm, 0)
        for index in range(1, 4):
            _cover_image(temp_dir / f"preview_{index}.png", norm, index)
        names.extend(["README.txt", "LICENSE.txt"])
        (temp_dir / "README.txt").write_text(_readme(norm), encoding="utf-8")
        (temp_dir / "LICENSE.txt").write_text(_license(norm), encoding="utf-8")
        manifest = {
            "format": FORMAT,
            "title": norm["title"],
            "business": norm["business"],
            "paper_sizes": list(PAPER_SIZES),
            "pages_per_pdf": 35,
            "files": sorted(names + ["manifest.json"]),
        }
        (temp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        names.append("manifest.json")
        out_path.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(temp_dir / name, out_path / name)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return sorted(names)
