"""mockups.py — simple listing images for a built product (Python standard library only).

For each product folder the Product Builder writes:
  cover.png        1280x720   (store cover: Gumroad / Payhip)
  preview-1.png .. preview-3.png  1280x720  (the first pages of the product, drawn as clean mockups)
  pinterest.png    1000x1500  (tall pin)

Design: flat colours, a white "paper" card, the product name in a built-in pixel font. No photos, no
logos, and no brand names or trademarks of other companies (they are replaced by generic words).
"""
import re
import struct
import unicodedata
import zlib

COVER_SIZE = (1280, 720)
PREVIEW_SIZE = (1280, 720)
PIN_SIZE = (1000, 1500)

PALETTES = {  # background, accent, ink
    "sheets": ((232, 244, 236), (46, 125, 82), (33, 37, 41)),
    "planner": ((251, 238, 230), (201, 98, 66), (45, 35, 33)),
    "dated_planner": ((251, 238, 230), (201, 98, 66), (45, 35, 33)),   # same look as the planners
    "notion": ((236, 238, 246), (72, 84, 160), (30, 32, 44)),
    "resume": ((234, 241, 248), (40, 96, 150), (28, 36, 46)),
}
KIND_LABEL = {"dated_planner": "2027 printable planner", "sheets": "Spreadsheet template", "planner": "Printable planner",
              "notion": "Digital workspace template", "resume": "Resume template bundle"}

# Brand names of other companies are never drawn on the images.
_BRANDS = [(r"google\s+sheets?", "spreadsheet"), (r"google\s+docs?", "document"), (r"microsoft\s+excel", "spreadsheet"),
           (r"microsoft\s+word", "document"), (r"\bexcel\b", "spreadsheet"), (r"\bnotion\b", ""),
           (r"\bcanva\b", ""), (r"\betsy\b", ""), (r"\bgumroad\b", ""), (r"\bpayhip\b", ""), (r"\bamazon\b", ""),
           (r"\bapple\s+numbers\b", "spreadsheet"), (r"\bgoodnotes\b", "note app"), (r"\bipad\b", "tablet"),
           (r"\blinkedin\b", "professional profile"), (r"\bgoogle\b", ""), (r"\bmicrosoft\b", ""),
           (r"\bchatgpt\b", ""), (r"\bopenai\b", "")]


_BRAND_WORDS = (r"google\s+sheets?|google\s+docs?|microsoft\s+excel|microsoft\s+word|excel|notion|canva|etsy|gumroad"
                r"|payhip|goodnotes|apple\s+numbers|ipad")


_PLAIN = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
          "\u2011": "-", "\u2010": "-", "\u2026": "...", "\u00a0": " ", "\u2022": "-"}


def plain(text):
    """Curly quotes and dashes become plain ones (the pixel font has them); nothing is silently dropped."""
    out = "".join(_PLAIN.get(ch, ch) for ch in str(text or ""))
    return unicodedata.normalize("NFKD", out).encode("ascii", "ignore").decode()


def fit(text, n):
    """Shorten for a narrow table cell, with a visible '...' (so a cut is never hidden)."""
    return text if len(text) <= n else text[:max(1, n - 3)].rstrip() + "..."


def no_brands(text):
    out = re.sub(rf"\s+(?:for|in|on|with|using)\s+(?:{_BRAND_WORDS})\b", "", str(text or ""), flags=re.IGNORECASE)
    for pattern, repl in _BRANDS:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    out = plain(out)
    return re.sub(r"\s{2,}", " ", out).strip(" -|,")


