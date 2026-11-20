#!/usr/bin/env python3
"""CCH-2: Pinterest Pins that are ready to post, for every SELLABLE pack.

For one pack folder this makes, in <pack marketing folder>/pinterest/:
  pin-1.png .. pin-5.png   1000 x 1500, in the pack theme (colours from the pack cover, the same theme the
                           listing images use), drawn by this module only: no logos, no amounts
  pins.json                per Pin: title (<= 100 chars), description (<= 500 chars), board name, link
                           placeholder, alt text, and the exact text layout used to draw it; plus the checker
                           result and made_at (UTC)
  PINS.txt                 copy-paste sheet for the owner (title / description / board / link per Pin)

check_pins(folder) is the checker: it re-opens every PNG and re-measures every text line with the same font,
so it fails on a wrong size, an empty or too long text, missing words, text running off the image (outside
the safe margin), low text contrast, money words or brand names.

Nothing is posted. The owner uploads the Pins by hand and replaces the link placeholder with the store link.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PIN_W, PIN_H = 1000, 1500
SAFE = 60
PIN_COUNT = 5
TITLE_MAX = 100
DESC_MAX = 500
MIN_CONTRAST = 4.5
FOLDER = "pinterest"
META_FILE = "pins.json"
SHEET_FILE = "PINS.txt"
LINK_PLACEHOLDER = "[STORE LINK]"
DEFAULT_BOARD = "Printable Worksheets & Organization Templates"
FORMAT_VERSION = 1
DESIGNS = ("hero", "inside", "steps", "pages", "benefit")

MONEY_CHECK = re.compile(
    r"[$₹€£¥]|\b(?:usd|inr|eur|gbp|rs\.?|rupees?|dollars?|euros?|pounds?|cents?|paise|price|priced|pricing|"
    r"discount|sale)\b|\b\d+\s?%\s?off\b", re.IGNORECASE)
BRAND_CHECK = re.compile(
    r"\b(google|microsoft|excel|notion|canva|etsy|gumroad|payhip|goodnotes|apple|ipad|amazon|chatgpt|openai|"
    r"linkedin|facebook|instagram|pinterest|tiktok|youtube|shopify|kofi|ko-fi|topmate|instamojo)\b",
    re.IGNORECASE)
_SKIP_ITEMS = re.compile(r"^(license|licence|readme|truth_check|checklist|cover_image_idea)\b", re.IGNORECASE)


# ---- text helpers ---------------------------------------------------------------------
def clean(text: Any) -> str:
    """Plain text without brand names, money words or extra spaces."""
    s = str(text or "")
    s = s.replace("—", " - ").replace("–", " - ").replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"').replace("…", "...").replace("·", "-")
    s = BRAND_CHECK.sub("", s)
    s = MONEY_CHECK.sub("", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" -,;:")


def cut(text: str, limit: int) -> str:
    """Cut at a word boundary so the text is at most `limit` characters."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    short = text[: limit - 3].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return short + "..."


def text_problems(text: str, label: str, limit: int) -> List[str]:
    problems = []
    if not str(text or "").strip():
        problems.append(f"{label}: empty")
    elif len(text) > limit:
        problems.append(f"{label}: {len(text)} chars (max {limit})")
    if MONEY_CHECK.search(str(text or "")):
        problems.append(f"{label}: money words")
    if BRAND_CHECK.search(str(text or "")):
        problems.append(f"{label}: brand name")
    return problems


