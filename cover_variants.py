"""cover_variants.py — 3 cover designs per ready pack (1280x720 PNG, Pillow only).

Generates 3 distinct cover variants and metadata for each ready upload pack:
  - Variant A: Big title + fanned pages from preview images.
  - Variant B: Split colour block + one page.
  - Variant C: Minimal frame.

Outputs are written outside the private pack folders to:
  exported_content/marketing/<pack>/covers/cover_a.png
  exported_content/marketing/<pack>/covers/cover_b.png
  exported_content/marketing/<pack>/covers/cover_c.png
  exported_content/marketing/<pack>/covers/covers.json

Rules:
  - Output dimensions: 1280x720 PNG
  - Safe margin >= 60px
  - Contrast ratio >= 4.5:1 (WCAG AA)
  - Text never cut (shrink/wrap down to fit within boundaries)
  - File size < 1MB each
  - Deterministic outputs and atomic writes
  - Reuses font helpers from mockups.py by import
  - No money amounts, no currency patterns, no brand logos/names
  - Nothing auto-posts or publishes
  - Pack folder is strictly read-only and never modified
  - Owner-facing messages in Tenglish house style
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageFont = None  # type: ignore
    HAS_PILLOW = False

# Import font helpers and utilities from mockups.py
import mockups

COVER_WIDTH = 1280
COVER_HEIGHT = 720
SAFE_MARGIN = 60
MIN_CONTRAST = 4.5
MAX_FILE_BYTES = 1024 * 1024  # 1 MB

# Disallowed brand names
_BRANDS = re.compile(
    r"\b(google\s+sheets?|google\s+docs?|microsoft\s+excel|microsoft\s+word|excel|notion|canva|etsy|gumroad|payhip|goodnotes|apple\s+numbers|ipad|amazon|chatgpt|openai|microsoft|google|linkedin)\b",
    re.IGNORECASE,
)

# Currency and money patterns
_MONEY_REGEX = re.compile(
    r"(?:\$|₹|£|€|\b(?:USD|INR|EUR|GBP|AUD|CAD|NZD|SGD|HKD|JPY|CNY|RMB|AED|CHF|ZAR|PKR|BDT|NGN|PHP|KRW|RUB|BRL|MXN|Rs\.?|rupees?|dollars?|euros?|pounds?|yen|cents?|paise|paisa)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|£|€|\b(?:USD|INR|EUR|GBP|AUD|CAD|NZD|SGD|HKD|JPY|CNY|RMB|AED|CHF|ZAR|PKR|BDT|NGN|PHP|KRW|RUB|BRL|MXN|Rs\.?|rupees?|dollars?|euros?|pounds?|yen|cents?|paise|paisa)\b)",
    re.IGNORECASE,
)


def clean_text(text: str) -> str:
    """Scrub brand names, currency amounts, symbols and normalize spaces."""
    if not text:
        return ""
    # Unicode normalize
    norm = unicodedata.normalize("NFKD", str(text))
    # Remove currency matches
    norm = _MONEY_REGEX.sub("", norm)
    # Remove currency symbols ($ ₹ £ € ¥ etc.)
    norm = re.sub(r"[\$₹£€¥]", "", norm)
    # Remove brands
    norm = _BRANDS.sub("", norm)
    # Drop markdown formatting tokens
    norm = re.sub(r"[\*\_\#\`\[\]\(\)]", " ", norm)
    # Collapse whitespace
    norm = re.sub(r"\s+", " ", norm).strip(" -|,:")
    return norm


def relative_luminance(rgb: Tuple[int, int, int]) -> float:
    """Calculate WCAG relative luminance."""
    lum = []
    for c in rgb[:3]:
        v = c / 255.0
        v = v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        lum.append(v)
    return 0.2126 * lum[0] + 0.7152 * lum[1] + 0.0722 * lum[2]


def contrast_ratio(rgb1: Tuple[int, int, int], rgb2: Tuple[int, int, int]) -> float:
    """Calculate WCAG contrast ratio (>= 1.0)."""
    l1 = relative_luminance(rgb1)
    l2 = relative_luminance(rgb2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def fit_text_custom(
    text: str,
    weight: str,
    max_size: int,
    min_size: int,
    max_w: float,
    max_lines: int,
) -> Tuple[List[str], Any]:
    """Reuses mockups.fit and mockups.wrap to find font size that wraps within max_lines and max_w.
    Never cuts: shrinks down to min_size or lower if necessary to prevent overflowing max_w.
    """
    cleaned = clean_text(text)
    if not cleaned:
        cleaned = "Template"

    lines, f = mockups.fit(cleaned, weight, max_size, min_size, int(max_w), max_lines)
    # If at min_size lines still exceed max_w or max_lines, continue stepping down
    cur_size = min_size
    while (len(lines) > max_lines or any(mockups.text_w(l, f) > max_w for l in lines)) and cur_size > 10:
        cur_size -= 2
        f = mockups.font(weight, cur_size)
        lines = mockups.wrap(cleaned, f, int(max_w))

    return lines, f


def parse_pack_listing(pack_dir: Path) -> Dict[str, Any]:
    """Read pack metadata without modifying pack folder."""
    pack_dir = Path(pack_dir).resolve()
    listing_file = pack_dir / "listing.txt"
    if not listing_file.is_file():
        listing_file = pack_dir / "listing.md"

    raw_title = ""
    raw_desc = ""
    raw_tags: List[str] = []
    bullets: List[str] = []

    if listing_file.is_file():
        text = listing_file.read_text(encoding="utf-8-sig", errors="ignore")
        in_desc = False
        in_what = False

        for line in text.splitlines():
            s = line.strip()
            sl = s.lower()
            if sl.startswith("title:"):
                raw_title = s.split(":", 1)[1].strip()
                in_desc = False
                in_what = False
            elif sl.startswith("tags:"):
                raw_tags = [t.strip() for t in s.split(":", 1)[1].split(",") if t.strip()]
                in_desc = False
                in_what = False
            elif sl.startswith("description:"):
                in_desc = True
                in_what = False
                p = s.split(":", 1)[1].strip()
                if p:
                    raw_desc = p
            elif sl.startswith("what you get:"):
                in_what = True
                in_desc = False
            elif in_desc:
                if sl.startswith("tags:") or sl.startswith("checklist:") or sl.startswith("files:") or sl.startswith("what you get:"):
                    in_desc = False
                else:
                    if s:
                        raw_desc = f"{raw_desc} {s}".strip() if raw_desc else s
            elif in_what:
                if s.startswith("- ") or s.startswith("* "):
                    bullets.append(s[2:].strip())
                elif s and not sl.startswith("tags:"):
                    bullets.append(s)
                elif sl.startswith("tags:"):
                    in_what = False

    title = clean_text(raw_title)
    if not title:
        title = clean_text(pack_dir.name.replace("-", " ").title())
    if not title:
        title = "Printable Planner Template"

    desc = clean_text(raw_desc)
    if not desc:
        desc = f"Clean, practical printable {title.lower()} designed for organized daily routines."

    clean_bullets = [clean_text(b) for b in bullets if clean_text(b)]
    if not clean_bullets:
        clean_bullets = [
            "Instant printable template",
            "Clear and organized layout",
            "Designed for everyday productivity",
        ]

    # Collect images
    cover_path: Optional[Path] = None
    preview_paths: List[Path] = []
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        for img_file in sorted(pack_dir.glob(f"*{ext}")):
            name_l = img_file.name.lower()
            if "cover" in name_l:
                if not cover_path:
                    cover_path = img_file
            elif "preview" in name_l or "pin" in name_l:
                preview_paths.append(img_file)
            else:
                if not cover_path:
                    cover_path = img_file
                else:
                    preview_paths.append(img_file)

    if not cover_path and preview_paths:
        cover_path = preview_paths.pop(0)

    return {
        "pack_name": pack_dir.name,
        "title": title,
        "description": desc,
        "tags": raw_tags,
        "bullets": clean_bullets,
        "cover_path": cover_path,
        "preview_paths": preview_paths,
    }


def create_fallback_page(title: str, size: Tuple[int, int] = (500, 700)) -> Any:
    """Generate a crisp, realistic mock page for preview/fanning if image not present."""
    w, h = size
    page = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(page)

    # Accent top banner
    accent = (47, 125, 120)
    d.rectangle([0, 0, w, 80], fill=accent)
    f_head = mockups.font("bold", 24)
    lines, f_head = fit_text_custom(title, "bold", 24, 16, w - 40, 2)
    y = 20
    for l in lines:
        d.text((20, y), l, font=f_head, fill=(255, 255, 255))
        y += mockups._line_h(f_head)

    # Lined content
    for line_y in range(120, h - 40, 40):
        d.line([(25, line_y), (w - 25, line_y)], fill=(226, 232, 240), width=2)
        d.rectangle([25, line_y - 18, 37, line_y - 6], outline=(148, 163, 184), width=1)

    # Outer border
    d.rectangle([0, 0, w - 1, h - 1], outline=(203, 213, 225), width=1)
    return page


def load_page_image(path: Optional[Path], fallback_title: str, size: Tuple[int, int]) -> Any:
    """Load image from disk and resize/contain, or generate fallback mock page."""
    if path and path.is_file():
        try:
            with Image.open(path) as img:
                im = img.convert("RGB")
                # Thumbnail to fit inside size maintaining aspect ratio
                im.thumbnail(size, Image.Resampling.LANCZOS)
                padded = Image.new("RGB", size, (255, 255, 255))
                offset = ((size[0] - im.width) // 2, (size[1] - im.height) // 2)
                padded.paste(im, offset)
                d = ImageDraw.Draw(padded)
                d.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(203, 213, 225), width=1)
                return padded
        except Exception:
            pass
    return create_fallback_page(fallback_title, size)


def draw_tilted_shadow_page(
    canvas: Any,
    page: Any,
    center: Tuple[int, int],
    angle: float,
    shadow: bool = True,
):
    """Draw tilted page with a smooth drop shadow onto canvas."""
    page_rgba = page.convert("RGBA")
    rot = page_rgba.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
    x = int(center[0] - rot.width / 2)
    y = int(center[1] - rot.height / 2)

    if shadow:
        alpha = rot.split()[3].point(lambda a: int(a * 0.26))
        sh = Image.new("RGBA", rot.size, (20, 30, 35, 0))
        sh.putalpha(alpha)
        pad = 30
        big = Image.new("RGBA", (rot.width + 2 * pad, rot.height + 2 * pad), (0, 0, 0, 0))
        big.paste(sh, (pad, pad))
        big = big.filter(ImageFilter.GaussianBlur(12))
        mockups._composite_clip(canvas, big, x - pad + 6, y - pad + 10)

    mockups._composite_clip(canvas, rot, x, y)


# =========================================================================
# VARIANT A: Big Title + Fanned Pages From Preview Images
# =========================================================================

def render_variant_a(
    title: str,
    pack_name: str,
    preview_pages: List[Any],
) -> Any:
    """Variant A: Big title on left/top + 3 fanned pages from preview images on right."""
    bg_color = (248, 250, 252)
    accent_color = (47, 125, 120)  # Contrast >= 4.5
    ink_color = (15, 23, 42)
    sub_ink = (71, 85, 105)

    canvas = Image.new("RGBA", (COVER_WIDTH, COVER_HEIGHT), bg_color + (255,))
    d = ImageDraw.Draw(canvas)

    # Subtle background accent circle behind fanned pages
    d.ellipse([700, -50, 1400, 750], fill=(230, 242, 241, 255))

    # Left content block
    max_title_w = 520
    lines, f_title = fit_text_custom(title, "bold", 54, 28, max_title_w, 3)

    # Calculate layout y
    y = max(SAFE_MARGIN, 90)

    # Category / Label
    cat_text = "PRINTABLE TEMPLATE"
    f_cat = mockups.font("bold", 18)
    mockups.draw_text(d, (SAFE_MARGIN, y), cat_text, f_cat, accent_color, spacing=2)
    y += 36

    # Title lines
    for line in lines:
        mockups.draw_text(d, (SAFE_MARGIN, y), line, f_title, ink_color)
        y += mockups._line_h(f_title)

    # Accent decorative bar
    y += 12
    d.rectangle([SAFE_MARGIN, y, SAFE_MARGIN + 80, y + 6], fill=accent_color)
    y += 24

    # Tagline / Subtitle
    sub_text = "Instant digital download · Clean, practical layout"
    f_sub = mockups.font("semibold", 20)
    sub_lines = mockups.wrap(sub_text, f_sub, max_title_w)
    for s_line in sub_lines:
        mockups.draw_text(d, (SAFE_MARGIN, y), s_line, f_sub, sub_ink)
        y += mockups._line_h(f_sub)

    # Chips: 3 key features
    chips = ["Print at home", "Instant access", "Undated template"]
    f_chip = mockups.font("semibold", 16)
    chip_x = SAFE_MARGIN
    chip_y = min(y + 30, COVER_HEIGHT - SAFE_MARGIN - 40)
    for c in chips:
        w_c = int(mockups.text_w(c, f_chip)) + 24
        if chip_x + w_c <= SAFE_MARGIN + max_title_w:
            d.rounded_rectangle([chip_x, chip_y, chip_x + w_c, chip_y + 36], radius=18, fill=(255, 255, 255), outline=accent_color, width=1)
            mockups.draw_text(d, (chip_x + 12, chip_y + 8), c, f_chip, ink_color)
            chip_x += w_c + 10

    # Right side: 3 fanned pages
    # Ensure we have 3 pages
    pages = list(preview_pages)
    while len(pages) < 3:
        pages.append(pages[0] if pages else create_fallback_page(title, (380, 520)))

    p1 = pages[0].resize((370, 510), Image.Resampling.LANCZOS)
    p2 = pages[1].resize((370, 510), Image.Resampling.LANCZOS)
    p3 = pages[2].resize((370, 510), Image.Resampling.LANCZOS)

    # Fan out: back-left (-10 deg), back-right (+10 deg), center (0 deg)
    draw_tilted_shadow_page(canvas, p1, (860, 370), angle=-10)
    draw_tilted_shadow_page(canvas, p2, (1080, 380), angle=10)
    draw_tilted_shadow_page(canvas, p3, (970, 360), angle=0)

    # Footer note at bottom left
    d = ImageDraw.Draw(canvas)
    f_foot = mockups.font("regular", 14)
    clean_pname = clean_text(pack_name.replace("-", " ").title())
    mockups.draw_text(d, (SAFE_MARGIN, COVER_HEIGHT - SAFE_MARGIN + 15), f"Ready to print · {clean_pname}", f_foot, sub_ink)

    return canvas.convert("RGB")


# =========================================================================
# VARIANT B: Split Colour Block + One Page
# =========================================================================

def render_variant_b(
    title: str,
    pack_name: str,
    page: Any,
    bullets: Optional[List[str]] = None,
) -> Any:
    """Variant B: Split colour block on left half with deep tone, one prominent page on right half."""
    left_bg = (30, 41, 59)       # Deep slate navy
    right_bg = (241, 245, 249)    # Light gray-blue
    accent = (45, 212, 191)       # Vibrant mint / teal (contrast on dark bg: > 8:1)
    title_text_color = (255, 255, 255)
    body_text_color = (226, 232, 240)

    canvas = Image.new("RGBA", (COVER_WIDTH, COVER_HEIGHT), right_bg + (255,))
    d = ImageDraw.Draw(canvas)

    # Left block (covers width of 600px)
    split_w = 580
    d.rectangle([0, 0, split_w, COVER_HEIGHT], fill=left_bg)

    # Decorative accent stripe at vertical split
    d.rectangle([split_w - 4, 0, split_w, COVER_HEIGHT], fill=accent)

    # Left content: Title and bullets
    y = max(SAFE_MARGIN, 85)
    badge_text = "OFFICIAL TEMPLATE"
    f_badge = mockups.font("bold", 16)
    mockups.draw_text(d, (SAFE_MARGIN, y), badge_text, f_badge, accent, spacing=2)
    y += 36

    max_title_w = split_w - SAFE_MARGIN - 40
    lines, f_title = fit_text_custom(title, "bold", 48, 26, max_title_w, 3)
    for l in lines:
        mockups.draw_text(d, (SAFE_MARGIN, y), l, f_title, title_text_color)
        y += mockups._line_h(f_title)

    y += 24
    # Bullets with tick marks
    ticks = bullets[:3] if bullets else [
        "Practical and easy to follow",
        "Clean undated layout for repeat use",
        "Home and office printing ready",
    ]

    f_tick = mockups.font("regular", 18)
    for t in ticks:
        # Accent tick dot
        d.ellipse([SAFE_MARGIN, y + 4, SAFE_MARGIN + 12, y + 16], fill=accent)
        t_lines = mockups.wrap(t, f_tick, max_title_w - 24)
        for tl in t_lines:
            mockups.draw_text(d, (SAFE_MARGIN + 22, y), tl, f_tick, body_text_color)
            y += mockups._line_h(f_tick)
        y += 8

    # Right half: One prominent page, centered vertically
    p = page.resize((450, 620), Image.Resampling.LANCZOS)
    page_center = (split_w + (COVER_WIDTH - split_w) // 2, COVER_HEIGHT // 2)
    draw_tilted_shadow_page(canvas, p, page_center, angle=0)

    # Subtle badge on bottom right
    d = ImageDraw.Draw(canvas)
    clean_pname = clean_text(pack_name.replace("-", " ").title())
    f_lbl = mockups.font("semibold", 15)
    lbl_text = f"Includes PDF · {clean_pname}"
    w_lbl = mockups.text_w(lbl_text, f_lbl)
    mockups.draw_text(d, (COVER_WIDTH - SAFE_MARGIN - w_lbl, COVER_HEIGHT - SAFE_MARGIN + 15), lbl_text, f_lbl, (100, 116, 139))

    return canvas.convert("RGB")


# =========================================================================
# VARIANT C: Minimal Frame
# =========================================================================

def render_variant_c(
    title: str,
    pack_name: str,
    page: Any,
) -> Any:
    """Variant C: Minimal elegant frame, centered typography, clean aesthetic."""
    bg_color = (255, 255, 255)
    frame_color = (226, 232, 240)
    ink_color = (15, 23, 42)
    accent_color = (47, 125, 120)
    sub_ink = (100, 116, 139)

    canvas = Image.new("RGBA", (COVER_WIDTH, COVER_HEIGHT), bg_color + (255,))
    d = ImageDraw.Draw(canvas)

    # Double outer minimal frame with safe margin
    d.rectangle([SAFE_MARGIN, SAFE_MARGIN, COVER_WIDTH - SAFE_MARGIN, COVER_HEIGHT - SAFE_MARGIN], outline=frame_color, width=2)
    d.rectangle([SAFE_MARGIN + 12, SAFE_MARGIN + 12, COVER_WIDTH - SAFE_MARGIN - 12, COVER_HEIGHT - SAFE_MARGIN - 12], outline=frame_color, width=1)

    # Left area for typography (width 540)
    left_w = 540
    y = SAFE_MARGIN + 50

    # Minimal label
    cat_text = "MINIMAL COLLECTION"
    f_cat = mockups.font("bold", 16)
    mockups.draw_text(d, (SAFE_MARGIN + 40, y), cat_text, f_cat, accent_color, spacing=3)
    y += 35

    # Main title
    lines, f_title = fit_text_custom(title, "bold", 50, 26, left_w - 60, 3)
    for l in lines:
        mockups.draw_text(d, (SAFE_MARGIN + 40, y), l, f_title, ink_color)
        y += mockups._line_h(f_title)

    y += 16
    d.line([(SAFE_MARGIN + 40, y), (SAFE_MARGIN + 140, y)], fill=accent_color, width=2)
    y += 24

    # Short description
    desc_text = "Designed with generous margins, crisp typography, and an uncluttered structure to bring clarity to your day."
    f_desc = mockups.font("regular", 18)
    desc_lines = mockups.wrap(desc_text, f_desc, left_w - 60)
    for dl in desc_lines[:3]:
        mockups.draw_text(d, (SAFE_MARGIN + 40, y), dl, f_desc, sub_ink)
        y += mockups._line_h(f_desc)

    # Key metadata chips
    y = max(y + 20, COVER_HEIGHT - SAFE_MARGIN - 80)
    f_meta = mockups.font("semibold", 15)
    mockups.draw_text(d, (SAFE_MARGIN + 40, y), "A4 & US Letter · Printable · Undated", f_meta, ink_color)

    # Right side: Single framed preview page tilted slightly
    p = page.resize((430, 580), Image.Resampling.LANCZOS)
    draw_tilted_shadow_page(canvas, p, (930, COVER_HEIGHT // 2), angle=-2)

    return canvas.convert("RGB")


# =========================================================================
# PACK GENERATION ORCHESTRATION
# =========================================================================

def discover_upload_packs(base_products_dir: Path | str = "exported_content/products") -> List[Path]:
    """Find all ready packs under base_products_dir/upload or base_products_dir.
    A pack is ready if it's a directory containing listing.txt (or listing.md).
    """
    base = Path(base_products_dir)
    upload_dir = base / "upload" if (base / "upload").is_dir() else base
    if not upload_dir.is_dir():
        return []

    packs = sorted([
        p for p in upload_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".") and ((p / "listing.txt").is_file() or (p / "listing.md").is_file())
    ])
    return packs


def make_pack_covers(
    pack_dir: Path | str,
    marketing_base: Path | str = "exported_content/marketing",
) -> Dict[str, Any]:
    """Generate 3 cover variants (1280x720 PNG) and covers.json for a ready upload pack.

    Outputs are written to:
      <marketing_base>/<pack>/covers/cover_a.png
      <marketing_base>/<pack>/covers/cover_b.png
      <marketing_base>/<pack>/covers/cover_c.png
      <marketing_base>/<pack>/covers/covers.json

    Writes atomically via temporary directory outside pack. Never touches pack folder.
    """
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for generating cover variants.")

    pack_dir = Path(pack_dir).resolve()
    if not pack_dir.is_dir():
        raise ValueError(f"Pack directory not found: {pack_dir}")

    info = parse_pack_listing(pack_dir)
    pack_name = info["pack_name"]
    title = info["title"]
    bullets = info["bullets"]

    # Target output folder
    target_covers_dir = Path(marketing_base).resolve() / pack_name / "covers"
    target_covers_dir.mkdir(parents=True, exist_ok=True)

    # Load or create preview pages
    cover_page = load_page_image(info["cover_path"], title, (480, 660))

    preview_pages: List[Any] = []
    if info["preview_paths"]:
        for p_path in info["preview_paths"][:3]:
            preview_pages.append(load_page_image(p_path, title, (480, 660)))

    while len(preview_pages) < 3:
        preview_pages.append(cover_page)

    # Render the 3 variants
    variant_a = render_variant_a(title, pack_name, preview_pages)
    variant_b = render_variant_b(title, pack_name, cover_page, bullets)
    variant_c = render_variant_c(title, pack_name, cover_page)

    variants_data = [
        {"file": "cover_a.png", "layout": "A", "title": title, "img": variant_a},
        {"file": "cover_b.png", "layout": "B", "title": title, "img": variant_b},
        {"file": "cover_c.png", "layout": "C", "title": title, "img": variant_c},
    ]

    # Atomic write pattern: write all to temp_dir, then move/replace into target
    temp_dir = Path(tempfile.mkdtemp(prefix=f".covers-{pack_name}-", dir=str(target_covers_dir.parent)))
    json_meta = []

    try:
        for item in variants_data:
            file_name = item["file"]
            dest_file = temp_dir / file_name
            im = item["img"]

            # Save PNG optimized
            im.save(dest_file, format="PNG", optimize=True)

            # Enforce < 1MB
            if dest_file.stat().st_size >= MAX_FILE_BYTES:
                quantized = im.quantize(colors=256)
                quantized.save(dest_file, format="PNG", optimize=True)

            json_meta.append({
                "file": file_name,
                "layout": item["layout"],
                "title": item["title"],
            })

        # Write covers.json
        (temp_dir / "covers.json").write_text(json.dumps(json_meta, indent=2), encoding="utf-8")

        # Atomic move to target
        for src in temp_dir.iterdir():
            target = target_covers_dir / src.name
            if target.exists():
                target.unlink()
            src.rename(target)

    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    # Return summary dict
    return {
        "pack": pack_name,
        "covers_dir": str(target_covers_dir),
        "covers": json_meta,
        "message": f"Mee {pack_name} pack kosam 3 cover designs ready ayyayi ✓",
    }


def main(argv: Optional[List[str]] = None) -> int:
    """CLI handler with Tenglish house style messages."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", help="Specific pack ID to generate covers for.")
    parser.add_argument("--all", action="store_true", help="Generate covers for all ready upload packs.")
    parser.add_argument(
        "--products-dir",
        default="exported_content/products",
        help="Products directory (default: exported_content/products).",
    )
    parser.add_argument(
        "--marketing-dir",
        default="exported_content/marketing",
        help="Marketing directory for output (default: exported_content/marketing).",
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
            # Tenglish error message
            print(f"Error: {args.pack} pack folder dorakaledu in {prod_dir}.", file=sys.stderr)
            return 1
        res = make_pack_covers(pack_path, mkt_dir)
        print(res["message"])
        print(f"Cover variants created: {res['covers_dir']}")
        return 0

    if args.all:
        packs = discover_upload_packs(prod_dir)
        if not packs:
            print(f"Ready upload packs emi levu in {prod_dir}.", file=sys.stderr)
            return 0
        count = 0
        for p in packs:
            res = make_pack_covers(p, mkt_dir)
            print(res["message"])
            count += 1
        print(f"Mothaniki {count} packs kosam covers tayaru ayyayi ✓")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
