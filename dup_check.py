#!/usr/bin/env python3
"""dup_check.py — Cross-listing duplicate content checker for ready packs.

Scans ready product packs' listing details (title, description, tags, and blog.md if present)
and checks for duplicate content across packs:
  - Word shingles (k=5) + Jaccard similarity
  - Pairs with similarity > 0.5 flagged
  - Exit code 1 if any pair has similarity > 0.8
  - Repeated title openings (e.g. sharing the same first 3+ words or leading prefix)
  - Identical or near-identical tag sets
  - Owner-facing suggestions in Tenglish house style (Telugu sentence words + English nouns)
  - Outputs JSON and Markdown reports atomically to exported_content/marketing/dup_report.*
  - No money amounts anywhere, no brand logos/names, no automatic publishing.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

# Try importing Pillow if needed, but not strictly required
try:
    from PIL import Image  # type: ignore # noqa: F401
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


def tokenize_words(text: str) -> List[str]:
    """Tokenize text into lowercase alphanumeric words."""
    if not text:
        return []
    # Replace non-alphanumeric characters with space
    clean = re.sub(r"[^a-zA-Z0-9]+", " ", text).strip().lower()
    return [w for w in clean.split() if w]


def get_word_shingles(words: List[str], k: int = 5) -> Set[str]:
    """Generate k-shingles (word n-grams) from a sequence of words.

    If words count is less than k and words is non-empty, falls back to a single
    shingle of all words to allow small texts (e.g. short titles) to be compared.
    """
    if not words:
        return set()
    if len(words) < k:
        return {" ".join(words)}
    shingles: Set[str] = set()
    for i in range(len(words) - k + 1):
        shingles.add(" ".join(words[i : i + k]))
    return shingles


def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Calculate Jaccard index between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


def parse_listing_file(listing_path: Path) -> Dict[str, Any]:
    """Parse listing.txt or listing.md into title, description, and tags."""
    out: Dict[str, Any] = {
        "title": "",
        "tags": [],
        "description": "",
    }
    if not listing_path.is_file():
        return out

    try:
        content = listing_path.read_text(encoding="utf-8-sig")
    except Exception:
        return out

    lines = content.splitlines()
    cur_section = ""
    desc_lines: List[str] = []

    for line in lines:
        s = line.strip()
        s_lower = s.lower()

        # Heading check # Title
        heading_m = re.match(r"^(#{1,6})\s+(.+)$", s)
        if heading_m:
            level = len(heading_m.group(1))
            label = heading_m.group(2).strip()
            label_lower = label.lower()
            if level == 1 and not out["title"]:
                out["title"] = label
                cur_section = "title"
                continue
            elif "tag" in label_lower:
                cur_section = "tags"
                continue
            elif "desc" in label_lower:
                cur_section = "desc"
                continue
            else:
                cur_section = "other"
                continue

        if s_lower.startswith("title:"):
            out["title"] = s.split(":", 1)[1].strip()
            cur_section = "title"
            continue
        elif s_lower.startswith("tags:"):
            parts = s.split(":", 1)[1].split(",")
            for p in parts:
                cleaned = p.strip()
                if cleaned:
                    out["tags"].append(cleaned)
            cur_section = "tags"
            continue
        elif s_lower.startswith("description:"):
            cur_section = "desc"
            continue
        elif (
            s_lower.startswith("what you get:")
            or s_lower.startswith("what's included:")
            or s_lower.startswith("included files:")
            or s_lower.startswith("what's inside:")
        ):
            cur_section = "what"
            continue
        elif re.match(r"^[A-Z][A-Za-z /-]*:\s*$", s) and not (
            s_lower.startswith("what") or s_lower.startswith("included")
        ):
            cur_section = "other"
            continue

        if cur_section == "tags":
            for p in s.split(","):
                cleaned = p.strip()
                if cleaned:
                    out["tags"].append(cleaned)
        elif cur_section == "desc":
            desc_lines.append(line)

    out["description"] = "\n".join(desc_lines).strip()
    return out


def normalize_tag(tag: str) -> str:
    """Normalize tag for case-insensitive and punctuation-free comparison."""
    return re.sub(r"[^a-zA-Z0-9]+", " ", tag).strip().lower()


def extract_pack_content(
    pack_dir: Path,
    marketing_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Extract listing and marketing content for a pack."""
    listing_path = pack_dir / "listing.txt"
    if not listing_path.is_file():
        listing_path = pack_dir / "listing.md"

    info = parse_listing_file(listing_path)
    title = info["title"] or pack_dir.name.replace("-", " ").title()
    desc = info["description"]
    tags = info["tags"]

    # Check for blog.md in marketing directory or inside pack directory
    blog_content = ""
    candidate_blog_paths = []
    if marketing_dir:
        candidate_blog_paths.append(marketing_dir / pack_dir.name / "blog.md")
    # Also check standard exported_content/marketing/<pack>/blog.md
    candidate_blog_paths.append(
        pack_dir.parent.parent / "marketing" / pack_dir.name / "blog.md"
    )
    candidate_blog_paths.append(pack_dir / "blog.md")

    for bp in candidate_blog_paths:
        if bp.is_file():
            try:
                blog_content = bp.read_text(encoding="utf-8-sig")
                break
            except Exception:
                pass

    # Clean combined tags
    norm_tags = set()
    for t in tags:
        nt = normalize_tag(t)
        if nt:
            norm_tags.add(nt)

    # Shingles
    title_words = tokenize_words(title)
    desc_words = tokenize_words(desc)
    blog_words = tokenize_words(blog_content)
    all_combined_words = title_words + desc_words + blog_words

    return {
        "pack_name": pack_dir.name,
        "pack_dir": str(pack_dir),
        "title": title,
        "description": desc,
        "tags": sorted(list(norm_tags)),
        "has_blog": bool(blog_content),
        "blog_len": len(blog_words),
        "title_words": title_words,
        "desc_words": desc_words,
        "blog_words": blog_words,
        "all_words": all_combined_words,
        "title_shingles": get_word_shingles(title_words, k=3 if len(title_words) < 5 else 5),
        "desc_shingles": get_word_shingles(desc_words, k=5),
        "blog_shingles": get_word_shingles(blog_words, k=5),
        "all_shingles": get_word_shingles(all_combined_words, k=5),
    }


