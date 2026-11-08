#!/usr/bin/env python3
"""keyword_bank.py — Offline keyword ideas engine for listings.

Generates offline keyword ideas (no network requests) from:
- All upload pack listings (titles, tags, descriptions)
- Built-in seed list of planner, printable, and worksheet words
- Derives stems, plural/singular variants, and long-tail combinations
  (e.g., "printable habit tracker pdf", "daily planner printable")
- Deduplicates and ranks keywords by how many packs share them
- Writes atomically to exported_content/marketing/keywords.json
- Strictly excludes brand names and money amounts.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Known brand names to exclude from keywords
BRAND_NAMES: Set[str] = {
    "gumroad", "topmate", "etsy", "notion", "pinterest", "instagram", "goodnotes",
    "excel", "canva", "facebook", "twitter", "tiktok", "youtube", "linkedin",
    "shopify", "amazon", "apple", "google", "microsoft", "word", "powerpoint",
}

# Currency words / patterns to exclude
CURRENCY_WORDS: Set[str] = {
    "usd", "inr", "eur", "gbp", "aud", "cad", "nzd", "sgd", "hkd", "jpy", "cny",
    "rmb", "aed", "chf", "zar", "pkr", "bdt", "ngn", "php", "krw", "rub", "brl",
    "mxn", "rs", "rupee", "rupees", "dollar", "dollars", "euro", "euros", "pound",
    "pounds", "yen", "cent", "cents", "paise", "paisa", "price", "cost", "sale",
}

_MONEY_PATTERN = re.compile(
    r"(?:\$|₹|€|£|¥|\b(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?)\s*\d+|\d+\s*(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?))",
    re.I
)

# Stop words to ignore during word extraction
STOP_WORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "what",
    "which", "this", "that", "these", "those", "then", "just", "so", "than",
    "such", "both", "through", "about", "for", "is", "of", "while", "during",
    "to", "from", "in", "out", "on", "off", "again", "further", "once",
    "here", "there", "when", "where", "why", "how", "all", "any", "each",
    "few", "more", "most", "other", "some", "no", "nor", "not", "only",
    "own", "same", "too", "very", "can", "will", "with", "by", "at",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "our", "your",
    "you", "we", "they", "i", "me", "my", "us", "it", "its",
}

# Built-in seed list of planner, printable, and worksheet words
SEED_WORDS: List[str] = [
    "planner", "printable", "worksheet", "tracker", "checklist", "template",
    "log", "organizer", "journal", "bundle", "sheet", "guide", "workbook",
    "routine", "schedule", "daily", "weekly", "monthly", "habit", "goal",
    "minimalist", "clean", "undated", "digital", "instant download", "pdf",
]

# Modifiers for long-tail combos
LONG_TAIL_PREFIXES: List[str] = [
    "printable", "daily", "weekly", "monthly", "minimalist", "simple", "clean",
]

LONG_TAIL_SUFFIXES: List[str] = [
    "printable", "pdf", "template", "worksheet", "tracker", "checklist",
    "planner", "bundle", "instant download",
]


def clean_term(term: str) -> str:
    """Normalize term to lowercase alphanumeric and spaces."""
    term = term.lower().strip()
    term = re.sub(r"[^\w\s-]", " ", term)
    term = re.sub(r"[\s_-]+", " ", term).strip()
    return term


def contains_brand_or_money(term: str) -> bool:
    """Check if term contains prohibited brands or currency symbols/words."""
    lower = term.lower()
    if _MONEY_PATTERN.search(lower):
        return True
    words = set(re.findall(r"\b[a-z0-9]+\b", lower))
    if words.intersection(BRAND_NAMES):
        return True
    if words.intersection(CURRENCY_WORDS):
        return True
    return False


def get_stems_and_forms(word: str) -> Set[str]:
    """Generate stem, plural, and singular variants for a single word."""
    w = clean_term(word)
    if not w or len(w) <= 2:
        return {w} if w else set()

    variants = {w}

    # Plural / singular conversions
    if w.endswith("ies") and len(w) > 4:
        variants.add(w[:-3] + "y")
    elif w.endswith("es") and len(w) > 3:
        variants.add(w[:-2])
        variants.add(w[:-1])
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        variants.add(w[:-1])
    elif not w.endswith("s"):
        if w.endswith("y") and len(w) > 2 and w[-2] not in "aeiou":
            variants.add(w[:-1] + "ies")
        elif w.endswith(("s", "x", "z", "ch", "sh")):
            variants.add(w + "es")
        else:
            variants.add(w + "s")

    # Participle -ing / -ed
    if w.endswith("ing") and len(w) > 5:
        stem = w[:-3]
        variants.add(stem)
        variants.add(stem + "e")
    elif w.endswith("ed") and len(w) > 4:
        stem = w[:-2]
        variants.add(stem)
        variants.add(stem + "e")

    return {v for v in variants if len(v) >= 2}


def generate_long_tail_combinations(base_terms: Iterable[str]) -> Set[str]:
    """Combine base terms with seed prefixes and suffixes to create long-tail ideas."""
    combos: Set[str] = set()
    for base in base_terms:
        if contains_brand_or_money(base):
            continue
        b = clean_term(base)
        if not b or len(b) < 3 or contains_brand_or_money(b):
            continue

        base_words = b.split()
        # Add base term itself
        combos.add(b)

        # Generate prefix + base
        for pref in LONG_TAIL_PREFIXES:
            if pref not in base_words:
                combos.add(f"{pref} {b}")

        # Generate base + suffix
        for suf in LONG_TAIL_SUFFIXES:
            if suf not in base_words:
                combos.add(f"{b} {suf}")

        # Generate prefix + base + suffix (e.g. "printable habit tracker pdf")
        for pref in ("printable", "daily", "minimalist"):
            for suf in ("pdf", "template", "worksheet"):
                if pref not in base_words and suf not in base_words:
                    combos.add(f"{pref} {b} {suf}")

    return combos


def extract_pack_terms(pack_dir: Path) -> Set[str]:
    """Extract candidate terms from an upload pack's listing file."""
    terms: Set[str] = set()
    listing_file = pack_dir / "listing.txt"
    if not listing_file.is_file():
        listing_file = pack_dir / "listing.md"

    if not listing_file.is_file():
        return terms

    try:
        content = listing_file.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception:
        return terms

    lines = content.splitlines()
    in_tags_section = False

    for line in lines:
        raw_strip = line.strip()
        lower = raw_strip.lower()

        if lower.startswith("## tags") or lower.startswith("# tags") or lower == "tags:":
            in_tags_section = True
            continue
        elif lower.startswith("#") or (":" in lower and not lower.startswith("tag")):
            in_tags_section = False

        if lower.startswith("tags:") or lower.startswith("tag:"):
            part = line.split(":", 1)[1]
            for tag in part.split(","):
                ct = clean_term(tag)
                if ct and not contains_brand_or_money(ct):
                    terms.add(ct)
        elif in_tags_section:
            for tag in raw_strip.split(","):
                ct = clean_term(tag)
                if ct and not contains_brand_or_money(ct):
                    terms.add(ct)
        elif lower.startswith("title:") or lower.startswith("# "):
            title = line.split(":", 1)[-1].lstrip("#").strip()
            ct = clean_term(title)
            if ct and not contains_brand_or_money(ct):
                terms.add(ct)
            words = [w for w in ct.split() if w not in STOP_WORDS and len(w) >= 3]
            for i in range(len(words)):
                if i + 1 < len(words):
                    ngram2 = f"{words[i]} {words[i+1]}"
                    if not contains_brand_or_money(ngram2):
                        terms.add(ngram2)
                if i + 2 < len(words):
                    ngram3 = f"{words[i]} {words[i+1]} {words[i+2]}"
                    if not contains_brand_or_money(ngram3):
                        terms.add(ngram3)

    return terms