# ---- the pack ---------------------------------------------------------------------------
def read_listing(pack_dir: Path) -> Dict[str, Any]:
    """Title, description, tags and 'What you get' items from listing.txt (or the robot's listing.md)."""
    pack_dir = Path(pack_dir)
    text = ""
    if (pack_dir / "listing.txt").is_file():
        text = (pack_dir / "listing.txt").read_text(encoding="utf-8-sig", errors="replace")
    elif (pack_dir / "listing.md").is_file():
        import marketing_auto
        text = marketing_auto._listing_txt_from_md(
            (pack_dir / "listing.md").read_text(encoding="utf-8-sig", errors="replace"))
    title, description, tags, items, section = "", [], [], [], None
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("title:"):
            title, section = line.split(":", 1)[1].strip(), None
        elif low.startswith("tags:"):
            tags = [t.strip() for t in line.split(":", 1)[1].split(",") if t.strip()]
            section = None
        elif low.startswith("description:"):
            section = "description"
            rest = line.split(":", 1)[1].strip()
            if rest:
                description.append(rest)
        elif low.startswith("what you get:"):
            section = "items"
        elif re.match(r"^[a-z ]{2,30}:$", low):
            section = None
        elif line and section == "description":
            description.append(line)
        elif line and section == "items":
            items.append(line.lstrip("-* ").strip())
    return {"title": title or pack_dir.name.replace("-", " ").title(), "description": " ".join(description),
            "tags": tags, "items": items}


def item_label(item: str) -> str:
    """'focus-planner-A4.pdf - PDF, page count: 3, A4' -> 'Focus planner A4 (PDF, 3 pages)'."""
    parts = re.split(r"\s+[-—]\s+", item, maxsplit=1)
    name, rest = parts[0], (parts[1] if len(parts) > 1 else "")
    stem, ext = os.path.splitext(name.strip())
    label = re.sub(r"[-_]+", " ", stem).strip()
    label = label[:1].upper() + label[1:]
    kind = ext.lstrip(".").upper() if ext else ""
    pages = re.search(r"page count:\s*(\d+)", rest, re.IGNORECASE)
    extra = ", ".join(x for x in (kind, f"{pages.group(1)} pages" if pages else "") if x)
    return clean(f"{label} ({extra})" if extra else label)


def buyer_items(listing: Dict[str, Any], limit: int = 5) -> List[str]:
    out = []
    for item in listing.get("items") or []:
        name = re.split(r"\s+[-—]\s+", item, maxsplit=1)[0].strip()
        if not name or _SKIP_ITEMS.match(name):
            continue
        label = item_label(item)
        if label and label not in out:
            out.append(label)
        if len(out) >= limit:
            break
    return out


def use_steps(listing: Dict[str, Any]) -> List[str]:
    """Three honest 'how it works' steps from the file types the pack really has."""
    names = " ".join(listing.get("items") or []).lower()
    images = len(re.findall(r"\.(?:png|jpe?g)\b", names))
    if images and images >= len(re.findall(r"\.pdf\b", names)):
        use = "Print at home or at a print shop and frame it"
    elif ".pdf" in names:
        use = "Print at 100% or write on it on a tablet"
    elif ".xlsx" in names or ".csv" in names:
        use = "Open the sheet in your spreadsheet app"
    elif ".docx" in names:
        use = "Open the document and add your details"
    elif ".zip" in names or "template" in names:
        use = "Import the template and follow the guide"
    else:
        use = "Open the files and follow the short guide"
    return ["Download the files", use, "Use it again whenever you need"]


def first_sentence(text: str, limit: int = 140) -> str:
    text = clean(text)
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return cut(match.group(1) if match else text, limit)


def pack_images(pack_dir: Path) -> List[Path]:
    """Real page previews (then covers) of the pack; an upload pack also looks in its build folder
    (<products>/<folder>), where the robot keeps preview-*.png."""
    pack_dir = Path(pack_dir)
    folders = [pack_dir]
    if pack_dir.parent.name == "upload":
        folders.append(pack_dir.parent.parent / pack_dir.name)
    previews: List[Path] = []
    covers: List[Path] = []
    for folder in folders:
        if folder.is_dir():
            previews += sorted(p for p in folder.glob("preview*.png") if p.is_file())
            covers += sorted(p for p in folder.glob("cover*.png") if p.is_file())
    return previews + covers


def pick_board(listing: Dict[str, Any]) -> str:
    """The best matching board from pin_boards' keyword boards (no brands, no money)."""
    try:
        import pin_boards
        themes = pin_boards.DEFAULT_BOARD_THEMES
    except Exception:
        return DEFAULT_BOARD
    title = str(listing.get("title", "")).lower()
    hay = " ".join([title] + list(listing.get("tags") or [])).lower()
    best, best_score = DEFAULT_BOARD, 0
    for theme in themes:
        score = 0
        for kw in theme.get("core_keywords") or []:
            words = kw.lower().split()
            if kw.lower() in title:            # the title says what the product is: it counts most
                score += 5
            elif kw.lower() in hay:
                score += 3
            else:
                score += sum(1 for w in words if len(w) > 3 and w in hay)
        score += 2 * sum(1 for w in str(theme.get("slug") or "").split("-") if len(w) > 4 and w in hay)
        if score > best_score:
            best, best_score = theme["name"], score
    return clean(best) or DEFAULT_BOARD