def find_common_opening(words1: List[str], words2: List[str], min_words: int = 3) -> str:
    """Check if two word lists share the same leading opening words."""
    common: List[str] = []
    for w1, w2 in zip(words1, words2):
        if w1 == w2:
            common.append(w1)
        else:
            break
    if len(common) >= min_words:
        return " ".join(common)
    return ""


def compare_packs(
    pack1: Dict[str, Any],
    pack2: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare two packs and calculate duplicate metrics."""
    # Shingle Jaccard similarities
    title_sim = round(jaccard_similarity(pack1["title_shingles"], pack2["title_shingles"]), 4)
    desc_sim = round(jaccard_similarity(pack1["desc_shingles"], pack2["desc_shingles"]), 4)
    overall_sim = round(jaccard_similarity(pack1["all_shingles"], pack2["all_shingles"]), 4)

    blog_sim = 0.0
    if pack1["has_blog"] and pack2["has_blog"]:
        blog_sim = round(jaccard_similarity(pack1["blog_shingles"], pack2["blog_shingles"]), 4)

    # Repeated title openings (3 or more leading words)
    common_title_opening = find_common_opening(pack1["title_words"], pack2["title_words"], min_words=3)

    # Tag set comparison
    tags1 = set(pack1["tags"])
    tags2 = set(pack2["tags"])
    identical_tags = bool(tags1 and tags1 == tags2)
    tag_sim = round(jaccard_similarity(tags1, tags2), 4)

    # Maximum similarity across key components
    max_sim = max(overall_sim, desc_sim, (blog_sim if pack1["has_blog"] and pack2["has_blog"] else 0.0))

    # Tenglish suggestion if duplicate indicators are detected
    tenglish_suggestions: List[str] = []
    if max_sim >= 0.8:
        tenglish_suggestions.append(
            f"Ee packs {pack1['pack_name']} mariyu {pack2['pack_name']} madhya content chaala ekkuva duplicate undi ({round(max_sim*100)}%). Description leda copy rewrite cheyandi."
        )
    elif max_sim >= 0.5:
        tenglish_suggestions.append(
            f"Ee packs {pack1['pack_name']} mariyu {pack2['pack_name']} content similarity {round(max_sim*100)}% undi. Konchem variation add cheyandi."
        )

    if common_title_opening:
        tenglish_suggestions.append(
            f"Titles lo oke opening words unnay ('{common_title_opening}'). Distinct title words vadandi."
        )

    if identical_tags:
        tenglish_suggestions.append(
            "Rendu packs lo identical tags unnayi. Audience search kosam niche tags differentiate cheyandi."
        )
    elif tag_sim >= 0.8:
        tenglish_suggestions.append(
            f"Tags similarity {round(tag_sim*100)}% chaala high undi. Differentiate cheyandi."
        )

    return {
        "pack_a": pack1["pack_name"],
        "pack_b": pack2["pack_name"],
        "overall_similarity": overall_sim,
        "title_similarity": title_sim,
        "description_similarity": desc_sim,
        "blog_similarity": blog_sim,
        "max_similarity": max_sim,
        "common_title_opening": common_title_opening,
        "identical_tags": identical_tags,
        "tag_similarity": tag_sim,
        "is_duplicate_pair": max_sim >= 0.5 or overall_sim >= 0.5,
        "is_severe_duplicate": max_sim >= 0.8 or overall_sim >= 0.8,
        "tenglish_suggestions": tenglish_suggestions,
    }


def analyze_packs(
    pack_contents: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Run cross-listing duplicate content analysis across all ready packs."""
    pairs: List[Dict[str, Any]] = []
    high_sim_pairs: List[Dict[str, Any]] = []
    severe_pairs: List[Dict[str, Any]] = []
    repeated_openings: List[Dict[str, Any]] = []
    identical_tag_pairs: List[Dict[str, Any]] = []

    n = len(pack_contents)
    for i in range(n):
        for j in range(i + 1, n):
            cmp = compare_packs(pack_contents[i], pack_contents[j])
            pairs.append(cmp)

            if cmp["is_duplicate_pair"]:
                high_sim_pairs.append(cmp)
            if cmp["is_severe_duplicate"]:
                severe_pairs.append(cmp)
            if cmp["common_title_opening"]:
                repeated_openings.append(cmp)
            if cmp["identical_tags"]:
                identical_tag_pairs.append(cmp)

    # Sort high sim pairs descending by max_similarity then pack names
    high_sim_pairs.sort(key=lambda p: (-p["max_similarity"], p["pack_a"], p["pack_b"]))
    severe_pairs.sort(key=lambda p: (-p["max_similarity"], p["pack_a"], p["pack_b"]))

    # Overall Tenglish summary line
    if severe_pairs:
        summary_tenglish = (
            f"Mee packs lo severe duplicate content undi: {len(severe_pairs)} pair(s) similarity > 80%. "
            f"Listing text rewrite chesi differentiate cheyandi."
        )
    elif high_sim_pairs:
        summary_tenglish = (
            f"Mee packs lo konchem duplicate content undi: {len(high_sim_pairs)} pair(s) similarity > 50%. "
            f"Variations add chesthe search lo manchidi."
        )
    elif repeated_openings or identical_tag_pairs:
        summary_tenglish = (
            "Content similarity safe ga undi, kani repeated title openings leda identical tags unnayi. Review cheyandi."
        )
    else:
        summary_tenglish = "Duplicate content ledu, ready packs antha clean ga distinct ga unnayi ✓."

    return {
        "total_packs": n,
        "total_pairs_compared": len(pairs),
        "duplicate_pairs_count": len(high_sim_pairs),
        "severe_duplicates_count": len(severe_pairs),
        "repeated_openings_count": len(repeated_openings),
        "identical_tag_pairs_count": len(identical_tag_pairs),
        "has_severe_duplicates": len(severe_pairs) > 0,
        "summary_tenglish": summary_tenglish,
        "flagged_pairs": high_sim_pairs,
        "repeated_title_openings": repeated_openings,
        "identical_tag_pairs": identical_tag_pairs,
        "all_pairs": pairs,
    }


def generate_markdown_report(report_data: Dict[str, Any]) -> str:
    """Generate deterministic Markdown duplicate report."""
    lines: List[str] = []
    lines.append("# Duplicate Content Report Across Ready Packs")
    lines.append("")
    lines.append(f"**Tenglish Summary**: {report_data['summary_tenglish']}")
    lines.append("")
    lines.append("## Overview Metrics")
    lines.append(f"- Total ready packs checked: {report_data['total_packs']}")
    lines.append(f"- Total pairs compared: {report_data['total_pairs_compared']}")
    lines.append(f"- Duplicate pairs (similarity >= 50%): {report_data['duplicate_pairs_count']}")
    lines.append(f"- Severe duplicate pairs (similarity >= 80%): {report_data['severe_duplicates_count']}")
    lines.append(f"- Repeated title openings: {report_data['repeated_openings_count']}")
    lines.append(f"- Identical tag sets: {report_data['identical_tag_pairs_count']}")
    lines.append("")

    lines.append("## Flagged Pairs (Similarity >= 50%)")
    flagged = report_data.get("flagged_pairs", [])
    if not flagged:
        lines.append("No duplicate pairs found above 50% similarity threshold. All pack listings look distinct.")
    else:
        for idx, item in enumerate(flagged, 1):
            severity = "SEVERE (>80%)" if item["is_severe_duplicate"] else "MODERATE (>50%)"
            lines.append(f"### {idx}. {item['pack_a']} vs {item['pack_b']} — {severity}")
            lines.append(f"- Max Similarity: {round(item['max_similarity'] * 100, 1)}%")
            lines.append(f"- Description Similarity: {round(item['description_similarity'] * 100, 1)}%")
            lines.append(f"- Title Similarity: {round(item['title_similarity'] * 100, 1)}%")
            if item.get("blog_similarity", 0.0) > 0:
                lines.append(f"- Blog Similarity: {round(item['blog_similarity'] * 100, 1)}%")
            lines.append(f"- Tag Similarity: {round(item['tag_similarity'] * 100, 1)}%")
            if item.get("common_title_opening"):
                lines.append(f"- Common Title Opening: `{item['common_title_opening']}`")
            if item.get("identical_tags"):
                lines.append("- Tags Status: Identical tag set")
            lines.append("- **Tenglish Suggestions**:")
            for sug in item.get("tenglish_suggestions", []):
                lines.append(f"  - {sug}")
            lines.append("")

    lines.append("## Repeated Title Openings")
    openings = report_data.get("repeated_title_openings", [])
    if not openings:
        lines.append("No repeated title openings detected.")
    else:
        for item in openings:
            lines.append(f"- `{item['pack_a']}` & `{item['pack_b']}`: shared opening `{item['common_title_opening']}`")
    lines.append("")

    lines.append("## Identical Tag Sets")
    id_tags = report_data.get("identical_tag_pairs", [])
    if not id_tags:
        lines.append("No identical tag sets detected across ready packs.")
    else:
        for item in id_tags:
            lines.append(f"- `{item['pack_a']}` & `{item['pack_b']}` share an identical set of tags.")
    lines.append("")

    lines.append("## Owner Action Checklist")
    lines.append("- [ ] Review flagged packs with similarity above 50% for unique value propositions.")
    lines.append("- [ ] Differentiate titles sharing identical prefix openings.")
    lines.append("- [ ] Customize tags per pack to target specific long-tail buyer queries.")
    lines.append("")

    return "\n".join(lines)


def write_reports_atomically(
    report_data: Dict[str, Any],
    out_dir: Path,
) -> Tuple[Path, Path]:
    """Write JSON and Markdown reports atomically to out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)

    json_dest = out_dir / "dup_report.json"
    md_dest = out_dir / "dup_report.md"

    md_content = generate_markdown_report(report_data)

    # Atomic write JSON
    fd, tmp_json = tempfile.mkstemp(prefix="dup_json_", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_json, json_dest)
    except Exception:
        if os.path.exists(tmp_json):
            os.unlink(tmp_json)
        raise

    # Atomic write Markdown
    fd, tmp_md = tempfile.mkstemp(prefix="dup_md_", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(md_content)
        os.replace(tmp_md, md_dest)
    except Exception:
        if os.path.exists(tmp_md):
            os.unlink(tmp_md)
        raise

    return json_dest, md_dest


def check_duplicate_content(
    products_dir: Path | str = "exported_content/products",
    marketing_dir: Optional[Path | str] = None,
    out_dir: Path | str = "exported_content/marketing",
) -> Dict[str, Any]:
    """Discover ready packs, compare listing content, and output reports."""
    import launch_checklist
    ready_packs = launch_checklist.discover_ready_packs(products_dir)

    mkt_dir = Path(marketing_dir) if marketing_dir else None
    pack_contents = [extract_pack_content(p, marketing_dir=mkt_dir) for p in ready_packs]

    report = analyze_packs(pack_contents)
    write_reports_atomically(report, Path(out_dir))
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-listing duplicate content checker across ready packs."
    )
    parser.add_argument(
        "--products-dir",
        default="exported_content/products",
        help="Products root directory (defaults to exported_content/products).",
    )
    parser.add_argument(
        "--marketing-dir",
        default="exported_content/marketing",
        help="Marketing directory containing pack marketing files such as blog.md.",
    )
    parser.add_argument(
        "--out-dir",
        default="exported_content/marketing",
        help="Directory to save dup_report.json and dup_report.md (defaults to exported_content/marketing).",
    )

    args = parser.parse_args(argv)

    report = check_duplicate_content(
        products_dir=args.products_dir,
        marketing_dir=args.marketing_dir,
        out_dir=args.out_dir,
    )

    print(report["summary_tenglish"])

    # Exit code 1 if any pair over 0.8 similarity, else 0
    if report.get("has_severe_duplicates", False):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
