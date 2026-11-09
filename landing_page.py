#!/usr/bin/env python3
"""landing_page.py — One self-contained landing page per pack.

Produces exported_content/marketing/<pack>/landing/index.html (outside the pack):
- Self-contained HTML file (clean inline CSS, NO external fonts/scripts/trackers).
- Semantic headings (h1, h2, h3, main, header, section, footer).
- Meta title (<= 60 characters) and meta description (<= 155 characters).
- Open Graph tags (og:title, og:description, og:type, og:image, og:url).
- JSON-LD Product schema WITHOUT price or currency.
- Images referenced relatively (copied next to index.html, resized with max dimension <= 1200px).
- FAQ section built using faq_builder if present.
- [STORE LINK] button CTA placeholder.
- Mobile friendly (responsive layout, meta viewport).
- Lighthouse-style basics (alt texts, lang="en", accessible contrast, semantic tags).
- HTML-escape all dynamic text.
- No money amounts, no third-party brand logos or brand names.
- Nothing posts, publishes, or emails automatically.
- Atomic writes (staging in temp file then renamed).
- Deterministic outputs and immutable pack folders.
- Owner-facing text in Tenglish house style.
"""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    HAS_PILLOW = False

# Try importing faq_builder
try:
    import faq_builder
    HAS_FAQ_BUILDER = True
except ImportError:
    faq_builder = None  # type: ignore
    HAS_FAQ_BUILDER = False

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_PACKS_DIR = REPO_ROOT / "exported_content" / "products" / "upload"
DEFAULT_MARKETING_DIR = REPO_ROOT / "exported_content" / "marketing"

MAX_IMAGE_DIMENSION = 1200

# Money regex patterns to detect and strip any accidental money references
MONEY_PATTERNS = [
    re.compile(r"\$[\d,]+(?:\.\d{1,2})?\b"),
    re.compile(r"[\d,]+(?:\.\d{1,2})?\s*(?:usd|eur|gbp|inr|rs\.?|rupees?|dollars?|cents?|paise)\b", re.IGNORECASE),
    re.compile(r"(?:usd|eur|gbp|inr|rs\.?|rupees?|dollars?|cents?|paise)\s*[\d,]+(?:\.\d{1,2})?\b", re.IGNORECASE),
    re.compile(r"[₹€£¥]\s*[\d,]+(?:\.\d{1,2})?"),
]

BRAND_PATTERNS = [
    (re.compile(r"\bgoogle\s+sheets?\b", re.I), "spreadsheet"),
    (re.compile(r"\bgoogle\s+docs?\b", re.I), "document"),
    (re.compile(r"\bmicrosoft\s+excel\b", re.I), "spreadsheet"),
    (re.compile(r"\bmicrosoft\s+word\b", re.I), "document"),
    (re.compile(r"\bexcel\b", re.I), "spreadsheet"),
    (re.compile(r"\bnotion\b", re.I), ""),
    (re.compile(r"\bcanva\b", re.I), ""),
    (re.compile(r"\betsy\b", re.I), ""),
    (re.compile(r"\bgumroad\b", re.I), ""),
    (re.compile(r"\bpayhip\b", re.I), ""),
    (re.compile(r"\bamazon\b", re.I), ""),
    (re.compile(r"\bapple\s+numbers\b", re.I), "spreadsheet"),
    (re.compile(r"\bgoodnotes\b", re.I), "notes app"),
    (re.compile(r"\bipad\b", re.I), "tablet"),
]


def sanitize_text(text: str) -> str:
    """Remove money patterns and replace disallowed third-party brands."""
    if not text:
        return ""
    cleaned = text
    for pat in MONEY_PATTERNS:
        cleaned = pat.sub("", cleaned)
    for pat, rep in BRAND_PATTERNS:
        cleaned = pat.sub(rep, cleaned)
    # Collapse multiple spaces but preserve newlines
    lines = []
    for line in cleaned.splitlines():
        lines.append(re.sub(r"[ \t]+", " ", line).strip())
    return "\n".join(lines).strip()


