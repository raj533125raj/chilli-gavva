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