# ---- drawing: one layout model, drawn and checked the same way ---------------------------
def _mv():
    import mockups_v2
    return mockups_v2


def font(weight: str, size: int):
    return _mv().font(weight, size)


def _line_w(text: str, fnt) -> int:
    box = fnt.getbbox(text)
    return int(box[2] - box[0]) if box else 0


def wrap(text: str, fnt, width: int) -> List[str]:
    """Word wrap; a word wider than the box is split by letters (so no text is ever lost)."""
    lines: List[str] = []
    current = ""
    for word in text.split():
        while _line_w(word, fnt) > width and len(word) > 1:
            cut_at = len(word)
            while cut_at > 1 and _line_w(word[:cut_at], fnt) > width:
                cut_at -= 1
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:cut_at])
            word = word[cut_at:]
        trial = f"{current} {word}".strip()
        if _line_w(trial, fnt) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def line_height(size: int, gap: float = 1.22) -> int:
    return int(round(size * gap))


def fit_block(text: str, weight: str, box_w: int, max_lines: int, sizes: Sequence[int]) -> Tuple[int, List[str]]:
    """Largest size whose wrapped lines fit `max_lines` lines of `box_w` pixels."""
    lines: List[str] = []
    size = sizes[-1]
    for size in sizes:
        lines = wrap(text, font(weight, size), box_w)
        if len(lines) <= max_lines:
            return size, lines
    return size, lines


def hex_of(rgb: Sequence[int]) -> str:
    return "#%02x%02x%02x" % tuple(int(c) for c in rgb[:3])


def rgb_of(value: Any) -> Tuple[int, int, int]:
    if isinstance(value, str):
        v = value.lstrip("#")
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    return tuple(int(c) for c in value[:3])  # type: ignore[return-value]


def contrast(a: Sequence[int], b: Sequence[int]) -> float:
    def lum(c):
        out = []
        for v in c[:3]:
            v = v / 255.0
            out.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def readable_on(bg: Sequence[int], preferred: Sequence[int]) -> Tuple[int, int, int]:
    """`preferred` when it reads well on `bg`, else near-black or white (whichever reads better)."""
    if contrast(preferred, bg) >= MIN_CONTRAST:
        return tuple(preferred[:3])  # type: ignore[return-value]
    dark, light = (23, 28, 30), (255, 255, 255)
    return dark if contrast(dark, bg) >= contrast(light, bg) else light


