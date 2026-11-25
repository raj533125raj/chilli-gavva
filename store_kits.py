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
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from typing import Any, Dict, List, Optional, Tuple, Union

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
        is_regional_indicator = 0x1F1E6 <= ord(char) <= 0x1F1FF
        starts_flag_pair = (
            is_regional_indicator
            and len(current_cluster) == 1
            and 0x1F1E6 <= ord(current_cluster) <= 0x1F1FF
        )

        if current_cluster and (is_combining or is_variation_selector or is_zwj or starts_flag_pair or current_cluster.endswith("\u200D")):
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
# Gumroad copy that converts (CCA-2) — used only for the Gumroad kit
#
# Everything here is built from what is REALLY in the pack: the files in product.zip and next to
# it, and the pack's own listing.txt (which already passed the truth check). Nothing is promised
# that the files do not have: a listing bullet that names a file type the pack does not contain is
# left out, and no file type, page count or feature is ever invented.
# ---------------------------------------------------------------------------

GUMROAD_TAG_COUNT = 10
GUMROAD_HOOK_LIMIT = 140
GUMROAD_OWNER_FILES = {"listing.txt", "listing.md", "checklist.txt"}
GUMROAD_SKIP_FOLDERS = {"store-kits", "store_kits", "kits", "support", "gumroad", "gumroad-images",
                        "gumroad_images", "marketing"}
# Brand names never go into tags (store rules and house rule).
GUMROAD_BRAND_WORDS = {
    "etsy", "amazon", "canva", "notion", "gumroad", "payhip", "kofi", "ko-fi", "topmate", "instamojo",
    "google", "microsoft", "excel", "powerpoint", "goodnotes", "notability", "ipad", "iphone", "kindle",
    "pinterest", "instagram", "facebook", "tiktok", "youtube", "adobe", "photoshop", "figma", "airtable",
    "trello", "shopify", "ikea", "disney", "nike", "whatsapp", "telegram", "chatgpt", "openai", "samsung",
    "android", "outlook", "gmail", "linkedin", "zoom", "slack", "evernote", "onenote", "clickup", "asana",
    "obsidian", "remarkable", "cricut",
}

