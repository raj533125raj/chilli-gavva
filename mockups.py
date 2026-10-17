"""mockups.py — listing images made from the REAL product files (free tools only).

For each product folder the Product Builder writes:
  cover.png                       1280x720   label, big title, one-line description, 3-4 fact chips, and
                                             2 real pages of THIS product, slightly tilted, on an accent circle
  preview-1.png .. preview-3.png  1280x720   one real page + "INSIDE · PAGE X OF N" + 3 short bullets
  pinterest.png                   1000x1500  title, 2 tilted real pages, a colour bar with the facts

Pages are rendered from the product's own PDF with poppler (pdftoppm). Sheets and resume products get a
picture drawn from the real file contents (sheet columns and rows, resume headings and bullets). Text uses
the Inter font (fonts/, SIL Open Font Licence 1.1, see fonts/Inter-LICENSE.txt), drawn with Pillow.

Every fact on an image (page count, paper sizes, "Dated 2027", sheet count ...) comes from the Truth
Checker's proofs (truth_check.image_facts). Never a page that is not in the product, never a brand name
of another company, never a "..." cut.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile

COVER_SIZE = (1280, 720)
PREVIEW_SIZE = (1280, 720)
PIN_SIZE = (1000, 1500)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "fonts")
FONTS = {"regular": "Inter-Regular.ttf", "semibold": "Inter-SemiBold.ttf", "bold": "Inter-Bold.ttf"}

THEMES = {   # background, accent, title ink, body ink (Style A teal is the default; B is the ADHD pastel)
    "A": {"bg": (230, 242, 241), "accent": (47, 125, 120), "ink": (20, 52, 50), "body": (62, 85, 83)},
    "B": {"bg": (251, 227, 225), "accent": (196, 101, 122), "ink": (61, 44, 62), "body": (93, 74, 92)},
}
LABELS = {"sheets": "SPREADSHEET TEMPLATE", "resume": "RESUME TEMPLATE BUNDLE", "notion": "DIGITAL TEMPLATE",
          "pdf": "PRINTABLE PLANNER"}

# Brand names of other companies are never drawn on the images.
_BRANDS = [(r"google\s+sheets?", "spreadsheet"), (r"google\s+docs?", "document"), (r"microsoft\s+excel", "spreadsheet"),
           (r"microsoft\s+word", "document"), (r"\bexcel\b", "spreadsheet"), (r"\bnotion\b", ""),
           (r"\bcanva\b", ""), (r"\betsy\b", ""), (r"\bgumroad\b", ""), (r"\bpayhip\b", ""), (r"\bamazon\b", ""),
           (r"\bapple\s+numbers\b", "spreadsheet"), (r"\bgoodnotes\b", "note app"), (r"\bipad\b", "tablet"),
           (r"\blinkedin\b", "professional profile"), (r"\bgoogle\b", ""), (r"\bmicrosoft\b", ""),
           (r"\bchatgpt\b", ""), (r"\bopenai\b", "")]
_BRAND_WORDS = (r"google\s+sheets?|google\s+docs?|microsoft\s+excel|microsoft\s+word|excel|notion|canva|etsy|gumroad"
                r"|payhip|goodnotes|apple\s+numbers|ipad")
_PLAIN = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
          "‑": "-", "‐": "-", " ": " "}

DRAWN = None    # the Truth Checker sets this to a list to collect every text the images show


def plain(text):
    """Curly quotes and dashes become plain ones and accents are dropped; nothing is silently cut."""
    out = "".join(_PLAIN.get(ch, ch) for ch in str(text or ""))
    return unicodedata.normalize("NFKD", out).encode("ascii", "ignore").decode()


def no_brands(text):
    out = re.sub(rf"\s+(?:for|in|on|with|using)\s+(?:{_BRAND_WORDS})\b", "", str(text or ""), flags=re.IGNORECASE)
    for pattern, repl in _BRANDS:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    out = plain(out)
    return re.sub(r"\s{2,}", " ", out).strip(" -|,")


def available():
    """True when the free tools are installed: Pillow, and poppler's pdftoppm for PDF pages."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return shutil.which("pdftoppm") is not None


# ---- text with Inter ------------------------------------------------------------------------------------
_font_cache = {}


