"""mockups_v2.py - procedural listing mockups built from a pack's real pages.

Eight deterministic scenes are drawn with Pillow only: tablet on a desk, clipboard,
printed sheets fan, flat lay with pen and coffee, phone screen, binder with tabs,
wall frame, and a stack of pages with a paper clip.  A pack can be any product
folder with page images or a PDF built by this repo.  When no page can be read,
a clean paper fallback is drawn instead of inventing a product.

Every scene is rendered at 2000x2000, with a 1000x1500 pin and a 1080x1080 square
for social stores.  Outputs are JPEG quality 88 and PNG, plus an alt-text JSON.
"""
from __future__ import annotations

import argparse
import colorsys
import json
import math
import os
import random
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BASE_DIR = Path(__file__).resolve().parent
FONT_DIR = BASE_DIR / "fonts"
LISTING_SIZE = (2000, 2000)
PIN_SIZE = (1000, 1500)
SQUARE_SIZE = (1080, 1080)
JPEG_QUALITY = 88
PAGE_SIZE = (1200, 1600)
THUMB_SIZE = (420, 560)
MAX_SOURCE_PAGES = 5
DEFAULT_THEME = {
    "bg": (235, 242, 240),
    "accent": (47, 125, 120),
    "ink": (24, 48, 46),
    "body": (69, 91, 88),
    "paper": (252, 250, 244),
    "desk": (215, 199, 176),
    "shadow": (35, 43, 45),
}
FONT_FILES = {
    "regular": "Inter-Regular.ttf",
    "semibold": "Inter-SemiBold.ttf",
    "bold": "Inter-Bold.ttf",
}
SCENE_NAMES = (
    "tablet_desk",
    "clipboard",
    "sheets_fan",
    "flat_lay",
    "phone_screen",
    "binder",
    "wall_frame",
    "page_stack",
)
SCENE_TITLES = {
    "tablet_desk": "On a tablet",
    "clipboard": "On a clipboard",
    "sheets_fan": "Printed sheets",
    "flat_lay": "Desk flat lay",
    "phone_screen": "On a phone",
    "binder": "In a binder",
    "wall_frame": "Framed on the wall",
    "page_stack": "Printed stack",
}
_BRANDS = (
    r"google\s+sheets?", r"google\s+docs?", r"microsoft\s+excel", r"microsoft\s+word",
    r"\bexcel\b", r"\bnotion\b", r"\bcanva\b", r"\betsy\b", r"\bgumroad\b", r"\bpayhip\b",
    r"\bamazon\b", r"\bapple\b", r"\bgoodnotes\b", r"\bipad\b", r"\blinkedin\b",
    r"\bgoogle\b", r"\bmicrosoft\b", r"\bchatgpt\b", r"\bopenai\b",
)
_PRICE_RE = re.compile(
    r"(?:[$€£₹]\s?\d[\d.,]*|\b(?:usd|eur|gbp|inr)\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:usd|eur|gbp|inr|dollars?)\b)",
    re.IGNORECASE,
)
_REVIEW_RE = re.compile(
    r"\b(?:five[- ]star|5[- ]star|best[- ]?seller|testimonials?|reviews?|rated\s+\d(?:\.\d)?)\b",
    re.IGNORECASE,
)
_PLAIN = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "‑": "-", "‐": "-", "\u00a0": " "}
_font_cache: Dict[Tuple[str, int], Any] = {}


def _pil():
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
    return Image, ImageDraw, ImageFilter, ImageFont, ImageOps


def available() -> bool:
    """True when Pillow can be imported; no other package is needed."""
    try:
        _pil()
        return True
    except Exception:
        return False


def font(weight: str, size: int):
    """One cached Inter font at a whole-pixel size."""
    _, _, _, ImageFont, _ = _pil()
    key = (weight if weight in FONT_FILES else "regular", max(1, int(size)))
    if key not in _font_cache:
        path = FONT_DIR / FONT_FILES[key[0]]
        try:
            _font_cache[key] = ImageFont.truetype(str(path), key[1])
        except OSError:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def plain_text(text: Any) -> str:
    """Plain ASCII with curly quotes and dashes normalised, accents dropped."""
    out = "".join(_PLAIN.get(ch, ch) for ch in str(text or ""))
    return unicodedata.normalize("NFKD", out).encode("ascii", "ignore").decode("ascii")


def safe_text(text: Any) -> str:
    """Remove prices, review claims, URLs and other-store brand names."""
    out = plain_text(text)
    out = re.sub(r"https?://\S+", " ", out, flags=re.IGNORECASE)
    for pattern in _BRANDS:
        out = re.sub(pattern, " ", out, flags=re.IGNORECASE)
    out = _PRICE_RE.sub(" ", out)
    out = _REVIEW_RE.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip(" -|,.;:")
    return out


def text_width(text: str, f) -> float:
    if hasattr(f, "getlength"):
        return float(f.getlength(text))
    return float(f.getsize(text)[0])


def line_height(f, factor: float = 1.18) -> int:
    return max(1, int(getattr(f, "size", 12) * factor))


