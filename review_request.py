#!/usr/bin/env python3
"""review_request.py — Buyer review request templates generator for ready upload packs.

For each ready pack, writes:
  exported_content/marketing/<pack>/review_request.md

Contains:
  - 3 polite message templates the OWNER can send BY HAND after a sale.
  - Structure per template:
      1. Polite thank you
      2. Clear 'how to use' / getting started tips based strictly on listing facts
      3. Gentle review ask (strictly no incentives, no discounts, no pressure, no fake urgency)
  - Both English and Tenglish versions for each of the 3 templates.
  - Standard placeholders: [BUYER NAME] and [STORE LINK].
  - Length constraint: Each template body (English and Tenglish) is strictly <= 600 characters.
  - Owner-facing guidance and section headers in Tenglish house style (Telugu sentence words + English nouns).
  - Facts strictly derived from the listing (files, format, what you get, title).
  - Safety rules:
      - NEVER sends or posts anything automatically.
      - No money amounts anywhere (no $, USD, INR, etc.).
      - No brand logos or brand names in generated content.
      - Deterministic outputs and atomic writes.
      - Output goes OUTSIDE pack folders into exported_content/marketing/<pack>/review_request.md.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import listing_fields

# Re-use money patterns from listing_fields
MONEY_PATTERNS = listing_fields.MONEY_PATTERNS

# Third-party brand patterns to scrub or sanitize
_BRAND_PATTERNS: List[Tuple[str, str]] = [
    (r"\bgoogle\s+sheets?\b", "spreadsheet"),
    (r"\bgoogle\s+docs?\b", "document"),
    (r"\bmicrosoft\s+excel\b", "spreadsheet"),
    (r"\bmicrosoft\s+word\b", "document"),
    (r"\bexcel\b", "spreadsheet"),
    (r"\bnotion\b", "notes app"),
    (r"\bcanva\b", "design tool"),
    (r"\betsy\b", "store"),
    (r"\bgumroad\b", "store"),
    (r"\btopmate\b", "store"),
    (r"\binstamojo\b", "store"),
    (r"\bpayhip\b", "store"),
    (r"\bamazon\b", "store"),
    (r"\bapple\s+numbers\b", "spreadsheet"),
    (r"\bgoodnotes\b", "notes app"),
    (r"\bipad\b", "tablet"),
    (r"\blinkedin\b", "profile"),
    (r"\bgoogle\b", "search"),
    (r"\bmicrosoft\b", "office"),
    (r"\bchatgpt\b", "AI"),
    (r"\bopenai\b", "AI"),
]


def check_for_money(text: str) -> Optional[str]:
    """Return matching money string if found in text, else None."""
    for pat in MONEY_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def sanitize_text(text: str) -> str:
    """Remove money mentions and scrub brand names."""
    for pat in MONEY_PATTERNS:
        text = pat.sub("", text)
    for pattern, replacement in _BRAND_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text.strip()


def parse_listing(pack_dir: Path | str) -> Dict[str, Any]:
    """Parse listing.txt and inspect files inside a pack directory."""
    pack_dir = Path(pack_dir)
    listing_path = pack_dir / "listing.txt"
    if not listing_path.is_file():
        # Fallback to empty if not found
        raw_text = ""
    else:
        raw_text = listing_path.read_text(encoding="utf-8")

    title = listing_fields._section(raw_text, "Title")
    desc = listing_fields._section(raw_text, "Description")
    what_you_get = listing_fields._section(raw_text, "What you get")
    tags_str = listing_fields._section(raw_text, "Tags")

    if not title:
        # Fallback to pack directory name
        title = pack_dir.name.replace("-", " ").replace("_", " ").title()

    tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

    # Parse items in "What you get"
    items: List[str] = []
    if what_you_get:
        for line in what_you_get.splitlines():
            line = line.strip()
            if line.startswith(("-", "*", "•")):
                cleaned = line.lstrip("-*• ").strip()
                if cleaned:
                    items.append(cleaned)
            elif line:
                items.append(line)

    # Inspect pack directory files
    pack_files: List[str] = []
    has_pdf = False
    has_xlsx = False
    has_zip = False
    if pack_dir.is_dir():
        for f in sorted(pack_dir.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                pack_files.append(f.name)
                ext = f.suffix.lower()
                if ext == ".pdf":
                    has_pdf = True
                elif ext in [".xlsx", ".xls", ".csv"]:
                    has_xlsx = True
                elif ext == ".zip":
                    has_zip = True

    # Check if what_you_get mentions pdf or spreadsheet
    lower_desc = (desc + " " + what_you_get).lower()
    if "pdf" in lower_desc:
        has_pdf = True
    if "spreadsheet" in lower_desc or "excel" in lower_desc or "xlsx" in lower_desc or re.search(r"\bspreadsheet\b", lower_desc):
        has_xlsx = True

    return {
        "title": sanitize_text(title),
        "description": sanitize_text(desc),
        "what_you_get": [sanitize_text(it) for it in items],
        "tags": [sanitize_text(t) for t in tags],
        "files": pack_files,
        "has_pdf": has_pdf,
        "has_xlsx": has_xlsx,
        "has_zip": has_zip,
        "pack_name": pack_dir.name,
    }


def _shorten(text: str, max_len: int = 50) -> str:
    """Shorten string cleanly on word boundary."""
    text = text.strip()
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 3]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",.- ") + "..."


def generate_templates(listing_info: Dict[str, Any]) -> List[Dict[str, str]]:
    """Generate 3 polite review request templates in English and Tenglish.

    Each template strictly includes:
      - Polite thank you
      - Practical 'how to use' tip derived from listing facts
      - Gentle review ask (no incentives, no pressure, no fake urgency)
      - Placeholders: [BUYER NAME], [STORE LINK]
      - Length constraint: <= 600 characters per version.

    Template Styles:
      1. Quick & Friendly (Day 1 / initial download confirmation)
      2. Practical & Focused on Routine (Day 3-5 / getting results)
      3. Calm & Open Feedback (Day 7 / suggestions & experience)
    """
    raw_title = listing_info.get("title", "digital planner")
    short_title = _shorten(raw_title, 38)
    has_pdf = listing_info.get("has_pdf", False)
    has_xlsx = listing_info.get("has_xlsx", False)
    items = listing_info.get("what_you_get", [])

    # Practical usage tip based on listing format
    if has_pdf and has_xlsx:
        en_usage_1 = "Unzip the files to find your printable PDF and spreadsheet template."
        te_usage_1 = "Files unzip chesi mee printable PDF mariyu spreadsheet open cheyandi."
        en_usage_2 = "Try filling in your first daily entry or print the pages that fit your routine."
        te_usage_2 = "Mee routine ki saripoye pages print cheyandi leda entry fill cheyandi."
        en_usage_3 = "Keep the PDF handy for daily notes and use the spreadsheet to track your progress."
        te_usage_3 = "Daily notes kosam PDF mariyu progress track cheyataniki sheet use cheyandi."
    elif has_xlsx:
        en_usage_1 = "Unzip the package and open your spreadsheet template to get started."
        te_usage_1 = "Files unzip chesi mee spreadsheet template open cheyandi."
        en_usage_2 = "Enter your starting goals or data in the first tab to begin organizing."
        te_usage_2 = "Modati tab lo mee goals leda data enter chesi start cheyandi."
        en_usage_3 = "Save a clean backup copy before editing so you can reuse the sheets anytime."
        te_usage_3 = "Sheets anytime malli vadadaniki oka clean backup copy save cheskondi."
    elif has_pdf:
        en_usage_1 = "Unzip the files to view your ready-to-print PDF pages on any device."
        te_usage_1 = "Files unzip chesi ready-to-print PDF pages ni device lo open cheyandi."
        en_usage_2 = "Print the pages at 100% scale or use them directly in your favourite PDF reader."
        te_usage_2 = "Pages ni 100% scale lo print cheyandi leda PDF reader lo direct ga vadandi."
        en_usage_3 = "Pick one section to focus on today and take it at your own steady pace."
        te_usage_3 = "Mee own pace lo oka section select cheskoni calm ga start cheyandi."
    else:
        en_usage_1 = "Unzip your downloaded folder to access all included files and guides."
        te_usage_1 = "Download ayina folder unzip chesi files anni access cheyandi."
        en_usage_2 = "Start with the main file to set up your workflow step by step."
        te_usage_2 = "Step by step setup kosam main file tho modalu pettandi."
        en_usage_3 = "Keep the templates saved in an easy-to-find folder for quick daily access."
        te_usage_3 = "Daily quick access kosam templates ni oka safe folder lo save cheskondi."

    # Highlight item if available
    item_note = f" ({_shorten(items[0], 25)})" if items else ""

    # Template 1: Quick & Friendly (Day 1 / delivery check)
    t1_en = (
        f"Hi [BUYER NAME], thank you so much for choosing {short_title}{item_note}! "
        f"{en_usage_1} If you have any trouble opening the files, reply anytime and I will happily help. "
        f"Once you have had a moment to explore, sharing a quick review at [STORE LINK] would mean the world to our small shop. "
        f"Wishing you a wonderful day!"
    )
    t1_te = (
        f"Namaste [BUYER NAME], {short_title} purchase chesinanduku chala thanks! "
        f"{te_usage_1} Files open cheyadam lo edaina doubt unte naku message cheyandi, nenu help chestanu. "
        f"Kudirinappudu [STORE LINK] lo oka small review share chesthe ma shop ki chala support ga untundi. "
        f"Have a great day!"
    )

    # Template 2: Practical & Focused on Routine (Day 3-5)
    t2_en = (
        f"Hello [BUYER NAME], I hope you are enjoying your {short_title}! "
        f"{en_usage_2} How to get the most from it: keep it simple and focus on what helps you best. "
        f"If you find it helpful, please consider leaving an honest review at [STORE LINK]. "
        f"Your genuine feedback helps others discover useful resources, with zero pressure. "
        f"Thank you again for your kind support!"
    )
    t2_te = (
        f"Hello [BUYER NAME], {short_title} meeku nachutundani aashistunnanu! "
        f"{te_usage_2} Simple ga start chesi meeku useful ga unna parts vadandi. "
        f"Meeku ee product help ayithe, [STORE LINK] lo mee honest review ivvandi. "
        f"Mee genuine feedback itarulaki guide chestundi, no pressure at all. "
        f"Malli meeku chala thanks!"
    )

    # Template 3: Calm & Open Feedback (Day 7)
    t3_en = (
        f"Hi [BUYER NAME], thank you once again for supporting our shop with {short_title}. "
        f"{en_usage_3} I would love to hear how it fits into your workflow. "
        f"Whenever you have a spare minute, leaving a kind review at [STORE LINK] is deeply appreciated. "
        f"If there is anything we can improve, please feel free to message me directly. "
        f"Warm regards!"
    )
    t3_te = (
        f"Namaste [BUYER NAME], {short_title} support chesinanduku malli heartful thanks. "
        f"{te_usage_3} Mee daily workflow lo idi ela help aindo thelusukovalani undi. "
        f"Time unnapudu [STORE LINK] meeda oka chinna review raste chala santoshistamu. "
        f"Emaina improvements unte kuda direct ga message cheyavachhu. "
        f"All the best!"
    )

    templates = [
        {
            "id": "template_1",
            "name": "Template 1: Quick & Friendly (Day 1 / First Download)",
            "timing_tenglish": "Order complete ayina ventane leda Day 1 send cheyandi.",
            "english": t1_en.strip(),
            "tenglish": t1_te.strip(),
        },
        {
            "id": "template_2",
            "name": "Template 2: Practical & Routine Focused (Day 3–5)",
            "timing_tenglish": "Buyer files use chesina konni rojula taruvatha (Day 3–5) pampandi.",
            "english": t2_en.strip(),
            "tenglish": t2_te.strip(),
        },
        {
            "id": "template_3",
            "name": "Template 3: Calm & Gentle Feedback (Day 7+)",
            "timing_tenglish": "One week taruvatha genuine feedback mariyu improvements kosam pampandi.",
            "english": t3_en.strip(),
            "tenglish": t3_te.strip(),
        },
    ]

    # Validate all lengths <= 600
    for t in templates:
        assert len(t["english"]) <= 600, f"Template {t['id']} English too long: {len(t['english'])}"
        assert len(t["tenglish"]) <= 600, f"Template {t['id']} Tenglish too long: {len(t['tenglish'])}"

    return templates


def build_markdown_document(listing_info: Dict[str, Any], templates: List[Dict[str, Any]]) -> str:
    """Build the final review_request.md document in Tenglish house style."""
    pack_name = listing_info.get("pack_name", "pack")
    title = listing_info.get("title", "Product")

    lines: List[str] = [
        f"# Buyer Review Request Templates — {title}",
        "",
        "> **Owner Guide (Tenglish House Style):**",
        "> Ee templates ni robot automate cheyadu mariyu automatically send cheyadu.",
        "> Owner direct ga hand tho customer message box leda email lo copy-paste chesi pampali.",
        "> Sale jarigina taruvatha right timing chusukoni okka message maatrame pampandi.",
        "> Placeholders `[BUYER NAME]` ni buyer peru tho, mariyu `[STORE LINK]` ni mee store/product page link tho replace cheyandi.",
        "> No discounts, no gift promises, no pressure, and no fake urgency undakudadu.",
        "",
        "## Guidelines for the Owner (Niyamaalu)",
        "- **Hand-sent only:** Robot emails send cheyadu; owner manual ga contact chestharu.",
        "- **Single message:** Okka customer ki okate review message pampali (no spamming).",
        "- **No incentives:** Discounts, coupons, gifts, leda freebies offer cheyadam strict ga prohibited.",
        "- **No pressure:** Customer review ivvakapoyina parvaledu; polite ga request cheyali.",
        "- **Accurate facts:** Listing lo unna files mariyu formats gurinchi maatrame matladali.",
        "",
        "---",
        "",
    ]

    for idx, tmpl in enumerate(templates, start=1):
        lines.extend([
            f"### {tmpl['name']}",
            f"**Timing:** {tmpl['timing_tenglish']}",
            "",
            "#### English Version (Char count: " + str(len(tmpl['english'])) + "/600):",
            "```text",
            tmpl["english"],
            "```",
            "",
            "#### Tenglish Version (Char count: " + str(len(tmpl['tenglish'])) + "/600):",
            "```text",
            tmpl["tenglish"],
            "```",
            "",
            "---",
            "",
        ])

    lines.extend([
        "## Ready-to-use Checklist for Owner",
        "- [ ] Buyer peru tho `[BUYER NAME]` placeholder replace chesara?",
        "- [ ] Store review URL tho `[STORE LINK]` placeholder replace chesara?",
        "- [ ] Single message maatrame copy chesi send chesara?",
        "- [ ] No spam, no discounts, no extra pressure unchara?",
        "",
        "*Generated deterministically for Chilli Gavva robot. Never sends automatically.*",
    ])

    return "\n".join(lines).strip() + "\n"


def discover_ready_packs(base_products_dir: Path | str = "exported_content/products") -> List[Path]:
    """Find all ready upload packs under base_products_dir/upload or base_products_dir."""
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


def write_review_request(
    pack_dir: Path | str,
    marketing_base_dir: Path | str = "exported_content/marketing",
) -> Path:
    """Generate review_request.md for a pack and write atomically outside pack folder."""
    pack_path = Path(pack_dir)
    listing_info = parse_listing(pack_path)
    templates = generate_templates(listing_info)
    md_content = build_markdown_document(listing_info, templates)

    # Sanity money check
    money_match = check_for_money(md_content)
    if money_match:
        raise ValueError(f"Prohibited money pattern found in review_request.md: {money_match}")

    out_dir = Path(marketing_base_dir) / pack_path.name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "review_request.md"

    # Atomic write via temporary file
    temp_fd, temp_path = tempfile.mkstemp(
        prefix="review_request_", suffix=".tmp", dir=str(out_dir)
    )
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(md_content)
        os.replace(temp_path, out_file)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

    return out_file


def process_all_ready_packs(
    products_dir: Path | str = "exported_content/products",
    marketing_dir: Path | str = "exported_content/marketing",
) -> List[Path]:
    """Generate review_request.md for all ready packs."""
    packs = discover_ready_packs(products_dir)
    results: List[Path] = []
    for pack in packs:
        out = write_review_request(pack, marketing_dir)
        results.append(out)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate buyer review request templates (review_request.md) for ready upload packs."
    )
    parser.add_argument("--pack", help="Specific pack directory or ID to generate templates for.")
    parser.add_argument(
        "--all", action="store_true", help="Generate review requests for all ready packs."
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

        out = write_review_request(pack_path, args.marketing_dir)
        print(f"Generated {out}")
        return 0

    written = process_all_ready_packs(args.products_dir, args.marketing_dir)
    print(f"Generated {len(written)} review request file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