def parse_listing_file(listing_path: Path) -> Dict[str, Any]:
    """Parse listing.txt into title, description, what_you_get, and tags."""
    if not listing_path.is_file():
        return {"title": "", "description": "", "what_you_get": [], "tags": []}

    content = listing_path.read_text(encoding="utf-8", errors="replace")
    title = ""
    description_lines = []
    what_you_get = []
    tags = []

    current_section = None
    for line in content.splitlines():
        line_clean = line.strip()
        m_title = re.match(r"^Title:\s*(.*)$", line, re.IGNORECASE)
        m_desc = re.match(r"^Description:\s*(.*)$", line, re.IGNORECASE)
        m_get = re.match(r"^(?:What you get|What's included|What's inside|Included files):\s*(.*)$", line, re.IGNORECASE)
        m_tags = re.match(r"^Tags:\s*(.*)$", line, re.IGNORECASE)

        if m_title:
            current_section = "title"
            title = m_title.group(1).strip()
        elif m_desc:
            current_section = "description"
            first = m_desc.group(1).strip()
            if first:
                description_lines.append(first)
        elif m_get:
            current_section = "get"
            first = m_get.group(1).strip()
            if first:
                what_you_get.append(first.lstrip("-*• ").strip())
        elif m_tags:
            current_section = "tags"
            tags_raw = m_tags.group(1).strip()
            if tags_raw:
                tags.extend([t.strip() for t in tags_raw.split(",") if t.strip()])
        else:
            if current_section == "description":
                description_lines.append(line_clean)
            elif current_section == "get":
                if line_clean.startswith(("-", "*", "•")):
                    what_you_get.append(line_clean.lstrip("-*• ").strip())
                elif line_clean:
                    what_you_get.append(line_clean)
            elif current_section == "tags":
                if line_clean:
                    tags.extend([t.strip() for t in line_clean.split(",") if t.strip()])

    description = "\n".join(description_lines).strip()
    return {
        "title": sanitize_text(title),
        "description": sanitize_text(description),
        "what_you_get": [sanitize_text(item) for item in what_you_get if item],
        "tags": [sanitize_text(t) for t in tags if t],
    }


def make_meta_title(title: str, pack_name: str) -> str:
    """Generate meta title <= 60 chars."""
    raw = title or pack_name.replace("-", " ").title()
    raw = sanitize_text(raw)
    if len(raw) <= 60:
        return raw
    # Truncate at word boundary
    truncated = raw[:57]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return (truncated + "...").strip()


def make_meta_description(description: str, title: str) -> str:
    """Generate meta description <= 155 chars."""
    candidate = description or f"Download {title}. High quality digital printable files for immediate download."
    # Take first paragraph or sentence
    first_para = candidate.split("\n\n")[0].replace("\n", " ").strip()
    first_para = sanitize_text(first_para)
    if len(first_para) <= 155:
        return first_para
    truncated = first_para[:152]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return (truncated + "...").strip()


def parse_faq_markdown(faq_md: str) -> List[Tuple[str, str]]:
    """Extract (question, answer) pairs from FAQ markdown."""
    qa_list = []
    current_q = None
    current_a_lines = []

    for line in faq_md.splitlines():
        if line.startswith("## "):
            if current_q and current_a_lines:
                qa_list.append((current_q, "\n".join(current_a_lines).strip()))
            current_q = line[3:].strip()
            current_a_lines = []
        elif current_q is not None:
            if not line.startswith("#"):
                current_a_lines.append(line.strip())

    if current_q and current_a_lines:
        qa_list.append((current_q, "\n".join(current_a_lines).strip()))

    return qa_list


