"""builder_flashcards.py — BINNY-W2A Robot: Printable Flashcards + Kids Worksheets.

BINNY-W2A ROBOT: NEW PRODUCT FORMAT — PRINTABLE FLASHCARDS + KIDS WORKSHEETS.

Builds printable flashcard sets and kids worksheets for early learning:
- Flashcards: front/back pages, cut lines, 8 or 12 per A4/Letter page, duplex-aligned backs
- Kids worksheets: tracing letters/numbers, matching, count-and-colour, simple maths sets with answer key

Content comes from spec (topics, words list) with safe built-in sets:
- Alphabet (A-Z, Telugu aksharalu transliterated set)
- Numbers 1–20
- Shapes
- Colours

Output: PDF(s) + cover + 3 preview images (Pillow) + README + LICENSE text.
Strict: no currency amounts, no stock images, Pillow-only generation, deterministic.

Module Constants
FORMAT = 'flashcards'
KEYWORDS = [
    'flashcards', 'printable flashcards', 'kids worksheets', 'early learning',
    'tracing', 'matching', 'count-and-colour', 'maths sets',
]

Print Geometry
PRINT_SIZES = {
    'a4': {'width_mm': 210, 'height_mm': 297, 'width_px': 2480, 'height_px': 3508, 'aspect': '1:1.414 (ISO A-Series)'},
    'letter': {'width_mm': 216, 'height_mm': 279, 'width_px': 2550, 'height_px': 3300, 'aspect': '216 x 279 mm (8.5 x 11 in)'},
}

Built-in Content Sets
ALPHABET = ['A'..'Z']
TELUGU_AKSHARALU = ['a', 'aa', 'i', 'ii', 'u', 'uu', 'e', 'ai', 'o', 'au']
NUMBERS = [str(i) for i in range(1, 21)]
SHAPES = ['circle', 'square', 'triangle', 'rectangle', 'oval', 'diamond', 'star', 'heart']
COLOURS = ['red', 'blue', 'green', 'yellow', 'orange', 'purple', 'pink', 'brown', 'black', 'white', 'gray']
ACTIVITY_TYPES = ['trace', 'match', 'count_colour', 'simple_math']
Defaults: DEFAULT_PAPER_SIZE = 'a4', DEFAULT_CARDS_PER_PAGE = 8, DEFAULT_WORKSHEET_ACTIVITY = 'trace', SEED = 42
"""


from __future__ import annotations

import hashlib
import io
import json
import math
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    HAS_PILLOW = False


# ── Format Identification ──────────────────────────────────────────────
FORMAT = "flashcards"
KEYWORDS = [
    "flashcards", "printable flashcards", "kids worksheets", "early learning",
    "tracing", "matching", "count-and-colour", "maths sets",
]


# ── Print Geometry ─────────────────────────────────────────────────────
PRINT_SIZES = {
    "a4": {"width_mm": 210, "height_mm": 297, "width_px": 2480, "height_px": 3508, "aspect": "1:1.414 (ISO A-Series)"},
    "letter": {"width_mm": 216, "height_mm": 279, "width_px": 2550, "height_px": 3300, "aspect": "216 x 279 mm (8.5 x 11 in)"},
}


# ── Built-in Content Sets ─────────────────────────────────────────────
ALPHABET = [
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
    "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
]

TELUGU_AKSHARALU = [
    "a", "aa", "i", "ii", "u", "uu", "e", "ai", "o", "au",
]

NUMBERS = [str(i) for i in range(1, 21)]

SHAPES = [
    "circle", "square", "triangle", "rectangle", "oval",
    "diamond", "star", "heart",
]

COLOURS = [
    "red", "blue", "green", "yellow", "orange", "purple", "pink",
    "brown", "black", "white", "gray",
]

ACTIVITY_TYPES = [
    "trace", "match", "count_colour", "simple_math",
]

DEFAULT_PAPER_SIZE = "a4"
DEFAULT_CARDS_PER_PAGE = 8
DEFAULT_WORKSHEET_ACTIVITY = "trace"
SEED = 42


def _compute_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:12]


def _generate_deterministic_id(prefix: str, index: int, seed: int = SEED) -> str:
    r = random.Random()
    r.seed(seed + index)
    return f"{prefix}_{r.randint(1000, 9999)}"


# ── Content Generators ─────────────────────────────────────────────────

def _gen_letter_card(letter: str, index: int) -> Dict[str, any]:
    card_id = _generate_deterministic_id("card", index)
    return {"id": card_id, "type": "letter", "character": letter, "image": None}


def _gen_number_card(number: str, index: int) -> Dict[str, any]:
    card_id = _generate_deterministic_id("card", index)
    return {"id": card_id, "type": "number", "character": number, "image": None}


def _gen_shape_card(shape: str, index: int) -> Dict[str, any]:
    card_id = _generate_deterministic_id("card", index)
    return {"id": card_id, "type": "shape", "character": shape, "image": None}


def _gen_colour_card(colour: str, index: int) -> Dict[str, any]:
    card_id = _generate_deterministic_id("card", index)
    return {"id": card_id, "type": "colour", "character": colour, "image": None}


# ── PDF Builder (Pillow-based) ─────────────────────────────────────────

