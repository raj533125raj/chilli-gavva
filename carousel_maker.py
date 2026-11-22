#!/usr/bin/env python3
"""carousel_maker.py — Instagram carousel generator for ready upload packs.

Produces a 6-slide carousel (1080x1350 PNG, Pillow only) for manual Instagram posting
by the owner into exported_content/marketing/<pack>/carousel/ (outside the pack).

Slides:
  1: Hook (pack title, big, max 2 lines)
  2-5: One benefit each from "What you get" with cropped/fitted preview image (never stretched)
  6: Call to action ("Link in bio" + pack short name)

Companion files:
  caption.txt: <= 2000 chars, listing text, 8 clean hashtags, no brand names, no money
  alt.txt: one alt text per slide (1 to 6)

Design rules:
  - 1080x1350 PNG per slide
  - Safe margins: 80 px
  - Text never cut (auto-shrink font, wrap at word boundaries, max 3 lines per block)
  - 2 color themes picked from cover image's main colors with contrast ratio >= 4.5:1
  - Reuse fonts from repo via mockups.font / fonts/Inter-*.ttf (do not duplicate font files)
  - File size < 1.5 MB each
  - Idempotent and atomic writes
  - Never touches the pack folder
"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None

# Ensure we can import font helpers from mockups if available
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "fonts")
FONTS = {
    "regular": "Inter-Regular.ttf",
    "semibold": "Inter-SemiBold.ttf",
    "bold": "Inter-Bold.ttf",
}
_font_cache: Dict[Tuple[str, int], Any] = {}

SLIDE_WIDTH = 1080
SLIDE_HEIGHT = 1350
MARGIN = 80
MAX_FILE_BYTES = 1_500_000  # 1.5 MB

# Brand names of other companies to filter out
_BRAND_PATTERNS = [
    (r"\bgoogle\s+sheets?\b", "spreadsheet"),
    (r"\bgoogle\s+docs?\b", "document"),
    (r"\bmicrosoft\s+excel\b", "spreadsheet"),
    (r"\bmicrosoft\s+word\b", "document"),
    (r"\bexcel\b", "spreadsheet"),
    (r"\bnotion\b", ""),
    (r"\bcanva\b", ""),
    (r"\betsy\b", ""),
    (r"\bgumroad\b", ""),
    (r"\bpayhip\b", ""),
    (r"\bamazon\b", ""),
    (r"\bapple\s+numbers\b", "spreadsheet"),
    (r"\bgoodnotes\b", "notes app"),
    (r"\bipad\b", "tablet"),
    (r"\blinkedin\b", "professional profile"),
    (r"\bgoogle\b", ""),
    (r"\bmicrosoft\b", ""),
    (r"\bchatgpt\b", ""),
    (r"\bopenai\b", ""),
]

BRAND_WORDS = {
    "google", "googlesheets", "googledocs", "excel", "microsoftexcel",
    "microsoftword", "word", "notion", "canva", "etsy", "gumroad", "payhip",
    "amazon", "applenumbers", "goodnotes", "ipad", "linkedin", "microsoft",
    "chatgpt", "openai",
}

_MONEY_PATTERN = re.compile(
    r"(?:\$[\d,]+(?:\.\d{1,2})?\b|"
    r"[\d,]+(?:\.\d{1,2})?\s*(?:usd|eur|gbp|inr|rs\.?|rupees?|dollars?|cents?|paise)\b|"
    r"(?:usd|eur|gbp|inr|rs\.?|rupees?|dollars?|cents?|paise)\s*[\d,]+(?:\.\d{1,2})?\b|"
    r"[₹€£¥]\s*[\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)

CURRENCY_WORDS = {
    "usd", "inr", "eur", "gbp", "rs", "rupee", "rupees", "dollar", "dollars",
    "cent", "cents", "price", "pricing", "cost", "costs", "pay", "payment",
}


def get_font(weight: str, size: int) -> Any:
    """Retrieve font from cache or load Inter TTF."""
    if ImageFont is None:
        raise RuntimeError("Pillow is required for font handling.")
    key = (weight, int(size))
    if key not in _font_cache:
        font_path = os.path.join(FONT_DIR, FONTS.get(weight, "Inter-Regular.ttf"))
        if os.path.isfile(font_path):
            _font_cache[key] = ImageFont.truetype(font_path, int(size))
        else:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def text_w(s: str, f: Any) -> float:
    return f.getlength(s)


def line_h(f: Any) -> int:
    return int(f.size * 1.25)


def wrap_text(s: str, f: Any, max_w: float) -> List[str]:
    """Wrap string at word boundaries within max_w width."""
    words = str(s).split()
    if not words:
        return [""]
    lines = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip() if cur else w
        if cur and text_w(trial, f) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [""]


def fit_text(
    s: str,
    weight: str,
    start_size: int,
    min_size: int,
    max_w: float,
    max_lines: int,
) -> Tuple[List[str], Any]:
    """Fit text within max_w and max_lines, shrinking font from start_size to min_size.

    If it still exceeds max_lines at min_size, words are truncated gracefully at a word
    boundary on the final line without cutting words in half.
    """
    size = start_size
    while size >= min_size:
        f = get_font(weight, size)
        lines = wrap_text(s, f, max_w)
        if len(lines) <= max_lines and all(text_w(l, f) <= max_w for l in lines):
            return lines, f
        size -= 2

    # At min_size, if lines > max_lines, truncate words to fit max_lines
    f = get_font(weight, min_size)
    lines = wrap_text(s, f, max_w)
    if len(lines) <= max_lines:
        return lines, f

    kept = lines[: max_lines - 1]
    # Distribute remaining words into the last line
    remaining_words = (" ".join(lines[max_lines - 1 :])).split()
    last_line = ""
    for w in remaining_words:
        trial = f"{last_line} {w}".strip() if last_line else w
        if text_w(trial, f) <= max_w:
            last_line = trial
        else:
            break
    kept.append(last_line if last_line else remaining_words[0])
    return kept, f


def sanitize_text(text: str) -> str:
    """Remove money amounts and brand names from text."""
    if not text:
        return ""
    # Strip money
    t = _MONEY_PATTERN.sub("", str(text))
    # Replace brand names
    for pat, repl in _BRAND_PATTERNS:
        t = re.sub(pat, repl, t, flags=re.IGNORECASE)
    # Clean whitespace
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


# --- Color and Contrast (WCAG 2.1) ---

def srgb_to_linear(c_srgb: float) -> float:
    """Convert sRGB component (0..1) to linear luminance value."""
    if c_srgb <= 0.04045:
        return c_srgb / 12.92
    return ((c_srgb + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: Tuple[int, int, int]) -> float:
    """Calculate relative luminance according to WCAG 2.1 standard."""
    r = srgb_to_linear(rgb[0] / 255.0)
    g = srgb_to_linear(rgb[1] / 255.0)
    b = srgb_to_linear(rgb[2] / 255.0)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(rgb1: Tuple[int, int, int], rgb2: Tuple[int, int, int]) -> float:
    """Calculate WCAG contrast ratio between two RGB colors (>= 1.0 and <= 21.0)."""
    l1 = relative_luminance(rgb1)
    l2 = relative_luminance(rgb2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def adjust_color_for_contrast(
    fg: Tuple[int, int, int],
    bg: Tuple[int, int, int],
    target_ratio: float = 4.5,
) -> Tuple[int, int, int]:
    """Adjust foreground color brightness until contrast ratio with bg is >= target_ratio."""
    cur_ratio = contrast_ratio(fg, bg)
    if cur_ratio >= target_ratio:
        return fg

    bg_lum = relative_luminance(bg)
    # If background is bright (lum > 0.5), darken foreground towards black
    # If background is dark (lum <= 0.5), lighten foreground towards white
    darken = bg_lum > 0.5

    r, g, b = fg
    for step in range(1, 101):
        factor = step / 100.0
        if darken:
            new_r = int(r * (1.0 - factor))
            new_g = int(g * (1.0 - factor))
            new_b = int(b * (1.0 - factor))
        else:
            new_r = int(r + (255 - r) * factor)
            new_g = int(g + (255 - g) * factor)
            new_b = int(b + (255 - b) * factor)

        cand = (max(0, min(255, new_r)), max(0, min(255, new_g)), max(0, min(255, new_b)))
        if contrast_ratio(cand, bg) >= target_ratio:
            return cand

    # Fallback to pure black or pure white
    return (0, 0, 0) if darken else (255, 255, 255)


def extract_cover_colors(cover_path: Optional[Path]) -> List[Tuple[int, int, int]]:
    """Sample dominant colors from cover image, or return fallback colors."""
    fallback = [(240, 245, 245), (32, 64, 60), (255, 240, 242), (80, 30, 45)]
    if not cover_path or not cover_path.is_file() or Image is None:
        return fallback

    try:
        with Image.open(cover_path) as im:
            rgb_im = im.convert("RGB")
            # Downsample to speed up processing
            small = rgb_im.resize((150, 150), Image.Resampling.BOX)
            # Quantize to 8 colors
            quant = small.quantize(colors=8)
            palette = quant.getpalette()
            if not palette:
                return fallback
            colors = []
            for i in range(8):
                idx = i * 3
                colors.append((palette[idx], palette[idx + 1], palette[idx + 2]))
            return colors
    except Exception:
        return fallback


def pick_carousel_themes(
    cover_path: Optional[Path],
) -> Tuple[Dict[str, Tuple[int, int, int]], Dict[str, Tuple[int, int, int]]]:
    """Pick 2 color themes from the cover image with verified contrast ratio >= 4.5:1.

    Theme 1 (e.g. Light theme):
      bg: light background
      text: dark ink with contrast >= 4.5:1 against bg
      accent: accent color with contrast >= 4.5:1 against bg

    Theme 2 (e.g. Accent/Bold theme):
      bg: deep/accent background
      text: light ink with contrast >= 4.5:1 against bg
      accent: secondary color with contrast >= 4.5:1 against bg
    """
    colors = extract_cover_colors(cover_path)

    # Sort colors by luminance
    colors_by_lum = sorted(colors, key=relative_luminance)
    darkest = colors_by_lum[0]
    lightest = colors_by_lum[-1]

    # Theme 1: Light canvas
    # Light background: blend lightest towards near-white if needed
    t1_bg = lightest
    if relative_luminance(t1_bg) < 0.75:
        # Lighten to ensure clean card style
        t1_bg = (
            int(t1_bg[0] * 0.3 + 255 * 0.7),
            int(t1_bg[1] * 0.3 + 255 * 0.7),
            int(t1_bg[2] * 0.3 + 255 * 0.7),
        )

    t1_text = adjust_color_for_contrast(darkest, t1_bg, 4.5)
    # Accent color
    mid_color = colors_by_lum[len(colors_by_lum) // 2]
    t1_accent = adjust_color_for_contrast(mid_color, t1_bg, 4.5)

    theme1 = {
        "bg": t1_bg,
        "text": t1_text,
        "accent": t1_accent,
    }

    # Theme 2: Dark / Deep canvas
    t2_bg = darkest
    if relative_luminance(t2_bg) > 0.25:
        # Darken to create a rich contrast slide
        t2_bg = (int(t2_bg[0] * 0.4), int(t2_bg[1] * 0.4), int(t2_bg[2] * 0.4))

    t2_text = adjust_color_for_contrast(lightest, t2_bg, 4.5)
    t2_accent = adjust_color_for_contrast(mid_color, t2_bg, 4.5)

    theme2 = {
        "bg": t2_bg,
        "text": t2_text,
        "accent": t2_accent,
    }

    # Verify both satisfy >= 4.5:1
    assert contrast_ratio(theme1["text"], theme1["bg"]) >= 4.5
    assert contrast_ratio(theme1["accent"], theme1["bg"]) >= 4.5
    assert contrast_ratio(theme2["text"], theme2["bg"]) >= 4.5
    assert contrast_ratio(theme2["accent"], theme2["bg"]) >= 4.5

    return theme1, theme2


# --- Parsing Pack Info ---

def parse_listing_file(listing_path: Path) -> Dict[str, Any]:
    """Parse listing.txt or listing.md to extract title, tags, description, and benefits."""
    out: Dict[str, Any] = {
        "title": "",
        "tags": [],
        "description": "",
        "benefits": [],
    }
    if not listing_path.is_file():
        return out

    try:
        content = listing_path.read_text(encoding="utf-8-sig")
    except Exception:
        return out

    lines = content.splitlines()
    cur_section = ""
    desc_lines = []
    what_lines = []

    for line in lines:
        raw_strip = line.strip()
        lower = raw_strip.lower()

        if lower.startswith("title:"):
            out["title"] = raw_strip.split(":", 1)[1].strip()
            cur_section = "title"
            continue
        elif lower.startswith("tags:"):
            tags_part = raw_strip.split(":", 1)[1].strip()
            out["tags"] = [t.strip() for t in tags_part.split(",") if t.strip()]
            cur_section = "tags"
            continue
        elif lower.startswith("description:"):
            cur_section = "desc"
            continue
        elif lower.startswith("what you get:") or lower.startswith("what's included:") or lower.startswith("included files:"):
            cur_section = "what"
            continue
        elif re.match(r"^[A-Z][A-Za-z /-]*:\s*$", raw_strip) and not lower.startswith("what"):
            cur_section = "other"
            continue

        if cur_section == "desc":
            desc_lines.append(line)
        elif cur_section == "what":
            # Item lines
            cleaned_item = re.sub(r"^[-*•\d.)\s]+", "", raw_strip).strip()
            if cleaned_item:
                what_lines.append(cleaned_item)

    out["description"] = "\n".join(desc_lines).strip()
    out["benefits"] = what_lines
    return out


def fit_and_crop_image(img: Any, target_w: int, target_h: int) -> Any:
    """Crop and fit an image into (target_w, target_h) preserving aspect ratio without stretching."""
    orig_w, orig_h = img.size
    target_ratio = target_w / target_h
    orig_ratio = orig_w / orig_h

    if orig_ratio > target_ratio:
        # Original is wider than target -> scale by height, crop width
        new_h = target_h
        new_w = int(orig_w * (target_h / orig_h))
    else:
        # Original is taller than target -> scale by width, crop height
        new_w = target_w
        new_h = int(orig_h * (target_w / orig_w))

    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    # Center crop
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    right = left + target_w
    bottom = top + target_h
    return resized.crop((left, top, right, bottom))


# --- Drawing Slides ---

def draw_slide_1(
    title: str,
    pack_short_name: str,
    theme: Dict[str, Tuple[int, int, int]],
    cover_img_path: Optional[Path],
) -> Any:
    """Slide 1: Hook — Title big (max 2 lines), safe margins 80px, cover preview."""
    im = Image.new("RGB", (SLIDE_WIDTH, SLIDE_HEIGHT), theme["bg"])
    d = ImageDraw.Draw(im)

    safe_w = SLIDE_WIDTH - 2 * MARGIN
    top_y = MARGIN + 40

    # Badge / Category tag
    badge_text = "SWIPE TO EXPLORE"
    badge_f = get_font("semibold", 22)
    bw = text_w(badge_text, badge_f)
    badge_box = [MARGIN, top_y, MARGIN + bw + 32, top_y + 44]
    d.rounded_rectangle(badge_box, radius=12, fill=theme["accent"])
    d.text((MARGIN + 16, top_y + 10), badge_text, font=badge_f, fill=theme["bg"])

    # Hook Title: Big, max 2 lines
    sanitized_title = sanitize_text(title) or sanitize_text(pack_short_name) or "Digital Template"
    t_lines, t_f = fit_text(sanitized_title, "bold", 68, 38, safe_w, 2)

    title_y = top_y + 64
    for line in t_lines:
        d.text((MARGIN, title_y), line, font=t_f, fill=theme["text"])
        title_y += line_h(t_f)

    # Subtitle or pack name
    sub_y = title_y + 16
    sub_text = sanitize_text(pack_short_name).upper()
    if sub_text:
        sub_f = get_font("semibold", 24)
        sub_lines, sub_font = fit_text(sub_text, "semibold", 24, 18, safe_w, 1)
        if sub_lines and sub_lines[0]:
            d.text((MARGIN, sub_y), sub_lines[0], font=sub_font, fill=theme["accent"])
            sub_y += line_h(sub_font) + 20
    else:
        sub_y += 10

    # Feature Cover Image in remaining vertical space
    remain_h = (SLIDE_HEIGHT - MARGIN - 60) - sub_y
    if remain_h > 200 and cover_img_path and cover_img_path.is_file():
        card_w = safe_w
        card_h = remain_h
        try:
            with Image.open(cover_img_path) as cov:
                fitted = fit_and_crop_image(cov.convert("RGB"), card_w, card_h)
                im.paste(fitted, (MARGIN, sub_y))
                # Border around the card
                d.rectangle([MARGIN, sub_y, MARGIN + card_w, sub_y + card_h], outline=theme["accent"], width=3)
        except Exception:
            pass

    # Pagination indicator at bottom: "1 / 6"
    pag_text = "1 / 6"
    pag_f = get_font("regular", 22)
    pw = text_w(pag_text, pag_f)
    d.text((SLIDE_WIDTH - MARGIN - pw, SLIDE_HEIGHT - MARGIN), pag_text, font=pag_f, fill=theme["accent"])

    return im


def draw_slide_benefit(
    slide_num: int,
    benefit_text: str,
    preview_path: Optional[Path],
    cover_path: Optional[Path],
    theme: Dict[str, Tuple[int, int, int]],
) -> Any:
    """Slides 2–5: One benefit each from 'What you get' with cropped/fitted preview image (never stretched)."""
    im = Image.new("RGB", (SLIDE_WIDTH, SLIDE_HEIGHT), theme["bg"])
    d = ImageDraw.Draw(im)

    safe_w = SLIDE_WIDTH - 2 * MARGIN
    top_y = MARGIN

    # Slide step pill: e.g. "WHAT'S INSIDE · PART 1"
    step_text = f"FEATURE · {slide_num - 1} OF 4"
    step_f = get_font("semibold", 22)
    sw = text_w(step_text, step_f)
    d.rounded_rectangle([MARGIN, top_y, MARGIN + sw + 28, top_y + 40], radius=10, fill=theme["accent"])
    d.text((MARGIN + 14, top_y + 8), step_text, font=step_f, fill=theme["bg"])

    # Benefit heading/text: max 3 lines per block, auto shrink, wrap at word boundaries
    clean_ben = sanitize_text(benefit_text)
    b_lines, b_f = fit_text(clean_ben, "bold", 48, 28, safe_w, 3)

    text_y = top_y + 60
    for line in b_lines:
        d.text((MARGIN, text_y), line, font=b_f, fill=theme["text"])
        text_y += line_h(b_f)

    # Preview image fitting: never stretched
    img_y = text_y + 30
    avail_h = (SLIDE_HEIGHT - MARGIN - 60) - img_y
    card_w = safe_w
    card_h = max(200, avail_h)

    # Use preview image if available, else fallback to cover
    chosen_path = preview_path if (preview_path and preview_path.is_file()) else cover_path
    if chosen_path and chosen_path.is_file():
        try:
            with Image.open(chosen_path) as src_im:
                cropped = fit_and_crop_image(src_im.convert("RGB"), card_w, card_h)
                im.paste(cropped, (MARGIN, img_y))
                d.rectangle([MARGIN, img_y, MARGIN + card_w, img_y + card_h], outline=theme["accent"], width=2)
        except Exception:
            pass

    # Pagination indicator: e.g. "2 / 6"
    pag_text = f"{slide_num} / 6"
    pag_f = get_font("regular", 22)
    pw = text_w(pag_text, pag_f)
    d.text((SLIDE_WIDTH - MARGIN - pw, SLIDE_HEIGHT - MARGIN), pag_text, font=pag_f, fill=theme["accent"])

    return im


def draw_slide_6_cta(
    pack_short_name: str,
    theme: Dict[str, Tuple[int, int, int]],
    cover_path: Optional[Path],
) -> Any:
    """Slide 6: Call to action — 'Link in bio' + pack short name, safe margins, high impact."""
    im = Image.new("RGB", (SLIDE_WIDTH, SLIDE_HEIGHT), theme["bg"])
    d = ImageDraw.Draw(im)

    safe_w = SLIDE_WIDTH - 2 * MARGIN
    top_y = MARGIN + 80

    # Header pill
    pill = "READY TO GET STARTED?"
    pf = get_font("semibold", 22)
    pw = text_w(pill, pf)
    d.rounded_rectangle([MARGIN, top_y, MARGIN + pw + 32, top_y + 44], radius=12, fill=theme["accent"])
    d.text((MARGIN + 16, top_y + 10), pill, font=pf, fill=theme["bg"])

    # Big CTA text: "Link in bio"
    cta_title = "Link in bio"
    c_lines, c_f = fit_text(cta_title, "bold", 72, 48, safe_w, 2)
    y = top_y + 70
    for line in c_lines:
        d.text((MARGIN, y), line, font=c_f, fill=theme["text"])
        y += line_h(c_f)

    # Pack name
    clean_name = sanitize_text(pack_short_name)
    n_lines, n_f = fit_text(f"Get your copy of {clean_name}", "semibold", 38, 24, safe_w, 3)
    y += 16
    for line in n_lines:
        d.text((MARGIN, y), line, font=n_f, fill=theme["accent"])
        y += line_h(n_f)

    # Action box in lower section
    box_y = y + 40
    box_h = (SLIDE_HEIGHT - MARGIN - 70) - box_y
    if box_h > 150:
        d.rounded_rectangle([MARGIN, box_y, MARGIN + safe_w, box_y + box_h], radius=20, fill=theme["accent"])

        # Inner CTA instruction text
        inner_lines, inner_f = fit_text("Tap the link in bio to download instantly.", "bold", 34, 22, safe_w - 60, 2)
        iy = box_y + (box_h // 2) - ((len(inner_lines) * line_h(inner_f)) // 2)
        for line in inner_lines:
            iw = text_w(line, inner_f)
            ix = MARGIN + (safe_w - iw) // 2
            d.text((ix, iy), line, font=inner_f, fill=theme["bg"])
            iy += line_h(inner_f)

    # Pagination indicator
    pag_text = "6 / 6"
    pag_f = get_font("regular", 22)
    p_w = text_w(pag_text, pag_f)
    d.text((SLIDE_WIDTH - MARGIN - p_w, SLIDE_HEIGHT - MARGIN), pag_text, font=pag_f, fill=theme["accent"])

    return im


# --- Caption and Alt text ---

def clean_hashtags(raw_tags: List[str], max_tags: int = 8) -> List[str]:
    """Return up to 8 clean hashtags (no spaces, no brands, no money, alphanumeric only)."""
    cleaned: List[str] = []
    seen = set()

    for tag in raw_tags:
        s = str(tag).strip().lower()
        if not s:
            continue
        parts = [p.strip() for p in s.split(",") if p.strip()]
        for part in parts:
            if _MONEY_PATTERN.search(part):
                continue
            norm = re.sub(r"^#+", "", part).strip()
            words = norm.split()
            if any(w in BRAND_WORDS or w in CURRENCY_WORDS for w in words):
                continue
            slug = "".join(re.findall(r"[a-z0-9]+", norm))
            if not slug or slug in BRAND_WORDS or slug in CURRENCY_WORDS:
                continue
            if slug not in seen:
                seen.add(slug)
                cleaned.append(f"#{slug}")
                if len(cleaned) >= max_tags:
                    return cleaned

    # Fallback hashtags if tags were sparse
    default_tags = ["#productivity", "#planner", "#organize", "#templates", "#digitalplanner", "#worksmart"]
    for dt in default_tags:
        if len(cleaned) >= max_tags:
            break
        if dt not in seen:
            seen.add(dt)
            cleaned.append(dt)

    return cleaned[:max_tags]


def build_caption(title: str, description: str, tags: List[str]) -> str:
    """Build Instagram caption <= 2000 chars, no brand names, no money, 8 hashtags."""
    clean_t = sanitize_text(title)
    clean_d = sanitize_text(description)

    # First line: Title
    lines = [clean_t, ""]

    # Body: Description
    if clean_d:
        lines.append(clean_d)
        lines.append("")

    # CTA line
    lines.append("Link in bio to get yours today!")
    lines.append("")

    # Hashtags
    hashtags = clean_hashtags(tags, 8)
    lines.append(" ".join(hashtags))

    full_caption = "\n".join(lines).strip()

    # Enforce 2,000 char maximum
    if len(full_caption) <= 2000:
        return full_caption

    # Truncate description to fit within 2,000 characters
    prefix = f"{clean_t}\n\n"
    suffix = f"\n\nLink in bio to get yours today!\n\n{' '.join(hashtags)}"
    available_desc = 2000 - len(prefix) - len(suffix)
    if available_desc > 50:
        truncated_desc = clean_d[:available_desc].rsplit(" ", 1)[0] + "..."
        return f"{prefix}{truncated_desc}{suffix}"
    return full_caption[:1997] + "..."


def build_alt_text(
    title: str,
    pack_short_name: str,
    benefits: List[str],
) -> str:
    """Build alt.txt with exactly 6 lines (one per slide)."""
    clean_t = sanitize_text(title) or "Digital template"
    clean_name = sanitize_text(pack_short_name) or clean_t

    alt_lines = []
    # Slide 1
    alt_lines.append(f"Slide 1: Cover slide showing the title {clean_t} and product overview.")

    # Slides 2-5
    for i in range(4):
        ben = sanitize_text(benefits[i]) if i < len(benefits) else f"Key feature and benefit {i + 1}"
        alt_lines.append(f"Slide {i + 2}: Feature highlight illustrating {ben}.")

    # Slide 6
    alt_lines.append(f"Slide 6: Call to action inviting viewers to check the link in bio for {clean_name}.")

    return "\n".join(alt_lines) + "\n"


# --- Carousel Maker Core ---

def get_pack_images(pack_dir: Path) -> Tuple[Optional[Path], List[Path]]:
    """Find cover.png and preview-*.png in the pack directory."""
    cover_path = pack_dir / "cover.png"
    if not cover_path.is_file():
        # Look for any cover*.png
        covs = sorted([p for p in pack_dir.iterdir() if "cover" in p.name.lower() and p.suffix.lower() == ".png"])
        cover_path = covs[0] if covs else None

    # Previews: preview-1.png, preview-2.png, etc.
    previews = sorted([
        p for p in pack_dir.iterdir()
        if p.name.lower().startswith("preview") and p.suffix.lower() == ".png"
    ])
    return cover_path, previews


def make_pack_carousel(
    pack_dir: Path,
    marketing_dir: Optional[Path] = None,
) -> Path:
    """Generate 6-slide carousel for one pack into exported_content/marketing/<pack>/carousel/.

    Idempotent, atomic writes, never touches the pack folder.
    """
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"Pack folder does not exist: {pack_dir}")

    pack_id = pack_dir.name
    # Determine output directory outside the pack
    if marketing_dir is None:
        target_dir = Path("exported_content") / "marketing" / pack_id / "carousel"
    else:
        target_dir = Path(marketing_dir) / pack_id / "carousel"

    # Read listing information from pack
    listing_file = pack_dir / "listing.txt"
    if not listing_file.is_file():
        listing_file = pack_dir / "listing.md"

    info = parse_listing_file(listing_file)
    title = info["title"] or pack_id.replace("-", " ").title()
    tags = info["tags"]
    desc = info["description"]
    benefits = info["benefits"]

    # Provide fallback benefits if fewer than 4
    default_benefits = [
        "Instant digital download ready to use immediately",
        "Clean, intentional layout designed for daily clarity",
        "Customizable format tailored to your workflow",
        "Step-by-step guidance and instructions included",
    ]
    while len(benefits) < 4:
        benefits.append(default_benefits[len(benefits)])

    cover_path, previews = get_pack_images(pack_dir)
    theme1, theme2 = pick_carousel_themes(cover_path)

    # Alternate or theme selection: Slide 1 and 6 use Theme 1, Slide 2-5 use Theme 1 / Theme 2
    # Ensure high contrast across slides
    slides: List[Any] = []

    # Slide 1: Hook
    slides.append(draw_slide_1(title, pack_id, theme1, cover_path))

    # Slides 2-5: Benefits
    for i in range(4):
        preview_img = previews[i] if i < len(previews) else (previews[0] if previews else cover_path)
        # Alternate theme between theme1 and theme2 for visual interest
        cur_theme = theme2 if (i % 2 == 1) else theme1
        slides.append(draw_slide_benefit(i + 2, benefits[i], preview_img, cover_path, cur_theme))

    # Slide 6: CTA
    slides.append(draw_slide_6_cta(title, theme1, cover_path))

    # Companion texts
    caption_content = build_caption(title, desc, tags)
    alt_content = build_alt_text(title, pack_id, benefits)

    # Atomic write to target directory
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_target = Path(tempfile.mkdtemp(prefix=".carousel-", dir=target_dir.parent))

    try:
        for idx, slide_im in enumerate(slides, 1):
            slide_file = temp_target / f"slide-{idx}.png"
            # Save PNG optimized, ensure size < 1.5MB
            slide_im.save(str(slide_file), "PNG", optimize=True)
            if slide_file.stat().st_size > MAX_FILE_BYTES:
                # If exceeds 1.5MB, resave with quantize or moderate compression
                quantized = slide_im.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
                quantized.save(str(slide_file), "PNG", optimize=True)

        (temp_target / "caption.txt").write_text(caption_content, encoding="utf-8")
        (temp_target / "alt.txt").write_text(alt_content, encoding="utf-8")

        # Atomic directory swap / replace
        if target_dir.exists():
            # If target exists, write into a staging folder and rename atomically
            old_backup = target_dir.parent / f".carousel-old-{os.getpid()}"
            target_dir.rename(old_backup)
            temp_target.rename(target_dir)
            import shutil
            shutil.rmtree(old_backup, ignore_errors=True)
        else:
            temp_target.rename(target_dir)

    except Exception:
        import shutil
        shutil.rmtree(temp_target, ignore_errors=True)
        raise

    return target_dir


def discover_upload_packs(base_products_dir: Path) -> List[Path]:
    """Find all ready packs under exported_content/products/upload/ or base dir."""
    upload_dir = base_products_dir / "upload" if (base_products_dir / "upload").is_dir() else base_products_dir
    if not upload_dir.is_dir():
        return []

    packs = sorted([
        p for p in upload_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    ])
    return packs


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", help="Specific pack ID to generate carousel for.")
    parser.add_argument("--all", action="store_true", help="Generate carousels for all ready packs.")
    parser.add_argument(
        "--products-dir",
        default="exported_content/products",
        help="Products directory (defaults to exported_content/products).",
    )
    parser.add_argument(
        "--marketing-dir",
        default="exported_content/marketing",
        help="Marketing directory for output (defaults to exported_content/marketing).",
    )
    args = parser.parse_args(argv)

    if not args.pack and not args.all:
        parser.print_help(sys.stderr)
        return 1

    prod_dir = Path(args.products_dir)
    mkt_dir = Path(args.marketing_dir)

    if args.pack:
        # Single pack mode
        # Look in prod_dir/upload/<pack> or prod_dir/<pack>
        cand1 = prod_dir / "upload" / args.pack
        cand2 = prod_dir / args.pack
        pack_path = cand1 if cand1.is_dir() else cand2
        if not pack_path.is_dir():
            print(f"Error: Pack '{args.pack}' not found in {prod_dir}.", file=sys.stderr)
            return 1
        out = make_pack_carousel(pack_path, mkt_dir)
        print(f"Carousel created for pack {args.pack} -> {out}")
        return 0

    if args.all:
        packs = discover_upload_packs(prod_dir)
        if not packs:
            print(f"No ready upload packs found in {prod_dir}.", file=sys.stderr)
            return 0
        count = 0
        for p in packs:
            out = make_pack_carousel(p, mkt_dir)
            print(f"Carousel created for pack {p.name} -> {out}")
            count += 1
        print(f"Successfully generated {count} carousels.")
        return 0

    return 0


# ==============================================================================
# CCH-3: Instagram carousel READY TO POST (SELLABLE packs, via marketing_auto)
# ==============================================================================
# One carousel of 5-8 slides (1080 x 1350) in the pack theme, drawn with pin_ready's layout model so the same
# checker can re-measure every text line; caption <= 2,200 chars with <= 30 hashtags; alt text per slide.
# Output: <pack marketing folder>/carousel/ slide-1.png.., caption.txt, alt.txt, carousel.json.
READY_W, READY_H = 1080, 1350
READY_MIN_SLIDES, READY_MAX_SLIDES = 5, 8
CAPTION_MAX = 2200
HASHTAG_MAX = 20
ALT_MAX = 100
READY_FILE = "carousel.json"
READY_VERSION = 1


def ready_hashtags(listing: Dict[str, Any], limit: int = 20) -> List[str]:
    """Hashtags from the listing's own tags and title words only (no brands, no money, no made-up topics)."""
    import pin_ready
    out: List[str] = []
    title = pin_ready.clean(listing.get("title") or "")
    words = list(listing.get("tags") or []) + [title] + [w for w in title.split() if len(w) > 4]
    for raw in words:
        text = pin_ready.clean(raw).lower()
        slug = "".join(re.findall(r"[a-z0-9]+", text))
        if len(slug) < 3 or len(slug) > 40 or slug.isdigit():
            continue
        tag = "#" + slug
        if tag not in out:
            out.append(tag)
        if len(out) >= min(limit, HASHTAG_MAX):
            break
    return out