# One entry per product type the robot can build (format_registry) + "digital_pack" as the fallback.
GUMROAD_TYPES: Dict[str, Dict[str, Any]] = {
    "planner": {
        "label": "printable planner",
        "audience": ["Anyone who likes to plan the day or week on paper",
                     "People who want a simple page to fill in, not another app"],
        "tags": ["printable planner", "daily planner", "weekly planner", "planner pages", "planner pdf",
                 "desk planner", "to do list", "productivity"],
    },
    "dated_planner": {
        "label": "dated planner",
        "audience": ["People who want the dates already printed on the page",
                     "Anyone planning a full year, month by month"],
        "tags": ["dated planner", "calendar planner", "monthly planner", "yearly planner", "printable calendar",
                 "planner pdf", "year planner", "organizer"],
    },
    "sheets": {
        "label": "spreadsheet",
        "audience": ["People who like to track numbers in a spreadsheet",
                     "Anyone who wants the tabs and formulas set up already"],
        "tags": ["spreadsheet template", "tracker spreadsheet", "budget spreadsheet", "digital template",
                 "spreadsheet", "tracker template", "organizer", "calculator sheet"],
    },
    "notion": {
        "label": "workspace template",
        "audience": ["People who organise their work in a notes workspace",
                     "Anyone who wants a ready page structure instead of a blank page"],
        "tags": ["workspace template", "digital planner", "dashboard template", "digital template",
                 "productivity template", "organizer template", "planner template", "life planner"],
    },
    "resume": {
        "label": "resume template",
        "audience": ["Job seekers who want a clean, editable resume",
                     "Anyone updating a CV or cover letter without starting from zero"],
        "tags": ["resume template", "cv template", "cover letter", "job search", "editable resume",
                 "professional resume", "career", "job application"],
    },
    "habit_tracker": {
        "label": "habit tracker",
        "audience": ["Anyone building a daily routine one small habit at a time",
                     "People who like to tick boxes and see the streak on paper"],
        "tags": ["habit tracker", "printable tracker", "routine tracker", "daily habits", "goal tracker",
                 "self care planner", "tracker pdf", "monthly tracker"],
    },
    "study_planner": {
        "label": "study planner",
        "audience": ["Students planning classes, revision and exams",
                     "Anyone learning on their own who wants a clear study plan"],
        "tags": ["study planner", "student planner", "exam planner", "revision planner", "homework planner",
                 "school planner", "academic planner", "study schedule"],
    },
    "recipe_book": {
        "label": "recipe book template",
        "audience": ["Home cooks who want their own recipes in one tidy book",
                     "Families collecting favourite dishes to keep or print"],
        "tags": ["recipe book", "recipe template", "recipe pages", "printable recipe", "kitchen organizer",
                 "recipe binder", "recipe cards", "cookbook template"],
    },
    "flashcards": {
        "label": "flashcard set",
        "audience": ["Learners who remember better with quick question-and-answer cards",
                     "Parents and teachers who want cards to print and cut"],
        "tags": ["flashcards", "printable flashcards", "study cards", "learning cards", "revision cards",
                 "memory cards", "study aid", "teaching resource"],
    },
    "event_planner": {
        "label": "event planner",
        "audience": ["Anyone organising a party, wedding or other event",
                     "People who want every task and guest on one checklist"],
        "tags": ["event planner", "party planner", "event checklist", "event organizer", "guest list",
                 "planning checklist", "celebration planner", "printable planner"],
    },
    "email_signature": {
        "label": "email signature template",
        "audience": ["Professionals and freelancers who want a tidy email sign-off",
                     "Small teams who want every email to look the same"],
        "tags": ["email signature", "signature template", "business email", "professional signature",
                 "email template", "branding template", "freelancer tools", "small business"],
    },
    "wall_art": {
        "label": "wall art print set",
        "audience": ["Anyone decorating a home, desk or office wall",
                     "People looking for a quick printable gift"],
        "tags": ["wall art", "printable art", "poster print", "quote print", "home decor", "digital print",
                 "office decor", "printable poster"],
    },
    'teacher_planner': {
        "label": 'teacher planner',
        "audience": ['Teachers organizing lessons, classes and school weeks', 'Educators keeping classroom plans and notes on paper'],
        "tags": ['teacher planner', 'lesson planner', 'classroom planner', 'school organizer', 'teacher printable', 'class roster', 'lesson plan', 'teaching planner'],
    },
    'fitness_planner': {
        "label": 'fitness planner',
        "audience": ['People recording workouts and daily routines', 'Anyone organizing exercise notes on printable pages'],
        "tags": ['fitness planner', 'workout planner', 'exercise log', 'workout tracker', 'fitness journal', 'routine planner', 'training log', 'printable workout'],
    },
    'baby_tracker': {
        "label": 'baby tracker',
        "audience": ['Parents recording feeding, sleep and daily care', 'Caregivers sharing written baby routine notes'],
        "tags": ['baby tracker', 'baby log', 'feeding log', 'sleep tracker', 'newborn tracker', 'baby routine', 'caregiver notes', 'printable baby log'],
    },
    'small_business_log': {
        "label": 'small business log',
        "audience": ['Small business owners organizing orders and stock', 'Freelancers keeping customer and weekly work notes'],
        "tags": ['small business log', 'business tracker', 'order log', 'inventory log', 'customer notes', 'business planner', 'stock tracker', 'printable business'],
    },
    'kids_chore_reward': {
        "label": 'kids chore and reward chart',
        "audience": ['Families organizing age appropriate chores', 'Parents tracking routines and rewards together with children'],
        "tags": ['kids chore chart', 'reward chart', 'chore tracker', 'kids routine', 'family chores', 'printable rewards', 'routine chart', 'chore planner'],
    },
    "digital_planner": {
        "label": "hyperlinked digital planner",
        "audience": ["People planning on a tablet with a PDF notes app", "Anyone wanting clickable month and index tabs"],
        "tags": ["digital planner", "hyperlinked planner", "tablet planner", "monthly planner", "digital journal", "weekly planner", "pdf planner", "year planner"],
    },
    "biz_sheets": {
        "label": "small business workbook",
        "audience": ["Small business owners tracking orders and inventory", "Shop owners reviewing expenses and monthly profit"],
        "tags": ["business spreadsheet", "order tracker", "inventory spreadsheet", "profit workbook", "small business", "expense tracker", "customer tracker", "shop workbook"],
    },
    "debt_payoff": {
        "label": "debt payoff tracker",
        "audience": ["People paying down several debts one at a time", "Anyone choosing a snowball or avalanche payoff order"],
        "tags": ["debt payoff tracker", "debt snowball", "debt avalanche", "debt tracker", "budget spreadsheet", "spreadsheet template", "loan tracker", "debt free plan"],
    },
    "couples_budget": {
        "label": "couples shared budget",
        "audience": ["Couples sharing bills and household costs", "Partners who want a fair split and one settle-up line"],
        "tags": ["couples budget", "shared budget", "joint budget", "household budget", "split expenses", "budget spreadsheet", "spreadsheet template", "bill tracker"],
    },
    "freelance_budget": {
        "label": "freelancer budget for uneven months",
        "audience": ["Freelancers whose pay changes from month to month", "Self-employed people setting money aside for tax and slow months"],
        "tags": ["freelancer budget", "uneven pay budget", "self employed budget", "tax set aside", "buffer fund", "budget spreadsheet", "spreadsheet template", "freelance planner"],
    },
    "sinking_funds": {
        "label": "sinking funds and savings goals tracker",
        "audience": ["People saving for planned costs a little at a time", "Anyone tracking several savings goals in one place"],
        "tags": ["sinking funds", "savings tracker", "savings goals", "budget spreadsheet", "spreadsheet template", "savings plan", "money tracker", "goal tracker"],
    },
    "digital_pack": {
        "label": "digital download",
        "audience": ["Anyone who wants ready-made files to download and use",
                     "People who prefer a simple tool over a complicated app"],
        "tags": ["digital download", "printable", "template", "digital template", "organizer",
                 "instant download", "digital product", "download", "digital files", "printable template"],
    },
}
GUMROAD_PRODUCT_TYPES = tuple(key for key in GUMROAD_TYPES if key != "digital_pack")

