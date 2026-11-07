"""bundle_builder.py — Bundle Builder: sell 2–4 related packs together.

Owner sets the price; nothing is listed or published automatically.
Suggests bundles of 2–4 ready packs based on keyword/tag overlap (Jaccard >= 0.25)
and common audience, and builds complete bundle upload packs containing:
  - bundle.zip (member product.zip files cleanly named inside)
  - cover.png (1280x720 fan layout of member covers created with Pillow)
  - listing.txt (title, description, 13 deduplicated tags, member "What you get")
  - LICENSE.txt (copied from a member pack)
  - External deep QA check report if product_qa_deep is available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

import upload_pack

# Common words to exclude from audience and keyword comparison
STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
    "during", "each", "few", "for", "from", "further", "had", "hadn't", "has",
    "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
    "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
    "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it",
    "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my",
    "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other",
    "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "shan't",
    "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves", "pack", "bundle", "kit", "template", "templates",
    "digital", "download", "print", "printable", "sheets", "sheet"
}

AUDIENCE_INDICATORS = {
    "adhd": {"adhd", "neurodivergent", "focus", "dopamine", "executive"},
    "freelancer": {"freelancer", "freelance", "contractor", "solopreneur", "client", "invoice"},
    "budget": {"budget", "finance", "money", "spending", "savings", "debt", "expense"},
    "resume": {"resume", "cv", "job", "career", "interview", "ats", "hiring"},
    "planner_2027": {"2027", "annual", "calendar", "dated"},
    "notion": {"notion", "workspace", "second brain"},
    "student": {"student", "study", "college", "school", "academic"},
    "teacher": {"teacher", "classroom", "lesson", "school"},
    "fitness": {"fitness", "workout", "gym", "health", "exercise"},
}

COVER_WIDTH = 1280
COVER_HEIGHT = 720
MAX_BUNDLE_UNCOMPRESSED_BYTES = 250 * 1024 * 1024  # 250 MB


def _extract_pack_info(pack_input: Union[str, Path, Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize pack input (dict or folder Path) into a standardized dict."""
    if isinstance(pack_input, dict):
        info = dict(pack_input)
        folder = info.get("folder") or info.get("path") or ""
        folder_path = Path(folder) if folder else None
    else:
        folder_path = Path(pack_input).resolve()
        info = {"folder": str(folder_path)}

    info.setdefault("pack_id", folder_path.name if folder_path else "pack")

    # If folder is present on disk, inspect files
    if folder_path and folder_path.is_dir():
        listing_file = folder_path / "listing.txt"
        if not listing_file.is_file():
            listing_file = folder_path / "listing.md"
        if listing_file.is_file() and not info.get("listing_raw"):
            try:
                info["listing_raw"] = listing_file.read_text(encoding="utf-8-sig")
            except Exception:
                pass

        if not info.get("cover_path"):
            cover_candidates = ["cover.png", "cover.jpg", "cover.jpeg", "cover.webp"]
            for cand in cover_candidates:
                p = folder_path / cand
                if p.is_file():
                    info["cover_path"] = str(p)
                    break

        if not info.get("product_zip"):
            zip_p = folder_path / "product.zip"
            if zip_p.is_file():
                info["product_zip"] = str(zip_p)
            else:
                for z in sorted(folder_path.glob("*.zip")):
                    info["product_zip"] = str(z)
                    break

        if not info.get("license_path"):
            lic = folder_path / "LICENSE.txt"
            if lic.is_file():
                info["license_path"] = str(lic)

    # Parse listing_raw if present to populate title, description, tags, what you get
    raw = info.get("listing_raw", "")
    if raw:
        _parse_listing_raw(raw, info)

    # Ensure tags is a list of strings
    tags = info.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    info["tags"] = [str(t).strip() for t in tags if str(t).strip()]

    # Extract keywords/tokens
    info["keywords"] = _extract_keywords(info)
    info["audience"] = _determine_audience(info)

    return info