def ready_caption(listing: Dict[str, Any], items: List[str], steps: List[str], tags: List[str]) -> str:
    import pin_ready
    title = pin_ready.clean(listing.get("title") or "") or "Printable pack"
    desc = pin_ready.cut(pin_ready.clean(listing.get("description") or ""), 900)
    parts = [title, ""]
    if desc:
        parts += [desc, ""]
    if items:
        parts += ["What you get:"] + ["- " + item for item in items] + [""]
    parts += ["How it works:"] + [f"{i}. {step}" for i, step in enumerate(steps, 1)] + [""]
    parts += ["Instant digital download for personal use.", "Link in bio to find it in the shop.", ""]
    hashtags = " ".join(tags)
    body = "\n".join(parts)
    if len(body) + len(hashtags) > CAPTION_MAX:        # keep the hashtags, shorten the description
        over = len(body) + len(hashtags) - CAPTION_MAX
        body = body.replace(desc, pin_ready.cut(desc, max(80, len(desc) - over - 10)), 1)
    return (body + hashtags).strip()


def _ready_slides(listing: Dict[str, Any], pack_dir: Path) -> List[Dict[str, Any]]:
    """The slide plan: cover, what's inside, 1-3 real page previews, how it works, a quote, call to action."""
    import pin_ready
    images = pin_ready.pack_images(pack_dir)
    previews = [p for p in images if p.name.lower().startswith("preview")][:3] or images[:1]
    title = pin_ready.cut(pin_ready.clean(listing.get("title") or ""), 100) or "Printable pack"
    word = pin_ready.page_word(listing)
    slides: List[Dict[str, Any]] = [
        {"design": "cover", "title": title, "image": images[0] if images else None,
         "alt": f"Cover slide with the title {title} and a page preview."},
        {"design": "inside", "title": "What's inside", "items": pin_ready.buyer_items(listing, 5),
         "image": images[-1] if len(images) > 1 else None, "alt": f"List of the files inside the {title}."}]
    for i, img in enumerate(previews, 1):
        slides.append({"design": "page", "title": f"{word} preview {i}", "image": img,
                       "alt": f"{word} preview {i} of the {title}."})
    slides.append({"design": "steps", "title": "How it works", "steps": pin_ready.use_steps(listing),
                   "alt": "Three numbered steps: download, use the files, use them again."})
    quote = pin_ready.first_sentence(listing.get("description") or "", 150)
    if quote:
        slides.append({"design": "quote", "title": quote, "alt": "Quote slide: " + quote})
    cta = {"design": "cta", "title": title, "alt": f"Last slide: {title}, link in bio to find it in the shop."}
    slides = slides[:READY_MAX_SLIDES - 1] + [cta]
    for slide in slides:
        slide["alt"] = pin_ready.cut(pin_ready.clean(slide["alt"]), ALT_MAX)
    return slides


