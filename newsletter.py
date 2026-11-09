#!/usr/bin/env python3
"""newsletter.py — Weekly email newsletter draft generator (Markdown + plain text).

Creates draft email newsletters from:
  - This week's ready upload packs (exported_content/products/upload/<pack>/ or products/<pack>/)
  - Blog posts (exported_content/marketing/<pack>/blog.md)
  - Seasonal collections / gift guides (exported_content/marketing/collections/*.md)

Generates:
  exported_content/marketing/newsletter/<YYYY-WW>.md
  exported_content/marketing/newsletter/<YYYY-WW>.txt

Requirements & Safety:
  - 3 subject line options (each <= 60 characters)
  - 1 preview text line (concise summary <= 150 characters)
  - 3–5 short content sections with [STORE LINK] placeholders
  - Unsubscribe line placeholder
  - Facts only from listing/pack/blog/collection files, no unverified promises
  - No money amounts ($ / USD / INR etc.) anywhere
  - No third-party brand logos or names
  - Nothing posts, publishes, or emails automatically (draft files only)
  - Outputs strictly OUTSIDE private pack folders
  - Atomic writes (via temporary file rename) and deterministic output
  - Owner-facing text in Tenglish house style (Telugu sentence words + English nouns)
  - Guard external libraries (CI installs ONLY Pillow)
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

# Guard optional libraries if any ever needed
try:
    from PIL import Image  # type: ignore # noqa: F401
except ImportError:
    pass

import listing_fields

# Disallowed money pattern: comprehensive and robust word-boundary check
MONEY_PATTERN = re.compile(
    r"(?:\$[\d,]+(?:\.\d{1,2})?\b|"
    r"[\d,]+(?:\.\d{1,2})?\s*(?:usd|eur|gbp|inr|rs\.?|rupees?|dollars?|cents?|paise)\b|"
    r"(?:usd|eur|gbp|inr|\brs\.?|rupees?|dollars?|cents?|paise)\s*[\d,]+(?:\.\d{1,2})?\b|"
    r"[₹€£¥]\s*[\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)

# Disallowed brand patterns
BRAND_PATTERNS = [
    (r"\bgoogle\s+sheets?\b", "spreadsheet"),
    (r"\bgoogle\s+docs?\b", "document"),
    (r"\bmicrosoft\s+excel\b", "spreadsheet"),
    (r"\bmicrosoft\s+word\b", "document"),
    (r"\bexcel\b", "spreadsheet"),
    (r"\bnotion\b", ""),
    (r"\bcanva\b", ""),
    (r"\betsy\b", ""),
    (r"\bgumroad\b", ""),
    (r"\bpayhip\b", ""),
    (r"\bamazon\b", ""),
    (r"\bapple\s+numbers\b", "spreadsheet"),
    (r"\bgoodnotes\b", "notes app"),
    (r"\bipad\b", "tablet"),
    (r"\blinkedin\b", "professional profile"),
    (r"\bgoogle\b", ""),
    (r"\bmicrosoft\b", ""),
    (r"\bchatgpt\b", ""),
    (r"\bopenai\b", ""),
]

UNSUBSCRIBE_PLACEHOLDER = "You are receiving this email because you subscribed to our digital organizer updates. [UNSUBSCRIBE LINK]"


def sanitize_text(text: str) -> str:
    """Strip disallowed brand names and replace with generic alternatives."""
    cleaned = text
    for pattern, replacement in BRAND_PATTERNS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    # Ensure money amounts are not present
    cleaned = MONEY_PATTERN.sub("", cleaned)
    # Clean up double spaces or dangling punctuation
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" +([.,!?;:])", r"\1", cleaned)
    return cleaned.strip()


def check_for_money(text: str) -> Optional[str]:
    """Check if any money amounts exist in text. Returns matched string or None."""
    m = MONEY_PATTERN.search(text)
    if m:
        return m.group(0)
    return None


def get_week_identifier(target_date: Optional[date] = None) -> str:
    """Format week identifier as YYYY-WW (e.g., 2026-W45)."""
    if target_date is None:
        target_date = date.today()
    iso_year, iso_week, _ = target_date.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


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


def extract_section_text(text: str, label: str) -> str:
    """Robust extraction of section value by label from listing text."""
    pattern = re.compile(rf"^{re.escape(label)}:\s*(.*?)(?=^[A-Za-z][A-Za-z /_-]*:|\Z)", re.MULTILINE | re.DOTALL)
    m = pattern.search(text)
    if not m:
        return ""
    return m.group(1).strip()


def parse_pack_listing(pack_dir: Path) -> Dict[str, Any]:
    """Extract listing information from a pack's listing.txt."""
    listing_path = pack_dir / "listing.txt"
    title = pack_dir.name
    desc = ""
    tags: List[str] = []
    what_you_get = ""

    if listing_path.is_file():
        text = listing_path.read_text(encoding="utf-8")
        extracted_title = extract_section_text(text, "Title")
        if extracted_title:
            title = extracted_title
        desc = extract_section_text(text, "Description")
        raw_tags = extract_section_text(text, "Tags")
        if raw_tags:
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        what_you_get = extract_section_text(text, "What you get")

    # Clean short title
    short_title = listing_fields._cut_title(title, 50)
    short_title = sanitize_text(short_title)
    desc = sanitize_text(desc)
    what_you_get = sanitize_text(what_you_get)
    clean_tags = [sanitize_text(t) for t in tags if sanitize_text(t)]

    return {
        "pack_id": pack_dir.name,
        "pack_dir": pack_dir,
        "title": title,
        "short_title": short_title,
        "description": desc,
        "tags": clean_tags,
        "what_you_get": what_you_get,
    }