def _parse_listing_raw(raw: str, info: Dict[str, Any]) -> None:
    lines = raw.splitlines()
    title = info.get("title")
    desc_lines = []
    tags_list = info.get("tags") or []
    what_get_lines = []

    current_section = None
    for line in lines:
        sline = line.strip()
        # Headings or keys
        m_title = re.match(r"^(?:#+\s*|Title:\s*)(.*)$", sline, re.IGNORECASE)
        m_head = re.match(r"^#{1,6}\s+(.+)$", sline)
        if not title and m_title and not sline.startswith("###"):
            cand = m_title.group(1).strip()
            if cand and not any(cand.lower().startswith(k) for k in ("description", "tags", "what you get")):
                title = cand
                continue

        if m_head:
            head_name = m_head.group(1).lower().replace("’", "'").strip()
            if "description" in head_name:
                current_section = "desc"
                continue
            elif "tags" in head_name:
                current_section = "tags"
                continue
            elif any(k in head_name for k in ("what you get", "what's included", "what's inside", "included files")):
                current_section = "get"
                continue
            elif any(k in head_name for k in ("license", "faq", "how to")):
                current_section = "other"
                continue

        if sline.lower().startswith("description:"):
            current_section = "desc"
            rem = sline.split(":", 1)[1].strip()
            if rem:
                desc_lines.append(rem)
            continue
        elif sline.lower().startswith("tags:"):
            current_section = "tags"
            rem = sline.split(":", 1)[1].strip()
            if rem:
                tags_list.extend(t.strip() for t in rem.split(",") if t.strip())
            continue
        elif any(sline.lower().startswith(k + ":") for k in ("what you get", "what's included")):
            current_section = "get"
            rem = sline.split(":", 1)[1].strip()
            if rem:
                what_get_lines.append(rem)
            continue

        if current_section == "desc":
            desc_lines.append(line)
        elif current_section == "tags":
            tags_list.extend(t.strip() for t in sline.lstrip("-*• ").split(",") if t.strip())
        elif current_section == "get":
            clean_item = sline.lstrip("-*• 0123456789.)").strip()
            if clean_item:
                what_get_lines.append(clean_item)

    if title and not info.get("title"):
        info["title"] = title
    if desc_lines and not info.get("description"):
        info["description"] = "\n".join(desc_lines).strip()
    if tags_list:
        info["tags"] = tags_list
    if what_get_lines and not info.get("what_you_get"):
        info["what_you_get"] = what_get_lines


def _extract_keywords(info: Dict[str, Any]) -> Set[str]:
    """Collect lowercase normalized keywords from title, tags, description, and pack_id."""
    words: Set[str] = set()

    for tag in info.get("tags", []):
        for token in re.findall(r"[a-z0-9]+", tag.lower()):
            if len(token) > 2 and token not in STOP_WORDS:
                words.add(token)

    title = info.get("title", "")
    for token in re.findall(r"[a-z0-9]+", title.lower()):
        if len(token) > 2 and token not in STOP_WORDS:
            words.add(token)

    pack_id = info.get("pack_id", "")
    for token in re.findall(r"[a-z0-9]+", pack_id.lower()):
        if len(token) > 2 and token not in STOP_WORDS:
            words.add(token)

    desc = info.get("description", "")
    for token in re.findall(r"[a-z0-9]+", desc.lower()[:300]):
        if len(token) > 2 and token not in STOP_WORDS:
            words.add(token)

    return words


def _determine_audience(info: Dict[str, Any]) -> str:
    """Identify buyer audience niche based on audience keywords."""
    combined_text = " ".join([
        info.get("pack_id", ""),
        info.get("title", ""),
        " ".join(info.get("tags", [])),
        info.get("description", "")[:400]
    ]).lower()

    scores: Dict[str, int] = {}
    for niche, indicators in AUDIENCE_INDICATORS.items():
        score = sum(1 for ind in indicators if re.search(r"\b" + re.escape(ind) + r"\b", combined_text))
        if score > 0:
            scores[niche] = score

    if not scores:
        return "general"
    return max(scores.items(), key=lambda x: x[1])[0]


def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Calculate Jaccard index between two token sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