def get_pack_faq(pack_dir: Path) -> List[Tuple[str, str]]:
    """Retrieve FAQ items using faq_builder if available, or fallback facts."""
    if HAS_FAQ_BUILDER and faq_builder is not None:
        try:
            faq_md = faq_builder.build_faq(pack_dir)
            qa = parse_faq_markdown(faq_md)
            if qa:
                return [(sanitize_text(q), sanitize_text(a)) for q, a in qa]
        except Exception:
            pass

    # Built-in fallback facts
    return [
        ("Which files are included?", "This download includes ready-to-use digital files as described in the listing."),
        ("How do I access my files?", "After completing your purchase via [STORE LINK], your download is available immediately."),
        ("Can I print these pages?", "Yes. Print at 100% scale (Actual Size) for best results."),
        ("Will anything be physically shipped?", "No. This is an instant digital download only."),
        ("Need help with your download?", "Customer support is ready to assist with any questions about your files."),
    ]


def find_pack_images(pack_dir: Path) -> List[Path]:
    """Find cover and preview images inside the pack directory."""
    images = []
    cover_candidates = ["cover.png", "cover.jpg", "cover.jpeg", "cover.webp"]
    for c in cover_candidates:
        p = pack_dir / c
        if p.is_file():
            images.append(p)
            break

    # Find previews
    previews = sorted([
        p for p in pack_dir.iterdir()
        if p.is_file() and p.name.startswith("preview") and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ])
    for prev in previews:
        if prev not in images:
            images.append(prev)

    return images


def copy_and_resize_image(src_path: Path, dst_path: Path, max_dim: int = MAX_IMAGE_DIMENSION) -> None:
    """Copy image to destination, resizing if dimensions exceed max_dim. Never alters source."""
    if HAS_PILLOW and Image is not None:
        try:
            with Image.open(src_path) as img:
                w, h = img.size
                if w > max_dim or h > max_dim:
                    scale = min(max_dim / w, max_dim / h)
                    new_w = max(1, int(w * scale))
                    new_h = max(1, int(h * scale))
                    # Resample filter
                    resample = getattr(Image, "Resampling", Image).LANCZOS
                    resized = img.resize((new_w, new_h), resample=resample)
                    resized.save(dst_path, optimize=True)
                    return
                else:
                    img.save(dst_path)
                    return
        except Exception:
            # Fallback to copy if pillow processing fails
            shutil.copy2(src_path, dst_path)
    else:
        shutil.copy2(src_path, dst_path)


