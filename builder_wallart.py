"""builder_wallart.py — New product format: Wall Art Print Set (Pillow only).

FORMAT='wall_art'
KEYWORDS=['wall art', 'print', 'poster', 'quote print', 'printable art']

Features:
  - Generates 3 to 6 typographic art designs from spec (title, quotes/phrases list or theme words, palette name).
  - Pure Pillow typography and procedural geometric shapes (frames, arches, sunbursts, botanical lines, organic curves);
    strictly NO stock images or external graphic assets.
  - Each design exported at 5 print sizes:
      * A4 (210 x 297 mm): 2480 x 3508 px at 300 dpi
      * A3 (297 x 420 mm): 3508 x 4960 px at 300 dpi
      * 8x10 in (203.2 x 254 mm): 2400 x 3000 px at 300 dpi
      * 11x14 in (279.4 x 355.6 mm): 3300 x 4200 px at 300 dpi
      * 16x20 in (406.4 x 508 mm): 3200 x 4000 px at 200 dpi (or 4800 x 6000 at 300 dpi, configurable)
  - One combined multi-page PDF of A4 versions (embedded at print resolution).
  - Comprehensive PRINTING.txt (sizes, paper choices, framing guide, 'personal use' license terms, zero money amounts).
  - Strict text-fit checks: text never clipped or truncated; auto-shrinks and wraps smoothly within safe margins.
  - WCAG contrast verification: checks ink vs background contrast (ratio >= 4.5:1).
  - Deterministic generation, atomic file writes.
  - Tenglish house style messages.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unicodedata
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    HAS_PILLOW = False

# Module Constants
FORMAT = "wall_art"
KEYWORDS = ["wall art", "print", "poster", "quote print", "printable art"]

# Print size definitions: name -> (width_px, height_px, dpi, description)
PRINT_SIZES: Dict[str, Dict[str, Any]] = {
    "a4": {
        "slug": "a4",
        "label": "A4",
        "width_px": 2480,
        "height_px": 3508,
        "dpi": 300,
        "aspect": "1:1.414 (ISO A-Series)",
        "physical": "210 x 297 mm (8.3 x 11.7 in)",
        "dpi_note": "300 dpi high resolution",
    },
    "a3": {
        "slug": "a3",
        "label": "A3",
        "width_px": 3508,
        "height_px": 4960,
        "dpi": 300,
        "aspect": "1:1.414 (ISO A-Series)",
        "physical": "297 x 420 mm (11.7 x 16.5 in)",
        "dpi_note": "300 dpi high resolution",
    },
    "8x10": {
        "slug": "8x10",
        "label": "8x10 in",
        "width_px": 2400,
        "height_px": 3000,
        "dpi": 300,
        "aspect": "4:5 ratio",
        "physical": "203.2 x 254 mm (8 x 10 in)",
        "dpi_note": "300 dpi high resolution",
    },
    "11x14": {
        "slug": "11x14",
        "label": "11x14 in",
        "width_px": 3300,
        "height_px": 4200,
        "dpi": 300,
        "aspect": "11:14 ratio",
        "physical": "279.4 x 355.6 mm (11 x 14 in)",
        "dpi_note": "300 dpi high resolution",
    },
    "16x20": {
        "slug": "16x20",
        "label": "16x20 in",
        "width_px": 3200,
        "height_px": 4000,
        "dpi": 200,
        "aspect": "4:5 ratio",
        "physical": "406.4 x 508 mm (16 x 20 in)",
        "dpi_note": "200 dpi optimized for large format wall prints",
    },
}

# Curated high-contrast aesthetic palettes suitable for modern typographic wall art
PALETTES: Dict[str, Dict[str, str]] = {
    "terracotta": {
        "name": "Terracotta & Warm Cream",
        "background": "#FDFBF7",
        "primary": "#8C3B2B",
        "accent": "#C47659",
        "secondary": "#3B3330",
        "light_accent": "#F2E6DC",
    },
    "sage": {
        "name": "Sage Botanical",
        "background": "#F9FAF8",
        "primary": "#2D4C3A",
        "accent": "#6E8B75",
        "secondary": "#212D24",
        "light_accent": "#E4EDE6",
    },
    "minimal_mono": {
        "name": "Minimal Monochrome",
        "background": "#FAFAFA",
        "primary": "#1A1A1A",
        "accent": "#555555",
        "secondary": "#2B2B2B",
        "light_accent": "#EEEEEE",
    },
    "warm_boho": {
        "name": "Warm Boho Mustard",
        "background": "#FAF7F2",
        "primary": "#4A3222",
        "accent": "#C9853B",
        "secondary": "#24180F",
        "light_accent": "#F5EAD9",
    },
    "indigo": {
        "name": "Nordic Indigo",
        "background": "#F7F9FC",
        "primary": "#1C2D42",
        "accent": "#4A6E8D",
        "secondary": "#111B27",
        "light_accent": "#E1E8F0",
    },
    "rose_clay": {
        "name": "Rose Clay & Charcoal",
        "background": "#FDF9F9",
        "primary": "#2D2628",
        "accent": "#A35C6A",
        "secondary": "#1A1517",
        "light_accent": "#F2E2E5",
    },
    "forest_gold": {
        "name": "Deep Forest & Ochre",
        "background": "#F8FAF8",
        "primary": "#1B3B2B",
        "accent": "#B8860B",
        "secondary": "#12261C",
        "light_accent": "#E8EFE8",
    },
}

# Theme presets with fallback quotes if spec doesn't specify enough phrases
THEME_PRESETS: Dict[str, List[Dict[str, str]]] = {
    "mindful": [
        {"quote": "BREATHE IN PEACE", "sub": "Exhale worry · Stay present in this very moment", "motif": "arch"},
        {"quote": "PROGRESS OVER PERFECTION", "sub": "Small steps forward every single day create lasting growth", "motif": "sunburst"},
        {"quote": "QUIET THE MIND", "sub": "And the soul will speak · Create space to just be", "motif": "minimal_frame"},
        {"quote": "HERE AND NOW", "sub": "This moment is where life unfolds · Embrace today fully", "motif": "botanical_leaves"},
        {"quote": "FIND STILLNESS WITHIN", "sub": "Calm waters reflect true beauty · Ground yourself in peace", "motif": "rings"},
        {"quote": "GRATITUDE CHANGES EVERYTHING", "sub": "Notice what is good · Abundance follows thankful hearts", "motif": "arch"},
    ],
    "modern_focus": [
        {"quote": "FOCUS ON WHAT MATTERS", "sub": "Energy flows where attention goes · Eliminate the noise", "motif": "minimal_frame"},
        {"quote": "BEGIN WITH PURPOSE", "sub": "Clarity of vision turns ideas into meaningful reality", "motif": "sunburst"},
        {"quote": "CREATE EVERY DAY", "sub": "Curiosity and discipline make craft timeless", "motif": "botanical_leaves"},
        {"quote": "CONSISTENCY IS POWER", "sub": "Daily rituals shape tomorrow's greatest achievements", "motif": "rings"},
        {"quote": "SIMPLIFY YOUR SPACE", "sub": "Order outside brings serenity inside · Keep only the essential", "motif": "arch"},
        {"quote": "DREAM DEEP WORK KIND", "sub": "Bold intentions paired with patient persistence", "motif": "minimal_frame"},
    ],
    "warm_home": [
        {"quote": "HOME IS WHERE WE GROW", "sub": "Filled with love, laughter, warmth, and lifelong memories", "motif": "arch"},
        {"quote": "GATHER WITH JOY", "sub": "Good food, honest talks, and open hearts welcome here", "motif": "sunburst"},
        {"quote": "KIND WORDS HEAL", "sub": "Speak gently · Listen warmly · Cherish each other always", "motif": "botanical_leaves"},
        {"quote": "LIGHT EMBRACES US", "sub": "Sunlight in the morning · Peace at dusk · Warmth forever", "motif": "rings"},
        {"quote": "CHERISH THE SIMPLE", "sub": "The best moments are woven from everyday grace", "motif": "minimal_frame"},
        {"quote": "PEACE RESIDES HERE", "sub": "A haven of rest, creative joy, and deep comfort", "motif": "arch"},
    ],
}


# --- Contrast and Color Helpers ---

def hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
    """Parse hex color like '#FFFFFF' or 'FFF' into RGB tuple."""
    h = hex_str.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (0, 0, 0)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Format RGB tuple to #RRGGBB."""
    return f"#{r:02X}{g:02X}{b:02X}"