def _group_jaccard(packs: List[Dict[str, Any]]) -> float:
    """Average pairwise Jaccard similarity across all pairs in the group."""
    if len(packs) < 2:
        return 0.0
    total = 0.0
    count = 0
    for i in range(len(packs)):
        for j in range(i + 1, len(packs)):
            total += jaccard_similarity(packs[i]["keywords"], packs[j]["keywords"])
            count += 1
    return total / count if count > 0 else 0.0


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:50] or "bundle"


def _make_bundle_id(member_ids: List[str]) -> str:
    """Generate a deterministic bundle ID from sorted member pack IDs."""
    sorted_ids = sorted(member_ids)
    clean_parts = []
    for mid in sorted_ids:
        # Strip leading numbers or "pack-"
        sub = re.sub(r"^\d+[-_]?", "", mid)
        clean_parts.append(_slugify(sub)[:15])
    short_slug = "-".join(clean_parts[:3])
    raw_hash = hashlib.sha256(":".join(sorted_ids).encode("utf-8")).hexdigest()[:8]
    return f"bundle-{short_slug}-{raw_hash}".rstrip("-")


def suggest_bundles(packs: List[Union[str, Path, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Suggest groups of 2–4 ready packs that fit together.

    Conditions:
    - Jaccard similarity >= 0.25 between all member pairs (or group average >= 0.25 and every pair >= 0.20)
    - Compatible audience words (never mix unrelated packs, e.g. ADHD + Resume or Budget + Resume)
    - Group size: 2 to 4 packs
    - Max 5 suggestions
    - Each suggestion includes member packs, deterministic bundle_id, and a reason line.
    """
    if not packs or len(packs) < 2:
        return []

    parsed_packs = [_extract_pack_info(p) for p in packs]

    # Pre-filter: only packs with keywords
    valid_packs = [p for p in parsed_packs if p["keywords"]]
    if len(valid_packs) < 2:
        return []

    n = len(valid_packs)
    # Group packs by audience (if specific audience)
    # Unrelated audiences can never be mixed
    candidate_groups: List[Tuple[float, List[Dict[str, Any]]]] = []

    # Helper: check pair compatibility
    def compatible(p1: Dict[str, Any], p2: Dict[str, Any]) -> Tuple[bool, float]:
        a1, a2 = p1["audience"], p2["audience"]
        if a1 != "general" and a2 != "general" and a1 != a2:
            return False, 0.0
        j = jaccard_similarity(p1["keywords"], p2["keywords"])
        if j < 0.25:
            return False, j
        return True, j

    # 1. Size 2 pairs
    for i in range(n):
        for j in range(i + 1, n):
            p1, p2 = valid_packs[i], valid_packs[j]
            comp, sim = compatible(p1, p2)
            if comp:
                candidate_groups.append((sim, [p1, p2]))

    # 2. Size 3 groups
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                group = [valid_packs[i], valid_packs[j], valid_packs[k]]
                # Check pairwise compatibility
                all_pairs_comp = True
                sims = []
                for x in range(len(group)):
                    for y in range(x + 1, len(group)):
                        comp, sim = compatible(group[x], group[y])
                        if not comp:
                            all_pairs_comp = False
                            break
                        sims.append(sim)
                    if not all_pairs_comp:
                        break
                if all_pairs_comp and sims:
                    avg_sim = sum(sims) / len(sims)
                    # Boost larger cohesive bundles slightly in score
                    candidate_groups.append((avg_sim * 1.1, group))

    # 3. Size 4 groups
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                for m in range(k + 1, n):
                    group = [valid_packs[i], valid_packs[j], valid_packs[k], valid_packs[m]]
                    all_pairs_comp = True
                    sims = []
                    for x in range(len(group)):
                        for y in range(x + 1, len(group)):
                            comp, sim = compatible(group[x], group[y])
                            if not comp:
                                all_pairs_comp = False
                                break
                            sims.append(sim)
                        if not all_pairs_comp:
                            break
                    if all_pairs_comp and sims:
                        avg_sim = sum(sims) / len(sims)
                        candidate_groups.append((avg_sim * 1.2, group))

    # Sort candidates by score descending, then by sorted member ids for determinism
    candidate_groups.sort(
        key=lambda item: (-item[0], sorted(p["pack_id"] for p in item[1]))
    )

    seen_sets: Set[Tuple[str, ...]] = set()
    suggestions: List[Dict[str, Any]] = []

    for sim_score, grp in candidate_groups:
        member_ids = tuple(sorted(p["pack_id"] for p in grp))
        if member_ids in seen_sets:
            continue
        seen_sets.add(member_ids)

        bundle_id = _make_bundle_id(list(member_ids))

        # Build reason line
        shared_keywords = set(grp[0]["keywords"])
        for p in grp[1:]:
            shared_keywords &= p["keywords"]

        aud = grp[0]["audience"]
        if aud == "general":
            for p in grp[1:]:
                if p["audience"] != "general":
                    aud = p["audience"]
                    break

        if aud == "adhd":
            aud_label = "ADHD"
        elif aud == "planner_2027":
            aud_label = "2027 Planner"
        else:
            aud_label = aud.replace("_", " ").title() if aud != "general" else "productivity"
        top_keys = sorted(shared_keywords)[:3]
        keys_str = ", ".join(top_keys) if top_keys else "matching workflow"

        reason = (f"Combines {len(grp)} complementary {aud_label} packs "
                  f"(shared themes: {keys_str}; tag overlap: {sim_score:.2f}).")

        suggestions.append({
            "bundle_id": bundle_id,
            "packs": grp,
            "pack_ids": list(member_ids),
            "size": len(grp),
            "similarity": round(sim_score, 3),
            "audience": aud,
            "reason": reason
        })

        if len(suggestions) >= 5:
            break

    return suggestions


def _create_fan_cover(member_covers: List[str], output_path: str) -> None:
    """Create a 1280x720 fan cover image using Pillow.

    Arranges member covers as a centered overlapping fan.
    Guarantees:
    - Exactly 1280x720
    - Text/graphics never cut
    - Shadows and clean borders
    - Works with 2, 3, or 4 covers
    """
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required to create bundle cover image.")

    canvas = Image.new("RGB", (COVER_WIDTH, COVER_HEIGHT), color=(244, 246, 248))
    draw = ImageDraw.Draw(canvas)

    # Gradient or soft subtle background
    for y in range(COVER_HEIGHT):
        ratio = y / COVER_HEIGHT
        r = int(240 - 15 * ratio)
        g = int(244 - 15 * ratio)
        b = int(248 - 10 * ratio)
        draw.line([(0, y), (COVER_WIDTH, y)], fill=(r, g, b))

    # Outer decorative frame
    draw.rectangle([16, 16, COVER_WIDTH - 17, COVER_HEIGHT - 17], outline=(220, 226, 234), width=2)

    # Load and prepare images
    loaded_imgs: List[Image.Image] = []
    for c_path in member_covers:
        try:
            if c_path and os.path.isfile(c_path):
                img = Image.open(c_path).convert("RGBA")
            else:
                img = _create_fallback_card("Pack Item")
        except Exception:
            img = _create_fallback_card("Pack Item")
        loaded_imgs.append(img)

    num = len(loaded_imgs)
    if num == 0:
        loaded_imgs = [_create_fallback_card("Bundle Item 1"), _create_fallback_card("Bundle Item 2")]
        num = 2

    # Fit each image into a card target size
    # For a 1280x720 canvas, card size around 460x345 or 420x315 looks balanced
    card_w = 460 if num <= 3 else 390
    card_h = int(card_w * 9 / 16)  # 16:9 aspect ratio

    cards: List[Image.Image] = []
    for im in loaded_imgs:
        resized = im.resize((card_w, card_h), Image.Resampling.LANCZOS)
        # Add a subtle 2px border inside card
        card_with_border = Image.new("RGBA", (card_w, card_h))
        card_with_border.paste(resized, (0, 0))
        c_draw = ImageDraw.Draw(card_with_border)
        c_draw.rectangle([0, 0, card_w - 1, card_h - 1], outline=(200, 205, 215, 200), width=2)
        cards.append(card_with_border)

    # Layout angles and horizontal centers
    if num == 2:
        angles = [-6, 6]
        x_offsets = [COVER_WIDTH // 2 - 180, COVER_WIDTH // 2 + 180]
        y_center = COVER_HEIGHT // 2 + 10
    elif num == 3:
        angles = [-10, 0, 10]
        x_offsets = [COVER_WIDTH // 2 - 270, COVER_WIDTH // 2, COVER_WIDTH // 2 + 270]
        y_center = COVER_HEIGHT // 2 + 15
    else:  # 4 or more
        angles = [-12, -4, 4, 12]
        x_offsets = [
            COVER_WIDTH // 2 - 340,
            COVER_WIDTH // 2 - 110,
            COVER_WIDTH // 2 + 110,
            COVER_WIDTH // 2 + 340,
        ]
        y_center = COVER_HEIGHT // 2 + 15

    # Composite with drop shadows onto canvas
    for i, card in enumerate(cards):
        angle = angles[i]
        cx = x_offsets[i]
        cy = y_center

        # Rotate card
        rotated = card.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)

        # Generate drop shadow
        shadow = Image.new("RGBA", rotated.size, (0, 0, 0, 0))
        # Mask from rotated alpha
        r_alpha = rotated.split()[-1]
        shadow_fill = Image.new("RGBA", rotated.size, (20, 25, 35, 75))
        shadow.paste(shadow_fill, (0, 0), r_alpha)
        # Blur shadow
        shadow = shadow.filter(ImageFilter.GaussianBlur(10))

        # Paste shadow offset
        pos_shadow = (cx - rotated.width // 2 + 6, cy - rotated.height // 2 + 10)
        canvas.paste(shadow, pos_shadow, shadow)

        # Paste rotated card
        pos_card = (cx - rotated.width // 2, cy - rotated.height // 2)
        canvas.paste(rotated, pos_card, rotated)

    # Add bundle badge or title bar at top
    # Text banner: "VALUE BUNDLE — ALL PACKS INCLUDED"
    banner_bg = Image.new("RGBA", (500, 48), (30, 41, 59, 230))
    b_draw = ImageDraw.Draw(banner_bg)
    b_draw.rectangle([0, 0, 499, 47], outline=(255, 255, 255, 60), width=1)
    b_text = f"BUNDLE • {num} COMPLETE PACKS INCLUDED"
    # Centered text using default font
    bbox = b_draw.textbbox((0, 0), b_text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    b_draw.text(((500 - tw) // 2, (48 - th) // 2), b_text, fill=(255, 255, 255))

    banner_pos = ((COVER_WIDTH - 500) // 2, 38)
    canvas.paste(banner_bg, banner_pos, banner_bg)

    canvas.save(output_path, "PNG")


def _create_fallback_card(text: str) -> Image.Image:
    """Create a placeholder card image if cover is missing."""
    card = Image.new("RGBA", (640, 360), (235, 238, 245, 255))
    draw = ImageDraw.Draw(card)
    draw.rectangle([8, 8, 631, 351], outline=(180, 190, 205), width=2)
    bbox = draw.textbbox((0, 0), text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((640 - tw) // 2, (360 - th) // 2), text, fill=(70, 80, 95))
    return card


def _build_bundle_listing(
    bundle_title: str,
    member_packs: List[Dict[str, Any]],
    merged_tags: List[str]
) -> str:
    """Compose listing.txt for the bundle."""
    # Build Description
    desc_lines = [
        f"Get {len(member_packs)} complete, matching digital products together in this comprehensive bundle.",
        "",
        "What you will receive in this collection:",
        ""
    ]

    all_what_you_get: List[str] = []

    for i, p in enumerate(member_packs, 1):
        p_title = p.get("title") or p.get("pack_id", f"Product {i}")
        p_desc = p.get("description", "").strip()
        # Clean individual member descriptions using upload_pack
        p_desc_clean = upload_pack._plain(p_desc)
        if p_desc_clean:
            # First line or concise summary
            first_summary = p_desc_clean.splitlines()[0]
            desc_lines.append(f"{i}. {p_title}: {first_summary}")
        else:
            desc_lines.append(f"{i}. {p_title}")
        wyg = p.get("what_you_get", [])
        if wyg:
            for item in wyg:
                all_what_you_get.append(f"- [{p_title}] {item}")
        else:
            all_what_you_get.append(f"- Complete files and documentation for {p_title}")

    desc_lines.append("")
    desc_lines.append("Each pack includes its full deliverables, documentation, and user guides.")
    desc_lines.append("Everything is organized into dedicated product folders inside your instant download.")

    raw_description = "\n".join(desc_lines)
    cleaned_desc = upload_pack._clean_description(raw_description)

    # Format listing text matching upload_pack format
    listing_body = (
        f"Title: {bundle_title}\n\n"
        f"Description:\n{cleaned_desc}\n\n"
        f"Tags: {', '.join(merged_tags)}\n\n"
        f"What you get:\n" + "\n".join(all_what_you_get) + "\n"
    )

    upload_pack._check_money(listing_body, "bundle listing")
    return listing_body


def _merge_tags(member_packs: List[Dict[str, Any]], bundle_title: str) -> List[str]:
    """Merge tags from all member packs, clean up to 13 deduplicated tags."""
    all_raw_tags: List[str] = []
    for p in member_packs:
        all_raw_tags.extend(p.get("tags", []))

    # Add bundle-specific tag candidates
    all_raw_tags.extend(["bundle kit", "complete collection", "digital bundle", "value pack"])

    return upload_pack._clean_tags(all_raw_tags, bundle_title)


def build_bundle(
    group: Union[Dict[str, Any], List[Any]],
    out_dir: Optional[Union[str, Path]] = None
) -> Path:
    """Build a bundle upload pack folder.

    Parameters:
      group: either a bundle suggestion dict (with 'bundle_id' and 'packs')
             or a list of pack objects/paths.
      out_dir: destination directory (defaults to 'exported_content/products/bundles').

    Produces:
      <out_dir>/<bundle_id>/
        ├── bundle.zip (member product.zip files cleanly named inside)
        ├── cover.png (1280x720 fan layout Pillow only)
        ├── listing.txt (title, description, 13 tags, what you get, personal use)
        └── LICENSE.txt (copied from a member pack)

    Also runs product_qa_deep.check_folder on the resulting bundle folder if available,
    writing the QA report file OUTSIDE the bundle folder.

    Rules:
      - Atomic write via temp directory
      - Deterministic bundle ID
      - No price or money amounts anywhere
      - No __MACOSX / .DS_Store / absolute paths in zip
      - Total uncompressed size < 250 MB
    """
    if isinstance(group, dict):
        member_inputs = group.get("packs") or group.get("member_packs") or []
        bundle_id = group.get("bundle_id")
    else:
        member_inputs = list(group)
        bundle_id = None

    if len(member_inputs) < 2 or len(member_inputs) > 4:
        raise ValueError(f"Bundles must contain between 2 and 4 packs; received {len(member_inputs)}.")

    member_packs = [_extract_pack_info(m) for m in member_inputs]

    # Verify all members have product_zip or files
    for p in member_packs:
        if not p.get("product_zip") or not os.path.isfile(p["product_zip"]):
            raise ValueError(f"Pack '{p['pack_id']}' is missing product.zip deliverable.")

    # Generate bundle ID if not supplied
    if not bundle_id:
        bundle_id = _make_bundle_id([p["pack_id"] for p in member_packs])

    # Target directories
    if out_dir is None:
        base_bundles_dir = Path("exported_content") / "products" / "bundles"
    else:
        base_bundles_dir = Path(out_dir)

    destination = (base_bundles_dir / bundle_id).resolve()
    base_bundles_dir.mkdir(parents=True, exist_ok=True)

    # Title generation
    aud = member_packs[0]["audience"]
    if aud == "adhd":
        aud_label = "ADHD"
    elif aud == "planner_2027":
        aud_label = "2027 Planner"
    else:
        aud_label = aud.replace("_", " ").title() if aud != "general" else "Productivity"
    bundle_title = f"{aud_label} Complete Digital Bundle ({len(member_packs)} Pack Set)"

    # Tags
    merged_tags = _merge_tags(member_packs, bundle_title)

    # Listing
    listing_text = _build_bundle_listing(bundle_title, member_packs, merged_tags)

    # Perform atomic creation in temp directory
    with tempfile.TemporaryDirectory(prefix=".bundle-build-", dir=str(base_bundles_dir)) as temp_dir:
        stage = Path(temp_dir) / bundle_id
        stage.mkdir()

        # 1. Create bundle.zip containing cleanly named member product.zip files
        bundle_zip_path = stage / "bundle.zip"
        import zipfile
        total_uncompressed = 0

        with zipfile.ZipFile(bundle_zip_path, "w", zipfile.ZIP_DEFLATED) as b_zip:
            for idx, p in enumerate(member_packs, 1):
                clean_name = f"{idx}-{_slugify(p['pack_id'])}.zip"
                src_zip = p["product_zip"]

                # Validate source zip has no dirty entries
                with zipfile.ZipFile(src_zip, "r") as z_check:
                    for info in z_check.infolist():
                        if "__MACOSX" in info.filename or os.path.basename(info.filename) == ".DS_Store":
                            raise ValueError(f"Member zip '{clean_name}' contains illegal metadata entry: {info.filename}")
                        if info.filename.startswith("/") or info.filename.startswith("\\"):
                            raise ValueError(f"Member zip '{clean_name}' contains absolute path: {info.filename}")
                        total_uncompressed += info.file_size

                b_zip.write(src_zip, arcname=clean_name)
                total_uncompressed += os.path.getsize(src_zip)

        if total_uncompressed >= MAX_BUNDLE_UNCOMPRESSED_BYTES:
            raise ValueError(f"Bundle uncompressed size {total_uncompressed} exceeds 250 MB limit.")

        # 2. Cover image (1280x720 Pillow fan)
        cover_path = stage / "cover.png"
        member_covers = [p.get("cover_path", "") for p in member_packs]
        _create_fan_cover(member_covers, str(cover_path))

        # 3. Listing text
        (stage / "listing.txt").write_text(listing_text, encoding="utf-8")

        # 4. LICENSE.txt (copied from a member pack or default standard license)
        lic_copied = False
        for p in member_packs:
            if p.get("license_path") and os.path.isfile(p["license_path"]):
                shutil.copyfile(p["license_path"], stage / "LICENSE.txt")
                lic_copied = True
                break
        if not lic_copied:
            (stage / "LICENSE.txt").write_text(
                "Personal use only. Do not resell, share, or redistribute these files.\n",
                encoding="utf-8"
            )

        # Atomic move to destination
        if destination.exists():
            shutil.rmtree(destination)
        os.rename(stage, destination)

    # 5. Run product_qa_deep if available, writing report file OUTSIDE bundle folder
    try:
        import product_qa_deep
        report_path = str(base_bundles_dir / f"{bundle_id}-qa_report.json")
        product_qa_deep.check_folder(str(destination), report_path=report_path)
    except Exception:
        pass

    return destination


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    # suggest
    parser_s = subparsers.add_parser("suggest", help="Suggest bundles from packs.")
    parser_s.add_argument("packs_dir", help="Directory containing upload packs.")

    # build
    parser_b = subparsers.add_parser("build", help="Build a bundle from specified packs.")
    parser_b.add_argument("pack_dirs", nargs="+", help="Pack directories to include in bundle.")
    parser_b.add_argument("--out-dir", default="exported_content/products/bundles", help="Output directory.")

    args = parser.parse_args(argv)

    if args.command == "suggest":
        p_dir = Path(args.packs_dir)
        packs = [p for p in p_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
        suggestions = suggest_bundles(packs)
        print(f"Found {len(suggestions)} bundle suggestions:")
        for s in suggestions:
            print(f"- [{s['bundle_id']}] {s['reason']}")
            print(f"  Packs: {', '.join(s['pack_ids'])}")
        return 0

    elif args.command == "build":
        dest = build_bundle(args.pack_dirs, out_dir=args.out_dir)
        print(f"Built bundle: {dest}")
        return 0

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