_PDF_EXT, _IMAGE_EXTS = ".pdf", {".png", ".jpg", ".jpeg", ".webp"}
_SHEET_EXTS, _DOC_EXTS, _TEXT_EXTS = {".xlsx", ".xls"}, {".docx", ".doc"}, {".md", ".txt"}
# File types a product type must really contain; otherwise the type is worked out from the files.
_TYPE_NEEDS = {
    **{kind: {_PDF_EXT} for kind in ("teacher_planner", "fitness_planner", "baby_tracker",
                                    "small_business_log", "kids_chore_reward")},
    "digital_planner": {_PDF_EXT},
    "biz_sheets": _SHEET_EXTS,
    **{kind: _SHEET_EXTS for kind in ("debt_payoff", "couples_budget", "freelance_budget", "sinking_funds")},
    "sheets": _SHEET_EXTS | {".csv"},
    "notion": {".md", ".csv", ".zip"},
    "resume": _DOC_EXTS | {_PDF_EXT},
    "wall_art": _IMAGE_EXTS | {_PDF_EXT},
    "email_signature": {".html", ".htm", ".txt"} | _IMAGE_EXTS,
}
# A listing bullet that uses one of these words is kept only when the pack has such a file.
_CLAIM_NEEDS = [
    (r"\bpdfs?\b", {_PDF_EXT}),
    (r"\b(?:xlsx|excel|spreadsheets?|workbooks?|google\s+sheets?)\b", _SHEET_EXTS | {".csv"}),
    (r"\bcsv\b", {".csv"}),
    (r"\b(?:docx|word\s+(?:file|doc|document)s?)\b", _DOC_EXTS),
    (r"\b(?:png|jpe?g|image\s+files?)\b", _IMAGE_EXTS),
    (r"\b(?:ics|calendar\s+file)\b", {".ics"}),
    (r"\bhtml\b", {".html", ".htm"}),
]
# Things the robot never ships: a listing bullet that promises one is always left out.
_NEVER_SHIPPED = re.compile(
    r"\b(?:canva|figma|psd|photoshop|video|videos|mp4|audio|mp3|course|coaching|community|"
    r"lifetime\s+updates?|free\s+updates?|24\s*/\s*7|printed\s+copy|physical|shipping|shipped\s+to)\b", re.IGNORECASE)