def generate_landing_html(
    pack_name: str,
    title: str,
    description: str,
    what_you_get: List[str],
    tags: List[str],
    faq_items: List[Tuple[str, str]],
    image_filenames: List[str],
) -> str:
    """Construct self-contained, semantic, responsive HTML string."""
    meta_title = make_meta_title(title, pack_name)
    meta_desc = make_meta_description(description, title)
    escaped_title = html.escape(title or pack_name.replace("-", " ").title())
    escaped_meta_title = html.escape(meta_title)
    escaped_meta_desc = html.escape(meta_desc)

    cover_file = image_filenames[0] if image_filenames else ""

    # JSON-LD Product schema WITHOUT price/offers/brand
    schema_dict: Dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": meta_title,
        "description": meta_desc,
    }
    if cover_file:
        schema_dict["image"] = [cover_file]
    if tags:
        schema_dict["keywords"] = ", ".join(tags)

    schema_json = json.dumps(schema_dict, indent=2, ensure_ascii=False)
    # Prevent </script> injection or raw unescaped closing tags inside script block
    schema_json = schema_json.replace("<", "\\u003c").replace(">", "\\u003e")

    # Paragraphs for description
    desc_paragraphs = [p.strip() for p in description.split("\n\n") if p.strip()]
    if not desc_paragraphs:
        desc_paragraphs = [f"Complete digital download package for {escaped_title}."]

    html_parts = []
    html_parts.append("<!DOCTYPE html>")
    html_parts.append('<html lang="en">')
    html_parts.append("<head>")
    html_parts.append('  <meta charset="utf-8">')
    html_parts.append('  <meta name="viewport" content="width=device-width, initial-scale=1.0">')
    html_parts.append(f"  <title>{escaped_meta_title}</title>")
    html_parts.append(f'  <meta name="description" content="{escaped_meta_desc}">')

    # Open Graph Tags
    html_parts.append(f'  <meta property="og:title" content="{escaped_meta_title}">')
    html_parts.append(f'  <meta property="og:description" content="{escaped_meta_desc}">')
    html_parts.append('  <meta property="og:type" content="product">')
    html_parts.append('  <meta property="og:url" content="./index.html">')
    if cover_file:
        html_parts.append(f'  <meta property="og:image" content="{html.escape(cover_file)}">')

    # JSON-LD Schema
    html_parts.append('  <script type="application/ld+json">')
    html_parts.append(schema_json)
    html_parts.append("  </script>")

    # Clean, responsive inline CSS with high contrast (WCAG AA >= 4.5:1), no external imports
    html_parts.append("  <style>")
    html_parts.append("""    :root {
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --text: #0f172a;
      --muted: #334155;
      --primary: #1e3a8a;
      --primary-hover: #172554;
      --border: #cbd5e1;
      --accent-bg: #f1f5f9;
      --radius: 8px;
    }
    *, *::before, *::after {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.6;
      font-size: 16px;
      padding: 24px 16px;
    }
    .container {
      max-width: 900px;
      margin: 0 auto;
    }
    header {
      text-align: center;
      margin-bottom: 32px;
    }
    h1 {
      font-size: 2rem;
      line-height: 1.25;
      color: var(--text);
      margin-bottom: 12px;
    }
    .lead {
      font-size: 1.15rem;
      color: var(--muted);
      max-width: 700px;
      margin: 0 auto;
    }
    .gallery {
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
      margin-bottom: 32px;
    }
    @media (min-width: 640px) {
      .gallery {
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      }
    }
    .gallery-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .gallery-card img {
      width: 100%;
      height: auto;
      display: block;
      object-fit: cover;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 24px;
      margin-bottom: 24px;
    }
    h2 {
      font-size: 1.4rem;
      margin-bottom: 16px;
      color: var(--primary);
      border-bottom: 2px solid var(--accent-bg);
      padding-bottom: 8px;
    }
    p {
      margin-bottom: 16px;
      color: var(--text);
    }
    p:last-child {
      margin-bottom: 0;
    }
    ul {
      list-style-type: square;
      padding-left: 24px;
      margin-bottom: 16px;
    }
    li {
      margin-bottom: 8px;
      color: var(--text);
    }
    .cta-section {
      text-align: center;
      padding: 32px 16px;
      background: var(--accent-bg);
      border-radius: var(--radius);
      border: 1px solid var(--border);
      margin-bottom: 32px;
    }
    .cta-btn {
      display: inline-block;
      background-color: var(--primary);
      color: #ffffff;
      padding: 14px 28px;
      font-size: 1.1rem;
      font-weight: 600;
      text-decoration: none;
      border-radius: 6px;
      transition: background-color 0.2s;
    }
    .cta-btn:hover, .cta-btn:focus {
      background-color: var(--primary-hover);
    }
    .faq-item {
      margin-bottom: 16px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 12px;
    }
    .faq-item:last-child {
      border-bottom: none;
      padding-bottom: 0;
    }
    .faq-question {
      font-size: 1.1rem;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 6px;
    }
    .faq-answer {
      color: var(--muted);
    }
    .tags-container {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    .tag-badge {
      background: var(--accent-bg);
      color: var(--text);
      font-size: 0.85rem;
      padding: 4px 10px;
      border-radius: 4px;
      border: 1px solid var(--border);
    }
    footer {
      text-align: center;
      padding: 24px 0;
      font-size: 0.9rem;
      color: var(--muted);
      border-top: 1px solid var(--border);
      margin-top: 32px;
    }""")
    html_parts.append("  </style>")
    html_parts.append("</head>")
    html_parts.append("<body>")
    html_parts.append('  <div class="container">')

    # Header
    html_parts.append("    <header>")
    html_parts.append(f"      <h1>{escaped_title}</h1>")
    first_sentence = desc_paragraphs[0] if desc_paragraphs else escaped_title
    html_parts.append(f'      <p class="lead">{html.escape(first_sentence)}</p>')
    html_parts.append("    </header>")

    # Main content
    html_parts.append("    <main>")

    # Image Gallery (if any images present)
    if image_filenames:
        html_parts.append('      <section class="gallery" aria-label="Product Images">')
        for idx, img_name in enumerate(image_filenames):
            alt_text = f"{title} preview {idx + 1}" if idx > 0 else f"{title} cover presentation"
            escaped_alt = html.escape(alt_text)
            escaped_src = html.escape(img_name)
            html_parts.append('        <div class="gallery-card">')
            html_parts.append(f'          <img src="{escaped_src}" alt="{escaped_alt}" loading="lazy">')
            html_parts.append("        </div>")
        html_parts.append("      </section>")

    # Primary Call to Action
    html_parts.append('      <section class="cta-section" aria-label="Purchase Link">')
    html_parts.append(f'        <h2>Get Instant Access to {escaped_title}</h2>')
    html_parts.append('        <p>Digital files ready for immediate download and personal use.</p>')
    html_parts.append('        <div style="margin-top: 16px;">')
    html_parts.append('          <a href="[STORE LINK]" class="cta-btn">[STORE LINK]</a>')
    html_parts.append("        </div>")
    html_parts.append("      </section>")

    # Product Overview Section
    html_parts.append('      <section class="card" aria-label="Product Description">')
    html_parts.append("        <h2>Product Overview</h2>")
    for para in desc_paragraphs:
        html_parts.append(f"        <p>{html.escape(para)}</p>")
    html_parts.append("      </section>")

    # What You Get Section
    if what_you_get:
        html_parts.append('      <section class="card" aria-label="Included Files">')
        html_parts.append("        <h2>What You Get</h2>")
        html_parts.append("        <ul>")
        for item in what_you_get:
            html_parts.append(f"          <li>{html.escape(item)}</li>")
        html_parts.append("        </ul>")
        html_parts.append("      </section>")

    # FAQ Section
    if faq_items:
        html_parts.append('      <section class="card" aria-label="Frequently Asked Questions">')
        html_parts.append("        <h2>Frequently Asked Questions</h2>")
        for q, a in faq_items:
            html_parts.append('        <article class="faq-item">')
            html_parts.append(f'          <h3 class="faq-question">{html.escape(q)}</h3>')
            html_parts.append(f'          <p class="faq-answer">{html.escape(a)}</p>')
            html_parts.append("        </article>")
        html_parts.append("      </section>")

    # Tags section
    if tags:
        html_parts.append('      <section class="card" aria-label="Product Tags">')
        html_parts.append("        <h2>Tags</h2>")
        html_parts.append('        <div class="tags-container">')
        for tag in tags:
            html_parts.append(f'          <span class="tag-badge">{html.escape(tag)}</span>')
        html_parts.append("        </div>")
        html_parts.append("      </section>")

    # Secondary Call to Action at bottom
    html_parts.append('      <section class="cta-section" aria-label="Final Call to Action">')
    html_parts.append(f'        <h2>Start Using {escaped_title} Today</h2>')
    html_parts.append('        <p>Instant digital access. No waiting for shipping.</p>')
    html_parts.append('        <div style="margin-top: 16px;">')
    html_parts.append('          <a href="[STORE LINK]" class="cta-btn">[STORE LINK]</a>')
    html_parts.append("        </div>")
    html_parts.append("      </section>")

    html_parts.append("    </main>")

    # Footer
    html_parts.append("    <footer>")
    html_parts.append("      <p>For personal use only. Digital download files.</p>")
    html_parts.append("    </footer>")

    html_parts.append("  </div>")
    html_parts.append("</body>")
    html_parts.append("</html>")
    html_parts.append("")

    return "\n".join(html_parts)