def parse_blog_posts(marketing_dir: Path | str = "exported_content/marketing") -> List[Dict[str, Any]]:
    """Scan marketing_dir/<pack>/blog.md files and extract titles and summaries."""
    m_dir = Path(marketing_dir)
    if not m_dir.is_dir():
        return []

    blog_posts: List[Dict[str, Any]] = []
    for sub in sorted(m_dir.iterdir()):
        if not sub.is_dir() or sub.name in {"collections", "newsletter", "catalog"}:
            continue
        blog_file = sub / "blog.md"
        if blog_file.is_file():
            try:
                content = blog_file.read_text(encoding="utf-8")
                # Extract H1 title
                h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
                title = h1_match.group(1).strip() if h1_match else sub.name
                title = sanitize_text(title)

                # Extract first meaningful paragraph (skipping headers)
                summary = ""
                paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
                for p in paragraphs:
                    if not p.startswith("#") and len(p.split()) >= 3:
                        summary = p
                        break
                summary = sanitize_text(summary)
                blog_posts.append({
                    "pack_id": sub.name,
                    "file_path": blog_file,
                    "title": title,
                    "summary": summary,
                })
            except Exception:
                continue

    return blog_posts


def parse_collections(marketing_dir: Path | str = "exported_content/marketing") -> List[Dict[str, Any]]:
    """Scan marketing_dir/collections/*.md files and extract title, intro, and featured packs."""
    coll_dir = Path(marketing_dir) / "collections"
    if not coll_dir.is_dir():
        return []

    collections: List[Dict[str, Any]] = []
    for md_file in sorted(coll_dir.glob("*.md")):
        if md_file.name.startswith("."):
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
            h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            title = h1_match.group(1).strip() if h1_match else md_file.stem
            title = sanitize_text(title)

            # Intro paragraph: first paragraph after H1
            intro = ""
            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
            for p in paragraphs:
                if not p.startswith("#") and len(p.split()) >= 3:
                    intro = p
                    break
            intro = sanitize_text(intro)

            # Featured items
            featured = re.findall(r"-\s+\*\*(.+?)\*\*", content)
            featured = [sanitize_text(f) for f in featured]

            collections.append({
                "slug": md_file.stem,
                "file_path": md_file,
                "title": title,
                "intro": intro,
                "featured": featured,
            })
        except Exception:
            continue

    return collections


