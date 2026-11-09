#!/usr/bin/env python3
"""shorts_script.py — YouTube Shorts Script Generator for Ready Upload Packs.

For each pack with reel frames (in exported_content/marketing/<pack>/reel/),
this module generates shorts.md:
  - Total duration 15–30 seconds
  - Timing per frame (matches reel frames, e.g. 8 frames ~ 2s to 3s per frame)
  - On-screen text (<= 6 words per frame)
  - Voice-over (VO) lines in English AND Tenglish
  - 3 title options (each <= 60 characters)
  - Description (<= 500 characters)
  - Exactly 5 hashtags (clean, no brands, no money)
  - Facts strictly from the listing (no invented claims, no medical/financial promises)
  - Never posts or uploads automatically
  - Outputs written OUTSIDE private pack folders into exported_content/marketing/<pack>/reel/shorts.md
    (and also exported_content/marketing/<pack>/shorts.md for convenience / root lookup)
  - Deterministic outputs and atomic writes
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

# Strict currency / money patterns
MONEY_PATTERNS = [
    re.compile(r"\$\s*\d+"),
    re.compile(r"₹\s*\d+"),
    re.compile(r"\b(?:USD|INR|EUR|GBP|AUD|CAD|Rs\.?|Rupees?|Dollars?|Cents?)\b\s*\d+", re.IGNORECASE),
    re.compile(r"\d+\s*(?:USD|INR|EUR|GBP|AUD|CAD|Rs\.?|Rupees?|Dollars?|Cents?)\b", re.IGNORECASE),
    re.compile(r"\b(?:price|pricing|cost|costs|discount|fee|fee\s+of|sale\s+price)\s*[:=]?\s*\d+", re.IGNORECASE),
]

BRAND_WORDS = {
    "canva",
    "notion",
    "etsy",
    "gumroad",
    "goodnotes",
    "ipad",
    "excel",
    "microsoftexcel",
    "word",
    "microsoftword",
    "google",
    "googledocs",
    "googlesheets",
    "chatgpt",
    "openai",
    "amazon",
    "linkedin",
    "payhip",
    "apple",
    "applenumbers",
    "microsoft",
}

CURRENCY_WORDS = {
    "usd",
    "inr",
    "rs",
    "rupee",
    "rupees",
    "dollar",
    "dollars",
    "cents",
    "cent",
    "eur",
    "gbp",
    "price",
    "pricing",
    "cost",
    "costs",
    "sale",
    "pay",
    "payment",
}

FALLBACK_HASHTAGS = [
    "#shorts",
    "#printable",
    "#planner",
    "#productivity",
    "#organization",
    "#templates",
    "#stationery",
    "#focus",
]


def contains_money(text: str) -> bool:
    """Check if text contains any money, currency symbols, or price amounts."""
    for pattern in MONEY_PATTERNS:
        if pattern.search(text):
            return True
    return False


def sanitize_text(text: str) -> str:
    """Strip brand names and money amounts from text."""
    if not text:
        return ""
    cleaned = text
    for pattern in MONEY_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    for b in BRAND_WORDS:
        cleaned = re.sub(rf"\b{b}\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def limit_words(text: str, max_words: int = 6) -> str:
    """Trim text to at most max_words words without punctuation at the end."""
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    trimmed = " ".join(words[:max_words])
    trimmed = re.sub(r"[,;:\-–—]+$", "", trimmed).strip()
    return trimmed


def parse_listing_file(listing_path: Path) -> Dict[str, Any]:
    """Extract title, tags, description, and benefits from listing file."""
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


def clean_hashtags(raw_tags: List[str], count: int = 5) -> List[str]:
    """Produce exactly `count` clean hashtags (no brands, no money, lowercase, alphanumeric)."""
    cleaned: List[str] = []
    seen = set()

    for tag in raw_tags:
        s = str(tag).strip().lower()
        if not s:
            continue
        parts = [p.strip() for p in s.split(",") if p.strip()]
        for part in parts:
            if contains_money(part):
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
                if len(cleaned) >= count:
                    return cleaned

    for fb in FALLBACK_HASHTAGS:
        if len(cleaned) >= count:
            break
        if fb not in seen:
            seen.add(fb)
            cleaned.append(fb)

    return cleaned[:count]


def generate_title_options(title: str, pack_name: str) -> List[str]:
    """Generate exactly 3 YouTube Shorts title options (each <= 60 characters)."""
    clean_t = sanitize_text(title) or sanitize_text(pack_name).replace("-", " ").title()
    if not clean_t:
        clean_t = "Daily Productivity Planner"

    # Option 1: Direct / Benefit-focused
    opt1 = clean_t
    if len(opt1) > 52:
        opt1 = opt1[:52].rstrip()
    opt1 = f"{opt1} #Shorts"
    if len(opt1) > 60:
        opt1 = opt1[:60].rstrip()

    # Option 2: How-to / Quick tour
    # Shorten base if needed
    words = clean_t.split()
    short_base = clean_t
    if len(words) > 3:
        short_base = " ".join(words[:3])
    opt2 = f"Inside The {short_base} | Quick Tour"
    if len(opt2) > 60:
        opt2 = f"Inside {short_base} #Shorts"
    if len(opt2) > 60:
        opt2 = f"{short_base[:40]} #Shorts"

    # Option 3: Organize / Action focus
    opt3 = f"Organize Better With {short_base}"
    if len(opt3) > 60:
        opt3 = f"Plan Smarter With {short_base}"
    if len(opt3) > 60:
        opt3 = f"{short_base[:45]} Setup"

    # Strict check and fallback to ensure <= 60 chars and exactly 3 unique options
    options = []
    for cand in [opt1, opt2, opt3]:
        cand = sanitize_text(cand)
        if len(cand) > 60:
            cand = cand[:60].rstrip()
        options.append(cand)

    # Ensure uniqueness
    if options[1] == options[0]:
        options[1] = f"How To Use {short_base}"[:60]
    if options[2] in options[:2]:
        options[2] = f"{short_base} Overview #Shorts"[:60]

    return options[:3]


def generate_description(title: str, description: str, benefits: List[str], hashtags: List[str]) -> str:
    """Generate YouTube Shorts description (strictly <= 500 characters, no money, no brands)."""
    clean_t = sanitize_text(title)
    clean_d = sanitize_text(description)

    # Build description components
    lines = [f"A quick walk-through of {clean_t}."]
    if clean_d:
        first_sentence = clean_d.split(".")[0].strip()
        if first_sentence and len(first_sentence) < 140:
            lines.append(f"{first_sentence}.")

    # 2-3 key points from benefits
    clean_bens = [sanitize_text(b) for b in benefits if sanitize_text(b)]
    if clean_bens:
        lines.append("Included:")
        for b in clean_bens[:3]:
            lines.append(f"• {b}")

    lines.append("Link in channel bio for printable instant download.")
    lines.append(" ".join(hashtags))

    full_desc = "\n".join(lines).strip()
    if len(full_desc) > 500:
        # Trim while preserving hashtags and link
        ht_line = " ".join(hashtags)
        tail = f"\nLink in channel bio for download.\n{ht_line}"
        avail = 500 - len(tail)
        trimmed_body = full_desc[:avail].rsplit("\n", 1)[0].strip()
        full_desc = f"{trimmed_body}{tail}".strip()
        if len(full_desc) > 500:
            full_desc = full_desc[:500].rstrip()

    return full_desc


def build_frame_scripts(
    frame_names: List[str],
    title: str,
    pack_name: str,
    benefits: List[str],
    description: str,
) -> List[Dict[str, Any]]:
    """Build timing, on-screen text (<= 6 words), English VO, and Tenglish VO for each frame."""
    num_frames = len(frame_names) if frame_names else 8
    # Frame duration: 2s per frame for 8 frames = 16s total (valid 15–30s range)
    # If 6 frames -> 3s each (18s), if 10 frames -> 2s each (20s)
    frame_dur = 2.0 if num_frames >= 8 else 3.0
    total_dur = num_frames * frame_dur
    if total_dur < 15.0:
        frame_dur = 15.0 / num_frames

    clean_t = sanitize_text(title) or sanitize_text(pack_name).replace("-", " ").title()
    clean_bens = [sanitize_text(b) for b in benefits if sanitize_text(b)]
    clean_desc = sanitize_text(description)

    frames_script: List[Dict[str, Any]] = []

    for idx in range(num_frames):
        start_sec = idx * frame_dur
        end_sec = (idx + 1) * frame_dur
        timing_str = f"{start_sec:.1f}s – {end_sec:.1f}s"
        frame_num = idx + 1

        if frame_num == 1:
            # Hook frame
            # On-screen text: <= 6 words
            words = clean_t.split()
            ost = limit_words(f"Plan Smarter: {clean_t}", 6)
            vo_en = f"Here is a quick look inside the {clean_t}."
            vo_te = f"Ee video lo {clean_t} quick ga chuddam."
        elif frame_num == num_frames:
            # CTA frame (Final frame)
            ost = "Instant Download Link In Bio"
            vo_en = "Tap the link in bio to get your printable copy today."
            vo_te = "Mee printable copy kosam bio lo link tap cheyandi."
        elif frame_num == num_frames - 1:
            # Printed / Mockup frame
            ost = "Clean Printable Home Page Layout"
            vo_en = "Print it at home cleanly on standard paper."
            vo_te = "Standard paper meeda intlone clean ga print cheyandi."
        else:
            # Benefit / feature frames
            ben_idx = frame_num - 2
            if ben_idx < len(clean_bens):
                ben_raw = clean_bens[ben_idx]
                ost = limit_words(ben_raw, 6)
                vo_en = f"Includes {ben_raw.lower()} to stay organized."
                vo_te = f"Organization kosam indulo {ben_raw.lower()} undi."
            else:
                ost = f"Feature {frame_num} Daily Layout"
                vo_en = "Designed to keep your daily schedule structured and clear."
                vo_te = "Mee daily schedule clear ga plan cheyataniki design chesindi."

        # Safety check: ensure on-screen text is strictly <= 6 words
        ost = limit_words(ost, 6)

        frames_script.append({
            "frame_index": frame_num,
            "frame_file": frame_names[idx] if idx < len(frame_names) else f"frame-{frame_num}.png",
            "timing": timing_str,
            "duration": frame_dur,
            "on_screen_text": ost,
            "voice_over_en": vo_en,
            "voice_over_te": vo_te,
        })

    return frames_script


def render_shorts_markdown(
    pack_name: str,
    title_options: List[str],
    description: str,
    hashtags: List[str],
    frames_script: List[Dict[str, Any]],
) -> str:
    """Render the shorts.md Markdown content adhering to Tenglish house style & constraints."""
    total_seconds = sum(f["duration"] for f in frames_script)

    lines: List[str] = [
        f"# YouTube Shorts Script — {pack_name}",
        "",
        "> Owner guide: Ee script YouTube Shorts recording mariyu voice-over kosam tayaru chesam.",
        "> Facts strictly listing nunchi matrame unnay, money amounts levu.",
        "",
        "## Overview",
        "",
        f"- **Total Duration**: {total_seconds:.1f} seconds (15–30s range)",
        f"- **Total Frames**: {len(frames_script)}",
        "- **Format**: 1080x1920 Vertical Short",
        "- **Voice-Over**: Dual Language (English + Tenglish)",
        "",
        "## Title Options (Choose 1 for upload)",
        "",
    ]

    for idx, opt in enumerate(title_options, 1):
        lines.append(f"{idx}. {opt}")

    lines.extend([
        "",
        "## Description",
        "",
        "```text",
        description,
        "```",
        "",
        "## Hashtags",
        "",
        " ".join(hashtags),
        "",
        "## Frame-by-Frame Production Script",
        "",
        "| Frame | Timing | On-Screen Text (<= 6 words) | Voice-Over (English) | Voice-Over (Tenglish) |",
        "| :---: | :---: | :--- | :--- | :--- |",
    ])

    for f in frames_script:
        f_idx = f["frame_index"]
        timing = f["timing"]
        ost = f["on_screen_text"].replace("|", "/")
        vo_en = f["voice_over_en"].replace("|", "/")
        vo_te = f["voice_over_te"].replace("|", "/")
        lines.append(f"| Frame {f_idx} | {timing} | **{ost}** | {vo_en} | {vo_te} |")

    lines.extend([
        "",
        "## Owner Checklist (Manual Upload)",
        "",
        "- [ ] Select one title from the 3 options above",
        "- [ ] Copy description and 5 clean hashtags",
        "- [ ] Record voice-over in English or Tenglish following frame timing",
        "- [ ] Set video visibility and link in channel bio (manual upload only)",
        "",
    ])

    return "\n".join(lines).strip() + "\n"


def generate_shorts_script_for_pack(
    pack_dir: Path,
    marketing_dir: Optional[Path] = None,
) -> Path:
    """Generate shorts.md for a pack having reel frames.

    Target destination: exported_content/marketing/<pack>/reel/shorts.md
    Also creates exported_content/marketing/<pack>/shorts.md as a pointer/duplicate.
    """
    pack_name = pack_dir.name
    if marketing_dir is None:
        target_dir = Path("exported_content") / "marketing" / pack_name / "reel"
        mkt_pack_dir = Path("exported_content") / "marketing" / pack_name
    else:
        target_dir = Path(marketing_dir) / pack_name / "reel"
        mkt_pack_dir = Path(marketing_dir) / pack_name

    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Inspect existing reel frames
    frame_files = sorted([f.name for f in target_dir.glob("frame-*.png")])
    if not frame_files:
        # Default to 8 frames if not rendered yet
        frame_files = [f"frame-{i}.png" for i in range(1, 9)]

    # 2. Parse listing facts
    listing_path = pack_dir / "listing.txt"
    if not listing_path.is_file():
        listing_path = pack_dir / "listing.md"
    listing_info = parse_listing_file(listing_path)

    title = listing_info.get("title") or pack_name.replace("-", " ").title()
    description = listing_info.get("description") or ""
    tags = listing_info.get("tags") or []
    benefits = listing_info.get("benefits") or []

    # 3. Generate components
    title_options = generate_title_options(title, pack_name)
    hashtags = clean_hashtags(tags, 5)
    shorts_desc = generate_description(title, description, benefits, hashtags)
    frames_script = build_frame_scripts(frame_files, title, pack_name, benefits, description)

    md_content = render_shorts_markdown(
        pack_name,
        title_options,
        shorts_desc,
        hashtags,
        frames_script,
    )

    # 4. Atomic write to reel/shorts.md
    out_file = target_dir / "shorts.md"
    temp_file = target_dir / f".shorts-{os.getpid()}.tmp"
    try:
        temp_file.write_text(md_content, encoding="utf-8")
        temp_file.replace(out_file)
    except Exception:
        if temp_file.is_file():
            temp_file.unlink(missing_ok=True)
        raise

    # Also atomically write to marketing/<pack>/shorts.md for easy discovery
    pack_level_out = mkt_pack_dir / "shorts.md"
    temp_pack_file = mkt_pack_dir / f".shorts-{os.getpid()}.tmp"
    try:
        temp_pack_file.write_text(md_content, encoding="utf-8")
        temp_pack_file.replace(pack_level_out)
    except Exception:
        if temp_pack_file.is_file():
            temp_pack_file.unlink(missing_ok=True)
        # Not fatal if pack level fails

    return out_file


def discover_packs_with_reels(
    products_dir: Path,
    marketing_dir: Path,
) -> List[Tuple[Path, Path]]:
    """Find packs in products_dir that have a reel directory in marketing_dir."""
    upload_dir = products_dir / "upload" if (products_dir / "upload").is_dir() else products_dir
    if not upload_dir.is_dir():
        return []

    matched = []
    for pack in sorted(upload_dir.iterdir()):
        if not pack.is_dir() or pack.name.startswith("."):
            continue
        reel_dir = marketing_dir / pack.name / "reel"
        # Check if reel directory exists or if pack is ready
        if reel_dir.is_dir():
            matched.append((pack, reel_dir))
    return matched


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", help="Specific pack ID to generate shorts script for.")
    parser.add_argument("--all", action="store_true", help="Generate scripts for all packs with reel frames.")
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
        out = generate_shorts_script_for_pack(pack_path, mkt_dir)
        print(f"Shorts script created for pack {args.pack} -> {out}")
        return 0

    if args.all:
        matched = discover_packs_with_reels(prod_dir, mkt_dir)
        if not matched:
            # If no existing reel folders found, look for all ready upload packs in prod_dir
            upload_dir = prod_dir / "upload" if (prod_dir / "upload").is_dir() else prod_dir
            if upload_dir.is_dir():
                packs = [p for p in sorted(upload_dir.iterdir()) if p.is_dir() and not p.name.startswith(".")]
            else:
                packs = []
            if not packs:
                print(f"No upload packs found in {prod_dir}.", file=sys.stderr)
                return 0
            count = 0
            for p in packs:
                out = generate_shorts_script_for_pack(p, mkt_dir)
                print(f"Shorts script created for pack {p.name} -> {out}")
                count += 1
            print(f"Successfully generated {count} shorts scripts.")
            return 0

        count = 0
        for pack_path, _ in matched:
            out = generate_shorts_script_for_pack(pack_path, mkt_dir)
            print(f"Shorts script created for pack {pack_path.name} -> {out}")
            count += 1
        print(f"Successfully generated {count} shorts scripts.")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