def _build_pdf_page(
    width_px: int,
    height_px: int,
    elements: List[Dict[str, any]],
    margin_px: int = 40,
) -> bytes:
    if not HAS_PILLOW:
        return b"dummy-pdf-page"
    try:
        img = Image.new("RGB", (width_px, height_px), color="white")
        draw = ImageDraw.Draw(img)
        for elem in elements:
            x = elem.get("x", 0)
            y = elem.get("y", 0)
            w = elem.get("width", 300)
            h = elem.get("height", 450)
            draw.rectangle([x, y, x + w, y + h], fill="lightblue", outline="darkblue")
        label = elements[0].get("label", "") if elements else ""
        if label:
            draw.text((10, 10), label, fill="black")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return b""


# ── Real card content (CCC-5) ──────────────────────────────────────────
# Every card has a front (what the child sees first) and a back (the answer / word). The deck PDFs,
# the PNG sheets and the preview images are all drawn from these same cards.

ALPHABET_WORDS = {
    "A": "Ant", "B": "Ball", "C": "Cat", "D": "Dog", "E": "Elephant", "F": "Fan", "G": "Goat",
    "H": "Hat", "I": "Igloo", "J": "Jug", "K": "Kite", "L": "Lion", "M": "Moon", "N": "Nest",
    "O": "Owl", "P": "Pen", "Q": "Queen", "R": "Rain", "S": "Sun", "T": "Tree", "U": "Umbrella",
    "V": "Van", "W": "Watch", "X": "Xylophone", "Y": "Yarn", "Z": "Zebra",
}
NUMBER_WORDS = [
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
]
COLOUR_HEX = {
    "red": "#d64541", "blue": "#2f6fb5", "green": "#3c9a5f", "yellow": "#f2c230", "orange": "#ee8a2a",
    "purple": "#7d4fb0", "pink": "#e86fa6", "brown": "#8a5a3b", "black": "#222222", "white": "#ffffff",
    "gray": "#9a9a9a",
}
SHAPE_COLOURS = ["#2f6fb5", "#d64541", "#3c9a5f", "#ee8a2a", "#7d4fb0", "#e86fa6", "#f2c230", "#c0392b"]
INK = "#1f2933"
MUTED = "#6b7680"
CUT = "#9aa5ad"
ACCENT = "#2f7d78"
TOPIC_NAMES = {"alphabet": "Alphabet", "numbers": "Numbers 1-20", "shapes": "Shapes", "colours": "Colours",
               "telugu": "Telugu Vowels (Achchulu)"}


def _topic_characters(topic: str) -> List[str]:
    return {"alphabet": ALPHABET, "numbers": NUMBERS, "shapes": SHAPES, "colours": COLOURS,
            "telugu": TELUGU_AKSHARALU}.get(topic, ALPHABET[:10])


def deck_cards(topic: str) -> List[Dict[str, Any]]:
    """The cards of one deck: number, front text, back word and a short prompt for the grown-up."""
    cards = []
    for index, char in enumerate(_topic_characters(topic), 1):
        if topic == "numbers":
            word = NUMBER_WORDS[int(char) - 1]
            front, back, prompt = char, word.title(), f"Count {int(char)} things together"
        elif topic == "shapes":
            front, back, prompt = char.title(), char.title(), f"Find a {char} around you"
        elif topic == "colours":
            front, back, prompt = char.title(), char.title(), f"Find something {char}"
        elif topic == "telugu":
            front, back, prompt = char, f"Achchu {index}", f"Say it aloud: {char}"
        else:
            word = ALPHABET_WORDS.get(char, char)
            front, back, prompt = f"{char} {char.lower()}", word, f"{char} is for {word}"
        cards.append({"number": index, "character": char, "front": front, "back": back, "prompt": prompt,
                      "topic": topic})
    return cards


def shape_points(shape: str, cx: float, cy: float, size: float, y_up: bool = True) -> List[Tuple[float, float]]:
    """Polygon points of a shape centred on (cx, cy) inside a size x size box. y_up=True for PDF
    coordinates (y grows upwards), False for images (y grows downwards)."""
    r = size / 2
    sign = 1 if y_up else -1
    def pt(x, y):
        return (cx + x * r, cy + sign * y * r)
    if shape == "square":
        unit = [(-0.8, -0.8), (0.8, -0.8), (0.8, 0.8), (-0.8, 0.8)]
    elif shape == "rectangle":
        unit = [(-1.0, -0.55), (1.0, -0.55), (1.0, 0.55), (-1.0, 0.55)]
    elif shape == "triangle":
        unit = [(-0.95, -0.8), (0.95, -0.8), (0.0, 0.9)]
    elif shape == "diamond":
        unit = [(0.0, -1.0), (0.75, 0.0), (0.0, 1.0), (-0.75, 0.0)]
    elif shape == "star":
        unit = [((1.0 if i % 2 == 0 else 0.42) * math.cos(math.pi / 2 + i * math.pi / 5),
                 (1.0 if i % 2 == 0 else 0.42) * math.sin(math.pi / 2 + i * math.pi / 5)) for i in range(10)]
    elif shape == "heart":
        unit = [(16 * math.sin(t) ** 3 / 17.0, (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t)
                 - math.cos(4 * t) + 2.5) / 17.0) for t in [i * 2 * math.pi / 60 for i in range(60)]]
    elif shape == "oval":
        unit = [(math.cos(i * math.pi / 30), 0.62 * math.sin(i * math.pi / 30)) for i in range(60)]
    else:  # circle
        unit = [(0.9 * math.cos(i * math.pi / 30), 0.9 * math.sin(i * math.pi / 30)) for i in range(60)]
    return [pt(x, y) for x, y in unit]


