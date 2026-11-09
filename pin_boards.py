#!/usr/bin/env python3
"""pin_boards.py — Pinterest Board Planning engine.

Proposes 5–8 keyword-first Pinterest boards based on:
- All ready packs' tags and listing metadata
- Keyword bank output (exported_content/marketing/keywords.json if present)
- Existing pins from exported_content/marketing/<pack>/pins/pins.json (or pack pins)

Outputs:
- exported_content/marketing/boards.json
- exported_content/marketing/boards.md

Rules & Constraints:
- 5 to 8 boards proposed.
- Board names: keyword-first, <= 50 characters, no brand names, title-cased or clean.
- Board descriptions: 160–500 characters each.
- Pin assignment: each pin is assigned to 1 or 2 boards (never 0, never > 2).
- Owner guidance in Tenglish house style (Telugu sentence words + English nouns).
- Strictly no money amounts anywhere ($ / USD / INR / etc.), also not in docs.
- No brand logos/names in generated content.
- Nothing posts, publishes, or emails automatically.
- Outputs go OUTSIDE the private pack folders into exported_content/marketing/...
- Atomic writes via temporary files.
- Deterministic outputs given the same inputs.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

# Only Pillow is installed in CI: guard any external imports
try:
    from PIL import Image  # noqa: F401
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

# Brand words to strictly filter out
BRAND_NAMES: Set[str] = {
    "canva", "pinterest", "amazon", "goodnotes", "google", "youtube",
    "microsoft", "etsy", "twitter", "gumroad", "excel", "tiktok",
    "linkedin", "topmate", "apple", "notion", "facebook", "instagram",
    "shopify", "payhip", "ipad", "powerpoint", "word", "chatgpt", "openai",
}

BRAND_REGEX = re.compile(
    r"\b(canva|pinterest|amazon|goodnotes|google(?:\s+sheets?|\s+docs?)?|youtube|"
    r"microsoft(?:\s+excel|\s+word)?|etsy|twitter|gumroad|excel|tiktok|"
    r"linkedin|topmate|apple(?:\s+numbers)?|notion|facebook|instagram|"
    r"shopify|payhip|ipad|powerpoint|word|chatgpt|openai)\b",
    re.IGNORECASE,
)

# Currency & money words and patterns
CURRENCY_WORDS: Set[str] = {
    "usd", "inr", "eur", "gbp", "aud", "cad", "nzd", "sgd", "hkd", "jpy", "cny",
    "rmb", "aed", "chf", "zar", "pkr", "bdt", "ngn", "php", "krw", "rub", "brl",
    "mxn", "rs", "rupee", "rupees", "dollar", "dollars", "euro", "euros", "pound",
    "pounds", "yen", "cent", "cents", "paise", "paisa", "price", "prices", "pricing",
    "free", "cost", "sale", "discount",
}

MONEY_REGEX = re.compile(
    r"(?:\$|₹|€|£|¥|\b(?:USD|INR|EUR|GBP|AUD|CAD|NZD|SGD|HKD|JPY|CNY|RMB|AED|CHF|ZAR|PKR|BDT|NGN|PHP|KRW|RUB|BRL|MXN|Rs\.?|rupees?|dollars?|euros?|pounds?|yen|cents?|paise|paisa)\b)\s*\d|"
    r"\d[\d,.]*\s*(?:\$|₹|€|£|¥|\b(?:USD|INR|EUR|GBP|AUD|CAD|NZD|SGD|HKD|JPY|CNY|RMB|AED|CHF|ZAR|PKR|BDT|NGN|PHP|KRW|RUB|BRL|MXN|Rs\.?|rupees?|dollars?|euros?|pounds?|yen|cents?|paise|paisa)\b)",
    re.IGNORECASE,
)

# Core fallback board categories (keyword-first, <= 50 chars, no brands, no money)
DEFAULT_BOARD_THEMES = [
    {
        "slug": "daily-planners-printable",
        "name": "Daily Planners Printable & Routine Templates",
        "core_keywords": ["daily planner", "routine templates", "printable planner", "day planner"],
        "description": (
            "Explore printable daily planners, daily focus sheets, and flexible routine templates. "
            "Simple minimalist page layouts designed to organize busy days, prioritize top tasks, "
            "and build calm productive daily habits. Perfect for home printing and instant download."
        ),
    },
    {
        "slug": "adhd-productivity-tools",
        "name": "ADHD Productivity Tools & Focus Trackers",
        "core_keywords": ["adhd planner", "focus sheets", "time blindness", "executive function", "energy planner"],
        "description": (
            "Thoughtful printable tools for ADHD minds, focus reset sheets, and energy-based planners. "
            "Plan your day by energy levels and clear priorities rather than rigid schedules. "
            "Clear brain dumps, micro-step project breakdown pages, and low-energy reset routines."
        ),
    },
    {
        "slug": "habit-trackers-printables",
        "name": "Habit Trackers & Goal Setting Printables",
        "core_keywords": ["habit tracker", "goal tracker", "weekly tracker", "monthly habit", "progress log"],
        "description": (
            "Minimalist habit trackers, monthly goal templates, and personal milestone progress logs. "
            "Stay consistent with clean printable sheets for daily routines, wellness habits, and "
            "weekly reflections. Undated templates ready for instant home printing."
        ),
    },
    {
        "slug": "weekly-overview-planners",
        "name": "Weekly Overview Planners & Schedule Sheets",
        "core_keywords": ["weekly planner", "weekly overview", "schedule sheet", "weekly reset", "capacity planner"],
        "description": (
            "Undated weekly planner pages, weekly capacity overview templates, and weekend reset sheets. "
            "Organize your family schedule, work priorities, and project milestones cleanly with "
            "easy-to-read printable layouts. Print as many copies as needed."
        ),
    },
    {
        "slug": "worksheets-organizers-clean",
        "name": "Printable Worksheets & Organization Templates",
        "core_keywords": ["printable worksheet", "organizer template", "checklist printable", "brain dump", "planning sheet"],
        "description": (
            "Clean printable worksheets, functional checklists, and personal life organizers. "
            "Designed for practical everyday productivity, brainstorming sessions, task breakdown, "
            "and mindful planning without complex setup or digital overwhelm."
        ),
    },
    {
        "slug": "minimalist-lifestyle-binders",
        "name": "Minimalist Lifestyle Planners & Home Printables",
        "core_keywords": ["minimalist planner", "lifestyle binder", "home organizer", "printable guide", "wellness sheet"],
        "description": (
            "Minimalist printable planner sheets, life binders, and home organization templates. "
            "Clean monochrome designs that save printer ink and look timeless on paper. "
            "Thoughtfully structured to cultivate calm and mindful living every single week."
        ),
    },
    {
        "slug": "student-study-checklists",
        "name": "Study Planners & Academic Checklist Sheets",
        "core_keywords": ["study planner", "exam checklist", "student worksheet", "academic tracker", "revision sheet"],
        "description": (
            "Functional study planners, exam preparation trackers, and revision checklists. "
            "Keep coursework organized, break down complex study modules into micro-steps, "
            "and track revision milestones with high-contrast, easily printable pages."
        ),
    },
    {
        "slug": "budget-finance-printables",
        "name": "Expense Trackers & Household Planning Logs",
        "core_keywords": ["expense tracker", "household organizer", "savings tracker", "spending log", "bill checklist"],
        "description": (
            "Simple undated expense tracking sheets, savings goal charts, and household planning logs. "
            "Track daily expenses and monthly household commitments with clean paper templates. "
            "Practical worksheets crafted for clear and peaceful personal organization."
        ),
    },
]


def clean_text_no_brands_or_money(text: str) -> str:
    """Remove brand words, currency terms, and excessive spaces."""
    if not text:
        return ""
    # Strip money patterns first
    text = MONEY_REGEX.sub("", text)
    # Strip brand regex
    text = BRAND_REGEX.sub("", text)
    # Clean words
    words = text.split()
    clean_words = []
    for w in words:
        clean_w = re.sub(r"[^\w\s-]", "", w).strip()
        low_w = clean_w.lower()
        if low_w in BRAND_NAMES or low_w in CURRENCY_WORDS:
            continue
        clean_words.append(w)
    result = " ".join(clean_words)
    result = re.sub(r"\s+", " ", result).strip()
    return result


def is_safe_text(text: str) -> bool:
    """Verify that text contains no brand names and no money patterns."""
    if not text:
        return True
    if MONEY_REGEX.search(text):
        return False
    if BRAND_REGEX.search(text):
        return False
    lower = text.lower()
    for b in BRAND_NAMES:
        if re.search(rf"\b{re.escape(b)}\b", lower):
            return False
    for c in CURRENCY_WORDS:
        # Standalone currency words check
        if re.search(rf"\b{re.escape(c)}\b", lower):
            return False
    return True


def discover_ready_packs(products_dir: Path | str = "exported_content/products") -> List[Path]:
    """Find all ready upload packs in products_dir."""
    p_dir = Path(products_dir)
    upload_dirs: List[Path] = []
    candidates = [p_dir / "upload", p_dir]
    for cand in candidates:
        if cand.is_dir():
            for sub in sorted(cand.iterdir()):
                if sub.is_dir() and ((sub / "listing.txt").is_file() or (sub / "listing.md").is_file()):
                    if sub not in upload_dirs:
                        upload_dirs.append(sub)
    return upload_dirs


def parse_pack_listing(pack_dir: Path) -> Dict[str, Any]:
    """Extract metadata (title, tags, description) from pack's listing file."""
    listing_file = pack_dir / "listing.txt"
    if not listing_file.is_file():
        listing_file = pack_dir / "listing.md"

    title = ""
    tags: List[str] = []
    description = ""

    if listing_file.is_file():
        try:
            content = listing_file.read_text(encoding="utf-8-sig", errors="ignore")
            lines = content.splitlines()
            in_tags = False
            in_desc = False
            desc_lines: List[str] = []

            for line in lines:
                sline = line.strip()
                lower = sline.lower()

                if lower.startswith("# ") or lower.startswith("title:"):
                    title = clean_text_no_brands_or_money(line.split(":", 1)[-1].lstrip("#").strip())
                    in_tags = False
                    in_desc = False
                elif lower.startswith("## tags") or lower.startswith("# tags") or lower == "tags:":
                    in_tags = True
                    in_desc = False
                elif lower.startswith("tags:") or lower.startswith("tag:"):
                    tag_part = line.split(":", 1)[1]
                    for t in tag_part.split(","):
                        cleaned = clean_text_no_brands_or_money(t.strip())
                        if cleaned and cleaned not in tags:
                            tags.append(cleaned)
                    in_tags = False
                elif lower.startswith("## description") or lower.startswith("# description") or lower == "description:":
                    in_desc = True
                    in_tags = False
                elif lower.startswith("#") or (":" in sline and not in_desc):
                    in_tags = False
                    if in_desc:
                        in_desc = False
                elif in_tags:
                    for t in sline.split(","):
                        cleaned = clean_text_no_brands_or_money(t.strip())
                        if cleaned and cleaned not in tags:
                            tags.append(cleaned)
                elif in_desc:
                    desc_lines.append(sline)

            if desc_lines:
                description = clean_text_no_brands_or_money(" ".join(desc_lines))
        except Exception:
            pass

    return {
        "pack_name": pack_dir.name,
        "title": title or pack_dir.name.replace("-", " ").title(),
        "tags": tags,
        "description": description,
    }


