"""30-day post planner + 'What to post today' for the owner.

The owner posts by hand on Pinterest and Instagram. The robot tells him
exactly what to post today, and remembers what he posted.
Nothing is posted automatically.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union

# Known brand names to exclude from hashtags
BRAND_NAMES = {
    "gumroad", "topmate", "etsy", "notion", "pinterest", "instagram", "goodnotes",
    "excel", "canva", "facebook", "twitter", "tiktok", "youtube", "linkedin",
    "shopify", "amazon", "apple", "google", "microsoft",
}

# Currency words / symbols to exclude from hashtags and captions
CURRENCY_WORDS = {
    "usd", "inr", "eur", "gbp", "aud", "cad", "nzd", "sgd", "hkd", "jpy", "cny",
    "rmb", "aed", "chf", "zar", "pkr", "bdt", "ngn", "php", "krw", "rub", "brl",
    "mxn", "rs", "rupee", "rupees", "dollar", "dollars", "euro", "euros", "pound",
    "pounds", "yen", "cent", "cents", "paise", "paisa", "price", "free", "cost", "sale",
}

_MONEY_PATTERN = re.compile(
    r"(?:\$|₹|€|£|¥|\b(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?)\s*\d+|\d+\s*(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?))",
    re.I
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
PLAN_PATH = os.path.join("exported_content", "post_plan.json")


def clean_caption(title: str, max_chars: int = 100) -> str:
    """Extract and cut caption from title at a word boundary <= max_chars.

    Also ensures no money patterns leak.
    """
    if not title:
        return ""
    # Title first line
    line = title.splitlines()[0].strip()
    # Strip any leading markdown like # or Title: repeatedly
    while True:
        stripped = re.sub(r"^(?:#+\s*|Title:\s*|[*_`~]+)", "", line, flags=re.I).strip()
        if stripped == line:
            break
        line = stripped
    # Remove any money pattern
    line = _MONEY_PATTERN.sub("", line).strip()

    if len(line) <= max_chars:
        return line

    # Cut at word boundary <= max_chars
    truncated = line[:max_chars]
    # Find last space or boundary
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space].rstrip(",;:- ")
    else:
        truncated = truncated.rstrip(",;:- ")
    return truncated


def clean_hashtags(raw_tags: List[str], max_tags: int = 8) -> List[str]:
    """Clean tags: lowercase, no spaces, no brand names, no money, alphanumeric only, max 8."""
    cleaned: List[str] = []
    seen = set()

    for tag in raw_tags:
        # Split on commas or spaces if multi-word, or remove spaces to form single tag
        # The prompt says: "lowercase, no spaces, no brand names, no money"
        tag_str = str(tag).strip().lower()
        if not tag_str:
            continue
        # Split tag if it contains commas
        parts = [p.strip() for p in tag_str.split(",") if p.strip()]
        for part in parts:
            # Check if part contains money
            if _MONEY_PATTERN.search(part):
                continue
            # Remove symbols/hashtags at front
            norm = re.sub(r"^#+", "", part).strip()
            # Remove internal whitespace to make a hashtag: "calm routine" -> "calmroutine"
            # Or if words separated by space, check each word against brands/currency
            words = norm.split()
            if any(w in BRAND_NAMES or w in CURRENCY_WORDS for w in words):
                continue
            tag_slug = "".join(re.findall(r"[a-z0-9]+", norm))
            if not tag_slug:
                continue
            if tag_slug in BRAND_NAMES or tag_slug in CURRENCY_WORDS:
                continue
            if tag_slug in seen:
                continue
            seen.add(tag_slug)
            cleaned.append(tag_slug)
            if len(cleaned) >= max_tags:
                return cleaned

    return cleaned


def _read_pack_info(pack_dir: Path) -> Optional[Dict[str, Any]]:
    """Inspect an upload pack directory and extract title, tags, and image assets."""
    if not pack_dir.is_dir():
        return None

    pack_id = pack_dir.name
    listing_path = pack_dir / "listing.txt"
    title = pack_id
    raw_tags: List[str] = []

    if listing_path.is_file():
        try:
            content = listing_path.read_text(encoding="utf-8-sig")
            for line in content.splitlines():
                line_str = line.strip()
                if line_str.lower().startswith("title:"):
                    title = line_str.split(":", 1)[1].strip()
                elif line_str.lower().startswith("tags:"):
                    tag_part = line_str.split(":", 1)[1].strip()
                    raw_tags = [t.strip() for t in tag_part.split(",") if t.strip()]
        except Exception:
            pass

    caption_short = clean_caption(title, 100)
    hashtags = clean_hashtags(raw_tags, 8)

    # Collect image assets (relative to pack directory)
    # Check for cover.png and pin images (e.g. pin_*.png, pin*.png, or pins/ folder)
    assets: List[str] = []

    # 1. Top-level images in pack_dir
    top_images = sorted([
        f.name for f in pack_dir.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    ])

    # 2. Check for pins/ subdirectory if present
    pins_dir = pack_dir / "pins"
    pin_images = []
    if pins_dir.is_dir():
        pin_images = sorted([
            f"pins/{f.name}" for f in pins_dir.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        ])

    # Prioritize pins and cover
    # e.g., pin_*.png, then cover.png, then any other images
    pin_candidates = [im for im in top_images if "pin" in im.lower()] + pin_images
    cover_candidates = [im for im in top_images if "cover" in im.lower()]
    other_candidates = [im for im in top_images if im not in pin_candidates and im not in cover_candidates]

    ordered_assets = pin_candidates + cover_candidates + other_candidates
    # If no images found, fallback to "cover.png"
    if not ordered_assets:
        ordered_assets = ["cover.png"]

    return {
        "pack_id": pack_id,
        "title": title,
        "caption_short": caption_short,
        "hashtags": hashtags,
        "assets": ordered_assets,
    }


def _discover_packs(products_dir: Union[str, Path]) -> List[Dict[str, Any]]:
    """Find ready packs from exported_content/products/upload/<pack>/ or directly under products_dir."""
    base = Path(products_dir).expanduser().resolve()
    upload_dir = base / "upload" if (base / "upload").is_dir() else base

    if not upload_dir.is_dir():
        return []

    pack_dirs = sorted([p for p in upload_dir.iterdir() if p.is_dir() and not p.name.startswith(".")])

    packs: List[Dict[str, Any]] = []
    for pdir in pack_dirs:
        info = _read_pack_info(pdir)
        if info:
            packs.append(info)
    return packs


def build_plan(
    products_dir: Union[str, Path] = "exported_content/products",
    start_date: Optional[Union[datetime.date, str]] = None,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """Build a 30-day posting plan.

    Rules:
    - max 3 pins + 1 Instagram post per day
    - rotate products so the same product is not shown 2 days in a row (if >= 2 products exist)
    - spread every ready pack at least 2 times in 30 days
    - captions from listing.txt (title first line, cut at 100 chars at a word)
    - hashtags from Tags (lowercase, no spaces, no brand names, no money, max 8)
    - status ("todo")
    """
    if start_date is None:
        cur_date = datetime.date.today()
    elif isinstance(start_date, str):
        cur_date = datetime.date.fromisoformat(start_date)
    else:
        cur_date = start_date

    packs = _discover_packs(products_dir)
    if not packs:
        return []

    num_packs = len(packs)
    plan: List[Dict[str, Any]] = []

    # Pre-calculate asset cycling per pack so different assets are used when available
    pack_asset_indices = {p["pack_id"]: 0 for p in packs}

    # Tracking product usage per day to prevent same product 2 days in a row
    # We have up to 4 posts per day: 3 Pinterest pins + 1 Instagram post.
    # To satisfy "rotate products so the same product is not shown 2 days in a row":
    # on day D, products used cannot intersect with products used on day D-1 (if num_packs allows).
    # Also "spread every ready pack at least 2 times in 30 days".

    # We assign packs to each day.
    # If num_packs == 1: cannot avoid 2 days in a row, so use that 1 pack.
    # If num_packs >= 2: we select packs for day D that were NOT used on day D-1.
    # For day D:
    # We need up to 4 slots (3 pins, 1 instagram).
    # If num_packs is e.g. 2, day 1 can use pack 0 for all 4 slots, day 2 uses pack 1 for all 4 slots.
    # If num_packs >= 4, each day can use 1 or more packs, but no pack from day D-1.

    # Let's track pack frequency across the 30 days to ensure every pack is used >= 2 times.
    pack_usage_counts = {p["pack_id"]: 0 for p in packs}

    prev_day_pack_ids: set = set()

    for day_idx in range(1, days + 1):
        day_date = cur_date + datetime.timedelta(days=day_idx - 1)
        day_date_str = day_date.isoformat()

        # Choose packs for this day.
        # Eligible packs: those not in prev_day_pack_ids (if possible).
        eligible = [p for p in packs if p["pack_id"] not in prev_day_pack_ids]
        if not eligible:
            eligible = list(packs)

        # Sort eligible packs prioritizing least used packs so all get >= 2 uses
        eligible.sort(key=lambda p: (pack_usage_counts[p["pack_id"]], packs.index(p)))

        # Assign slots for this day:
        # 3 Pinterest pins, 1 Instagram post = 4 posts per day.
        # Channels: ["pinterest", "pinterest", "pinterest", "instagram"]
        channels = ["pinterest", "pinterest", "pinterest", "instagram"]

        # Pick packs for these 4 slots:
        # If we have multiple eligible packs, we can cycle through them or pick 1 or more.
        # To make it clean and rotate nicely:
        # We can pick a primary pack for the day (or distribute amongst eligible).
        # But if we distribute, say 2 packs on day D, neither can be used on day D+1.
        # If num_packs == 2, if day 1 uses both pack A and B, day 2 has no eligible pack!
        # Therefore, if num_packs < 4:
        # Use single pack per day (or at most min(num_packs // 2, 2) packs per day) so alternation is always clean!
        # Specifically:
        # If num_packs == 1: use pack 0 every day.
        # If num_packs >= 2: pick the eligible pack with minimum usage as primary pack for the day.
        # If num_packs >= 4: we can optionally use 2 packs (e.g. 2 pins pack A, 1 pin + 1 insta pack B),
        # but 1 pack per day (or rotating 1 primary pack) strictly ensures NO product overlap between adjacent days!
        # And with 30 days and 1 pack per day, 30 days >= 2 * num_packs (if num_packs <= 15).
        # What if num_packs > 15?
        # If num_packs > 15, to spread every pack >= 2 times in 30 days:
        # 30 days * 4 posts = 120 posts!
        # If we assign packs per post slot, we can easily satisfy >= 2 uses for up to 60 packs!

        # Let's design the selection algorithm:
        # Day d needs 4 posts.
        # Let candidate set for Day d be C_d subset of packs such that C_d disjoint from C_{d-1}.
        # Max size of C_d can be up to 4 (one per post slot).
        # To guarantee C_d disjoint from C_{d-1}:
        # Number of available packs is num_packs.
        # We can select k distinct packs for day d, where 1 <= k <= min(4, num_packs // 2 if num_packs >= 2 else 1).
        # But wait! If num_packs == 3, num_packs // 2 is 1. Day 1 uses {A}, Day 2 uses {B}, Day 3 uses {C}, Day 4 uses {A}...
        # In fact, we can choose k = 1 if num_packs <= 4;
        # If num_packs > 4, k can be min(2, num_packs // 2), or even up to 4 if num_packs >= 8.
        # But wait! Even simpler and more flexible:
        # If we need every pack >= 2 times in 30 days:
        # If num_packs is e.g. 20, 1 pack per day would only cover 30 packs (some only 1 time).
        # So for num_packs > 14, we definitely want multiple packs per day.
        # Let's determine how many packs we pick for this day:
        # Let target_k = min(4, len(eligible))
        # But wait: if we pick target_k packs today, will there be enough remaining packs for tomorrow?
        # To ensure tomorrow has at least 1 (or target_k) packs not used today:
        # We need: num_packs - target_k >= 1 (or >= target_k).
        # Specifically, max k = max(1, num_packs // 2) if num_packs >= 2 else 1.
        # And k = min(4, max(1, num_packs // 2)).

        k = 1
        if num_packs >= 8:
            k = 4
        elif num_packs >= 4:
            k = 2
        elif num_packs >= 2:
            k = 1
        else:
            k = 1

        selected_for_day = eligible[:k]

        # Update prev_day_pack_ids
        cur_day_pack_ids = {p["pack_id"] for p in selected_for_day}
        prev_day_pack_ids = cur_day_pack_ids

        # Assign packs to the 4 slots:
        # channels = ["pinterest", "pinterest", "pinterest", "instagram"]
        # Slot assignments:
        for slot_idx, channel in enumerate(channels):
            # Select pack for this slot
            pack = selected_for_day[slot_idx % len(selected_for_day)]
            pid = pack["pack_id"]
            pack_usage_counts[pid] += 1

            # Select asset for this pack
            assets = pack["assets"]
            a_idx = pack_asset_indices[pid] % len(assets)
            asset = assets[a_idx]
            pack_asset_indices[pid] += 1

            plan.append({
                "day": day_idx,
                "date": day_date_str,
                "channel": channel,
                "pack_id": pid,
                "asset": asset,
                "caption_short": pack["caption_short"],
                "hashtags": list(pack["hashtags"]),
                "status": "todo",
            })

    return plan


def load_plan(plan_path: Union[str, Path] = PLAN_PATH, products_dir: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Load plan from JSON file. If missing, corrupt, or invalid, rebuilds or returns empty plan."""
    path = Path(plan_path)
    if not path.is_file():
        if products_dir:
            plan = build_plan(products_dir)
            if plan:
                save_plan(plan, plan_path)
            return plan
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("Expected list in JSON")
        # Validate elements
        for item in data:
            if not isinstance(item, dict) or "day" not in item or "date" not in item:
                raise ValueError("Invalid item format")
        return data
    except Exception:
        # Bad JSON -> rebuild or return empty, never crash
        if products_dir:
            plan = build_plan(products_dir)
            if plan:
                save_plan(plan, plan_path)
            return plan
        return []


