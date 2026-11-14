"""gumroad_images.py - the image set Gumroad needs for one product pack.

The builder reads the pack's real pages and cover and writes, in upload order:
  01-thumbnail-600x600.jpg
  02-cover-1280x720.jpg
  03-gallery-1-1600x1200.jpg ... up to 07-gallery-5-1600x1200.jpg
plus gumroad-alt-text.json.  Gallery scenes use mockups_v2 when it is importable;
when it is not, clean procedural layouts are drawn here.  No prices, review claims,
other-store brand names or invented facts are drawn.  Output is deterministic.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BASE_DIR = Path(__file__).resolve().parent
FONT_DIR = BASE_DIR / "fonts"
THUMBNAIL_SIZE = (600, 600)
COVER_SIZE = (1280, 720)
GALLERY_SIZE = (1600, 1200)
JPEG_QUALITY = 88
GALLERY_MIN = 3
GALLERY_MAX = 5
GALLERY_SCENES = ("tablet_desk", "flat_lay", "binder", "page_stack")
ALT_NAME = "gumroad-alt-text.json"
DEFAULT_THEME = {
    "bg": (235, 242, 240),
    "accent": (47, 125, 120),
    "ink": (24, 48, 46),
    "body": (69, 91, 88),
    "paper": (252, 250, 244),
}
FONT_FILES = {
    "regular": "Inter-Regular.ttf",
    "semibold": "Inter-SemiBold.ttf",
    "bold": "Inter-Bold.ttf",
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

try:
    import mockups_v2 as MV
except Exception:
    MV = None


def _pil():
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
    return Image, ImageDraw, ImageFilter, ImageFont, ImageOps


def available() -> bool:
    try:
        _pil()
        return True
    except Exception:
        return False


def plain_text(text: Any) -> str:
    out = "".join(_PLAIN.get(ch, ch) for ch in str(text or ""))
    return unicodedata.normalize("NFKD", out).encode("ascii", "ignore").decode("ascii")


def safe_text(text: Any) -> str:
    out = plain_text(text)
    out = re.sub(r"https?://\S+", " ", out, flags=re.IGNORECASE)
    for pattern in _BRANDS:
        out = re.sub(pattern, " ", out, flags=re.IGNORECASE)
    out = _PRICE_RE.sub(" ", out)
    out = _REVIEW_RE.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip(" -|,.;:")


def font(weight: str, size: int):
    _, _, _, ImageFont, _ = _pil()
    key = (weight if weight in FONT_FILES else "regular", max(1, int(size)))
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(str(FONT_DIR / FONT_FILES[key[0]]), key[1])
        except OSError:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def text_width(text: str, f) -> float:
    return float(f.getlength(text)) if hasattr(f, "getlength") else float(f.getsize(text)[0])


def line_height(f, factor: float = 1.18) -> int:
    return max(1, int(getattr(f, "size", 12) * factor))


def _split_word(word: str, f, max_width: int) -> List[str]:
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
    words = safe_text(text).split()
    if not words:
        return [""]
    lines, current = [], ""
    for word in words:
        if text_width(word, f) > max_width:
            if current:
                lines.append(current)
                current = ""
            pieces = _split_word(word, f, max_width)
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
             max_size: int = 64, min_size: int = 16):
    size = max(1, int(max_size))
    while True:
        f = font(weight, size)
        lines = wrap_text(text, f, max_width)
        if all(text_width(line, f) <= max_width for line in lines) and len(lines) <= max_lines:
            return lines, f
        if size <= min_size:
            return lines, f
        size = max(min_size, size - 2)


def mix(a: Sequence[int], b: Sequence[int], t: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, float(t)))
    return tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in range(3))


def luminance(color: Sequence[int]) -> float:
    r, g, b = [float(c) / 255.0 for c in color[:3]]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ink_on(color: Sequence[int]) -> Tuple[int, int, int]:
    return (255, 255, 255) if luminance(color) < 0.55 else (23, 28, 30)


def _product_meta(pack: Path) -> Dict[str, Any]:
    name = ""
    description = ""
    for filename in ("listing.json", "product.json", "spec.json"):
        path = pack / filename
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                listing = data.get("listing") if isinstance(data.get("listing"), dict) else {}
                name = data.get("name") or data.get("title") or listing.get("title") or name
                description = data.get("description") or listing.get("description") or description
        except Exception:
            pass
    readme = pack / "README.md"
    if readme.is_file():
        try:
            text = readme.read_text(encoding="utf-8", errors="ignore")
            if not name:
                for line in text.splitlines():
                    if line.startswith("# "):
                        name = line[2:].strip()
                        break
            if not description:
                paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
                description = next((p for p in paragraphs if not p.startswith("#")), "")
        except OSError:
            pass
    if not name and MV is not None:
        try:
            name = MV.product_name(pack)
        except Exception:
            name = ""
    if not name:
        name = re.sub(r"^\d+[-_]", "", pack.name).replace("_", " ").replace("-", " ").title()
    if not description:
        description = "Real pages from this digital product, made to print or use at home."
    return {"name": safe_text(name) or "Digital template",
            "description": safe_text(description) or "Real pages from this digital product."}


def _cover_path(pack: Path) -> Optional[Path]:
    for name in ("cover.png", "cover.jpg", "cover.jpeg"):
        path = pack / name
        if path.is_file():
            return path
    return None


def _page_files(pack: Path) -> List[Path]:
    out = []
    for path in sorted(pack.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        if path.name.startswith(("cover.", "preview-", "pinterest.", "mockup-", "gumroad-")):
            continue
        if re.match(r"^(?:page|sheet|sample|inside)[-_0-9]*\.", path.name, re.IGNORECASE):
            out.append(path)
    return out[:5]


def _load_rgb(path: Path):
    Image, _, _, _, ImageOps = _pil()
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def _fit_page(image, size=(1200, 1600)):
    Image, _, _, _, ImageOps = _pil()
    im = image.convert("RGB")
    fitted = ImageOps.contain(im, size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, (252, 250, 244))
    canvas.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return canvas


def fallback_page(title: str, index: int = 0, theme: Optional[Dict[str, Any]] = None,
                  size=(1200, 1600)):
    Image, ImageDraw, _, _, _ = _pil()
    theme = theme or DEFAULT_THEME
    im = Image.new("RGB", size, theme["paper"])
    d = ImageDraw.Draw(im)
    pad, band = int(size[0] * 0.07), int(size[1] * 0.12)
    d.rectangle([0, 0, size[0], band], fill=theme["accent"])
    lines, f = fit_text(title, "bold", size[0] - 2 * pad, 1, int(band * 0.4), 12)
    d.text((pad, int(band * 0.28)), lines[0], font=f, fill=ink_on(theme["accent"]))
    y = band + int(size[1] * 0.05)
    for _ in range(5):
        d.rounded_rectangle([pad, y, pad + int(size[0] * 0.44), y + int(size[1] * 0.02)],
                            radius=4, fill=mix(theme["ink"], theme["paper"], 0.82))
        y += int(size[1] * 0.05)
    y += int(size[1] * 0.02)
    cols = 2 + index % 2
    gap = int(size[0] * 0.03)
    width = (size[0] - 2 * pad - (cols - 1) * gap) // cols
    for row in range(3):
        for col in range(cols):
            x = pad + col * (width + gap)
            top = y + row * int(size[1] * 0.15)
            d.rounded_rectangle([x, top, x + width, top + int(size[1] * 0.115)], radius=8,
                                outline=mix(theme["accent"], theme["paper"], 0.45), width=3)
    return im


def _own_pages(pack: Path, theme: Dict[str, Any], title: str) -> List[Any]:
    pages = []
    for path in _page_files(pack):
        try:
            pages.append(_fit_page(_load_rgb(path)))
        except Exception:
            continue
    if pages:
        return pages
    pdf_path, total = None, 0
    try:
        import truth_check
        facts = truth_check.image_facts(str(pack))
        pdf_path, total = facts.get("pdf_path"), int(facts.get("pages") or 0)
    except Exception:
        pass
    if pdf_path and total:
        try:
            import mockups
            if mockups.available():
                for number in range(1, min(3, total) + 1):
                    pages.append(_fit_page(mockups.render_pdf_page(pdf_path, number, 1600)))
        except Exception:
            pages = []
    if pages:
        return pages
    return [fallback_page(title, index, theme) for index in range(3)]


def _theme(pack: Path) -> Dict[str, Any]:
    if MV is not None:
        try:
            return MV.theme_from_cover(_cover_path(pack))
        except Exception:
            pass
    return dict(DEFAULT_THEME)


def _pages(pack: Path, theme: Dict[str, Any], title: str) -> List[Any]:
    if MV is not None:
        try:
            pages = MV.source_pages(pack)
            if pages:
                return pages
        except Exception:
            pass
    return _own_pages(pack, theme, title)


def _rounded(size, radius: int, color):
    Image, ImageDraw, _, _, _ = _pil()
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=color)
    return layer


def _fit_layer(page, width: int, height: int):
    Image, _, _, _, ImageOps = _pil()
    im = ImageOps.contain(page.convert("RGB"), (width, height), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    layer.paste(im.convert("RGBA"), ((width - im.width) // 2, (height - im.height) // 2))
    return layer


def _paper(page, width: int, height: int, theme: Dict[str, Any]):
    Image, ImageDraw, _, _, _ = _pil()
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle([0, 0, width - 1, height - 1],
                                            radius=max(6, width // 40), fill=theme["paper"] + (255,))
    fitted = _fit_layer(page, width - max(12, width // 25), height - max(12, width // 25))
    layer.alpha_composite(fitted, (max(6, width // 50), max(6, width // 50)))
    return layer


def _shadow(canvas, layer, position, blur: int, opacity: float, offset=(10, 14)):
    Image, _, ImageFilter, _, _ = _pil()
    alpha = layer.convert("RGBA").split()[3].point(lambda a: int(a * opacity))
    shadow = Image.new("RGBA", layer.size, (20, 26, 28, 0))
    shadow.putalpha(alpha)
    pad = max(4, int(blur) * 2)
    big = Image.new("RGBA", (layer.width + 2 * pad, layer.height + 2 * pad), (0, 0, 0, 0))
    big.alpha_composite(shadow, (pad, pad))
    big = big.filter(ImageFilter.GaussianBlur(blur))
    x, y = position[0] + offset[0] - pad, position[1] + offset[1] - pad
    left, top = max(0, -x), max(0, -y)
    right, bottom = min(big.width, canvas.width - x), min(big.height, canvas.height - y)
    if right > left and bottom > top:
        canvas.alpha_composite(big.crop((left, top, right, bottom)), (x + left, y + top))


def _paste(canvas, layer, position):
    left, top = max(0, -position[0]), max(0, -position[1])
    right = min(layer.width, canvas.width - position[0])
    bottom = min(layer.height, canvas.height - position[1])
    if right > left and bottom > top:
        canvas.alpha_composite(layer.crop((left, top, right, bottom)),
                               (position[0] + left, position[1] + top))


def _rotated_paper(page, theme, width: int, height: int, angle: float):
    Image, _, _, _, _ = _pil()
    return _paper(page, width, height, theme).rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)


def _tilted(canvas, page, theme, width: int, height: int, angle: float,
            center: Tuple[int, int], blur: int = 14) -> None:
    layer = _rotated_paper(page, theme, width, height, angle)
    x, y = int(center[0] - layer.width / 2), int(center[1] - layer.height / 2)
    _shadow(canvas, layer, (x, y), blur, 0.32)
    _paste(canvas, layer, (x, y))


def thumbnail_image(page, theme: Dict[str, Any], title: str, size=THUMBNAIL_SIZE):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = Image.new("RGBA", size, theme["bg"] + (255,))
    d = ImageDraw.Draw(canvas)
    d.ellipse([int(w * 0.42), int(h * 0.10), int(w * 1.05), int(h * 0.95)],
              fill=theme["accent"] + (255,))
    _tilted(canvas, page, theme, int(w * 0.40), int(h * 0.52), -5, (int(w * 0.66), int(h * 0.56)), 10)
    d = ImageDraw.Draw(canvas)
    lines, f = fit_text(title, "bold", int(w * 0.48), 3, int(w * 0.075), int(w * 0.03))
    y = int(h * 0.24)
    for line in lines:
        d.text((int(w * 0.07), y), line, font=f, fill=theme["ink"])
        y += line_height(f)
    label, lf = fit_text("Digital download", "semibold", int(w * 0.45), 1, int(w * 0.038), 14)
    d.text((int(w * 0.07), y + int(h * 0.03)), label[0], font=lf, fill=theme["accent"])
    return canvas.convert("RGB")


def cover_image(pages, theme: Dict[str, Any], title: str, description: str, size=COVER_SIZE):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = Image.new("RGBA", size, theme["bg"] + (255,))
    d = ImageDraw.Draw(canvas)
    d.ellipse([int(w * 0.67), -int(h * 0.05), int(w * 1.12), int(h * 0.84)],
              fill=theme["accent"] + (255,))
    if len(pages) > 1:
        _tilted(canvas, pages[1], theme, int(w * 0.24), int(h * 0.55), -6,
                (int(w * 0.83), int(h * 0.52)), 10)
    _tilted(canvas, pages[0], theme, int(w * 0.25), int(h * 0.57), 4,
            (int(w * 0.71), int(h * 0.52)), 10)
    d = ImageDraw.Draw(canvas)
    lines, f = fit_text(title, "bold", int(w * 0.44), 3, int(w * 0.058), int(w * 0.025))
    y = int(h * 0.24)
    for line in lines:
        d.text((int(w * 0.07), y), line, font=f, fill=theme["ink"])
        y += line_height(f)
    y += int(h * 0.04)
    desc, df = fit_text(description, "regular", int(w * 0.42), 3, int(w * 0.024), int(w * 0.016))
    for line in desc:
        d.text((int(w * 0.07), y), line, font=df, fill=theme["body"])
        y += line_height(df)
    return canvas.convert("RGB")


def _plain_gallery(page, theme: Dict[str, Any], title: str, index: int, size=GALLERY_SIZE):
    Image, ImageDraw, _, _, _ = _pil()
    w, h = size
    canvas = Image.new("RGBA", size, theme["bg"] + (255,))
    d = ImageDraw.Draw(canvas)
    if index % 2:
        d.rectangle([0, int(h * 0.72), w, h], fill=theme["accent"] + (255,))
        d.ellipse([int(w * 0.62), int(h * 0.06), int(w * 0.98), int(h * 0.46)],
                  fill=mix(theme["accent"], (255, 255, 255), 0.32) + (255,))
    else:
        d.rectangle([0, 0, int(w * 0.36), h], fill=theme["accent"] + (255,))
    _tilted(canvas, page, theme, int(w * 0.34), int(h * 0.63), -4 if index % 2 else 4,
            (int(w * 0.64), int(h * 0.46)), 20)
    d = ImageDraw.Draw(canvas)
    lines, f = fit_text(title, "bold", int(w * 0.29), 3, int(w * 0.046), int(w * 0.02))
    y = int(h * 0.30)
    colour = ink_on(theme["accent"]) if index % 2 == 0 else theme["ink"]
    for line in lines:
        d.text((int(w * 0.06), y), line, font=f, fill=colour)
        y += line_height(f)
    label, lf = fit_text("Real page preview", "semibold", int(w * 0.28), 1, int(w * 0.026), 14)
    d.text((int(w * 0.06), y + int(h * 0.03)), label[0], font=lf,
           fill=colour if index % 2 == 0 else theme["accent"])
    return canvas.convert("RGB")


def gallery_image(pages, theme: Dict[str, Any], title: str, index: int,
                  size=GALLERY_SIZE):
    if MV is not None:
        scene = GALLERY_SCENES[index % len(GALLERY_SCENES)]
        try:
            return MV._scene_image(scene, pages, theme, title, size, True)
        except Exception:
            pass
    return _plain_gallery(pages[index % len(pages)], theme, title, index, size)


def _gallery_count(pages_count: int) -> int:
    return max(GALLERY_MIN, min(GALLERY_MAX, 4 if pages_count > 0 else GALLERY_MIN))


def gallery_count(pages_count: int = 1) -> int:
    return _gallery_count(pages_count)


def upload_names(gallery: int) -> List[str]:
    count = max(GALLERY_MIN, min(GALLERY_MAX, int(gallery)))
    return ["01-thumbnail-600x600.jpg", "02-cover-1280x720.jpg"] + [
        f"{index + 3:02d}-gallery-{index + 1}-1600x1200.jpg" for index in range(count)
    ]


def alt_text(kind: str, title: str) -> str:
    name = safe_text(title) or "Digital template"
    if kind == "thumbnail":
        return f"Square thumbnail for {name}. A real page preview with the product title."
    if kind == "cover":
        return f"Cover image for {name}. Two real page previews on a clean colour background."
    return f"Gallery image for {name}. A real page preview shown on a clean desk scene."


def _save_jpeg(image, path: Path) -> None:
    image.convert("RGB").save(path, "JPEG", quality=JPEG_QUALITY, subsampling=0,
                              optimize=False, progressive=False)


def build_gumroad_images(pack_dir, out_dir) -> List[str]:
    """Write the Gumroad upload set and return image paths in upload order."""
    if not available():
        raise RuntimeError("Pillow is required to build Gumroad images")
    pack, out = Path(pack_dir).resolve(), Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    meta = _product_meta(pack)
    theme = _theme(pack)
    pages = _pages(pack, theme, meta["name"])
    if not pages:
        pages = [fallback_page(meta["name"], 0, theme)]
    count = _gallery_count(len(pages))
    names = upload_names(count)
    images = [thumbnail_image(pages[0], theme, meta["name"]),
              cover_image(pages, theme, meta["name"], meta["description"])]
    for index in range(count):
        images.append(gallery_image(pages, theme, meta["name"], index))
    paths: List[str] = []
    alt: Dict[str, str] = {}
    for name, image in zip(names, images):
        path = out / name
        _save_jpeg(image, path)
        paths.append(str(path))
        kind = "thumbnail" if name.startswith("01-") else "cover" if name.startswith("02-") else "gallery"
        alt[name] = alt_text(kind, meta["name"])
    (out / ALT_NAME).write_text(
        json.dumps(alt, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    return paths


def for_pack(pack_dir, out_dir=None) -> List[str]:
    """Return Gumroad images in upload order, building the set when it is missing."""
    pack = Path(pack_dir).resolve()
    out = Path(out_dir).resolve() if out_dir is not None else pack / "gumroad-images"
    names = upload_names(gallery_count(1))
    existing = [out / name for name in names]
    if all(path.is_file() for path in existing) and (out / ALT_NAME).is_file():
        return [str(path) for path in existing]
    return build_gumroad_images(pack, out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Gumroad image set for one product pack.")
    parser.add_argument("pack_dir", help="product folder with page images, cover or PDF")
    parser.add_argument("--out", required=True, help="output folder")
    args = parser.parse_args(argv)
    files = build_gumroad_images(args.pack_dir, args.out)
    print(f"Gumroad images: {len(files)} file(s)")
    for path in files:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