def load_keywords_bank(keywords_path: Path | str = "exported_content/marketing/keywords.json") -> List[str]:
    """Load top ranked keywords from keyword bank if available."""
    k_path = Path(keywords_path)
    if not k_path.is_file():
        return []
    try:
        data = json.loads(k_path.read_text(encoding="utf-8"))
        keywords_list = data.get("keywords", [])
        clean_kw: List[str] = []
        for item in keywords_list:
            kw = item.get("keyword") if isinstance(item, dict) else str(item)
            cleaned = clean_text_no_brands_or_money(kw)
            if cleaned and is_safe_text(cleaned):
                clean_kw.append(cleaned)
        return clean_kw
    except Exception:
        return []


def discover_pack_pins(
    pack_name: str,
    marketing_dir: Path | str = "exported_content/marketing",
    pack_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Discover pins for a given pack from marketing pins.json or fallback synthetic list."""
    m_pins_file = Path(marketing_dir) / pack_name / "pins" / "pins.json"
    pins: List[Dict[str, Any]] = []

    if m_pins_file.is_file():
        try:
            raw_pins = json.loads(m_pins_file.read_text(encoding="utf-8"))
            if isinstance(raw_pins, list):
                for p in raw_pins:
                    pin_id = f"{pack_name}-{p.get('file', 'pin.png')}"
                    pins.append({
                        "id": pin_id,
                        "pack": pack_name,
                        "file": p.get("file", "pin.png"),
                        "title": clean_text_no_brands_or_money(p.get("title", f"Pin for {pack_name}")),
                        "description": clean_text_no_brands_or_money(p.get("description", "")),
                        "layout": p.get("layout", "A"),
                        "alt": clean_text_no_brands_or_money(p.get("alt", "")),
                    })
        except Exception:
            pins = []

    if not pins and pack_dir:
        # Check inside pack folder pins/pins.json if present
        pack_pins_file = pack_dir / "pins" / "pins.json"
        if pack_pins_file.is_file():
            try:
                raw_pins = json.loads(pack_pins_file.read_text(encoding="utf-8"))
                if isinstance(raw_pins, list):
                    for p in raw_pins:
                        pin_id = f"{pack_name}-{p.get('file', 'pin.png')}"
                        pins.append({
                            "id": pin_id,
                            "pack": pack_name,
                            "file": p.get("file", "pin.png"),
                            "title": clean_text_no_brands_or_money(p.get("title", f"Pin for {pack_name}")),
                            "description": clean_text_no_brands_or_money(p.get("description", "")),
                            "layout": p.get("layout", "A"),
                            "alt": clean_text_no_brands_or_money(p.get("alt", "")),
                        })
            except Exception:
                pins = []

    # If still no pins found, generate deterministic default 5 pins for the pack
    if not pins:
        layouts = ["A", "B", "C", "D", "E"]
        clean_name = pack_name.replace("-", " ").title()
        for i, layout in enumerate(layouts, 1):
            pins.append({
                "id": f"{pack_name}-pin-{i}.png",
                "pack": pack_name,
                "file": f"pin-{i}.png",
                "title": f"Printable {clean_name} Template {i}",
                "description": f"Download and print the {clean_name} template. Clean paper layout for daily planning and focus.",
                "layout": layout,
                "alt": f"Printable {clean_name} layout preview",
            })

    return pins


def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower()).strip()
    return re.sub(r"[\s_-]+", "-", slug)


def build_board_plan(
    products_dir: Path | str = "exported_content/products",
    marketing_dir: Path | str = "exported_content/marketing",
    keywords_path: Path | str = "exported_content/marketing/keywords.json",
    min_boards: int = 5,
    max_boards: int = 8,
) -> Dict[str, Any]:
    """Build Pinterest board plan with 5-8 boards, mapped pins, and metadata."""
    p_dir = Path(products_dir)
    m_dir = Path(marketing_dir)
    pack_dirs = discover_ready_packs(p_dir)

    # Collect tags, keywords, and pins from all packs
    packs_meta: List[Dict[str, Any]] = []
    all_pins: List[Dict[str, Any]] = []
    tag_counts: Counter[str] = Counter()

    for p_dir_path in pack_dirs:
        meta = parse_pack_listing(p_dir_path)
        packs_meta.append(meta)
        for t in meta["tags"]:
            tag_counts[t.lower()] += 1
        pack_pins = discover_pack_pins(meta["pack_name"], marketing_dir=m_dir, pack_dir=p_dir_path)
        all_pins.extend(pack_pins)

    # Keyword bank
    kw_bank = load_keywords_bank(keywords_path)

    # Score each default board theme against pack tags, keywords, and titles
    scored_themes: List[Tuple[float, Dict[str, Any]]] = []
    for theme in DEFAULT_BOARD_THEMES:
        score = 0.0
        c_kws = theme["core_keywords"]
        for ck in c_kws:
            ck_low = ck.lower()
            # check tags
            for t, count in tag_counts.items():
                if ck_low in t or t in ck_low:
                    score += 2.0 * count
            # check kw_bank
            for kw in kw_bank[:50]:
                if ck_low in kw.lower() or kw.lower() in ck_low:
                    score += 1.5
            # check pack titles
            for p in packs_meta:
                if ck_low in p["title"].lower():
                    score += 3.0

        # Base score ensures stable ordering
        scored_themes.append((score, theme))

    # Sort descending by score, tie-break by theme slug
    scored_themes.sort(key=lambda x: (x[0], -len(x[1]["name"]), x[1]["slug"]), reverse=True)

    # We need strictly between 5 and 8 boards
    target_count = min(max_boards, max(min_boards, 6))
    if len(pack_dirs) >= 4:
        target_count = min(max_boards, max(min_boards, 7))

    selected_themes = [item[1] for item in scored_themes[:target_count]]

    # Ensure boards satisfy all constraints:
    # 1. Keyword first
    # 2. <= 50 chars
    # 3. No brand names
    # 4. Description between 160 and 500 chars
    # 5. Clean Tenglish note
    boards: List[Dict[str, Any]] = []
    for t in selected_themes:
        name = clean_text_no_brands_or_money(t["name"])
        if len(name) > 50:
            name = name[:50].rsplit(" ", 1)[0].strip()

        desc = clean_text_no_brands_or_money(t["description"])
        # Ensure description is between 160 and 500 characters
        if len(desc) < 160:
            desc = (
                f"{desc} Undated printable templates created for mindful productivity, "
                "daily routine tracking, and practical desk organization. Instant download."
            )
        if len(desc) > 500:
            desc = desc[:497].rsplit(" ", 1)[0].strip() + "..."

        boards.append({
            "id": t["slug"],
            "name": name,
            "description": desc,
            "keywords": t["core_keywords"],
            "pins": [],  # will be populated next
        })

    # Pin assignment: each pin must be in 1 or 2 boards (never 0, never > 2)
    # Match pins to boards by keyword relevance
    pin_to_boards: Dict[str, List[int]] = defaultdict(list)

    for pin in all_pins:
        p_text = f"{pin['title']} {pin['description']} {pin['pack']}".lower()
        board_scores: List[Tuple[float, int]] = []

        for idx, b in enumerate(boards):
            match_score = 0.0
            for kw in b["keywords"]:
                if kw.lower() in p_text:
                    match_score += 3.0
                else:
                    for kw_word in kw.lower().split():
                        if kw_word in p_text:
                            match_score += 1.0
            # Tie breaker based on pin hash + board idx for determinism
            h = int(hashlib.md5(f"{pin['id']}_{b['id']}".encode("utf-8")).hexdigest(), 16) % 100
            score_total = match_score * 100 + h
            board_scores.append((score_total, idx))

        board_scores.sort(key=lambda x: x[0], reverse=True)

        primary_idx = board_scores[0][1]
        assigned_indices = [primary_idx]

        sec_score, sec_idx = board_scores[1]
        pin_hash = int(hashlib.md5(pin["id"].encode("utf-8")).hexdigest(), 16)
        if (sec_score >= 150 or pin_hash % 2 == 0) and sec_idx != primary_idx:
            assigned_indices.append(sec_idx)

        # Strictly 1 or 2 boards
        assigned_indices = assigned_indices[:2]
        pin_to_boards[pin["id"]] = assigned_indices

    # Distribute pins to boards
    pin_by_id = {p["id"]: p for p in all_pins}
    for pin_id, b_indices in pin_to_boards.items():
        pin = pin_by_id[pin_id]
        pin_entry = {
            "pin_id": pin["id"],
            "pack": pin["pack"],
            "file": pin["file"],
            "title": pin["title"],
            "layout": pin["layout"],
        }
        for b_idx in b_indices:
            boards[b_idx]["pins"].append(pin_entry)

    # Owner guidance in Tenglish house style
    total_pins_assigned = sum(len(b["pins"]) for b in boards)
    unique_pins_count = len(all_pins)
    tenglish_summary = (
        f"Pinterest board plan ready ga undi: mottham {len(boards)} boards create cheyandi. "
        f"{unique_pins_count} pins ni {total_pins_assigned} slots lo neat ga map chesam. "
        "Each pin 1 leda 2 boards lo post avtundi. Manual ga Pinterest lo create chesi save cheyandi."
    )

    result = {
        "boards_count": len(boards),
        "total_unique_pins": unique_pins_count,
        "total_pin_assignments": total_pins_assigned,
        "tenglish_summary": tenglish_summary,
        "owner_instructions_tenglish": [
            "1. Pinterest account lo login ayyi ee 5-8 boards ni create cheyandi.",
            "2. Board peru mariyu 160-500 char description ni copy chesi paste cheyandi.",
            "3. Prati pack pins ni mapped boards lo upload chesi link add cheyandi.",
            "4. Brand logos, unauthorized names leda direct currency words lekunda verify chesam.",
            "5. Robot automated ga post cheyadu; completely owner manual posting plan idi.",
        ],
        "boards": boards,
    }

    return result


def generate_boards_markdown(plan_data: Dict[str, Any]) -> str:
    """Format board plan into clean Markdown (boards.md) for the owner."""
    lines: List[str] = [
        "# Pinterest Board Strategy & Pin Allocation Plan",
        "",
        "> **Owner Note (Tenglish):**",
        f"> {plan_data['tenglish_summary']}",
        "",
        "## Board Overview",
        "",
        "| # | Board Name | Character Count | Target Keywords | Assigned Pins |",
        "|---|---|---|---|---|",
    ]

    for i, b in enumerate(plan_data["boards"], 1):
        kws_str = ", ".join(b["keywords"][:3])
        lines.append(f"| {i} | {b['name']} | {len(b['name'])} chars | {kws_str} | {len(b['pins'])} pins |")

    lines.extend([
        "",
        "## Detailed Board Plans & Copy",
        "",
    ])

    for i, b in enumerate(plan_data["boards"], 1):
        lines.extend([
            f"### {i}. {b['name']}",
            "",
            f"- **Board Slug:** `{b['id']}`",
            f"- **Title Length:** {len(b['name'])} characters (Limit: 50)",
            f"- **Description Length:** {len(b['description'])} characters (Limit: 160–500)",
            f"- **SEO Focus Keywords:** {', '.join(b['keywords'])}",
            "",
            "#### Board Description (Ready to Copy)",
            "",
            "```text",
            b["description"],
            "```",
            "",
            "#### Assigned Pins",
            "",
        ])

        if b["pins"]:
            for pin in b["pins"]:
                lines.append(f"- **[{pin['pack']}]** `{pin['file']}` (Layout {pin['layout']}): {pin['title']}")
        else:
            lines.append("- *(No pins currently assigned)*")

        lines.append("")

    lines.extend([
        "## Owner Instructions (Tenglish House Style)",
        "",
    ])
    for step in plan_data["owner_instructions_tenglish"]:
        lines.append(f"- {step}")

    lines.extend([
        "",
        "---",
        "*Safety Note: Generated strictly offline without auto-posting or publishing. No money amounts or brand logos included.*",
        "",
    ])

    return "\n".join(lines)


def write_board_plan(
    products_dir: Path | str = "exported_content/products",
    marketing_dir: Path | str = "exported_content/marketing",
    keywords_path: Path | str = "exported_content/marketing/keywords.json",
    output_json: Path | str = "exported_content/marketing/boards.json",
    output_md: Path | str = "exported_content/marketing/boards.md",
) -> Tuple[Path, Path]:
    """Generate board plan and atomically write boards.json and boards.md."""
    plan_data = build_board_plan(
        products_dir=products_dir,
        marketing_dir=marketing_dir,
        keywords_path=keywords_path,
    )

    md_content = generate_boards_markdown(plan_data)
    json_content = json.dumps(plan_data, indent=2, ensure_ascii=False)

    out_json = Path(output_json)
    out_md = Path(output_md)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write for JSON
    t_json = tempfile.NamedTemporaryFile("w", dir=out_json.parent, delete=False, encoding="utf-8")
    try:
        t_json.write(json_content)
        t_json.flush()
        os.fsync(t_json.fileno())
        t_json.close()
        os.replace(t_json.name, out_json)
    except Exception:
        if os.path.exists(t_json.name):
            os.remove(t_json.name)
        raise

    # Atomic write for Markdown
    t_md = tempfile.NamedTemporaryFile("w", dir=out_md.parent, delete=False, encoding="utf-8")
    try:
        t_md.write(md_content)
        t_md.flush()
        os.fsync(t_md.fileno())
        t_md.close()
        os.replace(t_md.name, out_md)
    except Exception:
        if os.path.exists(t_md.name):
            os.remove(t_md.name)
        raise

    return out_json, out_md


def main() -> None:
    parser = argparse.ArgumentParser(description="Propose 5-8 keyword-first Pinterest boards and pin allocation.")
    parser.add_argument("--products-dir", default="exported_content/products", help="Products directory path")
    parser.add_argument("--marketing-dir", default="exported_content/marketing", help="Marketing directory path")
    parser.add_argument("--keywords-path", default="exported_content/marketing/keywords.json", help="Keywords bank path")
    parser.add_argument("--output-json", default="exported_content/marketing/boards.json", help="Output boards.json path")
    parser.add_argument("--output-md", default="exported_content/marketing/boards.md", help="Output boards.md path")

    args = parser.parse_args()

    out_json, out_md = write_board_plan(
        products_dir=args.products_dir,
        marketing_dir=args.marketing_dir,
        keywords_path=args.keywords_path,
        output_json=args.output_json,
        output_md=args.output_md,
    )

    print(f"Board plan JSON saved: {out_json}")
    print(f"Board plan Markdown saved: {out_md}")


if __name__ == "__main__":
    main()
