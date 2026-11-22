"""builder_digital_planner.py — Hyperlinked digital planner for iPad/tablet note apps.

FORMAT = 'digital_planner'
Builds a deterministic landscape PDF with clickable side-tab link annotations.
The PDF writer is self-contained in this file (pdf_design.py is not modified).
No stock images, no currency amounts, no network.
"""

from __future__ import annotations

import hashlib
import json
import re
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

FORMAT = "digital_planner"
KEYWORDS = [
    "digital planner", "ipad planner", "goodnotes planner",
    "hyperlinked planner", "tablet planner", "notability planner",
]

DEFAULT_TITLE = "Hyperlinked Digital Planner"
DEFAULT_YEAR = 2027
MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
WEEK_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# Landscape tablet ratio roughly 2388x1668 -> use points scaled to keep PDF reasonable
PAGE_W = 1194.0
PAGE_H = 834.0
MARGIN = 54.0
TAB_W = 54.0
TAB_GAP = 4.0

COL = {
    "ink": "#1f2933",
    "muted": "#5f6b73",
    "accent": "#4f6d7a",
    "soft": "#edf3f6",
    "line": "#cfd8dc",
    "paper": "#fbfcfd",
    "tab": "#dce8ee",
    "tab_active": "#4f6d7a",
}


def _clean_title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    return title or DEFAULT_TITLE


def normalize_spec(spec: Any) -> Dict[str, Any]:
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
    return {"title": title, "year": year}


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


# ---- minimal PDF with link annotations --------------------------------------

def _pdf_escape(text: str) -> str:
    # WinAnsi-ish simple escape; keep ASCII-safe for portability
    out = []
    for ch in str(text):
        code = ord(ch)
        if ch in ("\\", "(", ")"):
            out.append("\\" + ch)
        elif 32 <= code <= 126:
            out.append(ch)
        else:
            # best-effort ASCII fallback
            import unicodedata
            plain = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii")
            out.extend(list(plain))
    return "".join(out)


def _rgb(hex_color: str) -> Tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _c(color: str, stroke: bool = False) -> str:
    r, g, b = _rgb(color)
    return f"{r:.3f} {g:.3f} {b:.3f} {'RG' if stroke else 'rg'}"