# ---- 5x8 pixel font (columns, bit 0 = top row), printable ASCII 32..126 ----------------------------
_FONT_HEX = (
    "0000000000 00005f0000 0007000700 147f147f14 242a7f2a12 2313086462 3649562050 0000070000 "
    "001c224100 0041221c00 2a1c7f1c2a 08083e0808 0050300000 0808080808 0060600000 2010080402 "
    "3e5149453e 00427f4000 4261514946 2141454b31 1814127f10 2745454539 3c4a494930 0171090503 "
    "3649494936 064949291e 0036360000 0056360000 0814224100 1414141414 0041221408 0201510906 "
    "3e415d594e 7c1211127c 7f49494936 3e41414122 7f4141413e 7f49494941 7f09090901 3e41415173 "
    "7f0808087f 00417f4100 2040413f01 7f08142241 7f40404040 7f021c027f 7f0408107f 3e4141413e "
    "7f09090906 3e4151215e 7f09192946 2649494932 03017f0103 3f4040403f 1f2040201f 3f4038403f "
    "6314081463 0304780403 6159494d43 007f414141 0204081020 004141417f 0402010204 4040404040 "
    "0003070800 2054547840 7f28444438 3844444428 384444287f 3854545418 00087e0902 18a4a4a47c "
    "7f08040478 00447d4000 4080847d00 7f10284400 00417f4000 7c04780478 7c08040478 3844444438 "
    "fc24242418 18242418fc 7c08040408 4854545424 04043f4424 3c4040207c 1c2040201c 3c4030403c "
    "4428102844 1ca0a0a07c 4464544c44 0008364100 00007f0000 0041360800 0201020402")
_FONT = {chr(32 + i): bytes.fromhex(h) for i, h in enumerate(_FONT_HEX.split())}


class Canvas:
    def __init__(self, width, height, color):
        self.w, self.h = width, height
        self.rows = [bytearray(bytes(color) * width) for _ in range(height)]

    def rect(self, x, y, w, h, color):
        x0, y0, x1, y1 = max(0, x), max(0, y), min(self.w, x + w), min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        line = bytes(color) * (x1 - x0)
        for yy in range(y0, y1):
            self.rows[yy][x0 * 3:x1 * 3] = line

    def outline(self, x, y, w, h, color, t=2):
        self.rect(x, y, w, t, color)
        self.rect(x, y + h - t, w, t, color)
        self.rect(x, y, t, h, color)
        self.rect(x + w - t, y, t, h, color)

    def text(self, x, y, text, color, scale=2):
        """Draw one line of text. Returns its width in pixels."""
        cx = x
        for ch in text:
            glyph = _FONT.get(ch, _FONT["?"])
            for col, bits in enumerate(glyph):
                for row in range(8):
                    if bits >> row & 1:
                        self.rect(cx + col * scale, y + row * scale, scale, scale, color)
            cx += 6 * scale
        return cx - x

    def png(self):
        raw = b"".join(b"\x00" + bytes(r) for r in self.rows)

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def text_width(text, scale):
    return len(text) * 6 * scale


def wrap(text, max_chars, max_lines):
    words, lines, cur = text.split(), [], ""
    for word in words:
        word = word[:max_chars]
        if cur and len(cur) + 1 + len(word) > max_chars:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:max_chars - 3].rstrip() + "..."
    return lines