def save_plan(plan: List[Dict[str, Any]], plan_path: Union[str, Path] = PLAN_PATH) -> bool:
    """Save plan atomically using a temporary file in the destination directory and rename."""
    dest = Path(plan_path).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=dest.parent, delete=False, encoding="utf-8", prefix=".tmp_plan_") as tf:
            temp_file = tf.name
            json.dump(plan, tf, indent=2, ensure_ascii=False)
        os.replace(temp_file, dest)
        return True
    except Exception:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass
        return False


def today_text(plan: List[Dict[str, Any]], date: Optional[Union[datetime.date, str]] = None) -> str:
    """Format short Telegram text in plain words for today's posts (max 1200 chars).

    "Today post (3 pins, 1 Instagram): 1) <pack_id> pin_2.png ..." + "Reply: posted 1"
    No money, no file paths outside the pack, <= 1200 chars.
    """
    if date is None:
        target_date = datetime.date.today().isoformat()
    elif isinstance(date, datetime.date):
        target_date = date.isoformat()
    else:
        target_date = str(date)

    day_posts = [p for p in plan if p.get("date") == target_date]
    if not day_posts:
        return f"No posts scheduled for {target_date}."

    pin_count = sum(1 for p in day_posts if p.get("channel") == "pinterest")
    ig_count = sum(1 for p in day_posts if p.get("channel") == "instagram")

    lines = [f"Today post ({pin_count} pins, {ig_count} Instagram):"]

    for idx, post in enumerate(day_posts, 1):
        status_mark = " [done]" if post.get("status") == "done" else ""
        pack_id = post.get("pack_id", "")
        asset = post.get("asset", "")
        # Strip any directory path outside pack
        clean_asset = Path(asset).name if "/" not in asset and "\\" not in asset else asset
        clean_asset = clean_asset.replace("\\", "/")
        channel = post.get("channel", "")
        caption = post.get("caption_short", "")
        tags = " ".join(f"#{t}" for t in post.get("hashtags", []))

        # Build post description line
        # 1) <pack_id> pin_2.png (pinterest): <caption_short> #tags
        desc = f"{idx}) {pack_id} {clean_asset} ({channel}){status_mark}"
        if caption:
            desc += f": {caption}"
        if tags:
            desc += f" {tags}"

        # Scrub any money pattern
        desc = _MONEY_PATTERN.sub("", desc)
        lines.append(desc)

    lines.append("\nReply: posted 1 (or 'posted 2', 'post chesa 1') to mark done.")

    result = "\n".join(lines).strip()
    # Final check: scrub money, check len <= 1200
    result = _MONEY_PATTERN.sub("", result)
    if len(result) > 1200:
        result = result[:1197] + "..."
    return result