class Canvas:
    """A Pillow image plus the list of text blocks drawn on it (the layout the checker re-measures)."""

    def __init__(self, width: int, height: int, bg: Sequence[int]):
        from PIL import Image, ImageDraw
        self.image = Image.new("RGB", (width, height), tuple(bg[:3]))
        self.draw = ImageDraw.Draw(self.image)
        self.width, self.height = width, height
        self.blocks: List[Dict[str, Any]] = []

    def rect(self, box, fill, radius: int = 0):
        if radius:
            self.draw.rounded_rectangle(box, radius=radius, fill=tuple(fill[:3]))
        else:
            self.draw.rectangle(box, fill=tuple(fill[:3]))

    def text(self, role: str, text: str, x: int, y: int, w: int, weight: str, sizes: Sequence[int],
             max_lines: int, fill: Sequence[int], bg: Sequence[int], align: str = "left") -> int:
        """Draw a wrapped text block; returns the y below it."""
        text = re.sub(r"\s+", " ", text).strip()
        size, lines = fit_block(text, weight, w, max_lines, sizes)
        fnt = font(weight, size)
        lh = line_height(size)
        fill = readable_on(bg, fill)
        for i, line in enumerate(lines):
            lw = _line_w(line, fnt)
            lx = x + (w - lw) // 2 if align == "center" else x
            self.draw.text((lx, y + i * lh), line, font=fnt, fill=fill)
        self.blocks.append({"role": role, "text": text, "lines": lines, "weight": weight, "size": size,
                            "x": x, "y": y, "w": w, "align": align, "line_h": lh,
                            "fill": hex_of(fill), "bg": hex_of(bg)})
        return y + lh * len(lines)

    def page(self, img_path: Optional[Path], box: Tuple[int, int, int, int], paper, shadow, angle: float = 0.0):
        """Paste a pack page (preview / cover) fitted into `box` on white paper with a soft shadow."""
        from PIL import Image, ImageFilter, ImageOps
        x0, y0, x1, y1 = box
        bw, bh = x1 - x0, y1 - y0
        inner = None
        if img_path is not None:
            try:
                with Image.open(img_path) as src:
                    src = ImageOps.exif_transpose(src).convert("RGB")
                    inner = ImageOps.contain(src, (bw - 24, bh - 24))
            except Exception:
                inner = None
        if inner is not None:          # the sheet takes the page's own shape (no big empty paper)
            bw, bh = inner.width + 24, inner.height + 24
        sheet = Image.new("RGBA", (bw, bh), tuple(paper[:3]) + (255,))
        if inner is not None:
            sheet.paste(inner, (12, 12))
        if angle:
            sheet = sheet.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
        shade = Image.new("RGBA", sheet.size, tuple(shadow[:3]) + (0,))
        shade.putalpha(sheet.getchannel("A").point(lambda a: 70 if a else 0))
        shade = shade.filter(ImageFilter.GaussianBlur(10))
        cx, cy = x0 + bw // 2, y0 + bh // 2
        px, py = cx - sheet.width // 2, cy - sheet.height // 2
        self.image.paste(shade, (px + 8, py + 12), shade)
        self.image.paste(sheet, (px, py), sheet)


# ---- the five designs ------------------------------------------------------------------------
def _theme(pack_dir: Path) -> Dict[str, Tuple[int, int, int]]:
    covers = sorted(Path(pack_dir).glob("cover*.png"))
    return dict(_mv().theme_from_cover(covers[0] if covers else None))


def _footer(cv: Canvas, th, label: str = "Digital download - personal use") -> None:
    cv.text("footer", label, SAFE + 20, PIN_H - SAFE - 46, PIN_W - 2 * SAFE - 40, "semibold", (28, 26, 24), 1,
            th["body"], th["bg"], align="center")


def design_hero(cv: Canvas, th, info) -> None:
    on_accent = _mv().contrast_text(th["accent"])
    cv.rect((0, 0, PIN_W, 470), th["accent"])
    cv.text("title", info["headline"], 90, 110, PIN_W - 180, "bold", (78, 70, 62, 56, 50, 44), 4,
            on_accent, th["accent"], align="center")
    cv.page(info["images"][0] if info["images"] else None, (110, 540, PIN_W - 110, 1330), th["paper"], th["shadow"])
    _footer(cv, th)


def block_height(text: str, weight: str, w: int, max_lines: int, sizes: Sequence[int]) -> int:
    size, lines = fit_block(re.sub(r"\s+", " ", text).strip(), weight, w, max_lines, sizes)
    return line_height(size) * len(lines)


def design_inside(cv: Canvas, th, info) -> None:
    y = cv.text("title", info["headline"], 90, 100, PIN_W - 180, "bold", (64, 58, 52, 46, 42), 3,
                th["ink"], th["bg"], align="center")
    y = cv.text("label", "What's inside", 90, y + 30, PIN_W - 180, "semibold", (40, 36), 1, th["accent"], th["bg"],
                align="center")
    items = [f"- {item}" for item in (info["items"] or ["Ready-to-use files"])]
    card_top = y + 24
    inner = sum(block_height(t, "regular", PIN_W - 260, 2, (34, 30, 28)) + 22 for t in items)
    card_bottom = card_top + 36 + inner + 14
    cv.rect((90, card_top, PIN_W - 90, card_bottom), th["paper"], radius=18)
    iy = card_top + 36
    for i, item in enumerate(items, 1):
        iy = cv.text(f"item{i}", item, 130, iy, PIN_W - 260, "regular", (34, 30, 28), 2, th["ink"], th["paper"]) + 22
    img = info["images"][1] if len(info["images"]) > 1 else (info["images"][0] if info["images"] else None)
    top = card_bottom + 50
    if top < 1200:
        cv.page(img, (160, top, PIN_W - 160, 1340), th["paper"], th["shadow"])
    _footer(cv, th)