def build_landing_page(pack_dir: Path | str, out_dir: Optional[Path | str] = None) -> Path:
    """Build landing page for a single pack folder and write index.html + copied images atomically.

    Never modifies pack_dir.
    Writes to out_dir / <pack_name> / landing / or DEFAULT_MARKETING_DIR / <pack_name> / landing /.
    Returns the path to the written index.html.
    """
    pack_path = Path(pack_dir).resolve()
    if not pack_path.is_dir():
        raise FileNotFoundError(f"Pack folder not found: {pack_path}")

    pack_name = pack_path.name
    if out_dir:
        target_dir = Path(out_dir).resolve()
        # If out_dir already ends with 'landing', use it directly; else nest <pack_name>/landing
        if target_dir.name != "landing":
            target_dir = target_dir / pack_name / "landing"
    else:
        target_dir = DEFAULT_MARKETING_DIR / pack_name / "landing"

    target_dir.parent.mkdir(parents=True, exist_ok=True)

    # Parse listing
    listing_path = pack_path / "listing.txt"
    listing_data = parse_listing_file(listing_path)
    title = listing_data["title"]
    description = listing_data["description"]
    what_you_get = listing_data["what_you_get"]
    tags = listing_data["tags"]

    # Gather FAQs
    faq_items = get_pack_faq(pack_path)

    # Gather images to copy & resize
    images = find_pack_images(pack_path)

    # Staging directory for atomic write
    temp_dir = Path(tempfile.mkdtemp(prefix="landing_stage_"))
    try:
        copied_filenames = []
        for img_path in images:
            dest_img = temp_dir / img_path.name
            copy_and_resize_image(img_path, dest_img, max_dim=MAX_IMAGE_DIMENSION)
            copied_filenames.append(img_path.name)

        # Generate HTML
        html_content = generate_landing_html(
            pack_name=pack_name,
            title=title,
            description=description,
            what_you_get=what_you_get,
            tags=tags,
            faq_items=faq_items,
            image_filenames=copied_filenames,
        )

        html_file = temp_dir / "index.html"
        html_file.write_text(html_content, encoding="utf-8")

        # Atomic replacement of target_dir
        if target_dir.exists():
            # If target_dir is already there, remove old content safely
            shutil.rmtree(target_dir, ignore_errors=True)
        shutil.move(str(temp_dir), str(target_dir))

    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    final_index = target_dir / "index.html"
    return final_index


