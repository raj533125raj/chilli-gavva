#!/usr/bin/env python3
"""seo_score.py — SEO scorer for pack listings.

Evaluates a pack listing on a 0–100 scale with granular criteria:
1. Main keyword in first 40 chars of title (max 20 pts)
2. Keyword in first sentence of description (max 15 pts)
3. Tag coverage of title words (max 15 pts)
4. No tag duplicates or near-duplicates/stems (max 15 pts)
5. Description length & structure (bullets, "What you get") (max 15 pts)
6. Readability (average sentence length <= 20 words) (max 10 pts)
7. Alt texts present for images (max 10 pts)

Outputs a comprehensive JSON structure plus a short Tenglish summary line.
Never modifies pack files. Outputs go outside private pack folders if written.
No money amounts or brand logos/names in generated content.
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

# Common stop words ignored when extracting title words / main keywords
STOP_WORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "what",
    "which", "this", "that", "these", "those", "then", "just", "so", "than",
    "such", "both", "through", "about", "for", "is", "of", "while", "during",
    "to", "from", "in", "out", "on", "off", "again", "further", "then", "once",
    "here", "there", "when", "where", "why", "how", "all", "any", "both", "each",
    "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only",
    "own", "same", "so", "than", "too", "very", "can", "will", "with", "by", "at",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "our", "your",
}

# Image extensions to check for alt texts
IMAGE_EXTENSIONS: Set[str] = {".png", ".jpg", ".jpeg", ".webp"}


def simple_stem(word: str) -> str:
    """Lightweight suffix-stripping stemmer for near-duplicate tag detection."""
    w = word.strip().lower()
    if len(w) <= 3:
        return w
    # Common plural / participle suffixes
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if w.endswith(suffix):
            if suffix == "ies" and len(w) > 4:
                return w[:-3] + "y"
            stemmed = w[:-len(suffix)]
            if len(stemmed) >= 3:
                return stemmed
    return w


def extract_keywords_from_title(title: str) -> List[str]:
    """Extract candidate keyword phrases or significant words from the title."""
    clean = re.sub(r"[^\w\s-]", " ", title.lower())
    words = [w for w in clean.split() if len(w) >= 3 and w not in STOP_WORDS]
    return words


def get_main_keyword(title: str, tags: List[str]) -> str:
    """Identify the main keyword/phrase.

    Looks for 2-word phrases or dominant single word from title that also matches tags,
    or falls back to the first 2-3 significant words of title.
    """
    clean_title = re.sub(r"[^\w\s-]", " ", title.lower()).strip()
    words = [w for w in clean_title.split() if len(w) >= 3 and w not in STOP_WORDS]
    if not words:
        return title.strip().lower()

    # Look if any tag is found in the title
    clean_tags = [t.strip().lower() for t in tags if t.strip()]
    for tag in clean_tags:
        if tag in clean_title and len(tag.split()) >= 2:
            return tag
    for tag in clean_tags:
        if tag in clean_title and len(tag) >= 3 and tag not in STOP_WORDS:
            return tag

    # Try two-word phrase
    if len(words) >= 2:
        candidate_phrase = f"{words[0]} {words[1]}"
        return candidate_phrase
    return words[0]


def parse_listing_text(content: str) -> Dict[str, Any]:
    """Parse listing text or markdown string into structured fields."""
    out: Dict[str, Any] = {
        "title": "",
        "tags": [],
        "description": "",
        "what_you_get": [],
        "raw": content,
    }

    lines = content.splitlines()
    cur_section = ""
    desc_lines: List[str] = []
    what_lines: List[str] = []

    for line in lines:
        raw_strip = line.strip()
        lower = raw_strip.lower()

        # Check for markdown heading # Title
        heading_m = re.match(r"^(#{1,6})\s+(.+)$", raw_strip)
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
            elif any(k in label_lower for k in ("what you get", "what's included", "included", "inside")):
                cur_section = "what"
                continue
            else:
                cur_section = "other"
                continue

        if lower.startswith("title:"):
            out["title"] = raw_strip.split(":", 1)[1].strip()
            cur_section = "title"
            continue
        elif lower.startswith("tags:"):
            tags_part = raw_strip.split(":", 1)[1].strip()
            out["tags"].extend(t.strip() for t in tags_part.split(",") if t.strip())
            cur_section = "tags"
            continue
        elif lower.startswith("description:"):
            cur_section = "desc"
            continue
        elif (
            lower.startswith("what you get:")
            or lower.startswith("what's included:")
            or lower.startswith("included files:")
            or lower.startswith("what's inside:")
        ):
            cur_section = "what"
            continue
        elif re.match(r"^[A-Z][A-Za-z /-]*:\s*$", raw_strip) and not (
            lower.startswith("what") or lower.startswith("included")
        ):
            cur_section = "other"
            continue

        if cur_section == "tags":
            if raw_strip:
                out["tags"].extend(t.strip() for t in raw_strip.split(",") if t.strip())
        elif cur_section == "desc":
            desc_lines.append(line)
        elif cur_section == "what":
            cleaned_item = re.sub(r"^[-*•\d.)\s]+", "", raw_strip).strip()
            if cleaned_item:
                what_lines.append(cleaned_item)

    out["description"] = "\n".join(desc_lines).strip()
    out["what_you_get"] = what_lines
    return out


def find_alt_texts(pack_dir: Path) -> Dict[str, str]:
    """Look for alt texts in alt.txt, pins/pins.json, or carousel/alt.txt."""
    alts: Dict[str, str] = {}
    if not pack_dir.is_dir():
        return alts

    # 1. Check pack_dir/alt.txt
    alt_file = pack_dir / "alt.txt"
    if alt_file.is_file():
        try:
            for line in alt_file.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
                line = line.strip()
                if ":" in line:
                    k, v = line.split(":", 1)
                    if v.strip():
                        alts[k.strip()] = v.strip()
                elif line:
                    alts[f"line_{len(alts)+1}"] = line
        except Exception:
            pass

    # 2. Check exported_content/marketing/<pack>/alt.txt or carousel/alt.txt or pins.json
    pack_name = pack_dir.name
    marketing_dir = Path("exported_content") / "marketing" / pack_name
    if marketing_dir.is_dir():
        # Check carousel alt
        c_alt = marketing_dir / "carousel" / "alt.txt"
        if c_alt.is_file():
            try:
                for line in c_alt.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
                    if line.strip():
                        alts[f"carousel_{len(alts)+1}"] = line.strip()
            except Exception:
                pass
        # Check pins.json
        pins_json = marketing_dir / "pins" / "pins.json"
        if pins_json.is_file():
            try:
                data = json.loads(pins_json.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("alt"):
                            alts[item.get("file", f"pin_{len(alts)+1}")] = item["alt"]
            except Exception:
                pass

    return alts


def check_tag_duplicates(tags: List[str]) -> Tuple[int, List[str], List[Tuple[str, str]]]:
    """Check for duplicate tags and near-duplicate stems."""
    seen_exact: Set[str] = set()
    exact_dups: List[str] = []
    stems_map: Dict[str, str] = {}
    near_dups: List[Tuple[str, str]] = []

    for tag in tags:
        t_clean = tag.strip().lower()
        if not t_clean:
            continue
        if t_clean in seen_exact:
            exact_dups.append(t_clean)
        else:
            seen_exact.add(t_clean)

        tag_words = [w for w in re.split(r"\s+", t_clean) if w]
        tag_stem = " ".join(simple_stem(w) for w in tag_words)
        if tag_stem in stems_map and t_clean != stems_map[tag_stem]:
            near_dups.append((stems_map[tag_stem], t_clean))
        else:
            stems_map[tag_stem] = t_clean

    total_issues = len(exact_dups) + len(near_dups)
    return total_issues, exact_dups, near_dups


def calculate_readability(text: str) -> Tuple[float, int, int]:
    """Calculate average sentence length in words."""
    if not text.strip():
        return 0.0, 0, 0
    # Split text into sentences using ., !, ?
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    if not sentences:
        return 0.0, 0, 0
    total_words = 0
    for s in sentences:
        words = [w for w in s.split() if w.strip()]
        total_words += len(words)
    avg_len = total_words / len(sentences)
    return round(avg_len, 1), len(sentences), total_words


def generate_tenglish_summary(score: int, checks: Dict[str, Any]) -> str:
    """Generate a short 1-line Tenglish summary for the owner."""
    failed = [k for k, v in checks.items() if v.get("score", 0) < v.get("max", 0)]
    if score >= 90:
        return f"SEO score {score}/100 baavundi! Listing full ready ga undi."
    elif score >= 75:
        if failed:
            first_fail = failed[0].replace("_", " ")
            return f"SEO score {score}/100 manchidi, kaani {first_fail} inka improve cheyavachhu."
        return f"SEO score {score}/100 manchidi, ready to list."
    elif score >= 50:
        fixes = ", ".join(f.replace("_", " ") for f in failed[:2])
        return f"SEO score {score}/100 maatrame vachindi. {fixes} saricheyali."
    else:
        return f"SEO score {score}/100 chaala low undi. Title, tags mariyu description baaga marchali."


def score_listing(
    listing_data: Dict[str, Any],
    pack_dir: Optional[Path] = None,
    alt_texts: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Calculate 0-100 SEO score for a listing with granular reasons."""
    title = (listing_data.get("title") or "").strip()
    tags = listing_data.get("tags") or []
    description = (listing_data.get("description") or "").strip()
    what_you_get = listing_data.get("what_you_get") or []

    # Main keyword
    main_kw = get_main_keyword(title, tags)

    checks: Dict[str, Any] = {}

    # 1. Main keyword in first 40 chars of title (20 pts)
    title_first_40 = title[:40].lower()
    kw_words = [w for w in main_kw.lower().split() if w]
    first_40_pass = False
    if main_kw and (main_kw.lower() in title_first_40 or all(w in title_first_40 for w in kw_words)):
        first_40_pass = True
        c1_score = 20
        c1_reason = f"Main keyword '{main_kw}' appears within the first 40 characters of the title."
    elif any(w in title_first_40 for w in kw_words if len(w) >= 3):
        c1_score = 10
        c1_reason = f"Part of main keyword '{main_kw}' appears in first 40 characters of title."
    else:
        c1_score = 0
        c1_reason = f"Main keyword '{main_kw}' is missing from the first 40 characters of the title."

    checks["keyword_in_title_front"] = {
        "score": c1_score,
        "max": 20,
        "passed": first_40_pass,
        "reason": c1_reason,
    }

    # 2. Keyword in first sentence of description (15 pts)
    first_sentence = ""
    if description:
        sentences = [s.strip() for s in re.split(r"[.\n!?]+", description) if s.strip()]
        if sentences:
            first_sentence = sentences[0]

    first_sentence_lower = first_sentence.lower()
    c2_pass = False
    if main_kw and (main_kw.lower() in first_sentence_lower or (kw_words and all(w in first_sentence_lower for w in kw_words))):
        c2_pass = True
        c2_score = 15
        c2_reason = f"Keyword '{main_kw}' found in first sentence of description."
    elif any(w in first_sentence_lower for w in kw_words if len(w) >= 3):
        c2_score = 8
        c2_reason = f"Partial keyword match in first sentence of description."
    else:
        c2_score = 0
        c2_reason = f"Keyword '{main_kw}' not found in first sentence of description."

    checks["keyword_in_first_sentence"] = {
        "score": c2_score,
        "max": 15,
        "passed": c2_pass,
        "reason": c2_reason,
    }

    # 3. Tag coverage of title words (15 pts)
    title_words = extract_keywords_from_title(title)
    if not title_words:
        c3_score = 0
        c3_pass = False
        c3_reason = "No significant keywords found in title to check tag coverage."
        covered_count = 0
    else:
        tags_flat = " ".join(tags).lower()
        covered = [w for w in title_words if w in tags_flat or simple_stem(w) in tags_flat]
        covered_count = len(covered)
        ratio = covered_count / len(title_words)
        if ratio >= 0.7:
            c3_score = 15
            c3_pass = True
            c3_reason = f"Tags cover {covered_count}/{len(title_words)} ({int(ratio*100)}%) title keywords."
        elif ratio >= 0.4:
            c3_score = 10
            c3_pass = False
            c3_reason = f"Tags cover {covered_count}/{len(title_words)} ({int(ratio*100)}%) title keywords (could improve)."
        else:
            c3_score = int(15 * ratio)
            c3_pass = False
            c3_reason = f"Low tag coverage: only {covered_count}/{len(title_words)} title keywords covered."

    checks["tag_coverage"] = {
        "score": c3_score,
        "max": 15,
        "passed": c3_pass,
        "reason": c3_reason,
        "title_words": title_words,
        "covered_count": covered_count,
    }

    # 4. No tag duplicates or near-duplicates (15 pts)
    issue_count, exact_dups, near_dups = check_tag_duplicates(tags)
    if not tags:
        c4_score = 0
        c4_pass = False
        c4_reason = "No tags provided."
    elif issue_count == 0:
        c4_score = 15
        c4_pass = True
        c4_reason = f"All {len(tags)} tags are unique with no duplicate stems."
    else:
        deduction = min(15, issue_count * 5)
        c4_score = 15 - deduction
        c4_pass = False
        reasons_list = []
        if exact_dups:
            reasons_list.append(f"exact duplicate: {exact_dups}")
        if near_dups:
            reasons_list.append(f"stem near-duplicates: {near_dups}")
        c4_reason = f"Found {issue_count} tag duplication issues ({', '.join(reasons_list)})."

    checks["tag_uniqueness"] = {
        "score": c4_score,
        "max": 15,
        "passed": c4_pass,
        "reason": c4_reason,
        "exact_duplicates": exact_dups,
        "near_duplicates": [f"{a} ~ {b}" for a, b in near_dups],
    }

    # 5. Description length & structure (bullets, "What you get") (15 pts)
    # Good length: >= 100 characters; structure: contains bullets (- or •) or "what you get" section
    has_bullets = bool(re.search(r"[-*•]\s+\w+", description) or what_you_get)
    has_what_you_get = bool(what_you_get or "what you get" in description.lower() or "what's included" in description.lower())
    desc_len = len(description)

    c5_points = 0
    if desc_len >= 150:
        c5_points += 5
    elif desc_len >= 60:
        c5_points += 3

    if has_bullets:
        c5_points += 5
    if has_what_you_get:
        c5_points += 5

    c5_pass = (c5_points >= 12)
    c5_reason = f"Description length {desc_len} chars, bullets={'yes' if has_bullets else 'no'}, what_you_get={'yes' if has_what_you_get else 'no'}."

    checks["description_structure"] = {
        "score": c5_points,
        "max": 15,
        "passed": c5_pass,
        "reason": c5_reason,
    }

    # 6. Readability: average sentence length <= 20 words (10 pts)
    avg_len, s_count, total_w = calculate_readability(description)
    if s_count == 0:
        c6_score = 0
        c6_pass = False
        c6_reason = "No sentences found in description."
    elif avg_len <= 20.0:
        c6_score = 10
        c6_pass = True
        c6_reason = f"Average sentence length is {avg_len} words (<= 20 words target)."
    elif avg_len <= 28.0:
        c6_score = 5
        c6_pass = False
        c6_reason = f"Average sentence length is {avg_len} words (slightly long; target <= 20)."
    else:
        c6_score = 2
        c6_pass = False
        c6_reason = f"Average sentence length is {avg_len} words (too long; target <= 20)."

    checks["readability"] = {
        "score": c6_score,
        "max": 10,
        "passed": c6_pass,
        "reason": c6_reason,
        "avg_sentence_length": avg_len,
    }

    # 7. Alt texts present for images (10 pts)
    # Check if images exist in pack, or if alt_texts provided
    images_count = 0
    if pack_dir and pack_dir.is_dir():
        for f in pack_dir.iterdir():
            if f.suffix.lower() in IMAGE_EXTENSIONS:
                images_count += 1
    if alt_texts is None and pack_dir:
        alt_texts = find_alt_texts(pack_dir)
    elif alt_texts is None:
        alt_texts = {}

    if images_count == 0 and not alt_texts:
        # If no images in folder and no alt texts passed, check if pack has covers/previews
        # Or if this is evaluated on plain text with no folder, grant neutral full or partial
        c7_score = 10
        c7_pass = True
        c7_reason = "No images requiring alt text found in pack."
    elif len(alt_texts) >= max(1, images_count):
        c7_score = 10
        c7_pass = True
        c7_reason = f"Alt texts present ({len(alt_texts)} alt texts for {images_count} images)."
    elif len(alt_texts) > 0:
        ratio = len(alt_texts) / max(1, images_count)
        c7_score = int(10 * ratio)
        c7_pass = False
        c7_reason = f"Partial alt texts ({len(alt_texts)} alt texts for {images_count} images)."
    else:
        c7_score = 0
        c7_pass = False
        c7_reason = f"Missing alt texts for {images_count} images."

    checks["alt_texts_present"] = {
        "score": c7_score,
        "max": 10,
        "passed": c7_pass,
        "reason": c7_reason,
        "alts_count": len(alt_texts),
        "images_count": images_count,
    }

    total_score = sum(c["score"] for c in checks.values())
    total_score = max(0, min(100, total_score))

    tenglish_summary = generate_tenglish_summary(total_score, checks)

    result = {
        "score": total_score,
        "main_keyword": main_kw,
        "checks": checks,
        "tenglish_summary": tenglish_summary,
    }
    return result


