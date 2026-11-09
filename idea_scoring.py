#!/usr/bin/env python3
"""idea_scoring.py — Smart offline idea scoring for new product concepts.

Scores candidate product ideas (list of {title, tags, niche}) offline on a 0–100 scale:
  1. Demand proxy (0–30 pts):
     Matches against keywords.json (or keyword_bank offline engine) — high pack sharing & demand.
  2. Overlap penalty / Novelty (0–25 pts):
     Avoids duplicating existing ready packs; rewards fresh product angles.
  3. Season fit (0–25 pts):
     Checks current date window and triggers from gift_guide seasonal collections.
  4. Simplicity (0–20 pts):
     Rewards simple, proven formats (printable, worksheet, tracker, checklist, log, template, planner).

Generates owner-facing explanations in Tenglish house style (Telugu sentence words + English nouns).
No money amounts or brand names anywhere in generated text or code.
CLI accepts a JSON file with idea objects and outputs scores atomically.
Not wired into the pipeline.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# Optional import of gift_guide
try:
    import gift_guide
except Exception:  # pragma: no cover
    gift_guide = None

# Optional import of keyword_bank
try:
    import keyword_bank
except Exception:  # pragma: no cover
    keyword_bank = None

# Known simplicity / format keywords that make a digital product fast and simple to build
SIMPLICITY_FORMAT_WORDS: Set[str] = {
    "printable",
    "worksheet",
    "worksheets",
    "tracker",
    "trackers",
    "checklist",
    "checklists",
    "log",
    "logs",
    "template",
    "templates",
    "planner",
    "planners",
    "sheet",
    "sheets",
    "journal",
    "guide",
    "cheatsheet",
    "organizer",
    "organizers",
    "schedule",
    "routine",
    "prompts",
    "cards",
    "flashcards",
    "workbook",
    "workbooks",
    "binder",
}

# Stop words ignored during tokenization
STOP_WORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "what",
    "which", "this", "that", "these", "those", "then", "just", "so", "than",
    "such", "both", "through", "about", "for", "is", "of", "while", "during",
    "to", "from", "in", "out", "on", "off", "again", "further", "once",
    "here", "there", "when", "where", "why", "how", "all", "any", "each",
    "few", "more", "most", "other", "some", "no", "nor", "not", "only",
    "own", "same", "too", "very", "can", "will", "with", "by", "at",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
}

# Disallowed brand words to strip or flag
BRAND_WORDS = re.compile(
    r"\b(canva|etsy|gumroad|topmate|payhip|notion|goodnotes|excel|microsoft|google|amazon|ipad|apple|facebook|instagram|tiktok|pinterest)\b",
    re.IGNORECASE,
)

# Currency patterns strictly forbidden
MONEY_PATTERN = re.compile(
    r"(?:[\$\u20B9\u20AC\u00A3\u00A5]|\b(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?)\b)\s*\d|\d[\d,.]*\s*(?:[\$\u20B9\u20AC\u00A3\u00A5]|\b(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?)\b)",
    re.IGNORECASE,
)


def tokenize_text(text: str) -> List[str]:
    """Tokenize a string into clean lowercase alphanumeric words, skipping stop words."""
    if not text:
        return []
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) >= 2]


def load_keywords_bank(
    keywords_path: Optional[str | Path] = None,
    products_dir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Load keyword bank data from file or build offline if available."""
    # 1. Try explicit path
    if keywords_path:
        kp = Path(keywords_path)
        if kp.is_file():
            try:
                data = json.loads(kp.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "keywords" in data:
                    return data
            except Exception:
                pass

    # 2. Try default location
    default_p = Path("exported_content/marketing/keywords.json")
    if default_p.is_file():
        try:
            data = json.loads(default_p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "keywords" in data:
                return data
        except Exception:
            pass

    # 3. Fallback: generate offline via keyword_bank module if present
    if keyword_bank is not None:
        try:
            target_pdir = products_dir or "exported_content/products"
            return keyword_bank.build_keyword_bank(products_dir=target_pdir)
        except Exception:
            pass

    return {"keywords": []}


def load_existing_packs(products_dir: Optional[str | Path] = None) -> List[Dict[str, Any]]:
    """Scan products directory for existing upload packs (titles, tags, folder names)."""
    p_dir = Path(products_dir or "exported_content/products")
    packs: List[Dict[str, Any]] = []

    candidates = [
        p_dir / "upload",
        p_dir,
    ]
    seen_dirs: Set[Path] = set()

    for cand in candidates:
        if cand.is_dir():
            for sub in cand.iterdir():
                if sub.is_dir() and sub not in seen_dirs:
                    listing_file = sub / "listing.txt"
                    if not listing_file.is_file():
                        listing_file = sub / "listing.md"
                    if listing_file.is_file():
                        seen_dirs.add(sub)
                        title = ""
                        tags: List[str] = []
                        try:
                            content = listing_file.read_text(encoding="utf-8-sig", errors="ignore")
                            for line in content.splitlines():
                                lower = line.strip().lower()
                                if lower.startswith("title:") or lower.startswith("# "):
                                    title = line.split(":", 1)[-1].lstrip("#").strip()
                                elif lower.startswith("tags:") or lower.startswith("tag:"):
                                    raw_tags = line.split(":", 1)[-1].strip()
                                    tags = [t.strip().lower() for t in raw_tags.split(",") if t.strip()]
                        except Exception:
                            pass
                        packs.append({
                            "name": sub.name,
                            "title": title or sub.name.replace("-", " "),
                            "tags": tags,
                            "tokens": set(tokenize_text(title + " " + " ".join(tags) + " " + sub.name.replace("-", " "))),
                        })

    return packs


def calculate_demand_score(
    idea: Dict[str, Any],
    keywords_data: Dict[str, Any],
) -> Tuple[int, Dict[str, Any]]:
    """Calculate market demand proxy score (0–30 pts).

    Evaluates keyword presence, pack sharing counts, and long-tail matches in keyword bank.
    """
    title = str(idea.get("title", ""))
    tags = idea.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    niche = str(idea.get("niche", ""))

    combined_text = f"{title} {' '.join(tags)} {niche}".lower()
    idea_tokens = set(tokenize_text(combined_text))

    kw_list = keywords_data.get("keywords", [])
    if not kw_list:
        # Fallback heuristic: check seed planner/printable demand
        seed_hits = sum(1 for w in idea_tokens if w in SIMPLICITY_FORMAT_WORDS)
        score = min(30, seed_hits * 6 + 10)
        return score, {
            "score": score,
            "max": 30,
            "matched_keywords": [],
            "max_pack_sharing": 0,
            "detail": "Default heuristic demand (keyword bank empty or unavailable)",
        }

    matched_kws: List[Dict[str, Any]] = []
    max_sharing = 0
    total_kw_score = 0

    for item in kw_list:
        kw_text = item.get("keyword", "").lower()
        kw_tokens = set(tokenize_text(kw_text))
        if kw_tokens and kw_tokens.issubset(idea_tokens):
            pack_cnt = int(item.get("pack_count", 0))
            item_score = int(item.get("score", 0))
            if pack_cnt > max_sharing:
                max_sharing = pack_cnt
            total_kw_score += item_score
            matched_kws.append({"keyword": kw_text, "score": item_score, "pack_count": pack_cnt})
        elif kw_text in combined_text:
            pack_cnt = int(item.get("pack_count", 0))
            item_score = int(item.get("score", 0))
            if pack_cnt > max_sharing:
                max_sharing = pack_cnt
            total_kw_score += item_score
            matched_kws.append({"keyword": kw_text, "score": item_score, "pack_count": pack_cnt})

    # Sort matched by score descending
    matched_kws.sort(key=lambda x: (x["score"], x["pack_count"]), reverse=True)
    top_matches = matched_kws[:5]

    # Calculate pts
    # Base: number of matches (up to 12 pts)
    match_pts = min(12, len(matched_kws) * 3)
    # Sharing: packs sharing terms (up to 12 pts)
    sharing_pts = min(12, max_sharing * 4)
    # Total relevance bonus (up to 6 pts)
    rel_pts = min(6, int(total_kw_score / 10))

    final_score = min(30, match_pts + sharing_pts + rel_pts)
    if not matched_kws and any(w in SIMPLICITY_FORMAT_WORDS for w in idea_tokens):
        final_score = max(final_score, 8)

    return final_score, {
        "score": final_score,
        "max": 30,
        "matched_keywords": [m["keyword"] for m in top_matches],
        "max_pack_sharing": max_sharing,
        "detail": f"{len(matched_kws)} keywords matched in bank",
    }


def calculate_overlap_score(
    idea: Dict[str, Any],
    existing_packs: List[Dict[str, Any]],
) -> Tuple[int, Dict[str, Any]]:
    """Calculate overlap penalty / novelty score (0–25 pts).

    25 pts = completely fresh idea, no duplicate overlap.
    Deducts points if existing packs have high word/tag overlap (Jaccard similarity).
    """
    if not existing_packs:
        return 25, {
            "score": 25,
            "max": 25,
            "max_overlap_ratio": 0.0,
            "closest_pack": None,
            "detail": "Existing packs ledu or zero overlap (completely novel)",
        }

    title = str(idea.get("title", ""))
    tags = idea.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    niche = str(idea.get("niche", ""))

    idea_tokens = set(tokenize_text(f"{title} {' '.join(tags)} {niche}"))
    if not idea_tokens:
        return 15, {
            "score": 15,
            "max": 25,
            "max_overlap_ratio": 0.0,
            "closest_pack": None,
            "detail": "Empty idea tokens",
        }

    max_sim = 0.0
    closest_pack: Optional[str] = None

    for pack in existing_packs:
        pack_tokens = pack.get("tokens", set())
        if not pack_tokens:
            continue
        intersection = idea_tokens.intersection(pack_tokens)
        union = idea_tokens.union(pack_tokens)
        sim = len(intersection) / len(union) if union else 0.0

        if sim > max_sim:
            max_sim = sim
            closest_pack = pack.get("name") or pack.get("title")

    # Scoring:
    # sim < 0.15 -> 25 pts (fresh)
    # sim < 0.30 -> 20 pts (minor overlap, safe variation)
    # sim < 0.50 -> 12 pts (moderate overlap)
    # sim < 0.70 -> 5 pts (heavy overlap)
    # sim >= 0.70 -> 0 pts (duplicate risk)
    if max_sim < 0.15:
        score = 25
    elif max_sim < 0.30:
        score = 20
    elif max_sim < 0.50:
        score = 12
    elif max_sim < 0.70:
        score = 5
    else:
        score = 0

    return score, {
        "score": score,
        "max": 25,
        "max_overlap_ratio": round(max_sim, 3),
        "closest_pack": closest_pack,
        "detail": f"Max overlap ratio: {round(max_sim, 2)} with {closest_pack or 'none'}",
    }


def calculate_season_score(
    idea: Dict[str, Any],
    target_date: Optional[date] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Calculate seasonal fit score (0–25 pts).

    Uses gift_guide season definitions if importable.
    Checks if idea matches current active season window or evergreen seasonless utility.
    """
    if target_date is None:
        target_date = date.today()

    title = str(idea.get("title", ""))
    tags = idea.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    niche = str(idea.get("niche", ""))
    tokens = set(tokenize_text(f"{title} {' '.join(tags)} {niche}"))

    # Check evergreen keywords first
    evergreen_words = {"daily", "habit", "routine", "weekly", "monthly", "fitness", "budget", "cleaning", "organizer"}
    is_evergreen = any(w in evergreen_words for w in tokens)

    # Specific seasonal triggers (to avoid generic terms like daily/routine matching new_year inappropriately)
    season_specific_triggers: Dict[str, Set[str]] = {
        "diwali": {"diwali", "deepawali", "rangoli", "mithai", "puja", "festive", "diyas"},
        "new_year": {"new year", "resolutions", "annual review", "january", "fresh start", "vision board"},
        "back_to_school": {"school", "college", "semester", "classroom", "teacher", "curriculum", "campus"},
        "exam_season": {"exam", "exams", "revision", "finals", "test prep", "active recall", "pomodoro"},
        "wedding_season": {"wedding", "bridal", "bride", "groom", "ceremony", "mehendi", "sangeet", "honeymoon"},
        "summer_holidays": {"summer", "vacation", "trip", "road trip", "camp", "beach", "itinerary"},
    }

    matching_seasons: List[str] = []
    active_season_match: Optional[str] = None

    seasons_dict = getattr(gift_guide, "SEASONS", {}) if gift_guide else {}
    if seasons_dict:
        combined_text = f"{title} {' '.join(tags)} {niche}".lower()
        for s_key, triggers in season_specific_triggers.items():
            if s_key in seasons_dict:
                matched_trigs = [trig for trig in triggers if trig in combined_text or trig in tokens]
                if matched_trigs:
                    matching_seasons.append(s_key)
                    if hasattr(gift_guide, "is_in_season_window") and gift_guide.is_in_season_window(s_key, target_date):
                        if not active_season_match:
                            active_season_match = s_key

    # Scoring logic:
    # 1. Matches an active season window -> 25 pts (peak seasonal timing)
    # 2. Specific seasonal fit, off-window right now -> 18 pts (upcoming seasonal opportunity)
    # 3. Evergreen daily habit/routine/organizer -> 16 pts (steady year-round demand)
    # 4. Generic/unknown -> 10 pts
    if active_season_match:
        score = 25
        detail = f"Active season lo unna product: {active_season_match}"
    elif matching_seasons:
        score = 18
        detail = f"Seasonal fit undi ({', '.join(matching_seasons)}), but off-window right now"
    elif is_evergreen:
        score = 16
        detail = "Evergreen product, year-round steady demand untundi"
    else:
        score = 10
        detail = "Neutral season fit"

    return score, {
        "score": score,
        "max": 25,
        "active_season": active_season_match,
        "matching_seasons": matching_seasons,
        "detail": detail,
    }


def calculate_simplicity_score(
    idea: Dict[str, Any],
) -> Tuple[int, Dict[str, Any]]:
    """Calculate simplicity and buildability score (0–20 pts).

    Rewards simple, high-converting digital formats (printable, worksheet, tracker, checklist, etc.).
    """
    title = str(idea.get("title", ""))
    tags = idea.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    niche = str(idea.get("niche", ""))
    tokens = set(tokenize_text(f"{title} {' '.join(tags)} {niche}"))

    found_formats = [w for w in tokens if w in SIMPLICITY_FORMAT_WORDS]

    # Complex formats to penalize (apps, software, physical, video courses, full dashboards)
    complex_words = {"software", "app", "dashboard", "course", "video", "notion", "excel", "sheets"}
    found_complex = [w for w in tokens if w in complex_words]

    # Scoring:
    # 2+ simplicity formats -> 20 pts
    # 1 simplicity format -> 15 pts
    # 0 format keywords -> 8 pts
    # Deduct 5 pts if complex keywords detected
    if len(found_formats) >= 2:
        base = 20
    elif len(found_formats) == 1:
        base = 15
    else:
        base = 8

    penalty = 6 if found_complex else 0
    score = max(0, min(20, base - penalty))

    return score, {
        "score": score,
        "max": 20,
        "format_keywords": sorted(set(found_formats)),
        "complex_keywords": sorted(set(found_complex)),
        "detail": f"{len(found_formats)} simple format keywords found",
    }


def generate_tenglish_reasons(
    total_score: int,
    demand: Dict[str, Any],
    overlap: Dict[str, Any],
    season: Dict[str, Any],
    simplicity: Dict[str, Any],
) -> List[str]:
    """Generate concise owner-facing reasons in Tenglish house style."""
    reasons: List[str] = []

    # Demand reason
    if demand["score"] >= 22:
        reasons.append("Demand chaala baavundi: search keywords bank lo strong pack sharing match ayyindi.")
    elif demand["score"] >= 14:
        reasons.append("Demand bagundi: matching keywords unnaayi.")
    else:
        reasons.append("Demand konchem low ga undi: keywords bank lo ekkuva matches levu.")

    # Overlap reason
    if overlap["score"] == 25:
        reasons.append("Existing pack tho overlap ledu: fresh product idea, unique angle.")
    elif overlap["score"] >= 18:
        reasons.append("Minor overlap maatrame undi: existing catalogue tho differentiate cheyavachu.")
    elif overlap["score"] >= 10:
        reasons.append(f"Catalogue tho moderate overlap undi ({overlap.get('closest_pack') or 'pack'}), unique features add cheyali.")
    else:
        reasons.append(f"Overlap chaala ekkuva undi ({overlap.get('closest_pack') or 'pack'}), duplicate risk untundi.")

    # Season reason
    if season.get("active_season"):
        reasons.append(f"Season timing perfect ga fit ayyindi: current active season window ({season['active_season']}).")
    elif season["score"] >= 16:
        reasons.append(season.get("detail", "Evergreen utility: round-the-year demand untundi."))
    else:
        reasons.append("Seasonal push ledu, neutral demand untundi.")

    # Simplicity reason
    if simplicity["score"] >= 18:
        reasons.append("Format chaala simple: proven printable/worksheet layout, build cheyadam easy.")
    elif simplicity["score"] >= 12:
        reasons.append("Format standard digital printable ga undi.")
    else:
        reasons.append("Format complex ga undi, simple worksheet ga simplify chesthe manchidi.")

    # Overall summary line
    if total_score >= 80:
        reasons.append(f"Final Verdict: {total_score}/100 — Super idea, direct ga build start cheyavachu.")
    elif total_score >= 60:
        reasons.append(f"Final Verdict: {total_score}/100 — Manchidi idea, konni tweaks tho launch cheyavachu.")
    else:
        reasons.append(f"Final Verdict: {total_score}/100 — Score takkuva undi, niche leda format revise cheyandi.")

    return reasons


def score_idea(
    idea: Dict[str, Any],
    keywords_data: Optional[Dict[str, Any]] = None,
    existing_packs: Optional[List[Dict[str, Any]]] = None,
    target_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Score a single candidate idea object {title, tags, niche}."""
    if not isinstance(idea, dict):
        idea = {}

    title = str(idea.get("title", "")).strip()
    tags = idea.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    elif not isinstance(tags, list):
        tags = []
    niche = str(idea.get("niche", "")).strip()

    clean_idea = {"title": title, "tags": tags, "niche": niche}

    kw_bank = keywords_data if keywords_data is not None else load_keywords_bank()
    packs = existing_packs if existing_packs is not None else load_existing_packs()

    d_score, d_meta = calculate_demand_score(clean_idea, kw_bank)
    o_score, o_meta = calculate_overlap_score(clean_idea, packs)
    s_score, s_meta = calculate_season_score(clean_idea, target_date=target_date)
    m_score, m_meta = calculate_simplicity_score(clean_idea)

    total_score = max(0, min(100, d_score + o_score + s_score + m_score))

    reasons = generate_tenglish_reasons(total_score, d_meta, o_meta, s_meta, m_meta)

    return {
        "title": title,
        "niche": niche,
        "tags": tags,
        "total_score": total_score,
        "components": {
            "demand": d_meta,
            "overlap": o_meta,
            "season": s_meta,
            "simplicity": m_meta,
        },
        "reasons": reasons,
        "tenglish_summary": reasons[-1],
    }


def score_ideas_list(
    ideas: Sequence[Dict[str, Any]],
    keywords_path: Optional[str | Path] = None,
    products_dir: Optional[str | Path] = None,
    target_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Score a list of idea objects and return results sorted descending by score."""
    if not ideas:
        return []

    kw_bank = load_keywords_bank(keywords_path=keywords_path, products_dir=products_dir)
    packs = load_existing_packs(products_dir=products_dir)

    results: List[Dict[str, Any]] = []
    for idea in ideas:
        scored = score_idea(idea, keywords_data=kw_bank, existing_packs=packs, target_date=target_date)
        results.append(scored)

    # Sort descending by total_score, then title
    results.sort(key=lambda x: (x["total_score"], x["title"]), reverse=True)
    return results


def atomic_write_json(filepath: Path | str, data: Any) -> None:
    """Atomically write JSON data to filepath."""
    target = Path(filepath)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    tmp = target.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp.write_text(serialized, encoding="utf-8")
        tmp.replace(target)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def parse_ideas_file(file_path: Path | str) -> List[Dict[str, Any]]:
    """Parse a JSON file containing a list of ideas or a single idea dictionary."""
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"Ideas file kanipinchaledu: {file_path}")

    content = p.read_text(encoding="utf-8")
    data = json.loads(content)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        if "ideas" in data and isinstance(data["ideas"], list):
            return [item for item in data["ideas"] if isinstance(item, dict)]
        return [data]
    return []


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for idea scoring."""
    parser = argparse.ArgumentParser(description="Offline smart idea scoring tool")
    parser.add_argument("input_file", help="Path to JSON file containing ideas to score")
    parser.add_argument("--keywords", dest="keywords_path", default=None, help="Path to keywords.json")
    parser.add_argument("--products-dir", dest="products_dir", default="exported_content/products", help="Path to products directory")
    parser.add_argument("--out", dest="out_path", default=None, help="Optional output JSON path for atomic write")
    parser.add_argument("--json", action="store_true", help="Output JSON directly to stdout")

    args = parser.parse_args(argv)

    try:
        ideas = parse_ideas_file(args.input_file)
    except Exception as e:
        sys.stderr.write(f"Error reading ideas file: {e}\n")
        return 1

    scored = score_ideas_list(
        ideas,
        keywords_path=args.keywords_path,
        products_dir=args.products_dir,
    )

    if args.out_path:
        atomic_write_json(args.out_path, scored)
        sys.stdout.write(f"Scored {len(scored)} ideas. Output written to {args.out_path}\n")

    if args.json or not args.out_path:
        if args.json:
            sys.stdout.write(json.dumps(scored, indent=2, ensure_ascii=False) + "\n")
        else:
            for item in scored:
                sys.stdout.write(f"\n========================================\n")
                sys.stdout.write(f"Idea: {item.get('title')} ({item.get('niche')})\n")
                sys.stdout.write(f"Score: {item.get('total_score')}/100\n")
                sys.stdout.write(f"Components:\n")
                sys.stdout.write(f"  - Demand:    {item['components']['demand']['score']}/30\n")
                sys.stdout.write(f"  - Overlap:   {item['components']['overlap']['score']}/25\n")
                sys.stdout.write(f"  - Season:    {item['components']['season']['score']}/25\n")
                sys.stdout.write(f"  - Simplicity:{item['components']['simplicity']['score']}/20\n")
                sys.stdout.write(f"Reasons (Tenglish):\n")
                for r in item["reasons"]:
                    sys.stdout.write(f"  * {r}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
