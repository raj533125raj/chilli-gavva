"""Pinterest pin generator for upload packs: 5 distinct 1000x1500 pin designs per pack with A/B title ideas.

Writes to exported_content/marketing/<pack>/pins/ (outside the pack):
- 5 PNG pins: 1000x1500, contrast >= 4.5:1, safe margin 60px, file size < 1MB, deterministic.
- Layouts:
    * Layout A: Big title top + tilted page
    * Layout B: Split screen two pages
    * Layout C: Checklist style (3 benefit ticks)
    * Layout D: "Free printable" badge style (used ONLY when listing says it is free; else skipped and Layout E used)
    * Layout E: Minimal photo-frame
- ab_titles.txt: listing the 5 titles for manual A/B testing
- pins.json: [{file, layout, title, description, alt}, ...]
"""

import html
import json
import os
import re
import unicodedata
import tempfile
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageFont = None  # type: ignore

PIN_WIDTH = 1000
PIN_HEIGHT = 1500
SAFE_MARGIN = 60
MIN_CONTRAST = 4.5

FONT_DIR = "/app/fonts" if os.path.isdir("/app/fonts") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONTS = {
    "regular": "Inter-Regular.ttf",
    "semibold": "Inter-SemiBold.ttf",
    "bold": "Inter-Bold.ttf",
}

_font_cache: Dict[Tuple[str, int], Any] = {}

# Brand names and disallowed words
_BRAND_WORDS = re.compile(
    r"\b(google\s+sheets?|google\s+docs?|microsoft\s+excel|microsoft\s+word|excel|notion|canva|etsy|gumroad|payhip|goodnotes|apple\s+numbers|ipad|amazon|chatgpt|openai|microsoft|google|linkedin)\b",
    re.IGNORECASE,
)

# Clickbait words to remove or avoid in titles
_CLICKBAIT_WORDS = re.compile(
    r"\b(viral|shocking|insane|secret|magic|unbelievable|miracle|hack|cheat|crazy|guaranteed|mindblowing)\b",
    re.IGNORECASE,
)

# Money / currency pattern (both with numbers and standalone currency words)
_MONEY_REGEX = re.compile(
    r"(?:\b(?:USD|INR|EUR|GBP|AUD|CAD|NZD|SGD|HKD|JPY|CNY|RMB|AED|CHF|ZAR|PKR|BDT|NGN|PHP|KRW|RUB|BRL|MXN|Rs\.?|rupees?|dollars?|euros?|pounds?|yen|cents?|paise|paisa)\s*[+-]?(?:\d{1,3}(?:[,\s]\d{2,3})+|\d+)(?:[.,]\d+)?|(?<!\w)[+-]?(?:\d{1,3}(?:[,\s]\d{2,3})+|\d+)(?:[.,]\d+)?\s*(?:USD|INR|EUR|GBP|AUD|CAD|NZD|SGD|HKD|JPY|CNY|RMB|AED|CHF|ZAR|PKR|BDT|NGN|PHP|KRW|RUB|BRL|MXN|Rs\.?|rupees?|dollars?|euros?|pounds?|yen|cents?|paise|paisa)\b|\b(?:USD|INR|EUR|GBP|AUD|CAD|NZD|SGD|HKD|JPY|CNY|RMB|AED|CHF|ZAR|PKR|BDT|NGN|PHP|KRW|RUB|BRL|MXN|Rs\.?|rupees?|dollars?|euros?|pounds?|yen|cents?|paise|paisa)\b)",
    re.IGNORECASE,
)


def get_font(weight: str, size: int):
    """Retrieve cached font or default if not found."""
    if ImageFont is None:
        raise RuntimeError("Pillow is required for font rendering.")
    key = (weight, int(size))
    if key not in _font_cache:
        font_file = FONTS.get(weight, "Inter-Regular.ttf")
        font_path = os.path.join(FONT_DIR, font_file)
        if os.path.isfile(font_path):
            try:
                _font_cache[key] = ImageFont.truetype(font_path, int(size))
            except Exception:
                _font_cache[key] = ImageFont.load_default()
        else:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def text_width(text: str, font: Any) -> float:
    """Accurately measure text width."""
    if hasattr(font, "getlength"):
        return font.getlength(text)
    if hasattr(font, "getbbox"):
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    return len(text) * 10


def text_height(text: str, font: Any) -> float:
    """Measure line height."""
    if hasattr(font, "getbbox"):
        bbox = font.getbbox(text or "Aygq")
        return bbox[3] - bbox[1]
    return 16


def line_height(font: Any) -> float:
    """Standard line step."""
    if hasattr(font, "getbbox"):
        bbox = font.getbbox("Aygq")
        return (bbox[3] - bbox[1]) * 1.3
    return 20