def build_subject_lines(
    packs: List[Dict[str, Any]],
    collections: List[Dict[str, Any]],
    week_str: str,
) -> List[str]:
    """Generate exactly 3 subject line options (each <= 60 chars)."""
    options: List[str] = []

    # Lead pack or collection title
    lead_pack_title = packs[0]["short_title"] if packs else ""
    lead_coll_title = collections[0]["title"] if collections else ""

    # Option 1: Direct / Benefit-oriented
    if lead_pack_title:
        cand1 = f"New: {lead_pack_title}"
        if len(cand1) > 60:
            cand1 = cand1[:57] + "..."
    elif lead_coll_title:
        cand1 = f"Curated: {lead_coll_title}"
        if len(cand1) > 60:
            cand1 = cand1[:57] + "..."
    else:
        cand1 = f"Weekly Digital Tools & Planning Essentials"
    options.append(cand1)

    # Option 2: Weekly / Fresh resources
    if lead_coll_title and lead_pack_title:
        cand2 = f"{lead_coll_title} + Fresh Guides"
        if len(cand2) > 60:
            cand2 = f"Fresh Releases: {lead_pack_title}"
            if len(cand2) > 60:
                cand2 = cand2[:57] + "..."
    elif lead_pack_title:
        cand2 = f"This Week's Digital Planners & Templates"
        if len(cand2) > 60:
            cand2 = cand2[:57] + "..."
    else:
        cand2 = f"Your Weekly Productivity & Organization Edit"
    options.append(cand2)

    # Option 3: Curious / Practical Question or Action
    if packs:
        tag_hint = packs[0]["tags"][0] if packs[0]["tags"] else "organization"
        cand3 = f"Ready to simplify your {tag_hint} routines?"
        if len(cand3) > 60:
            cand3 = f"Organize your week with simple printable guides"
    else:
        cand3 = f"Simple digital guides to streamline your daily routines"
    if len(cand3) > 60:
        cand3 = cand3[:57] + "..."
    options.append(cand3)

    # Sanity checks: strictly 3 options, <= 60 chars each, no money
    cleaned_options: List[str] = []
    for opt in options[:3]:
        opt_clean = sanitize_text(opt)
        if len(opt_clean) > 60:
            opt_clean = opt_clean[:57].rstrip() + "..."
        cleaned_options.append(opt_clean)

    return cleaned_options


def build_preview_text(
    packs: List[Dict[str, Any]],
    collections: List[Dict[str, Any]],
    blogs: List[Dict[str, Any]],
) -> str:
    """Generate preview text line (concise summary <= 150 chars)."""
    parts: List[str] = []
    if packs:
        pack_names = ", ".join([p["short_title"] for p in packs[:2]])
        parts.append(f"Explore new releases: {pack_names}")
    if collections:
        parts.append(f"Seasonal guide: {collections[0]['title']}")
    elif blogs:
        parts.append(f"Read: {blogs[0]['title']}")

    if not parts:
        preview = "Browse this week's digital planners, templates, and helpful guides."
    else:
        preview = ". ".join(parts) + "."

    preview = sanitize_text(preview)
    if len(preview) > 150:
        preview = preview[:147].rstrip() + "..."
    return preview