def _split_long_word(word: str, f, max_width: int) -> List[str]:
    parts, current = [], ""
    for ch in word:
        trial = current + ch
        if current and text_width(trial, f) > max_width:
            parts.append(current)
            current = ch
        else:
            current = trial
    if current:
        parts.append(current)
    return parts or [""]


def wrap_text(text: str, f, max_width: int) -> List[str]:
    """Wrap on spaces, and split an over-wide word so no line ever exceeds max_width."""
    words = safe_text(text).split()
    if not words:
        return [""]
    lines: List[str] = []
    current = ""
    for word in words:
        if text_width(word, f) > max_width:
            if current:
                lines.append(current)
                current = ""
            pieces = _split_long_word(word, f, max_width)
            lines.extend(pieces[:-1])
            current = pieces[-1]
            continue
        trial = f"{current} {word}".strip()
        if current and text_width(trial, f) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [""]


def fit_text(text: str, weight: str, max_width: int, max_lines: int = 2,
             max_size: int = 64, min_size: int = 18):
    """Largest font from max_size down to min_size that fits the width and line budget."""
    size = max(1, int(max_size))
    while True:
        f = font(weight, size)
        lines = wrap_text(text, f, max_width)
        widths_ok = all(text_width(line, f) <= max_width for line in lines)
        if widths_ok and len(lines) <= max_lines:
            return lines, f
        if size <= min_size:
            return lines, f
        size = max(min_size, size - 2)


def draw_text(draw, xy: Tuple[int, int], text: str, f, fill, spacing: int = 0) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill)
        x += text_width(ch, f) + spacing


def mix(a: Sequence[int], b: Sequence[int], t: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, float(t)))
    return tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in range(3))


def luminance(color: Sequence[int]) -> float:
    r, g, b = [float(c) / 255.0 for c in color[:3]]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_text(color: Sequence[int]) -> Tuple[int, int, int]:
    return (255, 255, 255) if luminance(color) < 0.55 else (23, 28, 30)


def theme_from_accent(accent: Sequence[int]) -> Dict[str, Tuple[int, int, int]]:
    accent = tuple(int(max(0, min(255, c))) for c in accent[:3])
    ink = mix(accent, (12, 20, 22), 0.72)
    return {
        "bg": mix(accent, (255, 255, 255), 0.88),
        "accent": accent,
        "ink": ink,
        "body": mix(ink, (90, 105, 108), 0.35),
        "paper": (252, 250, 244),
        "desk": mix(accent, (210, 190, 158), 0.82),
        "shadow": mix(accent, (20, 26, 28), 0.9),
    }


def theme_from_cover(path: Optional[Path]) -> Dict[str, Tuple[int, int, int]]:
    """Read the cover's strongest usable colour; default teal when there is no cover."""
    if not path or not Path(path).is_file():
        return dict(DEFAULT_THEME)
    try:
        Image, _, _, _, ImageOps = _pil()
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB").resize((48, 48))
            colors = im.quantize(colors=16).convert("RGB").getcolors(48 * 48) or []
        best, best_score = None, -1.0
        for count, rgb in colors:
            h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
            if s < 0.12 or v < 0.15 or v > 0.97:
                continue
            score = count * (0.35 + s) * (1.0 - abs(v - 0.55))
            if score > best_score:
                best, best_score = rgb, score
        if best:
            return theme_from_accent(best)
    except Exception:
        pass
    return dict(DEFAULT_THEME)


def paper_texture(size: Tuple[int, int], seed: int = 0, amount: int = 5):
    """A deterministic, low-contrast paper-grain layer."""
    Image, _, ImageFilter, _, _ = _pil()
    rng = random.Random(int(seed))
    small = Image.new("L", (64, 64))
    pixels = [128 + rng.randint(-amount, amount) for _ in range(64 * 64)]
    small.putdata(pixels)
    grain = small.resize(size, Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(0.4))
    return grain


def apply_texture(image, seed: int = 0, amount: int = 5, opacity: float = 0.16):
    Image, _, _, _, _ = _pil()
    rgb = image.convert("RGB")
    grain = paper_texture(rgb.size, seed, amount).convert("RGB")
    return Image.blend(rgb, grain, max(0.0, min(1.0, opacity)))


def add_shadow(canvas, shape, position: Tuple[int, int], blur: int = 18,
               opacity: float = 0.35, offset: Tuple[int, int] = (12, 18)) -> None:
    """Paste a soft shadow made from a shape's alpha, then let the caller paste the shape."""
    Image, _, ImageFilter, _, _ = _pil()
    alpha = shape.convert("RGBA").split()[3].point(lambda a: int(a * opacity))
    shadow = Image.new("RGBA", shape.size, (18, 22, 24, 0))
    shadow.putalpha(alpha)
    pad = max(4, int(blur) * 2)
    big = Image.new("RGBA", (shape.width + 2 * pad, shape.height + 2 * pad), (0, 0, 0, 0))
    big.alpha_composite(shadow, (pad, pad))
    big = big.filter(ImageFilter.GaussianBlur(blur))
    x, y = position[0] + offset[0] - pad, position[1] + offset[1] - pad
    canvas.alpha_composite(big, (max(0, x), max(0, y))) if x >= 0 and y >= 0 else _paste_clip(canvas, big, x, y)


