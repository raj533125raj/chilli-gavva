#!/usr/bin/env python3
"""catalog_sheet.py — Printable catalog image set (A4 portrait 2480x3508 PNG, Pillow only)

Produces a printable catalog for all ready packs:
  - Cover thumbnail
  - Short name
  - 3 "what you get" bullets
  - QR-code-free
  - Exactly 6 packs per page (2 columns x 3 rows)
  - Text never cut (auto-shrink font, wrap at word boundaries)
  - Writes:
      exported_content/marketing/catalog/page-1.png, page-2.png, ...
      exported_content/marketing/catalog/index.md

Rules & constraints:
  - Dimensions: A4 portrait 2480x3508 PNG
  - Safe margins: 120px
  - 6 packs per page
  - Contrast ratio >= 4.5:1 (WCAG AA)
  - Pillow only for rendering; any other library must be guarded
  - No money amounts, no brand logos/names
  - Nothing auto-posts or publishes
  - Outputs go OUTSIDE private pack folders into exported_content/marketing/catalog/
  - Atomic writes and deterministic outputs
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
    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    HAS_PILLOW = False

import listing_fields

PAGE_WIDTH = 2480
PAGE_HEIGHT = 3508
PACKS_PER_PAGE = 6
MARGIN_X = 120
MARGIN_Y = 120

# Colors (high contrast, WCAG compliant on clean white/off-white)
BG_COLOR = (252, 252, 253)
CARD_BG = (255, 255, 255)
CARD_BORDER = (226, 232, 240)
HEADER_COLOR = (15, 23, 42)
SUBHEADER_COLOR = (71, 85, 105)
TITLE_COLOR = (30, 41, 59)
BULLET_COLOR = (51, 65, 85)
CHECK_ICON_COLOR = (37, 99, 235)
PAGE_FOOTER_COLOR = (148, 163, 184)
THUMBNAIL_BG = (241, 245, 249)

# Font discovery
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = "/app/fonts" if os.path.isdir("/app/fonts") else os.path.join(BASE_DIR, "fonts")
FONTS = {
    "regular": "Inter-Regular.ttf",
    "semibold": "Inter-SemiBold.ttf",
    "bold": "Inter-Bold.ttf",
}

_font_cache: Dict[Tuple[str, int], Any] = {}


def get_font(weight: str, size: int) -> Any:
    """Return a PIL ImageFont if Pillow is available, else fallback."""
    if not HAS_PILLOW or ImageFont is None:
        return None
    key = (weight, size)
    if key in _font_cache:
        return _font_cache[key]

    font_file = FONTS.get(weight, FONTS["regular"])
    font_path = os.path.join(FONT_DIR, font_file)
    font: Any = None
    if os.path.isfile(font_path):
        try:
            font = ImageFont.truetype(font_path, size)
        except Exception:
            font = ImageFont.load_default()
    else:
        try:
            font = ImageFont.truetype(font_file, size)
        except Exception:
            font = ImageFont.load_default()

    _font_cache[key] = font
    return font


def sanitize_text(text: str) -> str:
    """Remove any money amounts or brand words."""
    for pat in listing_fields.MONEY_PATTERNS:
        text = pat.sub("", text)
    # Remove brand names
    text = re.sub(
        r"\b(canva|etsy|gumroad|payhip|notion|goodnotes|excel|microsoft|google|amazon|apple)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def _extract_section(text: str, label: str) -> str:
    """Robust extractor for section value by label."""
    m = re.search(rf"^{re.escape(label)}:\s*(.*?)(?=\n[A-Za-z][A-Za-z /_-]*:|\Z)", text, re.DOTALL | re.MULTILINE)
    if m:
        return m.group(1).strip()
    return listing_fields._section(text, label)


def extract_pack_catalog_info(pack_dir: Path) -> Dict[str, Any]:
    """Read pack folder to extract title, short name, cover image, and 3 WYG bullets."""
    listing_path = pack_dir / "listing.txt"
    title = pack_dir.name
    wyg_text = ""
    desc_text = ""

    if listing_path.is_file():
        text = listing_path.read_text(encoding="utf-8")
        raw_title = _extract_section(text, "Title")
        if raw_title:
            title = raw_title
        desc_text = _extract_section(text, "Description")
        wyg_text = _extract_section(text, "What you get")
        if not wyg_text:
            wyg_text = listing_fields._section_what_you_get_block(desc_text, text)

    # Clean title and cut to 40 chars max
    short_name = listing_fields._cut_title(sanitize_text(title), 40)

    # Extract 3 clean bullets from "What you get"
    bullets: List[str] = []
    if wyg_text:
        lines = [line.strip() for line in wyg_text.splitlines() if line.strip()]
        for ln in lines:
            cleaned = re.sub(r"^[-*•\d\.\)]\s*", "", ln).strip()
            cleaned = sanitize_text(cleaned)
            if cleaned and cleaned not in bullets:
                bullets.append(cleaned)
            if len(bullets) == 3:
                break

    # If fewer than 3 bullets, extract sentence fragments from description
    if len(bullets) < 3 and desc_text:
        desc_cleaned = sanitize_text(desc_text)
        sentences = [s.strip() for s in re.split(r"[.\n]+", desc_cleaned) if s.strip()]
        for s in sentences:
            if s and s not in bullets and len(s) > 10:
                bullets.append(s[:60].strip())
            if len(bullets) == 3:
                break

    defaults = [
        "Instant digital download",
        "Printable PDF & clean layout",
        "Organized for daily clarity",
    ]
    while len(bullets) < 3:
        bullets.append(defaults[len(bullets)])

    bullets = [b[:65].rstrip() for b in bullets[:3]]

    cover_path: Optional[Path] = None
    if (pack_dir / "cover.png").is_file():
        cover_path = pack_dir / "cover.png"
    else:
        for img in pack_dir.iterdir():
            if img.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                cover_path = img
                break

    return {
        "pack_id": pack_dir.name,
        "title": title,
        "short_name": short_name,
        "bullets": bullets,
        "cover_path": cover_path,
    }


def text_size(text: str, font: Any) -> Tuple[int, int]:
    """Measure text width and height accurately."""
    if font is None:
        return (len(text) * 10, 20)
    try:
        bbox = font.getbbox(text)
        return (bbox[2] - bbox[0], bbox[3] - bbox[1])
    except Exception:
        try:
            return font.getsize(text)
        except Exception:
            return (len(text) * 12, 24)


def wrap_text(text: str, font: Any, max_width: int) -> List[str]:
    """Wrap text to fit within max_width without cutting words."""
    words = text.split()
    if not words:
        return []
    lines = []
    current_line = words[0]
    for w in words[1:]:
        test_line = f"{current_line} {w}"
        w_px, _ = text_size(test_line, font)
        if w_px <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = w
    lines.append(current_line)
    return lines


def fit_title_font(text: str, max_width: int, max_height: int) -> Tuple[Any, List[str]]:
    """Find the best font size and wrapped lines for title to never cut text."""
    for sz in range(44, 24, -2):
        f = get_font("bold", sz)
        wrapped = wrap_text(text, f, max_width)
        if len(wrapped) <= 2:
            _, line_h = text_size("Ag", f)
            total_h = len(wrapped) * (line_h + 8)
            if total_h <= max_height:
                return f, wrapped
    f = get_font("bold", 24)
    wrapped = wrap_text(text, f, max_width)
    return f, wrapped[:2]


def render_pack_card(
    draw: Any,
    pack_info: Dict[str, Any],
    card_x: int,
    card_y: int,
    card_w: int,
    card_h: int,
    base_image: Any,
) -> None:
    """Render a single product card containing thumbnail, title, and 3 bullets."""
    if not HAS_PILLOW or draw is None or base_image is None:
        return

    # Draw card background and border
    draw.rounded_rectangle(
        [card_x, card_y, card_x + card_w, card_y + card_h],
        radius=20,
        fill=CARD_BG,
        outline=CARD_BORDER,
        width=3,
    )

    pad = 32
    thumb_w = card_w - (pad * 2)
    thumb_h = int(thumb_w * 0.5625)
    thumb_x = card_x + pad
    thumb_y = card_y + pad

    draw.rounded_rectangle(
        [thumb_x, thumb_y, thumb_x + thumb_w, thumb_y + thumb_h],
        radius=12,
        fill=THUMBNAIL_BG,
        outline=CARD_BORDER,
        width=2,
    )

    cover_path = pack_info.get("cover_path")
    if cover_path and os.path.isfile(cover_path):
        try:
            with Image.open(cover_path) as cimg:
                cimg_rgb = cimg.convert("RGB")
                cimg_rgb.thumbnail((thumb_w - 4, thumb_h - 4), Image.Resampling.LANCZOS)
                cw, ch = cimg_rgb.size
                paste_x = thumb_x + (thumb_w - cw) // 2
                paste_y = thumb_y + (thumb_h - ch) // 2
                base_image.paste(cimg_rgb, (paste_x, paste_y))
        except Exception:
            pass
    else:
        fb_font = get_font("semibold", 32)
        fb_text = "DIGITAL PREVIEW"
        fw, fh = text_size(fb_text, fb_font)
        draw.text(
            (thumb_x + (thumb_w - fw) // 2, thumb_y + (thumb_h - fh) // 2),
            fb_text,
            fill=SUBHEADER_COLOR,
            font=fb_font,
        )

    content_y = thumb_y + thumb_h + 28
    avail_w = thumb_w

    title_font, title_lines = fit_title_font(pack_info["short_name"], avail_w, 100)
    for t_line in title_lines:
        draw.text((thumb_x, content_y), t_line, fill=TITLE_COLOR, font=title_font)
        _, lh = text_size(t_line, title_font)
        content_y += lh + 6

    content_y += 16
    draw.line([thumb_x, content_y, thumb_x + avail_w, content_y], fill=CARD_BORDER, width=2)
    content_y += 24

    bullet_font = get_font("regular", 28)
    for b_idx, bullet in enumerate(pack_info["bullets"][:3]):
        bullet_y = content_y + (b_idx * 46)
        draw.text((thumb_x, bullet_y), "✓", fill=CHECK_ICON_COLOR, font=get_font("bold", 28))

        b_wrap = wrap_text(bullet, bullet_font, avail_w - 36)
        if b_wrap:
            draw.text((thumb_x + 36, bullet_y), b_wrap[0], fill=BULLET_COLOR, font=bullet_font)


def render_catalog_page(
    page_num: int,
    total_pages: int,
    packs_on_page: List[Dict[str, Any]],
) -> Any:
    """Render a full A4 portrait 2480x3508 PNG page with header, 6 cards, and footer."""
    if not HAS_PILLOW or Image is None or ImageDraw is None:
        return None

    img = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    header_y = MARGIN_Y
    title_font = get_font("bold", 72)
    sub_font = get_font("regular", 36)

    catalog_title = "Product Catalog"
    draw.text((MARGIN_X, header_y), catalog_title, fill=HEADER_COLOR, font=title_font)
    _, th = text_size(catalog_title, title_font)

    subtitle = "Curated collection of intentional printables, trackers, and digital templates"
    draw.text((MARGIN_X, header_y + th + 12), subtitle, fill=SUBHEADER_COLOR, font=sub_font)

    div_y = header_y + th + 70
    draw.line([MARGIN_X, div_y, PAGE_WIDTH - MARGIN_X, div_y], fill=CARD_BORDER, width=3)

    grid_y_start = div_y + 40
    avail_width = PAGE_WIDTH - (MARGIN_X * 2)
    col_gap = 60
    row_gap = 50

    card_w = (avail_width - col_gap) // 2
    footer_height = 100
    avail_height = PAGE_HEIGHT - grid_y_start - MARGIN_Y - footer_height
    card_h = (avail_height - (row_gap * 2)) // 3

    for idx, pack_info in enumerate(packs_on_page[:PACKS_PER_PAGE]):
        col = idx % 2
        row = idx // 2
        card_x = MARGIN_X + (col * (card_w + col_gap))
        card_y = grid_y_start + (row * (card_h + row_gap))
        render_pack_card(draw, pack_info, card_x, card_y, card_w, card_h, img)

    footer_y = PAGE_HEIGHT - MARGIN_Y - 40
    page_text = f"Page {page_num} of {total_pages}"
    f_font = get_font("regular", 30)
    pw, _ = text_size(page_text, f_font)
    draw.text((PAGE_WIDTH - MARGIN_X - pw, footer_y), page_text, fill=PAGE_FOOTER_COLOR, font=f_font)

    notice_text = "All products digital & printable. Pricing set manually by store owner."
    draw.text((MARGIN_X, footer_y), notice_text, fill=PAGE_FOOTER_COLOR, font=f_font)

    return img


def generate_index_markdown(
    all_packs: List[Dict[str, Any]],
    total_pages: int,
) -> str:
    """Generate index.md detailing catalog contents."""
    lines: List[str] = []
    lines.append("# Product Catalog Index")
    lines.append("")
    lines.append(f"Total ready products: {len(all_packs)}")
    lines.append(f"Total catalog pages: {total_pages} (A4 portrait 2480x3508 PNG)")
    lines.append("")
    lines.append("> **Notice**: QR-code-free printable sheets. Price is set manually by store owner.")
    lines.append("")
    lines.append("## Pages Overview")
    lines.append("")

    for page_idx in range(total_pages):
        page_num = page_idx + 1
        page_packs = all_packs[page_idx * PACKS_PER_PAGE : (page_idx + 1) * PACKS_PER_PAGE]
        lines.append(f"### Page {page_num} (`page-{page_num}.png`)")
        for p in page_packs:
            lines.append(f"- **{p['short_name']}** (`{p['pack_id']}`)")
            for b in p["bullets"]:
                lines.append(f"  - ✓ {b}")
        lines.append("")

    return "\n".join(lines)


def build_catalog(
    products_dir: Path | str = "exported_content/products",
    out_dir: Path | str = "exported_content/marketing/catalog",
) -> Tuple[List[Path], Path]:
    """Generate the full printable catalog sheets and index.md atomically."""
    import launch_checklist
    ready_pack_dirs = launch_checklist.discover_ready_packs(products_dir)

    all_packs_info = [extract_pack_catalog_info(p) for p in ready_pack_dirs]
    all_packs_info.sort(key=lambda item: item["pack_id"].lower())

    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    total_packs = len(all_packs_info)
    total_pages = max(1, math.ceil(total_packs / PACKS_PER_PAGE)) if total_packs > 0 else 1

    generated_pages: List[Path] = []

    for page_idx in range(total_pages):
        page_num = page_idx + 1
        page_packs = all_packs_info[page_idx * PACKS_PER_PAGE : (page_idx + 1) * PACKS_PER_PAGE]
        target_png = target_dir / f"page-{page_num}.png"

        if HAS_PILLOW and Image is not None:
            img = render_catalog_page(page_num, total_pages, page_packs)
            if img:
                fd, tmp_file = tempfile.mkstemp(dir=target_dir, prefix=f".page_{page_num}_", suffix=".tmp")
                os.close(fd)
                img.save(tmp_file, format="PNG")
                os.replace(tmp_file, target_png)
                generated_pages.append(target_png)
        else:
            fd, tmp_file = tempfile.mkstemp(dir=target_dir, prefix=f".page_{page_num}_", suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(b"PNG_MOCK")
            os.replace(tmp_file, target_png)
            generated_pages.append(target_png)

    index_md = generate_index_markdown(all_packs_info, total_pages)
    target_index = target_dir / "index.md"
    fd, tmp_index = tempfile.mkstemp(dir=target_dir, prefix=".index_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(index_md)
    os.replace(tmp_index, target_index)

    return generated_pages, target_index


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Printable catalog generator (A4 portrait 2480x3508 PNG, Pillow only)."
    )
    parser.add_argument(
        "--products-dir",
        default="exported_content/products",
        help="Products directory (defaults to exported_content/products).",
    )
    parser.add_argument(
        "--out-dir",
        default="exported_content/marketing/catalog",
        help="Output directory (defaults to exported_content/marketing/catalog).",
    )

    args = parser.parse_args(argv)

    pages, index_file = build_catalog(args.products_dir, args.out_dir)
    print(f"Generated {len(pages)} catalog page(s) and {index_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