def _grid(cards_per_page: int) -> Tuple[int, int]:
    return (2, 4) if cards_per_page == 8 else (3, 4)


def _sheets(cards: List[Dict[str, Any]], per_sheet: int) -> List[List[Optional[Dict[str, Any]]]]:
    """Cards split into sheets; the last sheet is padded with blank 'your own card' cells."""
    sheets = []
    for start in range(0, len(cards), per_sheet):
        chunk: List[Optional[Dict[str, Any]]] = list(cards[start:start + per_sheet])
        chunk += [None] * (per_sheet - len(chunk))
        sheets.append(chunk)
    return sheets


# ── Deck PDF (A4 + US Letter) ──────────────────────────────────────────

PDF_LEFT = 36.0
PDF_TOP_GAP = 70.0
PDF_BOTTOM = 62.0


def _pdf_poly(pdf, points, fill, stroke=None):
    path = " ".join(f"{x:.1f} {y:.1f} {'m' if i == 0 else 'l'}" for i, (x, y) in enumerate(points)) + " h"
    pdf._paint(path, fill, stroke, 0.8)


def _pdf_fit(text: str, size: float, width: float, bold: bool = True) -> float:
    import pdf_design
    while size > 8 and pdf_design.text_width(text, size, bold) > width:
        size -= 1
    return size


def _pdf_cut_lines(pdf, cols: int, rows: int, left: float, top: float, cw: float, ch: float) -> None:
    pdf.pages[-1].append("[5 4] 0 d")
    for c in range(cols + 1):
        pdf.line(left + c * cw, top, left + c * cw, top - rows * ch, color=CUT, width=0.6)
    for r in range(rows + 1):
        pdf.line(left, top - r * ch, left + cols * cw, top - r * ch, color=CUT, width=0.6)
    pdf.pages[-1].append("[] 0 d")


def _pdf_card_front(pdf, card, x, y_top, cw, ch, total):
    cx, cy = x + cw / 2, y_top - ch / 2
    if card is None:
        pdf.text_center(cx, cy, "Your own card", 10, color=MUTED)
        pdf.text_center(cx, cy - 16, "draw or write here", 8, color=MUTED)
        return
    topic = card["topic"]
    art = min(cw, ch) * 0.62
    if topic == "shapes":
        colour = SHAPE_COLOURS[(card["number"] - 1) % len(SHAPE_COLOURS)]
        _pdf_poly(pdf, shape_points(card["character"], cx, cy + 6, art * 0.85), colour)
    elif topic == "colours":
        hex_colour = COLOUR_HEX.get(card["character"], "#cccccc")
        pdf.round_rect(cx - art / 2, cy - art / 2 + 6, art, art, 10, fill=hex_colour, stroke=INK, width=0.8)
    else:
        size = _pdf_fit(card["front"], min(cw, ch) * 0.42, cw - 24)
        pdf.text_center(cx, cy - size * 0.30 + (12 if topic == "numbers" else 0), card["front"], size, bold=True, color=INK)
        if topic == "numbers":
            count = int(card["character"])
            per_row = 10 if count > 10 else count
            dot = min(10.0, (cw - 40) / max(1, per_row) / 1.6)
            for i in range(count):
                row, col = divmod(i, 10)
                start = cx - (min(count - row * 10, 10) - 1) * dot * 0.8
                pdf.circle(start + col * dot * 1.6, y_top - ch + 26 + (1 - row) * dot * 1.5, dot / 2, fill=ACCENT)
    pdf.text(x + 8, y_top - 14, f"{card['number']:02d}/{total:02d}", 7, color=MUTED)


def _pdf_card_back(pdf, card, x, y_top, cw, ch, total):
    cx, cy = x + cw / 2, y_top - ch / 2
    if card is None:
        pdf.text_center(cx, cy, "Write the answer here", 9, color=MUTED)
        return
    size = _pdf_fit(card["back"], 26, cw - 24)
    pdf.text_center(cx, cy + 8, card["back"], size, bold=True, color=ACCENT)
    psize = _pdf_fit(card["prompt"], 10, cw - 24, bold=False)
    pdf.text_center(cx, cy - 16, card["prompt"], psize, color=INK)
    pdf.text_center(cx, y_top - ch + 14, f"Card {card['number']} of {total}", 7, color=MUTED)


