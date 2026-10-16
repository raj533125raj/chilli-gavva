"""pdf_design.py — printable planner pages in the owner-approved design (mockups of 2026-10-16).

  Style A "Clean Accent" (teal)  — the default for every niche.
  Style B "Soft Pastel"          — ADHD Planners niche only.

A plain hand-written PDF with the built-in Helvetica fonts (nothing to install, no font files inside).
Every page:
  * uses the whole page: the last block in each column grows with writing lines down to the footer;
  * has a footer: product name · paper size · for personal use, and "Page X of N";
  * never cuts text off: long text wraps, or shrinks a little and wraps.
Every planner starts with a simple cover and a "How to use" page. Calendar dates come from Python's
calendar module, never from the AI.
"""
import calendar
import re
import unicodedata

PAGE_SIZES = {"A4": (595.28, 841.89), "Letter": (612.0, 792.0)}
MARGIN = 42
BOTTOM = 60            # content never goes below this line; the footer lives under it
FOOTER_Y = 30
PLANNER_YEAR = 2027
WEEKDAYS_SUN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

STYLES = {
    "A": {"name": "Clean Accent", "page": None, "accent": "#2f7d78", "bar": "#e6f2f1", "line": "#c9d3d6",
          "weekend": "#f1f6f6", "ink": "#1f2933", "muted": "#5f6b73", "faded": "#b4bec3", "on_accent": "#ffffff",
          "on_accent_soft": "#d3e8e6", "round": False, "boxes": None, "cell": "#c9d3d6"},
    "B": {"name": "Soft Pastel", "page": "#fffaf3", "accent": "#c4657a", "bar": None, "line": "#e2cfc5",
          "weekend": "#fcebea", "ink": "#3d2c3e", "muted": "#7b6b78", "faded": "#cbbcc1", "on_accent": "#ffffff",
          "on_accent_soft": "#ffffff", "round": True, "boxes": ["#fbe3e1", "#ece6f6", "#e8f0e3"],
          "cell": "#e6d3ca", "pill": "#e8f0e3", "header": "#fbe3e1", "lavender": "#ece6f6"},
}


def style_for(spec):
    """Style B for the ADHD Planners niche, Style A for everything else (or the spec's own choice)."""
    if spec.get("style") in STYLES:
        return spec["style"]
    return "B" if spec.get("niche") == "ADHD Planners Agent" else "A"


# ---- text: Windows-1252 encoding and real Helvetica widths -----------------------------------------------
# The fonts use WinAnsi (Windows-1252), which HAS curly quotes, en/em dashes, bullets and the ellipsis.
# Anything it does not have becomes the closest plain text: a PDF never shows "?" for a dash or quote.
_PDF_SUBS = {"‐": "-", "‑": "-", "‒": "-", "⁃": "-", "−": "-", " ": " ",
             " ": " ", " ": " ", "​": "", "→": "->", "←": "<-", "≤": "<=",
             "≥": ">=", "∑": "Sum", "Σ": "Sum", "✓": "v", "✔": "v", "★": "*",
             "☆": "*", "≈": "~", "≠": "!="}

_HELVETICA = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 "
    "556 278 278 584 584 584 556 1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 667 778 "
    "722 667 611 722 667 944 667 667 611 278 278 278 469 556 333 556 556 500 556 556 278 556 556 222 222 "
    "500 222 833 556 556 556 556 333 500 278 556 500 722 500 500 500 334 260 334 584 634 744 556 222 556 "
    "333 1000 556 556 333 1000 667 333 1000 556 611 556 556 222 222 333 333 350 556 1000 333 1000 500 333 "
    "944 556 500 667 278 333 556 556 556 556 260 556 333 737 370 556 584 333 737 333 400 549 333 333 333 "
    "576 537 278 333 333 365 556 834 834 834 611 667 667 667 667 667 667 1000 722 667 667 667 667 278 278 "
    "278 278 722 722 778 778 778 778 778 584 778 722 722 722 722 667 667 611 556 556 556 556 556 556 889 "
    "500 556 556 556 556 278 278 278 278 556 556 556 556 556 556 556 549 611 556 556 556 556 500 556 500 ")
_HELVETICA_BOLD = (
    "278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 "
    "556 333 333 584 584 584 611 975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 667 778 "
    "722 667 611 722 667 944 667 667 611 333 278 333 584 556 333 556 611 556 611 556 333 611 611 278 278 "
    "556 278 889 611 611 611 611 389 556 333 611 556 778 556 556 500 389 280 389 584 722 744 556 278 556 "
    "500 1000 556 556 333 1000 667 333 1000 556 611 556 556 278 278 500 500 350 556 1000 333 1000 556 333 "
    "944 556 500 667 278 333 556 556 556 556 280 556 333 737 370 556 584 333 737 333 400 549 333 333 333 "
    "576 556 278 333 333 365 556 834 834 834 611 722 722 722 722 722 722 1000 722 667 667 667 667 278 278 "
    "278 278 722 722 778 778 778 778 778 584 778 722 722 722 722 667 667 611 556 556 556 556 556 556 889 "
    "556 556 556 556 556 278 278 278 278 611 611 611 611 611 611 611 549 611 611 611 611 611 556 611 556 ")
