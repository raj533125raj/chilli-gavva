#!/usr/bin/env python3
"""listing_fields.py — copy-paste helper for listing products by hand.

Usage: python3 listing_fields.py <upload_pack_folder>

Reads <upload_pack_folder>/listing.txt and prints, clearly separated,
ready-to-copy Gumroad and Topmate listing blocks.

Refuses (exit 1) if listing.txt is missing or contains a money amount.
"""

from __future__ import annotations

import os
import re
import sys
import zipfile


_CURRENCY_CODES = r"USD|INR|EUR|GBP|JPY|CAD|AUD|NZD|CHF|CNY|RMB|HKD|SGD|AED|SAR|KRW|RUB|BRL|MXN|ZAR|SEK|NOK|DKK|Rs"
_MONEY_NUMBER = r"[-+]?\s*(?:\d[\d,.]*|\.\d+)"
MONEY_PATTERNS = [
    re.compile(r"(?:[$₹€£¥₩₽]\s*" + _MONEY_NUMBER
               + r"|(?<![A-Za-z])(?:" + _CURRENCY_CODES + r")\.?(?![A-Za-z])\s*" + _MONEY_NUMBER
               + r"|(?<![\w.,])" + _MONEY_NUMBER + r"\s*(?:[$₹€£¥₩₽]|(?:" + _CURRENCY_CODES + r")\.?(?![A-Za-z])))", re.I),
]


def parse_fields(text: str) -> dict:
    """Read labelled or Markdown listing sections without swallowing the next field."""
    fields = {name: [] for name in ("title", "description", "tags", "what_you_get", "how_to_use",
                                   "license", "format", "size", "main_keyword")}
    labels = {"title": "title", "name": "title", "description": "description", "tags": "tags",
              "what you get": "what_you_get", "what's included": "what_you_get",
              "what's inside": "what_you_get", "included files": "what_you_get",
              "how to use": "how_to_use", "instructions": "how_to_use", "usage": "how_to_use",
              "license": "license", "licence": "license", "file format": "format", "format": "format",
              "size": "size", "page count": "size", "main keyword": "main_keyword", "keyword": "main_keyword"}
    current = None
    for raw in str(text).replace("’", "'").splitlines():
        stripped = raw.strip()
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", stripped)
        label_match = re.match(r"^([A-Za-z][A-Za-z '/-]*):\s*(.*)$", stripped)
        if heading:
            label = heading.group(2).strip().rstrip(":").lower()
            current = labels.get(label)
            if heading.group(1) == "#" and not fields["title"] and current is None:
                fields["title"].append(heading.group(2).strip())
                current = "title"
        elif label_match and label_match.group(1).lower() in labels:
            current = labels[label_match.group(1).lower()]
            fields[current].append(label_match.group(2))
        elif label_match:
            current = None
        elif stripped.rstrip(":").casefold() in labels:
            current = labels[stripped.rstrip(":").casefold()]
        elif current:
            fields[current].append(raw)
    return {name: "\n".join(lines).strip() for name,lines in fields.items()}