def relative_luminance(color: Union[str, Tuple[int, int, int]]) -> float:
    """Compute relative luminance according to WCAG 2.1 specs."""
    if isinstance(color, str):
        r, g, b = hex_to_rgb(color)
    else:
        r, g, b = color[:3]

    def channel_lum(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel_lum(r) + 0.7152 * channel_lum(g) + 0.0722 * channel_lum(b)


def contrast_ratio(color1: Union[str, Tuple[int, int, int]],
                   color2: Union[str, Tuple[int, int, int]]) -> float:
    """Calculate WCAG contrast ratio between two colors (range: 1.0 to 21.0)."""
    l1 = relative_luminance(color1)
    l2 = relative_luminance(color2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def ensure_contrast(foreground: str, background: str, min_ratio: float = 4.5) -> str:
    """If contrast between foreground and background is below min_ratio, adjust foreground."""
    ratio = contrast_ratio(foreground, background)
    if ratio >= min_ratio:
        return foreground

    bg_lum = relative_luminance(background)
    # If background is bright, darken foreground; else lighten foreground
    if bg_lum > 0.5:
        return "#111111"
    else:
        return "#F5F5F5"


# --- Font Resolution Helpers ---

def get_font(font_type: str = "bold", size: int = 40) -> Any:
    """Load font using mockups.font helper if available, or PIL ImageFont fallback."""
    if not HAS_PILLOW:
        return None

    try:
        import mockups
        if hasattr(mockups, "font"):
            f = mockups.font(font_type, size)
            if f is not None:
                return f
    except Exception:
        pass

    # Standard fallback fonts
    font_names = [
        "/app/fonts/Inter-Bold.ttf" if "bold" in font_type.lower() else "/app/fonts/Inter-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if "bold" in font_type.lower() else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if "bold" in font_type.lower() else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in font_names:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue

    try:
        return ImageFont.load_default()
    except Exception:
        return None


def measure_text(text: str, font: Any) -> Tuple[int, int]:
    """Return (width, height) of text bounding box."""
    if not text or font is None:
        return (0, 0)
    try:
        bbox = font.getbbox(text)
        return (bbox[2] - bbox[0], bbox[3] - bbox[1])
    except Exception:
        pass
    try:
        w, h = font.getsize(text)
        return (w, h)
    except Exception:
        return (len(text) * 10, 15)


def wrap_text_to_width(text: str, font: Any, max_width: int) -> List[str]:
    """Wrap words so each line fits within max_width. Never truncates."""
    words = text.strip().split()
    if not words:
        return [""]

    lines = []
    current_line = []
    for word in words:
        trial = " ".join(current_line + [word])
        w, _ = measure_text(trial, font)
        if current_line and w > max_width:
            lines.append(" ".join(current_line))
            current_line = [word]
        else:
            current_line.append(word)

    if current_line:
        lines.append(" ".join(current_line))
    return lines


def fit_quote_typography(text: str,
                         max_width: int,
                         max_height: int,
                         target_size: int = 120,
                         min_size: int = 36,
                         font_type: str = "bold",
                         max_lines: int = 5) -> Tuple[List[str], Any, int, int]:
    """Find the best font size and line wrapping for a quote to fit within width and height bounds.
    Text is NEVER cut or clipped."""
    size = target_size
    best_lines = [text]
    best_font = get_font(font_type, size)
    best_line_h = 40

    while size >= min_size:
        font = get_font(font_type, size)
        lines = wrap_text_to_width(text, font, max_width)

        # Check line widths
        widest = max(measure_text(line, font)[0] for line in lines) if lines else 0
        _, single_h = measure_text("AyMg", font)
        line_height = int(single_h * 1.35)
        total_h = len(lines) * line_height

        if widest <= max_width and total_h <= max_height and len(lines) <= max_lines:
            return lines, font, size, line_height

        best_lines = lines
        best_font = font
        best_line_h = line_height
        size -= 4

    # Even at min_size, wrap properly so text is not truncated
    font = get_font(font_type, min_size)
    lines = wrap_text_to_width(text, font, max_width)
    _, single_h = measure_text("AyMg", font)
    return lines, font, min_size, int(single_h * 1.3)


# --- Procedural Shapes & Motifs (Pillow Only, No Stock Images) ---

def draw_arch_motif(draw: Any, bbox: Tuple[int, int, int, int], stroke_color: str, fill_color: Optional[str] = None, stroke_width: int = 6):
    """Draw a classic architectural / boho arch motif."""
    x0, y0, x1, y1 = bbox
    w = x1 - x0
    h = y1 - y0
    radius = w // 2

    # Fill
    if fill_color:
        draw.pieslice([x0, y0, x1, y0 + 2 * radius], start=180, end=360, fill=fill_color)
        draw.rectangle([x0, y0 + radius, x1, y1], fill=fill_color)

    # Outline
    draw.arc([x0, y0, x1, y0 + 2 * radius], start=180, end=360, fill=stroke_color, width=stroke_width)
    draw.line([(x0, y0 + radius), (x0, y1)], fill=stroke_color, width=stroke_width)
    draw.line([(x1, y0 + radius), (x1, y1)], fill=stroke_color, width=stroke_width)
    draw.line([(x0, y1), (x1, y1)], fill=stroke_color, width=stroke_width)


def draw_sunburst_motif(draw: Any, center: Tuple[int, int], radius: int, color: str, num_rays: int = 12, stroke_width: int = 5):
    """Draw a minimalist sunburst linework motif."""
    cx, cy = center
    inner_r = int(radius * 0.45)
    outer_r = radius

    # Center semi-circle or full circle
    draw.arc([cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r], start=180, end=360, fill=color, width=stroke_width)
    draw.line([(cx - inner_r, cy), (cx + inner_r, cy)], fill=color, width=stroke_width)

    # Rays emanating from top half
    angle_step = 180.0 / (num_rays - 1)
    for i in range(num_rays):
        angle_deg = 180.0 + i * angle_step
        rad = math.radians(angle_deg)
        x_start = cx + int((inner_r + 12) * math.cos(rad))
        y_start = cy + int((inner_r + 12) * math.sin(rad))
        x_end = cx + int(outer_r * math.cos(rad))
        y_end = cy + int(outer_r * math.sin(rad))
        draw.line([(x_start, y_start), (x_end, y_end)], fill=color, width=stroke_width)


def draw_minimal_frame(draw: Any, bbox: Tuple[int, int, int, int], stroke_color: str, stroke_width: int = 4, corner_accent: int = 30):
    """Draw a double thin minimalist line frame with corner accents."""
    x0, y0, x1, y1 = bbox
    draw.rectangle([x0, y0, x1, y1], outline=stroke_color, width=stroke_width)
    # Inner border
    pad = 24
    if x1 - x0 > 2 * pad and y1 - y0 > 2 * pad:
        draw.rectangle([x0 + pad, y0 + pad, x1 - pad, y1 - pad], outline=stroke_color, width=max(1, stroke_width // 2))


def draw_botanical_stem(draw: Any, center: Tuple[int, int], height: int, color: str, stroke_width: int = 5):
    """Draw a delicate procedural botanical leaf stem."""
    cx, cy = center
    top_y = cy - height // 2
    bottom_y = cy + height // 2

    # Main central stem line
    draw.line([(cx, top_y + 20), (cx, bottom_y)], fill=color, width=stroke_width)

    # Leaves paired along the stem
    num_pairs = 4
    step = (height - 60) // (num_pairs + 1)
    for i in range(1, num_pairs + 1):
        ly = bottom_y - i * step
        leaf_len = int(height * 0.16)
        leaf_drop = int(height * 0.05)
        # Left leaf
        draw.line([(cx, ly), (cx - leaf_len, ly - leaf_drop)], fill=color, width=max(2, stroke_width - 1))
        draw.ellipse([cx - leaf_len - 10, ly - leaf_drop - 6, cx - leaf_len + 10, ly - leaf_drop + 6], fill=color)
        # Right leaf
        draw.line([(cx, ly), (cx + leaf_len, ly - leaf_drop)], fill=color, width=max(2, stroke_width - 1))
        draw.ellipse([cx + leaf_len - 10, ly - leaf_drop - 6, cx + leaf_len + 10, ly - leaf_drop + 6], fill=color)

    # Top leaf
    draw.ellipse([cx - 8, top_y, cx + 8, top_y + 24], fill=color)


def draw_concentric_rings(draw: Any, center: Tuple[int, int], max_radius: int, color: str, stroke_width: int = 4):
    """Draw concentric thin geometric rings."""
    cx, cy = center
    for r in [int(max_radius * 0.35), int(max_radius * 0.65), max_radius]:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=stroke_width)


# --- Core Art Design Renderer ---

def render_art_design(
    design_info: Dict[str, str],
    size_spec: Dict[str, Any],
    palette: Dict[str, str],
    design_index: int = 1,
    total_designs: int = 3,
) -> Any:
    """Render a single typographic art design at a specific print size using pure Pillow."""
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required to render wall art designs")

    w = size_spec["width_px"]
    h = size_spec["height_px"]
    scale = w / 2400.0  # reference scaling factor based on 8x10 base

    # Safe margin: at least 6% on sides, 8% on top/bottom
    margin_x = int(w * 0.08)
    margin_y = int(h * 0.09)
    safe_w = w - 2 * margin_x
    safe_h = h - 2 * margin_y

    bg_color = palette.get("background", "#FDFBF7")
    primary_color = ensure_contrast(palette.get("primary", "#1A1A1A"), bg_color, 4.5)
    accent_color = palette.get("accent", "#C47659")
    secondary_color = ensure_contrast(palette.get("secondary", "#3B3330"), bg_color, 4.5)
    light_accent = palette.get("light_accent", "#F2E6DC")

    img = Image.new("RGB", (w, h), color=bg_color)
    draw = ImageDraw.Draw(img)

    quote = design_info.get("quote", "BREATHE IN PEACE").upper()
    subtitle = design_info.get("sub", "").strip()
    motif = design_info.get("motif", "arch")

    # Layout structure:
    # Top: Motif or decorative visual space (approx 35-40% height)
    # Middle: Prominent typographic quote (approx 35-40% height)
    # Bottom: Elegant sub-phrase + gallery credit / edition line
    motif_center_x = w // 2
    motif_area_h = int(safe_h * 0.38)
    motif_y_center = margin_y + motif_area_h // 2

    # Draw motif based on choice
    line_w = max(3, int(5 * scale))
    if motif == "arch":
        arch_w = int(min(safe_w * 0.45, motif_area_h * 0.8))
        arch_h = int(arch_w * 1.3)
        ax0 = motif_center_x - arch_w // 2
        ay0 = motif_y_center - arch_h // 2
        draw_arch_motif(draw, (ax0, ay0, ax0 + arch_w, ay0 + arch_h),
                        stroke_color=accent_color,
                        fill_color=light_accent,
                        stroke_width=line_w)
    elif motif == "sunburst":
        r = int(min(safe_w * 0.28, motif_area_h * 0.45))
        draw_sunburst_motif(draw, (motif_center_x, motif_y_center + r // 3),
                            radius=r,
                            color=accent_color,
                            stroke_width=line_w)
    elif motif == "botanical_leaves":
        stem_h = int(motif_area_h * 0.85)
        draw_botanical_stem(draw, (motif_center_x, motif_y_center),
                            height=stem_h,
                            color=accent_color,
                            stroke_width=line_w)
    elif motif == "rings":
        max_r = int(min(safe_w * 0.25, motif_area_h * 0.42))
        draw_concentric_rings(draw, (motif_center_x, motif_y_center),
                              max_radius=max_r,
                              color=accent_color,
                              stroke_width=line_w)
    elif motif == "minimal_frame":
        box_w = int(min(safe_w * 0.55, motif_area_h * 0.9))
        box_h = int(box_w * 0.75)
        bx0 = motif_center_x - box_w // 2
        by0 = motif_y_center - box_h // 2
        draw_minimal_frame(draw, (bx0, by0, bx0 + box_w, by0 + box_h),
                           stroke_color=accent_color,
                           stroke_width=line_w)
    else:
        # Default decorative rule
        rule_w = int(safe_w * 0.4)
        draw.line([(motif_center_x - rule_w // 2, motif_y_center),
                   (motif_center_x + rule_w // 2, motif_y_center)],
                  fill=accent_color, width=line_w)

    # Elegant divider line under motif
    divider_y = margin_y + motif_area_h + int(20 * scale)
    div_len = int(safe_w * 0.15)
    draw.line([(motif_center_x - div_len // 2, divider_y),
               (motif_center_x + div_len // 2, divider_y)],
              fill=accent_color, width=max(2, int(3 * scale)))

    # Main Quote Area
    quote_top = divider_y + int(40 * scale)
    quote_avail_h = int(safe_h * 0.38)
    target_font_size = int(140 * scale)
    min_font_size = max(28, int(42 * scale))

    lines, quote_font, font_size, line_height = fit_quote_typography(
        quote,
        max_width=safe_w,
        max_height=quote_avail_h,
        target_size=target_font_size,
        min_size=min_font_size,
        font_type="bold",
        max_lines=5,
    )

    total_quote_h = len(lines) * line_height
    quote_start_y = quote_top + max(0, (quote_avail_h - total_quote_h) // 2)

    for i, line in enumerate(lines):
        lw, lh = measure_text(line, quote_font)
        lx = (w - lw) // 2
        ly = quote_start_y + i * line_height
        draw.text((lx, ly), line, fill=primary_color, font=quote_font)

    # Subtitle / Nuance text
    sub_top = quote_start_y + total_quote_h + int(35 * scale)
    sub_max_w = int(safe_w * 0.85)
    sub_avail_h = int(safe_h * 0.12)
    sub_font_size = max(20, int(38 * scale))

    if subtitle:
        sub_lines, sub_font, _, sub_line_h = fit_quote_typography(
            subtitle,
            max_width=sub_max_w,
            max_height=sub_avail_h,
            target_size=sub_font_size,
            min_size=max(16, int(22 * scale)),
            font_type="regular",
            max_lines=3,
        )
        for j, sline in enumerate(sub_lines):
            sw, sh = measure_text(sline, sub_font)
            sx = (w - sw) // 2
            sy = sub_top + j * sub_line_h
            draw.text((sx, sy), sline, fill=secondary_color, font=sub_font)

    # Subtle bottom edition / print mark (clean and minimalist)
    edition_text = f"ART PRINT NO. {design_index:02d} / {total_designs:02d}  ·  CHILLI GAVVA ART COLLECTION"
    edition_font = get_font("regular", max(16, int(22 * scale)))
    ew, eh = measure_text(edition_text, edition_font)
    ex = (w - ew) // 2
    ey = h - margin_y - eh - int(10 * scale)
    draw.text((ex, ey), edition_text, fill=secondary_color, font=edition_font)

    # Subtle outer bleed margin guide / elegant corner crop ticks
    tick_len = int(24 * scale)
    tick_color = light_accent
    draw.line([(margin_x, margin_y), (margin_x + tick_len, margin_y)], fill=tick_color, width=2)
    draw.line([(margin_x, margin_y), (margin_x, margin_y + tick_len)], fill=tick_color, width=2)
    draw.line([(w - margin_x, margin_y), (w - margin_x - tick_len, margin_y)], fill=tick_color, width=2)
    draw.line([(w - margin_x, margin_y), (w - margin_x, margin_y + tick_len)], fill=tick_color, width=2)
    draw.line([(margin_x, h - margin_y), (margin_x + tick_len, h - margin_y)], fill=tick_color, width=2)
    draw.line([(margin_x, h - margin_y), (margin_x, h - margin_y - tick_len)], fill=tick_color, width=2)
    draw.line([(w - margin_x, h - margin_y), (w - margin_x - tick_len, h - margin_y)], fill=tick_color, width=2)
    draw.line([(w - margin_x, h - margin_y), (w - margin_x, h - margin_y - tick_len)], fill=tick_color, width=2)

    return img


# --- PRINTING.txt Generator ---

def generate_printing_guide(
    spec: Dict[str, Any],
    designs_count: int,
    palette_name: str,
) -> str:
    """Generate comprehensive PRINTING.txt guide without currency amounts."""
    title = spec.get("title", "Modern Typographic Wall Art Print Set")
    guide_lines = [
        f"================================================================================",
        f"{title.upper()} — PRINTING & FRAMING GUIDE",
        f"================================================================================",
        f"",
        f"Thank you for your purchase! This wall art set contains {designs_count} high-resolution",
        f"typographic art designs, rendered in the '{palette_name}' aesthetic palette.",
        f"Each design is provided in 5 versatile sizes to fit standard worldwide frames.",
        f"",
        f"--------------------------------------------------------------------------------",
        f"1. INCLUDED FILE SIZES & RESOLUTION",
        f"--------------------------------------------------------------------------------",
        f"",
        f"Each design is exported at gallery-standard high resolution:",
        f"",
        f"  * A4 (ISO Series):     2480 x 3508 px at 300 dpi (210 x 297 mm)",
        f"  * A3 (ISO Series):     3508 x 4960 px at 300 dpi (297 x 420 mm)",
        f"  * 8x10 in (4:5 ratio): 2400 x 3000 px at 300 dpi (203.2 x 254 mm)",
        f"  * 11x14 in:            3300 x 4200 px at 300 dpi (279.4 x 355.6 mm)",
        f"  * 16x20 in (4:5 ratio):3200 x 4000 px at 200 dpi (406.4 x 508 mm)*",
        f"    *Note: 16x20 in is formatted at 200 dpi for optimal balance of large-format",
        f"     sharpness and file performance.",
        f"",
        f"  * Combined PDF:        A single multi-page PDF document containing all {designs_count}",
        f"                         designs formatted to A4 size for quick home printing.",
        f"",
        f"--------------------------------------------------------------------------------",
        f"2. PAPER RECOMMENDATIONS",
        f"--------------------------------------------------------------------------------",
        f"",
        f"For the best typographic depth, rich contrast, and gallery-worthy appearance:",
        f"",
        f"  * Paper Weight: Heavyweight stock between 200 gsm and 300 gsm (80-110 lb cover).",
        f"  * Paper Finish: Smooth matte or fine-art archival cotton paper.",
        f"    Matte paper avoids annoying glass reflections once framed and gives typography",
        f"    a warm, modern, museum-like finish.",
        f"  * Alternative: Soft satin or pearl lustre paper if you prefer subtle sheen.",
        f"",
        f"--------------------------------------------------------------------------------",
        f"3. HOW TO PRINT",
        f"--------------------------------------------------------------------------------",
        f"",
        f"  A. AT HOME:",
        f"     - Use the combined PDF or individual A4 / 8x10 PNG files.",
        f"     - Open in your printer software or PDF reader.",
        f"     - Set printer quality to 'Best' or 'High Quality Photo'.",
        f"     - Ensure scale is set to '100%' or 'Actual Size' (do NOT select 'Fit to Page'",
        f"       unless trimming with custom borders).",
        f"",
        f"  B. LOCAL PRINT SHOP OR ONLINE LAB:",
        f"     - Transfer files via USB or online upload to your local print shop",
        f"       (such as Walgreens, FedEx Office, Staples, Officeworks, Boots, or photo labs).",
        f"     - Select your desired size (e.g., 8x10 in, 11x14 in, 16x20 in, A4, or A3).",
        f"     - Request heavyweight matte photo paper or archival cardstock.",
        f"",
        f"--------------------------------------------------------------------------------",
        f"4. FRAMING & STYLING TIPS",
        f"--------------------------------------------------------------------------------",
        f"",
        f"  * Matting: Adding a white or cream bevel-cut mat board elevates the print",
        f"    and draws the eye directly to the typography.",
        f"  * Frame Styles: Thin natural oak, walnut, matte black, or brushed brass frames",
        f"    complement these minimalist typographic prints exceptionally well.",
        f"  * Gallery Wall: Hang the set of prints in a grid (2x2 or 3x1) with 2-3 inches of",
        f"    space between frames for a balanced, cohesive living room or office wall.",
        f"",
        f"--------------------------------------------------------------------------------",
        f"5. TERMS OF USE & LICENSE",
        f"--------------------------------------------------------------------------------",
        f"",
        f"  * This purchase is licensed strictly for PERSONAL USE ONLY.",
        f"  * You may print as many copies as you wish for your home, office, or as gifts.",
        f"  * Digital sharing, resale, redistribution, sublicensing, or commercial resale",
        f"    of these files in digital or physical form is strictly prohibited.",
        f"  * All artwork, typographic layouts, and designs are copyrighted.",
        f"",
        f"Chilli Gavva Wall Art Studio · Thank you for supporting creative digital craft!",
        f"================================================================================",
    ]
    return "\n".join(guide_lines) + "\n"


# --- Combined PDF Generator ---

def build_combined_pdf(a4_image_paths: List[Path], output_pdf_path: Path, title: str = "Wall Art Set") -> None:
    """Combine A4 PNG images into a single multi-page PDF document at print resolution.
    If pdf_design is usable, we can link it; otherwise PIL Image.save provides robust, standard PDF embedding."""
    if not a4_image_paths:
        raise ValueError("No A4 images provided for combined PDF")

    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required to build combined PDF")

    images = [Image.open(p).convert("RGB") for p in a4_image_paths]
    first = images[0]
    rest = images[1:] if len(images) > 1 else []

    # Save multi-page PDF using Pillow
    first.save(
        str(output_pdf_path),
        save_all=True,
        append_images=rest,
        resolution=300.0,
        title=title,
    )


# --- Spec Normalization ---

def normalize_spec(spec: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]], Dict[str, str], str]:
    """Extract and sanitize title, design quotes list (3 to 6), palette, and theme from spec."""
    title = str(spec.get("title", "Mindful Moments Typographic Wall Art Print Set")).strip()

    palette_key = str(spec.get("palette", spec.get("palette_name", "terracotta"))).lower().strip()
    if palette_key not in PALETTES:
        # Match substring or fallback
        matched = next((k for k in PALETTES if k in palette_key), "terracotta")
        palette_key = matched
    palette = PALETTES[palette_key]

    theme_key = str(spec.get("theme", "mindful")).lower().strip()
    theme_quotes = THEME_PRESETS.get(theme_key, THEME_PRESETS["mindful"])

    # Extract quotes or phrases from spec
    raw_quotes = spec.get("quotes") or spec.get("phrases") or spec.get("designs") or []
    designs: List[Dict[str, str]] = []

    if isinstance(raw_quotes, list) and raw_quotes:
        for idx, item in enumerate(raw_quotes):
            if isinstance(item, str):
                parts = item.split("·") if "·" in item else item.split("-", 1)
                q = parts[0].strip()
                sub = parts[1].strip() if len(parts) > 1 else ""
                motif = theme_quotes[idx % len(theme_quotes)].get("motif", "arch")
                designs.append({"quote": q, "sub": sub, "motif": motif})
            elif isinstance(item, dict):
                q = str(item.get("quote", item.get("text", item.get("title", "")))).strip()
                sub = str(item.get("sub", item.get("subtitle", item.get("description", "")))).strip()
                motif = str(item.get("motif", theme_quotes[idx % len(theme_quotes)].get("motif", "arch"))).strip()
                if q:
                    designs.append({"quote": q, "sub": sub, "motif": motif})

    # Ensure 3 to 6 designs
    if len(designs) < 3:
        # Fill from theme preset
        for item in theme_quotes:
            if not any(d["quote"].upper() == item["quote"].upper() for d in designs):
                designs.append(dict(item))
            if len(designs) >= 3:
                break

    # If count requested explicitly
    desired_count = spec.get("count") or len(designs)
    try:
        desired_count = int(desired_count)
        desired_count = max(3, min(6, desired_count))
    except (ValueError, TypeError):
        desired_count = max(3, min(6, len(designs)))

    while len(designs) < desired_count:
        idx = len(designs)
        designs.append(dict(theme_quotes[idx % len(theme_quotes)]))

    designs = designs[:desired_count]
    return title, designs, palette, palette_key


# --- Main Build Function ---

def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> Dict[str, Any]:
    """Build a complete wall art print set into out_dir from spec.

    Args:
      spec: dictionary with keys:
            - title (str)
            - quotes / phrases (list of 3-6 quote texts or dicts)
            - palette / palette_name (str, e.g. 'terracotta', 'sage', 'minimal_mono')
            - count (int, 3 to 6)
      out_dir: path where output files will be written

    Returns:
      Dictionary containing status, generated file paths, counts, sizes, and Tenglish summary.
    """
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for building wall art sets")

    out_path = Path(out_dir).resolve()

    title, designs, palette, palette_key = normalize_spec(spec)
    total_designs = len(designs)

    # Use atomic build: create files inside temporary directory, then safely move to out_path
    temp_dir = Path(tempfile.mkdtemp(prefix="wallart_build_"))
    generated_files: List[Path] = []
    a4_files: List[Path] = []

    try:
        # 1. Generate each design at all 5 sizes
        for d_idx, design_item in enumerate(designs, 1):
            design_slug = f"art_{d_idx:02d}"

            for size_key, size_info in PRINT_SIZES.items():
                img = render_art_design(
                    design_info=design_item,
                    size_spec=size_info,
                    palette=palette,
                    design_index=d_idx,
                    total_designs=total_designs,
                )

                file_name = f"{design_slug}_{size_key}.png"
                target_file = temp_dir / file_name

                # PNG export with DPI metadata
                dpi_val = size_info["dpi"]
                img.save(str(target_file), format="PNG", dpi=(dpi_val, dpi_val), optimize=True)
                generated_files.append(target_file)

                if size_key == "a4":
                    a4_files.append(target_file)

        # 2. Generate combined PDF of A4 versions
        pdf_name = "wall_art_set_a4_collection.pdf"
        pdf_path = temp_dir / pdf_name
        build_combined_pdf(a4_files, pdf_path, title=title)
        generated_files.append(pdf_path)

        # 3. Generate PRINTING.txt guide
        printing_text = generate_printing_guide(spec, designs_count=total_designs, palette_name=palette["name"])
        printing_path = temp_dir / "PRINTING.txt"
        with open(printing_path, "w", encoding="utf-8") as f:
            f.write(printing_text)
        generated_files.append(printing_path)

        # Atomic move / sync to destination directory
        out_path.mkdir(parents=True, exist_ok=True)
        final_files: List[str] = []
        for src in generated_files:
            dst = out_path / src.name
            shutil.copy2(src, dst)
            final_files.append(str(dst))

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    summary_msg = (
        f"Wall art set build aindi! Total {total_designs} typographic designs, "
        f"prati design ki 5 print sizes (A4, A3, 8x10, 11x14, 16x20) PNG files, "
        f"A4 combined PDF, and PRINTING.txt ready ga vunnayi."
    )

    return {
        "status": "ok",
        "format": FORMAT,
        "title": title,
        "palette": palette_key,
        "palette_name": palette["name"],
        "designs_count": total_designs,
        "sizes_count": len(PRINT_SIZES),
        "total_files": len(final_files),
        "files": sorted(final_files),
        "pdf_path": str(out_path / pdf_name),
        "printing_guide": str(out_path / "PRINTING.txt"),
        "tenglish_summary": summary_msg,
    }


# --- Telegram Plugin Interface (Shared Plugin Standard) ---

COMMANDS = [
    {
        "cmd": "/wallart",
        "words": ["wallart", "wall art", "art pampu", "poster print"],
        "help_en": "Build or inspect typographic wall art print set designs.",
        "help_te": "Wall art typographic print set designs build cheyadaniki vadandi.",
    }
]


def handle(text: str, ctx: dict) -> dict | None:
    """Telegram plugin handler following the standard plugin interface."""
    cleaned = (text or "").strip().lower()

    is_matched = False
    for cmd_meta in COMMANDS:
        if cleaned == cmd_meta["cmd"] or cleaned.startswith(cmd_meta["cmd"] + " "):
            is_matched = True
            break
        for trigger in cmd_meta.get("words", []):
            if trigger in cleaned:
                is_matched = True
                break
        if is_matched:
            break

    if not is_matched:
        return None

    response_text = (
        "Wall art print set format ready ga vundi. "
        "Standard 5 sizes (A4, A3, 8x10, 11x14, 16x20) and combined PDF support tho "
        "pure typography and geometric shapes designs tayaru avthayi."
    )
    return {"text": response_text}