_WIDTHS = ([int(n) for n in _HELVETICA.split()], [int(n) for n in _HELVETICA_BOLD.split()])   # chars 32..255


def _winansi(s):
    out = []
    for ch in str(s):
        ch = _PDF_SUBS.get(ch, ch)
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            plain = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(plain)          # unknown symbols are dropped, never shown as "?"
    return "".join(out)


def _pdf_text(s):
    s = _winansi(s).encode("cp1252").decode("latin-1")   # one byte per character, as the PDF expects
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_width(s, size, bold=False, spacing=0.0):
    """Width in points, from the real Helvetica / Helvetica-Bold character widths."""
    table = _WIDTHS[1 if bold else 0]
    data = _winansi(s).encode("cp1252")
    return sum(table[b - 32] if b >= 32 else 556 for b in data) * size / 1000.0 + spacing * len(data)


def wrap_text(s, size, max_width, bold=False, spacing=0.0):
    words, lines, cur = _winansi(s).split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if cur and text_width(trial, size, bold, spacing) > max_width:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    out = []
    for line in lines or [""]:       # a single very long word is split rather than cut off
        while text_width(line, size, bold, spacing) > max_width and len(line) > 1:
            n = len(line) - 1
            while n > 1 and text_width(line[:n], size, bold, spacing) > max_width:
                n -= 1
            out.append(line[:n])
            line = line[n:]
        out.append(line)
    return out


def fit_text(s, size, max_width, bold=False, max_lines=1, min_size=None, spacing=0.0):
    """Shrink (down to min_size) until the text wraps into max_lines. Never cuts: if it still does not
    fit, ALL the lines are returned. Returns (lines, size)."""
    min_size = min_size or size
    while True:
        lines = wrap_text(s, size, max_width, bold, spacing)
        if len(lines) <= max_lines or size <= min_size:
            return lines, size
        size = max(min_size, round(size - 0.5, 1))


def _rgb(color):
    if isinstance(color, (tuple, list)):
        return color
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _c(color, stroke=False):
    r, g, b = _rgb(color)
    return f"{r:.3f} {g:.3f} {b:.3f} {'RG' if stroke else 'rg'}"


