#!/usr/bin/env python3
"""seo_meta.py — SEO metadata and image naming planner for upload packs.

For each pack:
- Proposes SEO file names for images (lowercase-hyphen, keyword first, <= 60 chars)
- Proposes alt texts (descriptive, keyword-relevant, <= 125 chars)
- Writes a plan file: exported_content/marketing/<pack>/seo_meta.json
- NEVER renames files in the pack folder! (Pack files remain untouched)
- Checks and flags images that have no alt text in the plan
- Atomic writes outside private pack folders
- Strictly excludes brand logos/names and money amounts.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

# Supported image file extensions
IMAGE_EXTENSIONS: Set[str] = {".png", ".jpg", ".jpeg", ".webp"}

# Brand names to exclude
BRAND_NAMES: Set[str] = {
    "gumroad", "topmate", "etsy", "notion", "pinterest", "instagram", "goodnotes",
    "excel", "canva", "facebook", "twitter", "tiktok", "youtube", "linkedin",
    "shopify", "amazon", "apple", "google", "microsoft", "word",
}

# Currency patterns to exclude
_MONEY_PATTERN = re.compile(
    r"(?:\$|₹|€|£|¥|\b(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?)\s*\d+|\d+\s*(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?))",
    re.I
)

STOP_WORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "what",
    "which", "this", "that", "these", "those", "then", "just", "so", "than",
    "such", "both", "through", "about", "for", "is", "of", "while", "during",
    "to", "from", "in", "out", "on", "off", "again", "further", "once",
    "here", "there", "when", "where", "why", "how", "all", "any", "each",
    "few", "more", "most", "other", "some", "no", "nor", "not", "only",
    "own", "same", "too", "very", "can", "will", "with", "by", "at",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "our", "your",
}


def clean_slug(text: str, max_len: int = 60) -> str:
    """Format string to lowercase-hyphen slug without brands, max_len chars."""
    t = text.lower()
    # Remove money patterns
    t = _MONEY_PATTERN.sub("", t)
    # Remove brands
    for b in BRAND_NAMES:
        t = re.sub(rf"\b{re.escape(b)}\b", "", t)

    t = re.sub(r"[^\w\s-]", " ", t)
    words = [w for w in t.split() if w and w not in BRAND_NAMES]
    slug = "-".join(words)
    slug = re.sub(r"-+", "-", slug).strip("-")

    if len(slug) > max_len:
        # Cut at hyphen boundary if possible
        head = slug[:max_len]
        dash = head.rfind("-")
        if dash > 10:
            slug = head[:dash]
        else:
            slug = head.rstrip("-")
    return slug


def clean_alt_text(text: str, max_len: int = 125) -> str:
    """Format alt text string: clean, no brands/money, <= max_len chars."""
    t = text.strip()
    t = _MONEY_PATTERN.sub("", t)
    for b in BRAND_NAMES:
        t = re.sub(rf"\b{re.escape(b)}\b", "", t, flags=re.IGNORECASE)

    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= max_len:
        return t

    head = t[:max_len]
    sp = head.rfind(" ")
    if sp > 20:
        return head[:sp].rstrip()
    return head.rstrip()


def extract_pack_info(pack_dir: Path) -> Dict[str, Any]:
    """Extract title, tags, benefits, and images from pack directory."""
    out: Dict[str, Any] = {
        "title": "",
        "tags": [],
        "benefits": [],
        "images": [],
    }
    if not pack_dir.is_dir():
        return out

    # Find images in pack folder
    for f in sorted(pack_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
            out["images"].append(f.name)

    listing_file = pack_dir / "listing.txt"
    if not listing_file.is_file():
        listing_file = pack_dir / "listing.md"

    if not listing_file.is_file():
        return out

    try:
        content = listing_file.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception:
        return out

    lines = content.splitlines()
    in_tags = False
    in_what = False

    for line in lines:
        raw_strip = line.strip()
        lower = raw_strip.lower()

        if lower.startswith("## tags") or lower.startswith("# tags") or lower == "tags:":
            in_tags = True
            in_what = False
            continue
        elif lower.startswith("## what") or lower.startswith("# what") or lower.startswith("what you get:"):
            in_what = True
            in_tags = False
            continue
        elif lower.startswith("#") or (":" in lower and not lower.startswith("tag")):
            in_tags = False
            in_what = False

        if lower.startswith("title:") or lower.startswith("# "):
            if not out["title"]:
                out["title"] = line.split(":", 1)[-1].lstrip("#").strip()
        elif lower.startswith("tags:") or lower.startswith("tag:"):
            part = line.split(":", 1)[1]
            out["tags"].extend(t.strip() for t in part.split(",") if t.strip())
        elif in_tags and raw_strip:
            out["tags"].extend(t.strip() for t in raw_strip.split(",") if t.strip())
        elif in_what and raw_strip:
            cleaned_item = re.sub(r"^[-*•\d.)\s]+", "", raw_strip).strip()
            if cleaned_item:
                out["benefits"].append(cleaned_item)

    return out


def propose_seo_meta_plan(pack_dir: Path | str) -> Dict[str, Any]:
    """Generate proposed SEO file names and alt texts plan for a pack without renaming."""
    p_dir = Path(pack_dir)
    pack_name = p_dir.name
    info = extract_pack_info(p_dir)

    title = info["title"] or pack_name.replace("-", " ").replace("_", " ")
    tags = [t for t in info["tags"] if t.lower() not in BRAND_NAMES]

    # Determine main keyword (first 2-3 words or best tag)
    main_kw = ""
    for t in tags:
        t_clean = t.lower()
        if len(t_clean.split()) >= 2 and t_clean in title.lower():
            main_kw = t_clean
            break
    if not main_kw and tags:
        main_kw = tags[0].lower()
    if not main_kw:
        title_words = [w for w in title.lower().split() if w not in STOP_WORDS]
        main_kw = " ".join(title_words[:2]) if title_words else "printable-planner"

    kw_slug = clean_slug(main_kw, max_len=30)
    if not kw_slug:
        kw_slug = "printable"

    # Plan for each image
    images_plan: List[Dict[str, Any]] = []
    missing_alt_images: List[str] = []

    # Check for existing alt texts in alt.txt
    existing_alts: Dict[str, str] = {}
    alt_file = p_dir / "alt.txt"
    if alt_file.is_file():
        try:
            for l in alt_file.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
                if ":" in l:
                    k, v = l.split(":", 1)
                    existing_alts[k.strip().lower()] = v.strip()
        except Exception:
            pass

    for idx, img_name in enumerate(info["images"], 1):
        ext = Path(img_name).suffix.lower()
        base_stem = Path(img_name).stem.lower()
        # Clean money or brands from original stem before determining role
        cleaned_stem = _MONEY_PATTERN.sub("", base_stem)
        for b in BRAND_NAMES:
            cleaned_stem = re.sub(rf"\b{re.escape(b)}\b", "", cleaned_stem)

        # Propose SEO file name: lowercase-hyphen, keyword first, <= 60 chars
        # e.g. habit-tracker-cover.png, habit-tracker-preview-1.png
        if "cover" in cleaned_stem:
            role = "cover"
        elif "preview" in cleaned_stem:
            num = re.search(r"\d+", cleaned_stem)
            role = f"preview-{num.group(0)}" if num else f"preview-{idx}"
        elif "mockup" in cleaned_stem:
            role = "mockup"
        else:
            role = clean_slug(cleaned_stem, max_len=20) or f"view-{idx}"

        # Ensure keyword is first
        proposed_base = f"{kw_slug}-{role}"
        # Limit base so that full name <= 60 chars including ext
        max_base_len = 60 - len(ext)
        proposed_base = clean_slug(proposed_base, max_len=max_base_len)
        proposed_filename = f"{proposed_base}{ext}"

        # Propose alt text (<= 125 chars)
        alt_text = ""
        # 1. Try existing alts
        if img_name.lower() in existing_alts:
            alt_text = existing_alts[img_name.lower()]
        elif base_stem in existing_alts:
            alt_text = existing_alts[base_stem]
        elif role in existing_alts:
            alt_text = existing_alts[role]

        # 2. Synthesize descriptive alt text if not provided
        if not alt_text:
            if role == "cover":
                alt_text = f"{title} printable cover mockup layout"
            elif role.startswith("preview"):
                benefit_idx = idx - 2  # usually cover is 1st
                if 0 <= benefit_idx < len(info["benefits"]):
                    benefit_desc = info["benefits"][benefit_idx]
                    alt_text = f"{main_kw} preview sheet: {benefit_desc}"
                else:
                    alt_text = f"{main_kw} interior worksheet preview page"
            elif role == "mockup":
                alt_text = f"Printed {main_kw} on desk workspace setup"
            else:
                alt_text = f"{title} {role.replace('-', ' ')} page detail"

        alt_text = clean_alt_text(alt_text, max_len=125)

        has_alt = bool(alt_text)
        if not has_alt:
            missing_alt_images.append(img_name)

        images_plan.append({
            "original_filename": img_name,
            "proposed_filename": proposed_filename,
            "role": role,
            "alt_text": alt_text,
            "has_alt": has_alt,
            "proposed_length": len(proposed_filename),
            "alt_length": len(alt_text),
        })

    plan: Dict[str, Any] = {
        "pack_name": pack_name,
        "title": title,
        "main_keyword": main_kw,
        "keyword_slug": kw_slug,
        "images_count": len(info["images"]),
        "has_missing_alts": len(missing_alt_images) > 0,
        "missing_alts": missing_alt_images,
        "images": images_plan,
        "renamed_in_place": False,  # Explanatory flag: never rename files in pack folder!
    }
    return plan


def check_missing_alt_texts(plan: Dict[str, Any]) -> List[str]:
    """Flag images in the plan that have no alt text."""
    missing: List[str] = []
    for img in plan.get("images", []):
        if not img.get("alt_text") or not img.get("has_alt"):
            missing.append(img.get("original_filename", "unknown"))
    return missing


def generate_pack_seo_meta(
    pack_dir: Path | str,
    marketing_dir: Path | str = "exported_content/marketing",
) -> Path:
    """Generate SEO meta plan and atomically save to exported_content/marketing/<pack>/seo_meta.json."""
    p_dir = Path(pack_dir)
    pack_name = p_dir.name
    plan = propose_seo_meta_plan(p_dir)

    out_dir = Path(marketing_dir) / pack_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "seo_meta.json"

    # Atomic write
    temp_dir = tempfile.mkdtemp(prefix="seometa_")
    try:
        tmp_target = Path(temp_dir) / "seo_meta.tmp.json"
        tmp_target.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_target.replace(out_file)
    finally:
        if os.path.exists(temp_dir):
            try:
                os.rmdir(temp_dir)
            except Exception:
                pass

    return out_file


def generate_all_seo_meta(
    products_dir: Path | str = "exported_content/products",
    marketing_dir: Path | str = "exported_content/marketing",
) -> List[Path]:
    """Generate SEO meta plans for all ready packs under products directory."""
    p_dir = Path(products_dir)
    upload_dirs: List[Path] = []
    candidates = [p_dir / "upload", p_dir]
    for cand in candidates:
        if cand.is_dir():
            for sub in cand.iterdir():
                if sub.is_dir() and ((sub / "listing.txt").is_file() or (sub / "listing.md").is_file()):
                    if sub not in upload_dirs:
                        upload_dirs.append(sub)

    written: List[Path] = []
    for pack in upload_dirs:
        out = generate_pack_seo_meta(pack, marketing_dir=marketing_dir)
        written.append(out)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate SEO image naming and alt text plans for packs.")
    parser.add_argument("--pack", help="Specific pack directory to generate plan for.")
    parser.add_argument("--all", action="store_true", help="Generate plans for all upload packs.")
    parser.add_argument("--products-dir", default="exported_content/products", help="Products directory.")
    parser.add_argument("--marketing-dir", default="exported_content/marketing", help="Marketing output directory.")
    parser.add_argument("--check-missing", action="store_true", help="Flag images missing alt text and exit 1 if any.")
    args = parser.parse_args()

    if args.pack:
        target = Path(args.pack)
        plan_path = generate_pack_seo_meta(target, marketing_dir=args.marketing_dir)
        print(f"SEO meta plan generated: {plan_path}")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        missing = check_missing_alt_texts(plan)
        if missing:
            print(f"Warning: {len(missing)} images missing alt text: {missing}")
            if args.check_missing:
                sys.exit(1)
    elif args.all:
        paths = generate_all_seo_meta(products_dir=args.products_dir, marketing_dir=args.marketing_dir)
        print(f"Generated SEO meta plans for {len(paths)} packs.")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