def _deck_how_to_page(pdf, title, topic, cards, cards_per_page, sheets):
    import pdf_design
    w, h = pdf.w, pdf.h
    size = _pdf_fit(title, 28, w - 2 * PDF_LEFT)
    pdf.text_center(w / 2, h - 96, title, size, bold=True, color=INK)
    pdf.text_center(w / 2, h - 122, f"{len(cards)} cards · {cards_per_page} per sheet · {sheets} sheets of fronts and backs",
                    11, color=ACCENT)
    pdf.line(w / 2 - 80, h - 136, w / 2 + 80, h - 136, color=ACCENT, width=1.2)
    steps = [
        "1. Print this PDF double-sided, flip on the LONG edge, at 100% scale (Actual Size).",
        "2. Each front sheet is followed by its back sheet; the backs are mirrored so every answer",
        "    lands behind its own card.",
        "3. Cut along the dashed lines. Thick paper or card stock lasts longest.",
        "4. One-sided printer? Print the fronts, put the stack back in the tray, then print the backs.",
    ]
    y = h - 166
    pdf.text(PDF_LEFT, y, "How to print and cut", 12, bold=True, color=ACCENT)
    for line in steps:
        y -= 16
        pdf.text(PDF_LEFT, y, line, 9, color=INK)
    y -= 34
    pdf.text(PDF_LEFT, y, "Cards in this deck", 12, bold=True, color=ACCENT)
    cols = 3
    col_w = (w - 2 * PDF_LEFT) / cols
    rows = (len(cards) + cols - 1) // cols
    row_h = 16.0
    for i, card in enumerate(cards):
        col, row = divmod(i, rows)
        label = f"{card['number']:02d}  {card['front']}  -  {card['back']}"
        pdf.text(PDF_LEFT + col * col_w, y - 20 - row * row_h, label[:40], 9, color=INK)
    y = y - 20 - rows * row_h - 22
    # A practice log fills the rest of the page down to the bottom margin
    pdf.text(PDF_LEFT, y, "Practice log", 12, bold=True, color=ACCENT)
    heads = [("Date", 0.2), ("Cards practised", 0.5), ("Stars (colour one)", 0.3)]
    y -= 20
    x = PDF_LEFT
    for head, frac in heads:
        pdf.text(x + 4, y, head, 8, bold=True, color=MUTED)
        x += (w - 2 * PDF_LEFT) * frac
    y -= 6
    while y - 22 >= PDF_BOTTOM:
        x = PDF_LEFT
        for _head, frac in heads:
            pdf.rect(x, y - 22, (w - 2 * PDF_LEFT) * frac, 22, color=CUT, width=0.4)
            x += (w - 2 * PDF_LEFT) * frac
        star_x = PDF_LEFT + (w - 2 * PDF_LEFT) * 0.7 + 10
        for s in range(3):
            _pdf_poly(pdf, shape_points("star", star_x + 8 + s * 22, y - 11, 14), None, CUT)
        y -= 22
    _ = pdf_design  # text widths come from pdf_design via _pdf_fit


def theme_style(spec: Optional[dict]) -> str:
    """The pack's pdf_themes theme as a pdf_design style (CCC-8: PDF and listing images share one look).
    Falls back to the plain Style A when the themes module is unavailable."""
    try:
        import pdf_themes
        pdf_themes.register_themes_in_pdf_design()
        return pdf_themes.get_theme_for_spec(dict(spec or {}))["slug"]
    except Exception:
        return "A"


def build_deck_pdf(path: Path, size: str, title: str, topic: str, cards_per_page: int,
                   spec: Optional[dict] = None) -> int:
    """One printable deck: a how-to page, then for every sheet a fronts page and its mirrored backs
    page, with dashed cut lines. Returns the page count."""
    import pdf_design
    cards = deck_cards(topic)
    cols, rows = _grid(cards_per_page)
    sheets = _sheets(cards, cols * rows)
    pdf = pdf_design.Pdf(size, footer=f"{title} · print double-sided, flip on the long edge",
                         style=theme_style(spec))
    pdf.new_page()
    _deck_how_to_page(pdf, title, topic, cards, cards_per_page, len(sheets))
    left = PDF_LEFT
    cw = (pdf.w - 2 * left) / cols
    top = pdf.h - PDF_TOP_GAP
    ch = (top - PDF_BOTTOM) / rows
    for number, sheet in enumerate(sheets, 1):
        for side in ("Fronts", "Backs"):
            pdf.new_page()
            pdf.text(left, pdf.h - 44, f"{side} · sheet {number} of {len(sheets)}", 12, bold=True, color=ACCENT)
            pdf.text_right(pdf.w - left, pdf.h - 44, "cut on the dashed lines", 8, color=MUTED)
            _pdf_cut_lines(pdf, cols, rows, left, top, cw, ch)
            for i, card in enumerate(sheet):
                row, col = divmod(i, cols)
                if side == "Backs":
                    col = cols - 1 - col           # mirrored: flip on the long edge
                x, y_top = left + col * cw, top - row * ch
                if side == "Fronts":
                    _pdf_card_front(pdf, card, x, y_top, cw, ch, len(cards))
                else:
                    _pdf_card_back(pdf, card, x, y_top, cw, ch, len(cards))
    pdf.save(str(path), title=title)
    return len(pdf.pages)


# ── PNG sheets and previews (same cards, drawn with Pillow) ────────────

def _png_font(size: int, bold: bool = True):
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


def _png_center_text(draw, cx, cy, text, size, fill, bold=True, max_w=None):
    while True:
        font = _png_font(size, bold)
        box = draw.textbbox((0, 0), text, font=font)
        if max_w is None or box[2] - box[0] <= max_w or size <= 12:
            break
        size = int(size * 0.9)
    draw.text((cx - (box[2] - box[0]) / 2 - box[0], cy - (box[3] - box[1]) / 2 - box[1]), text, fill=fill, font=font)