def _draw_ready_slide(slide: Dict[str, Any], th: Dict[str, Any], index: int, total: int) -> Any:
    import pin_ready
    W, H = READY_W, READY_H
    cv = pin_ready.Canvas(W, H, th["bg"])
    on_accent = pin_ready._mv().contrast_text(th["accent"])
    design = slide["design"]
    if design == "cover":
        cv.rect((0, 0, W, 470), th["accent"])
        cv.text("title", slide["title"], 100, 110, W - 200, "bold", (80, 72, 64, 56, 50, 44), 4, on_accent,
                th["accent"], align="center")
        cv.page(slide.get("image"), (130, 540, W - 130, H - 150), th["paper"], th["shadow"])
        cv.text("hint", "Swipe to see inside", 100, H - 120, W - 380, "semibold", (34, 30), 1, th["body"],
                th["bg"], align="center")
    elif design == "inside":
        y = cv.text("title", slide["title"], 100, 120, W - 200, "bold", (72, 64), 1, th["ink"], th["bg"],
                    align="center")
        items = ["- " + item for item in (slide.get("items") or ["Ready-to-use files"])]
        top = y + 50
        inner = sum(pin_ready.block_height(t, "regular", W - 300, 2, (40, 36, 32)) + 30 for t in items)
        bottom = top + 50 + inner + 20
        cv.rect((100, top, W - 100, bottom), th["paper"], radius=22)
        iy = top + 50
        for i, item in enumerate(items, 1):
            iy = cv.text(f"item{i}", item, 150, iy, W - 300, "regular", (40, 36, 32), 2, th["ink"], th["paper"]) + 30
        if slide.get("image") is not None and bottom + 60 < H - 450:
            cv.page(slide["image"], (200, bottom + 60, W - 200, H - 150), th["paper"], th["shadow"])
    elif design == "page":
        cv.text("title", slide["title"], 100, 90, W - 200, "bold", (56, 50), 1, th["accent"], th["bg"],
                align="center")
        cv.page(slide.get("image"), (90, 200, W - 90, H - 140), th["paper"], th["shadow"])
    elif design == "steps":
        start = max(120, (H - (pin_ready.line_height(72) + 60 + 230 * len(slide.get("steps") or []))) // 2)
        y = cv.text("title", slide["title"], 100, start, W - 200, "bold", (72, 64), 1, th["ink"], th["bg"],
                    align="center") + 60
        for i, step in enumerate(slide.get("steps") or [], 1):
            cv.rect((100, y, W - 100, y + 190), th["paper"], radius=22)
            cv.rect((140, y + 50, 230, y + 140), th["accent"], radius=45)
            cv.text(f"num{i}", str(i), 140, y + 63, 90, "bold", (50,), 1, on_accent, th["accent"], align="center")
            cv.text(f"step{i}", step, 270, y + 45, W - 100 - 270 - 40, "semibold", (40, 36, 32), 2, th["ink"],
                    th["paper"])
            y += 230
    elif design == "quote":
        text_h = pin_ready.block_height(slide["title"], "bold", W - 330, 7, (64, 58, 52, 46, 40))
        top = max(120, (H - text_h - 200) // 2)
        cv.rect((90, top, W - 90, top + text_h + 200), th["paper"], radius=26)
        cv.rect((90, top, 108, top + text_h + 200), th["accent"])
        cv.text("quote", slide["title"], 160, top + 100, W - 330, "bold", (64, 58, 52, 46, 40), 7, th["ink"],
                th["paper"])
    else:  # cta
        cv.rect((0, 0, W, H), th["accent"])
        y = cv.text("title", slide["title"], 100, 380, W - 200, "bold", (76, 68, 60, 52, 46), 4, on_accent,
                    th["accent"], align="center")
        cv.text("cta", "Link in bio to find it in the shop", 100, y + 60, W - 200, "semibold", (44, 40, 36), 2,
                on_accent, th["accent"], align="center")
        cv.text("note", "Instant digital download - personal use", 100, H - 200, W - 200, "regular", (34, 30), 1,
                on_accent, th["accent"], align="center")
    if design != "cta":
        cv.text("count", f"{index} / {total}", W - 260, H - 110, 180, "semibold", (28,), 1, th["body"], th["bg"],
                align="center")
    return cv


def make_ready_carousel(pack_dir: Path, out_dir: Path, now: Any = None) -> Dict[str, Any]:
    """Make the ready carousel of one pack into `out_dir` (the pack's carousel folder), check it and return the
    carousel.json data. The folder is replaced atomically; the pack folder is only read. Nothing is posted."""
    import datetime as _dt
    import json as _json
    import shutil
    import pin_ready
    pack_dir, out_dir = Path(pack_dir), Path(out_dir)
    listing = pin_ready.read_listing(pack_dir)
    th = pin_ready._theme(pack_dir)
    plan = _ready_slides(listing, pack_dir)
    tags = ready_hashtags(listing)
    caption = ready_caption(listing, pin_ready.buyer_items(listing, 8), pin_ready.use_steps(listing), tags)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".carousel-", dir=str(out_dir.parent)))
    try:
        slides = []
        for index, slide in enumerate(plan, 1):
            cv = _draw_ready_slide(slide, th, index, len(plan))
            name = f"slide-{index}.png"
            cv.image.save(stage / name, "PNG", optimize=True)
            slides.append({"file": name, "design": slide["design"], "alt": slide["alt"], "width": READY_W,
                           "height": READY_H, "layout": cv.blocks})
        (stage / "caption.txt").write_text(caption + "\n", encoding="utf-8")
        (stage / "alt.txt").write_text("".join(f"Slide {i}: {s['alt']}\n" for i, s in enumerate(slides, 1)),
                                       encoding="utf-8")
        stamp = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc).replace(tzinfo=None)
        data = {"version": READY_VERSION, "pack": pack_dir.name, "made_at": stamp.isoformat(timespec="seconds"),
                "caption": caption, "hashtags": tags, "theme": {k: pin_ready.hex_of(v) for k, v in th.items()},
                "note": "Ready to post by hand on Instagram. Nothing auto-posts.", "slides": slides}
        (stage / READY_FILE).write_text(_json.dumps(data, indent=2), encoding="utf-8")
        problems = check_ready_carousel(stage)
        data["check"] = {"ok": not problems, "problems": problems}
        (stage / READY_FILE).write_text(_json.dumps(data, indent=2), encoding="utf-8")
        if out_dir.exists():
            old = out_dir.parent / f".carousel-old-{os.getpid()}"
            out_dir.rename(old)
            stage.rename(out_dir)
            shutil.rmtree(old, ignore_errors=True)
        else:
            stage.rename(out_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return data


def check_ready_carousel(folder: Path) -> List[str]:
    """Posting gate: real slides, visible hook/CTA, safe caption and matching alt text."""
    import json as _json
    import pin_ready
    import secret_guard
    folder = Path(folder)
    try:
        data = _json.loads((folder / READY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["Carousel vivaralu missing leda broken; malli save cheyandi."]
    slides = data.get("slides") if isinstance(data, dict) else None
    count = len(slides) if isinstance(slides, list) else 0
    if not READY_MIN_SLIDES <= count <= READY_MAX_SLIDES:
        return [f"Carousel lo {READY_MIN_SLIDES}-{READY_MAX_SLIDES} slides tayaru cheyandi."]
    if Image is None:
        return ["Carousel images check kosam Pillow setup cheyandi."]
    problems = []
    for i, slide in enumerate(slides, 1):
        label = f"Slide {i}"
        if not isinstance(slide, dict):
            problems.append(f"{label}: Slide vivaralu saricheyandi.")
            continue
        path = folder / str(slide.get("file") or "")
        try:
            path.resolve().relative_to(folder.resolve())
        except ValueError:
            problems.append(f"{label}: Image ni ee Carousel folder lone pettandi.")
            continue
        try:
            with Image.open(path) as im:
                if im.size != (READY_W, READY_H):
                    problems.append(f"{label}: Image {READY_W}x{READY_H} ga tayaru cheyandi.")
                else:
                    im.load()
        except (OSError, ValueError, TypeError, Image.DecompressionBombError):
            problems.append(f"{label}: teriche slide image file pettandi.")
        alt = slide.get("alt")
        if not isinstance(alt,str) or not 0 < len(alt.strip()) <= 500:
            problems.append(f"{label}: Alt text 1-500 characters lo rayandi.")
        elif pin_ready.check_money_text(alt) or pin_ready.BRAND_CHECK.search(alt):
            problems.append(f"{label}: Alt text lo brand/money text teeseyandi.")
        layout = slide.get("layout")
        if not isinstance(layout,list) or not layout:
            problems.append(f"{label}: readable Image text layout save cheyandi.")
            layout = []
        else:
            try:
                for problem in pin_ready.check_layout(layout,READY_W,READY_H,label):
                    detail = problem.split(": ",1)[-1].split(" ('",1)[0]
                    problems.append(f"{label}: {detail}; font, contrast mariyu margins saricheyandi.")
            except (ValueError,TypeError,KeyError,AttributeError,IndexError,OverflowError):
                problems.append(f"{label}: Image text layout vivaralu saricheyandi.")
        if i == 1 and not any(isinstance(block,dict) and block.get("role") in ("title","hook")
                              and str(block.get("text") or "").strip() for block in layout):
            problems.append("Slide 1: buyer ni aape Hook ni Image meeda rayandi.")
        if i == count and not any(isinstance(block,dict) and
                re.search(r"\b(?:download|visit|shop|save|tap|open|explore|try|get)\b|link in bio",
                          str(block.get("text") or ""),re.I) for block in layout):
            problems.append("Last Slide: call to action rayandi; Link in bio leda Download ani cheppandi.")
    caption = data.get("caption")
    if not isinstance(caption,str) or not 0 < len(caption.strip()) <= 2200:
        problems.append("Caption 1-2200 characters lo rayandi.")
        caption = str(caption or "")
    if pin_ready.check_money_text(caption) or pin_ready.BRAND_CHECK.search(caption):
        problems.append("Caption lo brand/money text teeseyandi.")
    if len(re.findall(r"(?<![\w#])#\w+",caption)) > 20:
        problems.append("Caption lo 20 kanna ekkuva hashtags unnayi; 20 lopala pettandi.")
    try:
        on_disk = (folder / "caption.txt").read_text(encoding="utf-8").strip()
    except (OSError,ValueError):
        on_disk = None
    if on_disk != caption.strip():
        problems.append("caption.txt lo check chesina ade Caption save cheyandi.")
    try:
        alt_lines = [line.strip() for line in (folder / "alt.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError,ValueError):
        alt_lines = []
    expected = [f"Slide {i}: {slide.get('alt') or ''}" for i,slide in enumerate(slides,1) if isinstance(slide,dict)]
    if len(alt_lines) != count or alt_lines != expected:
        problems.append("alt.txt lo prathi Slide ki ade Alt text okka line lo save cheyandi.")
    if secret_guard.scan_text(_json.dumps(data,ensure_ascii=False)):
        problems.append("Carousel lo private vivaralu teesesi malli check cheyandi.")
    return list(dict.fromkeys(problems))


if __name__ == "__main__":
    sys.exit(main())