def design_steps(cv: Canvas, th, info) -> None:
    y = cv.text("title", info["headline"], 90, 100, PIN_W - 180, "bold", (62, 56, 50, 44, 40), 3, th["ink"],
                th["bg"], align="center")
    on_accent = _mv().contrast_text(th["accent"])
    y += 40
    for i, step in enumerate(info["steps"], 1):
        cv.rect((90, y, PIN_W - 90, y + 150), th["paper"], radius=18)
        cv.rect((120, y + 35, 200, y + 115), th["accent"], radius=40)
        cv.text(f"num{i}", str(i), 120, y + 47, 80, "bold", (44,), 1, on_accent, th["accent"], align="center")
        cv.text(f"step{i}", step, 230, y + 30, PIN_W - 90 - 230 - 30, "semibold", (36, 32, 28), 2, th["ink"],
                th["paper"])
        y += 180
    img = info["images"][2] if len(info["images"]) > 2 else (info["images"][0] if info["images"] else None)
    cv.page(img, (160, y + 20, PIN_W - 160, 1340), th["paper"], th["shadow"])
    _footer(cv, th)


def design_pages(cv: Canvas, th, info) -> None:
    imgs = list(info["images"][:3]) or [None]
    while len(imgs) < 3:
        imgs.append(imgs[-1])
    cv.page(imgs[2], (70, 230, 650, 830), th["paper"], th["shadow"], angle=8)
    cv.page(imgs[1], (350, 250, 930, 850), th["paper"], th["shadow"], angle=-7)
    cv.page(imgs[0], (150, 170, 850, 900), th["paper"], th["shadow"])
    cv.rect((0, 980, PIN_W, PIN_H), th["accent"])
    on_accent = _mv().contrast_text(th["accent"])
    y = cv.text("title", info["headline"], 90, 1040, PIN_W - 180, "bold", (64, 56, 50, 44, 40), 3, on_accent,
                th["accent"], align="center")
    cv.text("label", f"{info['page_word']} preview", 90, y + 24, PIN_W - 180, "semibold", (34, 30), 1, on_accent,
            th["accent"], align="center")


def design_benefit(cv: Canvas, th, info) -> None:
    quote_h = block_height(info["benefit"], "bold", PIN_W - 260, 6, (52, 46, 42, 38, 34))
    bottom = 90 + 60 + quote_h + 60
    cv.rect((70, 90, PIN_W - 70, bottom), th["paper"], radius=24)
    cv.rect((70, 90, 86, bottom), th["accent"])
    cv.text("quote", info["benefit"], 130, 150, PIN_W - 260, "bold", (52, 46, 42, 38, 34), 6, th["ink"],
            th["paper"])
    y = cv.text("title", info["pack_title"], 90, bottom + 40, PIN_W - 180, "semibold", (40, 36, 32), 2,
                th["accent"], th["bg"], align="center")
    cv.page(info["images"][0] if info["images"] else None, (110, y + 50, PIN_W - 110, PIN_H - SAFE - 20),
            th["paper"], th["shadow"])


DRAW = {"hero": design_hero, "inside": design_inside, "steps": design_steps, "pages": design_pages,
        "benefit": design_benefit}


# ---- texts per Pin ----------------------------------------------------------------------------
def page_word(listing: Dict[str, Any]) -> str:
    names = " ".join(listing.get("items") or []).lower()
    return "Sheet" if (".xlsx" in names and ".pdf" not in names) else "Page"