def _fit_title(name, box_w, max_lines, start_scale):
    """Largest scale (down to 3) at which the name fits in max_lines lines."""
    for scale in range(start_scale, 2, -1):
        per_line = max(4, box_w // (6 * scale))
        lines = wrap(name, per_line, max_lines)
        if not lines[-1].endswith("...") or scale == 3:
            return lines, scale
    return wrap(name, box_w // 18, max_lines), 3


def _palette(kind):
    return PALETTES.get(kind, PALETTES["sheets"])


# ---- content for the preview pages ------------------------------------------------------------------
def preview_pages(spec):
    """Up to 3 pages: {"title", "columns" (table) or "lines" (list)} taken from the start of the product."""
    kind = spec.get("kind")
    pages = []
    if kind == "sheets":
        for s in spec.get("sheets", [])[:3]:
            pages.append({"title": s.get("name", ""), "columns": [c.get("name", "") if isinstance(c, dict) else str(c)
                                                                  for c in s.get("columns", [])][:6],
                          "rows": [[str(v) for v in r] for r in (s.get("rows") or [])[:6]]})
    elif kind == "planner":
        for p in spec.get("pages", [])[:3]:
            lines = []
            for sec in p.get("sections", []):
                lines.append(("h", sec.get("heading") or ""))
                lines += [("box" if sec.get("type") == "checklist" else "li", i) for i in sec.get("items", [])[:4]]
                if not sec.get("items"):
                    lines += [("line", "")] * min(3, sec.get("count", 3))
            pages.append({"title": p.get("title", ""), "lines": lines})
    elif kind == "notion":
        for p in spec.get("pages", [])[:2]:
            body = [l.strip("#*- ").strip() for l in p.get("markdown", "").splitlines() if l.strip()][1:9]
            pages.append({"title": p.get("title", ""), "lines": [("li", b) for b in body]})
        for d in spec.get("databases", [])[:3 - len(pages)]:
            pages.append({"title": d.get("name", ""), "columns": d.get("columns", [])[:6],
                          "rows": d.get("rows", [])[:4]})
    elif kind == "dated_planner":   # dates come from the real calendar, like the PDF itself
        import calendar
        year = spec.get("year", 2027)
        days = [("li", f"{calendar.month_name[m]}: {calendar.monthrange(year, m)[1]} days, starts on a "
                       f"{calendar.day_name[calendar.monthrange(year, m)[0]]}") for m in range(1, 7)]
        pages.append({"title": f"{year} at a glance", "lines": days})
        jan = [("pre", "".join(f"{d:<4}" for d in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")))]
        jan += [("pre", "".join(f"{d:<4}" if d else "    " for d in week))
                for week in calendar.Calendar(firstweekday=6).monthdayscalendar(year, 1)]
        jan += [("h", h) for h in spec.get("monthly_sections", [])]
        pages.append({"title": f"January {year}", "lines": jan})
        for p in spec.get("pages", [])[:1]:
            lines = []
            for sec in p.get("sections", []):
                lines.append(("h", sec.get("heading") or ""))
                lines += [("box" if sec.get("type") == "checklist" else "li", i) for i in sec.get("items", [])[:4]]
                if not sec.get("items"):
                    lines += [("line", "")] * min(3, sec.get("count", 3))
            pages.append({"title": p.get("title", ""), "lines": lines})
    elif kind == "resume":
        lines = []
        for s in spec.get("resume", [])[:4]:
            lines.append(("h", s.get("heading", "")))
            lines += [("li", b) for b in s.get("bullets", [])[:2]]
        pages.append({"title": "Resume", "lines": lines})
        pages.append({"title": "Cover letter", "lines": [("p", p) for p in spec.get("cover_letter", [])[:3]]})
        tracker = spec.get("tracker") or {}
        pages.append({"title": tracker.get("name", "Job Applications"),
                      "columns": [c.get("name", "") if isinstance(c, dict) else str(c)
                                  for c in tracker.get("columns", [])][:6]})
    pages = [p for p in pages if p.get("title")]
    while pages and len(pages) < 3:  # always 3 previews: repeat the first pages if the product is short
        pages.append(dict(pages[len(pages) % len(pages)]))
    return pages[:3] or [{"title": spec.get("name", "Preview"), "lines": []}]


def _draw_page(c, page, x, y, w, h, accent, ink):
    c.rect(x + 10, y + 10, w, h, (200, 200, 200))   # soft shadow
    c.rect(x, y, w, h, (255, 255, 255))
    c.rect(x, y, w, 12, accent)
    title = no_brands(page["title"])
    title_lines = wrap(title, max(8, (w - 60) // 24), 2)
    if title_lines and title_lines[-1].endswith("...") and len(title) > 3:   # too long: smaller, never cut
        title_lines = wrap(title, max(8, (w - 60) // 18), 2)
        size = 3
    else:
        size = 4
    for i, line in enumerate(title_lines):
        c.text(x + 30, y + 40 + i * 12 * size, line, ink, size)
    top = y + 100 + (len(title_lines) - 1) * 12 * size
    grey, light = (120, 124, 130), (226, 229, 234)
    if page.get("columns"):
        cols = [no_brands(col) for col in page["columns"]] or ["Item"]
        cw = (w - 60) // len(cols)
        c.rect(x + 30, top, cw * len(cols), 40, accent)
        for i, col in enumerate(cols):
            c.text(x + 38 + i * cw, top + 12, fit(col, max(3, (cw - 20) // 12)), (255, 255, 255), 2)
        rows = page.get("rows") or []
        for r in range(min(18, (h - (top - y) - 70) // 40)):
            ry = top + 40 + r * 40
            c.outline(x + 30, ry, cw * len(cols), 41, light, 2)
            for i in range(1, len(cols)):
                c.rect(x + 30 + i * cw, ry, 2, 40, light)
            if r < len(rows):
                for i, val in enumerate(rows[r][:len(cols)]):
                    c.text(x + 38 + i * cw, ry + 13, fit(no_brands(val), max(3, (cw - 20) // 12)), grey, 2)
        return
    yy = top
    per_line = max(10, (w - 110) // 12)
    for kind, text in page.get("lines", []):
        if yy > y + h - 50:
            break
        if kind == "pre":    # calendar rows: fixed-width font, spacing kept exactly
            c.text(x + 60, yy + 2, plain(text), ink, 2)
            c.rect(x + 60, yy + 26, w - 90, 1, light)
            yy += 36
            continue
        text = no_brands(text)
        if kind == "h":
            for line in wrap(text, max(8, (w - 60) // 18), 2):
                c.text(x + 30, yy, line, accent, 3)
                yy += 32
            yy += 8
        elif kind == "line":
            c.rect(x + 30, yy + 20, w - 60, 2, light)
            yy += 34
        elif kind == "p":
            for line in wrap(text, per_line + 4, 3):
                c.text(x + 30, yy, line, grey, 2)
                yy += 24
            yy += 12
        else:
            if kind == "box":
                c.outline(x + 30, yy, 18, 18, accent, 2)
            else:
                c.rect(x + 36, yy + 6, 8, 8, accent)
            lines = wrap(text, per_line, 2)
            for i, line in enumerate(lines):
                c.text(x + 60, yy + 2 + 24 * i, line, ink, 2)
            yy += 24 * (len(lines) - 1)
            c.rect(x + 60, yy + 26, w - 90, 1, light)
            yy += 36


def _brand_footer(c, label, y, ink, scale=2):
    c.text((c.w - text_width(label, scale)) // 2, y, label, ink, scale)


def cover_png(spec):
    bg, accent, ink = _palette(spec.get("kind"))
    w, h = COVER_SIZE
    c = Canvas(w, h, bg)
    c.rect(0, 0, w, 16, accent)
    c.rect(80, 90, w - 160, h - 180, (255, 255, 255))
    c.rect(80, 90, 14, h - 180, accent)
    name = no_brands(spec.get("name", "")) or "Digital template"
    lines, scale = _fit_title(name, w - 300, 3, 9)
    ty = 90 + (h - 180 - len(lines) * 12 * scale - 80) // 2
    for i, line in enumerate(lines):
        c.text(150, ty + i * 12 * scale, line, ink, scale)
    label = KIND_LABEL.get(spec.get("kind"), "Digital template") + "  |  Instant download"
    c.text(150, ty + len(lines) * 12 * scale + 30, label, accent, 3)
    return c.png()


def preview_png(spec, page, number):
    bg, accent, ink = _palette(spec.get("kind"))
    w, h = PREVIEW_SIZE
    c = Canvas(w, h, bg)
    _draw_page(c, page, 190, 50, w - 380, h - 130, accent, ink)
    _brand_footer(c, f"Preview {number} of 3", h - 50, ink, 2)
    return c.png()


def pinterest_png(spec, page):
    bg, accent, ink = _palette(spec.get("kind"))
    w, h = PIN_SIZE
    c = Canvas(w, h, bg)
    c.rect(0, 0, w, 300, accent)
    name = no_brands(spec.get("name", "")) or "Digital template"
    lines, scale = _fit_title(name, w - 120, 3, 8)
    for i, line in enumerate(lines):
        c.text(60, 50 + i * 11 * scale, line, (255, 255, 255), scale)
    _draw_page(c, page, 110, 380, w - 220, 900, accent, ink)
    _brand_footer(c, KIND_LABEL.get(spec.get("kind"), "Digital template"), h - 140, ink, 4)
    return c.png()


def write_mockups(folder, spec):
    """Write cover, 3 previews and the Pinterest image into folder. Returns the file names."""
    import os
    pages = preview_pages(spec)
    files = {"cover.png": cover_png(spec), "pinterest.png": pinterest_png(spec, pages[0])}
    for i, page in enumerate(pages, 1):
        files[f"preview-{i}.png"] = preview_png(spec, page, i)
    for name, data in files.items():
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
    return ["cover.png", "preview-1.png", "preview-2.png", "preview-3.png", "pinterest.png"]