def mark_posted(
    plan: List[Dict[str, Any]],
    date: Optional[Union[datetime.date, str]] = None,
    n: int = 1,
) -> bool:
    """Mark the n-th post (1-indexed) of given date as status='done'."""
    if date is None:
        target_date = datetime.date.today().isoformat()
    elif isinstance(date, datetime.date):
        target_date = date.isoformat()
    else:
        target_date = str(date)

    day_posts = [p for p in plan if p.get("date") == target_date]
    if not day_posts or n < 1 or n > len(day_posts):
        return False

    day_posts[n - 1]["status"] = "done"
    return True


def week_summary(
    plan: List[Dict[str, Any]],
    date: Optional[Union[datetime.date, str]] = None,
) -> str:
    """Return summary for the week (7-day window containing date, or starting from date).

    Format: 'This week: X of Y posts done'
    """
    if not plan:
        return "This week: 0 of 0 posts done"

    if date is None:
        ref_date = datetime.date.today()
    elif isinstance(date, str):
        ref_date = datetime.date.fromisoformat(date)
    else:
        ref_date = date

    # Standard calendar week (Monday to Sunday) containing ref_date
    start_of_week = ref_date - datetime.timedelta(days=ref_date.weekday())
    end_of_week = start_of_week + datetime.timedelta(days=6)

    start_str = start_of_week.isoformat()
    end_str = end_of_week.isoformat()

    week_posts = [p for p in plan if start_str <= p.get("date", "") <= end_str]
    total = len(week_posts)
    done = sum(1 for p in week_posts if p.get("status") == "done")

    return f"This week: {done} of {total} posts done"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="30-day post planner + what to post today.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true", help="Build a new 30-day posting plan.")
    group.add_argument("--today", action="store_true", help="Print today's scheduled posts.")
    group.add_argument("--posted", type=int, metavar="N", help="Mark post N of today as done.")
    group.add_argument("--week", action="store_true", help="Print this week's progress summary.")

    parser.add_argument("--products-dir", default="exported_content/products", help="Products directory.")
    parser.add_argument("--plan-path", default=PLAN_PATH, help="Path to post_plan.json.")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (defaults to today).")

    args = parser.parse_args(argv)

    plan_file = args.plan_path
    prod_dir = args.products_dir
    target_date = args.date or datetime.date.today().isoformat()

    if args.build:
        plan = build_plan(prod_dir, start_date=target_date)
        if not plan:
            print("No upload packs found to build a plan.")
            return 0
        saved = save_plan(plan, plan_file)
        if saved:
            print(f"Created 30-day plan with {len(plan)} posts saved to {plan_file}.")
        else:
            print("Failed to save plan.")
        return 0

    plan = load_plan(plan_file, products_dir=prod_dir)

    if args.today:
        print(today_text(plan, target_date))
        return 0

    if args.posted is not None:
        ok = mark_posted(plan, target_date, args.posted)
        if ok:
            save_plan(plan, plan_file)
            print(f"Marked post {args.posted} for {target_date} as done.")
        else:
            print(f"Could not mark post {args.posted} for {target_date}.")
        return 0

    if args.week:
        print(week_summary(plan, target_date))
        return 0

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