def build_sections(
    packs: List[Dict[str, Any]],
    blogs: List[Dict[str, Any]],
    collections: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Build between 3 and 5 short content sections with [STORE LINK] placeholders."""
    sections: List[Dict[str, str]] = []

    # Section 1: Welcome & Highlights (Always present)
    welcome_title = "Welcome to This Week's Edition"
    welcome_body_parts = [
        "Welcome to our weekly planning roundup! Here are the newest digital tools, "
        "printable guides, and thoughtful resources designed to simplify your daily workflow."
    ]
    if packs:
        welcome_body_parts.append(
            f"This week we are highlighting {len(packs)} ready organizer packs crafted for immediate daily use."
        )
    welcome_body_parts.append("Explore our complete catalog and find the right fit for your routine: [STORE LINK]")
    sections.append({
        "title": welcome_title,
        "content": " ".join(welcome_body_parts),
    })

    # Section 2: Featured Ready Digital Packs
    if packs:
        pack_items: List[str] = []
        for p in packs[:3]:
            bullets = ""
            if p["what_you_get"]:
                lines = [line.strip("- *") for line in p["what_you_get"].split("\n") if line.strip()][:2]
                if lines:
                    bullets = " (" + "; ".join(lines) + ")"
            pack_items.append(f"- **{p['short_title']}**: {p['description']}{bullets} — [STORE LINK]")

        sections.append({
            "title": "Fresh Digital Releases",
            "content": "Take a look at our latest digital and printable releases:\n\n" + "\n".join(pack_items),
        })

    # Section 3: Seasonal Collection Feature (if available)
    if collections:
        coll = collections[0]
        coll_lines = [coll["intro"] if coll["intro"] else "Check out our curated seasonal collection."]
        if coll["featured"]:
            coll_lines.append("\nFeatured essentials in this collection:")
            for item in coll["featured"][:3]:
                coll_lines.append(f"- {item}")
        coll_lines.append("\nView the seasonal guide and featured printables here: [STORE LINK]")
        sections.append({
            "title": f"Seasonal Spotlight: {coll['title']}",
            "content": "\n".join(coll_lines),
        })

    # Section 4: Deep Dive / Article Feature (if available)
    if blogs:
        blog = blogs[0]
        blog_summary = blog["summary"] if blog["summary"] else "Discover practical planning tips and helpful strategies."
        sections.append({
            "title": f"From the Library: {blog['title']}",
            "content": f"{blog_summary}\n\nRead the full guide and explore companion organizers: [STORE LINK]",
        })

    # Section 5 / Fallback: Quick Productivity Tip or Next Steps (to guarantee 3–5 sections)
    if len(sections) < 3:
        sections.append({
            "title": "Weekly Planning Tip",
            "content": (
                "Set aside fifteen minutes at the beginning of each week to review your core priorities. "
                "Writing your focus tasks down in an intentional layout keeps your routines calm and manageable. "
                "Find dedicated printable schedules to support your morning and evening review: [STORE LINK]"
            ),
        })

    if len(sections) < 3:
        sections.append({
            "title": "Digital Workspace Organization",
            "content": (
                "Keep your file templates organized in dedicated folders for quick access. "
                "Print only the sheets you need for the week ahead to maintain a distraction-free desk. "
                "Browse our minimalist daily organizers: [STORE LINK]"
            ),
        })

    # Ensure max 5 sections
    sections = sections[:5]
    return sections


def generate_newsletter_markdown(
    subject_lines: List[str],
    preview_text: str,
    sections: List[Dict[str, str]],
    week_str: str,
) -> str:
    """Format newsletter as Markdown document."""
    lines: List[str] = []

    lines.append(f"# Weekly Newsletter Draft — {week_str}")
    lines.append("")
    lines.append("## Subject Line Options")
    lines.append("")
    for i, opt in enumerate(subject_lines, start=1):
        lines.append(f"- Option {i}: {opt}")
    lines.append("")
    lines.append("## Preview Text")
    lines.append("")
    lines.append(preview_text)
    lines.append("")
    lines.append("---")
    lines.append("")

    for sec in sections:
        lines.append(f"## {sec['title']}")
        lines.append("")
        lines.append(sec["content"].strip())
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(UNSUBSCRIBE_PLACEHOLDER)
    lines.append("")

    content = "\n".join(lines)
    return content


def generate_newsletter_plaintext(
    subject_lines: List[str],
    preview_text: str,
    sections: List[Dict[str, str]],
    week_str: str,
) -> str:
    """Format newsletter as clean plain text document."""
    lines: List[str] = []

    lines.append(f"WEEKLY NEWSLETTER DRAFT — {week_str}")
    lines.append("=" * 40)
    lines.append("")
    lines.append("SUBJECT LINE OPTIONS:")
    for i, opt in enumerate(subject_lines, start=1):
        lines.append(f"  Option {i}: {opt}")
    lines.append("")
    lines.append(f"PREVIEW TEXT: {preview_text}")
    lines.append("")
    lines.append("-" * 40)
    lines.append("")

    for sec in sections:
        lines.append(sec["title"].upper())
        lines.append("-" * len(sec["title"]))
        # Convert any markdown formatting in content to plain text
        cleaned_body = re.sub(r"\*\*(.+?)\*\*", r"\1", sec["content"])
        cleaned_body = re.sub(r"`(.+?)`", r"\1", cleaned_body)
        lines.append(cleaned_body.strip())
        lines.append("")

    lines.append("-" * 40)
    lines.append("")
    lines.append(UNSUBSCRIBE_PLACEHOLDER)
    lines.append("")

    content = "\n".join(lines)
    return content


def generate_tenglish_summary(
    week_str: str,
    packs_count: int,
    blogs_count: int,
    colls_count: int,
) -> str:
    """Format owner report in Tenglish house style."""
    return (
        f"Ee vaaram ({week_str}) newsletter draft ready ayyindi: "
        f"{packs_count} packs, {blogs_count} blog posts, {colls_count} collections tho. "
        f"Markdown mariyu plain text files save chesam."
    )


def make_weekly_newsletter(
    products_dir: Path | str = "exported_content/products",
    marketing_dir: Path | str = "exported_content/marketing",
    target_date: Optional[date] = None,
    week_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate both markdown and plain text newsletter drafts atomically."""
    prod_path = Path(products_dir)
    mkt_path = Path(marketing_dir)

    week_str = week_override if week_override else get_week_identifier(target_date)

    # Discover packs, blogs, and collections
    raw_packs = discover_ready_packs(prod_path)
    packs = [parse_pack_listing(p) for p in raw_packs]
    blogs = parse_blog_posts(mkt_path)
    collections = parse_collections(mkt_path)

    subject_lines = build_subject_lines(packs, collections, week_str)
    preview_text = build_preview_text(packs, collections, blogs)
    sections = build_sections(packs, blogs, collections)

    md_content = generate_newsletter_markdown(subject_lines, preview_text, sections, week_str)
    txt_content = generate_newsletter_plaintext(subject_lines, preview_text, sections, week_str)

    # Assert no money in contents
    money_md = check_for_money(md_content)
    assert not money_md, f"Disallowed money amount in newsletter markdown: {money_md}"
    money_txt = check_for_money(txt_content)
    assert not money_txt, f"Disallowed money amount in newsletter text: {money_txt}"

    # Target directory
    out_dir = mkt_path / "newsletter"
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / f"{week_str}.md"
    txt_path = out_dir / f"{week_str}.txt"

    # Atomic write for markdown
    fd_md, tmp_md = tempfile.mkstemp(dir=out_dir, prefix=f".nl_{week_str}_md_", suffix=".tmp")
    with os.fdopen(fd_md, "w", encoding="utf-8") as f:
        f.write(md_content)
    os.replace(tmp_md, md_path)

    # Atomic write for plain text
    fd_txt, tmp_txt = tempfile.mkstemp(dir=out_dir, prefix=f".nl_{week_str}_txt_", suffix=".tmp")
    with os.fdopen(fd_txt, "w", encoding="utf-8") as f:
        f.write(txt_content)
    os.replace(tmp_txt, txt_path)

    tenglish_summary = generate_tenglish_summary(
        week_str=week_str,
        packs_count=len(packs),
        blogs_count=len(blogs),
        colls_count=len(collections),
    )

    return {
        "week": week_str,
        "md_path": md_path,
        "txt_path": txt_path,
        "subject_lines": subject_lines,
        "preview_text": preview_text,
        "sections_count": len(sections),
        "packs_count": len(packs),
        "blogs_count": len(blogs),
        "collections_count": len(collections),
        "tenglish_summary": tenglish_summary,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Weekly email newsletter draft generator (Markdown + plain text)."
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
    parser.add_argument(
        "--date",
        default=None,
        help="Target date in YYYY-MM-DD format (defaults to today).",
    )
    parser.add_argument(
        "--iso-week",
        default=None,
        help="Week override in YYYY-WW format (e.g. 2026-W45).",
    )
    parser.add_argument(
        "--print",
        dest="print_output",
        action="store_true",
        help="Print the generated plain text draft to stdout.",
    )

    args = parser.parse_args(argv)

    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: Invalid date format {args.date!r}, expected YYYY-MM-DD.", file=sys.stderr)
            return 1

    try:
        res = make_weekly_newsletter(
            products_dir=args.products_dir,
            marketing_dir=args.marketing_dir,
            target_date=target_date,
            week_override=args.iso_week,
        )
        print(f"Newsletter drafted for week {res['week']}:")
        print(f"  Markdown: {res['md_path']}")
        print(f"  Plain text: {res['txt_path']}")
        print(f"Owner summary: {res['tenglish_summary']}")

        if args.print_output:
            print("\n" + res["txt_path"].read_text(encoding="utf-8"))

        return 0
    except Exception as e:
        print(f"Error generating newsletter: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