def gumroad_pack_files(pack_dir: Path) -> List[Dict[str, Any]]:
    """The real buyer files: members of product.zip plus deliverables next to it.

    Owner notes (listing, checklist), cover / preview images and helper folders are not buyer files.
    Each item: {"name", "ext", "size", "where": "zip" | "folder"}.
    """
    import zipfile
    pack_dir = Path(pack_dir)
    found: Dict[str, Dict[str, Any]] = {}
    archive = pack_dir / "product.zip"
    if archive.is_file():
        try:
            with zipfile.ZipFile(archive) as bundle:
                for info in bundle.infolist():
                    name = Path(info.filename).name
                    if info.is_dir() or not name or name.startswith(".") or "__MACOSX" in info.filename:
                        continue
                    found.setdefault(name.casefold(), {"name": name, "ext": Path(name).suffix.lower(),
                                                       "size": int(info.file_size), "where": "zip"})
        except (OSError, zipfile.BadZipFile):
            pass
    if pack_dir.is_dir():
        for path in sorted(pack_dir.iterdir()):
            low = path.name.lower()
            if (not path.is_file() or path.is_symlink() or low.startswith(".") or low in GUMROAD_OWNER_FILES
                    or low == "product.zip" or low.startswith(("cover", "preview"))):
                continue
            if path.suffix.lower() not in {".pdf", ".xlsx", ".xls", ".csv", ".docx", ".doc", ".png", ".jpg",
                                           ".jpeg", ".webp", ".html", ".htm", ".md", ".txt", ".ics", ".zip"}:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            found.setdefault(low, {"name": path.name, "ext": path.suffix.lower(), "size": int(size),
                                   "where": "folder"})
    return [found[key] for key in sorted(found)]


def _is_guide_file(name: str) -> bool:
    low = name.lower()
    return low.startswith(("readme", "read-me", "read_me", "license", "licence", "how-to", "how_to"))