class Pdf:
    """A tiny PDF writer: text, lines, boxes, filled and rounded shapes. The footer with 'Page X of N'
    is added to every page when the file is saved (only then is N known)."""

    def __init__(self, size, footer=None, style=None):
        self.size = size
        self.w, self.h = PAGE_SIZES[size]
        self.pages = []
        self.footer = footer
        self.style_key = style if style in STYLES else None
        self.style = STYLES.get(style) if isinstance(style, str) else style

    def new_page(self):
        self.pages.append([])
        if self.style and self.style.get("page"):
            self.fill(0, 0, self.w, self.h, self.style["page"])

    def text(self, x, y, s, size=11, bold=False, color=None, spacing=0):
        ops = self.pages[-1]
        if color:
            ops.append(_c(color))
        if spacing:
            ops.append(f"{spacing:g} Tc")
        ops.append(f"BT /{'F2' if bold else 'F1'} {size:g} Tf {x:.1f} {y:.1f} Td ({_pdf_text(s)}) Tj ET")
        if spacing:
            ops.append("0 Tc")
        if color:
            ops.append("0 g")

    def text_right(self, x_right, y, s, size=11, bold=False, color=None, spacing=0):
        self.text(x_right - text_width(s, size, bold, spacing) + spacing, y, s, size, bold, color, spacing)

    def text_center(self, x_mid, y, s, size=11, bold=False, color=None, spacing=0):
        self.text(x_mid - (text_width(s, size, bold, spacing) - spacing) / 2, y, s, size, bold, color, spacing)

    def line(self, x1, y1, x2, y2, gray=0.7, color=None, width=0.6):
        stroke = _c(color, True) if color else f"{gray} G"
        self.pages[-1].append(f"{stroke} {width:g} w {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S 0 G")

    def box(self, x, y, size=10, color=None, width=0.8):
        stroke = _c(color, True) if color else "0.3 G"
        self.pages[-1].append(f"{stroke} {width:g} w {x:.1f} {y:.1f} {size:g} {size:g} re S 0 G")

    def rect(self, x, y, w, h, gray=0.7, color=None, width=0.6):
        stroke = _c(color, True) if color else f"{gray} G"
        self.pages[-1].append(f"{stroke} {width:g} w {x:.1f} {y:.1f} {w:.1f} {h:.1f} re S 0 G")

    def fill(self, x, y, w, h, color):
        self.pages[-1].append(f"{_c(color)} {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f 0 g")

    def _paint(self, path, fill, stroke, width):
        paint = "B" if fill and stroke else ("f" if fill else "S")
        pre = (_c(fill) + " " if fill else "") + (_c(stroke, True) + f" {width:g} w " if stroke else "")
        self.pages[-1].append(f"{pre}{path} {paint} 0 g 0 G")

    def round_rect(self, x, y, w, h, r, fill=None, stroke=None, width=0.8):
        r = max(0.0, min(r, w / 2, h / 2))
        k = 0.5523 * r
        p = (f"{x + r:.1f} {y:.1f} m {x + w - r:.1f} {y:.1f} l "
             f"{x + w - r + k:.1f} {y:.1f} {x + w:.1f} {y + r - k:.1f} {x + w:.1f} {y + r:.1f} c "
             f"{x + w:.1f} {y + h - r:.1f} l "
             f"{x + w:.1f} {y + h - r + k:.1f} {x + w - r + k:.1f} {y + h:.1f} {x + w - r:.1f} {y + h:.1f} c "
             f"{x + r:.1f} {y + h:.1f} l "
             f"{x + r - k:.1f} {y + h:.1f} {x:.1f} {y + h - r + k:.1f} {x:.1f} {y + h - r:.1f} c "
             f"{x:.1f} {y + r:.1f} l {x:.1f} {y + r - k:.1f} {x + r - k:.1f} {y:.1f} {x + r:.1f} {y:.1f} c h")
        self._paint(p, fill, stroke, width)

    def circle(self, cx, cy, r, fill=None, stroke=None, width=0.8):
        self.round_rect(cx - r, cy - r, 2 * r, 2 * r, r, fill, stroke, width)

    def _draw_footer(self, number, total):
        st = self.style or STYLES["A"]
        right = f"Page {number} of {total}"
        self.line(MARGIN, 44, self.w - MARGIN, 44, color=st["line"], width=0.6)
        self.text_right(self.w - MARGIN, FOOTER_Y, right, 8, color=st["muted"])
        room = self.w - 2 * MARGIN - text_width(right, 8) - 24
        lines, size = fit_text(self.footer, 8, room, max_lines=1, min_size=6.5)
        for i, line in enumerate(lines[:2] if len(lines) <= 2 else lines):
            self.text(MARGIN, FOOTER_Y + 4 * (len(lines) - 1) - 8 * i, line, size, color=st["muted"])

    def save(self, path, title=""):
        if self.footer:
            for i, ops in enumerate(self.pages, 1):
                saved, self.pages = self.pages, [ops]
                self._draw_footer(i, len(saved))
                self.pages = saved
        objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
                "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
        kids = []
        for ops in self.pages:
            stream = "\n".join(ops).encode("latin-1")
            objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
            content_id = len(objs)
            objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.w} {self.h}] "
                        f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>")
            kids.append(f"{len(objs)} 0 R")
        objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
        info = f"<< /Producer (Chilli Gavva Product Builder) /Title ({_pdf_text(title)}) " \
               f"/ChilliStyle ({self.style_key or 'none'}) >>"
        objs.append(info)
        out, offsets = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"), []
        for i, obj in enumerate(objs, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + (obj if isinstance(obj, bytes) else obj.encode("latin-1")) + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
        out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {len(objs)} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n").encode()
        with open(path, "wb") as f:
            f.write(bytes(out))


def pdf_style(path):
    """'A' or 'B' as written by Pdf.save, or None for older files."""
    try:
        with open(path, "rb") as f:
            m = re.search(rb"/ChilliStyle \((\w+)\)", f.read())
        return m.group(1).decode() if m and m.group(1).decode() in STYLES else None
    except OSError:
        return None


# ---- page headers -------------------------------------------------------------------------------------
def _header(pdf, title, subtitle="", right=None):
    """Title band (Style A) or soft title (Style B). Returns the y where the content starts."""
    st, w, h = pdf.style, pdf.w, pdf.h
    room = w - 2 * MARGIN - (170 if right else 0)
    lines, size = fit_text(title, 26, room, True, max_lines=2, min_size=18)
    y = h - 30 - size
    title_ys = [y - i * size * 1.12 for i in range(len(lines))]
    y = title_ys[-1]
    subs = wrap_text(subtitle, 10.5, room)[:3] if subtitle else []
    sub_ys = [y - 18 - 13 * i for i in range(len(subs))]
    bottom = (sub_ys[-1] if subs else y) - 20
    if st["round"]:
        title_color, sub_color = st["accent"], st["muted"]
    else:
        pdf.fill(0, bottom, w, h - bottom, st["accent"])
        title_color, sub_color = st["on_accent"], st["on_accent_soft"]
    for line, yy in zip(lines, title_ys):
        pdf.text(MARGIN, yy, line, size, True, title_color)
    for line, yy in zip(subs, sub_ys):
        pdf.text(MARGIN, yy, line, 10.5, False, sub_color)
    if right == "date_energy":
        _date_energy(pdf, title_ys[0])
    return bottom - (20 if st["round"] else 22)


def _date_energy(pdf, y_top):
    st, x_r = pdf.style, pdf.w - MARGIN
    if st["round"]:
        pdf.round_rect(x_r - 168, y_top - 16, 168, 30, 15, fill=st["lavender"])
        ink = st["ink"]
        y1 = y2 = y_top - 4
        x_date, x_energy = x_r - 156, x_r - 82
    else:
        ink = st["on_accent"]
        y1, y2 = y_top + 4, y_top - 18
        x_date, x_energy = x_r - 150, x_r - 150
    pdf.text(x_date, y1, "Date", 9.5, color=ink)
    pdf.line(x_date + 24, y1 - 2, x_date + (66 if st["round"] else 150), y1 - 2, color=ink, width=0.6)
    if st["round"]:
        pdf.text(x_energy, y2, "Energy", 9.5, color=ink)
        cx = x_energy + 36
    else:
        pdf.text(x_energy + 46, y2, "Energy", 9.5, color=ink)
        cx = x_energy + 86
    for i in range(5):
        pdf.circle(cx + 3 + i * 8.5, y2 + 3, 2.6, stroke=ink, width=0.6)


# ---- blocks: one section = heading + rows; the layout places them in 1 or 2 columns ------------------------
ROW = 20
GAP = 14


class _Block:
    def __init__(self, sec, index, style, colw):
        self.sec, self.index, self.st = sec, index, style
        self.round = style["round"]
        self.pad_x = 12 if self.round else 0
        self.pad_top = 12 if self.round else 0
        self.pad_bottom = 10 if self.round else 2
        self.inner = colw - 2 * self.pad_x
        self.heading = (sec.get("heading") or "").upper()
        self.head_lines, self.head_size = (fit_text(self.heading, 11, self.inner - (0 if self.round else 16), True,
                                                    max_lines=2, min_size=10.5, spacing=0.9)
                                           if self.heading else ([], 11))
        self.head_h = (13 * len(self.head_lines) + (8 if self.round else 16)) if self.head_lines else 0
        self.rows = self._rows()
        self.grow_kind = {"table": "trow", "nnl": "nnl", "checkboxes": "checkbox"}.get(sec.get("type"), "line")
        self.column = sec.get("column")

    def _rows(self):
        sec, iw = self.sec, self.inner
        stype, items, count = sec.get("type"), sec.get("items") or [], int(sec.get("count") or 3)
        rows = []
        if stype == "checklist" and items:
            for item in items:
                lines = wrap_text(item, 10.5, iw - 18)
                rows.append(("check", lines, ROW + 12.5 * (len(lines) - 1)))
        elif stype == "checkboxes":
            rows = [("checkbox", None, ROW)] * count
        elif stype == "schedule" and items:
            for item in items:
                lines = wrap_text(item, 9.5, 92, True)
                rows.append(("sched", lines, ROW + 11.5 * (len(lines) - 1)))
        elif stype == "numbered":
            for i, item in enumerate(items, 1):
                lines = wrap_text(item, 10.5, iw - 20)
                rows.append(("num", (i, lines), ROW + 12.5 * (len(lines) - 1)))
        elif stype == "index":
            for label, pages in items:
                lines = wrap_text(label, 10.5, iw - 80)
                rows.append(("index", (pages, lines), ROW + 12.5 * (len(lines) - 1)))
        elif stype == "bullets":
            for item in items:
                lines = wrap_text(item, 10, iw - 14)
                rows.append(("bullet", lines, 6 + 12.5 * len(lines)))
        elif stype == "table":
            rows = [("thead", sec.get("columns") or [], 18)] + [("trow", None, ROW + 1)] * count
        elif stype == "nnl":
            rows = [("nnl_head", None, 16)] + [("nnl", None, ROW)] * count
        elif stype == "blocks":
            size = min(26.0, (iw - 7 * 5) / 8)
            rows = [("blocks", size, 2 * size + 10)]
            note = sec.get("note")
            if note:
                rows += [("caption", line, 11) for line in wrap_text(note, 7.5, iw)]
        else:
            rows = [("line", None, ROW)] * count
        return rows

    def height(self, rows):
        return self.pad_top + self.head_h + sum(r[2] for r in rows) + self.pad_bottom

    def grow_row(self):
        return (self.grow_kind, None, ROW + (1 if self.grow_kind == "trow" else 0))

    def draw(self, pdf, x, y_top, colw, height, rows, cont):
        st = self.st
        ink, line_c, accent = st["ink"], st["line"], st["accent"]
        if self.round:
            pdf.round_rect(x, y_top - height, colw, height, 12, fill=st["boxes"][self.index % 3])
        y = y_top - self.pad_top
        ix = x + self.pad_x
        if self.head_lines:
            lines = self.head_lines if not cont else wrap_text(self.heading + " (CONT.)", self.head_size,
                                                               self.inner, True, 0.9)
            if not self.round:
                bar_h = 13 * len(lines) + 9
                pdf.round_rect(x, y - bar_h, colw, bar_h, 3, fill=st["bar"])
                for i, line in enumerate(lines):
                    pdf.text(ix + 8, y - 15 - 13 * i, line, self.head_size, True, accent, 0.9)
            else:
                for i, line in enumerate(lines):
                    pdf.text(ix, y - 11 - 13 * i, line, self.head_size, True, accent, 0.9)
            y -= 13 * len(lines) + (8 if self.round else 16)
        right = ix + self.inner
        for kind, data, h in rows:
            bottom = y - h
            if kind == "check":
                pdf.box(ix, y - 14, 9, ink, 0.8)
                for i, line in enumerate(data):
                    pdf.text(ix + 17, y - 13 - 12.5 * i, line, 10.5, color=ink)
                pdf.line(ix, bottom + 2, right, bottom + 2, color=line_c)
            elif kind == "checkbox":
                pdf.box(ix, y - 14, 9, ink, 0.8)
                pdf.line(ix, bottom + 2, right, bottom + 2, color=line_c)
            elif kind == "sched":
                for i, line in enumerate(data):
                    pdf.text(ix, y - 13 - 11.5 * i, line, 9.5, True, ink)
                pdf.line(ix + 100, bottom + 2, right, bottom + 2, color=line_c)
            elif kind == "num":
                number, lines = data
                pdf.text(ix, y - 13, f"{number}.", 10.5, True, accent)
                for i, line in enumerate(lines):
                    pdf.text(ix + 18, y - 13 - 12.5 * i, line, 10.5, color=ink)
                pdf.line(ix, bottom + 2, right, bottom + 2, color=line_c)
            elif kind == "index":
                pages, lines = data
                for i, line in enumerate(lines):
                    pdf.text(ix, y - 13 - 12.5 * i, line, 10.5, color=ink)
                pdf.text_right(right, y - 13, pages, 10, True, accent)
                pdf.line(ix, bottom + 2, right, bottom + 2, color=line_c)
            elif kind == "bullet":
                pdf.circle(ix + 3, y - 9.5, 1.8, fill=accent)
                for i, line in enumerate(data):
                    pdf.text(ix + 12, y - 13 - 12.5 * i, line, 10, color=ink)
            elif kind == "thead":
                xs = [ix, ix + self.inner * 0.30, ix + self.inner * 0.64]
                for cx, label in zip(xs, data):
                    pdf.text(cx + 3, y - 12, label, 9, True, ink)
                pdf.line(ix, bottom + 2, right, bottom + 2, color=ink, width=0.9)
            elif kind == "nnl_head":
                third = (self.inner - 20) / 3
                for i, label in enumerate(("NOW", "NEXT", "LATER")):
                    pdf.text(ix + i * (third + 10), y - 11, label, 9, True, accent)
            elif kind == "nnl":
                third = (self.inner - 20) / 3
                for i in range(3):
                    x0 = ix + i * (third + 10)
                    pdf.line(x0, bottom + 2, x0 + third, bottom + 2, color=line_c)
            elif kind == "blocks":
                size = data
                for r in range(2):
                    for c in range(8):
                        bx = ix + c * (size + 5)
                        by = y - 4 - (r + 1) * size - r * 4
                        pdf.round_rect(bx, by, size, size, 5 if self.round else 3,
                                       fill="#ffffff" if self.round else None, stroke=accent, width=1)
            elif kind == "caption":
                pdf.text(ix, y - 9, data, 7.5, color=st["muted"])
            else:   # "line" and "trow"
                pdf.line(ix, bottom + 2, right, bottom + 2, color=line_c)
            y = bottom


def _pack(blocks, ncols, col_h):
    """Place blocks in columns, page by page. A block too tall for the room left is split by rows and
    continues ('cont.') in the next column or on the next page. Returns pages: [[ [placements] per column ]]."""
    if ncols == 2:
        if all(b.column in (1, 2) for b in blocks):
            groups = [[b for b in blocks if b.column == c] for c in (1, 2)]
            for g in groups:   # the last block grows anyway, so it may start with fewer writing lines
                last = g[-1] if g else None
                while last and len(last.rows) > 1 and last.rows[-1][0] == last.grow_kind and \
                        sum(b.height(b.rows) for b in g) + GAP * (len(g) - 1) > col_h:
                    last.rows = last.rows[:-1]
        else:
            groups = None
            heights = [b.height(b.rows) for b in blocks]
            best = None
            for k in range(1, len(blocks)):
                left = sum(heights[:k]) + GAP * (k - 1)
                right = sum(heights[k:]) + GAP * (len(blocks) - k - 1)
                if left <= col_h and right <= col_h and (best is None or max(left, right) < best[0]):
                    best = (max(left, right), k)
            if best:
                groups = [blocks[:best[1]], blocks[best[1]:]]
        if groups and all(sum(b.height(b.rows) for b in g) + GAP * (len(g) - 1) <= col_h for g in groups):
            return [[[(b, list(b.rows), False) for b in g] for g in groups]]
    pages = [[[] for _ in range(ncols)]]
    state = {"col": 0, "used": 0.0}

    def advance():
        state["col"] += 1
        state["used"] = 0.0
        if state["col"] == ncols:
            pages.append([[] for _ in range(ncols)])
            state["col"] = 0

    for b in blocks:
        rows, cont = list(b.rows), False
        while True:
            gap = GAP if state["used"] else 0
            avail = col_h - state["used"] - gap
            if b.height(rows) <= avail:
                pages[-1][state["col"]].append((b, rows, cont))
                state["used"] += gap + b.height(rows)
                break
            k, acc = 0, b.height([])
            while k < len(rows) and acc + rows[k][2] <= avail:
                acc += rows[k][2]
                k += 1
            if k >= 2 or (state["used"] == 0 and k >= 1):
                pages[-1][state["col"]].append((b, rows[:k], cont))
                rows, cont = rows[k:], True
            elif state["used"] == 0:      # nothing fits even in an empty column: draw it anyway
                pages[-1][state["col"]].append((b, rows, cont))
                state["used"] = col_h
                break
            advance()
    return pages


def _notes_block(style, colw, index):
    return _Block({"heading": "Notes", "type": "lines", "count": 1}, index, style, colw)


def render_page(pdf, page, header_right=None):
    """Draw one planner page (and its 'cont.' pages). The last block of every column grows with writing
    lines down to the footer, so no page ends with a big empty area."""
    st = pdf.style
    width = pdf.w - 2 * MARGIN
    sections = page["sections"]
    ncols = 2 if len(sections) >= 4 or page.get("columns") == 2 else 1
    colw = (width - 18) / 2 if ncols == 2 else width
    blocks = [_Block(sec, i, st, colw) for i, sec in enumerate(sections)]
    first = True
    pdf.new_page()
    top = _header(pdf, page["title"], page.get("subtitle", ""), header_right)
    col_h = top - BOTTOM
    for p_i, columns in enumerate(_pack(blocks, ncols, col_h)):
        if p_i:
            pdf.new_page()
            top = _header(pdf, page["title"] + " (cont.)", "")
            col_h = top - BOTTOM
        first = False
        for c_i, placed in enumerate(columns):
            if not placed:
                placed.append((_notes_block(st, colw, c_i + 1), [], False))
            x = MARGIN + c_i * (colw + 18)
            y = top
            for n, (b, rows, cont) in enumerate(placed):
                h = b.height(rows)
                if n == len(placed) - 1:          # the last block grows down to the footer
                    room = y - BOTTOM
                    rows = list(rows)
                    while b.height(rows + [b.grow_row()]) <= room:
                        rows.append(b.grow_row())
                    h = room
                b.draw(pdf, x, y, colw, h, rows, cont)
                y -= h + GAP
    return first


# ---- the code-made pages: Today (ADHD / time blindness), cover, How to use ---------------------------
TODAY_PAGE = {
    "title": "Today", "subtitle": "Plan by anchors, not by the clock", "columns": 2, "header_right": "date_energy",
    "sections": [
        {"heading": "Time anchors", "type": "checklist", "column": 1,
         "items": ["Wake up / breakfast", "Medication or water", "Leave the house / start work", "Lunch",
                   "Wind-down alarm"]},
        {"heading": "Top 3 (only 3)", "type": "checkboxes", "count": 3, "column": 1},
        {"heading": "Now · Next · Later", "type": "nnl", "count": 2, "column": 1},
        {"heading": "Waiting for / errands", "type": "checkboxes", "count": 4, "column": 1},
        {"heading": "Wins today (even small)", "type": "lines", "count": 3, "column": 1},
        {"heading": "Guess vs real time", "type": "table", "columns": ["Task", "My guess", "Real time"],
         "count": 4, "column": 2},
        {"heading": "Focus blocks (15 min each)", "type": "blocks", "column": 2,
         "note": "Colour one box for every 15 minutes you work. 16 boxes = 4 hours."},
        {"heading": "Tomorrow’s first step", "type": "lines", "count": 2, "column": 2},
        {"heading": "Brain dump", "type": "lines", "count": 4, "column": 2},
    ]}


def _cover(pdf, spec, inside, year=None):
    """A simple cover: a label, the product name, who it is for, and what is inside (with page numbers)."""
    st, w, h = pdf.style, pdf.w, pdf.h
    pdf.new_page()
    label = f"PRINTABLE PLANNER · {year}" if year else "PRINTABLE PLANNER"
    room = w - 2 * MARGIN - (0 if not st["round"] else 40)
    lines, size = fit_text(spec.get("name") or spec["listing"]["title"], 36, room, True, max_lines=3, min_size=24)
    buyer = spec.get("listing", {}).get("buyer") or ""
    buyer_lines = wrap_text(f"Made for {buyer}", 12, room)[:3] if buyer else []
    block_h = 60 + len(lines) * size * 1.12 + 18 * len(buyer_lines) + 40
    band_bottom = h - max(h * 0.42, block_h + 60)
    if st["round"]:
        pdf.round_rect(MARGIN, band_bottom, w - 2 * MARGIN, h - band_bottom - MARGIN, 22, fill=st["header"])
        tx, title_c, sub_c, label_c = MARGIN + 24, st["accent"], st["ink"], st["muted"]
    else:
        pdf.fill(0, band_bottom, w, h - band_bottom, st["accent"])
        tx, title_c, sub_c, label_c = MARGIN, st["on_accent"], st["on_accent_soft"], st["on_accent_soft"]
    y = band_bottom + (h - band_bottom) * 0.5 + (len(lines) * size * 1.12) / 2
    pdf.text(tx, y + 26, label, 10, False, label_c, 2.4)
    for line in lines:
        pdf.text(tx, y - size * 0.2, line, size, True, title_c)
        y -= size * 1.12
    y -= 10
    for line in buyer_lines:
        pdf.text(tx, y, line, 12, False, sub_c)
        y -= 16
    width = w - 2 * MARGIN
    top = band_bottom - 26
    blocks = [_Block({"heading": "What’s inside", "type": "index", "items": inside}, 0, st, width),
              _Block({"heading": "This planner belongs to", "type": "lines", "count": 2}, 1, st, width)]
    columns = _pack(blocks, 1, top - BOTTOM)
    yy = top
    placed = columns[0][0]
    for n, (b, rows, cont) in enumerate(placed):
        hh = b.height(rows)
        if n == len(placed) - 1:
            rows = list(rows)
            while b.height(rows + [b.grow_row()]) <= yy - BOTTOM:
                rows.append(b.grow_row())
            hh = yy - BOTTOM
        b.draw(pdf, MARGIN, yy, width, hh, rows, cont)
        yy -= hh + GAP


def _how_to(pdf, spec, dated):
    steps = [s for s in (spec.get("instructions") or []) if s][:6] or [
        "Print the pages you need at 100% (actual size).",
        "Start with the page that fits your day, and keep the rest for later."]
    tips = ["Both paper sizes are included: print the A4 file or the US Letter file at 100% (actual size).",
            "Print as many copies as you need for your own personal use."]
    if dated:
        tips.append("Every date was made by a calendar program: weeks start on Sunday, weekends are shaded.")
    page = {"title": "How to use", "subtitle": "A few minutes to set up, then just print and write",
            "sections": [{"heading": "Steps", "type": "numbered", "items": steps},
                         {"heading": "Printing tips", "type": "bullets", "items": tips},
                         {"heading": "My notes", "type": "lines", "count": 3}]}
    render_page(pdf, page)


def _inside(titles, offset):
    """[(label, 'Page N' / 'Pages N-M')] from the content page titles, grouping cont. pages and months."""
    groups = []
    for i, t in enumerate(titles):
        number = offset + i + 1
        base = t.replace(" (cont.)", "")
        month = re.fullmatch(r"(\w+) (\d{4})", base)
        key = "months" if month and month.group(1) in calendar.month_name[1:] else base
        if groups and groups[-1][0] == key:
            groups[-1][2] = number
        else:
            groups.append([key, number, number, base])
    out = []
    for key, a, b, base in groups:
        label = f"January to December {base.split()[-1]} (one page per month)" if key == "months" else base
        out.append((label, f"Page {a}" if a == b else f"Pages {a}-{b}"))
    return out


def _finish(path, spec, size, body, year=None):
    """Cover + How to use + the body pages; page numbers in the cover index are exact."""
    style = style_for(spec)
    footer = f"{spec.get('name') or spec['listing']['title']} · {size} · for personal use"
    content = Pdf(size, footer, style)
    titles = body(content)
    offset = 2
    for _ in range(3):
        front = Pdf(size, footer, style)
        _cover(front, spec, _inside(titles, offset), year)
        _how_to(front, spec, bool(year))
        if len(front.pages) == offset:
            break
        offset = len(front.pages)
    front.pages += content.pages
    front.save(path, spec.get("name") or "")
    return len(front.pages)


def _content_pages(spec):
    pages = list(spec.get("pages") or [])
    if spec.get("daily_page"):
        pages = [TODAY_PAGE] + pages
    return pages


def build_planner_pdf(path, spec, size):
    def body(pdf):
        titles = []
        for page in _content_pages(spec):
            before = len(pdf.pages)
            render_page(pdf, page, page.get("header_right"))
            titles += [page["title"]] + [page["title"] + " (cont.)"] * (len(pdf.pages) - before - 1)
        return titles
    return _finish(path, spec, size, body)


# ---- dated planners: every date comes from code, never from the AI ------------------------------------
MONTH_BOXES = ["This month’s focus", "Key dates", "Notes"]


def month_weeks(year, month):
    """Weeks of a month, Sunday first: lists of 7 day numbers (0 = blank). Made by Python's calendar."""
    return calendar.Calendar(firstweekday=6).monthdayscalendar(year, month)


def month_cells(year, month):
    """6 weeks x 7 days of (day, in_this_month) for a Sunday-first grid, other months' days included."""
    dates = list(calendar.Calendar(firstweekday=6).itermonthdates(year, month))
    while len(dates) < 42:
        import datetime
        dates.append(dates[-1] + datetime.timedelta(days=1))
    return [[(d.day, d.month == month) for d in dates[r * 7:(r + 1) * 7]] for r in range(6)]


def _month_page(pdf, year, month):
    st, w, h = pdf.style, pdf.w, pdf.h
    width = w - 2 * MARGIN
    pdf.new_page()
    name = calendar.month_name[month]
    if st["round"]:
        box_top = h - 34
        pdf.round_rect(MARGIN, box_top - 84, width, 84, 20, fill=st["header"])
        pdf.text(MARGIN + 22, box_top - 26, "MONTHLY PLAN", 9.5, False, st["muted"], 2.4)
        pdf.text(MARGIN + 22, box_top - 64, name, 30, True, st["accent"])
        pdf.circle(MARGIN + width - 46, box_top - 42, 30, fill="#ffffff")
        pdf.text_center(MARGIN + width - 46, box_top - 48, str(year), 18, True, st["ink"])
        top = box_top - 84 - 18
    else:
        band = 104
        pdf.fill(0, h - band, w, band, st["accent"])
        pdf.text(MARGIN, h - 34, "MONTHLY PLAN", 9.5, False, st["on_accent_soft"], 2.4)
        pdf.text(MARGIN, h - 74, name, 32, True, st["on_accent"])
        pdf.text_right(w - MARGIN, h - 74, str(year), 28, True, st["on_accent_soft"])
        top = h - band - 24
    boxes_h = round((top - BOTTOM) * 0.27)
    grid_bottom = BOTTOM + boxes_h + 22
    col = width / 7
    head_h = 17
    if st["round"]:
        for i, day in enumerate(WEEKDAYS_SUN):
            pdf.round_rect(MARGIN + i * col + 2, top - head_h, col - 4, head_h, 8.5, fill=st["pill"])
            pdf.text_center(MARGIN + i * col + col / 2, top - 12, day, 9, True, st["ink"])
    else:
        pdf.fill(MARGIN, top - head_h, width, head_h, st["accent"])
        for i, day in enumerate(WEEKDAYS_SUN):
            pdf.text_center(MARGIN + i * col + col / 2, top - 12, day, 9, True, st["on_accent"])
    grid_top = top - head_h - (5 if st["round"] else 0)
    row_h = (grid_top - grid_bottom) / 6
    for r, week in enumerate(month_cells(year, month)):
        for c, (day, here) in enumerate(week):
            x, y = MARGIN + c * col, grid_top - (r + 1) * row_h
            weekend = c in (0, 6) and here
            if st["round"]:
                pdf.round_rect(x + 2, y + 2, col - 4, row_h - 4, 8,
                               fill=st["weekend"] if weekend else "#ffffff", stroke=st["cell"], width=0.8)
            else:
                if weekend:
                    pdf.fill(x, y, col, row_h, st["weekend"])
                pdf.rect(x, y, col, row_h, color=st["cell"], width=0.7)
            if here:
                pdf.text(x + 6, y + row_h - 14, str(day), 9, True, st["ink"])
            else:
                pdf.text(x + 6, y + row_h - 14, str(day), 8, False, st["faded"])
    # the three boxes at the bottom
    gap = 16
    bw = (width - 2 * gap) / 3
    for i, heading in enumerate(MONTH_BOXES):
        x = MARGIN + i * (bw + gap)
        box_top = BOTTOM + boxes_h
        if st["round"]:
            pdf.round_rect(x, BOTTOM, bw, boxes_h, 12, fill=st["pill"])
            hx, y = x + 12, box_top - 20
        else:
            pdf.fill(x, box_top - 2.5, bw, 2.5, st["accent"])
            hx, y = x, box_top - 20
        lines, size = fit_text(heading.upper(), 10.5, bw - (24 if st["round"] else 0), True, 2, 10.5, 1)
        for line in lines:
            pdf.text(hx, y, line, size, True, st["accent"], 1)
            y -= 13
        y -= 6
        x0, x1 = (x + 12, x + bw - 12) if st["round"] else (x, x + bw)
        while y - ROW >= BOTTOM + (8 if st["round"] else 0):
            y -= ROW
            pdf.line(x0, y, x1, y, color=st["line"])


def _year_page(pdf, year):
    st = pdf.style
    width = pdf.w - 2 * MARGIN
    pdf.new_page()
    top = _header(pdf, f"{year} at a glance", "Every month of the year. Weeks start on Sunday.")
    cols, rows_n, gap = 3, 4, 14
    cw = (width - gap * (cols - 1)) / cols
    ch = (top - BOTTOM - gap * (rows_n - 1)) / rows_n
    for m in range(1, 13):
        r, c = divmod(m - 1, cols)
        x0, y0 = MARGIN + c * (cw + gap), top - r * (ch + gap)
        if st["round"]:
            pdf.round_rect(x0, y0 - ch, cw, ch, 10, fill=st["boxes"][(m - 1) % 3])
            pad = 8
        else:
            pdf.rect(x0, y0 - ch, cw, ch, color=st["cell"], width=0.7)
            pdf.fill(x0, y0 - 18, cw, 18, st["bar"])
            pad = 6
        pdf.text(x0 + pad, y0 - 13, calendar.month_name[m].upper(), 10.5, True, st["accent"], 0.8)
        gx, gw = x0 + pad, cw - 2 * pad
        colw = gw / 7
        for i, day in enumerate(WEEKDAYS_SUN):
            pdf.text_center(gx + i * colw + colw / 2, y0 - 30, day[0], 6.5, True, st["muted"])
        weeks = month_cells(year, m)
        row_h = (ch - 40) / 6
        for wr, week in enumerate(weeks):
            for wc, (day, here) in enumerate(week):
                if not here:
                    continue
                cy = y0 - 36 - (wr + 1) * row_h
                if wc in (0, 6):
                    pdf.fill(gx + wc * colw + 1, cy + 1, colw - 2, row_h - 1, st["weekend"])
                pdf.text_center(gx + wc * colw + colw / 2, cy + row_h / 2 - 2.5, str(day), 7, False, st["ink"])


def build_dated_planner_pdf(path, spec, size, year=PLANNER_YEAR):
    def body(pdf):
        _year_page(pdf, year)
        titles = [f"{year} at a glance"]
        for m in range(1, 13):
            _month_page(pdf, year, m)
            titles.append(f"{calendar.month_name[m]} {year}")
        for page in spec.get("pages") or []:
            before = len(pdf.pages)
            render_page(pdf, page)
            titles += [page["title"]] + [page["title"] + " (cont.)"] * (len(pdf.pages) - before - 1)
        return titles
    return _finish(path, spec, size, body, year)