def _paste_clip(canvas, layer, x: int, y: int) -> None:
    left, top = max(0, -x), max(0, -y)
    right, bottom = min(layer.width, canvas.width - x), min(layer.height, canvas.height - y)
    if right > left and bottom > top:
        canvas.alpha_composite(layer.crop((left, top, right, bottom)), (x + left, y + top))


def paste_rgba(canvas, layer, position: Tuple[int, int]) -> None:
    _paste_clip(canvas, layer.convert("RGBA"), int(position[0]), int(position[1]))


def rounded_layer(size: Tuple[int, int], radius: int, color, border: int = 0,
                  border_color=(0, 0, 0, 255)):
    Image, ImageDraw, _, _, _ = _pil()
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=color,
                        outline=border_color if border else None, width=border)
    return layer


def fit_page(image, size: Tuple[int, int] = PAGE_SIZE):
    """Fit a real page into a paper-shaped canvas without stretching it."""
    Image, _, _, _, ImageOps = _pil()
    im = image.convert("RGB")
    fitted = ImageOps.contain(im, size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, (252, 250, 244))
    canvas.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return canvas


def _solve(matrix: List[List[float]], values: List[float]) -> List[float]:
    n = len(values)
    a = [row[:] + [values[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("singular perspective matrix")
        a[col], a[pivot] = a[pivot], a[col]
        div = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= div
        for r in range(n):
            if r == col:
                continue
            factor = a[r][col]
            for j in range(col, n + 1):
                a[r][j] -= factor * a[col][j]
    return [a[i][n] for i in range(n)]


def find_coeffs(dest_quad: Sequence[Tuple[float, float]],
                src_quad: Sequence[Tuple[float, float]]) -> List[float]:
    """Perspective coefficients mapping destination corners back to source corners."""
    matrix: List[List[float]] = []
    values: List[float] = []
    for (dx, dy), (sx, sy) in zip(dest_quad, src_quad):
        matrix.append([dx, dy, 1, 0, 0, 0, -sx * dx, -sx * dy])
        matrix.append([0, 0, 0, dx, dy, 1, -sy * dx, -sy * dy])
        values.extend([sx, sy])
    return _solve(matrix, values)


def warp_quad(image, quad: Sequence[Tuple[float, float]], canvas_size: Tuple[int, int]):
    """Own quad transform: warp a page onto any four-corner shape on a transparent canvas."""
    Image, _, _, _, _ = _pil()
    w, h = image.size
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    coeffs = find_coeffs(quad, src)
    return image.convert("RGBA").transform(
        canvas_size, Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )


def _load_rgb(path: Path):
    Image, _, _, _, ImageOps = _pil()
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def _page_files(pack: Path) -> List[Path]:
    names = []
    for path in sorted(pack.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        if path.name.startswith(("cover.", "preview-", "pinterest.", "mockup-", "gumroad-")):
            continue
        if re.match(r"^(?:page|sheet|sample|inside)[-_0-9]*\.", path.name, re.IGNORECASE):
            names.append(path)
    return names[:MAX_SOURCE_PAGES]


def product_name(pack_dir) -> str:
    """The real product name from README, listing JSON, builder spec, or the folder name."""
    pack = Path(pack_dir)
    readme = pack / "README.md"
    if readme.is_file():
        try:
            for line in readme.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("# "):
                    name = safe_text(line[2:])
                    if name:
                        return name
        except OSError:
            pass
    for filename in ("listing.json", "product.json", "spec.json"):
        path = pack / filename
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                name = (data.get("name") or data.get("title")
                        or (data.get("listing") or {}).get("title"))
                if name and safe_text(name):
                    return safe_text(name)
        except Exception:
            pass
    try:
        import product_builder
        spec = product_builder.spec_from_folder(str(pack))
        name = safe_text(spec.get("name"))
        if name:
            return name
    except Exception:
        pass
    cleaned = re.sub(r"^\d+[-_]", "", pack.name).replace("_", " ").replace("-", " ")
    return safe_text(cleaned).title() or "Digital template"


def fallback_page(title: str, index: int = 0, theme: Optional[Dict[str, Any]] = None,
                  size: Tuple[int, int] = PAGE_SIZE):
    """A clean procedural page used only when no real page can be read."""
    Image, ImageDraw, _, _, _ = _pil()
    theme = theme or DEFAULT_THEME
    im = Image.new("RGB", size, theme["paper"])
    d = ImageDraw.Draw(im)
    pad = int(size[0] * 0.07)
    band = int(size[1] * 0.12)
    d.rectangle([0, 0, size[0], band], fill=theme["accent"])
    lines, f = fit_text(title, "bold", size[0] - 2 * pad, 1, int(band * 0.42), 12)
    draw_text(d, (pad, int(band * 0.28)), lines[0], f, contrast_text(theme["accent"]))
    y = band + int(size[1] * 0.045)
    for _ in range(5):
        d.rounded_rectangle([pad, y, pad + int(size[0] * 0.42), y + int(size[1] * 0.021)],
                            radius=4, fill=mix(theme["ink"], theme["paper"], 0.82))
        y += int(size[1] * 0.045)
    y += int(size[1] * 0.02)
    cols = 2 + (index % 2)
    gap = int(size[0] * 0.035)
    width = (size[0] - 2 * pad - (cols - 1) * gap) // cols
    for row in range(3):
        for col in range(cols):
            x = pad + col * (width + gap)
            top = y + row * int(size[1] * 0.15)
            d.rounded_rectangle([x, top, x + width, top + int(size[1] * 0.115)], radius=8,
                                outline=mix(theme["accent"], theme["paper"], 0.45), width=3)
            d.line([x + 12, top + 30, x + width - 12, top + 30],
                   fill=mix(theme["accent"], theme["paper"], 0.55), width=3)
    d.rectangle([0, size[1] - int(size[1] * 0.035), size[0], size[1]], fill=theme["accent"])
    return im


def source_pages(pack_dir, limit: int = MAX_SOURCE_PAGES) -> List[Any]:
    """Real page images from explicit page files, else a PDF, else procedural fallback pages."""
    pack = Path(pack_dir)
    pages = []
    for path in _page_files(pack)[:limit]:
        try:
            pages.append(fit_page(_load_rgb(path)))
        except Exception:
            continue
    if pages:
        return pages
    pdf_path = None
    try:
        import truth_check
        facts = truth_check.image_facts(str(pack))
        pdf_path = facts.get("pdf_path")
        total = int(facts.get("pages") or 0)
    except Exception:
        total = 0
    if pdf_path and total:
        try:
            import mockups
            if mockups.available():
                for number in range(1, min(limit, total) + 1):
                    pages.append(fit_page(mockups.render_pdf_page(pdf_path, number, PAGE_SIZE[1])))
        except Exception:
            pages = []
    if pages:
        return pages
    name = product_name(pack)
    theme = theme_from_cover(_cover_path(pack))
    return [fallback_page(name, index, theme) for index in range(min(3, max(1, limit)))]


def _cover_path(pack: Path) -> Optional[Path]:
    for name in ("cover.png", "cover.jpg", "cover.jpeg"):
        path = pack / name
        if path.is_file():
            return path
    return None


def _canvas(size: Tuple[int, int], color, seed: int = 0):
    Image, _, _, _, _ = _pil()
    im = Image.new("RGBA", size, tuple(color) + (255,))
    return apply_texture(im.convert("RGB"), seed, 4, 0.10).convert("RGBA")


def _ribbon(canvas, title: str, theme: Dict[str, Any], top: bool = True):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = canvas.size
    pad = int(w * 0.055)
    max_text_w = w - 2 * pad - int(w * 0.06)
    lines, f = fit_text(title, "bold", max_text_w, 2, int(w * 0.048), int(w * 0.022))
    height = len(lines) * line_height(f) + int(h * 0.032)
    y = int(h * 0.035) if top else h - height - int(h * 0.035)
    box = (pad, y, w - pad, y + height)
    band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    bd.rounded_rectangle(box, radius=max(8, height // 5), fill=tuple(theme["accent"]) + (240,))
    canvas.alpha_composite(band)
    d = ImageDraw.Draw(canvas)
    yy = y + int(h * 0.016)
    for line in lines:
        draw_text(d, (pad + int(w * 0.025), yy), line, f, contrast_text(theme["accent"]))
        yy += line_height(f)


def _fit_layer(page, width: int, height: int):
    Image, _, _, _, ImageOps = _pil()
    im = ImageOps.contain(page.convert("RGB"), (width, height), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    layer.paste(im.convert("RGBA"), ((width - im.width) // 2, (height - im.height) // 2))
    return layer


def _paper_layer(page, width: int, height: int, theme: Dict[str, Any]):
    Image, ImageDraw, _, _, _ = _pil()
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([0, 0, width - 1, height - 1], radius=max(6, width // 40),
                        fill=theme["paper"] + (255,), outline=(215, 212, 204, 255), width=2)
    fitted = _fit_layer(page, width - max(12, width // 24), height - max(12, width // 24))
    layer.alpha_composite(fitted, (max(6, width // 48), max(6, width // 48)))
    return layer


def _draw_desk(canvas, theme):
    _, ImageDraw, _, _, _ = _pil()
    d = ImageDraw.Draw(canvas)
    w, h = canvas.size
    d.rectangle([0, 0, w, h], fill=tuple(theme["bg"]) + (255,))
    d.rectangle([0, int(h * 0.55), w, h], fill=tuple(theme["desk"]) + (255,))
    for i in range(9):
        y = int(h * 0.59 + i * h * 0.045)
        d.line([0, y, w, y + int(h * 0.006)], fill=mix(theme["desk"], (255, 255, 255), 0.15), width=2)


def _draw_plant(d, x: int, y: int, scale: float, theme):
    pot_w, pot_h = int(120 * scale), int(95 * scale)
    d.rounded_rectangle([x, y, x + pot_w, y + pot_h], radius=int(18 * scale),
                        fill=mix((185, 118, 81), theme["ink"], 0.12))
    for i in range(6):
        cx = x + pot_w // 2 + int(math.cos(i * math.pi / 3) * 70 * scale)
        cy = y - int(55 * scale) + int(math.sin(i * math.pi / 3) * 65 * scale)
        d.ellipse([cx - int(42 * scale), cy - int(22 * scale), cx + int(42 * scale), cy + int(22 * scale)],
                  fill=mix(theme["accent"], (45, 105, 55), 0.55))


def _draw_mug(d, x: int, y: int, scale: float, theme):
    d.rounded_rectangle([x, y, x + int(150 * scale), y + int(140 * scale)],
                        radius=int(26 * scale), fill=(250, 248, 242))
    d.ellipse([x + int(20 * scale), y + int(14 * scale), x + int(130 * scale), y + int(64 * scale)],
              fill=(93, 62, 44))
    d.arc([x + int(140 * scale), y + int(36 * scale), x + int(205 * scale), y + int(112 * scale)],
          260, 100, fill=(250, 248, 242), width=max(8, int(16 * scale)))


def _draw_pen(d, x: int, y: int, scale: float, theme):
    d.rounded_rectangle([x, y, x + int(300 * scale), y + int(34 * scale)], radius=int(17 * scale),
                        fill=mix(theme["ink"], (255, 255, 255), 0.12))
    d.polygon([(x + int(300 * scale), y), (x + int(356 * scale), y + int(17 * scale)),
               (x + int(300 * scale), y + int(34 * scale))], fill=(210, 196, 168))
    d.rectangle([x + int(22 * scale), y + int(8 * scale), x + int(38 * scale), y + int(26 * scale)],
                fill=theme["accent"])


def scene_tablet_desk(pages, theme, title: str, size=LISTING_SIZE, show_title: bool = True):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = _canvas(size, theme["bg"], 1)
    _draw_desk(canvas, theme)
    body_w, body_h = int(w * 0.66), int(h * 0.64)
    x, y = (w - body_w) // 2, int(h * 0.16)
    body = rounded_layer((body_w, body_h), int(w * 0.028), (38, 42, 45, 255), 4, (90, 94, 96, 255))
    add_shadow(canvas, body, (x, y), int(w * 0.018), 0.42, (int(w * 0.012), int(h * 0.018)))
    paste_rgba(canvas, body, (x, y))
    inset = int(w * 0.035)
    screen = _paper_layer(pages[0], body_w - 2 * inset, body_h - 2 * inset, theme)
    quad = [
        (x + inset + int(w * 0.004), y + inset + int(h * 0.003)),
        (x + body_w - inset - int(w * 0.002), y + inset),
        (x + body_w - inset, y + body_h - inset),
        (x + inset, y + body_h - inset - int(h * 0.003)),
    ]
    canvas.alpha_composite(warp_quad(screen, quad, size))
    d = ImageDraw.Draw(canvas)
    d.ellipse([w // 2 - 8, y + body_h - inset // 2 - 8, w // 2 + 8, y + body_h - inset // 2 + 8],
              fill=(210, 214, 216))
    _draw_mug(d, int(w * 0.075), int(h * 0.72), w / 2000, theme)
    _draw_plant(d, int(w * 0.80), int(h * 0.78), w / 2100, theme)
    if show_title:
        _ribbon(canvas, title, theme)
    return canvas.convert("RGB")


def scene_clipboard(pages, theme, title: str, size=LISTING_SIZE, show_title: bool = True):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = _canvas(size, theme["bg"], 2)
    _draw_desk(canvas, theme)
    board_w, board_h = int(w * 0.57), int(h * 0.72)
    x, y = (w - board_w) // 2, int(h * 0.12)
    board = rounded_layer((board_w, board_h), int(w * 0.012), (167, 118, 73, 255), 4, (108, 74, 45, 255))
    add_shadow(canvas, board, (x, y), int(w * 0.016), 0.36, (int(w * 0.01), int(h * 0.015)))
    paste_rgba(canvas, board, (x, y))
    pad = int(w * 0.035)
    paper = _paper_layer(pages[0], board_w - 2 * pad, board_h - 2 * pad, theme)
    paste_rgba(canvas, paper, (x + pad, y + pad))
    d = ImageDraw.Draw(canvas)
    clip_w, clip_h = int(board_w * 0.34), int(board_h * 0.07)
    cx, cy = x + (board_w - clip_w) // 2, y - clip_h // 5
    d.rounded_rectangle([cx, cy, cx + clip_w, cy + clip_h], radius=clip_h // 2,
                        fill=(77, 82, 84), outline=(160, 166, 168), width=3)
    d.ellipse([cx + clip_w // 2 - 11, cy + clip_h // 2 - 11,
               cx + clip_w // 2 + 11, cy + clip_h // 2 + 11], fill=(210, 214, 215))
    _draw_pen(d, int(w * 0.73), int(h * 0.82), w / 2600, theme)
    if show_title:
        _ribbon(canvas, title, theme, top=False)
    return canvas.convert("RGB")


def _rotated_paper(page, theme, width: int, height: int, angle: float):
    Image, _, _, _, _ = _pil()
    layer = _paper_layer(page, width, height, theme).convert("RGBA")
    return layer.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)


def scene_sheets_fan(pages, theme, title: str, size=LISTING_SIZE, show_title: bool = True):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = _canvas(size, theme["bg"], 3)
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, int(h * 0.62), w, h], fill=tuple(theme["desk"]) + (255,))
    pw, ph = int(w * 0.40), int(h * 0.56)
    for index, angle in ((2, 12), (1, -8), (0, 3)):
        layer = _rotated_paper(pages[index % len(pages)], theme, pw, ph, angle)
        x = int(w * 0.50 - layer.width / 2 + (index - 1) * w * 0.075)
        y = int(h * 0.50 - layer.height / 2 + (index - 1) * h * 0.012)
        add_shadow(canvas, layer, (x, y), int(w * 0.012), 0.28, (int(w * 0.006), int(h * 0.012)))
        paste_rgba(canvas, layer, (x, y))
    _draw_plant(ImageDraw.Draw(canvas), int(w * 0.07), int(h * 0.70), w / 2200, theme)
    if show_title:
        _ribbon(canvas, title, theme)
    return canvas.convert("RGB")


def scene_flat_lay(pages, theme, title: str, size=LISTING_SIZE, show_title: bool = True):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = _canvas(size, theme["desk"], 4)
    d = ImageDraw.Draw(canvas)
    for i in range(12):
        x = int((i * 0.19 + 0.03) * w)
        d.line([x, 0, x + int(w * 0.05), h], fill=mix(theme["desk"], (255, 255, 255), 0.12), width=3)
    layer = _rotated_paper(pages[0], theme, int(w * 0.46), int(h * 0.64), -6)
    x, y = int(w * 0.22), int(h * 0.16)
    add_shadow(canvas, layer, (x, y), int(w * 0.015), 0.34, (int(w * 0.01), int(h * 0.014)))
    paste_rgba(canvas, layer, (x, y))
    _draw_pen(d, int(w * 0.68), int(h * 0.70), w / 1900, theme)
    _draw_mug(d, int(w * 0.66), int(h * 0.18), w / 1700, theme)
    _draw_plant(d, int(w * 0.07), int(h * 0.80), w / 2200, theme)
    if show_title:
        _ribbon(canvas, title, theme, top=False)
    return canvas.convert("RGB")


def scene_phone_screen(pages, theme, title: str, size=LISTING_SIZE, show_title: bool = True):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = _canvas(size, theme["bg"], 5)
    _draw_desk(canvas, theme)
    pw, ph = int(w * 0.31), int(h * 0.67)
    x, y = (w - pw) // 2, int(h * 0.14)
    body = rounded_layer((pw, ph), int(pw * 0.13), (29, 32, 34, 255), 5, (102, 108, 110, 255))
    add_shadow(canvas, body, (x, y), int(w * 0.018), 0.44, (int(w * 0.012), int(h * 0.017)))
    paste_rgba(canvas, body, (x, y))
    inset = int(pw * 0.055)
    screen = _paper_layer(pages[0], pw - 2 * inset, ph - int(ph * 0.10), theme)
    paste_rgba(canvas, screen, (x + inset, y + int(ph * 0.045)))
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([x + pw // 2 - int(pw * 0.13), y + int(ph * 0.02),
                         x + pw // 2 + int(pw * 0.13), y + int(ph * 0.038)],
                        radius=int(ph * 0.01), fill=(10, 12, 13))
    d.rounded_rectangle([x + pw // 2 - int(pw * 0.17), y + ph - int(ph * 0.035),
                         x + pw // 2 + int(pw * 0.17), y + ph - int(ph * 0.025)],
                        radius=6, fill=(205, 209, 211))
    _draw_plant(d, int(w * 0.76), int(h * 0.80), w / 2000, theme)
    if show_title:
        _ribbon(canvas, title, theme)
    return canvas.convert("RGB")


def scene_binder(pages, theme, title: str, size=LISTING_SIZE, show_title: bool = True):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = _canvas(size, theme["bg"], 6)
    _draw_desk(canvas, theme)
    bw, bh = int(w * 0.62), int(h * 0.70)
    x, y = (w - bw) // 2, int(h * 0.14)
    cover = rounded_layer((bw, bh), int(w * 0.015), tuple(theme["ink"]) + (255,), 4, (20, 24, 26, 255))
    add_shadow(canvas, cover, (x, y), int(w * 0.018), 0.40, (int(w * 0.012), int(h * 0.018)))
    paste_rgba(canvas, cover, (x, y))
    d = ImageDraw.Draw(canvas)
    paper = _paper_layer(pages[0], int(bw * 0.72), int(bh * 0.90), theme)
    paste_rgba(canvas, paper, (x + int(bw * 0.20), y + int(bh * 0.05)))
    ring_x = x + int(bw * 0.09)
    for i in range(5):
        cy = y + int(bh * 0.18 + i * bh * 0.16)
        d.ellipse([ring_x - 17, cy - 17, ring_x + 17, cy + 17], outline=(196, 201, 202), width=6)
    for i, colour in enumerate((theme["accent"], mix(theme["accent"], (240, 190, 90), 0.5),
                                mix(theme["accent"], (210, 105, 120), 0.5))):
        ty = y + int(bh * (0.24 + i * 0.14))
        d.rounded_rectangle([x + bw - 8, ty, x + bw + int(w * 0.045), ty + int(h * 0.055)],
                            radius=8, fill=colour)
    _draw_pen(d, int(w * 0.74), int(h * 0.82), w / 2500, theme)
    if show_title:
        _ribbon(canvas, title, theme, top=False)
    return canvas.convert("RGB")


def scene_wall_frame(pages, theme, title: str, size=LISTING_SIZE, show_title: bool = True):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = _canvas(size, mix(theme["bg"], (250, 248, 243), 0.45), 7)
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, int(h * 0.84), w, h], fill=tuple(theme["desk"]) + (255,))
    fw, fh = int(w * 0.50), int(h * 0.62)
    x, y = (w - fw) // 2, int(h * 0.12)
    frame = rounded_layer((fw, fh), int(w * 0.006), (92, 70, 49, 255), 10, (55, 42, 30, 255))
    add_shadow(canvas, frame, (x, y), int(w * 0.015), 0.30, (int(w * 0.01), int(h * 0.015)))
    paste_rgba(canvas, frame, (x, y))
    pad = int(w * 0.035)
    mat = rounded_layer((fw - 2 * pad, fh - 2 * pad), 2, (250, 249, 245, 255))
    paste_rgba(canvas, mat, (x + pad, y + pad))
    inner = int(pad * 0.55)
    paper = _paper_layer(pages[0], fw - 2 * pad - 2 * inner, fh - 2 * pad - 2 * inner, theme)
    paste_rgba(canvas, paper, (x + pad + inner, y + pad + inner))
    _draw_plant(ImageDraw.Draw(canvas), int(w * 0.78), int(h * 0.76), w / 2100, theme)
    if show_title:
        _ribbon(canvas, title, theme, top=False)
    return canvas.convert("RGB")


def scene_page_stack(pages, theme, title: str, size=LISTING_SIZE, show_title: bool = True):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = _canvas(size, theme["bg"], 8)
    _draw_desk(canvas, theme)
    pw, ph = int(w * 0.42), int(h * 0.58)
    for index, angle in ((2, -7), (1, 4), (0, -2)):
        layer = _rotated_paper(pages[index % len(pages)], theme, pw, ph, angle)
        x = int(w * 0.52 - layer.width / 2 + (index - 1) * w * 0.025)
        y = int(h * 0.47 - layer.height / 2 + (index - 1) * h * 0.018)
        add_shadow(canvas, layer, (x, y), int(w * 0.012), 0.32, (int(w * 0.007), int(h * 0.012)))
        paste_rgba(canvas, layer, (x, y))
    d = ImageDraw.Draw(canvas)
    cx, cy = int(w * 0.58), int(h * 0.27)
    d.rounded_rectangle([cx - 12, cy - 80, cx + 12, cy + 30], radius=10, fill=(184, 190, 192))
    d.arc([cx - 45, cy - 95, cx + 32, cy - 15], 200, 340, fill=(184, 190, 192), width=9)
    d.arc([cx - 45, cy - 40, cx + 32, cy + 45], 20, 160, fill=(184, 190, 192), width=9)
    _draw_pen(d, int(w * 0.10), int(h * 0.82), w / 2300, theme)
    _draw_plant(d, int(w * 0.79), int(h * 0.79), w / 2200, theme)
    if show_title:
        _ribbon(canvas, title, theme)
    return canvas.convert("RGB")


SCENES = {
    "tablet_desk": scene_tablet_desk,
    "clipboard": scene_clipboard,
    "sheets_fan": scene_sheets_fan,
    "flat_lay": scene_flat_lay,
    "phone_screen": scene_phone_screen,
    "binder": scene_binder,
    "wall_frame": scene_wall_frame,
    "page_stack": scene_page_stack,
}


def _scene_image(scene: str, pages, theme, title: str, size=LISTING_SIZE, show_title: bool = True):
    try:
        renderer = SCENES[scene]
    except KeyError:
        raise ValueError(f"unknown scene: {scene}") from None
    return renderer(pages, theme, title, size, show_title)


def pin_mockup(pages, theme, title: str, size=PIN_SIZE, show_title: bool = True):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = _canvas(size, theme["bg"], 11)
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, int(h * 0.76), w, h], fill=tuple(theme["accent"]) + (255,))
    y = int(h * 0.055)
    if show_title:
        lines, f = fit_text(title, "bold", w - int(w * 0.14), 2, int(w * 0.078), int(w * 0.036))
        for line in lines:
            draw_text(d, ((w - text_width(line, f)) // 2, y), line, f, theme["ink"])
            y += line_height(f)
    y += int(h * 0.02)
    pw, ph = int(w * 0.46), int(h * 0.47)
    for index, angle in ((1, 8), (0, -4)):
        layer = _rotated_paper(pages[index % len(pages)], theme, pw, ph, angle)
        x = (w - layer.width) // 2 + (index - 1) * int(w * 0.10)
        yy = int(h * 0.30 + (index - 1) * h * 0.02)
        add_shadow(canvas, layer, (x, yy), int(w * 0.012), 0.30, (int(w * 0.008), int(h * 0.012)))
        paste_rgba(canvas, layer, (x, yy))
    d = ImageDraw.Draw(canvas)
    label, f = fit_text("Instant download", "semibold", w - int(w * 0.16), 1, int(w * 0.048), 20)
    draw_text(d, ((w - text_width(label[0], f)) // 2, h - int(h * 0.13)), label[0], f,
              contrast_text(theme["accent"]))
    return canvas.convert("RGB")


def square_mockup(pages, theme, title: str, size=SQUARE_SIZE, show_title: bool = True):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = _canvas(size, theme["bg"], 12)
    d = ImageDraw.Draw(canvas)
    d.ellipse([int(w * 0.42), -int(h * 0.12), int(w * 1.10), int(h * 0.58)],
              fill=tuple(theme["accent"]) + (255,))
    pw, ph = int(w * 0.44), int(h * 0.60)
    layer = _rotated_paper(pages[0], theme, pw, ph, -5)
    x, y = int(w * 0.56 - layer.width / 2), int(h * 0.30)
    add_shadow(canvas, layer, (x, y), int(w * 0.018), 0.34, (int(w * 0.01), int(h * 0.015)))
    paste_rgba(canvas, layer, (x, y))
    d = ImageDraw.Draw(canvas)
    if show_title:
        lines, f = fit_text(title, "bold", int(w * 0.45), 3, int(w * 0.068), int(w * 0.028))
        yy = int(h * 0.18)
        for line in lines:
            draw_text(d, (int(w * 0.07), yy), line, f, theme["ink"])
            yy += line_height(f)
    return canvas.convert("RGB")


def _alt_text(scene: str, title: str, index: int) -> str:
    name = safe_text(title) or "Digital template"
    if scene == "pin":
        return f"Pin image for {name}. Real page previews on a clean colour background."
    if scene == "square":
        return f"Square listing image for {name}. A real page preview with the product title."
    return (f"{SCENE_TITLES.get(scene, 'Mockup')} showing a real page from {name}. "
            f"Procedural product mockup, image {index}.")


def build_all(pack_dir, out_dir=None, include_png: bool = True,
              scenes: Optional[Iterable[str]] = None, show_title: bool = True) -> List[str]:
    """Render the 8 listing scenes, pin, square, JPEG/PNG files and alt-text JSON."""
    Image, _, _, _, _ = _pil()
    pack = Path(pack_dir).resolve()
    out = Path(out_dir).resolve() if out_dir is not None else pack / "mockups-v2"
    out.mkdir(parents=True, exist_ok=True)
    chosen = tuple(scenes or SCENE_NAMES)
    unknown = [scene for scene in chosen if scene not in SCENES]
    if unknown:
        raise ValueError(f"unknown scene: {unknown[0]}")
    pages = source_pages(pack) or [fallback_page(product_name(pack))]
    theme = theme_from_cover(_cover_path(pack))
    title = product_name(pack)
    files: List[str] = []
    alt: Dict[str, str] = {}
    for index, scene in enumerate(chosen, 1):
        image = _scene_image(scene, pages, theme, title, LISTING_SIZE, show_title)
        stem = f"mockup-{index:02d}-{scene}-2000x2000"
        jpg = out / f"{stem}.jpg"
        image.save(jpg, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=False, progressive=False)
        files.append(str(jpg))
        alt[jpg.name] = _alt_text(scene, title, index)
        if include_png:
            png = out / f"{stem}.png"
            image.save(png, "PNG", compress_level=6)
            files.append(str(png))
            alt[png.name] = alt[jpg.name]
    pin = pin_mockup(pages, theme, title, PIN_SIZE, show_title)
    for suffix, fmt, opts in (("jpg", "JPEG", {"quality": JPEG_QUALITY, "subsampling": 0,
                                               "optimize": False, "progressive": False}),
                              ("png", "PNG", {"compress_level": 6})):
        if suffix == "png" and not include_png:
            continue
        path = out / f"mockup-pin-1000x1500.{suffix}"
        pin.save(path, fmt, **opts)
        files.append(str(path))
        alt[path.name] = _alt_text("pin", title, 0)
    square = square_mockup(pages, theme, title, SQUARE_SIZE, show_title)
    for suffix, fmt, opts in (("jpg", "JPEG", {"quality": JPEG_QUALITY, "subsampling": 0,
                                               "optimize": False, "progressive": False}),
                              ("png", "PNG", {"compress_level": 6})):
        if suffix == "png" and not include_png:
            continue
        path = out / f"mockup-square-1080x1080.{suffix}"
        square.save(path, fmt, **opts)
        files.append(str(path))
        alt[path.name] = _alt_text("square", title, 0)
    alt_path = out / "mockups-alt.json"
    alt_path.write_text(json.dumps(alt, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    files.append(str(alt_path))
    return files


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build procedural mockups from a product pack.")
    parser.add_argument("pack_dir", help="product folder with page images, cover or PDF")
    parser.add_argument("--out", default=None, help="output folder (default: <pack>/mockups-v2)")
    parser.add_argument("--no-png", action="store_true", help="write JPEG only")
    parser.add_argument("--no-title", action="store_true", help="skip the title ribbon")
    parser.add_argument("--scenes", nargs="*", choices=SCENE_NAMES, default=None,
                        help="subset of listing scenes")
    args = parser.parse_args(argv)
    files = build_all(args.pack_dir, args.out, not args.no_png, args.scenes, not args.no_title)
    print(f"Mockups: {len(files)} file(s)")
    for path in files:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