def score_pack_folder(pack_dir: Path | str) -> Dict[str, Any]:
    """Score a pack folder by reading listing.txt (or listing.md) and images."""
    p = Path(pack_dir)
    if not p.is_dir():
        raise FileNotFoundError(f"Pack directory not found: {p}")

    listing_file = p / "listing.txt"
    if not listing_file.is_file():
        listing_file = p / "listing.md"

    if not listing_file.is_file():
        raise FileNotFoundError(f"Neither listing.txt nor listing.md found in {p}")

    content = listing_file.read_text(encoding="utf-8-sig", errors="ignore")
    listing_data = parse_listing_text(content)
    alt_texts = find_alt_texts(p)

    return score_listing(listing_data, pack_dir=p, alt_texts=alt_texts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate SEO score for pack listings.")
    parser.add_argument("pack", help="Path to upload pack folder or listing file.")
    parser.add_argument("--json", action="store_true", help="Output only JSON.")
    args = parser.parse_args()

    target = Path(args.pack)
    if target.is_dir():
        res = score_pack_folder(target)
    elif target.is_file():
        content = target.read_text(encoding="utf-8-sig", errors="ignore")
        data = parse_listing_text(content)
        res = score_listing(data)
    else:
        print(f"Error: {target} not found", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"SEO Score: {res['score']}/100")
        print(f"Main Keyword: {res['main_keyword']}")
        print(f"Tenglish Summary: {res['tenglish_summary']}")
        print("\nBreakdown:")
        for name, item in res["checks"].items():
            print(f"  - {name}: {item['score']}/{item['max']} pts ({'PASS' if item['passed'] else 'FAIL'}) — {item['reason']}")


if __name__ == "__main__":
    main()