class LinkPdf:
    """Small PDF writer supporting text, lines, rects, fills, and link annotations."""

    def __init__(self, w: float = PAGE_W, h: float = PAGE_H):
        self.w = w
        self.h = h
        self.pages: List[Dict[str, Any]] = []

    def new_page(self) -> int:
        self.pages.append({"ops": [], "annots": []})
        return len(self.pages) - 1

    @property
    def _ops(self) -> List[str]:
        return self.pages[-1]["ops"]

    @property
    def _annots(self) -> List[Dict[str, Any]]:
        return self.pages[-1]["annots"]

    def fill(self, x, y, w, h, color: str):
        self._ops.append(f"{_c(color)} {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f 0 g")

    def rect(self, x, y, w, h, color: str = COL["line"], width: float = 0.8):
        self._ops.append(f"{_c(color, True)} {width:.2f} w {x:.2f} {y:.2f} {w:.2f} {h:.2f} re S 0 G")

    def line(self, x1, y1, x2, y2, color: str = COL["line"], width: float = 0.8):
        self._ops.append(f"{_c(color, True)} {width:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S 0 G")

    def text(self, x, y, s: str, size: float = 10, bold: bool = False, color: str = COL["ink"]):
        font = "/F2" if bold else "/F1"
        self._ops.append(f"BT {_c(color)} {font} {size:.2f} Tf {x:.2f} {y:.2f} Td ({_pdf_escape(s)}) Tj ET 0 g")

    def text_center(self, cx, y, s: str, size: float = 10, bold: bool = False, color: str = COL["ink"]):
        # rough centering with Helvetica average width
        avg = 0.52 if not bold else 0.56
        w = len(_pdf_escape(s)) * size * avg
        self.text(cx - w / 2, y, s, size=size, bold=bold, color=color)

    def link_rect(self, x, y, w, h, dest_page: int):
        """Add an internal link annotation to dest_page (0-based)."""
        self._annots.append({"rect": [x, y, x + w, y + h], "dest_page": dest_page})

    def save(self, path: Union[str, Path], title: str = ""):
        path = Path(path)
        objects: List[bytes] = []

        def add_obj(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        # reserve 1: catalog, 2: pages, 3: font1, 4: font2
        objects.extend([b"", b"", b"", b""])
        font1_id = 3
        font2_id = 4
        objects[font1_id - 1] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        objects[font2_id - 1] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"

        page_ids: List[int] = []
        page_content_ids: List[int] = []
        # first pass create page/content placeholders
        for _ in self.pages:
            content_id = add_obj(b"")
            page_id = add_obj(b"")
            page_content_ids.append(content_id)
            page_ids.append(page_id)

        # second pass write content and page objects with annotations
        for i, page in enumerate(self.pages):
            stream = "\n".join(page["ops"]).encode("latin-1", "replace")
            content_body = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
            objects[page_content_ids[i] - 1] = content_body

            annot_refs = []
            for ann in page["annots"]:
                dest_page_idx = int(ann["dest_page"])
                if 0 <= dest_page_idx < len(page_ids):
                    target = page_ids[dest_page_idx]
                    rect = ann["rect"]
                    rect_str = "[%.2f %.2f %.2f %.2f]" % tuple(rect)
                    ann_body = (
                        f"<< /Type /Annot /Subtype /Link /Rect {rect_str} /Border [0 0 0] "
                        f"/Dest [{target} 0 R /Fit] >>"
                    ).encode("latin-1")
                    annot_id = add_obj(ann_body)
                    annot_refs.append(f"{annot_id} 0 R")

            annots_part = ""
            if annot_refs:
                annots_part = " /Annots [" + " ".join(annot_refs) + "]"
            page_body = (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.w:.2f} {self.h:.2f}] "
                f"/Resources << /Font << /F1 {font1_id} 0 R /F2 {font2_id} 0 R >> >> "
                f"/Contents {page_content_ids[i]} 0 R{annots_part} >>"
            ).encode("latin-1")
            objects[page_ids[i] - 1] = page_body

        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")
        objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"

        info_id = add_obj(
            f"<< /Producer (Chilli Gavva Product Builder) /Title ({_pdf_escape(title)}) /Creator (Chilli Gavva robot) >>".encode("latin-1")
        )

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objects, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode("latin-1")
            out += body
            out += b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1")
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode("latin-1")
        out += (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("latin-1")
        path.write_bytes(bytes(out))


def _draw_tabs(pdf: LinkPdf, targets: Dict[str, int], active: str = ""):
    # tabs on right side: Index + 12 months
    labels = ["Index"] + [m[:3] for m in MONTHS]
    keys = ["index"] + [f"month:{i}" for i in range(12)]
    tab_h = (pdf.h - 120) / len(labels)
    x = pdf.w - TAB_W
    for i, (label, key) in enumerate(zip(labels, keys)):
        y = pdf.h - 70 - (i + 1) * tab_h
        fill = COL["tab_active"] if key == active else COL["tab"]
        text_col = "#ffffff" if key == active else COL["ink"]
        pdf.fill(x, y + 2, TAB_W - 8, tab_h - TAB_GAP, fill)
        pdf.rect(x, y + 2, TAB_W - 8, tab_h - TAB_GAP, COL["line"], 0.5)
        # rotate-ish not supported; write short vertical-ish by stacking? better horizontal small text
        pdf.text(x + 8, y + tab_h / 2 - 4, label, 8, bold=(key == active), color=text_col)
        dest = targets.get(key)
        if dest is not None:
            pdf.link_rect(x, y + 2, TAB_W - 8, tab_h - TAB_GAP, dest)


def _draw_header(pdf: LinkPdf, title: str, subtitle: str = ""):
    pdf.fill(0, pdf.h - 52, pdf.w, 52, COL["accent"])
    pdf.text(MARGIN, pdf.h - 32, title[:70], 20, bold=True, color="#ffffff")
    if subtitle:
        pdf.text(MARGIN + 420, pdf.h - 32, subtitle[:60], 11, color="#e8eff2")
    pdf.line(MARGIN, MARGIN, pdf.w - TAB_W - 12, MARGIN, COL["line"], 0.8)


def _draw_index(pdf: LinkPdf, norm: Dict[str, Any], targets: Dict[str, int]):
    _draw_header(pdf, norm["title"], f"{norm['year']} · Hyperlinked tablet planner")
    _draw_tabs(pdf, targets, active="index")
    left = MARGIN
    top = pdf.h - 90
    pdf.text(left, top, "Tap a month tab on the right, or tap a month card below.", 11, color=COL["muted"])
    cols, rows = 4, 3
    gap = 14
    card_w = (pdf.w - TAB_W - 24 - 2 * MARGIN - (cols - 1) * gap) / cols
    card_h = (top - 40 - MARGIN - (rows - 1) * gap) / rows
    for i, month in enumerate(MONTHS):
        c = i % cols
        r = i // cols
        x = left + c * (card_w + gap)
        y = top - 30 - (r + 1) * card_h - r * gap
        pdf.fill(x, y, card_w, card_h, COL["soft"])
        pdf.rect(x, y, card_w, card_h, COL["line"], 0.8)
        pdf.text(x + 14, y + card_h - 24, month, 16, bold=True, color=COL["accent"])
        pdf.text(x + 14, y + card_h - 44, f"Open {month} overview", 9, color=COL["muted"])
        pdf.text(x + 14, y + 24, "Monthly + weekly + daily links", 8, color=COL["muted"])
        pdf.link_rect(x, y, card_w, card_h, targets[f"month:{i}"])


def _draw_month(pdf: LinkPdf, norm: Dict[str, Any], month_idx: int, targets: Dict[str, int], weekly_targets: List[int], daily_targets: List[int]):
    month = MONTHS[month_idx]
    _draw_header(pdf, f"{month} {norm['year']}", "Monthly overview")
    _draw_tabs(pdf, targets, active=f"month:{month_idx}")
    left = MARGIN
    top = pdf.h - 100
    # calendar grid placeholder (undated structure)
    pdf.text(left, top + 10, "Monthly planner (undated) — use with any note app.", 10, color=COL["muted"])
    grid_w = (pdf.w - TAB_W - 24 - 2 * MARGIN) * 0.68
    grid_h = pdf.h - 170
    cell_w = grid_w / 7
    cell_h = (grid_h - 24) / 6
    gx = left
    gy = MARGIN
    for i, wd in enumerate(WEEK_DAYS):
        pdf.fill(gx + i * cell_w, gy + grid_h - 24, cell_w, 24, COL["soft"])
        pdf.rect(gx + i * cell_w, gy + grid_h - 24, cell_w, 24, COL["line"], 0.6)
        pdf.text(gx + i * cell_w + 8, gy + grid_h - 17, wd, 9, bold=True, color=COL["accent"])
    for r in range(6):
        for c in range(7):
            x = gx + c * cell_w
            y = gy + (5 - r) * cell_h
            pdf.rect(x, y, cell_w, cell_h, COL["line"], 0.5)
            pdf.text(x + 6, y + cell_h - 14, str(r * 7 + c + 1), 8, color=COL["muted"])
    # right panel links
    px = left + grid_w + 18
    pw = pdf.w - TAB_W - 24 - px - 8
    pdf.fill(px, MARGIN, pw, grid_h, "#ffffff")
    pdf.rect(px, MARGIN, pw, grid_h, COL["line"], 0.8)
    pdf.text(px + 12, MARGIN + grid_h - 24, "Quick links", 12, bold=True, color=COL["accent"])
    ly = MARGIN + grid_h - 52
    for i, widx in enumerate(weekly_targets[:5]):
        label = f"Week {i + 1}"
        pdf.fill(px + 10, ly - 8, pw - 20, 28, COL["soft"])
        pdf.rect(px + 10, ly - 8, pw - 20, 28, COL["line"], 0.6)
        pdf.text(px + 18, ly, label, 10, color=COL["ink"])
        pdf.link_rect(px + 10, ly - 8, pw - 20, 28, widx)
        ly -= 36
    ly -= 8
    pdf.text(px + 12, ly, "Daily pages", 11, bold=True, color=COL["accent"])
    ly -= 24
    for i, didx in enumerate(daily_targets[:7]):
        label = f"Day {i + 1}"
        pdf.fill(px + 10, ly - 6, pw - 20, 24, "#ffffff")
        pdf.rect(px + 10, ly - 6, pw - 20, 24, COL["line"], 0.5)
        pdf.text(px + 18, ly, label, 9, color=COL["ink"])
        pdf.link_rect(px + 10, ly - 6, pw - 20, 24, didx)
        ly -= 28


def _draw_weekly(pdf: LinkPdf, norm: Dict[str, Any], month_idx: int, week_no: int, targets: Dict[str, int]):
    month = MONTHS[month_idx]
    _draw_header(pdf, f"{month} Week {week_no}", f"{norm['year']} · Weekly plan")
    _draw_tabs(pdf, targets, active=f"month:{month_idx}")
    left = MARGIN
    top = pdf.h - 108
    cols = 7
    col_w = (pdf.w - TAB_W - 24 - 2 * MARGIN) / cols
    row_h = pdf.h - 170
    for i, day in enumerate(WEEK_DAYS):
        x = left + i * col_w
        pdf.fill(x, MARGIN + row_h - 28, col_w, 28, COL["soft"])
        pdf.rect(x, MARGIN, col_w, row_h, COL["line"], 0.7)
        pdf.text(x + 8, MARGIN + row_h - 19, day, 10, bold=True, color=COL["accent"])
        for ln in range(12):
            ly = MARGIN + row_h - 46 - ln * ((row_h - 54) / 12)
            pdf.line(x + 6, ly, x + col_w - 6, ly, COL["line"], 0.4)


def _draw_daily(pdf: LinkPdf, norm: Dict[str, Any], month_idx: int, day_no: int, targets: Dict[str, int]):
    month = MONTHS[month_idx]
    _draw_header(pdf, f"{month} Day {day_no}", f"{norm['year']} · Daily plan")
    _draw_tabs(pdf, targets, active=f"month:{month_idx}")
    left = MARGIN
    right = pdf.w - TAB_W - 24 - MARGIN
    top = pdf.h - 110
    # schedule
    pdf.text(left, top + 8, "Schedule", 11, bold=True, color=COL["accent"])
    sched_w = (right - left) * 0.58
    row_h = (top - MARGIN - 20) / 12
    for r in range(12):
        y = top - 16 - (r + 1) * row_h
        pdf.rect(left, y, sched_w, row_h, COL["line"], 0.5)
        pdf.text(left + 6, y + row_h / 2 - 3, f"{7 + r}:00", 8, color=COL["muted"])
        pdf.line(left + 45, y, left + 45, y + row_h, COL["line"], 0.4)
    # notes/priorities
    nx = left + sched_w + 16
    nw = right - nx
    pdf.rect(nx, MARGIN, nw, top - MARGIN, COL["line"], 0.8)
    pdf.fill(nx, top - 28, nw, 28, COL["soft"])
    pdf.text(nx + 10, top - 20, "Priorities & Notes", 11, bold=True, color=COL["accent"])
    for i, label in enumerate(["Top 3 priorities", "To-do list", "Notes"]):
        by = top - 60 - i * 150
        pdf.text(nx + 10, by, label, 9, bold=True, color=COL["ink"])
        for ln in range(5):
            ly = by - 18 - ln * 18
            if ly > MARGIN + 20:
                pdf.line(nx + 10, ly, nx + nw - 10, ly, COL["line"], 0.5)


def _draw_notes(pdf: LinkPdf, norm: Dict[str, Any], targets: Dict[str, int]):
    _draw_header(pdf, "Notes", f"{norm['year']} · Extra pages")
    _draw_tabs(pdf, targets, active="index")
    left = MARGIN
    right = pdf.w - TAB_W - 24 - MARGIN
    top = pdf.h - 110
    bottom = MARGIN
    pdf.rect(left, bottom, right - left, top - bottom, COL["line"], 0.8)
    for i in range(28):
        y = top - 24 - i * ((top - bottom - 40) / 28)
        if y > bottom + 12:
            pdf.line(left + 14, y, right - 14, y, COL["line"], 0.5)


def build_pdf(path: Path, norm: Dict[str, Any]) -> Dict[str, Any]:
    pdf = LinkPdf(PAGE_W, PAGE_H)
    # Plan: index, 12 months, 5 weekly per month, 7 daily per month, 1 notes = 1+12+60+84+1 = 158
    index_page = pdf.new_page()
    month_pages = [pdf.new_page() for _ in range(12)]
    weekly_pages: List[List[int]] = []
    daily_pages: List[List[int]] = []
    for _ in range(12):
        weekly_pages.append([pdf.new_page() for _ in range(5)])
        daily_pages.append([pdf.new_page() for _ in range(7)])
    notes_page = pdf.new_page()

    targets = {"index": index_page, "notes": notes_page}
    for i in range(12):
        targets[f"month:{i}"] = month_pages[i]

    # index
    pdf.pages[index_page]["ops"] = []
    pdf.pages[index_page]["annots"] = []
    # temporarily switch active page by moving ops/annots reference: easiest rebuild by drawing into selected page
    def with_page(idx):
        class _Ctx:
            def __enter__(self_non):
                self_non.ops = pdf.pages[idx]["ops"]
                self_non.ann = pdf.pages[idx]["annots"]
                pdf.pages[idx]["ops"] = []
                pdf.pages[idx]["annots"] = []
                return idx
            def __exit__(self_non, exc_type, exc, tb):
                return False
        return _Ctx()

    # We draw by setting current page via helper
    orig_pages = pdf.pages

    def draw_on(page_idx):
        pdf.pages.append({"ops": [], "annots": []})  # dummy not used
        # We'll directly operate by swapping list objects
        pass

    # simpler: create fresh LinkPdf-like drawing by monkeypatching current ops/annots
    class PageCtx:
        def __init__(self, p: LinkPdf, idx: int):
            self.p = p; self.idx = idx; self.prev=None
        def __enter__(self):
            self.prev = self.p.pages[self.idx]
            # use same dict object
            self.p.pages[self.idx] = self.prev
            # make _ops/_annots refer to this page
            self.p._active_idx = self.idx
            return self.prev
        def __exit__(self, exc_type, exc, tb):
            return False

    # Patch LinkPdf to use active index
    pdf._active_idx = 0
    def _ops_get(self):
        return self.pages[self._active_idx]["ops"]
    def _ann_get(self):
        return self.pages[self._active_idx]["annots"]
    LinkPdf._ops = property(_ops_get)  # type: ignore
    LinkPdf._annots = property(_ann_get)  # type: ignore

    pdf._active_idx = index_page
    _draw_index(pdf, norm, targets)
    for i in range(12):
        pdf._active_idx = month_pages[i]
        _draw_month(pdf, norm, i, targets, weekly_pages[i], daily_pages[i])
        for w, wp in enumerate(weekly_pages[i], start=1):
            pdf._active_idx = wp
            _draw_weekly(pdf, norm, i, w, targets)
        for d, dp in enumerate(daily_pages[i], start=1):
            pdf._active_idx = dp
            _draw_daily(pdf, norm, i, d, targets)
    pdf._active_idx = notes_page
    _draw_notes(pdf, norm, targets)

    pdf.save(path, title=norm["title"])
    page_count = len(pdf.pages)
    return {"page_count": page_count, "month_pages": month_pages, "index_page": index_page, "targets": targets}


def _cover_image(path: Path, norm: Dict[str, Any], preview: int = 0) -> None:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for digital planner images")
    labels = ["Cover", "Index Page", "Monthly Page", "Daily Page"]
    kind = preview % len(labels)
    w, h = (1600, 1067)
    bg = "#f7fafb"
    ink = COL["ink"]; accent = COL["accent"]; soft = COL["soft"]; line = COL["line"]
    im = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(im)
    draw.rectangle([20, 20, w - 20, h - 20], outline=accent, width=4)
    draw.rectangle([20, 20, w - 20, 84], fill=accent)
    draw.text((44, 36), "CHILLI GAVVA · DIGITAL PLANNER", fill="#ffffff", font=_font(24, True))
    draw.text((48, 110), norm["title"][:48], fill=ink, font=_font(46, True))
    draw.text((48, 170), f"{norm['year']} · Hyperlinked · iPad/tablet · A4 landscape style", fill=accent, font=_font(24, True))
    draw.text((48, 206), "For GoodNotes, Notability, and other note apps that open PDFs", fill=COL["muted"], font=_font(18))

    def mini(x, y, mw, mh, title):
        draw.rectangle([x, y, x + mw, y + mh], fill="#ffffff", outline=line, width=2)
        draw.rectangle([x, y, x + mw, y + 26], fill=accent)
        draw.text((x + 8, y + 5), title, fill="#ffffff", font=_font(12, True))
        for i in range(6):
            ly = y + 42 + i * ((mh - 52) / 6)
            draw.line([x + 10, ly, x + mw - 10, ly], fill=line, width=1)

    if kind == 0:
        draw.rounded_rectangle([48, 260, w - 48, h - 110], radius=18, fill="#ffffff", outline=line, width=2)
        draw.text((72, 280), "INSIDE THE PLANNER", fill=accent, font=_font(20, True))
        mini(72, 330, 430, 420, "Index")
        mini(540, 330, 430, 420, "Monthly")
        mini(1008, 330, 430, 420, "Daily")
        draw.text((72, 780), "Index · 12 months · Weekly pages · Daily pages · Notes · Clickable side tabs", fill=ink, font=_font(18))
    elif kind == 1:
        draw.text((48, 250), "INDEX PAGE", fill=accent, font=_font(22, True))
        for i, m in enumerate(MONTHS):
            c = i % 4; r = i // 4
            x = 48 + c * 360; y = 296 + r * 190
            draw.rectangle([x, y, x + 330, y + 160], fill=soft, outline=line, width=2)
            draw.text((x + 14, y + 16), m, fill=accent, font=_font(18, True))
            draw.text((x + 14, y + 52), "Tap to open month", fill=COL["muted"], font=_font(13))
        # side tabs
        for i, lab in enumerate(["Index"] + [m[:3] for m in MONTHS]):
            ty = 296 + i * 44
            draw.rectangle([w - 80, ty, w - 48, ty + 38], fill=accent if i == 0 else COL["tab"], outline=line)
    elif kind == 2:
        draw.text((48, 250), "MONTHLY PAGE", fill=accent, font=_font(22, True))
        # grid
        gx, gy, gw, gh = 48, 296, 980, 620
        draw.rectangle([gx, gy, gx + gw, gy + gh], fill="#ffffff", outline=line, width=2)
        for c in range(7):
            x = gx + c * gw / 7
            draw.line([x, gy, x, gy + gh], fill=line, width=1)
            draw.text((x + 8, gy + 8), WEEK_DAYS[c], fill=accent, font=_font(12, True))
        for r in range(6):
            y = gy + 30 + r * (gh - 30) / 6
            draw.line([gx, y, gx + gw, y], fill=line, width=1)
        draw.rectangle([1050, 296, w - 100, 916], fill=soft, outline=line, width=2)
        draw.text((1068, 316), "Quick links", fill=accent, font=_font(16, True))
        for i in range(5):
            yy = 360 + i * 56
            draw.rectangle([1068, yy, w - 120, yy + 40], fill="#ffffff", outline=line)
            draw.text((1080, yy + 10), f"Week {i+1}", fill=ink, font=_font(13))
        for i, lab in enumerate(["Index"] + [m[:3] for m in MONTHS]):
            ty = 296 + i * 44
            draw.rectangle([w - 80, ty, w - 48, ty + 38], fill=accent if lab == 'Jan' else COL["tab"], outline=line)
    else:
        draw.text((48, 250), "DAILY PAGE", fill=accent, font=_font(22, True))
        draw.rectangle([48, 296, 860, 916], fill="#ffffff", outline=line, width=2)
        draw.text((64, 312), "Schedule", fill=accent, font=_font(16, True))
        for i in range(10):
            yy = 350 + i * 52
            draw.line([64, yy, 840, yy], fill=line, width=1)
            draw.text((64, yy - 18), f"{7+i}:00", fill=COL["muted"], font=_font(11))
        draw.rectangle([890, 296, w - 100, 916], fill=soft, outline=line, width=2)
        draw.text((908, 316), "Priorities & Notes", fill=accent, font=_font(16, True))
        for i in range(12):
            yy = 360 + i * 42
            draw.line([908, yy, w - 120, yy], fill=line, width=1)
        for i, lab in enumerate(["Index"] + [m[:3] for m in MONTHS]):
            ty = 296 + i * 44
            draw.rectangle([w - 80, ty, w - 48, ty + 38], fill=accent if lab == 'Jan' else COL["tab"], outline=line)

    draw.text((48, h - 72), "Clickable side tabs on every page · Index · 12 months · Weekly · Daily · Notes", fill=COL["muted"], font=_font(16))
    draw.text((w - 260, h - 72), labels[kind], fill=accent, font=_font(18, True))
    im.save(str(path), format="PNG", optimize=True)


def _readme(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"{norm['title']} — {norm['year']}",
        "=" * 52,
        "",
        "WHAT IS INSIDE",
        "- 1 hyperlinked landscape PDF digital planner (tablet-friendly size)",
        "- Index page with month links",
        "- 12 monthly pages",
        "- Weekly planning pages",
        "- Daily planning pages",
        "- Notes page",
        "- Clickable side tabs on every page (Index + months)",
        "- cover.png and three preview images",
        "",
        "HOW TO USE IN A NOTE APP",
        "1. Download the PDF to your iPad/tablet or cloud drive.",
        "2. Import/open it in GoodNotes, Notability, Noteshelf, Xodo, or another PDF note app.",
        "3. Use read/select mode to tap tabs and links; use pen mode to write.",
        "4. Duplicate daily/weekly pages in your note app if you want extra copies.",
        "",
        "LICENSE",
        "Personal use only. Do not resell or redistribute the files.",
    ]) + "\n"


def _license(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"LICENSE — {norm['title']}",
        "",
        "This digital planner is for personal use only.",
        "You may use it on your own devices and note apps.",
        "You may not resell, redistribute, share, or claim the files as your own.",
        "",
    ])


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for the digital planner builder")
    norm = normalize_spec(spec)
    out_path = Path(out_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="digital_planner_build_"))
    names: List[str] = []
    try:
        pdf_name = "digital_planner.pdf"
        info = build_pdf(temp_dir / pdf_name, norm)
        names.append(pdf_name)

        cover_name = "cover.png"
        _cover_image(temp_dir / cover_name, norm, preview=0)
        names.append(cover_name)
        for i in range(1, 4):
            n = f"preview_{i}.png"
            _cover_image(temp_dir / n, norm, preview=i)
            names.append(n)

        names.extend(["README.txt", "LICENSE.txt"])
        _write_text(temp_dir / "README.txt", _readme(norm))
        _write_text(temp_dir / "LICENSE.txt", _license(norm))

        manifest = {
            "format": FORMAT,
            "title": norm["title"],
            "year": norm["year"],
            "page_count": info["page_count"],
            "hyperlinked": True,
            "tab_links": ["index"] + [f"month:{i}" for i in range(12)],
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