def font(weight, size):
    from PIL import ImageFont
    key = (weight, int(size))
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(os.path.join(FONT_DIR, FONTS[weight]), int(size))
    return _font_cache[key]


def text_w(s, f, spacing=0):
    return f.getlength(s) + spacing * max(0, len(s) - 1)


def wrap(s, f, max_w, spacing=0):
    words, lines, cur = str(s).split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if cur and text_w(trial, f, spacing) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [""]


def fit(s, weight, size, min_size, max_w, max_lines, spacing=0):
    """Largest size (down to min_size) that wraps into max_lines and whose longest word fits.
    Never cuts: at min_size every line is returned. Returns (lines, font)."""
    while True:
        f = font(weight, size)
        lines = wrap(s, f, max_w, spacing)
        if (len(lines) <= max_lines and all(text_w(l, f, spacing) <= max_w for l in lines)) or size <= min_size:
            return lines, f
        size = max(min_size, size - 2)


def draw_text(d, xy, s, f, fill, spacing=0):
    if DRAWN is not None:
        DRAWN.append(s)
    if not spacing:
        d.text(xy, s, font=f, fill=fill)
        return
    x, y = xy
    for ch in s:
        d.text((x, y), ch, font=f, fill=fill)
        x += f.getlength(ch) + spacing


def _line_h(f):
    return int(f.size * 1.18)


# ---- real pages ------------------------------------------------------------------------------------------
_page_cache = {}


def render_pdf_page(pdf, number, height):
    """One real page of the PDF as a picture (poppler's pdftoppm), `height` pixels tall."""
    from PIL import Image
    key = (os.path.abspath(pdf), number, height, os.path.getmtime(pdf))
    if key not in _page_cache:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "p")
            subprocess.run(["pdftoppm", "-f", str(number), "-l", str(number), "-scale-to-y", str(height),
                            "-scale-to-x", "-1", "-png", "-singlefile", pdf, out], check=True,
                           capture_output=True, timeout=60)
            with Image.open(out + ".png") as im:
                _page_cache[key] = im.convert("RGB")
    return _page_cache[key].copy()


def _xlsx_sheets(path):
    """[(sheet name, [[cell text]])] read from the workbook itself."""
    out = []
    with zipfile.ZipFile(path) as z:
        wb = z.read("xl/workbook.xml").decode("utf-8", "ignore")
        names = re.findall(r'<sheet [^>]*name="([^"]+)"', wb)
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = [re.sub(r"<[^>]+>", "", si) for si in
                      re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode("utf-8", "ignore"), re.S)]
        for i, name in enumerate(names, 1):
            member = f"xl/worksheets/sheet{i}.xml"
            if member not in z.namelist():
                continue
            body = z.read(member).decode("utf-8", "ignore")
            rows = []
            for row in re.findall(r"<row[^>]*>(.*?)</row>", body, re.S):
                cells = []
                for attrs, inner in re.findall(r"<c ([^>]*?)(?:/>|>(.*?)</c>)", row, re.S):
                    if "<f>" in (inner or "") or "<f " in (inner or ""):
                        cells.append("(formula)")
                    elif 't="inlineStr"' in attrs:
                        cells.append(re.sub(r"<[^>]+>", "", inner))
                    elif 't="s"' in attrs:
                        v = re.search(r"<v>(\d+)</v>", inner or "")
                        cells.append(shared[int(v.group(1))] if v and int(v.group(1)) < len(shared) else "")
                    else:
                        v = re.search(r"<v>([^<]*)</v>", inner or "")
                        cells.append(v.group(1) if v else "")
                rows.append([_unescape(c) for c in cells])
            out.append((_unescape(name), rows))
    return out


