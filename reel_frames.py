#!/usr/bin/env python3
"""reel_frames.py — Instagram/Pinterest vertical Reel frame generator for ready upload packs.

Produces 8 vertical frames (1080x1920 PNG, Pillow only) for a 15-second reel
by the owner into exported_content/marketing/<pack>/reel/ (outside the pack).

Frames:
  1: Hook (pack title, big, compelling hook badge)
  2-6: 5 Benefit frames highlighting key features using fitted preview images (never stretched)
  7: "How it looks printed" (showing printed / paper mockup preview)
  8: Call to action ("Link in bio" + pack title + download info)

Companion files:
  reel.txt: caption (clean, no brand names, no money) + 8 clean hashtags
  make_reel.sh: ffmpeg script for owner manual execution (never run ffmpeg in CI)

Design rules:
  - 1080x1920 PNG per frame
  - Safe margins: 120 px top and bottom, 80 px left/right
  - WCAG contrast ratio >= 4.5:1
  - Text never cut (auto-shrink font, wrap at word boundaries, max lines enforced)
  - Fitted preview images (never stretched; aspect ratio preserved)
  - No money amounts, no third-party brand logos or names
  - Idempotent and deterministic outputs
  - Atomic writes (staging directory renamed)
  - Never touches or modifies the pack folder
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None
    HAS_PIL = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = "/app/fonts" if os.path.isdir("/app/fonts") else os.path.join(BASE_DIR, "fonts")
FONTS = {
    "regular": "Inter-Regular.ttf",
    "semibold": "Inter-SemiBold.ttf",
    "bold": "Inter-Bold.ttf",
}
_font_cache: Dict[Tuple[str, int], Any] = {}

FRAME_WIDTH = 1080
FRAME_HEIGHT = 1920
SAFE_MARGIN_TB = 120  # Top and bottom safe margin
SAFE_MARGIN_LR = 80   # Left and right safe margin
TOTAL_FRAMES = 8
MAX_FILE_BYTES = 2_000_000  # 2.0 MB per PNG

# Brand names to filter
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
    "cent", "cents", "price", "pricing", "cost", "costs", "pay", "payment", "sale",
}


def get_font(weight: str, size: int) -> Any:
    """Retrieve font from cache or load Inter TTF."""
    if ImageFont is None:
        raise RuntimeError("Pillow is required for font handling.")
    key = (weight, int(size))
    if key not in _font_cache:
        font_path = os.path.join(FONT_DIR, FONTS.get(weight, "Inter-Regular.ttf"))
        if os.path.isfile(font_path):
            try:
                _font_cache[key] = ImageFont.truetype(font_path, int(size))
            except Exception:
                _font_cache[key] = ImageFont.load_default()
        else:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def text_w(s: str, f: Any) -> float:
    if hasattr(f, "getlength"):
        return f.getlength(s)
    if hasattr(f, "getbbox"):
        bbox = f.getbbox(s)
        return float(bbox[2] - bbox[0])
    return float(len(s) * 10)


def line_h(f: Any) -> int:
    if hasattr(f, "size"):
        return int(f.size * 1.3)
    return 24


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
    """Fit text within max_w and max_lines, shrinking font from start_size down to min_size."""
    size = start_size
    while size >= min_size:
        f = get_font(weight, size)
        lines = wrap_text(s, f, max_w)
        if len(lines) <= max_lines and all(text_w(l, f) <= max_w for l in lines):
            return lines, f
        size -= 2

    f = get_font(weight, min_size)
    lines = wrap_text(s, f, max_w)
    if len(lines) <= max_lines:
        return lines, f

    kept = lines[: max_lines - 1]
    remaining_words = (" ".join(lines[max_lines - 1:])).split()
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
    t = _MONEY_PATTERN.sub("", str(text))
    for pat, repl in _BRAND_PATTERNS:
        t = re.sub(pat, repl, t, flags=re.IGNORECASE)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def srgb_to_linear(c_srgb: float) -> float:
    if c_srgb <= 0.04045:
        return c_srgb / 12.92
    return ((c_srgb + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: Tuple[int, int, int]) -> float:
    r = srgb_to_linear(rgb[0] / 255.0)
    g = srgb_to_linear(rgb[1] / 255.0)
    b = srgb_to_linear(rgb[2] / 255.0)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(rgb1: Tuple[int, int, int], rgb2: Tuple[int, int, int]) -> float:
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
    """Adjust foreground brightness until contrast with bg is >= target_ratio."""
    cur_ratio = contrast_ratio(fg, bg)
    if cur_ratio >= target_ratio:
        return fg

    bg_lum = relative_luminance(bg)
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
    return (0, 0, 0) if darken else (255, 255, 255)


def extract_cover_colors(cover_path: Optional[Path]) -> List[Tuple[int, int, int]]:
    fallback = [(245, 247, 248), (30, 60, 55), (230, 240, 240), (70, 130, 120)]
    if not cover_path or not cover_path.is_file() or Image is None:
        return fallback
    try:
        with Image.open(cover_path) as im:
            rgb_im = im.convert("RGB")
            small = rgb_im.resize((150, 150), Image.Resampling.BOX)
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


def pick_reel_theme(cover_path: Optional[Path]) -> Dict[str, Tuple[int, int, int]]:
    """Determine theme with verified WCAG contrast >= 4.5:1."""
    colors = extract_cover_colors(cover_path)
    colors_by_lum = sorted(colors, key=relative_luminance)
    darkest = colors_by_lum[0]
    lightest = colors_by_lum[-1]

    # Theme: crisp light card style with dark ink & high-contrast accent
    bg = lightest
    if relative_luminance(bg) < 0.8:
        bg = (
            int(bg[0] * 0.25 + 255 * 0.75),
            int(bg[1] * 0.25 + 255 * 0.75),
            int(bg[2] * 0.25 + 255 * 0.75),
        )

    text_col = adjust_color_for_contrast(darkest, bg, 4.5)
    mid_col = colors_by_lum[len(colors_by_lum) // 2]
    accent_col = adjust_color_for_contrast(mid_col, bg, 4.5)

    assert contrast_ratio(text_col, bg) >= 4.5
    assert contrast_ratio(accent_col, bg) >= 4.5

    return {
        "bg": bg,
        "text": text_col,
        "accent": accent_col,
    }


def fit_and_contain_image(img: Any, target_w: int, target_h: int, bg_color: Tuple[int, int, int]) -> Any:
    """Fit image inside target dimensions maintaining aspect ratio (letterboxed with bg_color, never stretched)."""
    orig_w, orig_h = img.size
    if orig_w <= 0 or orig_h <= 0:
        return Image.new("RGB", (target_w, target_h), bg_color)

    ratio = min(target_w / orig_w, target_h / orig_h)
    new_w = max(1, int(orig_w * ratio))
    new_h = max(1, int(orig_h * ratio))

    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), bg_color)
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    canvas.paste(resized, (paste_x, paste_y))
    return canvas


def fit_and_crop_image(img: Any, target_w: int, target_h: int) -> Any:
    """Fit image filling target dimensions preserving aspect ratio (centered crop, never stretched)."""
    orig_w, orig_h = img.size
    target_ratio = target_w / target_h
    orig_ratio = orig_w / orig_h

    if orig_ratio > target_ratio:
        new_h = target_h
        new_w = int(orig_w * (target_h / orig_h))
    else:
        new_w = target_w
        new_h = int(orig_h * (target_w / orig_w))

    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def parse_listing_file(listing_path: Path) -> Dict[str, Any]:
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
            cleaned_item = re.sub(r"^[-*•\d.)\s]+", "", raw_strip).strip()
            if cleaned_item:
                what_lines.append(cleaned_item)

    out["description"] = "\n".join(desc_lines).strip()
    out["benefits"] = what_lines
    return out


def clean_hashtags(raw_tags: List[str], max_tags: int = 8) -> List[str]:
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

    fallbacks = ["#reel", "#printable", "#planner", "#productivity", "#organization", "#template", "#stationery", "#focus"]
    for fb in fallbacks:
        if len(cleaned) >= max_tags:
            break
        if fb not in seen:
            seen.add(fb)
            cleaned.append(fb)

    return cleaned[:max_tags]


# --- Drawing Reel Frames ---

def draw_frame_1_hook(
    title: str,
    pack_name: str,
    theme: Dict[str, Tuple[int, int, int]],
    cover_path: Optional[Path],
) -> Any:
    """Frame 1: Hook — Big Hook Header, Pack Title, fitted preview."""
    im = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), theme["bg"])
    d = ImageDraw.Draw(im)

    safe_w = FRAME_WIDTH - 2 * SAFE_MARGIN_LR
    cur_y = SAFE_MARGIN_TB

    # Hook Badge
    badge_text = "WATCH THIS BEFORE YOU PLAN"
    bf = get_font("semibold", 26)
    bw = text_w(badge_text, bf)
    d.rounded_rectangle([SAFE_MARGIN_LR, cur_y, SAFE_MARGIN_LR + bw + 40, cur_y + 54], radius=14, fill=theme["accent"])
    d.text((SAFE_MARGIN_LR + 20, cur_y + 12), badge_text, font=bf, fill=theme["bg"])
    cur_y += 84

    # Big Hook Pack Title (max 3 lines)
    sanitized_title = sanitize_text(title) or sanitize_text(pack_name) or "Printable Template"
    t_lines, t_f = fit_text(sanitized_title, "bold", 72, 42, safe_w, 3)
    for line in t_lines:
        d.text((SAFE_MARGIN_LR, cur_y), line, font=t_f, fill=theme["text"])
        cur_y += line_h(t_f)

    cur_y += 24
    sub_text = "Designed for daily clarity & focus"
    sf = get_font("semibold", 30)
    d.text((SAFE_MARGIN_LR, cur_y), sub_text, font=sf, fill=theme["accent"])
    cur_y += line_h(sf) + 40

    # Image Area: Preview fitted in remaining safe height
    bottom_bound = FRAME_HEIGHT - SAFE_MARGIN_TB - 80
    avail_h = bottom_bound - cur_y
    if avail_h > 300 and cover_path and cover_path.is_file():
        try:
            with Image.open(cover_path) as cov:
                fitted = fit_and_contain_image(cov.convert("RGB"), safe_w, avail_h, theme["bg"])
                im.paste(fitted, (SAFE_MARGIN_LR, cur_y))
                d.rectangle([SAFE_MARGIN_LR, cur_y, SAFE_MARGIN_LR + safe_w, cur_y + avail_h], outline=theme["accent"], width=3)
        except Exception:
            pass

    # Progress/indicator at bottom within safe zone
    prog_text = "1 / 8"
    pf = get_font("regular", 24)
    pw = text_w(prog_text, pf)
    d.text((FRAME_WIDTH - SAFE_MARGIN_LR - pw, FRAME_HEIGHT - SAFE_MARGIN_TB - 40), prog_text, font=pf, fill=theme["accent"])

    return im


def draw_frame_benefit(
    frame_idx: int,
    benefit_idx: int,
    benefit_text: str,
    preview_path: Optional[Path],
    cover_path: Optional[Path],
    theme: Dict[str, Tuple[int, int, int]],
) -> Any:
    """Frames 2–6: 5 Benefit frames highlighting key features using fitted preview images (never stretched)."""
    im = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), theme["bg"])
    d = ImageDraw.Draw(im)

    safe_w = FRAME_WIDTH - 2 * SAFE_MARGIN_LR
    cur_y = SAFE_MARGIN_TB

    # Feature Badge: e.g. "BENEFIT 01 OF 05"
    pill_text = f"FEATURE {benefit_idx:02d} OF 05"
    bf = get_font("semibold", 24)
    bw = text_w(pill_text, bf)
    d.rounded_rectangle([SAFE_MARGIN_LR, cur_y, SAFE_MARGIN_LR + bw + 36, cur_y + 48], radius=12, fill=theme["accent"])
    d.text((SAFE_MARGIN_LR + 18, cur_y + 11), pill_text, font=bf, fill=theme["bg"])
    cur_y += 74

    # Benefit Heading: clean, max 3 lines, auto-shrink, wrap at word boundaries
    clean_ben = sanitize_text(benefit_text)
    b_lines, b_f = fit_text(clean_ben, "bold", 54, 32, safe_w, 3)
    for line in b_lines:
        d.text((SAFE_MARGIN_LR, cur_y), line, font=b_f, fill=theme["text"])
        cur_y += line_h(b_f)

    cur_y += 30

    # Image Area: Preview fitted preserving aspect ratio
    bottom_bound = FRAME_HEIGHT - SAFE_MARGIN_TB - 80
    avail_h = bottom_bound - cur_y
    card_w = safe_w
    card_h = max(200, avail_h)

    img_source = preview_path if (preview_path and preview_path.is_file()) else cover_path
    if img_source and img_source.is_file():
        try:
            with Image.open(img_source) as src_im:
                fitted = fit_and_contain_image(src_im.convert("RGB"), card_w, card_h, theme["bg"])
                im.paste(fitted, (SAFE_MARGIN_LR, cur_y))
                d.rectangle([SAFE_MARGIN_LR, cur_y, SAFE_MARGIN_LR + card_w, cur_y + card_h], outline=theme["accent"], width=2)
        except Exception:
            pass

    prog_text = f"{frame_idx} / 8"
    pf = get_font("regular", 24)
    pw = text_w(prog_text, pf)
    d.text((FRAME_WIDTH - SAFE_MARGIN_LR - pw, FRAME_HEIGHT - SAFE_MARGIN_TB - 40), prog_text, font=pf, fill=theme["accent"])

    return im


def draw_frame_7_printed(
    title: str,
    preview_path: Optional[Path],
    cover_path: Optional[Path],
    theme: Dict[str, Tuple[int, int, int]],
) -> Any:
    """Frame 7: 'How it looks printed' — shows physical/paper layout preview."""
    im = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), theme["bg"])
    d = ImageDraw.Draw(im)

    safe_w = FRAME_WIDTH - 2 * SAFE_MARGIN_LR
    cur_y = SAFE_MARGIN_TB

    badge_text = "READY TO PRINT"
    bf = get_font("semibold", 24)
    bw = text_w(badge_text, bf)
    d.rounded_rectangle([SAFE_MARGIN_LR, cur_y, SAFE_MARGIN_LR + bw + 36, cur_y + 48], radius=12, fill=theme["accent"])
    d.text((SAFE_MARGIN_LR + 18, cur_y + 11), badge_text, font=bf, fill=theme["bg"])
    cur_y += 74

    heading = "How it looks printed"
    h_lines, h_f = fit_text(heading, "bold", 64, 40, safe_w, 2)
    for line in h_lines:
        d.text((SAFE_MARGIN_LR, cur_y), line, font=h_f, fill=theme["text"])
        cur_y += line_h(h_f)

    cur_y += 16
    sub = "Print at home on standard paper, binder, or desk clipboard."
    sub_lines, sub_f = fit_text(sub, "semibold", 28, 20, safe_w, 2)
    for line in sub_lines:
        d.text((SAFE_MARGIN_LR, cur_y), line, font=sub_f, fill=theme["accent"])
        cur_y += line_h(sub_f)

    cur_y += 36
    bottom_bound = FRAME_HEIGHT - SAFE_MARGIN_TB - 80
    avail_h = bottom_bound - cur_y

    img_source = preview_path if (preview_path and preview_path.is_file()) else cover_path
    if img_source and img_source.is_file():
        try:
            with Image.open(img_source) as src_im:
                # Add shadow / simulated paper margin around fitted image
                fitted = fit_and_contain_image(src_im.convert("RGB"), safe_w - 40, avail_h - 40, (255, 255, 255))
                # Paste paper canvas
                paper_box = [SAFE_MARGIN_LR + 20, cur_y, SAFE_MARGIN_LR + safe_w - 20, cur_y + avail_h]
                d.rectangle(paper_box, fill=(255, 255, 255), outline=theme["accent"], width=3)
                im.paste(fitted, (SAFE_MARGIN_LR + 20, cur_y + 20))
        except Exception:
            pass

    prog_text = "7 / 8"
    pf = get_font("regular", 24)
    pw = text_w(prog_text, pf)
    d.text((FRAME_WIDTH - SAFE_MARGIN_LR - pw, FRAME_HEIGHT - SAFE_MARGIN_TB - 40), prog_text, font=pf, fill=theme["accent"])

    return im


def draw_frame_8_cta(
    title: str,
    pack_name: str,
    theme: Dict[str, Tuple[int, int, int]],
    cover_path: Optional[Path],
) -> Any:
    """Frame 8: Call to Action — 'Link in bio' + title, instant download info."""
    im = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), theme["bg"])
    d = ImageDraw.Draw(im)

    safe_w = FRAME_WIDTH - 2 * SAFE_MARGIN_LR
    cur_y = SAFE_MARGIN_TB + 60

    badge_text = "INSTANT DIGITAL DOWNLOAD"
    bf = get_font("semibold", 24)
    bw = text_w(badge_text, bf)
    d.rounded_rectangle([SAFE_MARGIN_LR, cur_y, SAFE_MARGIN_LR + bw + 36, cur_y + 48], radius=12, fill=theme["accent"])
    d.text((SAFE_MARGIN_LR + 18, cur_y + 11), badge_text, font=bf, fill=theme["bg"])
    cur_y += 90

    cta_heading = "Link in bio"
    c_lines, c_f = fit_text(cta_heading, "bold", 80, 52, safe_w, 2)
    for line in c_lines:
        d.text((SAFE_MARGIN_LR, cur_y), line, font=c_f, fill=theme["text"])
        cur_y += line_h(c_f)

    cur_y += 24
    clean_title = sanitize_text(title) or sanitize_text(pack_name)
    sub = f"Get your copy of {clean_title}"
    sub_lines, sub_f = fit_text(sub, "semibold", 38, 24, safe_w, 3)
    for line in sub_lines:
        d.text((SAFE_MARGIN_LR, cur_y), line, font=sub_f, fill=theme["accent"])
        cur_y += line_h(sub_f)

    cur_y += 60
    # Big CTA action button block
    button_h = 240
    button_y = cur_y
    d.rounded_rectangle([SAFE_MARGIN_LR, button_y, SAFE_MARGIN_LR + safe_w, button_y + button_h], radius=24, fill=theme["accent"])

    action_text1 = "Tap the link in bio to download"
    action_text2 = "Print & start organizing today!"
    af1 = get_font("bold", 36)
    af2 = get_font("semibold", 28)

    w1 = text_w(action_text1, af1)
    w2 = text_w(action_text2, af2)
    x1 = SAFE_MARGIN_LR + (safe_w - w1) // 2
    x2 = SAFE_MARGIN_LR + (safe_w - w2) // 2

    d.text((x1, button_y + 60), action_text1, font=af1, fill=theme["bg"])
    d.text((x2, button_y + 130), action_text2, font=af2, fill=theme["bg"])

    # Bottom safe zone footer
    prog_text = "8 / 8"
    pf = get_font("regular", 24)
    pw = text_w(prog_text, pf)
    d.text((FRAME_WIDTH - SAFE_MARGIN_LR - pw, FRAME_HEIGHT - SAFE_MARGIN_TB - 40), prog_text, font=pf, fill=theme["accent"])

    return im


def build_reel_caption(title: str, description: str, tags: List[str]) -> str:
    """Build reel.txt caption + 8 clean hashtags."""
    clean_t = sanitize_text(title)
    clean_d = sanitize_text(description)
    lines = [clean_t, ""]
    if clean_d:
        lines.append(clean_d)
        lines.append("")
    lines.append("Link in bio to get yours today!")
    lines.append("")
    hashtags = clean_hashtags(tags, 8)
    lines.append(" ".join(hashtags))
    return "\n".join(lines).strip() + "\n"


def build_make_reel_sh() -> str:
    """Create make_reel.sh script for owner manual execution with ffmpeg (never run in CI).

    Creates a 15-second reel at 1080x1920 (approx 1.875s per frame).
    """
    sh_content = """#!/usr/bin/env bash