def pin_texts(listing: Dict[str, Any], board: str) -> List[Dict[str, str]]:
    title = clean(listing["title"]) or "Printable pack"
    short = cut(title, 60)
    items = buyer_items(listing)
    desc = clean(listing.get("description") or "")
    base = cut(desc, 260) if desc else f"{title}, ready to download."
    inside = ("What you get: " + "; ".join(items) + ".") if items else ""
    tail = "Instant digital download for personal use. Link to the shop is in the Pin."
    word = page_word(listing).lower()
    titles = [title, f"What's inside the {short}", f"How the {short} works", f"{short}: a look at the {word}s",
              first_sentence(desc, 90) or f"{short} for your own routine"]
    descs = [f"{base} {inside} {tail}", f"{inside or base} {base if inside else ''} {tail}",
             f"How it works: {'; '.join(use_steps(listing))}. {base} {tail}",
             f"A look at the real {word}s of the {short}. {inside} {tail}",
             f"{base} {tail}"]
    alts = ["Pin with the title {t} on a coloured band above a preview of the first page.",
            "Pin listing what is inside the {t} with a small page preview.",
            "Pin showing three numbered steps to use the {t} and a page preview.",
            "Pin with three page previews of the {t} fanned out above the title.",
            "Pin with a short quote about the {t}, the title and a page preview."]
    out = []
    for design, t, d, alt in zip(DESIGNS, titles, descs, alts):
        out.append({"design": design, "title": cut(clean(t), TITLE_MAX),
                    "description": cut(clean(re.sub(r"\s+", " ", d)), DESC_MAX),
                    "alt": cut(clean(alt.format(t=short)), 500), "board": board, "link": LINK_PLACEHOLDER})
    return out