def _unescape(s):
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def _docx_paragraphs(path):
    with zipfile.ZipFile(path) as z:
        body = z.read("word/document.xml").decode("utf-8", "ignore")
    out = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", body, re.S):
        style = re.search(r'<w:pStyle w:val="([^"]+)"', p)
        text = _unescape("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)))
        if text.strip():
            out.append((style.group(1) if style else "Normal", text))
    return out


def _paper(theme, title, draw_body, size=(850, 1100)):
    """A drawn page for sheet / document products: header band with the real name, then the real content."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(im)
    lines, f = fit(no_brands(title), "bold", 40, 24, size[0] - 100, 2)
    band = 50 + len(lines) * _line_h(f) + 30
    d.rectangle([0, 0, size[0], band], fill=theme["accent"])
    y = 45
    for line in lines:
        draw_text(d, (50, y), line, f, (255, 255, 255))
        y += _line_h(f)
    draw_body(d, im, band + 30)
    return im


def _sheet_picture(theme, name, rows):
    cols = max([len(r) for r in rows[:14]] + [1])
    cols = min(cols, 6)

    def body(d, im, top):
        w = im.width - 100
        cw = w / cols
        y = top
        light = tuple(int(c * 0.15 + 255 * 0.85) for c in theme["accent"])
        for r_i, row in enumerate(rows[:16]):
            cells = (row + [""] * cols)[:cols]
            f = font("semibold" if r_i == 0 else "regular", 18)
            texts = []
            for c in cells:   # each cell wraps to at most 2 lines and shrinks; never cut with "..."
                lines, cf = fit(no_brands(c), "semibold" if r_i == 0 else "regular", 18, 11, cw - 16, 2)
                texts.append((lines, cf))
            h = max(len(t[0]) * _line_h(t[1]) for t in texts) + 18
            if y + h > im.height - 40:
                break
            if r_i == 0:
                d.rectangle([50, y, 50 + w, y + h], fill=light)
            for c_i, (lines, cf) in enumerate(texts):
                yy = y + 9
                for line in lines:
                    draw_text(d, (58 + c_i * cw, yy), line, cf, theme["ink"])
                    yy += _line_h(cf)
            d.line([50, y + h, 50 + w, y + h], fill=(201, 211, 214), width=2)
            y += h
        for c_i in range(cols + 1):
            d.line([50 + c_i * cw, top, 50 + c_i * cw, y], fill=(201, 211, 214), width=1)
    return _paper(theme, name, body)


def _doc_picture(theme, name, paragraphs):
    def body(d, im, top):
        y = top
        for style, text in paragraphs:
            weight, size, color, bullet = "regular", 20, theme["ink"], False
            if style == "Title":
                weight, size = "bold", 34
            elif style.startswith("Heading"):
                weight, size, color = "semibold", 24, theme["accent"]
            elif style == "Bullet":
                bullet = True
            lines, f = fit(no_brands(text), weight, size, size, im.width - 130, 99)
            need = len(lines) * _line_h(f) + 12
            if y + need > im.height - 40:
                break      # whole paragraphs only
            for i, line in enumerate(lines):
                if bullet and i == 0:
                    d.ellipse([60, y + 11, 68, y + 19], fill=theme["accent"])
                draw_text(d, (80 if bullet else 50, y), line, f, color)
                y += _line_h(f)
            y += 12
    return _paper(theme, name, body)


# ---- what the product really contains -------------------------------------------------------------------
def _sentence(text):
    t = re.sub(r"\s+", " ", text).strip()
    return t[:1].upper() + t[1:].lower() if t.isupper() else t


def product_pages(folder, facts):
    """[{"title", "bullets", "picture"(callable height->Image)}] for the pages the images may show, all
    real. For PDFs: the content pages (no cover, no How-to page, no 'cont.' pages)."""
    import truth_check
    kind, theme = facts["kind"], THEMES[facts["style"]]
    out = []
    if kind == "pdf":
        pdf = facts["pdf_path"]
        pages = truth_check._pdf_pages(pdf)
        for n, page in enumerate(pages, 1):
            big = [t for f_, s, _, _, t in page["items"] if f_ == "F2" and s >= 18]
            title = " ".join(big)
            heads = [_sentence(t) for f_, s, _, _, t in page["items"] if f_ == "F2" and 10.5 <= s < 18]
            if n == 1 or not title or title == "How to use" or title.endswith("(cont.)"):
                continue
            month = re.fullmatch(r"(\w+) (\d{4})", title)
            week = facts.get("week_start")
            if month and facts.get("dated"):
                big_title = f"Monthly pages with correct {facts['dated']} dates"
                bullets = [f"Weeks start on {week}" if week else "Every date in the right place",
                           "Weekends shaded", _join(heads) or "Space for notes"]
                kind_ = "month"
            elif "at a glance" in title.lower():
                big_title = title
                bullets = ["All 12 months on one page", f"Weeks start on {week}" if week else "Weekends shaded",
                           "Weekends shaded" if week else "Print and pin it up"]
                kind_ = "year"
            else:
                big_title = title
                bullets = short_bullets(heads)
                if len(bullets) < 3:
                    bullets.append("Room to write down to the bottom")
                if len(bullets) < 3 and facts.get("sizes") == ["A4", "Letter"]:
                    bullets.append("A4 and US Letter files included")
                kind_ = "page"
            out.append({"number": n, "title": big_title, "bullets": [b for b in bullets if b][:3], "kind": kind_,
                        "picture": (lambda h, n=n: render_pdf_page(pdf, n, h))})
        if facts.get("dated"):   # January first, then the year page, then the extra pages, then other months
            months = [p for p in out if p["kind"] == "month"]
            out = months[:1] + [p for p in out if p["kind"] == "year"] + \
                [p for p in out if p["kind"] == "page"] + months[1:]
    elif kind == "sheets":
        for name in facts["xlsx"]:
            for sheet, rows in _xlsx_sheets(os.path.join(folder, name)):
                heads = [c for c in (rows[0] if rows else []) if c][:3]
                out.append({"number": None, "title": f"{sheet} sheet", "kind": "sheet",
                            "bullets": [f"Columns: {_join(heads)}" if heads else "Ready to fill in",
                                        f"{max(0, len(rows) - 1)} example rows to replace" if len(rows) > 1
                                        else "Clear instructions", "Opens in spreadsheet apps"],
                            "picture": (lambda h, s=sheet, r=rows: _fit_height(_sheet_picture(theme, s, r), h))})
    elif kind == "resume":
        for name in facts["docx"]:
            paragraphs = _docx_paragraphs(os.path.join(folder, name))
            what = "Cover letter" if "cover-letter" in name else "Resume"
            heads = [t for s, t in paragraphs if s.startswith("Heading")][:3]
            out.append({"number": None, "title": f"{what} template", "kind": "doc",
                        "bullets": ([_join(heads)] if heads else []) + ["Replace every [placeholder]",
                                                                         "Editable document file"],
                        "picture": (lambda h, w=what, p=paragraphs: _fit_height(_doc_picture(theme, w, p), h))})
        for name in facts["xlsx"]:
            for sheet, rows in _xlsx_sheets(os.path.join(folder, name))[1:2]:
                out.append({"number": None, "title": "Job tracker sheet", "kind": "sheet",
                            "bullets": ["Log every application", "Follow-up date column", "Opens in spreadsheet apps"],
                            "picture": (lambda h, s=sheet, r=rows: _fit_height(_sheet_picture(theme, s, r), h))})
    elif kind == "notion":
        for name in facts.get("zips", []):
            with zipfile.ZipFile(os.path.join(folder, name)) as z:
                for member in sorted(z.namelist(), key=lambda m: (not m.endswith(".md"), m)):
                    body = z.read(member).decode("utf-8", "ignore")
                    title = os.path.splitext(member)[0].replace("-", " ").title()
                    if member.endswith(".md"):
                        paragraphs = [("Heading1" if l.startswith("#") else "Bullet" if l.lstrip().startswith("- ")
                                       else "Normal", l.lstrip("#-* ").strip()) for l in body.splitlines() if l.strip()]
                        out.append({"number": None, "title": title, "kind": "doc",
                                    "bullets": ["Page to import", "Your own notes and steps", "Plain text and tables"],
                                    "picture": (lambda h, t=title, p=paragraphs: _fit_height(_doc_picture(theme, t, p), h))})
                    elif member.endswith(".csv"):
                        import csv
                        import io
                        rows = list(csv.reader(io.StringIO(body)))
                        out.append({"number": None, "title": f"{title} table", "kind": "sheet",
                                    "bullets": [f"Columns: {_join(rows[0][:3])}" if rows else "Table to import",
                                                "Example rows to replace", "Imports as a table"],
                                    "picture": (lambda h, t=title, r=rows: _fit_height(_sheet_picture(theme, t, r), h))})
    return out


def short_bullets(headings, n=3, limit=26):
    """Up to n short, natural bullets from a page's own section headings (the shortest first, kept in
    page order). 'Now · Next · Later' reads as 'Now, next, later'."""
    clean = [h.replace(" \u00b7 ", ", ").replace("\u00b7", ",") for h in headings]
    short = [h for h in clean if len(h) <= limit] or sorted(clean, key=len)
    return short[:n]


def _join(items):
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    items = [items[0]] + [i[:1].lower() + i[1:] if not i[:2].isupper() else i for i in items[1:]]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _fit_height(im, h):
    return im.resize((max(1, round(im.width * h / im.height)), h))


def chips(facts):
    """3-4 short facts, ALL from the Truth Checker's proofs."""
    out = []
    if facts["kind"] == "pdf":
        out.append(f"{facts['pages']} pages")
        sizes = facts.get("sizes") or []
        if sizes:
            out.append(" + ".join("US Letter" if s == "Letter" else s for s in sizes))
        if facts.get("dated"):
            out.append(f"Dated {facts['dated']}" if "-" not in facts["dated"] else facts["dated_range"])
        out.append("Printable PDF")
    elif facts["kind"] == "sheets":
        out.append(f"{facts['sheet_count']} sheets")
        if facts.get("formulas"):
            out.append("Formulas built in")
        if facts.get("dropdowns"):
            out.append("Drop-down lists")
        out.append("Spreadsheet file (.xlsx)")
    elif facts["kind"] == "resume":
        out += ["Resume + cover letter" if len(facts["docx"]) >= 2 else "Resume template"]
        if facts["xlsx"]:
            out.append("Job tracker sheet")
        out.append("Editable .docx files")
    else:
        out.append("Digital template files")
    return out[:4]


