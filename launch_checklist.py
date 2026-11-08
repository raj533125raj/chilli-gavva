#!/usr/bin/env python3
"""launch_checklist.py — Manual upload checklist generator for ready upload packs.

For each ready pack, writes:
  exported_content/marketing/<pack>/LAUNCH.md

A step-by-step manual upload checklist for Gumroad, Topmate, and Instamojo using:
  - listing_fields.py output (gumroad_block, topmate_block, instamojo_block)
  - Files to upload (product.zip, license, etc.)
  - Cover and preview image upload order (cover.png, preview-*.png)
  - The 5 pin titles (loaded from pins.json if present, else fallback or empty note)
  - Paths to marketing assets (Instagram carousel slides/caption, blog article)
  - Clear "Price: SET BY OWNER" note
  - Hand-tickable Markdown checkboxes (- [ ]) for the owner

Safety & compliance:
  - Pillow is optional; any other library must be guarded.
  - No money amounts ($ / USD / INR etc.) anywhere in generated output.
  - No brand logos or brand names injected.
  - Nothing is published or posted automatically.
  - Outputs are written OUTSIDE the pack folders into exported_content/marketing/<pack>/
  - Writes are atomic (via temporary files) and deterministic.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import listing_fields

# Money pattern check
MONEY_PATTERNS = listing_fields.MONEY_PATTERNS


def check_for_money(text: str) -> Optional[str]:
    """Return matching money string if found in text, else None."""
    for pat in MONEY_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def clean_no_money_or_brands(text: str) -> str:
    """Sanitize text to guarantee no money amounts remain."""
    for pat in MONEY_PATTERNS:
        text = pat.sub("[PRICE REMOVED]", text)
    return text


def discover_ready_packs(base_products_dir: Path | str = "exported_content/products") -> List[Path]:
    """Find all ready upload packs under base_products_dir/upload or base_products_dir.

    A pack folder is considered ready if it is a directory, contains listing.txt
    and product.zip (or at least listing.txt for upload staging).
    """
    base = Path(base_products_dir)
    upload_dir = base / "upload" if (base / "upload").is_dir() else base
    if not upload_dir.is_dir():
        return []

    ready: List[Path] = []
    for item in sorted(upload_dir.iterdir()):
        if not item.is_dir() or item.name.startswith("."):
            continue
        if (item / "listing.txt").is_file():
            ready.append(item)
    return ready


def get_pack_files_to_upload(pack_dir: Path) -> List[str]:
    """Determine the files to upload for this pack.

    Usually includes product.zip, LICENSE.txt (if present), etc.
    """
    candidates = ["product.zip", "LICENSE.txt", "checklist.txt"]
    files = []
    for c in candidates:
        if (pack_dir / c).is_file():
            files.append(c)
    for p in sorted(pack_dir.iterdir()):
        if p.is_file() and p.name not in files and not p.name.startswith("."):
            if p.suffix.lower() in [".zip", ".pdf", ".xlsx", ".docx"]:
                files.append(p.name)
    if "product.zip" not in files:
        files.insert(0, "product.zip")
    return files


def get_cover_and_preview_order(pack_dir: Path) -> List[str]:
    """Return ordered list of cover and preview images."""
    images = []
    if (pack_dir / "cover.png").is_file():
        images.append("cover.png")

    previews = []
    for p in pack_dir.iterdir():
        if p.is_file() and p.name.lower().startswith("preview") and p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
            previews.append(p.name)
    previews.sort(key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)])
    images.extend(previews)

    for p in sorted(pack_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
            if p.name not in images and not p.name.startswith("."):
                images.append(p.name)

    if not images:
        images.append("cover.png")
    return images


def get_pin_titles(marketing_pack_dir: Path) -> List[str]:
    """Load pin titles from pins.json if present in marketing_pack_dir/pins/pins.json."""
    pins_json_path = marketing_pack_dir / "pins" / "pins.json"
    titles: List[str] = []
    if pins_json_path.is_file():
        try:
            with open(pins_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "title" in item and item["title"]:
                            t = str(item["title"]).strip()
                            if t and t not in titles:
                                titles.append(t)
                        elif isinstance(item, str) and item.strip():
                            titles.append(item.strip())
        except Exception:
            pass

    if not titles:
        ab_titles_path = marketing_pack_dir / "pins" / "ab_titles.txt"
        if ab_titles_path.is_file():
            try:
                for line in ab_titles_path.read_text(encoding="utf-8").splitlines():
                    cleaned = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
                    if cleaned and cleaned not in titles:
                        titles.append(cleaned)
            except Exception:
                pass

    return titles[:5]


def get_marketing_paths(pack_id: str, marketing_dir: Path) -> Dict[str, str]:
    """Find relative/standard marketing paths for carousel, pins, and blog."""
    pack_mktg = marketing_dir / pack_id
    carousel_dir = pack_mktg / "carousel"
    pins_dir = pack_mktg / "pins"
    blog_file = pack_mktg / "blog.md"

    mktg_str = str(marketing_dir).replace("\\", "/")
    return {
        "carousel_dir": f"{mktg_str}/{pack_id}/carousel",
        "carousel_caption": f"{mktg_str}/{pack_id}/carousel/caption.txt",
        "pins_dir": f"{mktg_str}/{pack_id}/pins",
        "pins_json": f"{mktg_str}/{pack_id}/pins/pins.json",
        "blog_md": f"{mktg_str}/{pack_id}/blog.md",
        "carousel_exists": carousel_dir.is_dir(),
        "blog_exists": blog_file.is_file(),
        "pins_exists": pins_dir.is_dir(),
    }


def _extract_section(text: str, label: str) -> str:
    """Robust extractor for section value by label."""
    m = re.search(rf"^{re.escape(label)}:\s*(.*?)(?=\n[A-Za-z][A-Za-z /_-]*:|\Z)", text, re.DOTALL | re.MULTILINE)
    if m:
        return m.group(1).strip()
    return listing_fields._section(text, label)


def parse_listing_info(listing_file: Path) -> Dict[str, str]:
    """Read listing.txt and extract Title, Description, Tags, What you get."""
    text = listing_file.read_text(encoding="utf-8")
    title = _extract_section(text, "Title")
    description = _extract_section(text, "Description")
    tags = _extract_section(text, "Tags")
    what_you_get = _extract_section(text, "What you get")
    return {
        "raw": text,
        "title": title or listing_file.parent.name,
        "description": description,
        "tags": tags,
        "what_you_get": what_you_get,
    }


def generate_launch_markdown(
    pack_id: str,
    pack_dir: Path,
    marketing_dir: Path = Path("exported_content/marketing"),
) -> str:
    """Generate the full content of LAUNCH.md for the given pack."""
    listing_path = pack_dir / "listing.txt"
    if not listing_path.is_file():
        raise FileNotFoundError(f"Missing listing.txt in {pack_dir}")

    info = parse_listing_info(listing_path)
    title = info["title"]
    desc = info["description"]
    tags = info["tags"]
    raw = info["raw"]

    # Listing blocks from listing_fields
    gumroad = listing_fields.gumroad_block(title, desc, tags)
    topmate = listing_fields.topmate_block(title, desc)
    what_you_get_block = listing_fields._section_what_you_get_block(desc, raw)
    instamojo = listing_fields.instamojo_block(title, desc, what_you_get_block)

    # Pack files to upload
    files_to_upload = get_pack_files_to_upload(pack_dir)
    image_order = get_cover_and_preview_order(pack_dir)

    # Marketing paths
    mktg_paths = get_marketing_paths(pack_id, marketing_dir)
    pin_titles = get_pin_titles(marketing_dir / pack_id)

    lines: List[str] = []
    lines.append(f"# Launch Checklist: {title}")
    lines.append("")
    lines.append("> **Note**: Price is **SET BY OWNER**. All upload steps below are manual.")
    lines.append("> Nothing is published or posted automatically.")
    lines.append("")

    # Section 1: Pre-Upload Checklist
    lines.append("## 1. Pre-Upload Verification")
    lines.append("- [ ] Verify `product.zip` contents and extracted files")
    lines.append("- [ ] Review preview images and cover design")
    lines.append("- [ ] Determine pricing (Pricing rule: SET BY OWNER)")
    lines.append("")

    # Section 2: Files to Upload
    lines.append("## 2. Files to Upload")
    for f in files_to_upload:
        lines.append(f"- [ ] Upload file: `{f}`")
    lines.append("")

    # Section 3: Visual Assets & Order
    lines.append("## 3. Cover & Preview Order")
    for idx, img in enumerate(image_order, start=1):
        role = "Cover Image" if idx == 1 else f"Preview {idx - 1}"
        lines.append(f"- [ ] Slot {idx} ({role}): `{img}`")
    lines.append("")

    # Section 4: Store Listing Details
    lines.append("## 4. Platform Listings")
    lines.append("")

    lines.append("### Gumroad")
    lines.append("- [ ] Create digital product on Gumroad")
    lines.append("- [ ] Copy title, description, and tags:")
    lines.append("```")
    lines.append(gumroad.strip())
    lines.append("```")
    lines.append("- [ ] Upload files and set cover image")
    lines.append("- [ ] Set price manually: **SET BY OWNER**")
    lines.append("- [ ] Publish product")
    lines.append("")

    lines.append("### Topmate")
    lines.append("- [ ] Create digital product on Topmate")
    lines.append("- [ ] Copy title and descriptions:")
    lines.append("```")
    lines.append(topmate.strip())
    lines.append("```")
    lines.append("- [ ] Upload files and set cover image")
    lines.append("- [ ] Set price manually: **SET BY OWNER**")
    lines.append("- [ ] Publish product")
    lines.append("")

    lines.append("### Instamojo")
    lines.append("- [ ] Create digital product on Instamojo")
    lines.append("- [ ] Copy title and description:")
    lines.append("```")
    lines.append(instamojo.strip())
    lines.append("```")
    lines.append("- [ ] Upload files and set cover image")
    lines.append("- [ ] Set price manually: **SET BY OWNER**")
    lines.append("- [ ] Publish product")
    lines.append("")

    # Section 5: Marketing Assets & Distribution
    lines.append("## 5. Marketing Assets & Promotion")
    lines.append(f"- [ ] Instagram Carousel folder: `{mktg_paths['carousel_dir']}`")
    lines.append(f"  - Caption file: `{mktg_paths['carousel_caption']}`")
    lines.append("- [ ] Post Instagram carousel manually (Swipe gallery + link in bio)")

    lines.append(f"- [ ] Pinterest Pins folder: `{mktg_paths['pins_dir']}`")
    lines.append(f"  - Pin details: `{mktg_paths['pins_json']}`")
    if pin_titles:
        lines.append("  - Pin A/B title variants:")
        for pt in pin_titles:
            lines.append(f"    - [ ] Pin Title: {pt}")
    else:
        lines.append("  - [ ] 5 Pin titles from pins.json (Generate pins if not yet built)")

    lines.append(f"- [ ] SEO Blog Article: `{mktg_paths['blog_md']}`")
    lines.append("  - [ ] Publish or syndicate blog article with store link")
    lines.append("")

    # Section 6: Completion
    lines.append("## 6. Launch Sign-Off")
    lines.append("- [ ] Verify live product links across all active stores")
    lines.append("- [ ] Launch verified by owner")
    lines.append("")

    content = "\n".join(lines)
    money_hit = check_for_money(content)
    if money_hit:
        content = clean_no_money_or_brands(content)

    return content


def write_launch_checklist(
    pack_dir: Path | str,
    marketing_dir: Path | str = "exported_content/marketing",
) -> Path:
    """Generate and write LAUNCH.md atomically to exported_content/marketing/<pack>/LAUNCH.md."""
    pack_path = Path(pack_dir)
    pack_id = pack_path.name
    mktg_path = Path(marketing_dir)
    out_dir = mktg_path / pack_id
    out_dir.mkdir(parents=True, exist_ok=True)

    target_file = out_dir / "LAUNCH.md"
    content = generate_launch_markdown(pack_id, pack_path, mktg_path)

    # Atomic write
    fd, tmp_file = tempfile.mkstemp(dir=out_dir, prefix=".launch_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_file, target_file)

    return target_file


def process_all_ready_packs(
    products_dir: Path | str = "exported_content/products",
    marketing_dir: Path | str = "exported_content/marketing",
) -> List[Path]:
    """Generate LAUNCH.md for all ready packs."""
    packs = discover_ready_packs(products_dir)
    results: List[Path] = []
    for pack in packs:
        out = write_launch_checklist(pack, marketing_dir)
        results.append(out)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate manual upload checklists (LAUNCH.md) for ready upload packs."
    )
    parser.add_argument("--pack", help="Specific pack directory or ID to generate checklist for.")
    parser.add_argument(
        "--all", action="store_true", help="Generate checklists for all ready packs."
    )
    parser.add_argument(
        "--products-dir",
        default="exported_content/products",
        help="Products directory (defaults to exported_content/products).",
    )
    parser.add_argument(
        "--marketing-dir",
        default="exported_content/marketing",
        help="Marketing directory (defaults to exported_content/marketing).",
    )

    args = parser.parse_args(argv)

    if args.pack:
        pack_path = Path(args.pack)
        if not pack_path.is_dir():
            cand1 = Path(args.products_dir) / "upload" / args.pack
            cand2 = Path(args.products_dir) / args.pack
            if cand1.is_dir():
                pack_path = cand1
            elif cand2.is_dir():
                pack_path = cand2
            else:
                print(f"Error: Pack directory {args.pack!r} not found.", file=sys.stderr)
                return 1

        out = write_launch_checklist(pack_path, args.marketing_dir)
        print(f"Generated {out}")
        return 0

    written = process_all_ready_packs(args.products_dir, args.marketing_dir)
    print(f"Generated {len(written)} checklist(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