def build_all(packs_dir: Optional[Path | str] = None, out_root: Optional[Path | str] = None) -> List[Path]:
    """Find all ready packs and generate landing pages for each."""
    pdir = Path(packs_dir) if packs_dir else DEFAULT_PACKS_DIR
    if not pdir.is_dir():
        return []

    results = []
    for pack in sorted(pdir.iterdir()):
        if pack.is_dir() and (pack / "listing.txt").is_file():
            res = build_landing_page(pack, out_dir=out_root)
            results.append(res)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate self-contained landing page for upload packs.")
    parser.add_argument("pack", nargs="?", help="Specific pack directory (optional)")
    parser.add_argument("--all", action="store_true", help="Generate landing pages for all packs in packs_dir")
    parser.add_argument("--packs-dir", default=str(DEFAULT_PACKS_DIR), help="Upload packs directory")
    parser.add_argument("--out", default=None, help="Output destination folder")
    args = parser.parse_args(argv)

    if args.all or not args.pack:
        written = build_all(packs_dir=args.packs_dir, out_root=args.out)
        count = len(written)
        # Tenglish house style summary output
        print(f"Landing pages tayaru ayyayi: {count} packs complete. Anni files landing/index.html lo save chesam.")
        return 0
    else:
        pack_path = Path(args.pack)
        if not pack_path.is_dir():
            print(f"Tappu: pack folder kanapadaledu: {pack_path}", file=sys.stderr)
            return 1
        written_path = build_landing_page(pack_path, out_dir=args.out)
        print(f"Landing page tayaru ayindi: {written_path} lo ready ga undi.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