def _description(facts, pages):
    if facts["kind"] != "pdf":
        names = [p["title"] for p in pages][:4]
        return _join([n[0].upper() + n[1:] if i == 0 else n for i, n in enumerate(names)]) + "."
    parts = []
    if any(p["kind"] == "year" for p in pages):
        parts.append("year at a glance")
    if any(p["kind"] == "month" for p in pages):
        parts.append("12 monthly pages")
    parts += [_sentence(p["title"]).lower() if p["title"].isupper() else p["title"].lower()
              for p in pages if p["kind"] == "page"]
    text = _join(parts[:4]) if len(parts) <= 4 else ", ".join(parts[:3]) + " and more"
    return (text[:1].upper() + text[1:] + ". Print at home and start today.") if text else "Print at home."


# ---- the images -------------------------------------------------------------------------------------------
def _tilted(canvas, page, center, angle, shadow=True):
    from PIL import Image, ImageFilter
    page = page.convert("RGBA")
    rot = page.rotate(angle, expand=True, resample=Image.BICUBIC)
    x, y = int(center[0] - rot.width / 2), int(center[1] - rot.height / 2)
    if shadow:
        alpha = rot.split()[3].point(lambda a: int(a * 0.30))
        sh = Image.new("RGBA", rot.size, (20, 30, 35, 0))
        sh.putalpha(alpha)
        pad = 40
        big = Image.new("RGBA", (rot.width + 2 * pad, rot.height + 2 * pad), (0, 0, 0, 0))
        big.paste(sh, (pad, pad))
        big = big.filter(ImageFilter.GaussianBlur(14))
        _composite_clip(canvas, big, x - pad + 10, y - pad + 16)
    _composite_clip(canvas, rot, x, y)


