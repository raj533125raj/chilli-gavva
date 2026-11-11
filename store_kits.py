"""store_kits.py — Copy-paste listing kit generator per digital product store.

For each ready pack, writes:
  <pack>/store-kits/<store>.txt
for 6 supported stores:
  - Gumroad
  - Payhip
  - Etsy
  - Ko-fi
  - Topmate
  - Instamojo

Each kit contains every field the store form requires:
  - Store Name & slug
  - Title within the store's character limit (trimmed at word boundaries)
  - Short description (if applicable / supported)
  - Long description (full description + what you get, trimmed if limit applies)
  - Tags formatted according to the store's specific tag rules
  - Category suggestion appropriate for the digital product
  - Ordered file upload list (e.g. product.zip, LICENSE.txt)
  - Ordered cover & preview image upload list (e.g. cover.png, preview-*.png)
  - Explicit 'Price: owner decides' placeholder (strictly never any money amount)
  - Checklist of manual UI clicks in Tenglish house style

Safety & constraints:
  - No currency or money amounts anywhere (must pass test_r22_markdown_currency).
  - No external network calls, no automated posting/publishing.
  - Limits table consolidated in one dictionary with comments per store.
  - Trimming strictly at word boundaries.
  - Emoji-safe character counting (handles surrogate pairs and graphemes correctly).
  - Deterministic outputs and atomic file writes.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import listing_fields

# Money detection patterns reused from listing_fields
MONEY_PATTERNS = listing_fields.MONEY_PATTERNS


# ---------------------------------------------------------------------------
# Store Limits & Specifications Table
# Consolidated into one dictionary with a comment per store.
# ---------------------------------------------------------------------------

STORE_LIMITS: Dict[str, Dict[str, Any]] = {
    # -----------------------------------------------------------------------
    # Gumroad:
    # Popular creator platform for digital downloads and software.
    # Title limit: 150 chars (form accepts up to ~250, 150 is the safe visible cap).
    # Short description / summary: ~150 chars.
    # Description: rich markdown or plain text (no hard length cap, practically 10,000+ chars).
    # Tags: max 10 tags, each up to 30 chars, comma-separated or pill tags.
    # Category suggestion: Digital Products / Self-Improvement / Productivity.
    # -----------------------------------------------------------------------
    "gumroad": {
        "store_name": "Gumroad",
        "file_name": "gumroad.txt",
        "title_limit": 150,
        "short_desc_limit": 150,
        "desc_limit": 10000,
        "max_tags": 10,
        "tag_char_limit": 30,
        "tag_format": "comma",  # comma-separated
        "tag_lowercase": True,
        "category": "Digital Products > Productivity & Planning",
        "price_placeholder": "Price: owner decides",
        "click_checklist": [
            "Gumroad Dashboard lo Products tab open cheyandi.",
            "'New product' button click chesi 'Digital product' select cheyandi.",
            "Title box lo paina unna Name paste cheyandi.",
            "Price field lo owner decide chesina price enter cheyandi (default: owner decides).",
            "Next: Customize page lo Description paste cheyandi.",
            "Cover image upload section lo cover.png upload cheyandi.",
            "Additional preview images upload cheyandi (preview-*.png).",
            "Tags section lo paina unna 10 tags add cheyandi.",
            "Next: Content page lo product.zip file upload cheyandi.",
            "Settings check chesukoni 'Publish and continue' click cheyandi.",
        ],
    },

    # -----------------------------------------------------------------------
    # Payhip:
    # E-commerce platform for digital downloads, courses, and memberships.
    # Title limit: 100 chars recommended for store grid cards.
    # Short description: 160 chars summary / SEO meta description.
    # Description: full HTML/Markdown body up to 8000 chars.
    # Tags / Collections: max 10 tags / categories.
    # Category suggestion: Digital Downloads / Productivity.
    # -----------------------------------------------------------------------
    "payhip": {
        "store_name": "Payhip",
        "file_name": "payhip.txt",
        "title_limit": 100,
        "short_desc_limit": 160,
        "desc_limit": 8000,
        "max_tags": 10,
        "tag_char_limit": 35,
        "tag_format": "comma",
        "tag_lowercase": True,
        "category": "Digital Downloads > Planners & Trackers",
        "price_placeholder": "Price: owner decides",
        "click_checklist": [
            "Payhip Dashboard lo Products -> 'Add new product' click cheyandi.",
            "'Add a digital product' card select cheyandi.",
            "Upload product file section lo product.zip upload cheyandi.",
            "Title field lo paina unna Title paste cheyandi.",
            "Price field lo owner decide chesina price enter cheyandi.",
            "Product image slot lo cover.png mariyu previews upload cheyandi.",
            "Description box lo rich description paste cheyandi.",
            "Advanced options lo Collection / Tags add cheyandi.",
            "'Add Product' button click chesi live status verify cheyandi.",
        ],
    },

    # -----------------------------------------------------------------------
    # Etsy:
    # Marketplace with strict 140 char title and exactly max 13 tags of 20 chars each.
    # Title limit: 140 chars max (Etsy search truncates beyond this).
    # Short description: first 160 chars visible before 'read more' fold.
    # Description: plain text up to 5000 chars.
    # Tags: max 13 tags, each strictly <= 20 chars, lowercase, no special chars.
    # Category suggestion: Paper & Party Supplies > Paper > Calendars & Planners.
    # -----------------------------------------------------------------------
    "etsy": {
        "store_name": "Etsy",
        "file_name": "etsy.txt",
        "title_limit": 140,
        "short_desc_limit": 160,
        "desc_limit": 5000,
        "max_tags": 13,
        "tag_char_limit": 20,
        "tag_format": "comma",
        "tag_lowercase": True,
        "category": "Templates > Planners & Trackers",
        "price_placeholder": "Price: owner decides",
        "click_checklist": [
            "Etsy Shop Manager lo Listings -> 'Add a listing' click cheyandi.",
            "Listing type lo 'Digital files' radio button select cheyandi.",
            "Title box lo 140-char title paste cheyandi (no punctuation spam).",
            "Photos section lo 1:1 / 4:3 cover.png mariyu preview images upload cheyandi.",
            "Digital files section lo product.zip upload cheyandi.",
            "Description box lo product details mariyu 'What you get' paste cheyandi.",
            "Tags section lo paina unna 13 tags okkokkati add cheyandi (each <= 20 chars).",
            "Category search lo 'Digital Planners' or 'Templates' pick cheyandi.",
            "Price field lo owner decide chesina amount enter cheyandi.",
            "Renewal options check chesi 'Publish' click cheyandi.",
        ],
    },

    # -----------------------------------------------------------------------
    # Ko-fi:
    # Creator tipping and digital shop platform.
    # Title limit: 100 chars max.
    # Short description / summary: 140 chars.
    # Description: up to 4000 chars formatted text.
    # Tags: max 5 tags, comma-separated.
    # Category suggestion: Digital Goods / Templates.
    # -----------------------------------------------------------------------
    "kofi": {
        "store_name": "Ko-fi",
        "file_name": "kofi.txt",
        "title_limit": 100,
        "short_desc_limit": 140,
        "desc_limit": 4000,
        "max_tags": 5,
        "tag_char_limit": 25,
        "tag_format": "comma",
        "tag_lowercase": True,
        "category": "Digital Products > Templates & Downloads",
        "price_placeholder": "Price: owner decides",
        "click_checklist": [
            "Ko-fi menu lo 'Shop' section click cheyandi.",
            "'Add Product' button press chesi 'Digital Item' option pick cheyandi.",
            "Product Name field lo paina unna Title paste cheyandi.",
            "Price section lo 'Fixed Price' or 'Pay what you want' (owner decides) set cheyandi.",
            "Upload file button tho product.zip attach cheyandi.",
            "Images lo cover.png mariyu preview images drop cheyandi.",
            "Description box lo product summary mariyu features paste cheyandi.",
            "Category & Tags lo paina unna 5 tags add cheyandi.",
            "'Enable product' toggle on chesi 'Save' click cheyandi.",
        ],
    },

    # -----------------------------------------------------------------------
    # Topmate:
    # India & Global 1:1 advisory and digital product selling platform.
    # Title limit: 80 chars max.
    # Short description: 150 chars max (displayed on card preview).
    # Long description: up to 3000 chars rich text.
    # Tags: max 5 tags.
    # Category suggestion: Digital Products / Productivity Templates.
    # -----------------------------------------------------------------------
    "topmate": {
        "store_name": "Topmate",
        "file_name": "topmate.txt",
        "title_limit": 80,
        "short_desc_limit": 150,
        "desc_limit": 3000,
        "max_tags": 5,
        "tag_char_limit": 30,
        "tag_format": "comma",
        "tag_lowercase": True,
        "category": "Digital Products > Productivity",
        "price_placeholder": "Price: owner decides",
        "click_checklist": [
            "Topmate dashboard lo Services / Digital Products tab open cheyandi.",
            "'Add Service' click chesi 'Digital Product' select cheyandi.",
            "Title box lo 80-char Title paste cheyandi.",
            "Short description box lo 150-char summary paste cheyandi.",
            "Long description box lo full details paste cheyandi.",
            "Price field lo owner decide chesina price enter cheyandi.",
            "Upload digital file section lo product.zip upload cheyandi.",
            "Cover image banner lo cover.png upload cheyandi.",
            "Tags enter chesi 'Publish' button click cheyandi.",
        ],
    },

    # -----------------------------------------------------------------------
    # Instamojo:
    # Indian e-commerce & payment gateway store for digital files.
    # Title limit: 80 chars max.
    # Short description: 200 chars.
    # Description: up to 5000 chars (includes 'What you get').
    # Tags: max 10 tags.
    # Category suggestion: Digital Goods & Templates.
    # -----------------------------------------------------------------------
    "instamojo": {
        "store_name": "Instamojo",
        "file_name": "instamojo.txt",
        "title_limit": 80,
        "short_desc_limit": 200,
        "desc_limit": 5000,
        "max_tags": 10,
        "tag_char_limit": 30,
        "tag_format": "comma",
        "tag_lowercase": True,
        "category": "Digital Goods > Templates & Planners",
        "price_placeholder": "Price: owner decides",
        "click_checklist": [
            "Instamojo Dashboard lo 'Products & Store' -> 'Add Product' click cheyandi.",
            "'Digital Product' type select cheyandi.",
            "Title box lo 80-char Title paste cheyandi.",
            "Upload file section lo product.zip file attach cheyandi.",
            "Images section lo cover.png mariyu preview files upload cheyandi.",
            "Description box lo Description + What you get paste cheyandi.",
            "Price box lo owner decide chesina amount enter cheyandi (free or paid).",
            "Category lo 'Digital Goods' select chesi Tags enter cheyandi.",
            "'Save and Publish' click chesi product link verify cheyandi.",
        ],
    },
}

SUPPORTED_STORES = list(STORE_LIMITS.keys())


# ---------------------------------------------------------------------------
# Unicode & Emoji Safe Character Counting & Word Boundary Trimming
# ---------------------------------------------------------------------------

def grapheme_clusters(text: str) -> List[str]:
    """Break text into unicode grapheme clusters (emoji and combining mark safe).

    Handles combining marks (accents), variation selectors (e.g. U+FE0F),
    and zero-width joiners (ZWJ) sequences without slicing emojis into invalid characters.
    """
    clusters: List[str] = []
    current_cluster = ""
    for char in text:
        # Check if char is combining character, variation selector, or ZWJ
        category = unicodedata.category(char)
        is_combining = category in ("Mn", "Mc", "Me")
        is_variation_selector = (0xFE00 <= ord(char) <= 0xFE0F) or (0xE0100 <= ord(char) <= 0xE01EF)
        is_zwj = ord(char) == 0x200D

        if current_cluster and (is_combining or is_variation_selector or is_zwj or current_cluster.endswith("\u200D")):
            current_cluster += char
        else:
            if current_cluster:
                clusters.append(current_cluster)
            current_cluster = char
    if current_cluster:
        clusters.append(current_cluster)
    return clusters


def count_graphemes(text: str) -> int:
    """Return count of visual graphemes in text (emoji-safe)."""
    return len(grapheme_clusters(text))


def trim_to_word_boundary(text: str, max_chars: int) -> str:
    """Trim text to at most max_chars visual graphemes, strictly cutting at a word boundary.

    If text length <= max_chars, returns stripped text.
    If no whitespace exists within max_chars, cuts at max_chars graphemes cleanly.
    """
    text = text.strip()
    clusters = grapheme_clusters(text)
    if len(clusters) <= max_chars:
        return text

    head_clusters = clusters[:max_chars]
    head_str = "".join(head_clusters)

    # Find the last whitespace within head_str to avoid slicing words
    last_space_idx = -1
    for i in range(len(head_clusters) - 1, -1, -1):
        if head_clusters[i].isspace():
            last_space_idx = i
            break

    if last_space_idx > 0:
        return "".join(head_clusters[:last_space_idx]).rstrip()
    return head_str.rstrip()


# ---------------------------------------------------------------------------
# Money Sanitization & Safety
# ---------------------------------------------------------------------------

def check_money(text: str) -> Optional[str]:
    """Check text for forbidden currency amounts."""
    for pat in MONEY_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def sanitize_no_money(text: str) -> str:
    """Remove any currency matches and replace with safe placeholder."""
    for pat in MONEY_PATTERNS:
        text = pat.sub("[PRICE SET BY OWNER]", text)
    return text


# ---------------------------------------------------------------------------
# Listing & File Inspection Helpers
# ---------------------------------------------------------------------------

def _extract_section(text: str, label: str) -> str:
    """Extract section from listing.txt cleanly."""
    pattern = re.compile(rf"^{re.escape(label)}:\s*(.*?)(?=\n[A-Za-z][A-Za-z /_-]*:|\Z)", re.DOTALL | re.MULTILINE)
    m = pattern.search(text)
    if m:
        return m.group(1).strip()
    return listing_fields._section(text, label)


def parse_listing_file(listing_path: Path) -> Dict[str, str]:
    """Read listing.txt and extract fields."""
    if not listing_path.is_file():
        raise FileNotFoundError(f"listing.txt not found in {listing_path.parent}")
    raw = listing_path.read_text(encoding="utf-8")

    money_hit = check_money(raw)
    if money_hit:
        raise ValueError(f"listing.txt contains forbidden money amount: {money_hit}")

    title = _extract_section(raw, "Title")
    desc = _extract_section(raw, "Description")
    tags = _extract_section(raw, "Tags")
    what_you_get = _extract_section(raw, "What you get")

    return {
        "raw": raw,
        "title": title or listing_path.parent.name,
        "description": desc,
        "tags": tags,
        "what_you_get": what_you_get,
    }


def get_files_to_upload(pack_dir: Path) -> List[str]:
    """Ordered list of deliverables to upload."""
    files: List[str] = []
    # Standard deliverables in preferred order
    for name in ["product.zip", "LICENSE.txt", "checklist.txt", "README.txt", "README-HOW-TO-INSTALL.txt"]:
        if (pack_dir / name).is_file() and name not in files:
            files.append(name)

    # Any other digital deliverable
    for p in sorted(pack_dir.iterdir()):
        if p.is_file() and not p.name.startswith(".") and p.name not in files:
            if p.suffix.lower() in [".zip", ".pdf", ".xlsx", ".docx", ".png", ".html"]:
                if not p.name.startswith("cover") and not p.name.startswith("preview"):
                    files.append(p.name)

    if not files:
        files.append("product.zip")
    return files


def get_images_to_upload(pack_dir: Path) -> List[str]:
    """Ordered list of cover and preview images."""
    images: List[str] = []
    if (pack_dir / "cover.png").is_file():
        images.append("cover.png")

    previews: List[Path] = []
    for p in pack_dir.iterdir():
        if p.is_file() and p.name.lower().startswith("preview") and p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
            previews.append(p)

    # Sort previews naturally (preview-1.png, preview-2.png, preview-10.png)
    previews.sort(key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)])
    for p in previews:
        images.append(p.name)

    # Add other images if any
    for p in sorted(pack_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
            if p.name not in images and not p.name.startswith("."):
                images.append(p.name)

    if not images:
        images.append("cover.png")
    return images


# ---------------------------------------------------------------------------
# Tag Formatting per Store
# ---------------------------------------------------------------------------

def format_tags_for_store(raw_tags: str, store_key: str) -> List[str]:
    """Format tags according to store specific constraints."""
    limits = STORE_LIMITS.get(store_key, STORE_LIMITS["gumroad"])
    max_tags = limits.get("max_tags", 10)
    tag_char_limit = limits.get("tag_char_limit", 30)
    lowercase = limits.get("tag_lowercase", True)

    tags: List[str] = []
    seen = set()

    # Split on commas or newlines
    raw_list = re.split(r"[,;\n]+", raw_tags)
    for raw in raw_list:
        t = raw.strip()
        if not t:
            continue
        if lowercase:
            t = t.lower()

        # Clean special characters if Etsy (letters, numbers, spaces only)
        if store_key == "etsy":
            t = re.sub(r"[^\w\s-]", "", t).strip()

        # Trim tag to store char limit at word boundary if possible, else slice
        if count_graphemes(t) > tag_char_limit:
            t = trim_to_word_boundary(t, tag_char_limit)

        if t and t not in seen:
            seen.add(t)
            tags.append(t)
        if len(tags) >= max_tags:
            break

    return tags


# ---------------------------------------------------------------------------
# Kit Generator Function per Store
# ---------------------------------------------------------------------------

def generate_kit_text(
    store_key: str,
    pack_dir: Path,
    listing_info: Dict[str, str],
) -> str:
    """Generate the full copy-paste kit text for a specific store."""
    if store_key not in STORE_LIMITS:
        raise ValueError(f"Unsupported store: {store_key}. Must be one of {SUPPORTED_STORES}")

    limits = STORE_LIMITS[store_key]
    store_name = limits["store_name"]

    raw_title = listing_info.get("title", pack_dir.name)
    raw_desc = listing_info.get("description", "")
    raw_wyg = listing_info.get("what_you_get", "")
    raw_tags = listing_info.get("tags", "")

    # 1. Title trimmed to limit
    title_limit = limits.get("title_limit", 100)
    title = trim_to_word_boundary(raw_title, title_limit)

    # 2. Short description
    short_limit = limits.get("short_desc_limit", 150)
    short_desc = trim_to_word_boundary(raw_desc, short_limit)

    # 3. Long description
    desc_limit = limits.get("desc_limit", 5000)
    full_desc_parts = [raw_desc]
    if raw_wyg and raw_wyg not in raw_desc:
        full_desc_parts.append(f"What you get:\n{raw_wyg}")
    combined_desc = "\n\n".join(part for part in full_desc_parts if part.strip())
    long_desc = trim_to_word_boundary(combined_desc, desc_limit)

    # 4. Tags
    tags_list = format_tags_for_store(raw_tags, store_key)
    tags_formatted = ", ".join(tags_list)

    # 5. Category
    category = limits.get("category", "Digital Products")

    # 6. Deliverables & Images
    files_to_upload = get_files_to_upload(pack_dir)
    images_to_upload = get_images_to_upload(pack_dir)

    # 7. Price
    price_placeholder = limits.get("price_placeholder", "Price: owner decides")

    # 8. Tenglish checklist
    checklist = limits.get("click_checklist", [])

    # Compose output file lines
    lines: List[str] = []
    lines.append(f"=== {store_name.upper()} LISTING KIT ===")
    lines.append(f"Store: {store_name}")
    lines.append(f"Pack: {pack_dir.name}")
    lines.append("")

    lines.append("--- FIELD: TITLE ---")
    lines.append(f"Title: {title}")
    lines.append(f"(Length: {count_graphemes(title)} / {title_limit} max)")
    lines.append("")

    lines.append("--- FIELD: SHORT DESCRIPTION ---")
    lines.append(f"Short Description: {short_desc}")
    lines.append(f"(Length: {count_graphemes(short_desc)} / {short_limit} max)")
    lines.append("")

    lines.append("--- FIELD: LONG DESCRIPTION ---")
    lines.append("Description:")
    lines.append(long_desc)
    lines.append(f"(Length: {count_graphemes(long_desc)} / {desc_limit} max)")
    lines.append("")

    lines.append("--- FIELD: TAGS ---")
    lines.append(f"Tags ({len(tags_list)} tags):")
    lines.append(tags_formatted)
    lines.append("")

    lines.append("--- FIELD: CATEGORY ---")
    lines.append(f"Category: {category}")
    lines.append("")

    lines.append("--- FIELD: PRICING ---")
    lines.append(f"{price_placeholder}")
    lines.append("Note: Strict rule — owner sets actual amount in dashboard; robot never suggests or enters amounts.")
    lines.append("")

    lines.append("--- FIELD: FILES TO UPLOAD (IN ORDER) ---")
    for idx, f in enumerate(files_to_upload, 1):
        lines.append(f"{idx}. {f}")
    lines.append("")

    lines.append("--- FIELD: IMAGES TO UPLOAD (IN ORDER) ---")
    for idx, img in enumerate(images_to_upload, 1):
        role = "Cover Image" if idx == 1 else f"Preview {idx - 1}"
        lines.append(f"{idx}. {img} ({role})")
    lines.append("")

    lines.append("--- CHECKLIST OF CLICKS (TENGLISH) ---")
    for idx, step in enumerate(checklist, 1):
        lines.append(f"[ ] Step {idx}: {step}")
    lines.append("")

    content = "\n".join(lines)

    # Safety check: ensure no currency amounts slipped in
    money_hit = check_money(content)
    if money_hit:
        content = sanitize_no_money(content)

    return content


# ---------------------------------------------------------------------------
# Core Builder: build(pack_dir)
# ---------------------------------------------------------------------------

def build(pack_dir: Path | str) -> Dict[str, Path]:
    """Generate <pack>/store-kits/<store>.txt for all 6 supported stores.

    Writes atomically and deterministically.
    Returns dict mapping store_key -> Path of written kit file.
    """
    pdir = Path(pack_dir).resolve()
    if not pdir.is_dir():
        raise FileNotFoundError(f"Pack directory not found: {pdir}")

    listing_file = pdir / "listing.txt"
    listing_info = parse_listing_file(listing_file)

    kits_dir = pdir / "store-kits"
    kits_dir.mkdir(parents=True, exist_ok=True)

    results: Dict[str, Path] = {}

    for store_key, limits in STORE_LIMITS.items():
        file_name = limits["file_name"]
        target_path = kits_dir / file_name
        content = generate_kit_text(store_key, pdir, listing_info)

        # Atomic write
        fd, tmp_file = tempfile.mkstemp(dir=str(kits_dir), prefix=f".{store_key}_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_file, str(target_path))

        results[store_key] = target_path

    return results


# ---------------------------------------------------------------------------
# Pack Discovery & CLI
# ---------------------------------------------------------------------------

def discover_ready_packs(products_dir: Path | str = "exported_content/products") -> List[Path]:
    """Find all ready upload packs under products_dir/upload or products_dir."""
    base = Path(products_dir)
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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate copy-paste store listing kits for ready packs.")
    parser.add_argument("--pack", help="Specific pack directory or pack ID.")
    parser.add_argument("--all", action="store_true", help="Generate kits for all ready packs.")
    parser.add_argument(
        "--products-dir",
        default="exported_content/products",
        help="Products root directory.",
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

        written = build(pack_path)
        print(f"Built {len(written)} store kits in {pack_path / 'store-kits'}")
        return 0

    packs = discover_ready_packs(args.products_dir)
    total_written = 0
    for p in packs:
        written = build(p)
        total_written += len(written)

    print(f"Built store kits for {len(packs)} packs ({total_written} files total).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