def wrap_text(text: str, font: Any, max_w: float) -> List[str]:
    """Wrap text to fit within max_w without clipping words."""
    words = str(text).split()
    lines: List[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip() if cur else w
        if cur and text_width(trial, font) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [""]


def fit_text(
    text: str,
    weight: str,
    max_size: int,
    min_size: int,
    max_w: float,
    max_lines: int,
) -> Tuple[List[str], Any]:
    """Find the best font size (from max_size down to min_size) that wraps within max_lines and max_w."""
    size = max_size
    while size > min_size:
        f = get_font(weight, size)
        lines = wrap_text(text, f, max_w)
        if len(lines) <= max_lines and all(text_width(l, f) <= max_w for l in lines):
            return lines, f
        size -= 2
    f = get_font(weight, min_size)
    lines = wrap_text(text, f, max_w)
    return lines, f


def relative_luminance(rgb: Tuple[int, int, int]) -> float:
    """Calculate WCAG relative luminance from 0..255 RGB values."""
    lum = []
    for c in rgb[:3]:
        v = c / 255.0
        v = v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        lum.append(v)
    return 0.2126 * lum[0] + 0.7152 * lum[1] + 0.0722 * lum[2]


def contrast_ratio(rgb1: Tuple[int, int, int], rgb2: Tuple[int, int, int]) -> float:
    """Calculate WCAG contrast ratio between two RGB colors (>= 1.0)."""
    l1 = relative_luminance(rgb1)
    l2 = relative_luminance(rgb2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def clean_text_no_brands_or_money(text: str) -> str:
    """Strip brands, money amounts, clickbait, and normalize text."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", html.unescape(text))
    # Remove currency symbols and numbers
    symbols = "".join(re.escape(c) for c in set(norm) if unicodedata.category(c) == "Sc")
    if symbols:
        norm = re.sub(r"(?:[" + symbols + r"]\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*[" + symbols + r"])", "", norm)
        norm = re.sub(r"[" + symbols + r"]", "", norm)
    norm = _MONEY_REGEX.sub("", norm)
    norm = _BRAND_WORDS.sub("", norm)
    norm = _CLICKBAIT_WORDS.sub("", norm)
    norm = re.sub(r"[\*\_\#\`\[\]\(\)]", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip(" -|,:")
    return norm


def is_free_pack(listing_content: str, folder_name: str = "") -> bool:
    """Detect if the pack is offered for free."""
    combined = f"{listing_content} {folder_name}".lower()
    patterns = [
        r"\bfree\b",
        r"\$0\b",
        r"\b0\s*(?:usd|eur|gbp|inr)\b",
        r"\bfree\s+download\b",
        r"\bfree\s+printable\b",
        r"\bfreebie\b",
        r"\bno\s+cost\b",
    ]
    for pat in patterns:
        if re.search(pat, combined):
            return True
    return False


def extract_pack_info(pack_dir: Path) -> Dict[str, Any]:
    """Read listing and image assets from upload pack without modifying anything in pack_dir."""
    pack_dir = Path(pack_dir).resolve()
    listing_file = pack_dir / "listing.txt"
    if not listing_file.is_file():
        # Fallback to listing.md if present
        listing_file = pack_dir / "listing.md"

    raw_title = pack_dir.name
    raw_desc = ""
    raw_tags: List[str] = []
    is_free = False
    listing_text = ""

    if listing_file.is_file():
        listing_text = listing_file.read_text(encoding="utf-8-sig", errors="ignore")
        is_free = is_free_pack(listing_text, pack_dir.name)

        desc_lines: List[str] = []
        is_in_desc = False
        for line in listing_text.splitlines():
            sline = line.strip()
            l_lower = sline.lower()
            if l_lower.startswith("title:"):
                raw_title = sline.split(":", 1)[1].strip()
                is_in_desc = False
            elif l_lower.startswith("tags:"):
                tag_part = sline.split(":", 1)[1].strip()
                raw_tags = [t.strip() for t in tag_part.split(",") if t.strip()]
                is_in_desc = False
            elif l_lower.startswith("description:"):
                is_in_desc = True
                part = sline.split(":", 1)[1].strip()
                if part:
                    desc_lines.append(part)
            elif is_in_desc:
                if l_lower.startswith("tags:") or l_lower.startswith("checklist:") or l_lower.startswith("files:"):
                    is_in_desc = False
                else:
                    desc_lines.append(sline)
        raw_desc = " ".join(desc_lines).strip()

    title_clean = clean_text_no_brands_or_money(raw_title)
    if not title_clean:
        title_clean = re.sub(r"[-_]+", " ", pack_dir.name).title()
        title_clean = clean_text_no_brands_or_money(title_clean)

    desc_clean = clean_text_no_brands_or_money(raw_desc)
    if not desc_clean:
        desc_clean = f"Clean, practical printable {title_clean.lower()} designed to keep you organized."

    # Look for image files inside pack_dir
    images: List[Path] = []
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        images.extend(pack_dir.glob(f"*{ext}"))
    images = sorted([img for img in images if img.is_file()])

    # Prioritize cover.png, preview*, pin*
    cover_img = None
    preview_imgs: List[Path] = []
    for img in images:
        name_l = img.name.lower()
        if "cover" in name_l:
            cover_img = img
        else:
            preview_imgs.append(img)
    if not cover_img and images:
        cover_img = images[0]

    return {
        "pack_name": pack_dir.name,
        "raw_title": title_clean,
        "description": desc_clean,
        "tags": raw_tags,
        "is_free": is_free,
        "cover_path": cover_img,
        "preview_paths": preview_imgs,
    }


def generate_ab_titles(base_title: str) -> List[str]:
    """Generate 5 distinct title variants (<= 60 chars each, no money, no brands, no clickbait).

    Rules:
    1. Benefit-first: Focus on the result / outcome
    2. Number-first: Leading numeral (e.g. 5 Simple Steps, 10 Pages, etc.)
    3. Question: Starts with a compelling question (e.g. "Looking to...?", "Need a...?")
    4. "How to": Direct practical guidance (e.g. "How to Organize with...")
    5. Short: Punchy, minimal title under 40 chars
    """
    clean = clean_text_no_brands_or_money(base_title)
    # Extract core subject
    core = re.sub(
        r"^(simple|easy|ultimate|complete|printable|minimal|daily|weekly|monthly)\s+",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip()
    if not core:
        core = clean or "Planner Template"

    # 1. Benefit-first
    benefit = f"Stay Organized With {core}"
    if len(benefit) > 60:
        benefit = f"Organize With {core}"
    if len(benefit) > 60:
        benefit = benefit[:60].rsplit(" ", 1)[0]

    # 2. Number-first
    number = f"5 Steps To Master {core}"
    if len(number) > 60:
        number = f"3 Ways To Use {core}"
    if len(number) > 60:
        number = number[:60].rsplit(" ", 1)[0]

    # 3. Question
    question = f"Need A Better {core}?"
    if len(question) > 60:
        question = f"Ready For {core}?"
    if len(question) > 60:
        question = question[:60].rsplit(" ", 1)[0]

    # 4. How to
    howto = f"How To Use A {core}"
    if len(howto) > 60:
        howto = f"How To Start {core}"
    if len(howto) > 60:
        howto = howto[:60].rsplit(" ", 1)[0]

    # 5. Short
    short = core
    if len(short) > 40:
        short = short[:40].rsplit(" ", 1)[0]
    if short.lower() == clean.lower() and len(f"Simple {short}") <= 40:
        short = f"Simple {short}"

    variants = [benefit, number, question, howto, short]

    # Post-process: guarantee uniqueness, length <= 60, no forbidden patterns
    final_titles: List[str] = []
    seen: set = set()

    fallbacks = [
        f"Easy {core}",
        f"Daily {core}",
        f"Your Guide To {core}",
        f"Start Your {core}",
        f"Simple Printable {core}",
    ]

    for t in variants + fallbacks:
        t_clean = clean_text_no_brands_or_money(t)
        if len(t_clean) > 60:
            t_clean = t_clean[:60].rsplit(" ", 1)[0]
        if not t_clean:
            continue
        lower_t = t_clean.lower()
        if lower_t not in seen:
            seen.add(lower_t)
            final_titles.append(t_clean)
        if len(final_titles) == 5:
            break

    # If still fewer than 5, synthesize guaranteed unique variants
    counter = 1
    while len(final_titles) < 5:
        cand = f"Printable {core} Vol {counter}"[:60]
        if cand.lower() not in seen:
            seen.add(cand.lower())
            final_titles.append(cand)
        counter += 1

    return final_titles[:5]


def create_fallback_page_image(title: str, size: Tuple[int, int] = (600, 800)) -> Any:
    """Create a crisp, realistic mock page image if no input image exists."""
    w, h = size
    page = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(page)

    # Top accent header
    d.rectangle([0, 0, w, 100], fill=(47, 125, 120))
    f_head = get_font("bold", 30)
    lines, f_head = fit_text(title, "bold", 30, 20, w - 60, 2)
    y = 25
    for l in lines:
        d.text((30, y), l, font=f_head, fill=(255, 255, 255))
        y += line_height(f_head)

    # Grid / lined body
    f_sub = get_font("semibold", 18)
    d.text((30, 130), "Summary & Action Items", font=f_sub, fill=(62, 85, 83))
    for row_y in range(180, h - 60, 45):
        d.line([(30, row_y), (w - 30, row_y)], fill=(225, 230, 230), width=2)
        d.rectangle([30, row_y - 20, 42, row_y - 8], outline=(150, 165, 165), width=2)

    # Outer subtle border
    d.rectangle([0, 0, w - 1, h - 1], outline=(200, 205, 205), width=1)
    return page


def load_or_create_page(path: Optional[Path], fallback_title: str, size: Tuple[int, int]) -> Any:
    """Load image from disk and resize/fit into size, or generate fallback."""
    if path and path.is_file():
        try:
            with Image.open(path) as img:
                im = img.convert("RGB")
                im.thumbnail(size, Image.Resampling.LANCZOS)
                # Pad into standard paper background if aspect ratio differs
                padded = Image.new("RGB", size, (255, 255, 255))
                offset = ((size[0] - im.width) // 2, (size[1] - im.height) // 2)
                padded.paste(im, offset)
                d = ImageDraw.Draw(padded)
                d.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(210, 215, 215), width=1)
                return padded
        except Exception:
            pass
    return create_fallback_page_image(fallback_title, size)


def draw_tilted_page(
    canvas: Any,
    page: Any,
    center: Tuple[int, int],
    angle: float,
    shadow: bool = True,
):
    """Draw tilted page with a smooth shadow."""
    page_rgba = page.convert("RGBA")
    rot = page_rgba.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
    x = int(center[0] - rot.width / 2)
    y = int(center[1] - rot.height / 2)

    if shadow:
        alpha = rot.split()[3].point(lambda a: int(a * 0.28))
        sh = Image.new("RGBA", rot.size, (15, 25, 30, 0))
        sh.putalpha(alpha)
        pad = 40
        big = Image.new("RGBA", (rot.width + 2 * pad, rot.height + 2 * pad), (0, 0, 0, 0))
        big.paste(sh, (pad, pad))
        big = big.filter(ImageFilter.GaussianBlur(15))
        canvas.paste(big, (x - pad + 10, y - pad + 16), big)

    canvas.paste(rot, (x, y), rot)


def draw_footer(draw: Any, pack_name: str, y: int = 1420, width: int = 1000, color: Tuple[int, int, int] = (62, 85, 83)):
    """Draw a clean, minimal footer with pack short name."""
    clean_name = clean_text_no_brands_or_money(pack_name.replace("-", " ").title())
    footer_text = f"Instant Printable · {clean_name}"
    f_footer = get_font("semibold", 22)
    w_text = text_width(footer_text, f_footer)
    x = max(SAFE_MARGIN, (width - w_text) / 2)
    draw.text((x, y), footer_text, font=f_footer, fill=color)


# ==========================================
# 5 LAYOUT IMPLEMENTATIONS
# ==========================================

def render_layout_a(
    title: str,
    pack_name: str,
    page_image: Any,
) -> Any:
    """Layout A: Big Title Top + Tilted Page."""
    bg_color = (248, 249, 250)
    canvas = Image.new("RGBA", (PIN_WIDTH, PIN_HEIGHT), bg_color + (255,))
    d = ImageDraw.Draw(canvas)

    # Sub-header category
    cat_text = "PRACTICAL TEMPLATE"
    f_cat = get_font("bold", 24)
    cat_color = (47, 125, 120)  # Contrast >= 4.5
    d.text((SAFE_MARGIN, 80), cat_text, font=f_cat, fill=cat_color)

    # Big Title
    ink_color = (20, 35, 40)
    lines, f_title = fit_text(title, "bold", 64, 38, PIN_WIDTH - 2 * SAFE_MARGIN, 3)
    y = 125
    for l in lines:
        d.text((SAFE_MARGIN, y), l, font=f_title, fill=ink_color)
        y += line_height(f_title)

    # Divider bar
    d.rectangle([SAFE_MARGIN, y + 10, SAFE_MARGIN + 120, y + 16], fill=cat_color)

    # Page placement
    center = (PIN_WIDTH // 2, 920)
    draw_tilted_page(canvas, page_image, center, angle=-3)

    # Footer
    d = ImageDraw.Draw(canvas)
    draw_footer(d, pack_name, y=1430, color=ink_color)

    return canvas.convert("RGB")


def render_layout_b(
    title: str,
    pack_name: str,
    page1: Any,
    page2: Any,
) -> Any:
    """Layout B: Split Screen Two Pages."""
    bg_top = (240, 244, 245)
    bg_bottom = (255, 255, 255)
    canvas = Image.new("RGBA", (PIN_WIDTH, PIN_HEIGHT), bg_top + (255,))
    d = ImageDraw.Draw(canvas)

    # Bottom split rectangle
    d.rectangle([0, 480, PIN_WIDTH, PIN_HEIGHT], fill=bg_bottom)

    # Title in top half
    accent_color = (35, 105, 100)
    ink_color = (18, 30, 35)

    tagline = "DAILY PRODUCTIVITY"
    f_tag = get_font("bold", 22)
    w_tag = text_width(tagline, f_tag)
    d.text(((PIN_WIDTH - w_tag) / 2, 75), tagline, font=f_tag, fill=accent_color)

    lines, f_title = fit_text(title, "bold", 60, 36, PIN_WIDTH - 2 * SAFE_MARGIN, 3)
    y = 120
    for l in lines:
        w_l = text_width(l, f_title)
        d.text(((PIN_WIDTH - w_l) / 2, y), l, font=f_title, fill=ink_color)
        y += line_height(f_title)

    # Two pages side-by-side / overlapping
    p1 = page1.resize((480, 680), Image.Resampling.LANCZOS)
    p2 = page2.resize((480, 680), Image.Resampling.LANCZOS)

    draw_tilted_page(canvas, p1, (PIN_WIDTH // 2 - 160, 930), angle=-4)
    draw_tilted_page(canvas, p2, (PIN_WIDTH // 2 + 160, 950), angle=3)

    # Footer
    d = ImageDraw.Draw(canvas)
    draw_footer(d, pack_name, y=1430, color=ink_color)

    return canvas.convert("RGB")


def render_layout_c(
    title: str,
    pack_name: str,
    page_image: Any,
    benefits: Optional[List[str]] = None,
) -> Any:
    """Layout C: Checklist Style (3 Benefit Ticks)."""
    bg_color = (252, 252, 253)
    canvas = Image.new("RGBA", (PIN_WIDTH, PIN_HEIGHT), bg_color + (255,))
    d = ImageDraw.Draw(canvas)

    ink_color = (15, 28, 32)
    accent_color = (38, 110, 105)

    # Header
    lines, f_title = fit_text(title, "bold", 58, 36, PIN_WIDTH - 2 * SAFE_MARGIN, 3)
    y = 80
    for l in lines:
        d.text((SAFE_MARGIN, y), l, font=f_title, fill=ink_color)
        y += line_height(f_title)

    y += 20
    # 3 Benefit Ticks
    ticks = benefits or [
        "Instant easy-to-print format",
        "Clear step-by-step layout",
        "Reusable undated template",
    ]

    f_tick = get_font("semibold", 28)
    for tick in ticks[:3]:
        # Draw check circle
        d.ellipse([SAFE_MARGIN, y + 2, SAFE_MARGIN + 32, y + 34], fill=accent_color)
        # White checkmark lines
        d.line([(SAFE_MARGIN + 8, y + 17), (SAFE_MARGIN + 14, y + 24)], fill=(255, 255, 255), width=4)
        d.line([(SAFE_MARGIN + 14, y + 24), (SAFE_MARGIN + 24, y + 12)], fill=(255, 255, 255), width=4)

        d.text((SAFE_MARGIN + 48, y), tick, font=f_tick, fill=ink_color)
        y += 55

    # Center-bottom page preview
    p_img = page_image.resize((620, 800), Image.Resampling.LANCZOS)
    draw_tilted_page(canvas, p_img, (PIN_WIDTH // 2, 1040), angle=0)

    d = ImageDraw.Draw(canvas)
    draw_footer(d, pack_name, y=1430, color=ink_color)

    return canvas.convert("RGB")


def render_layout_d(
    title: str,
    pack_name: str,
    page_image: Any,
) -> Any:
    """Layout D: 'Free Printable' Style Badge."""
    bg_color = (245, 248, 248)
    canvas = Image.new("RGBA", (PIN_WIDTH, PIN_HEIGHT), bg_color + (255,))
    d = ImageDraw.Draw(canvas)

    ink_color = (18, 32, 36)
    badge_bg = (215, 60, 75)
    badge_text_color = (255, 255, 255)

    # Large prominent badge at top
    badge_rect = [SAFE_MARGIN, 70, SAFE_MARGIN + 320, 135]
    d.rounded_rectangle(badge_rect, radius=12, fill=badge_bg)
    f_badge = get_font("bold", 28)
    d.text((SAFE_MARGIN + 24, 85), "FREE PRINTABLE", font=f_badge, fill=badge_text_color)

    # Main Title
    lines, f_title = fit_text(title, "bold", 62, 38, PIN_WIDTH - 2 * SAFE_MARGIN, 3)
    y = 160
    for l in lines:
        d.text((SAFE_MARGIN, y), l, font=f_title, fill=ink_color)
        y += line_height(f_title)

    # Subline
    f_sub = get_font("semibold", 26)
    d.text((SAFE_MARGIN, y + 10), "Download now · Print at home", font=f_sub, fill=(50, 115, 110))

    # Center page preview with slight angle
    p_img = page_image.resize((640, 840), Image.Resampling.LANCZOS)
    draw_tilted_page(canvas, p_img, (PIN_WIDTH // 2, 950), angle=2)

    d = ImageDraw.Draw(canvas)
    draw_footer(d, pack_name, y=1430, color=ink_color)

    return canvas.convert("RGB")


def render_layout_e(
    title: str,
    pack_name: str,
    page_image: Any,
) -> Any:
    """Layout E: Minimal Photo-Frame."""
    bg_color = (255, 255, 255)
    canvas = Image.new("RGBA", (PIN_WIDTH, PIN_HEIGHT), bg_color + (255,))
    d = ImageDraw.Draw(canvas)

    ink_color = (25, 30, 35)
    border_color = (220, 225, 228)

    # Outer elegant frame (safe margin)
    frame_rect = [40, 40, PIN_WIDTH - 40, PIN_HEIGHT - 40]
    d.rectangle(frame_rect, outline=border_color, width=2)

    # Centered Title
    tagline = "SIMPLE & MINIMALIST"
    f_tag = get_font("bold", 20)
    w_tag = text_width(tagline, f_tag)
    d.text(((PIN_WIDTH - w_tag) / 2, 85), tagline, font=f_tag, fill=(70, 120, 115))

    lines, f_title = fit_text(title, "bold", 54, 34, PIN_WIDTH - 2 * SAFE_MARGIN, 3)
    y = 125
    for l in lines:
        w_l = text_width(l, f_title)
        d.text(((PIN_WIDTH - w_l) / 2, y), l, font=f_title, fill=ink_color)
        y += line_height(f_title)

    # Framed central image
    p_img = page_image.resize((660, 880), Image.Resampling.LANCZOS)
    center = (PIN_WIDTH // 2, 910)
    draw_tilted_page(canvas, p_img, center, angle=0, shadow=True)

    d = ImageDraw.Draw(canvas)
    draw_footer(d, pack_name, y=1410, color=ink_color)

    return canvas.convert("RGB")


# ==========================================
# PACK GENERATION ORCHESTRATION
# ==========================================

def generate_pins_for_pack(
    pack_dir: Path,
    out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Generate 5 distinct 1000x1500 PNG pins, pins.json, and ab_titles.txt for a pack.

    Outputs are written outside the pack to exported_content/marketing/<pack>/pins/
    (or custom out_dir). The pack_dir is strictly read-only and never modified.
    """
    pack_dir = Path(pack_dir).resolve()
    if not pack_dir.is_dir():
        raise ValueError(f"Pack directory not found: {pack_dir}")

    info = extract_pack_info(pack_dir)
    pack_name = info["pack_name"]
    is_free = info["is_free"]
    base_title = info["raw_title"]

    # 5 A/B Titles
    titles = generate_ab_titles(base_title)

    # Determine destination directory
    if out_dir is None:
        target_pins_dir = Path("exported_content") / "marketing" / pack_name / "pins"
    else:
        target_pins_dir = Path(out_dir).resolve() / "marketing" / pack_name / "pins"

    target_pins_dir.mkdir(parents=True, exist_ok=True)

    # Prepare page images
    cover_page = load_or_create_page(info["cover_path"], base_title, (700, 950))
    preview_path = info["preview_paths"][0] if info["preview_paths"] else None
    second_page = load_or_create_page(preview_path, f"{base_title} Overview", (700, 950))

    # Layout selection: A, B, C, (D if free else E), E
    # If not free: layouts are [A, B, C, E, E] or [A, B, C, E, A-variant] -> prompt states:
    # "D 'free printable' style badge (only if the listing says it is free — else skip D and use E)"
    # When free: [A, B, C, D, E]
    # When not free: [A, B, C, E, E] (two E variants or layout E replaces D)
    layouts_order: List[str] = ["A", "B", "C"]
    if is_free:
        layouts_order.extend(["D", "E"])
    else:
        layouts_order.extend(["E", "E"])

    # Atomic writing: write to a temporary directory first, then rename or move files
    import tempfile
    temp_dir = Path(tempfile.mkdtemp(prefix=f".pins-{pack_name}-", dir=str(target_pins_dir.parent)))

    try:
        pins_meta = []

        for idx, (layout, title) in enumerate(zip(layouts_order, titles), 1):
            file_name = f"pin_{idx}_{layout.lower()}.png"
            dest_file = temp_dir / file_name

            if layout == "A":
                img = render_layout_a(title, pack_name, cover_page)
            elif layout == "B":
                img = render_layout_b(title, pack_name, cover_page, second_page)
            elif layout == "C":
                img = render_layout_c(title, pack_name, cover_page)
            elif layout == "D":
                img = render_layout_d(title, pack_name, cover_page)
            else:  # Layout E
                img = render_layout_e(title, pack_name, cover_page)

            # Save PNG optimized (must be < 1MB)
            img.save(dest_file, format="PNG", optimize=True)

            # Ensure file size < 1MB
            if dest_file.stat().st_size > 1024 * 1024:
                # Quantize or compress if exceeded
                quantized = img.quantize(colors=256)
                quantized.save(dest_file, format="PNG", optimize=True)

            desc = f"{title}. {info['description']}"
            if len(desc) > 500:
                desc = desc[:497].rsplit(" ", 1)[0] + "..."

            alt_text = f"Pinterest pin for {title} - {pack_name.replace('-', ' ')} printable template"
            if len(alt_text) > 500:
                alt_text = alt_text[:500]

            pins_meta.append({
                "file": file_name,
                "layout": layout,
                "title": title,
                "description": desc,
                "alt": alt_text,
            })

        # Write pins.json
        (temp_dir / "pins.json").write_text(json.dumps(pins_meta, indent=2), encoding="utf-8")

        # Write ab_titles.txt
        ab_lines = [
            f"# A/B Title Ideas for {pack_name}",
            "# Review and test manually. Nothing auto-posts.",
            "",
        ]
        for i, t in enumerate(titles, 1):
            ab_lines.append(f"{i}. {t}")
        ab_lines.append("")
        (temp_dir / "ab_titles.txt").write_text("\n".join(ab_lines), encoding="utf-8")

        # Move files to target_pins_dir atomically
        for src in temp_dir.iterdir():
            target = target_pins_dir / src.name
            if target.exists():
                target.unlink()
            src.rename(target)

    finally:
        # Cleanup temp dir if it still exists
        if temp_dir.exists():
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "pack_name": pack_name,
        "pins_dir": str(target_pins_dir),
        "titles": titles,
        "meta": pins_meta,
    }


# ==============================================================================
# BINNY-W5A EXTENDED SOCIAL POST & PIN VARIANTS GENERATOR
# ==============================================================================

SQUARE_WIDTH = 1080
SQUARE_HEIGHT = 1080
STORY_WIDTH = 1080
STORY_HEIGHT = 1920

THEMES = {
    "calm sage": {
        "name": "Calm Sage",
        "bg": (246, 248, 246),
        "card": (255, 255, 255),
        "ink": (24, 38, 32),
        "primary": (42, 102, 79),
        "accent": (180, 75, 35),
        "muted": (88, 108, 98),
        "tag": "ORGANIZATION & FOCUS",
    },
    "midnight focus": {
        "name": "Midnight Focus",
        "bg": (245, 247, 250),
        "card": (255, 255, 255),
        "ink": (15, 23, 42),
        "primary": (30, 58, 138),
        "accent": (180, 83, 9),
        "muted": (71, 85, 105),
        "tag": "DAILY CLARITY",
    },
    "terracotta": {
        "name": "Terracotta",
        "bg": (253, 248, 246),
        "card": (255, 255, 255),
        "ink": (44, 24, 16),
        "primary": (154, 52, 18),
        "accent": (180, 83, 9),
        "muted": (120, 80, 68),
        "tag": "WARM & MINIMAL",
    },
    "ocean": {
        "name": "Ocean",
        "bg": (244, 249, 251),
        "card": (255, 255, 255),
        "ink": (12, 33, 48),
        "primary": (14, 116, 144),
        "accent": (190, 24, 60),
        "muted": (70, 95, 110),
        "tag": "CLEAN HABITS",
    },
    "minimal mono": {
        "name": "Minimal Mono",
        "bg": (250, 250, 250),
        "card": (255, 255, 255),
        "ink": (17, 24, 39),
        "primary": (55, 65, 81),
        "accent": (185, 28, 28),
        "muted": (107, 114, 128),
        "tag": "PRACTICAL TEMPLATE",
    },
    "festive marigold": {
        "name": "Festive Marigold",
        "bg": (254, 250, 242),
        "card": (255, 255, 255),
        "ink": (45, 28, 10),
        "primary": (180, 83, 9),
        "accent": (170, 20, 80),
        "muted": (115, 85, 55),
        "tag": "SPECIAL EDITION",
    },
}


def resolve_theme(theme_name: Optional[str] = None, pack_name: str = "") -> Dict[str, Any]:
    """Resolve theme colors, falling back to a harmonious theme based on name."""
    try:
        import pdf_themes  # type: ignore

        if hasattr(pdf_themes, "THEMES"):
            t_cand = str(theme_name or "").lower().strip()
            for k, val in pdf_themes.THEMES.items():
                if k.lower() == t_cand or getattr(val, "name", "").lower() == t_cand:
                    return {
                        "name": getattr(val, "name", k),
                        "bg": getattr(val, "bg", (246, 248, 246)),
                        "card": (255, 255, 255),
                        "ink": getattr(val, "ink", (24, 38, 32)),
                        "primary": getattr(val, "primary", (42, 102, 79)),
                        "accent": getattr(val, "accent", (180, 75, 35)),
                        "muted": getattr(val, "muted", (88, 108, 98)),
                        "tag": "ORGANIZATION & FOCUS",
                    }
    except Exception:
        pass

    key = str(theme_name or "").lower().strip()
    if key in THEMES:
        return THEMES[key]

    for k, th in THEMES.items():
        if k in key:
            return th

    p_lower = pack_name.lower()
    if "adhd" in p_lower or "focus" in p_lower:
        return THEMES["calm sage"]
    elif "2027" in p_lower or "budget" in p_lower or "finance" in p_lower:
        return THEMES["midnight focus"]
    elif "wedding" in p_lower or "event" in p_lower or "warm" in p_lower:
        return THEMES["terracotta"]
    elif "fitness" in p_lower or "habit" in p_lower or "water" in p_lower:
        return THEMES["ocean"]
    elif "diwali" in p_lower or "festival" in p_lower:
        return THEMES["festive marigold"]

    return THEMES["calm sage"]


def render_hero_mockup(
    title: str,
    pack_name: str,
    pages: List[Any],
    dimensions: Tuple[int, int] = (PIN_WIDTH, PIN_HEIGHT),
    theme: Optional[Dict[str, Any]] = None,
) -> Any:
    """Hero mockup: Big prominent angled hero page with clear title band and badges."""
    w, h = dimensions
    th = theme or resolve_theme(pack_name=pack_name)
    canvas = Image.new("RGBA", (w, h), th["bg"] + (255,))
    d = ImageDraw.Draw(canvas)

    f_tag = get_font("bold", max(18, int(w * 0.024)))
    d.text((SAFE_MARGIN, int(h * 0.05)), th["tag"], font=f_tag, fill=th["primary"])

    max_w = w - 2 * SAFE_MARGIN
    max_title_size = int(w * 0.065)
    min_title_size = int(w * 0.038)
    lines, f_title = fit_text(title, "bold", max_title_size, min_title_size, max_w, 3)
    y = int(h * 0.08)
    for l in lines:
        d.text((SAFE_MARGIN, y), l, font=f_title, fill=th["ink"])
        y += line_height(f_title)

    d.rectangle([SAFE_MARGIN, int(y + 10), SAFE_MARGIN + 100, int(y + 16)], fill=th["accent"])

    hero_w = int(w * 0.65)
    hero_h = int(hero_w * 1.35)
    p_hero = pages[0].resize((hero_w, hero_h), Image.Resampling.LANCZOS)

    center_y = int(h * 0.62)
    center_x = w // 2
    draw_tilted_page(canvas, p_hero, (center_x, center_y), angle=-2, shadow=True)

    d = ImageDraw.Draw(canvas)
    draw_footer(d, pack_name, y=int(h - 70), width=w, color=th["ink"])
    return canvas.convert("RGB")


def render_three_page_fan(
    title: str,
    pack_name: str,
    pages: List[Any],
    dimensions: Tuple[int, int] = (PIN_WIDTH, PIN_HEIGHT),
    theme: Optional[Dict[str, Any]] = None,
) -> Any:
    """3-page fan: Elegant fanned display of 3 pages with soft drop shadows."""
    w, h = dimensions
    th = theme or resolve_theme(pack_name=pack_name)
    canvas = Image.new("RGBA", (w, h), th["bg"] + (255,))
    d = ImageDraw.Draw(canvas)

    tagline = "ALL-IN-ONE PACK"
    f_tag = get_font("bold", max(18, int(w * 0.022)))
    w_tag = text_width(tagline, f_tag)
    d.text(((w - w_tag) / 2, int(h * 0.05)), tagline, font=f_tag, fill=th["primary"])

    max_w = w - 2 * SAFE_MARGIN
    max_title_size = int(w * 0.06)
    min_title_size = int(w * 0.036)
    lines, f_title = fit_text(title, "bold", max_title_size, min_title_size, max_w, 3)
    y = int(h * 0.085)
    for l in lines:
        w_l = text_width(l, f_title)
        d.text(((w - w_l) / 2, y), l, font=f_title, fill=th["ink"])
        y += line_height(f_title)

    p_w = int(w * 0.46)
    p_h = int(p_w * 1.35)

    p0 = pages[0].resize((p_w, p_h), Image.Resampling.LANCZOS)
    p1 = (pages[1] if len(pages) > 1 else pages[0]).resize((p_w, p_h), Image.Resampling.LANCZOS)
    p2 = (pages[2] if len(pages) > 2 else pages[0]).resize((p_w, p_h), Image.Resampling.LANCZOS)

    cy = int(h * 0.63)
    draw_tilted_page(canvas, p1, (int(w * 0.32), cy), angle=-9, shadow=True)
    draw_tilted_page(canvas, p2, (int(w * 0.68), cy), angle=9, shadow=True)
    draw_tilted_page(canvas, p0, (w // 2, cy - 10), angle=0, shadow=True)

    d = ImageDraw.Draw(canvas)
    draw_footer(d, pack_name, y=int(h - 70), width=w, color=th["ink"])
    return canvas.convert("RGB")


def render_checklist_teaser(
    title: str,
    pack_name: str,
    pages: List[Any],
    dimensions: Tuple[int, int] = (PIN_WIDTH, PIN_HEIGHT),
    theme: Optional[Dict[str, Any]] = None,
    benefits: Optional[List[str]] = None,
) -> Any:
    """Checklist teaser: 3 benefit ticks + page preview."""
    w, h = dimensions
    th = theme or resolve_theme(pack_name=pack_name)
    canvas = Image.new("RGBA", (w, h), th["bg"] + (255,))
    d = ImageDraw.Draw(canvas)

    max_w = w - 2 * SAFE_MARGIN
    lines, f_title = fit_text(title, "bold", int(w * 0.058), int(w * 0.036), max_w, 3)
    y = int(h * 0.055)
    for l in lines:
        d.text((SAFE_MARGIN, y), l, font=f_title, fill=th["ink"])
        y += line_height(f_title)

    y += 15
    ticks = benefits or [
        "Instant easy-to-print format",
        "Clear step-by-step layout",
        "Reusable undated template",
    ]

    f_tick = get_font("semibold", max(18, int(w * 0.028)))
    for tick in ticks[:3]:
        d.ellipse([SAFE_MARGIN, int(y + 2), SAFE_MARGIN + 32, int(y + 34)], fill=th["primary"])
        d.line([(SAFE_MARGIN + 9, int(y + 17)), (SAFE_MARGIN + 15, int(y + 23))], fill=(255, 255, 255), width=3)
        d.line([(SAFE_MARGIN + 15, int(y + 23)), (SAFE_MARGIN + 24, int(y + 13))], fill=(255, 255, 255), width=3)

        d.text((SAFE_MARGIN + 46, int(y + 3)), tick, font=f_tick, fill=th["ink"])
        y += max(45, int(h * 0.04))

    p_w = int(w * 0.62)
    p_h = int(p_w * 1.35)
    p_img = pages[0].resize((p_w, p_h), Image.Resampling.LANCZOS)
    draw_tilted_page(canvas, p_img, (w // 2, int(h * 0.70)), angle=0, shadow=True)

    d = ImageDraw.Draw(canvas)
    draw_footer(d, pack_name, y=int(h - 70), width=w, color=th["ink"])
    return canvas.convert("RGB")


def render_before_after(
    title: str,
    pack_name: str,
    pages: List[Any],
    dimensions: Tuple[int, int] = (PIN_WIDTH, PIN_HEIGHT),
    theme: Optional[Dict[str, Any]] = None,
    before_text: str = "Scattered Notes & Overwhelm",
    after_text: str = "Calm Structure & Clear Focus",
) -> Any:
    """Before / after: Side-by-side comparison card contrasting chaos vs clarity."""
    w, h = dimensions
    th = theme or resolve_theme(pack_name=pack_name)
    canvas = Image.new("RGBA", (w, h), th["bg"] + (255,))
    d = ImageDraw.Draw(canvas)

    tagline = "THE DIFFERENCE"
    f_tag = get_font("bold", max(18, int(w * 0.022)))
    w_tag = text_width(tagline, f_tag)
    d.text(((w - w_tag) / 2, int(h * 0.05)), tagline, font=f_tag, fill=th["primary"])

    max_w = w - 2 * SAFE_MARGIN
    lines, f_title = fit_text(title, "bold", int(w * 0.056), int(w * 0.035), max_w, 2)
    y = int(h * 0.085)
    for l in lines:
        w_l = text_width(l, f_title)
        d.text(((w - w_l) / 2, y), l, font=f_title, fill=th["ink"])
        y += line_height(f_title)

    card_w = int((w - 3 * SAFE_MARGIN) // 2)
    card_h = int(h * 0.64)
    card_y = int(h * 0.22)

    x_before = int(SAFE_MARGIN)
    d.rounded_rectangle([x_before, card_y, x_before + card_w, card_y + card_h], radius=16, fill=(238, 240, 242))
    f_badge = get_font("bold", max(16, int(w * 0.022)))
    d.rounded_rectangle([x_before + 20, card_y + 20, x_before + 120, card_y + 55], radius=8, fill=(100, 115, 125))
    d.text((x_before + 32, card_y + 26), "BEFORE", font=f_badge, fill=(255, 255, 255))

    f_card_desc = get_font("semibold", max(16, int(w * 0.023)))
    b_lines = wrap_text(before_text, f_card_desc, card_w - 40)
    by = card_y + 75
    for bl in b_lines:
        d.text((x_before + 20, by), bl, font=f_card_desc, fill=(70, 85, 95))
        by += int(line_height(f_card_desc))

    for ly in range(int(by + 20), int(card_y + card_h - 40), 35):
        d.line([(x_before + 20, ly), (x_before + card_w - 20, ly)], fill=(210, 215, 220), width=2)

    x_after = int(x_before + card_w + SAFE_MARGIN)
    d.rounded_rectangle([x_after, card_y, x_after + card_w, card_y + card_h], radius=16, fill=(255, 255, 255), outline=th["primary"], width=2)
    d.rounded_rectangle([x_after + 20, card_y + 20, x_after + 120, card_y + 55], radius=8, fill=th["primary"])
    d.text((x_after + 38, card_y + 26), "AFTER", font=f_badge, fill=(255, 255, 255))

    a_lines = wrap_text(after_text, f_card_desc, card_w - 40)
    ay = card_y + 75
    for al in a_lines:
        d.text((x_after + 20, ay), al, font=f_card_desc, fill=th["ink"])
        ay += int(line_height(f_card_desc))

    mini_w = card_w - 40
    mini_h = int(mini_w * 1.35)
    p_mini = pages[0].resize((mini_w, mini_h), Image.Resampling.LANCZOS)
    canvas.paste(p_mini, (x_after + 20, int(card_y + card_h - mini_h - 25)))

    d = ImageDraw.Draw(canvas)
    draw_footer(d, pack_name, y=int(h - 70), width=w, color=th["ink"])
    return canvas.convert("RGB")


def render_quote_card(
    title: str,
    pack_name: str,
    pages: List[Any],
    dimensions: Tuple[int, int] = (PIN_WIDTH, PIN_HEIGHT),
    theme: Optional[Dict[str, Any]] = None,
    quote_text: str = "Small daily habits create remarkable long-term clarity.",
    quote_author: str = "Mindful Focus",
) -> Any:
    """Quote card: Inspiring mindset quote with elegant typography and subtle mockup."""
    w, h = dimensions
    th = theme or resolve_theme(pack_name=pack_name)
    canvas = Image.new("RGBA", (w, h), th["bg"] + (255,))
    d = ImageDraw.Draw(canvas)

    f_mark = get_font("bold", int(w * 0.12))
    d.text((SAFE_MARGIN, int(h * 0.05)), "“", font=f_mark, fill=th["primary"])

    f_quote = get_font("bold", max(24, int(w * 0.046)))
    q_lines = wrap_text(f'"{quote_text}"', f_quote, w - 2 * SAFE_MARGIN)
    qy = int(h * 0.16)
    for ql in q_lines:
        d.text((SAFE_MARGIN, qy), ql, font=f_quote, fill=th["ink"])
        qy += line_height(f_quote)

    f_author = get_font("semibold", max(18, int(w * 0.026)))
    d.text((SAFE_MARGIN, int(qy + 15)), f"— {quote_author}", font=f_author, fill=th["accent"])

    p_w = int(w * 0.64)
    p_h = int(p_w * 1.35)
    p_img = pages[0].resize((p_w, p_h), Image.Resampling.LANCZOS)
    draw_tilted_page(canvas, p_img, (w // 2, int(h * 0.72)), angle=-1, shadow=True)

    d = ImageDraw.Draw(canvas)
    draw_footer(d, pack_name, y=int(h - 70), width=w, color=th["ink"])
    return canvas.convert("RGB")


def render_whats_inside_grid(
    title: str,
    pack_name: str,
    pages: List[Any],
    dimensions: Tuple[int, int] = (PIN_WIDTH, PIN_HEIGHT),
    theme: Optional[Dict[str, Any]] = None,
    grid_items: Optional[List[str]] = None,
) -> Any:
    """What's inside grid: 2x2 grid of items/pages."""
    w, h = dimensions
    th = theme or resolve_theme(pack_name=pack_name)
    canvas = Image.new("RGBA", (w, h), th["bg"] + (255,))
    d = ImageDraw.Draw(canvas)

    tagline = "WHAT'S INCLUDED"
    f_tag = get_font("bold", max(18, int(w * 0.022)))
    w_tag = text_width(tagline, f_tag)
    d.text(((w - w_tag) / 2, int(h * 0.05)), tagline, font=f_tag, fill=th["primary"])

    max_w = w - 2 * SAFE_MARGIN
    lines, f_title = fit_text(title, "bold", int(w * 0.056), int(w * 0.035), max_w, 2)
    y = int(h * 0.085)
    for l in lines:
        w_l = text_width(l, f_title)
        d.text(((w - w_l) / 2, y), l, font=f_title, fill=th["ink"])
        y += line_height(f_title)

    items = grid_items or [
        "Daily Routine Tracker",
        "Weekly Planning Grid",
        "Priority Action Matrix",
        "Reflective Habit Log",
    ]

    cols = 2
    rows = 2
    grid_top = int(h * 0.22)
    col_w = int((w - 3 * SAFE_MARGIN) // cols)
    row_h = int((h * 0.65) // rows)

    f_num = get_font("bold", max(18, int(w * 0.026)))
    f_item = get_font("semibold", max(16, int(w * 0.024)))

    for idx, item in enumerate(items[:4]):
        r = idx // cols
        c = idx % cols
        bx = SAFE_MARGIN + c * (col_w + SAFE_MARGIN)
        by = grid_top + r * row_h

        d.rounded_rectangle([bx, by, bx + col_w, by + row_h - 20], radius=14, fill=(255, 255, 255), outline=(225, 230, 232), width=1)
        d.ellipse([bx + 16, by + 16, bx + 50, by + 50], fill=th["primary"])
        d.text((bx + 26, by + 21), str(idx + 1), font=f_num, fill=(255, 255, 255))

        i_lines = wrap_text(item, f_item, col_w - 70)
        iy = by + 20
        for il in i_lines:
            d.text((bx + 62, iy), il, font=f_item, fill=th["ink"])
            iy += line_height(f_item)

        page_idx = idx % len(pages)
        p_thumb = pages[page_idx].resize((col_w - 32, max(40, row_h - 90)), Image.Resampling.LANCZOS)
        canvas.paste(p_thumb, (bx + 16, by + 65))

    d = ImageDraw.Draw(canvas)
    draw_footer(d, pack_name, y=int(h - 70), width=w, color=th["ink"])
    return canvas.convert("RGB")


def generate_image_captions_and_alt(
    pack_name: str,
    title: str,
    layout: str,
    aspect: str,
    desc: str,
) -> Tuple[str, str, str]:
    """Generate Alt Text, English Caption, and Tenglish Caption.

    Tenglish: Telugu sentence words + common English nouns (Pack, Pin, Post, Store, Settings...).
    No money amounts, no brands.
    """
    clean_title = clean_text_no_brands_or_money(title)
    clean_pname = clean_text_no_brands_or_money(pack_name.replace("-", " ").title())
    alt_text = f"{clean_title} - {clean_pname} printable template {layout} {aspect} image"
    if len(alt_text) > 400:
        alt_text = alt_text[:400]

    en_caption = f"Organize your day with {clean_title}. Simple printable template designed for clear daily structure. {desc[:200]}"
    en_caption = clean_text_no_brands_or_money(en_caption)

    te_templates = {
        "hero mockup": f"Mee daily routine kosam {clean_title} ready ga undi. Simple printable template download chesukoni print cheyandi.",
        "3-page fan": f"Ee {clean_pname} pack lo 3 clear printable pages unnay. Easy structure tho mee goals track cheyandi.",
        "checklist teaser": f"Clear daily checklist tho organize avvandi. Ee {clean_title} template tho prathi task complete cheyandi.",
        "before/after": f"Chaotic notes vadilesi calm structure loki marandi. Ee {clean_title} pack mee schedule ni clear ga unchutundi.",
        "quote card": f"Daily consistency tho pedda goals reach avvochu. Ee {clean_title} printable pack meeku help chestundi.",
        "what's inside grid": f"Ee {clean_pname} pack lo unna printable pages overview chudandi. Store lo file ready ga undi.",
    }
    te_caption = te_templates.get(
        layout,
        f"Mee daily planning kosam {clean_title} pack ready ga undi. Store nunchi printable files download chesukondi.",
    )
    te_caption = clean_text_no_brands_or_money(te_caption)

    return alt_text, en_caption, te_caption


def generate_social_variants(
    pack_dir: Path,
    out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Generate all BINNY-W5A image variants:

    - 6 Pin designs: 1000x1500
    - 3 Square posts: 1080x1080
    - 2 Story images: 1080x1920

    Layouts used:
    - hero mockup, 3-page fan, checklist teaser, before/after, quote card, what's inside grid
    Outputs written atomically to marketing/<pack>/pins/ and marketing/<pack>/posts/.
    """
    pack_dir = Path(pack_dir).resolve()
    if not pack_dir.is_dir():
        raise ValueError(f"Pack directory not found: {pack_dir}")

    info = extract_pack_info(pack_dir)
    pack_name = info["pack_name"]
    base_title = info["raw_title"]
    th = resolve_theme(info.get("theme"), pack_name=pack_name)

    titles = generate_ab_titles(base_title)
    while len(titles) < 6:
        titles.append(f"{base_title} Template {len(titles) + 1}")

    if out_dir is None:
        target_pins_dir = Path("exported_content") / "marketing" / pack_name / "pins"
        target_posts_dir = Path("exported_content") / "marketing" / pack_name / "posts"
    else:
        target_pins_dir = Path(out_dir).resolve() / "marketing" / pack_name / "pins"
        target_posts_dir = Path(out_dir).resolve() / "marketing" / pack_name / "posts"

    target_pins_dir.mkdir(parents=True, exist_ok=True)
    target_posts_dir.mkdir(parents=True, exist_ok=True)

    page1 = load_or_create_page(info["cover_path"], base_title, (700, 950))
    preview1 = info["preview_paths"][0] if info["preview_paths"] else None
    page2 = load_or_create_page(preview1, f"{base_title} Checklist", (700, 950))
    preview2 = info["preview_paths"][1] if len(info["preview_paths"]) > 1 else None
    page3 = load_or_create_page(preview2, f"{base_title} Grid", (700, 950))
    pages = [page1, page2, page3]

    temp_parent = target_pins_dir.parent
    temp_dir = Path(tempfile.mkdtemp(prefix=f".w5a-{pack_name}-", dir=str(temp_parent)))

    try:
        pins_meta: List[Dict[str, Any]] = []
        posts_meta: List[Dict[str, Any]] = []
        all_meta: List[Dict[str, Any]] = []

        # 6 Pin designs (1000x1500)
        pin_specs = [
            ("variant_pin_1_hero.png", "hero mockup", titles[0]),
            ("variant_pin_2_fan.png", "3-page fan", titles[1]),
            ("variant_pin_3_checklist.png", "checklist teaser", titles[2]),
            ("variant_pin_4_before_after.png", "before/after", titles[3]),
            ("variant_pin_5_quote.png", "quote card", titles[4]),
            ("variant_pin_6_grid.png", "what's inside grid", titles[5]),
        ]
        for fname, layout, title in pin_specs:
            dest = temp_dir / fname
            if layout == "hero mockup":
                img = render_hero_mockup(title, pack_name, pages, (PIN_WIDTH, PIN_HEIGHT), th)
            elif layout == "3-page fan":
                img = render_three_page_fan(title, pack_name, pages, (PIN_WIDTH, PIN_HEIGHT), th)
            elif layout == "checklist teaser":
                img = render_checklist_teaser(title, pack_name, pages, (PIN_WIDTH, PIN_HEIGHT), th)
            elif layout == "before/after":
                img = render_before_after(title, pack_name, pages, (PIN_WIDTH, PIN_HEIGHT), th)
            elif layout == "quote card":
                img = render_quote_card(title, pack_name, pages, (PIN_WIDTH, PIN_HEIGHT), th)
            else:
                img = render_whats_inside_grid(title, pack_name, pages, (PIN_WIDTH, PIN_HEIGHT), th)

            img.save(dest, format="PNG", optimize=True)
            if dest.stat().st_size > 1024 * 1024:
                q = img.quantize(colors=256)
                q.save(dest, format="PNG", optimize=True)

            alt, en_cap, te_cap = generate_image_captions_and_alt(
                pack_name, title, layout, "pin", info["description"]
            )
            item = {
                "file": fname,
                "layout": layout,
                "title": title,
                "aspect": "pin",
                "width": PIN_WIDTH,
                "height": PIN_HEIGHT,
                "alt": alt,
                "caption_en": en_cap,
                "caption_te": te_cap,
                "description": en_cap,
            }
            pins_meta.append(item)
            all_meta.append(item)

        # 3 Square posts (1080x1080)
        square_specs = [
            ("post_1_hero.png", "hero mockup", titles[0]),
            ("post_2_checklist.png", "checklist teaser", titles[2]),
            ("post_3_quote.png", "quote card", titles[4]),
        ]
        for fname, layout, title in square_specs:
            dest = temp_dir / fname
            if layout == "hero mockup":
                img = render_hero_mockup(title, pack_name, pages, (SQUARE_WIDTH, SQUARE_HEIGHT), th)
            elif layout == "checklist teaser":
                img = render_checklist_teaser(title, pack_name, pages, (SQUARE_WIDTH, SQUARE_HEIGHT), th)
            else:
                img = render_quote_card(title, pack_name, pages, (SQUARE_WIDTH, SQUARE_HEIGHT), th)

            img.save(dest, format="PNG", optimize=True)
            if dest.stat().st_size > 1024 * 1024:
                q = img.quantize(colors=256)
                q.save(dest, format="PNG", optimize=True)

            alt, en_cap, te_cap = generate_image_captions_and_alt(
                pack_name, title, layout, "square", info["description"]
            )
            item = {
                "file": fname,
                "layout": layout,
                "title": title,
                "aspect": "square",
                "width": SQUARE_WIDTH,
                "height": SQUARE_HEIGHT,
                "alt": alt,
                "caption_en": en_cap,
                "caption_te": te_cap,
                "description": en_cap,
            }
            posts_meta.append(item)
            all_meta.append(item)

        # 2 Story images (1080x1920)
        story_specs = [
            ("story_1_fan.png", "3-page fan", titles[1]),
            ("story_2_before_after.png", "before/after", titles[3]),
        ]
        for fname, layout, title in story_specs:
            dest = temp_dir / fname
            if layout == "3-page fan":
                img = render_three_page_fan(title, pack_name, pages, (STORY_WIDTH, STORY_HEIGHT), th)
            else:
                img = render_before_after(title, pack_name, pages, (STORY_WIDTH, STORY_HEIGHT), th)

            img.save(dest, format="PNG", optimize=True)
            if dest.stat().st_size > 1024 * 1024:
                q = img.quantize(colors=256)
                q.save(dest, format="PNG", optimize=True)

            alt, en_cap, te_cap = generate_image_captions_and_alt(
                pack_name, title, layout, "story", info["description"]
            )
            item = {
                "file": fname,
                "layout": layout,
                "title": title,
                "aspect": "story",
                "width": STORY_WIDTH,
                "height": STORY_HEIGHT,
                "alt": alt,
                "caption_en": en_cap,
                "caption_te": te_cap,
                "description": en_cap,
            }
            posts_meta.append(item)
            all_meta.append(item)

        (temp_dir / "variants.json").write_text(json.dumps(pins_meta, indent=2), encoding="utf-8")
        (temp_dir / "posts.json").write_text(json.dumps(posts_meta, indent=2), encoding="utf-8")

        for f in temp_dir.iterdir():
            if f.name.startswith("variant_pin_") or f.name == "variants.json":
                target = target_pins_dir / f.name
                if target.exists():
                    target.unlink()
                shutil.copy2(f, target)
            if f.name.startswith("post_") or f.name.startswith("story_") or f.name == "posts.json":
                target = target_posts_dir / f.name
                if target.exists():
                    target.unlink()
                shutil.copy2(f, target)

    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "pack_name": pack_name,
        "pins_dir": str(target_pins_dir),
        "posts_dir": str(target_posts_dir),
        "titles": titles,
        "pins_meta": pins_meta,
        "posts_meta": posts_meta,
        "all_meta": all_meta,
    }