def build_keyword_bank(
    products_dir: Path | str = "exported_content/products",
    extra_seed_words: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Extract, expand, deduplicate, and rank keywords across all packs."""
    p_dir = Path(products_dir)
    upload_dirs: List[Path] = []

    # Check common upload directory paths
    candidates = [
        p_dir / "upload",
        p_dir,
    ]
    for cand in candidates:
        if cand.is_dir():
            for sub in cand.iterdir():
                if sub.is_dir() and ((sub / "listing.txt").is_file() or (sub / "listing.md").is_file()):
                    if sub not in upload_dirs:
                        upload_dirs.append(sub)

    pack_terms_map: Dict[str, Set[str]] = {}
    term_pack_count: Counter[str] = Counter()
    all_raw_terms: Set[str] = set()

    for pack_folder in upload_dirs:
        p_name = pack_folder.name
        terms = extract_pack_terms(pack_folder)
        pack_terms_map[p_name] = terms
        for t in terms:
            term_pack_count[t] += 1
            all_raw_terms.add(t)

    # Built-in seeds
    seeds = list(SEED_WORDS)
    if extra_seed_words:
        seeds.extend(extra_seed_words)

    for s in seeds:
        cs = clean_term(s)
        if cs and not contains_brand_or_money(cs):
            all_raw_terms.add(cs)

    # Word stems and singular/plural expansions
    expanded_base_terms: Set[str] = set()
    stems_lookup: Dict[str, Set[str]] = defaultdict(set)

    for term in all_raw_terms:
        expanded_base_terms.add(term)
        words = term.split()
        if len(words) == 1:
            for v in get_stems_and_forms(words[0]):
                expanded_base_terms.add(v)
                stems_lookup[words[0]].add(v)

    # Generate long-tail combinations
    all_combos = generate_long_tail_combinations(expanded_base_terms)

    # Filter out anything with brand names, money, or invalid characters
    valid_keywords: Set[str] = set()
    for kw in all_combos:
        kw_clean = clean_term(kw)
        if (
            kw_clean
            and len(kw_clean) >= 3
            and not contains_brand_or_money(kw_clean)
        ):
            valid_keywords.add(kw_clean)

    # Rank keywords
    # Criteria:
    # 1. Number of packs sharing the term or sub-terms
    # 2. Contains core seeds
    ranked_list: List[Dict[str, Any]] = []

    for kw in sorted(valid_keywords):
        words = kw.split()
        # Count packs containing this keyword or its constituent significant words
        pack_matches = 0
        matching_packs: List[str] = []
        for p_name, p_terms in pack_terms_map.items():
            if any(kw == t or kw in t or all(w in t for w in words) for t in p_terms):
                pack_matches += 1
                matching_packs.append(p_name)

        # Seed relevance bonus
        seed_matches = sum(1 for s in seeds if s in words)

        score = (pack_matches * 10) + (seed_matches * 2) + (1 if len(words) >= 2 else 0)

        # Categorize
        if len(words) >= 3:
            kw_type = "long_tail"
        elif len(words) == 2:
            kw_type = "phrase"
        else:
            kw_type = "seed"

        ranked_list.append({
            "keyword": kw,
            "score": score,
            "pack_count": pack_matches,
            "matching_packs": matching_packs,
            "type": kw_type,
            "word_count": len(words),
        })

    # Sort descending by score, then pack_count, then length
    ranked_list.sort(key=lambda x: (x["score"], x["pack_count"], -x["word_count"], x["keyword"]), reverse=True)

    result = {
        "total_keywords": len(ranked_list),
        "total_packs_analyzed": len(upload_dirs),
        "seed_words_count": len(seeds),
        "keywords": ranked_list,
    }
    return result


def export_keyword_bank(
    products_dir: Path | str = "exported_content/products",
    output_path: Path | str = "exported_content/marketing/keywords.json",
) -> Path:
    """Run build_keyword_bank and atomically save output to JSON file."""
    data = build_keyword_bank(products_dir)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write via temporary file
    temp_dir = tempfile.mkdtemp(prefix="kwbank_")
    try:
        temp_file = Path(temp_dir) / "keywords.tmp.json"
        temp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(out)
    finally:
        if os.path.exists(temp_dir):
            try:
                os.rmdir(temp_dir)
            except Exception:
                pass

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate offline keyword bank from packs and seeds.")
    parser.add_argument(
        "--products-dir",
        default="exported_content/products",
        help="Products directory (default: exported_content/products).",
    )
    parser.add_argument(
        "--output",
        default="exported_content/marketing/keywords.json",
        help="Output JSON file path (default: exported_content/marketing/keywords.json).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Limit number of keywords printed to console (default: 50).",
    )
    args = parser.parse_args()

    out_file = export_keyword_bank(args.products_dir, args.output)
    print(f"Keyword bank saved to: {out_file}")

    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"Total packs analyzed: {data['total_packs_analyzed']}")
    print(f"Total keywords generated: {data['total_keywords']}")
    print(f"\nTop {args.limit} keywords:")
    for i, item in enumerate(data["keywords"][:args.limit], 1):
        print(f"  {i}. {item['keyword']} (score: {item['score']}, packs: {item['pack_count']}, type: {item['type']})")


if __name__ == "__main__":
    main()