def _composite_clip(canvas, layer, x, y):
    """alpha_composite that allows a layer to hang over the edge of the canvas."""
    left, top = max(0, -x), max(0, -y)
    right, bottom = min(layer.width, canvas.width - x), min(layer.height, canvas.height - y)
    if right > left and bottom > top:
        canvas.alpha_composite(layer.crop((left, top, right, bottom)), (x + left, y + top))


def _chips(d, x, y, items, theme, max_w):
    f = font("semibold", 19)
    cx, cy = x, y
    for item in items:
        w = int(text_w(item, f)) + 32
        if cx + w > x + max_w and cx > x:
            cx, cy = x, cy + 56
        d.rounded_rectangle([cx, cy, cx + w, cy + 44], radius=22, fill=(255, 255, 255))
        draw_text(d, (cx + 16, cy + 10), item, f, theme["ink"])
        cx += w + 12
    return cy + 44


def cover_image(info):
    from PIL import Image, ImageDraw
    theme, w, h = info["theme"], *COVER_SIZE
    im = Image.new("RGBA", (w, h), theme["bg"] + (255,))
    d = ImageDraw.Draw(im)
    d.ellipse([1010 - 430, 380 - 430, 1010 + 430, 380 + 430], fill=theme["accent"])
    shown = info["pages"][:2]
    if len(shown) > 1:
        _tilted(im, shown[1]["picture"](500), (1095, 365), -5)
    if shown:
        _tilted(im, shown[0]["picture"](540), (905, 372), 4)
    d = ImageDraw.Draw(im)
    # left side: label, title, description, chips — centred vertically
    lab_f = font("semibold", 20)
    t_lines, t_f = fit(info["name"], "bold", 64, 38, 470, 3)
    desc_lines, desc_f = fit(info["description"], "regular", 24, 18, 470, 3)
    height = 30 + len(t_lines) * _line_h(t_f) + 20 + len(desc_lines) * _line_h(desc_f) + 30 + 100
    y = max(50, (h - height) // 2)
    draw_text(d, (90, y), info["label"], lab_f, theme["accent"], 4)
    y += 42
    for line in t_lines:
        draw_text(d, (88, y), line, t_f, theme["ink"])
        y += _line_h(t_f)
    y += 16
    for line in desc_lines:
        draw_text(d, (90, y), line, desc_f, theme["body"])
        y += _line_h(desc_f)
    _chips(d, 90, y + 28, info["chips"], theme, 470)
    return im.convert("RGB")


def preview_image(info, page):
    from PIL import Image, ImageDraw
    theme, w, h = info["theme"], *PREVIEW_SIZE
    im = Image.new("RGBA", (w, h), theme["bg"] + (255,))
    pic = page["picture"](650)
    _tilted(im, pic, (920, 360), 0)
    d = ImageDraw.Draw(im)
    label = (f"INSIDE · PAGE {page['number']} OF {info['total']}" if page["number"]
             else f"INSIDE · {info['chips'][0].upper()}")
    lab_f = font("semibold", 20)
    t_lines, t_f = fit(page["title"], "bold", 52, 32, 470, 3)
    bullets = [fit(b, "regular", 24, 18, 430, 2) for b in page["bullets"]]
    height = 40 + len(t_lines) * _line_h(t_f) + 30 + sum(len(l) * _line_h(f) + 14 for l, f in bullets)
    y = max(50, (h - height) // 2)
    draw_text(d, (90, y), label, lab_f, theme["accent"], 4)
    y += 44
    for line in t_lines:
        draw_text(d, (88, y), line, t_f, theme["ink"])
        y += _line_h(t_f)
    y += 24
    for lines, f in bullets:
        d.ellipse([92, y + f.size // 2 - 1, 100, y + f.size // 2 + 7], fill=theme["accent"])
        for line in lines:
            draw_text(d, (114, y), line, f, theme["ink"])
            y += _line_h(f)
        y += 14
    return im.convert("RGB")


def pin_image(info):
    from PIL import Image, ImageDraw
    theme, w, h = info["theme"], *PIN_SIZE
    im = Image.new("RGBA", (w, h), theme["bg"] + (255,))
    d = ImageDraw.Draw(im)
    lab_f = font("semibold", 22)
    label_w = text_w(info["label"], lab_f, 4)
    draw_text(d, ((w - label_w) / 2, 70), info["label"], lab_f, theme["accent"], 4)
    t_lines, t_f = fit(info["name"], "bold", 76, 44, 860, 3)
    y = 118
    for line in t_lines:
        draw_text(d, ((w - text_w(line, t_f)) / 2, y), line, t_f, theme["ink"])
        y += _line_h(t_f)
    bar = 250
    mid = (y + 30 + (h - bar - 20)) / 2
    room = h - bar - 40 - (y + 30)
    shown = info["pages"][:2]
    page_h = int(min(760, room * 0.92))
    if len(shown) > 1:
        _tilted(im, shown[1]["picture"](int(page_h * 0.94)), (w / 2 + 150, mid), -5)
    if shown:
        _tilted(im, shown[0]["picture"](page_h), (w / 2 - 90, mid + 10), 4)
    d = ImageDraw.Draw(im)
    d.rectangle([0, h - bar, w, h], fill=theme["accent"])
    facts_line = " · ".join(info["chips"][:2])
    f_lines, f_f = fit(facts_line, "bold", 44, 28, w - 120, 2)
    yy = h - bar + 60 - (len(f_lines) - 1) * 24
    for line in f_lines:
        draw_text(d, ((w - text_w(line, f_f)) / 2, yy), line, f_f, (255, 255, 255))
        yy += _line_h(f_f)
    sub = "Instant download · print at home" if info["kind"] == "pdf" else "Instant download"
    s_f = font("regular", 26)
    draw_text(d, ((w - text_w(sub, s_f)) / 2, yy + 12), sub, s_f, (255, 255, 255))
    return im.convert("RGB")


def product_info(folder, spec=None):
    """Everything the images need, read from the folder (the files are the only source of facts)."""
    import truth_check
    facts = truth_check.image_facts(folder)
    pages = product_pages(folder, facts)
    name = (spec or {}).get("name")
    if not name:
        import product_builder
        name = product_builder.spec_from_folder(folder)["name"]
    label = LABELS.get(facts["kind"], "DIGITAL TEMPLATE")
    if facts["kind"] == "pdf" and facts.get("dated"):
        label = f"PRINTABLE · {facts['dated']}"
    return {"name": no_brands(name) or "Digital template", "label": label, "kind": facts["kind"],
            "theme": THEMES[facts["style"]], "chips": chips(facts), "pages": pages, "total": facts["pages"],
            "description": no_brands(_description(facts, pages))}


def make_images(folder, spec=None):
    """{file name: PIL image} for one product folder."""
    info = product_info(folder, spec)
    if not info["pages"]:
        raise ValueError("no real pages to show")
    previews = info["pages"][:3]
    while len(previews) < 3:     # a short product repeats its own pages; never an invented page
        previews.append(previews[len(previews) % len(info["pages"])])
    out = {"cover.png": cover_image(info), "pinterest.png": pin_image(info)}
    for i, page in enumerate(previews, 1):
        out[f"preview-{i}.png"] = preview_image(info, page)
    return out


THUMB_SIZE = (600, 600)


def thumbnail_image(info, cover):
    """600x600 store thumbnail made from the REAL cover: the product name on top, the whole cover in the
    middle (scaled, never cropped) and the first two proven facts below."""
    from PIL import Image, ImageDraw
    theme, w, h = info["theme"], *THUMB_SIZE
    im = Image.new("RGB", (w, h), theme["bg"])
    d = ImageDraw.Draw(im)
    lines, f = fit(info["name"], "bold", 44, 26, w - 60, 2)
    y = 40
    for line in lines:
        draw_text(d, ((w - text_w(line, f)) / 2, y), line, f, theme["ink"])
        y += _line_h(f)
    small = cover.resize((w, round(cover.height * w / cover.width)), Image.LANCZOS)
    top = max(y + 16, (h - small.height) // 2)
    im.paste(small, (0, top))
    facts = " \u00b7 ".join(info["chips"][:2])
    f_lines, ff = fit(facts, "semibold", 26, 18, w - 60, 2)
    yy = top + small.height + max(14, (h - top - small.height - len(f_lines) * _line_h(ff)) // 2 - 4)
    for line in f_lines:
        draw_text(d, ((w - text_w(line, ff)) / 2, yy), line, ff, theme["accent"])
        yy += _line_h(ff)
    return im


def write_mockups(folder, spec=None):
    """Write cover, 3 previews, the Pinterest image and a 600x600 thumbnail into folder. Returns the names."""
    images = make_images(folder, spec)
    images["thumbnail.png"] = thumbnail_image(product_info(folder, spec), images["cover.png"])
    for name, im in images.items():
        im.save(os.path.join(folder, name), optimize=True)
    return ["cover.png", "preview-1.png", "preview-2.png", "preview-3.png", "pinterest.png", "thumbnail.png"]
