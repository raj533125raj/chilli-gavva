"""pdf_themes.py — Premium PDF Planner Design System.

6 premium themes with complete color tokens, type scale, spacing grids,
edge tabs, dotted/lined/grid areas, habit circles, checkboxes, covers,
divider pages, How-to-use page, and legend.

Built on existing PDF operations without external libraries (pure Python).
Strictly no money or currency references.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# 1. COLOR SYSTEM & CONTRAST
# ---------------------------------------------------------------------------

def hex_to_rgb(hex_code: str) -> Tuple[float, float, float]:
    """Convert hex string (e.g. '#2f7d78') to float RGB (0.0 - 1.0)."""
    h = hex_code.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return (r, g, b)


def relative_luminance(rgb: Tuple[float, float, float]) -> float:
    """Calculate relative luminance for WCAG contrast calculation."""
    def adjust(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else math.pow((c + 0.055) / 1.055, 2.4)

    r, g, b = (adjust(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(color1: str, color2: str) -> float:
    """Compute WCAG contrast ratio between two hex colors."""
    l1 = relative_luminance(hex_to_rgb(color1))
    l2 = relative_luminance(hex_to_rgb(color2))
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------------------
# 2. THEME DEFINITIONS & COLOR TOKENS
# ---------------------------------------------------------------------------
# Six premium themes:
# 1. Calm Sage: soft organic sage green palette, mindful & grounded
# 2. Midnight Focus: deep navy / midnight slate, executive & high concentration
# 3. Terracotta: warm earthenware clay & sand, creative & cozy
# 4. Ocean: crisp deep oceanic blues & seafoam, refreshing & structured
# 5. Minimal Mono: high-contrast refined architectural grayscale, timeless & sleek
# 6. Festive Marigold: vibrant warm marigold amber & deep spice, celebratory & energetic

PREMIUM_THEMES: Dict[str, Dict[str, Any]] = {
    "Calm Sage": {
        "slug": "calm_sage",
        "name": "Calm Sage",
        "description": "Soft organic sage green and warm mist, mindful and grounded",
        "tokens": {
            "bg": "#f7f9f7",
            "page": "#f7f9f7",
            "surface": "#ffffff",
            "surface_soft": "#eef3ee",
            "accent": "#386641",
            "accent_light": "#6a994e",
            "accent_soft": "#dce8dc",
            "ink": "#1e2d24",
            "muted": "#526356",
            "faded": "#9eaba1",
            "line": "#cfdcd0",
            "border": "#b8c9ba",
            "cell": "#d4e2d6",
            "highlight": "#f2e8cf",
            "bar": "#e2ede2",
            "tab_bg": "#386641",
            "tab_text": "#ffffff",
            "tab_inactive_bg": "#e6efe6",
            "tab_inactive_text": "#526356",
            "badge_bg": "#dce8dc",
            "badge_text": "#26462c",
            "weekend": "#f0f5f0",
            "on_accent": "#ffffff",
            "on_accent_soft": "#e8f0e8",
        },
        "round": True,
        "round_radius": 6.0,
        "style_key": "calm_sage",
    },
    "Midnight Focus": {
        "slug": "midnight_focus",
        "name": "Midnight Focus",
        "description": "Deep midnight slate and rich indigo accents, executive and clear",
        "tokens": {
            "bg": "#f8f9fc",
            "page": "#f8f9fc",
            "surface": "#ffffff",
            "surface_soft": "#eef2f9",
            "accent": "#1e3a8a",
            "accent_light": "#3b82f6",
            "accent_soft": "#dbeafe",
            "ink": "#0f172a",
            "muted": "#475569",
            "faded": "#94a3b8",
            "line": "#cbd5e1",
            "border": "#94a3b8",
            "cell": "#cbd5e1",
            "highlight": "#e0e7ff",
            "bar": "#e0e7ff",
            "tab_bg": "#1e3a8a",
            "tab_text": "#ffffff",
            "tab_inactive_bg": "#e2e8f0",
            "tab_inactive_text": "#475569",
            "badge_bg": "#dbeafe",
            "badge_text": "#1e3a8a",
            "weekend": "#edf2f7",
            "on_accent": "#ffffff",
            "on_accent_soft": "#dbeafe",
        },
        "round": False,
        "round_radius": 3.0,
        "style_key": "midnight_focus",
    },
    "Terracotta": {
        "slug": "terracotta",
        "name": "Terracotta",
        "description": "Warm earthenware clay, earthy ochre and linen, organic and inviting",
        "tokens": {
            "bg": "#faf7f5",
            "page": "#faf7f5",
            "surface": "#ffffff",
            "surface_soft": "#f4ede8",
            "accent": "#a24936",
            "accent_light": "#d36135",
            "accent_soft": "#fcebe6",
            "ink": "#38241d",
            "muted": "#6e554d",
            "faded": "#b5a29b",
            "line": "#e2d5ce",
            "border": "#c9b5ac",
            "cell": "#e5d8d1",
            "highlight": "#fae3d9",
            "bar": "#f7e8e1",
            "tab_bg": "#a24936",
            "tab_text": "#ffffff",
            "tab_inactive_bg": "#f0e4de",
            "tab_inactive_text": "#6e554d",
            "badge_bg": "#fcebe6",
            "badge_text": "#823625",
            "weekend": "#f7ede8",
            "on_accent": "#ffffff",
            "on_accent_soft": "#fcebe6",
        },
        "round": True,
        "round_radius": 7.0,
        "style_key": "terracotta",
    },
    "Ocean": {
        "slug": "ocean",
        "name": "Ocean",
        "description": "Crisp maritime deep teal and refreshing seafoam, structured and airy",
        "tokens": {
            "bg": "#f5f9fa",
            "page": "#f5f9fa",
            "surface": "#ffffff",
            "surface_soft": "#eaf2f5",
            "accent": "#005f73",
            "accent_light": "#0a9396",
            "accent_soft": "#d6eff2",
            "ink": "#132a30",
            "muted": "#466067",
            "faded": "#90a5ab",
            "line": "#c7d9de",
            "border": "#a8c2c9",
            "cell": "#ccdbe0",
            "highlight": "#e0f2f1",
            "bar": "#e1eff2",
            "tab_bg": "#005f73",
            "tab_text": "#ffffff",
            "tab_inactive_bg": "#e0ecf0",
            "tab_inactive_text": "#466067",
            "badge_bg": "#d6eff2",
            "badge_text": "#004352",
            "weekend": "#eaf3f5",
            "on_accent": "#ffffff",
            "on_accent_soft": "#d6eff2",
        },
        "round": False,
        "round_radius": 4.0,
        "style_key": "ocean",
    },
    "Minimal Mono": {
        "slug": "minimal_mono",
        "name": "Minimal Mono",
        "description": "High-contrast architectural black, white and neutral grays, timeless and pure",
        "tokens": {
            "bg": "#ffffff",
            "page": "#ffffff",
            "surface": "#ffffff",
            "surface_soft": "#f4f4f5",
            "accent": "#18181b",
            "accent_light": "#52525b",
            "accent_soft": "#e4e4e7",
            "ink": "#18181b",
            "muted": "#52525b",
            "faded": "#a1a1aa",
            "line": "#e4e4e7",
            "border": "#a1a1aa",
            "cell": "#d4d4d8",
            "highlight": "#f4f4f5",
            "bar": "#f4f4f5",
            "tab_bg": "#18181b",
            "tab_text": "#ffffff",
            "tab_inactive_bg": "#f4f4f5",
            "tab_inactive_text": "#52525b",
            "badge_bg": "#e4e4e7",
            "badge_text": "#18181b",
            "weekend": "#fafafa",
            "on_accent": "#ffffff",
            "on_accent_soft": "#d4d4d8",
        },
        "round": False,
        "round_radius": 2.0,
        "style_key": "minimal_mono",
    },
    "Festive Marigold": {
        "slug": "festive_marigold",
        "name": "Festive Marigold",
        "description": "Rich golden marigold amber with warm plum and saffron accents, celebratory and radiant",
        "tokens": {
            "bg": "#fffdfa",
            "page": "#fffdfa",
            "surface": "#ffffff",
            "surface_soft": "#fdf6e9",
            "accent": "#9d4b00",
            "accent_light": "#d97706",
            "accent_soft": "#fef3c7",
            "ink": "#2b1c08",
            "muted": "#664d30",
            "faded": "#ab9982",
            "line": "#ebdcc9",
            "border": "#d4be9f",
            "cell": "#eddac2",
            "highlight": "#fef3c7",
            "bar": "#faebd7",
            "tab_bg": "#9d4b00",
            "tab_text": "#ffffff",
            "tab_inactive_bg": "#fbf0df",
            "tab_inactive_text": "#664d30",
            "badge_bg": "#fef3c7",
            "badge_text": "#78350f",
            "weekend": "#fdf4e7",
            "on_accent": "#ffffff",
            "on_accent_soft": "#fef3c7",
        },
        "round": True,
        "round_radius": 6.0,
        "style_key": "festive_marigold",
    },
}

# Aliases and normalized key mapping
THEME_NAME_MAP: Dict[str, str] = {
    "calm sage": "Calm Sage",
    "calmsage": "Calm Sage",
    "calm_sage": "Calm Sage",
    "sage": "Calm Sage",
    "midnight focus": "Midnight Focus",
    "midnightfocus": "Midnight Focus",
    "midnight_focus": "Midnight Focus",
    "midnight": "Midnight Focus",
    "focus": "Midnight Focus",
    "terracotta": "Terracotta",
    "terra": "Terracotta",
    "ocean": "Ocean",
    "sea": "Ocean",
    "minimal mono": "Minimal Mono",
    "minimalmono": "Minimal Mono",
    "minimal_mono": "Minimal Mono",
    "mono": "Minimal Mono",
    "minimal": "Minimal Mono",
    "grayscale": "Minimal Mono",
    "festive marigold": "Festive Marigold",
    "festivemarigold": "Festive Marigold",
    "festive_marigold": "Festive Marigold",
    "marigold": "Festive Marigold",
    "festive": "Festive Marigold",
}

# Niche to default theme mapping (stable, predictable)
NICHE_THEME_MAP: Dict[str, str] = {
    "ADHD Planners Agent": "Calm Sage",
    "2027 Planners Agent": "Ocean",
    "Budget Sheets Agent": "Midnight Focus",
    "Freelancer Trackers Agent": "Terracotta",
    "Notion Templates Agent": "Minimal Mono",
    "Resume Bundle Agent": "Midnight Focus",
}


def normalize_theme_name(theme_input: Optional[str]) -> Optional[str]:
    """Normalize any theme input string into a standard canonical theme name."""
    if not theme_input or not isinstance(theme_input, str):
        return None
    key = re.sub(r"[-_\s]+", " ", theme_input.strip().lower())
    if key in THEME_NAME_MAP:
        return THEME_NAME_MAP[key]
    key_slug = key.replace(" ", "_")
    if key_slug in THEME_NAME_MAP:
        return THEME_NAME_MAP[key_slug]
    for canon_name in PREMIUM_THEMES:
        if canon_name.lower() == key:
            return canon_name
    return None


def get_theme_for_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve theme from spec['theme'], or spec['niche'] default, or 'Calm Sage' fallback."""
    explicit = spec.get("theme")
    canon = normalize_theme_name(explicit)
    if canon and canon in PREMIUM_THEMES:
        return PREMIUM_THEMES[canon]

    niche = spec.get("niche")
    if niche and niche in NICHE_THEME_MAP:
        return PREMIUM_THEMES[NICHE_THEME_MAP[niche]]

    # Check title / tags hints if available
    title = str(spec.get("name") or (spec.get("listing") or {}).get("title") or "").lower()
    if any(w in title for w in ("focus", "deep", "work", "executive")):
        return PREMIUM_THEMES["Midnight Focus"]
    if any(w in title for w in ("festive", "diwali", "celebrate", "holiday")):
        return PREMIUM_THEMES["Festive Marigold"]
    if any(w in title for w in ("ocean", "sea", "blue", "water", "academic")):
        return PREMIUM_THEMES["Ocean"]
    if any(w in title for w in ("minimal", "clean", "mono", "simple")):
        return PREMIUM_THEMES["Minimal Mono"]
    if any(w in title for w in ("warm", "clay", "art", "craft", "home")):
        return PREMIUM_THEMES["Terracotta"]

    return PREMIUM_THEMES["Calm Sage"]