# make_reel.sh — Generate 15-second vertical 1080x1920 MP4 video from reel frames.
# Manual execution by the owner using ffmpeg if installed.
# DO NOT RUN IN CI.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_FILE="${SCRIPT_DIR}/reel.mp4"

if ! command -v ffmpeg &> /dev/null; then
    echo "Error: ffmpeg is not installed on this system." >&2
    echo "Install ffmpeg to generate the video, or upload the PNG frames directly." >&2
    exit 1
fi

echo "Creating 15s reel video at 1080x1920..."
# 8 frames over 15 seconds -> 8 / 15 fps = 0.53333 fps
ffmpeg -y -framerate 8/15 -i "${SCRIPT_DIR}/frame-%d.png" \\
    -c:v libx264 -r 30 -pix_fmt yuv420p -vf "scale=1080:1920" \\
    "${OUTPUT_FILE}"

echo "Reel created successfully: ${OUTPUT_FILE}"
"""
    return sh_content


def get_pack_images(pack_dir: Path) -> Tuple[Optional[Path], List[Path]]:
    cover_path = pack_dir / "cover.png"
    if not cover_path.is_file():
        covs = sorted([p for p in pack_dir.iterdir() if "cover" in p.name.lower() and p.suffix.lower() == ".png"])
        cover_path = covs[0] if covs else None

    previews = sorted([
        p for p in pack_dir.iterdir()
        if p.name.lower().startswith("preview") and p.suffix.lower() == ".png"
    ])
    return cover_path, previews


def make_pack_reel(
    pack_dir: Path,
    marketing_dir: Optional[Path] = None,
) -> Path:
    """Generate 8-frame reel, reel.txt, and make_reel.sh for one ready pack.

    Idempotent, atomic writes into exported_content/marketing/<pack>/reel/.
    Never modifies or touches pack_dir.
    """
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"Pack folder does not exist: {pack_dir}")

    pack_id = pack_dir.name
    if marketing_dir is None:
        target_dir = Path("exported_content") / "marketing" / pack_id / "reel"
    else:
        target_dir = Path(marketing_dir) / pack_id / "reel"

    listing_file = pack_dir / "listing.txt"
    if not listing_file.is_file():
        listing_file = pack_dir / "listing.md"

    info = parse_listing_file(listing_file)
    title = info["title"] or pack_id.replace("-", " ").title()
    tags = info["tags"]
    desc = info["description"]
    benefits = info["benefits"]

    default_benefits = [
        "Instant digital download ready to use immediately",
        "Clean, intentional layout designed for daily clarity",
        "Customizable format tailored to your workflow",
        "Step-by-step guidance and daily progress tracking",
        "High quality printable pages designed for binder or desk use",
    ]
    while len(benefits) < 5:
        benefits.append(default_benefits[len(benefits)])

    cover_path, previews = get_pack_images(pack_dir)
    theme = pick_reel_theme(cover_path)

    frames: List[Any] = []
    # 1: Hook
    frames.append(draw_frame_1_hook(title, pack_id, theme, cover_path))

    # 2-6: 5 Benefits
    for i in range(5):
        preview_img = previews[i] if i < len(previews) else (previews[0] if previews else cover_path)
        frames.append(draw_frame_benefit(i + 2, i + 1, benefits[i], preview_img, cover_path, theme))

    # 7: How it looks printed
    print_preview = previews[-1] if previews else cover_path
    frames.append(draw_frame_7_printed(title, print_preview, cover_path, theme))

    # 8: CTA
    frames.append(draw_frame_8_cta(title, pack_id, theme, cover_path))

    caption_text = build_reel_caption(title, desc, tags)
    sh_content = build_make_reel_sh()

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_target = Path(tempfile.mkdtemp(prefix=".reel-", dir=target_dir.parent))

    try:
        for idx, frame_im in enumerate(frames, 1):
            frame_file = temp_target / f"frame-{idx}.png"
            frame_im.save(str(frame_file), "PNG", optimize=True)
            if frame_file.stat().st_size > MAX_FILE_BYTES:
                quantized = frame_im.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
                quantized.save(str(frame_file), "PNG", optimize=True)

        (temp_target / "reel.txt").write_text(caption_text, encoding="utf-8")
        sh_file = temp_target / "make_reel.sh"
        sh_file.write_text(sh_content, encoding="utf-8")
        # Ensure executable permissions for owner
        sh_file.chmod(0o755)

        if target_dir.exists():
            old_backup = target_dir.parent / f".reel-old-{os.getpid()}"
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
    upload_dir = base_products_dir / "upload" if (base_products_dir / "upload").is_dir() else base_products_dir
    if not upload_dir.is_dir():
        return []
    return sorted([p for p in upload_dir.iterdir() if p.is_dir() and not p.name.startswith(".")])


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", help="Specific pack ID to generate reel for.")
    parser.add_argument("--all", action="store_true", help="Generate reels for all ready packs.")
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
        cand1 = prod_dir / "upload" / args.pack
        cand2 = prod_dir / args.pack
        pack_path = cand1 if cand1.is_dir() else cand2
        if not pack_path.is_dir():
            print(f"Error: Pack '{args.pack}' not found in {prod_dir}.", file=sys.stderr)
            return 1
        out = make_pack_reel(pack_path, mkt_dir)
        print(f"Reel frames created for pack {args.pack} -> {out}")
        return 0

    if args.all:
        packs = discover_upload_packs(prod_dir)
        if not packs:
            print(f"No ready upload packs found in {prod_dir}.", file=sys.stderr)
            return 0
        count = 0
        for p in packs:
            out = make_pack_reel(p, mkt_dir)
            print(f"Reel frames created for pack {p.name} -> {out}")
            count += 1
        print(f"Successfully generated {count} reels.")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