# ---- make + check ---------------------------------------------------------------------------------
def make_pins(pack_dir: Path | str, out_dir: Path | str, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Make the 5 ready Pins of one pack into `out_dir` (the pack's pinterest folder), check them, and return
    the pins.json data. The folder is replaced atomically; the pack folder is only read."""
    pack_dir, out_dir = Path(pack_dir), Path(out_dir)
    listing = read_listing(pack_dir)
    board = pick_board(listing)
    th = _theme(pack_dir)
    images = pack_images(pack_dir)
    info = {"images": images, "items": buyer_items(listing, 4), "steps": use_steps(listing),
            "benefit": first_sentence(listing.get("description") or "", 150) or clean(listing["title"]),
            "pack_title": cut(clean(listing["title"]), TITLE_MAX) or "Printable pack",
            "page_word": page_word(listing)}
    texts = pin_texts(listing, board)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".pins-", dir=str(out_dir.parent)))
    try:
        pins = []
        for index, meta in enumerate(texts, 1):
            info["headline"] = meta["title"]
            cv = Canvas(PIN_W, PIN_H, th["bg"])
            DRAW[meta["design"]](cv, th, info)
            name = f"pin-{index}.png"
            cv.image.save(stage / name, "PNG", optimize=True)
            pins.append(dict(meta, file=name, width=PIN_W, height=PIN_H, layout=cv.blocks))
        data = {"version": FORMAT_VERSION, "pack": pack_dir.name, "board": board, "link": LINK_PLACEHOLDER,
                "theme": {k: hex_of(v) for k, v in th.items()},
                "made_at": (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(
                    datetime.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
                "note": "Ready to post by hand. Replace [STORE LINK] with the store link. Nothing auto-posts.",
                "pins": pins}
        (stage / META_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")
        problems = check_pins(stage)
        data["check"] = {"ok": not problems, "problems": problems}
        (stage / META_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")
        (stage / SHEET_FILE).write_text(sheet_text(data), encoding="utf-8")
        if out_dir.exists():
            old = out_dir.parent / f".pins-old-{os.getpid()}"
            out_dir.rename(old)
            stage.rename(out_dir)
            shutil.rmtree(old, ignore_errors=True)
        else:
            stage.rename(out_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return data


def sheet_text(data: Dict[str, Any]) -> str:
    lines = [f"Pinterest Pins: {data.get('pack')}", "Robot edi post cheyadu. Meeru phone / computer nunchi post "
             f"cheyandi; {LINK_PLACEHOLDER} badulu mee store link pettandi.", ""]
    for i, pin in enumerate(data.get("pins") or [], 1):
        lines += [f"== Pin {i}: {pin['file']} ==", f"Title: {pin['title']}", f"Description: {pin['description']}",
                  f"Board: {pin['board']}", f"Link: {pin['link']}", f"Alt text: {pin['alt']}", ""]
    return "\n".join(lines)


def check_layout(blocks: List[Dict[str, Any]], width: int, height: int, label: str) -> List[str]:
    """Re-measure every text line with its font: inside the safe margin, no words lost, readable colours."""
    problems: List[str] = []
    if not blocks:
        return [f"{label}: no text on the image"]
    for block in blocks:
        role = f"{label} {block.get('role')}"
        lines = [str(x) for x in block.get("lines") or []]
        text = str(block.get("text") or "")
        if not text.strip() or not lines:
            problems.append(f"{role}: empty text")
            continue
        if "".join(lines).replace(" ", "") != text.replace(" ", ""):
            problems.append(f"{role}: words missing from the image")
        try:
            fnt = font(str(block["weight"]), int(block["size"]))
            x, y, w, lh = int(block["x"]), int(block["y"]), int(block["w"]), int(block["line_h"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"{role}: layout data broken")
            continue
        for i, line in enumerate(lines):
            box = fnt.getbbox(line)
            lw = int(box[2] - box[0])
            lx = x + (w - lw) // 2 if block.get("align") == "center" else x
            left, right = lx + box[0], lx + box[2]
            top, bottom = y + i * lh + box[1], y + i * lh + box[3]
            if lw > w:
                problems.append(f"{role}: line wider than its box ('{line[:30]}')")
            if left < SAFE or right > width - SAFE or top < SAFE or bottom > height - SAFE:
                problems.append(f"{role}: text runs off the image ('{line[:30]}')")
        try:
            if contrast(rgb_of(block["fill"]), rgb_of(block["bg"])) < MIN_CONTRAST:
                problems.append(f"{role}: text colour too weak")
        except (KeyError, ValueError, TypeError):
            problems.append(f"{role}: colours missing")
        problems += [f"{role}: {p.split(': ', 1)[1]}" for p in text_problems(text, "x", 10 ** 6)
                     if "money" in p or "brand" in p]
    return problems


def check_pins(folder: Path | str) -> List[str]:
    """The Pin checker. [] means every Pin is ready to post."""
    folder = Path(folder)
    try:
        data = json.loads((folder / META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"{META_FILE} missing or broken"]
    pins = data.get("pins") if isinstance(data, dict) else None
    if not isinstance(pins, list) or len(pins) != PIN_COUNT:
        return [f"need {PIN_COUNT} Pins, found {len(pins) if isinstance(pins, list) else 0}"]
    problems: List[str] = []
    try:
        from PIL import Image
    except ImportError:
        return ["Pillow missing: cannot check the images"]
    for i, pin in enumerate(pins, 1):
        label = f"Pin {i}"
        path = folder / str(pin.get("file") or "")
        if not pin.get("file") or not path.is_file():
            problems.append(f"{label}: image file missing")
            continue
        try:
            with Image.open(path) as im:
                size = im.size
        except Exception:
            problems.append(f"{label}: image does not open")
            continue
        if size != (PIN_W, PIN_H):
            problems.append(f"{label}: size {size[0]}x{size[1]} (need {PIN_W}x{PIN_H})")
        problems += text_problems(pin.get("title"), f"{label} title", TITLE_MAX)
        problems += text_problems(pin.get("description"), f"{label} description", DESC_MAX)
        problems += text_problems(pin.get("board"), f"{label} board", 100)
        problems += text_problems(pin.get("alt"), f"{label} alt text", 500)
        if str(pin.get("link") or "") != LINK_PLACEHOLDER:
            problems.append(f"{label}: link placeholder missing")
        problems += check_layout(pin.get("layout") or [], PIN_W, PIN_H, label)
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Ready-to-post Pins for one pack (nothing is posted).")
    parser.add_argument("pack")
    parser.add_argument("out")
    args = parser.parse_args(argv)
    data = make_pins(args.pack, args.out)
    print("ready" if data["check"]["ok"] else "NOT ready:\n" + "\n".join(data["check"]["problems"]))
    return 0 if data["check"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
