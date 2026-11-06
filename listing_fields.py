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


MONEY_PATTERNS = [
    re.compile(r"\$[\d,]+(?:\.\d{1,2})?\b"),            # $10, $10.00, $1,200
    re.compile(r"[\d,]+(\s?)USD\b", re.IGNORECASE),      # 10 USD, 10USD
    re.compile(r"[\d,]+(\s?)INR\b", re.IGNORECASE),      # 10 INR
    re.compile(r"[Rs]?\s?₹\s?[\d,]+(?:\.\d{1,2})?"),    # ₹10, Rs ₹10, ₹ 10
    re.compile(r"Rs\.?\s?[\d,]+(?:\.\d{1,2})?\b", re.IGNORECASE),  # Rs 10, Rs.10
]


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


def _section(text: str, label: str) -> str:
    """Extract a section value by label. Returns '' if missing."""
    # Match "Label:" at start of a line, capture everything until next section label or EOF.
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


def gumroad_block(title: str, description: str, tags: str) -> str:
    what_you_get = _section_raw_for_what_you_get(description)
    desc_block = description
    if what_you_get:
        desc_block = description + "\n\n" + what_you_get

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    tag_list = tag_list[:10]
    tags_out = ", ".join(tag_list)

    lines = []
    lines.append("GUMROAD")
    lines.append(f"Name: {title}")
    lines.append(f"Description: {desc_block}")
    lines.append(f"Tags: {tags_out}")
    lines.append("Files to upload: product.zip")
    lines.append("Cover: cover.png")
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


def topmate_block(title: str, description: str) -> str:
    short_desc = _cut_150(description)
    return (
        "TOPMATE\n"
        f"Title: {title}\n"
        f"Short description: {short_desc}\n"
        f"Long description: {description}\n"
        "Files: product.zip\n"
        "Price: SET BY OWNER"
    )


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


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 listing_fields.py <upload_pack_folder>", file=sys.stderr)
        sys.exit(1)

    folder = sys.argv[1]
    text = _read_listing_txt(folder)

    money_hit = _check_money(text)
    if money_hit:
        print(
            f"Error: listing.txt contains a money amount ({money_hit!r}). "
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

    print(gumroad_block(title, description, tags))
    print()
    print(topmate_block(title, description))
    print()


if __name__ == "__main__":
    main()