def gumroad_product_type(pack_dir: Path, listing_info: Dict[str, str],
                         files: Optional[List[Dict[str, Any]]] = None) -> str:
    """One of GUMROAD_PRODUCT_TYPES (or "digital_pack"), from the listing words AND the real files."""
    files = gumroad_pack_files(pack_dir) if files is None else files
    exts = {item["ext"] for item in files if not _is_guide_file(item["name"])}
    guess = None
    try:
        import format_registry
        pack_dir = Path(pack_dir)
        source = pack_dir.parent.parent / pack_dir.name if pack_dir.parent.name == "upload" else pack_dir
        for name in ("build-format.json", "manifest.json"):
            manifest = source / name
            if source.is_symlink() or not manifest.is_file() or manifest.is_symlink() or manifest.stat().st_size > 65536:
                continue
            try:
                metadata = json.loads(manifest.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            recorded = metadata.get("format") if isinstance(metadata, dict) else None
            if recorded in GUMROAD_PRODUCT_TYPES and format_registry.get_format_info(recorded):
                guess = recorded
                break
    except (OSError, ValueError, TypeError):
        guess = None
    # Built metadata survives honest title sanitization; older and external
    # packs (including unreadable metadata) still use their listing words.
    if guess is None:
        try:
            import format_registry
            guess = format_registry.classify(" ".join([listing_info.get("title", ""), listing_info.get("tags", "")]))
        except Exception:
            guess = None
    if guess in GUMROAD_TYPES and guess != "digital_pack":
        needed = _TYPE_NEEDS.get(guess)
        if needed is None or (exts & needed):
            return guess
    if exts & _SHEET_EXTS:
        return "sheets"
    if exts & _DOC_EXTS:
        return "resume"
    if _PDF_EXT in exts:
        return "planner"
    if ".csv" in exts and ".md" in exts:
        return "notion"
    if ".csv" in exts:
        return "sheets"
    if exts & _IMAGE_EXTS:
        return "wall_art"
    return "digital_pack"


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _claim_is_backed(line: str, exts: set, names: List[str], fillable: bool = False) -> bool:
    """True when every file type a listing bullet names is really in the pack."""
    if _NEVER_SHIPPED.search(line):
        return False
    low = line.lower()
    for pattern, needed in _CLAIM_NEEDS:
        if re.search(pattern, low) and not (exts & needed):
            return False
    joined = " ".join(names)
    if re.search(r"\ba4\b", low) and "a4" not in joined:
        return False
    if re.search(r"\b(?:us\s+)?letter\s+(?:size|pdf|paper|format|version)\b|\bus\s+letter\b", low) and "letter" not in joined:
        return False
    if re.search(r"\bfillable\b", low) and not fillable:
        return False
    return True


def _pack_has_fillable_pdf(pack_dir: Path) -> bool:
    try:
        import support_kit
        return "pdf_fillable" in support_kit.detect_formats(pack_dir)
    except Exception:
        return False


def gumroad_whats_inside(pack_dir: Path, listing_info: Dict[str, str],
                         files: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """"What's inside" bullets: the listing's own bullets that the files back up, then file facts."""
    files = gumroad_pack_files(pack_dir) if files is None else files
    content = [item for item in files if not _is_guide_file(item["name"])]
    exts = {item["ext"] for item in content}
    names = [item["name"].lower() for item in files]
    fillable = _PDF_EXT in exts and _pack_has_fillable_pdf(pack_dir)
    bullets: List[str] = []
    seen = set()

    def add(text: str) -> None:
        text = " ".join(text.split()).strip(" -•*")
        key = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        if text and key and key not in seen and not check_money(text):
            seen.add(key)
            bullets.append(text)

    for raw in re.split(r"[\n\r]+", listing_info.get("what_you_get", "") or ""):
        line = raw.strip().lstrip("-•*0123456789.) ").strip()
        if 3 <= len(line) <= 160 and _claim_is_backed(line, exts, names, fillable):
            add(line)

    def count(group) -> int:
        return sum(1 for item in content if item["ext"] in group)

    pdfs = count({_PDF_EXT})
    if pdfs:
        pdf_names = " ".join(item["name"].lower() for item in content if item["ext"] == _PDF_EXT)
        sizes = "A4 and US Letter sizes" if ("a4" in pdf_names and "letter" in pdf_names) else ""
        kind = "fillable PDF" if fillable else "printable PDF"
        add(_plural(pdfs, f"{kind} file", f"{kind} files") + (f" ({sizes})" if sizes else ""))
    sheets = count(_SHEET_EXTS)
    if sheets:
        add(_plural(sheets, "spreadsheet workbook (.xlsx)", "spreadsheet workbooks (.xlsx)"))
    csvs = count({".csv"})
    if csvs:
        add(_plural(csvs, "CSV data file", "CSV data files"))
    docs = count(_DOC_EXTS)
    if docs:
        add(_plural(docs, "editable document (.docx)", "editable documents (.docx)"))
    images = count(_IMAGE_EXTS)
    if images:
        add(_plural(images, "image file", "image files"))
    pages = count({".html", ".htm"})
    if pages:
        add(_plural(pages, "HTML file", "HTML files"))
    if count({".ics"}):
        add(_plural(count({".ics"}), "calendar file (.ics)", "calendar files (.ics)"))
    notes = count(_TEXT_EXTS)
    if notes:
        add(_plural(notes, "text / Markdown file", "text / Markdown files"))
    said = " ".join(bullets).lower()
    if (any(name.startswith(("readme", "read-me", "read_me", "how-to", "how_to")) for name in names)
            and not re.search(r"read[\s-]?me|guide", said)):
        add("A read-me guide to get you started")
    if any(name.startswith(("license", "licence")) for name in names) and not re.search(r"licen[cs]e", said):
        add("Personal use licence")
    return bullets[:12]


def gumroad_formats(pack_dir: Path, files: List[Dict[str, Any]]) -> List[str]:
    """support_kit format ids for the BUYER files only (cover / preview images are not buyer files)."""
    content = [item for item in files if not _is_guide_file(item["name"])]
    exts = {item["ext"] for item in content}
    names = " ".join(item["name"].lower() for item in files)
    formats = set()
    if exts & (_SHEET_EXTS | {".csv"}) and "notion" not in names:
        formats.add("sheets")
    if _PDF_EXT in exts:
        formats.add("pdf_fillable" if _pack_has_fillable_pdf(pack_dir) else "pdf")
    if exts & _DOC_EXTS:
        formats.add("resume" if re.search(r"resume|cover|cv", names) else "docx")
    if ".ics" in exts:
        formats.add("calendar")
    if exts & _IMAGE_EXTS:
        formats.add("images")
    if "notion" in names:
        formats.add("notion")
    if not formats and exts & _TEXT_EXTS:
        formats.add("text")
    return sorted(formats)


def _only_real_files(answer: str, files: List[Dict[str, Any]]) -> str:
    """Drop every sentence that names a file the buyer does not get."""
    real = {item["name"].lower() for item in files}
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", answer):
        named = re.findall(r"\b[\w-]+\.(?:md|txt|pdf|xlsx|csv|docx|png|jpg|html|ics|zip)\b", sentence, re.IGNORECASE)
        if not all(name.lower() in real for name in named):
            break                         # later sentences usually point back at the dropped one ("Read it ...")
        kept.append(sentence)
    return " ".join(kept).strip()


def gumroad_how_to_use(files: List[Dict[str, Any]]) -> List[str]:
    """Numbered steps that match the file types really in the pack."""
    content = [item for item in files if not _is_guide_file(item["name"])]
    exts = {item["ext"] for item in content}
    names = [item["name"].lower() for item in files]
    steps = ["Download the files from your Gumroad receipt or library."]
    if any(item["where"] == "zip" for item in files):
        steps.append("Unzip product.zip (double-click it on a computer).")
    if any(name.startswith(("readme", "read-me", "read_me")) for name in names):
        steps.append("Open the README first: it lists every file.")
    if _PDF_EXT in exts:
        steps.append("Open the PDF in any free PDF reader and print at 100% (\"Actual size\"), "
                     "or use it on a tablet with a PDF notes app.")
    if exts & _SHEET_EXTS:
        steps.append("Open the .xlsx file in your spreadsheet app, save your own copy and fill in the input cells.")
    if ".csv" in exts:
        steps.append("Import the .csv file into your spreadsheet or workspace app.")
    if exts & _DOC_EXTS:
        steps.append("Open the .docx file in your word processor and replace the sample text with your own.")
    if exts & {".html", ".htm"}:
        steps.append("Open the .html file in a browser, then copy it into your email or web tool.")
    if exts & _IMAGE_EXTS:
        steps.append("Print the image files at home or at a print shop, or use them on screen.")
    if ".ics" in exts:
        steps.append("Import the .ics file into your calendar app.")
    if exts & _TEXT_EXTS and not (exts - _TEXT_EXTS):
        steps.append("Open the text files in any editor and follow the notes inside.")
    steps.append("Keep the original download as a backup so you can start fresh any time.")
    return steps


def gumroad_file_list(files: List[Dict[str, Any]], limit: int = 15) -> List[str]:
    """Real file names for the "Files you get" block."""
    lines = [item["name"] for item in files[:limit]]
    if len(files) > limit:
        lines.append(f"and {len(files) - limit} more file(s)")
    return lines


def gumroad_faq(pack_dir: Path, files: List[Dict[str, Any]], limit: int = 5) -> List[Tuple[str, str]]:
    """Questions and answers for buyers: the pack's support/FAQ.md when the support kit made one,
    else the same answers worked out by support_kit from the real files, else two plain facts."""
    text = ""
    faq_file = Path(pack_dir) / "support" / "FAQ.md"
    try:
        if faq_file.is_file():
            text = faq_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        text = ""
    if not text:
        try:
            import support_kit
            text = support_kit._faq(support_kit._display_name(Path(pack_dir)),
                                    gumroad_formats(Path(pack_dir), files), [{"name": item["name"]} for item in files])
        except Exception:
            text = ""
    pairs: List[Tuple[str, str]] = []
    for block in re.split(r"(?m)^##\s+", text)[1:]:
        question, _, answer = block.partition("\n")
        question = question.strip()
        answer = " ".join(line.strip() for line in answer.strip().splitlines() if line.strip())
        if not question or question.lower().startswith(("what is in this pack", "which formats are included")):
            continue                      # the file list and What's inside have their own blocks
        answer = _only_real_files(answer, files)
        if not answer or check_money(question + " " + answer):
            continue
        pairs.append((question, trim_to_word_boundary(answer, 300)))
    if not pairs:
        pairs = [("Is this a physical product?", "No. It is a digital download; nothing is shipped."),
                 ("Is this for personal use?", "Yes, the files are for your own personal use.")]
    return pairs[:limit]


def gumroad_tags(raw_tags: str, product_type: str) -> List[str]:
    """Exactly 10 relevant tags when possible: the listing's tags without brand names, then tags for
    this product type. Lower case, no duplicates, no money, 30 characters at most."""
    chosen: List[str] = []
    seen = set()

    def add(tag: str) -> None:
        tag = re.sub(r"\s+", " ", re.sub(r"[#\"'`]+", "", tag.lower())).strip(" .,-")
        if not tag or count_graphemes(tag) > 30 or len(chosen) >= GUMROAD_TAG_COUNT:
            return
        tokens = set(re.findall(r"[a-z0-9-]+", tag))
        if tokens & GUMROAD_BRAND_WORDS or check_money(tag) or tag in seen:
            return
        seen.add(tag)
        chosen.append(tag)

    for tag in re.split(r"[,;\n]+", raw_tags or ""):
        add(tag)
    profile = GUMROAD_TYPES.get(product_type, GUMROAD_TYPES["digital_pack"])
    for tag in list(profile["tags"]) + GUMROAD_TYPES["digital_pack"]["tags"]:
        add(tag)
    return chosen


def gumroad_hook(title: str, description: str, product_type: str) -> Tuple[str, str]:
    """(hook line, rest of the description). The hook is the listing's own first sentence when it is a
    good length; otherwise a plain factual line from the title and the product type."""
    description = (description or "").strip()
    match = re.match(r"(.+?[.!?])(?:\s+|$)(.*)", description, re.DOTALL)
    first, rest = (match.group(1).strip(), match.group(2).strip()) if match else (description, "")
    first = " ".join(first.split())
    if 25 <= count_graphemes(first) <= GUMROAD_HOOK_LIMIT:
        return first, rest
    label = GUMROAD_TYPES.get(product_type, GUMROAD_TYPES["digital_pack"])["label"]
    hook = trim_to_word_boundary(f"{title} — a {label} you can download and use today.", GUMROAD_HOOK_LIMIT)
    return hook, description


def gumroad_copy(pack_dir: Path, listing_info: Dict[str, str]) -> Dict[str, Any]:
    """All parts of the Gumroad description, each built from the real pack."""
    pack_dir = Path(pack_dir)
    files = gumroad_pack_files(pack_dir)
    product_type = gumroad_product_type(pack_dir, listing_info, files)
    title = listing_info.get("title") or pack_dir.name
    hook, rest = gumroad_hook(title, listing_info.get("description", ""), product_type)
    return {
        "product_type": product_type,
        "hook": hook,
        "about": rest,
        "whats_inside": gumroad_whats_inside(pack_dir, listing_info, files),
        "who_for": list(GUMROAD_TYPES[product_type]["audience"]),
        "how_to_use": gumroad_how_to_use(files),
        "files": gumroad_file_list(files),
        "faq": gumroad_faq(pack_dir, files),
        "tags": gumroad_tags(listing_info.get("tags", ""), product_type),
    }


def gumroad_description(copy: Dict[str, Any], limit: int = 10000) -> str:
    """The long description text, hook first, ready to paste into Gumroad."""
    blocks: List[str] = [copy["hook"]]
    if copy["about"]:
        blocks.append(copy["about"])
    if copy["whats_inside"]:
        blocks.append("WHAT'S INSIDE\n" + "\n".join(f"- {line}" for line in copy["whats_inside"]))
    blocks.append("WHO IT IS FOR\n" + "\n".join(f"- {line}" for line in copy["who_for"]))
    blocks.append("HOW TO USE\n" + "\n".join(f"{index}. {step}" for index, step in enumerate(copy["how_to_use"], 1)))
    if copy["files"]:
        blocks.append(f"FILES YOU GET\n" + "\n".join(f"- {name}" for name in copy["files"]))
    if copy["faq"]:
        blocks.append("FAQ\n" + "\n\n".join(f"Q: {question}\nA: {answer}" for question, answer in copy["faq"]))
    blocks.append("Digital download. Nothing is shipped.")
    return trim_to_word_boundary("\n\n".join(blocks), limit)


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

    # Gumroad only (CCA-2): hook first line, What's inside from the real files, who it is for,
    # how to use, file list, FAQ and 10 brand-free tags. Other stores keep the plain listing text.
    if store_key == "gumroad":
        copy = gumroad_copy(pack_dir, listing_info)
        if not short_desc.strip():
            short_desc = trim_to_word_boundary(copy["hook"], short_limit)
        long_desc = gumroad_description(copy, desc_limit)
        tags_list = copy["tags"]
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

    money_hit = check_money(content)
    if money_hit:
        raise ValueError(f"Generated kit content contains forbidden money amount: {money_hit}")

    return content


# ---------------------------------------------------------------------------
# Core Builder: build(pack_dir)
# ---------------------------------------------------------------------------

def build(pack_dir: Union[Path, str]) -> Dict[str, Path]:
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

def discover_ready_packs(products_dir: Union[Path, str] = "exported_content/products") -> List[Path]:
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