def get_theme(name_or_slug: str) -> Dict[str, Any]:
    """Get theme dictionary by canonical name or slug."""
    canon = normalize_theme_name(name_or_slug)
    if canon and canon in PREMIUM_THEMES:
        return PREMIUM_THEMES[canon]
    for theme in PREMIUM_THEMES.values():
        if theme["slug"] == name_or_slug:
            return theme
    raise KeyError(f"Unknown theme: {name_or_slug}")


def list_theme_names() -> List[str]:
    """List the 6 canonical premium theme names."""
    return list(PREMIUM_THEMES.keys())


def theme_to_pdf_design_style(theme: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Convert a premium theme into the style dict format expected by pdf_design.STYLES."""
    th = theme if isinstance(theme, dict) and "tokens" in theme else get_theme(str(theme))
    tok = th["tokens"]
    return {
        "name": th["name"],
        "page": tok.get("page"),
        "accent": tok["accent"],
        "bar": tok.get("bar"),
        "line": tok["line"],
        "weekend": tok.get("weekend"),
        "ink": tok["ink"],
        "muted": tok["muted"],
        "faded": tok["faded"],
        "on_accent": tok["on_accent"],
        "on_accent_soft": tok["on_accent_soft"],
        "round": bool(th.get("round", False)),
        "boxes": [tok.get("surface_soft", "#eef3ee"), tok.get("bar", "#e2ede2"), tok.get("highlight", "#f2e8cf")],
        "cell": tok.get("cell", tok["line"]),
        "pill": tok.get("surface_soft", "#eef3ee"),
        "header": tok.get("surface_soft", "#eef3ee"),
        "lavender": tok.get("accent_soft", "#dce8dc"),
    }


def register_themes_in_pdf_design() -> None:
    """Register all 6 premium themes into pdf_design.STYLES dynamically."""
    import pdf_design
    for th_name, th in PREMIUM_THEMES.items():
        st = theme_to_pdf_design_style(th)
        pdf_design.STYLES[th["slug"]] = st
        pdf_design.STYLES[th_name] = st


# ---------------------------------------------------------------------------
# 3. DESIGN SYSTEM: TYPE SCALE & SPACING GRID
# ---------------------------------------------------------------------------

TYPE_SCALE = {
    "display": 32.0,      # Main cover title
    "title_lg": 24.0,     # Large section headers / divider titles
    "title_md": 18.0,     # Page headers / prominent titles
    "title_sm": 14.0,     # Subsection headings / card titles
    "body_lg": 11.5,      # Featured body text / list items
    "body": 10.0,         # Standard body text / planner lines
    "body_sm": 9.0,       # Secondary notes / captions / grid headers
    "caption": 8.0,       # Footer / meta text / micro labels
    "micro": 6.5,         # Ultra-small badges / legend text
}

SPACING_GRID = {
    "margin_page": 42.0,     # Outer page margin
    "bottom_safe": 60.0,     # Content bottom boundary
    "footer_y": 30.0,        # Footer baseline
    "gutter": 14.0,          # Inter-column / inter-box gutter
    "row_height": 20.0,      # Default planner row height
    "row_compact": 16.0,     # Compact planner row
    "tab_width": 24.0,       # Width of edge section tabs
    "tab_height": 48.0,      # Height of edge section tabs
}


# ---------------------------------------------------------------------------
# 4. COMPONENT DRAWING HELPERS
# ---------------------------------------------------------------------------

def draw_header_band(
    pdf: Any,
    title: str,
    subtitle: str = "",
    badge: str = "",
    theme: Optional[Dict[str, Any]] = None,
    right_label: str = "",
) -> float:
    """Draw a polished premium header band across the top of a page.
    Returns the y coordinate where content below may start.
    """
    th = theme or PREMIUM_THEMES["Calm Sage"]
    tok = th["tokens"]
    w, h = pdf.w, pdf.h
    margin = SPACING_GRID["margin_page"]

    band_top = h - 24.0
    band_height = 54.0 if subtitle else 44.0
    band_bottom = band_top - band_height

    # Decorative header background container
    if th["round"]:
        pdf.round_rect(margin, band_bottom, w - 2 * margin, band_height, th["round_radius"], fill=tok["surface_soft"])
        # Accent left accent bar
        pdf.round_rect(margin, band_bottom, 4.0, band_height, 2.0, fill=tok["accent"])
    else:
        pdf.fill(margin, band_bottom, w - 2 * margin, band_height, tok["surface_soft"])
        # Accent left edge stripe
        pdf.fill(margin, band_bottom, 4.0, band_height, tok["accent"])

    # Title & Badge
    tx = margin + 14.0
    ty = band_bottom + (28.0 if subtitle else 18.0)
    pdf.text(tx, ty, title, TYPE_SCALE["title_md"], bold=True, color=tok["ink"])

    if badge:
        # Measure title width to position badge next to it
        from pdf_design import text_width
        tw = text_width(title, TYPE_SCALE["title_md"], bold=True)
        bx = tx + tw + 10.0
        by = ty - 2.0
        draw_badge(pdf, bx, by, badge, theme=th)

    if subtitle:
        sy = band_bottom + 12.0
        pdf.text(tx, sy, subtitle, TYPE_SCALE["body_sm"], bold=False, color=tok["muted"])

    if right_label:
        pdf.text_right(w - margin - 12.0, ty, right_label, TYPE_SCALE["body_sm"], bold=True, color=tok["accent"])

    return band_bottom - SPACING_GRID["gutter"]


def draw_page_number(
    pdf: Any,
    current_page: int,
    total_pages: int,
    theme: Optional[Dict[str, Any]] = None,
    prefix: str = "Page",
) -> None:
    """Draw a consistent, elegant page number indicator at the bottom edge."""
    th = theme or PREMIUM_THEMES["Calm Sage"]
    tok = th["tokens"]
    margin = SPACING_GRID["margin_page"]
    text = f"{prefix} {current_page} of {total_pages}"
    pdf.text_right(pdf.w - margin, SPACING_GRID["footer_y"], text, TYPE_SCALE["caption"], bold=False, color=tok["muted"])


def draw_section_tab(
    pdf: Any,
    tab_index: int,
    label: str,
    total_tabs: int = 5,
    active: bool = False,
    theme: Optional[Dict[str, Any]] = None,
) -> None:
    """Draw an indexed section tab along the right edge of the page.
    Provides tactile visual cue for planners.
    """
    th = theme or PREMIUM_THEMES["Calm Sage"]
    tok = th["tokens"]
    w, h = pdf.w, pdf.h
    tab_w = SPACING_GRID["tab_width"]
    tab_h = SPACING_GRID["tab_height"]
    gap = 6.0

    # Vertical distribution on the right edge
    available_h = h - 2 * SPACING_GRID["margin_page"]
    step = tab_h + gap
    start_y = h - SPACING_GRID["margin_page"] - (tab_index + 1) * step

    tx = w - tab_w
    ty = max(SPACING_GRID["bottom_safe"], start_y)

    bg_color = tok["tab_bg"] if active else tok["tab_inactive_bg"]
    text_color = tok["tab_text"] if active else tok["tab_inactive_text"]

    if th["round"]:
        pdf.round_rect(tx - 4, ty, tab_w + 4, tab_h, 4.0, fill=bg_color)
    else:
        pdf.fill(tx, ty, tab_w, tab_h, bg_color)

    # 1-2 character label or small abbreviation
    short = label[:3].upper()
    pdf.text_center(tx + tab_w / 2.0, ty + tab_h / 2.0 - 3.0, short, TYPE_SCALE["caption"], bold=True, color=text_color)


def draw_dotted_area(
    pdf: Any,
    x: float,
    y: float,
    width: float,
    height: float,
    spacing: float = 14.0,
    theme: Optional[Dict[str, Any]] = None,
) -> None:
    """Render a bullet-journal style 5mm/14pt dot grid inside the specified bounds."""
    th = theme or PREMIUM_THEMES["Calm Sage"]
    tok = th["tokens"]
    dot_color = tok["border"]

    cols = int(width // spacing)
    rows = int(height // spacing)
    offset_x = x + (width - (cols - 1) * spacing) / 2.0
    offset_y = y + (height - (rows - 1) * spacing) / 2.0

    r = 0.75  # radius of each dot
    for r_i in range(rows):
        cur_y = offset_y + r_i * spacing
        for c_i in range(cols):
            cur_x = offset_x + c_i * spacing
            pdf.circle(cur_x, cur_y, r, fill=dot_color)


def draw_lined_area(
    pdf: Any,
    x: float,
    y: float,
    width: float,
    height: float,
    line_spacing: float = 20.0,
    theme: Optional[Dict[str, Any]] = None,
) -> int:
    """Render subtle horizontal writing lines with an accent baseline."""
    th = theme or PREMIUM_THEMES["Calm Sage"]
    tok = th["tokens"]
    line_color = tok["line"]

    num_lines = int(height // line_spacing)
    cur_y = y + height - line_spacing
    for _ in range(num_lines):
        pdf.line(x, cur_y, x + width, cur_y, color=line_color, width=0.5)
        cur_y -= line_spacing

    return num_lines


def draw_grid_area(
    pdf: Any,
    x: float,
    y: float,
    width: float,
    height: float,
    cell_size: float = 18.0,
    theme: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int]:
    """Render a clean architectural square grid for math / layout / tracking."""
    th = theme or PREMIUM_THEMES["Calm Sage"]
    tok = th["tokens"]
    grid_color = tok["line"]

    cols = int(width // cell_size)
    rows = int(height // cell_size)
    w_actual = cols * cell_size
    h_actual = rows * cell_size

    # Outer border
    pdf.rect(x, y, w_actual, h_actual, color=tok["border"], width=0.6)

    # Vertical grid lines
    for c in range(1, cols):
        cx = x + c * cell_size
        pdf.line(cx, y, cx, y + h_actual, color=grid_color, width=0.4)

    # Horizontal grid lines
    for r in range(1, rows):
        ry = y + r * cell_size
        pdf.line(x, ry, x + w_actual, ry, color=grid_color, width=0.4)

    return (cols, rows)


def draw_habit_circles(
    pdf: Any,
    x: float,
    y: float,
    count: int = 7,
    radius: float = 5.0,
    labels: Optional[List[str]] = None,
    theme: Optional[Dict[str, Any]] = None,
) -> None:
    """Render a row of habit tracker check circles with optional weekday/number labels."""
    th = theme or PREMIUM_THEMES["Calm Sage"]
    tok = th["tokens"]
    step = (radius * 2) + 8.0

    for i in range(count):
        cx = x + radius + i * step
        cy = y
        # Outer ring
        pdf.circle(cx, cy, radius, stroke=tok["accent"], width=0.8)
        # Inner soft wash
        pdf.circle(cx, cy, radius - 1.2, fill=tok["surface_soft"])

        if labels and i < len(labels):
            lbl = labels[i][:2]
            pdf.text_center(cx, cy + radius + 3.0, lbl, TYPE_SCALE["micro"], bold=True, color=tok["muted"])


def draw_checkbox(
    pdf: Any,
    x: float,
    y: float,
    size: float = 10.0,
    style: str = "rounded",
    checked: bool = False,
    theme: Optional[Dict[str, Any]] = None,
) -> None:
    """Render a crisp designer checkbox (square, rounded, or circle)."""
    th = theme or PREMIUM_THEMES["Calm Sage"]
    tok = th["tokens"]
    border_color = tok["accent"] if checked else tok["border"]
    fill_color = tok["accent_soft"] if checked else None

    if style == "circle":
        r = size / 2.0
        pdf.circle(x + r, y + r, r, fill=fill_color, stroke=border_color, width=0.8)
    elif style == "rounded" or th.get("round"):
        pdf.round_rect(x, y, size, size, 2.5, fill=fill_color, stroke=border_color, width=0.8)
    else:
        pdf.rect(x, y, size, size, color=border_color, width=0.8)

    if checked:
        # Small check indicator tick lines
        pdf.line(x + 2.0, y + size / 2.0, x + size / 2.0 - 0.5, y + 2.5, color=tok["accent"], width=1.0)
        pdf.line(x + size / 2.0 - 0.5, y + 2.5, x + size - 2.0, y + size - 2.5, color=tok["accent"], width=1.0)


def draw_badge(
    pdf: Any,
    x: float,
    y: float,
    text: str,
    theme: Optional[Dict[str, Any]] = None,
) -> float:
    """Render a small pill badge with contrast-compliant text.
    Returns the badge width.
    """
    from pdf_design import text_width
    th = theme or PREMIUM_THEMES["Calm Sage"]
    tok = th["tokens"]
    font_size = TYPE_SCALE["micro"]
    tw = text_width(text.upper(), font_size, bold=True, spacing=0.5)
    pad_h = 7.0
    pad_v = 3.5
    bw = tw + 2 * pad_h
    bh = font_size + 2 * pad_v

    if th["round"]:
        pdf.round_rect(x, y - pad_v, bw, bh, bh / 2.0, fill=tok["badge_bg"], stroke=tok["border"], width=0.4)
    else:
        pdf.round_rect(x, y - pad_v, bw, bh, 2.0, fill=tok["badge_bg"], stroke=tok["border"], width=0.4)

    pdf.text_center(x + bw / 2.0, y, text.upper(), font_size, bold=True, color=tok["badge_text"], spacing=0.5)
    return bw


# ---------------------------------------------------------------------------
# 5. SPECIAL PAGE LAYOUTS
# ---------------------------------------------------------------------------

def draw_cover_page(
    pdf: Any,
    spec: Dict[str, Any],
    theme: Optional[Dict[str, Any]] = None,
    badge_text: str = "PREMIUM EDITION",
) -> None:
    """Draw a cover page showcasing title, subtitle, buyer badge, and table of contents."""
    from pdf_design import fit_text, wrap_text
    th = theme or get_theme_for_spec(spec)
    tok = th["tokens"]
    w, h = pdf.w, pdf.h
    margin = SPACING_GRID["margin_page"]

    pdf.new_page()

    # Outer decorative framed border
    inset = 18.0
    if th["round"]:
        pdf.round_rect(inset, inset, w - 2 * inset, h - 2 * inset, 12.0, stroke=tok["border"], width=0.8)
        # Inner hairline frame
        pdf.round_rect(inset + 4, inset + 4, w - 2 * (inset + 4), h - 2 * (inset + 4), 8.0, stroke=tok["line"], width=0.4)
    else:
        pdf.rect(inset, inset, w - 2 * inset, h - 2 * inset, color=tok["border"], width=0.8)
        pdf.rect(inset + 4, inset + 4, w - 2 * (inset + 4), h - 2 * (inset + 4), color=tok["line"], width=0.4)

    # Hero Banner Box
    title = spec.get("name") or (spec.get("listing") or {}).get("title") or "Printable Planner"
    buyer = (spec.get("listing") or {}).get("buyer") or "Personal Use"

    hero_y = h - 90.0
    draw_badge(pdf, margin + 8.0, hero_y, badge_text, theme=th)

    # Main Title
    room = w - 2 * margin - 32.0
    title_lines, title_sz = fit_text(title, TYPE_SCALE["display"], room, bold=True, max_lines=2, min_size=20.0)
    cur_y = hero_y - 30.0
    for line in title_lines:
        pdf.text(margin + 8.0, cur_y, line, title_sz, bold=True, color=tok["ink"])
        cur_y -= title_sz * 1.15

    # Subtitle / Buyer designation
    buyer_line = f"Designed specifically for {buyer}"
    sub_lines = wrap_text(buyer_line, TYPE_SCALE["body_lg"], room)
    cur_y -= 8.0
    for line in sub_lines[:2]:
        pdf.text(margin + 8.0, cur_y, line, TYPE_SCALE["body_lg"], bold=False, color=tok["muted"])
        cur_y -= 16.0

    # Horizontal divider rule with accent diamond or dot
    cur_y -= 12.0
    pdf.line(margin + 8.0, cur_y, w - margin - 8.0, cur_y, color=tok["accent"], width=1.0)
    pdf.circle(w / 2.0, cur_y, 2.5, fill=tok["accent"])

    # Inside contents preview / Belong to block
    content_y = cur_y - 32.0
    box_w = (w - 2 * margin - 16.0)

    # "This planner belongs to" luxury card
    card_h = 76.0
    card_y = content_y - card_h
    if th["round"]:
        pdf.round_rect(margin + 8.0, card_y, box_w, card_h, 8.0, fill=tok["surface_soft"], stroke=tok["border"], width=0.5)
    else:
        pdf.fill(margin + 8.0, card_y, box_w, card_h, tok["surface_soft"])
        pdf.rect(margin + 8.0, card_y, box_w, card_h, color=tok["border"], width=0.5)

    pdf.text(margin + 24.0, card_y + card_h - 22.0, "THIS PLANNER BELONGS TO:", TYPE_SCALE["body_sm"], bold=True, color=tok["accent"], spacing=1.0)
    pdf.line(margin + 24.0, card_y + 30.0, margin + box_w - 24.0, card_y + 30.0, color=tok["line"], width=0.8)
    pdf.line(margin + 24.0, card_y + 12.0, margin + box_w - 24.0, card_y + 12.0, color=tok["line"], width=0.8)

    # Footer note on cover
    footer_text = f"Theme: {th['name']} \u00b7 Designed by Chilli Gavva \u00b7 All rights reserved"
    pdf.text_center(w / 2.0, SPACING_GRID["footer_y"] + 4.0, footer_text, TYPE_SCALE["caption"], bold=False, color=tok["faded"])


def draw_divider_page(
    pdf: Any,
    section_title: str,
    section_number: int = 1,
    subtitle: str = "",
    theme: Optional[Dict[str, Any]] = None,
) -> None:
    """Draw a full-page divider / section separator with centered typography and thematic art."""
    th = theme or PREMIUM_THEMES["Calm Sage"]
    tok = th["tokens"]
    w, h = pdf.w, pdf.h
    margin = SPACING_GRID["margin_page"]

    pdf.new_page()

    # Fill background with theme's soft surface wash
    if tok.get("bg"):
        pdf.fill(0, 0, w, h, tok["bg"])

    # Center hero layout
    center_y = h * 0.55
    center_x = w / 2.0

    # Section index pill
    num_str = f"SECTION 0{section_number}" if section_number < 10 else f"SECTION {section_number}"
    draw_badge(pdf, center_x - 45.0, center_y + 60.0, num_str, theme=th)

    # Main Section Heading
    from pdf_design import fit_text, text_width
    lines, sz = fit_text(section_title.upper(), TYPE_SCALE["title_lg"], w - 2 * margin - 40.0, bold=True, max_lines=2, min_size=18.0)
    cur_y = center_y + 20.0
    for line in lines:
        tw = text_width(line, sz, bold=True)
        pdf.text(center_x - tw / 2.0, cur_y, line, sz, bold=True, color=tok["ink"])
        cur_y -= sz * 1.2

    # Accent rule
    cur_y -= 10.0
    pdf.line(center_x - 60.0, cur_y, center_x + 60.0, cur_y, color=tok["accent"], width=1.5)

    if subtitle:
        cur_y -= 22.0
        from pdf_design import wrap_text
        sub_lines = wrap_text(subtitle, TYPE_SCALE["body"], w - 2 * margin - 60.0)
        for line in sub_lines[:2]:
            tw = text_width(line, TYPE_SCALE["body"], bold=False)
            pdf.text(center_x - tw / 2.0, cur_y, line, TYPE_SCALE["body"], bold=False, color=tok["muted"])
            cur_y -= 14.0

    # Subtle bottom decorative dots
    dot_y = cur_y - 25.0
    for i in range(-2, 3):
        pdf.circle(center_x + i * 12.0, dot_y, 1.5, fill=tok["border"])


def draw_how_to_use_page(
    pdf: Any,
    spec: Dict[str, Any],
    theme: Optional[Dict[str, Any]] = None,
    week_start: Optional[str] = None,
) -> None:
    """Draw a structured 'How to use' page incorporating instructions, printing tips, and notes."""
    th = theme or get_theme_for_spec(spec)
    tok = th["tokens"]
    w, h = pdf.w, pdf.h
    margin = SPACING_GRID["margin_page"]

    pdf.new_page()

    # Header
    top_y = draw_header_band(
        pdf,
        title="How to Use This Planner",
        subtitle="Quick guidance to get the most from your daily & weekly pages",
        badge="GUIDE",
        theme=th,
    )

    steps = [s for s in (spec.get("instructions") or []) if s][:5] or [
        "Choose either the A4 or US Letter PDF file matching your regional paper standard.",
        "Print desired pages at 100% scale (or 'Actual Size') without page scaling.",
        "Bind using a disc-bound system, ring binder, or keep loose in a clean folio.",
        "Set aside 5 quiet minutes each morning to review anchors and top 3 priorities.",
        "Keep the planner open on your desk throughout the day for easy note-taking.",
    ]

    tips = [
        "High quality 100gsm+ paper prevents any ink bleed-through.",
        "Both A4 and US Letter sizes are formatted with generous margin gutters.",
        "Print as many fresh copies as you need for your personal routine.",
    ]
    if week_start:
        tips.append(f"Calendar dated pages begin on {week_start} with weekend shading.")

    cur_y = top_y

    # Steps Container Card
    from pdf_design import wrap_text
    card_w = w - 2 * margin
    pdf.text(margin, cur_y, "STEP-BY-STEP WORKFLOW", TYPE_SCALE["body_sm"], bold=True, color=tok["accent"], spacing=0.8)
    cur_y -= 16.0

    for i, step in enumerate(steps, 1):
        # Step number circle
        pdf.circle(margin + 10.0, cur_y - 2.0, 7.5, fill=tok["accent_soft"], stroke=tok["accent"], width=0.8)
        pdf.text_center(margin + 10.0, cur_y - 5.0, str(i), TYPE_SCALE["body_sm"], bold=True, color=tok["accent"])

        lines = wrap_text(step, TYPE_SCALE["body"], card_w - 36.0)
        line_y = cur_y - 1.0
        for line in lines:
            pdf.text(margin + 26.0, line_y, line, TYPE_SCALE["body"], bold=False, color=tok["ink"])
            line_y -= 13.5
        cur_y = min(cur_y - 26.0, line_y - 8.0)

    # Printing Tips Card
    cur_y -= 10.0
    pdf.text(margin, cur_y, "PRINTING & BINDING TIPS", TYPE_SCALE["body_sm"], bold=True, color=tok["accent"], spacing=0.8)
    cur_y -= 16.0

    for tip in tips:
        pdf.circle(margin + 8.0, cur_y - 2.0, 2.5, fill=tok["accent"])
        lines = wrap_text(tip, TYPE_SCALE["body"], card_w - 28.0)
        line_y = cur_y - 1.0
        for line in lines:
            pdf.text(margin + 20.0, line_y, line, TYPE_SCALE["body"], bold=False, color=tok["ink"])
            line_y -= 13.5
        cur_y = min(cur_y - 20.0, line_y - 6.0)

    # Legend / Key Block at the bottom
    cur_y -= 12.0
    legend_h = 58.0
    if th["round"]:
        pdf.round_rect(margin, cur_y - legend_h, card_w, legend_h, 6.0, fill=tok["surface_soft"], stroke=tok["border"], width=0.5)
    else:
        pdf.fill(margin, cur_y - legend_h, card_w, legend_h, tok["surface_soft"])
        pdf.rect(margin, cur_y - legend_h, card_w, legend_h, color=tok["border"], width=0.5)

    pdf.text(margin + 14.0, cur_y - 18.0, "SYMBOL LEGEND", TYPE_SCALE["micro"], bold=True, color=tok["accent"], spacing=1.0)
    # Checkbox symbol
    draw_checkbox(pdf, margin + 14.0, cur_y - 42.0, size=9.0, style="rounded", checked=True, theme=th)
    pdf.text(margin + 28.0, cur_y - 40.0, "= Action completed", TYPE_SCALE["body_sm"], color=tok["muted"])
    # Habit circle symbol
    draw_habit_circles(pdf, margin + 150.0, cur_y - 38.0, count=1, radius=4.5, theme=th)
    pdf.text(margin + 164.0, cur_y - 40.0, "= Daily routine target", TYPE_SCALE["body_sm"], color=tok["muted"])
    # Dot symbol
    pdf.circle(margin + 300.0, cur_y - 38.0, 2.5, fill=tok["accent"])
    pdf.text(margin + 310.0, cur_y - 40.0, "= Event / Key note", TYPE_SCALE["body_sm"], color=tok["muted"])


def draw_legend(
    pdf: Any,
    x: float,
    y: float,
    width: float,
    theme: Optional[Dict[str, Any]] = None,
) -> float:
    """Draw a modular icon & visual indicator legend strip."""
    th = theme or PREMIUM_THEMES["Calm Sage"]
    tok = th["tokens"]
    h = 32.0

    if th["round"]:
        pdf.round_rect(x, y - h, width, h, 4.0, fill=tok["surface_soft"], stroke=tok["border"], width=0.4)
    else:
        pdf.fill(x, y - h, width, h, tok["surface_soft"])
        pdf.rect(x, y - h, width, h, color=tok["border"], width=0.4)

    # 3 items horizontally spaced
    step = width / 3.0

    # 1: Checkbox
    draw_checkbox(pdf, x + 10.0, y - 20.0, size=8.0, checked=True, theme=th)
    pdf.text(x + 24.0, y - 19.0, "Complete", TYPE_SCALE["micro"], bold=True, color=tok["muted"])

    # 2: In-progress
    draw_checkbox(pdf, x + step + 10.0, y - 20.0, size=8.0, checked=False, theme=th)
    pdf.text(x + step + 24.0, y - 19.0, "In Progress", TYPE_SCALE["micro"], bold=True, color=tok["muted"])

    # 3: Target circle
    pdf.circle(x + 2 * step + 14.0, y - 16.0, 4.0, stroke=tok["accent"], width=0.8)
    pdf.text(x + 2 * step + 24.0, y - 19.0, "Habit Goal", TYPE_SCALE["micro"], bold=True, color=tok["muted"])

    return h


# ---------------------------------------------------------------------------
# 6. EXPORTED DESIGN SYSTEM INTERFACE
# ---------------------------------------------------------------------------

__all__ = [
    "PREMIUM_THEMES",
    "THEME_NAME_MAP",
    "NICHE_THEME_MAP",
    "TYPE_SCALE",
    "SPACING_GRID",
    "get_theme",
    "get_theme_for_spec",
    "list_theme_names",
    "normalize_theme_name",
    "contrast_ratio",
    "hex_to_rgb",
    "relative_luminance",
    "theme_to_pdf_design_style",
    "register_themes_in_pdf_design",
    "draw_header_band",
    "draw_page_number",
    "draw_section_tab",
    "draw_dotted_area",
    "draw_lined_area",
    "draw_grid_area",
    "draw_habit_circles",
    "draw_checkbox",
    "draw_badge",
    "draw_cover_page",
    "draw_divider_page",
    "draw_how_to_use_page",
    "draw_legend",
]