def _read_listing_txt(folder: str) -> str:
    path = os.path.join(folder, "listing.txt")
    if not os.path.isfile(path):
        print(f"Error: listing.txt not found in {folder!r}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _check_money(text: str) -> 'str | None':
    for pat in MONEY_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def _is_bundle_folder(folder: str) -> bool:
    """A bundle folder (made by bundle_builder.py) ships bundle.zip instead of product.zip."""
    return os.path.isfile(os.path.join(folder, "bundle.zip"))


def _bundle_member_count(folder: str) -> int:
    """Count member packs inside bundle.zip (one .zip entry per member pack)."""
    zip_path = os.path.join(folder, "bundle.zip")
    if not os.path.isfile(zip_path):
        return 0
    try:
        with zipfile.ZipFile(zip_path) as z:
            return sum(1 for name in z.namelist() if name.lower().endswith(".zip"))
    except Exception:
        return 0


def _section(text: str, label: str) -> str:
    """Extract a section value by label. Returns '' if missing."""
    parsed = parse_fields(text)
    key = {"Title": "title", "Description": "description", "Tags": "tags", "What you get": "what_you_get"}.get(label)
    if key is not None:
        return parsed[key]
    # Other legacy callers may ask for an additional labelled section.
    pattern = re.compile(rf"^{re.escape(label)}:\s*(.*?)$", re.MULTILINE | re.DOTALL)
    m = pattern.search(text)
    if not m:
        return ""
    value = m.group(1).strip()
    # Trim trailing section labels so we don't bleed into the next section.
    # Find the next section header after this match.
    after = text[m.end():]
    next_label_match = re.search(r"^\s*[A-Za-z][A-Za-z /-]*:\s*$", after, re.MULTILINE)
    if next_label_match:
        value = value[: value.find(next_label_match.group(0))]
    return value.strip()


def gumroad_block(title: str, description: str, tags: str, member_count: int | None = None) -> str:
    desc_block = description

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    tag_list = tag_list[:10]
    tags_out = ", ".join(tag_list)

    lines = []
    lines.append("GUMROAD")
    lines.append(f"Name: {title}")
    lines.append(f"Description: {desc_block}")
    lines.append(f"Tags: {tags_out}")
    if member_count:
        lines.append("Files to upload: bundle.zip")
    else:
        lines.append("Files to upload: product.zip")
    lines.append("Cover: cover.png")
    if member_count:
        lines.append("BUNDLE")
        lines.append(f"Members: {member_count} packs")
    lines.append("Price: SET BY OWNER")
    return "\n".join(lines)


def _section_raw_for_what_you_get(description: str) -> str:
    """Pull the 'What you get' portion out of the description region."""
    m = re.search(r"^What you get:\s*(.*?)$", description, re.MULTILINE | re.DOTALL)
    if not m:
        return ""
    val = m.group(1).strip()
    # Cut at the next section label if present.
    after = description[m.end():]
    next_label_match = re.search(r"^\s*[A-Za-z][A-Za-z /-]*:\s*$", after, re.MULTILINE)
    if next_label_match:
        val = val[: val.find(next_label_match.group(0))]
    return val.strip()


def topmate_block(title: str, description: str, member_count: int | None = None) -> str:
    short_desc = _cut_150(description)
    lines = [
        "TOPMATE",
        f"Title: {title}",
        f"Short description: {short_desc}",
        f"Long description: {description}",
    ]
    if member_count:
        lines.append("Files: bundle.zip")
    else:
        lines.append("Files: product.zip")
    if member_count:
        lines.append("BUNDLE")
        lines.append(f"Members: {member_count} packs")
    lines.append("Price: SET BY OWNER")
    return "\n".join(lines)


def _cut_150(text: str) -> str:
    """First 150 characters of text, cut at a word boundary."""
    text = text.strip()
    if len(text) <= 150:
        return text
    head = text[:150]
    # Cut at the last space within the head so we don't slice a word.
    sp = head.rfind(" ")
    if sp == -1:
        return head
    return head[:sp].rstrip()


def instamojo_block(title: str, description: str, what_you_get: str = "", member_count: int | None = None) -> str:
    """Produce an INSTAMOJO listing block."""
    title_cut = _cut_title(title, 80)
    desc_block = description
    if what_you_get:
        desc_block = description + "\n\n" + what_you_get
    desc_cut = desc_block[:5000]

    lines = [
        "INSTAMOJO",
        f"Title: {title_cut}",
        f"Description: {desc_cut}",
    ]
    if member_count:
        lines.append("Files to upload: bundle.zip")
    else:
        lines.append("Files to upload: product.zip")
    lines.append("Cover: cover.png")
    if member_count:
        lines.append("BUNDLE")
        lines.append(f"Members: {member_count} packs")
    lines.append("Price: SET BY OWNER")
    return "\n".join(lines)


def _section_what_you_get_block(description: str, whole_text: str = "") -> str:
    """Return just the 'What you get' block, starting with its label."""
    if not whole_text:
        whole_text = description
    m = re.search(r"^What you get:\s*(.*?)$", whole_text, re.MULTILINE | re.DOTALL)
    if not m:
        return ""
    block = m.group(1).rstrip()
    # Cut before any later section label.
    after = whole_text[m.end():]
    next_label_match = re.search(r"^\s*[A-Za-z][A-Za-z /-]*:\s*$", after, re.MULTILINE)
    if next_label_match:
        block = block[: block.find(next_label_match.group(0))].rstrip()
    return block


def _cut_title(text: str, max_len: int) -> str:
    """Truncate text to max_len characters, cutting at a word boundary."""
    text = text.strip()
    if len(text) <= max_len:
        return text
    head = text[:max_len]
    sp = head.rfind(" ")
    if sp == -1:
        return head
    return head[:sp].rstrip()


def owner_quality_summary(folder: str) -> str:
    """Owner-only review guidance, separate from the copyable public fields."""
    from listing_lint import quality_report
    return quality_report(folder)["tenglish_summary"]


def buyer_description(description: str, whole_text: str = "") -> str:
    """Keep the checked buyer information in the actual copyable description."""
    fields = parse_fields(whole_text or description)
    result = description.strip()
    for key,label in (("what_you_get", "What you get"), ("format", "File format"),
                      ("size", "Page count / size"), ("how_to_use", "How to use"), ("license", "License")):
        value = fields[key]
        if value and " ".join(value.split()) not in " ".join(result.split()):
            result += f"\n\n{label}:\n{value}"
    return result


def build_blocks(title: str, description: str, tags: str, store: str = "all", whole_text: str = "",
                 member_count: int | None = None) -> str:
    """Return one or more listing blocks, separated by blank lines.

    store: 'gumroad', 'topmate', 'instamojo', or 'all'.
    member_count: when set (bundle folder), upload bundle.zip and print a
    "BUNDLE / Members: N packs" line instead of product.zip.
    """
    description = buyer_description(description, whole_text)
    parts = []
    if store in ("all", "gumroad"):
        parts.append(gumroad_block(title, description, tags, member_count))
    if store in ("all", "topmate"):
        parts.append(topmate_block(title, description, member_count))
    if store in ("all", "instamojo"):
        parts.append(instamojo_block(title, description, member_count=member_count))
    return "\n\n".join(parts)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 listing_fields.py <upload_pack_folder>", file=sys.stderr)
        sys.exit(1)

    folder = sys.argv[1]
    text = _read_listing_txt(folder)

    money_hit = _check_money(text)
    if money_hit:
        print(
            "Error: listing.txt contains a money amount. "
            "Refusing to print listing fields.",
            file=sys.stderr,
        )
        sys.exit(1)

    title = _section(text, "Title")
    description = _section(text, "Description")
    tags = _section(text, "Tags")
    what_you_get = _section(text, "What you get")

    if not title:
        print("Error: listing.txt is missing a Title section.", file=sys.stderr)
        sys.exit(1)

    member_count = _bundle_member_count(folder) if _is_bundle_folder(folder) else None

    print(build_blocks(title, description, tags, whole_text=text, member_count=member_count))
    print()
    print(owner_quality_summary(folder), file=sys.stderr)


if __name__ == "__main__":
    main()