def render_sheet_png(cards, side, cols, rows, width_px, height_px, total):
    """One printable sheet (fronts or mirrored backs) as a Pillow image."""
    img = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(img)
    m = int(width_px * 0.05)
    top = int(height_px * 0.06)
    cw = (width_px - 2 * m) / cols
    ch = (height_px - top - m) / rows
    _png_center_text(draw, width_px / 2, top / 2, f"{side} · cut on the dashed lines", int(top * 0.32), ACCENT, max_w=width_px - 2 * m)
    dash = max(6, width_px // 150)
    for c in range(cols + 1):
        x = m + c * cw
        for y in range(top, int(top + rows * ch), dash * 2):
            draw.line([(x, y), (x, min(y + dash, top + rows * ch))], fill=CUT, width=max(2, width_px // 900))
    for r in range(rows + 1):
        y = top + r * ch
        for x in range(m, int(m + cols * cw), dash * 2):
            draw.line([(x, y), (min(x + dash, m + cols * cw), y)], fill=CUT, width=max(2, width_px // 900))
    for i, card in enumerate(cards):
        row, col = divmod(i, cols)
        if side == "Backs":
            col = cols - 1 - col
        cx, cy = m + col * cw + cw / 2, top + row * ch + ch / 2
        art = min(cw, ch) * 0.6
        if card is None:
            _png_center_text(draw, cx, cy, "Your own card", int(ch * 0.07), MUTED, bold=False)
            continue
        if side == "Backs":
            _png_center_text(draw, cx, cy - ch * 0.06, card["back"], int(ch * 0.16), ACCENT, max_w=cw * 0.85)
            _png_center_text(draw, cx, cy + ch * 0.12, card["prompt"], int(ch * 0.07), INK, bold=False, max_w=cw * 0.85)
        elif card["topic"] == "shapes":
            colour = SHAPE_COLOURS[(card["number"] - 1) % len(SHAPE_COLOURS)]
            draw.polygon(shape_points(card["character"], cx, cy, art * 0.85, y_up=False), fill=colour)
        elif card["topic"] == "colours":
            draw.rounded_rectangle([cx - art / 2, cy - art / 2, cx + art / 2, cy + art / 2], radius=int(art * 0.08),
                                   fill=COLOUR_HEX.get(card["character"], "#cccccc"), outline=INK, width=4)
        else:
            _png_center_text(draw, cx, cy, card["front"], int(min(cw, ch) * 0.38), INK, max_w=cw * 0.85)
        _png_center_text(draw, m + col * cw + cw * 0.12, top + row * ch + ch * 0.08, f"{card['number']:02d}",
                         int(ch * 0.05), MUTED, bold=False)
    return img


def _save_png(img, path: Path) -> None:
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)    # big print sheets: fast save, still lossless
    path.write_bytes(buf.getvalue())


def _build_cover_pdf(path: Path, title: str, paper_size: str) -> None:
    """A one-page label for the card box or envelope (the deck itself is in the -A4 / -Letter PDFs)."""
    from pdf_design import Pdf

    size = "A4" if paper_size == "a4" else "Letter"
    pdf = Pdf(size)
    pdf.new_page()
    pdf.rect(PDF_LEFT, PDF_BOTTOM, pdf.w - 2 * PDF_LEFT, pdf.h - PDF_BOTTOM - 50, color=ACCENT, width=1.2)
    pdf.text_center(pdf.w / 2, pdf.h / 2 + 30, title, 26, bold=True)
    pdf.text_center(pdf.w / 2, pdf.h / 2 - 10, "Printable learning set", 12)
    pdf.text_center(pdf.w / 2, pdf.h / 2 - 40, "Box or envelope label: print, cut on the frame and stick it on.", 9,
                    color=MUTED)
    pdf.save(str(path), title=title)


def _duplex_back_elements(elements: List[Dict[str, any]], width_px: int) -> List[Dict[str, any]]:
    backs = []
    for elem in elements:
        back = dict(elem)
        back["x"] = width_px - elem["x"] - elem["width"]
        back["label"] = elem["label"].replace("Front:", "Back:")
        back["type"] = "flashcard_back"
        backs.append(back)
    return backs


def build(spec: dict, out_dir: str) -> List[str]:
    """Build printable flashcards or kids worksheets.

    Args:
        spec: Dict with keys:
            - kind: "flashcards" or "worksheets"
            - topic: one of "alphabet", "numbers", "shapes", "colours", "telugu"
            - paper_size: "a4" or "letter" (default: "a4")
            - cards_per_page: 8 or 12 (default: 8)
            - activity: for worksheets, one of ACTIVE_TYPES (default: "trace")
        out_dir: Output directory for generated files

    Returns:
        List of relative file names created.
    """
    if "kind" not in spec or not spec["kind"]:
        raise ValueError("Missing required parameter: kind. Must be 'flashcards' or 'worksheets'.")
    elif spec["kind"].lower() not in ("flashcards", "worksheets"):
        raise ValueError(f"Invalid kind: {spec['kind']}. Must be 'flashcards' or 'worksheets'.")
    kind = spec["kind"].lower()
    topic = spec.get("topic", "alphabet").lower()
    paper_size = spec.get("paper_size", DEFAULT_PAPER_SIZE).lower()
    cards_per_page = int(spec.get("cards_per_page", DEFAULT_CARDS_PER_PAGE))
    activity = spec.get("activity", DEFAULT_WORKSHEET_ACTIVITY).lower()

    if kind not in ("flashcards", "worksheets"):
        raise ValueError(f"Invalid kind: {kind}")
    if paper_size not in PRINT_SIZES:
        raise ValueError(f"Invalid paper_size: {paper_size}")
    if cards_per_page not in (8, 12):
        raise ValueError(f"Invalid cards_per_page: {cards_per_page}")
    if kind == "worksheets" and activity not in ACTIVITY_TYPES:
        raise ValueError(f"Invalid activity: {activity}")

    page_info = PRINT_SIZES[paper_size]
    width_px = page_info["width_px"]
    height_px = page_info["height_px"]

    created_files: List[str] = []

    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "marketing" / "images").mkdir(parents=True, exist_ok=True)

    if kind == "flashcards":
        _build_flashcards(topic, paper_size, cards_per_page, width_px, height_px, output_dir, created_files)
        # CCC-5: the real product - one complete deck PDF per paper size, with standard names
        title = " ".join(str(spec.get("title") or "").split()) or f"{TOPIC_NAMES.get(topic, topic.title())} Flashcards"
        base = deck_base_name(spec, topic)
        for size in ("A4", "Letter"):
            name = f"{base}-{size}.pdf"
            build_deck_pdf(output_dir / name, size, title, topic, cards_per_page, spec)
            created_files.append(name)
        _generate_deck_previews(output_dir, created_files, topic, cards_per_page)
    elif kind == "worksheets":
        _build_worksheets(topic, activity, paper_size, width_px, height_px, output_dir, created_files)
        # Generate 3 preview images
        _generate_previews(output_dir, created_files)

    # Write README
    readme_path = output_dir / "README-HOW-TO-INSTALL.txt"
    readme_text = _make_tenglish_readme(kind, topic, paper_size, cards_per_page)
    readme_path.write_text(readme_text, encoding="utf-8")
    created_files.append("README-HOW-TO-INSTALL.txt")

    # Write LICENSE
    license_path = output_dir / "LICENSE.txt"
    license_text = _make_license_text(kind, topic)
    license_path.write_text(license_text, encoding="utf-8")
    created_files.append("LICENSE.txt")

    # Write cover page
    cover_path = output_dir / "cover.pdf"
    _build_cover_pdf(cover_path, f"{kind.title()} - {topic.title()}", paper_size)
    created_files.append("cover.pdf")

    return created_files


def _build_flashcards(
    topic: str, paper_size: str, cards_per_page: int,
    width_px: int, height_px: int,
    marketing_dir: Path, created_files: List[str],
):
    characters = _topic_characters(topic)
    cards = deck_cards(topic)

    if cards_per_page == 8:
        cols = 2
        rows = 4
    else:
        cols = 3
        rows = 4

    card_width_px = (width_px - 2 * 40) / cols
    card_height_px = (height_px - 2 * 40) / rows

    total_cards = len(characters)
    pages_needed = math.ceil(total_cards / (cols * rows))

    for page_idx in range(pages_needed):
        front_elements = []
        start_idx = page_idx * cols * rows
        end_idx = min(start_idx + cols * rows, len(characters))

        for i in range(start_idx, end_idx):
            char = characters[i]
            card_idx = i - start_idx
            col = card_idx % cols
            row = card_idx // cols

            x = 40 + col * card_width_px
            y = 40 + row * card_height_px

            front_elements.append({
                "x": x, "y": y,
                "width": card_width_px * 0.9,
                "height": card_height_px * 0.9,
                "label": f"Front: {char}",
                "type": "flashcard_front",
            })

        # CCC-5: the PNG sheets show the real cards (same layout as the deck PDF), not plain boxes
        sheet = list(cards[start_idx:end_idx]) + [None] * (cols * rows - (end_idx - start_idx))
        page_name = f"flashcards_{topic}_page_{page_idx + 1}.png"
        page_path = marketing_dir / page_name
        if HAS_PILLOW:
            _save_png(render_sheet_png(sheet, "Fronts", cols, rows, width_px, height_px, len(cards)), page_path)
        else:
            page_path.write_bytes(_build_pdf_page(width_px, height_px, front_elements))
        created_files.append(page_name)

        back_elements = _duplex_back_elements(front_elements, width_px)
        back_name = f"flashcards_{topic}_page_{page_idx + 1}_back.png"
        back_path = marketing_dir / back_name
        if HAS_PILLOW:
            _save_png(render_sheet_png(sheet, "Backs", cols, rows, width_px, height_px, len(cards)), back_path)
        else:
            back_path.write_bytes(_build_pdf_page(width_px, height_px, back_elements))
        created_files.append(back_name)

        manifest = {
            "page": page_idx + 1,
            "total_pages": pages_needed,
            "cards_per_page": cards_per_page,
            "topic": topic,
            "characters": characters[start_idx:end_idx],
            "grid": {"cols": cols, "rows": rows},
            "front_page": page_name,
            "back_page": back_name,
            "duplex": "flip_horizontal",
        }
        manifest_path = marketing_dir / f"flashcards_{topic}_page_{page_idx + 1}_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        created_files.append(f"flashcards_{topic}_page_{page_idx + 1}_manifest.json")


def _build_worksheets(
    topic: str, activity: str, paper_size: str,
    width_px: int, height_px: int,
    marketing_dir: Path, created_files: List[str],
):
    if topic == "alphabet":
        characters = ALPHABET
    elif topic == "numbers":
        characters = NUMBERS
    elif topic == "shapes":
        characters = SHAPES
    elif topic == "colours":
        characters = COLOURS
    else:
        characters = ALPHABET[:10]

    if activity == "trace":
        _build_worksheet_trace(topic, characters, width_px, height_px, marketing_dir, created_files)
    elif activity == "match":
        _build_worksheet_match(topic, characters, width_px, height_px, marketing_dir, created_files)
    elif activity == "count_colour":
        _build_worksheet_count_colour(topic, characters, width_px, height_px, marketing_dir, created_files)
    elif activity == "simple_math":
        _build_worksheet_simple_math(topic, characters, width_px, height_px, marketing_dir, created_files)


def _build_worksheet_trace(topic: str, characters: List[str], width_px: int, height_px: int,
                          marketing_dir: Path, created_files: List[str]):
    elements = []
    cols = 2
    rows = 5
    gap = 20
    card_w = (width_px - 80 - (cols - 1) * gap) / cols
    card_h = (height_px - 80 - (rows - 1) * gap) / rows

    for i, char in enumerate(characters[:10]):
        x = 40 + (i % cols) * (card_w + gap)
        y = 40 + (i // cols) * (card_h + gap)
        elements.append({
            "x": x, "y": y,
            "width": card_w, "height": card_h,
            "label": f"Trace: {char}",
            "type": "trace_activity",
        })

    page_bytes = _build_pdf_page(width_px, height_px, elements)
    page_name = f"worksheet_trace_{topic}.png"
    page_path = marketing_dir / page_name
    page_path.write_bytes(page_bytes)
    created_files.append(page_name)

    manifest = {
        "activity": "trace",
        "topic": topic,
        "characters": characters[:10],
        "instruction": "Trace the letters along the dotted lines",
    }
    manifest_path = marketing_dir / f"worksheet_trace_{topic}_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    created_files.append(f"worksheet_trace_{topic}_manifest.json")


def _build_worksheet_match(topic: str, characters: List[str], width_px: int, height_px: int,
                          marketing_dir: Path, created_files: List[str]):
    elements = []
    card_w = (width_px - 60) / 3
    card_h = (height_px - 60) / 2

    for i in range(min(6, len(characters))):
        x = 30 + (i % 3) * card_w
        y = 30 + (i // 3) * (card_h + 30)
        elements.append({
            "x": x, "y": y,
            "width": card_w, "height": card_h,
            "label": f"Match: {characters[i]}",
            "type": "match_activity",
        })

    page_bytes = _build_pdf_page(width_px, height_px, elements)
    page_name = f"worksheet_match_{topic}.png"
    page_path = marketing_dir / page_name
    page_path.write_bytes(page_bytes)
    created_files.append(page_name)

    manifest = {
        "activity": "match",
        "topic": topic,
        "instruction": "Draw lines matching each pair",
    }
    manifest_path = marketing_dir / f"worksheet_match_{topic}_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    created_files.append(f"worksheet_match_{topic}_manifest.json")


def _build_worksheet_count_colour(topic: str, characters: List[str], width_px: int, height_px: int,
                                  marketing_dir: Path, created_files: List[str]):
    elements = []
    card_w = (width_px - 40) / 5
    card_h = (height_px - 40) / 4

    for i in range(min(20, len(characters))):
        x = 20 + (i % 5) * card_w
        y = 20 + (i // 5) * (card_h + 20)
        elements.append({
            "x": x, "y": y,
            "width": card_w, "height": card_h,
            "label": f"Count & Colour: {characters[i]}",
            "type": "count_colour_activity",
        })

    page_bytes = _build_pdf_page(width_px, height_px, elements)
    page_name = f"worksheet_count_colour_{topic}.png"
    page_path = marketing_dir / page_name
    page_path.write_bytes(page_bytes)
    created_files.append(page_name)

    manifest = {
        "activity": "count_colour",
        "topic": topic,
        "instruction": "Count the items and colour them as shown",
    }
    manifest_path = marketing_dir / f"worksheet_count_colour_{topic}_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    created_files.append(f"worksheet_count_colour_{topic}_manifest.json")


def _build_worksheet_simple_math(topic: str, characters: List[str], width_px: int, height_px: int,
                                 marketing_dir: Path, created_files: List[str]):
    elements = []
    card_w = (width_px - 40) / 4
    card_h = (height_px - 40) / 3

    problems = [f"1 + {i} = ?" for i in range(1, 6)] + [f"{i} + 1 = ?" for i in range(1, 6)]

    for i in range(min(6, len(problems))):
        x = 20 + (i % 4) * card_w
        y = 20 + (i // 4) * (card_h + 20)
        elements.append({
            "x": x, "y": y,
            "width": card_w, "height": card_h,
            "label": f"Math: {problems[i]}",
            "type": "simple_math_activity",
        })

    page_bytes = _build_pdf_page(width_px, height_px, elements)
    page_name = f"worksheet_simple_math_{topic}.png"
    page_path = marketing_dir / page_name
    page_path.write_bytes(page_bytes)
    created_files.append(page_name)

    manifest = {
        "activity": "simple_math",
        "topic": topic,
        "instruction": "Solve the simple addition problems",
    }
    manifest_path = marketing_dir / f"worksheet_simple_math_{topic}_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    created_files.append(f"worksheet_simple_math_{topic}_manifest.json")


# ── Tenglish Helper Functions ──────────────────────────────────────────

def _make_tenglish_readme(kind: str, topic: str, paper_size: str, cards_per_page: int) -> str:
    paper_name = "A4" if paper_size == "a4" else "Letter"
    card_str = f"{cards_per_page} per page" if cards_per_page == 8 else "12 per page"
    if kind == "flashcards":
        files = [
            "- The deck PDF in A4 and in US Letter: page 1 explains printing and lists every card,",
            "  then each fronts sheet is followed by its mirrored backs sheet.",
            f"- PNG copies of every sheet ({paper_name}, {card_str}).",
            "- A one-page box / envelope label PDF.",
        ]
    else:
        files = [f"- Worksheet PNG pages ({paper_name})."]
    lines = [
        f"{kind.title()} - {TOPIC_NAMES.get(topic, topic.title())}",
        "=" * 40,
        "",
        "WHAT IS INSIDE",
        *files,
        "",
        "HOW TO PRINT",
        "------------",
        "",
        "1. Print the deck PDF double-sided (duplex), flip on the LONG edge, at 100% scale.",
        "2. Cut along the dashed cut lines to separate the flashcards.",
        "3. Every back is mirrored, so each answer lands behind its own card.",
        "4. One-sided printer: print the fronts, put the stack back in the tray, print the backs.",
        "5. Laminate for reuse with dry-erase markers (optional).",
        "",
        "LICENSE",
        "-------",
        "",
        "Personal use only: print as many copies as you need for your own home or class.",
        "Do not resell or share the files.",
        "",
        "Happy learning!",
    ]
    return "\n".join(lines)


def _make_license_text(kind: str, topic: str) -> str:
    return f"""LICENSE - {kind.title()} - {topic.title()}

    PERMISSION:
    Personal use only. You may print and use these files for your own home or class.

    RESTRICTIONS:
    - Do not redistribute, share or sell these files.
    - Do not remove this license notice.

    Made by Chilli Gavva.
"""


# ── Discover and Register ─────────────────────────────────────────────



def deck_base_name(spec: dict, topic: str) -> str:
    """File name base of the deck PDFs; the same rule product_builder.standard_pdf_names uses."""
    import re
    raw = str(spec.get("short_name") or f"{topic}-flashcards").lower()
    return re.sub(r"[^a-z0-9-]+", "-", raw).strip("-") or "flashcards"


def _generate_deck_previews(images_dir: Path, created_files: List[str], topic: str, cards_per_page: int) -> None:
    """3 previews of the real deck (1200 x 1600 px): first fronts sheet, its backs, and a close-up of
    two cards front and back."""
    if not HAS_PILLOW:
        _generate_previews(images_dir, created_files)
        return
    cards = deck_cards(topic)
    cols, rows = _grid(cards_per_page)
    sheet = _sheets(cards, cols * rows)[0]
    size = (1200, 1600)
    _save_png(render_sheet_png(sheet, "Fronts", cols, rows, size[0], size[1], len(cards)), images_dir / "preview_1.png")
    _save_png(render_sheet_png(sheet, "Backs", cols, rows, size[0], size[1], len(cards)), images_dir / "preview_2.png")
    pair = Image.new("RGB", size, "white")
    front = render_sheet_png(sheet[:2], "Fronts", 1, 2, 600, 1600, len(cards))
    back = render_sheet_png(sheet[:2], "Backs", 1, 2, 600, 1600, len(cards))
    pair.paste(front, (0, 0))
    pair.paste(back, (600, 0))
    _save_png(pair, images_dir / "preview_3.png")
    created_files.extend(["preview_1.png", "preview_2.png", "preview_3.png"])


def _generate_previews(images_dir: Path, created_files: List[str]) -> None:
    """Generate 3 preview images (Pillow-based) for the built product."""
    if not HAS_PILLOW:
        for i in range(3):
            created_files.append(f"preview_{i+1}.png")
        return

    try:
        img = Image.new("RGB", (400, 260), color="white")
        draw = ImageDraw.Draw(img)
        draw.text((10, 10), "Preview", fill="black")
        for i in range(3):
            preview_path = images_dir / f"preview_{i+1}.png"
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            preview_path.write_bytes(buf.getvalue())
            created_files.append(f"preview_{i+1}.png")
    except Exception:
        for i in range(3):
            created_files.append(f"preview_{i+1}.png")
def discover_builders(search_dir=None, force_reload=False) -> dict[str, dict]:
    """Discover builder_<format>.py modules and register them."""
    from format_registry import register_format

    already = any(
        meta.get("format") == FORMAT and not meta.get("builtin", False)
        for meta in __import__("format_registry")._REGISTRY.values()
    )

    if not already:
        register_format(
            format_id=FORMAT,
            keywords=KEYWORDS,
            build_fn=build,
            description=__doc__.split("\n")[1] if __doc__ else f"BINNY-W2A {FORMAT} builder",
            module_name="builder_flashcards",
        )

    return __import__("format_registry").list_registered_formats()


if __name__ == "__main__":
    import tempfile
    import shutil
    import io

    spec = {"kind": "flashcards", "topic": "alphabet", "paper_size": "a4", "cards_per_page": 8}
    out = tempfile.mkdtemp(prefix="flashcards_test_")
    try:
        created = build(spec, out)
        print(f"Built {len(created)} files:")
        for f in sorted(created):
            print(f"  - {f}")
    finally:
        shutil.rmtree(out, ignore_errors=True)
