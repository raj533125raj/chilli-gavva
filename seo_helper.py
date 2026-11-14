"""SEO and keyword helper built from a pack's real files (OC2-2).

Reads listing.txt / README / support notes / product.zip text members from the
pack folder, then produces keywords, A/B titles for three store styles, 13 shop
tags, a meta description, Pinterest ideas and alt texts.

No network, no publishing, no money amounts and no invented claims.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PRODUCTS_DIR = BASE_DIR / "exported_content" / "products"
MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 1_000_000
MAX_ZIP_MEMBERS = 30

BRAND_BLOCKLIST: Set[str] = {
    "etsy", "gumroad", "payhip", "notion", "canva", "pinterest", "instagram",
    "facebook", "twitter", "tiktok", "youtube", "linkedin", "shopify", "amazon",
    "apple", "google", "microsoft", "adobe", "photoshop", "excel", "powerpoint",
    "word", "kindle", "ipad", "goodnotes", "procreate", "redbubble", "teepublic",
    "society6", "creativemarket", "teacherspayteachers", "walmart", "target",
}

FAKE_CLAIMS_BLOCKLIST: Set[str] = {
    "best seller", "bestseller", "top seller", "#1", "number one", "no. 1",
    "guaranteed income", "guaranteed results", "guaranteed", "money back",
    "viral", "proven to sell", "hot item", "trending now", "instant success",
}

_MONEY_PATTERN = re.compile(
    r"(?:[$€£₹]\s*\d+(?:[.,]\d+)?)"
    r"|(?:\d+(?:[.,]\d+)?\s*(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?|paise?|cents?))"
    r"|(?:(?:USD|INR|EUR|GBP|Rs\.?)\s*\d+(?:[.,]\d+)?)",
    re.I,
)

_STOP_WORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "what", "which",
    "this", "that", "these", "those", "then", "just", "so", "than", "such", "both",
    "through", "about", "for", "is", "of", "while", "during", "to", "from", "in",
    "out", "on", "off", "again", "further", "once", "here", "there", "when", "where",
    "why", "how", "all", "any", "each", "few", "more", "most", "other", "some", "no",
    "nor", "not", "only", "own", "same", "too", "very", "can", "will", "with", "by",
    "at", "be", "been", "being", "have", "has", "had", "do", "does", "did", "our",
    "your", "you", "it", "its", "are", "was", "were", "pack", "file", "files",
    "page", "pages", "use", "using", "includes", "include", "print", "printed",
    "title", "sections", "section", "readme", "description",
}

_FALLBACK_TERMS: Tuple[str, ...] = (
    "printable planner", "digital planner", "planner template", "daily planner",
    "weekly planner", "monthly planner", "habit tracker", "goal tracker",
    "task checklist", "to do list", "organizer", "worksheet", "journal",
    "productivity", "time management", "home office", "study planner",
    "meal planner", "budget planner", "self care", "routine", "schedule",
    "printable download", "instant download", "digital download", "a4 pdf",
    "us letter pdf", "minimalist layout", "simple layout", "clean layout",
    "printable pages", "planner pages", "tracker pages", "checklist pages",
    "goal setting", "habit building", "weekly schedule", "daily routine",
    "monthly overview", "project tracker", "wellness journal", "note pages",
    "pdf printable", "planner bundle", "worksheet pack", "organizer kit",
)

_STORE_MAX_CHARS: Dict[str, int] = {"etsy": 140, "gumroad": 100, "payhip": 120}
_STORE_LABELS: Dict[str, str] = {"etsy": "Etsy", "gumroad": "Gumroad", "payhip": "Payhip"}

_TITLE_PREFIXES: Tuple[str, ...] = (
    "Printable", "Digital", "Minimalist", "Simple", "Modern", "Complete",
    "Weekly", "Daily", "Monthly", "Easy", "Clean", "Practical",
)
_TITLE_SUFFIXES: Tuple[str, ...] = (
    "Printable Pack", "Digital Download", "PDF Printable", "Template Set",
    "Organizer Kit", "Checklist Pages", "Journal Pages", "Goal Tracker",
    "Productivity Pack", "Home Office Kit", "Planner Pages", "Worksheet Pack",
)

_PIN_FALLBACK: Tuple[str, ...] = (
    "printable planner", "daily organizer", "goal tracker", "habit planner",
    "weekly schedule", "home office", "productivity tips", "simple planning",
    "digital download", "printable pages",
)

_TAG_FALLBACK: Tuple[str, ...] = (
    "printable", "planner", "template", "digital", "download", "organizer",
    "journal", "tracker", "checklist", "goal", "daily", "weekly", "monthly",
    "habit", "productivity", "stationery", "homeoffice", "desk", "minimalist",
    "modern", "simple", "routine", "schedule", "todo", "notes", "study",
    "selfcare", "worksheet", "printablepdf", "instantdownload",
)


class PackNotFoundError(FileNotFoundError):
    """The requested pack folder does not exist below the products folder."""


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------
def _remove_brands(text: str) -> str:
    out = text or ""
    for brand in sorted(BRAND_BLOCKLIST, key=len, reverse=True):
        out = re.sub(rf"(?<!\w){re.escape(brand)}(?!\w)", " ", out, flags=re.I)
    return out


def _remove_claims(text: str) -> str:
    out = text or ""
    for claim in sorted(FAKE_CLAIMS_BLOCKLIST, key=len, reverse=True):
        out = re.sub(rf"(?<!\w){re.escape(claim)}(?!\w)", " ", out, flags=re.I)
    return out


def _remove_money(text: str) -> str:
    return _MONEY_PATTERN.sub(" ", text or "")


def _clean_text(text: str) -> str:
    out = _remove_brands(_remove_claims(_remove_money(text or "")))
    out = re.sub(r"[^\w\s-]", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def _clean_text_keep_punct(text: str) -> str:
    out = _remove_brands(_remove_claims(_remove_money(text or "")))
    return re.sub(r"[ \t]+", " ", out).strip()


def _clean_slug(text: str, max_len: int = 60) -> str:
    words = [w for w in _clean_text(text).lower().split() if w and w not in _STOP_WORDS]
    slug = re.sub(r"-+", "-", "-".join(words)).strip("-")
    if len(slug) > max_len:
        head = slug[:max_len]
        dash = head.rfind("-")
        slug = head[:dash] if dash > 10 else head.rstrip("-")
    return slug.strip("-")


def _contains_banned(text: str) -> List[str]:
    found: List[str] = []
    for brand in BRAND_BLOCKLIST:
        if re.search(rf"(?<!\w){re.escape(brand)}(?!\w)", text or "", flags=re.I):
            found.append(brand)
    for claim in FAKE_CLAIMS_BLOCKLIST:
        if re.search(rf"(?<!\w){re.escape(claim)}(?!\w)", text or "", flags=re.I):
            found.append(claim)
    if _MONEY_PATTERN.search(text or ""):
        found.append("money")
    return sorted(set(found))


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", _clean_text(text).lower())


def _content_words(text: str) -> List[str]:
    return [w for w in _words(text) if len(w) >= 3 and w not in _STOP_WORDS]


# ---------------------------------------------------------------------------
# Pack discovery / reading
# ---------------------------------------------------------------------------
def resolve_products_dir(products_dir: Optional[Any] = None) -> Path:
    if products_dir:
        return Path(products_dir)
    env = os.environ.get("CHILLI_PRODUCTS_DIR")
    return Path(env) if env else DEFAULT_PRODUCTS_DIR


def find_pack_dir(pack_number: int, products_dir: Optional[Any] = None) -> Optional[Path]:
    base = resolve_products_dir(products_dir)
    if not base.is_dir():
        return None
    pattern = re.compile(rf"^{int(pack_number)}(?:-|$)")
    for root in (base / "upload", base):
        if not root.is_dir():
            continue
        try:
            matches = sorted(p for p in root.iterdir() if p.is_dir() and pattern.match(p.name))
        except OSError:
            continue
        if matches:
            return matches[0]
    return None


def _read_text(path: Path) -> str:
    try:
        data = path.read_bytes()[:MAX_FILE_BYTES]
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _zip_texts(zip_path: Path) -> Dict[str, str]:
    texts: Dict[str, str] = {}
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            members.sort(key=lambda m: m.filename)
            for member in members[:MAX_ZIP_MEMBERS]:
                name = member.filename
                if ".." in name or name.startswith("/"):
                    continue
                if not name.lower().endswith((".txt", ".md", ".csv", ".json")):
                    continue
                if member.file_size > MAX_FILE_BYTES:
                    continue
                try:
                    texts[name] = archive.read(member)[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
                except (OSError, RuntimeError):
                    continue
    except (OSError, zipfile.BadZipFile):
        return {}
    return texts


def _candidate_files(folder: Path) -> List[Path]:
    names = ("listing.txt", "listing.md", "README.md", "README.txt", "readme.md",
             "LAUNCH.md", "description.txt", "product.md")
    files = [folder / n for n in names if (folder / n).is_file()]
    for sub in ("support", "content"):
        child = folder / sub
        if child.is_dir():
            try:
                files.extend(sorted(p for p in child.iterdir()
                                    if p.is_file() and p.suffix.lower() in {".md", ".txt"}))
            except OSError:
                pass
    return files


def read_pack_content(pack_number: int, products_dir: Optional[Any] = None) -> Dict[str, Any]:
    """Read the real text files (and zip text members) of one pack."""
    folder = find_pack_dir(pack_number, products_dir)
    if folder is None:
        raise PackNotFoundError(f"pack {pack_number} not found")
    texts: Dict[str, str] = {}
    total = 0
    for path in _candidate_files(folder):
        text = _read_text(path)
        if not text:
            continue
        texts[path.relative_to(folder).as_posix()] = text
        total += len(text)
        if total >= MAX_TOTAL_BYTES:
            break
    if total < MAX_TOTAL_BYTES and (folder / "product.zip").is_file():
        for name, text in _zip_texts(folder / "product.zip").items():
            if total >= MAX_TOTAL_BYTES:
                break
            key = f"product.zip/{name}"
            if key not in texts:
                texts[key] = text
                total += len(text)

    combined = "\n\n".join(texts.values())
    return {
        "pack_number": int(pack_number),
        "folder": folder.name,
        "title": _extract_title(combined, folder.name),
        "sections": _extract_sections(combined),
        "texts": texts,
        "sources": sorted(texts),
        "combined": combined[:MAX_TOTAL_BYTES],
    }


def _extract_title(text: str, folder_name: str) -> str:
    match = re.search(r"(?im)^\s*(?:product\s+)?title\s*:\s*(.+?)\s*$", text or "")
    if not match:
        match = re.search(r"(?m)^\s*#\s+(.+?)\s*$", text or "")
    if match:
        title = _clean_text(match.group(1))
    else:
        title = ""
    if not title:
        title = _clean_text(re.sub(r"^\d+[-_ ]*", "", folder_name).replace("_", " ").replace("-", " "))
    return title.title() if title else "Printable Pack"


def _extract_sections(text: str) -> List[str]:
    sections: List[str] = []
    match = re.search(r"(?im)^\s*sections?\s*:\s*(.+?)\s*$", text or "")
    if match:
        sections.extend(part.strip() for part in re.split(r"[,;|]", match.group(1)) if part.strip())
    for line in (text or "").splitlines():
        heading = re.match(r"^\s*#{2,4}\s+(.+?)\s*$", line)
        if heading:
            sections.append(heading.group(1).strip())
        bullet = re.match(r"^\s*[-*]\s+([A-Za-z][^:]{2,80})$", line)
        if bullet:
            sections.append(bullet.group(1).strip())
    clean: List[str] = []
    seen: Set[str] = set()
    for section in sections:
        value = _clean_text(section)
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            clean.append(value)
        if len(clean) >= 30:
            break
    return clean


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------
def _phrases(words: Sequence[str], sizes: Iterable[int], limit: int) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for size in sizes:
        for idx in range(0, max(0, len(words) - size + 1)):
            phrase = " ".join(words[idx:idx + size])
            if phrase not in seen:
                seen.add(phrase)
                out.append(phrase)
                if len(out) >= limit:
                    return out
    return out


def _keyword_candidates(content: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    title_words = _content_words(content.get("title", ""))
    body_words = _content_words(
        " ".join([content.get("title", "")] + list(content.get("sections", [])) + [content.get("combined", "")])
    )
    words = title_words + [w for w in body_words if w not in title_words]
    head = _phrases(words, (2, 3, 4), 24)
    long_tail = _phrases(words, (4, 5, 3), 30)
    return head, long_tail


def build_keyword_set(content: Any) -> Dict[str, Any]:
    """Head phrases plus long-tail phrases, at least 40 unique keywords."""
    data = content if isinstance(content, dict) else {"combined": str(content or ""), "title": "", "sections": []}
    head, long_tail = _keyword_candidates(data)
    main_terms = _content_words(" ".join([data.get("title", "")] + list(data.get("sections", []))))

    used: Set[str] = set()
    clean_head: List[str] = []
    for phrase in head:
        value = _clean_text(phrase).lower()
        if value and value not in used and len(value.split()) >= 2 and not _contains_banned(value):
            used.add(value)
            clean_head.append(value)
        if len(clean_head) >= 20:
            break

    clean_long: List[str] = []
    for phrase in long_tail:
        value = _clean_text(phrase).lower()
        if value and value not in used and len(value.split()) >= 2 and not _contains_banned(value):
            used.add(value)
            clean_long.append(value)
        if len(clean_long) >= 25:
            break

    base = clean_head[0] if clean_head else (main_terms[0] if main_terms else "printable planner")
    patterns = (
        "{} printable", "{} pdf", "{} download", "digital {}", "{} template",
        "{} planner", "{} checklist", "{} organizer", "printable {}", "{} pages",
        "{} set", "{} kit", "{} bundle", "{} worksheet", "{} journal",
    )
    for pattern in patterns:
        candidate = _clean_text(pattern.format(base)).lower()
        if candidate and candidate not in used and not _contains_banned(candidate):
            used.add(candidate)
            clean_long.append(candidate)
        if len(clean_head) + len(clean_long) >= 42:
            break

    for term in _FALLBACK_TERMS:
        if len(clean_head) + len(clean_long) >= 40:
            break
        value = _clean_text(term).lower()
        if value and value not in used and not _contains_banned(value):
            used.add(value)
            if len(clean_long) < 25:
                clean_long.append(value)
            elif len(clean_head) < 20:
                clean_head.append(value)
    return {
        "head": clean_head[:20],
        "long_tail": clean_long[:25],
        "total": len(clean_head[:20]) + len(clean_long[:25]),
        "all": (clean_head[:20] + clean_long[:25]),
    }


# ---------------------------------------------------------------------------
# Title variants
# ---------------------------------------------------------------------------
def _fit(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", _clean_text(text)).strip(" -:")
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    space = cut.rfind(" ")
    return (cut[:space] if space > 10 else cut).strip(" -:")


def _title_variants_for(base: str, style: str, max_chars: int) -> List[str]:
    base = _fit(base or "Printable Planner", max_chars)
    variants: List[str] = []
    seen: Set[str] = set()

    def add(candidate: str) -> None:
        value = _fit(candidate, max_chars)
        key = value.lower()
        if value and key not in seen and not _contains_banned(value):
            seen.add(key)
            variants.append(value)

    add(base)
    for suffix in _TITLE_SUFFIXES[:5]:
        add(f"{base} {suffix}")
    for prefix in _TITLE_PREFIXES[:5]:
        add(f"{prefix} {base}")
        if len(variants) >= 10:
            return variants[:10]
    for suffix in _TITLE_SUFFIXES:
        for prefix in _TITLE_PREFIXES:
            add(f"{prefix} {base} {suffix}")
            if len(variants) >= 10:
                return variants[:10]
    index = 2
    while len(variants) < 10:
        add(f"{base} Edition {index}")
        index += 1
        if index > 50:
            break
    return variants[:10]


def build_title_variants(main_title: str, store_style: str = "etsy") -> Dict[str, Any]:
    """Ten A/B titles for one store style, within that store's character limit."""
    style = store_style if store_style in _STORE_MAX_CHARS else "etsy"
    max_chars = _STORE_MAX_CHARS[style]
    variants = _title_variants_for(main_title or "Printable Planner", style, max_chars)
    return {"store_style": style, "max_chars": max_chars, "variants": variants}


def build_all_title_variants(main_title: str) -> Dict[str, Any]:
    return {style: build_title_variants(main_title, style) for style in ("etsy", "gumroad", "payhip")}


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------
def _tag_candidates(content: Dict[str, Any]) -> List[str]:
    keyword = build_keyword_set(content)
    words: List[str] = []
    for phrase in keyword["all"]:
        words.extend(phrase.split())
    words.extend(_content_words(" ".join(content.get("sections", []))))
    candidates: List[str] = []
    for word in words:
        value = re.sub(r"[^a-z0-9]", "", word.lower())
        if 3 <= len(value) <= 20:
            candidates.append(value)
    for phrase in keyword["head"]:
        value = re.sub(r"[^a-z0-9]", "", phrase.lower())
        if 3 <= len(value) <= 20:
            candidates.append(value)
    candidates.extend(_TAG_FALLBACK)
    return candidates


def build_etsy_tag_set(content: Any, count: int = 13) -> Dict[str, Any]:
    """Exactly ``count`` tags (default 13), each <=20 chars, unique, no brands."""
    data = content if isinstance(content, dict) else {"combined": str(content or ""), "title": "", "sections": []}
    tags: List[str] = []
    seen: Set[str] = set()
    for candidate in _tag_candidates(data):
        tag = re.sub(r"\s+", "", candidate.strip().lower())
        if not tag or len(tag) > 20 or tag in seen:
            continue
        if _contains_banned(tag):
            continue
        seen.add(tag)
        tags.append(tag)
        if len(tags) >= count:
            break
    return {"count": len(tags), "max_chars": 20, "tags": tags[:count]}


# ---------------------------------------------------------------------------
# Meta description
# ---------------------------------------------------------------------------
_META_FILLERS: Tuple[str, ...] = (
    "Instant download.",
    "Print at home.",
    "Simple pages for busy weeks.",
    "Organize your day in minutes.",
    "Clean layouts that are easy to use.",
    "Made for home, study and office.",
    "Start your routine today.",
    "A practical printable pack.",
)


def _meta_description(title: str, main_keyword: str) -> str:
    title_clean = _clean_text(title or "Printable Pack")
    keyword = _clean_text(main_keyword or "printable planner")
    if len(title_clean) > 90:
        title_clean = _fit(title_clean, 90)
    desc = f"{title_clean} - {keyword} printable you can use right away."
    for filler in _META_FILLERS:
        if len(desc) >= 150:
            break
        desc = f"{desc} {filler}"
    if len(desc) > 160:
        desc = desc[:160]
        space = desc.rfind(" ")
        desc = desc[:space] if space >= 130 else desc
    desc = desc.strip()
    if len(desc) < 150:
        for filler in _META_FILLERS:
            if len(desc) >= 160:
                break
            candidate = f"{desc} {filler}".strip()
            if len(candidate) <= 160:
                desc = candidate
            else:
                room = 159 - len(desc)
                add = filler[:max(0, room)].rsplit(" ", 1)[0]
                if add:
                    desc = f"{desc} {add}".strip()
    return desc[:160]


def build_meta_description(title: str, main_keyword: str) -> Dict[str, Any]:
    desc = _meta_description(title, main_keyword)
    return {"description": desc, "char_length": len(desc)}


# ---------------------------------------------------------------------------
# Pinterest and alt texts
# ---------------------------------------------------------------------------
def build_pinterest_info(main_keyword: str, title: str, sections: Optional[Sequence[str]] = None) -> Dict[str, List[str]]:
    keyword = _clean_text(main_keyword or "printable planner")
    title_head = " ".join(_content_words(title)[:3]) or keyword
    section_terms = [_clean_text(s) for s in (sections or []) if _clean_text(s)]
    boards: List[str] = [
        f"{keyword.title()} Ideas",
        f"{title_head.title()} Printables",
        f"{keyword.title()} Planner Pages",
        f"{title_head.title()} Tips",
        "Home Office Printables",
    ]
    pins: List[str] = [keyword, title_head]
    pins.extend(section_terms[:4])
    pins.extend(_PIN_FALLBACK)
    out_boards: List[str] = []
    out_pins: List[str] = []
    seen_b: Set[str] = set()
    seen_p: Set[str] = set()
    for board in boards:
        value = _clean_text(board)
        if value and value.lower() not in seen_b and not _contains_banned(value):
            seen_b.add(value.lower())
            out_boards.append(value)
    for pin in pins:
        value = _clean_text(pin)
        if value and value.lower() not in seen_p and not _contains_banned(value):
            seen_p.add(value.lower())
            out_pins.append(value)
    return {"boards": out_boards[:5], "pin_keywords": out_pins[:10]}


def build_alt_texts(title: str, main_keyword: str, image_roles: Optional[Sequence[str]] = None,
                    sections: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    roles = list(image_roles) if image_roles else ["cover", "preview-1", "preview-2", "mockup", "icon"]
    title_clean = _clean_text(title or "Printable Pack")
    keyword = _clean_text(main_keyword or "printable planner")
    benefits = [_clean_text(s) for s in (sections or []) if _clean_text(s)] or [
        "goal tracker page", "daily planner page", "monthly overview sheet"
    ]
    alts: List[str] = []
    for role in roles:
        if role == "cover":
            alt = f"{title_clean} printable cover layout"
        elif role.startswith("preview"):
            index = int(role.split("-")[1]) - 1 if role.split("-")[-1].isdigit() else 0
            benefit = benefits[index % len(benefits)]
            alt = f"{keyword} preview: {benefit}"
        elif role == "mockup":
            alt = f"printed {keyword} pages on a desk workspace"
        elif role == "icon":
            alt = f"{title_clean} small icon"
        else:
            alt = f"{title_clean} {role} image"
        alts.append(_clean_text(alt) or "printable image")
    return {"roles": roles, "alts": alts}


# ---------------------------------------------------------------------------
# Readability / stuffing
# ---------------------------------------------------------------------------
def check_readability(text: str) -> Dict[str, Any]:
    kept = _clean_text_keep_punct(text or "")
    sentences = [s for s in re.split(r"[.!?]+", kept) if s.strip()]
    words = re.sub(r"[^\w\s]", " ", kept).split()
    if not sentences or not words:
        return {"avg_sentence_len": 0.0, "long_word_ratio": 0.0,
                "warnings": ["Text empty: readability check skipped."]}
    avg = len(words) / len(sentences)
    long_ratio = sum(1 for w in words if len(w) > 12) / len(words)
    warnings: List[str] = []
    if avg > 25:
        warnings.append(f"Sentences too long: average {avg:.1f} words. Split them.")
    if long_ratio > 0.2:
        warnings.append(f"Many long words: {long_ratio:.0%}. Use simpler words.")
    return {"avg_sentence_len": round(avg, 2), "long_word_ratio": round(long_ratio, 3),
            "warnings": warnings}


def check_keyword_stuffing(text: str, keyword: str) -> Dict[str, Any]:
    clean = _clean_text(text or "").lower()
    kw = _clean_text(keyword or "").lower()
    words = clean.split()
    if not words or not kw:
        return {"keyword": kw, "count": 0, "density": 0.0,
                "warnings": ["No keyword or no text to check."]}
    kw_words = kw.split()
    count = sum(1 for i in range(len(words) - len(kw_words) + 1)
                if words[i:i + len(kw_words)] == kw_words)
    density = count * len(kw_words) / len(words)
    warnings: List[str] = []
    if density > 0.03:
        warnings.append(f"Keyword stuffing risk: '{kw}' {density:.1%} (>3%). Use natural wording.")
    elif density < 0.005:
        warnings.append(f"Keyword too rare: '{kw}' {density:.2%}. Mention it naturally once or twice.")
    return {"keyword": kw, "count": count, "density": round(density, 4), "warnings": warnings}


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------
def _main_keyword(title: str, keyword_set: Dict[str, Any]) -> str:
    if keyword_set.get("head"):
        return keyword_set["head"][0]
    words = _content_words(title)
    return words[0] if words else "printable planner"


def build_seo(content: Any, pack_number: int = 1) -> Dict[str, Any]:
    """Build the complete SEO output from a pack content dict or plain text."""
    if isinstance(content, dict):
        data = dict(content)
    else:
        text = str(content or "")
        data = {"pack_number": pack_number, "title": _extract_title(text, f"{pack_number}-pack"),
                "sections": _extract_sections(text), "combined": text, "sources": [], "folder": ""}
    title = data.get("title") or _extract_title(data.get("combined", ""), f"{pack_number}-pack")
    keyword_set = build_keyword_set({**data, "title": title})
    main_kw = _main_keyword(title, keyword_set)
    titles = build_all_title_variants(title)
    tags = build_etsy_tag_set({**data, "title": title})
    meta = build_meta_description(title, main_kw)
    pinterest = build_pinterest_info(main_kw, title, data.get("sections"))
    alts = build_alt_texts(title, main_kw, sections=data.get("sections"))
    readability = check_readability(data.get("combined", ""))
    stuffing = check_keyword_stuffing(data.get("combined", ""), main_kw)

    warnings: List[str] = []
    warnings.extend(readability["warnings"])
    warnings.extend(stuffing["warnings"])
    source_banned = _contains_banned(data.get("combined", ""))
    if source_banned:
        warnings.append("Source text had blocked brand/claim/money wording; it was removed from the SEO output.")
    if meta["char_length"] < 150 or meta["char_length"] > 160:
        warnings.append(f"Meta length is {meta['char_length']} chars; target is 150-160.")

    summary = (
        f"Pack {pack_number}: {keyword_set['total']} keywords, "
        f"30 title variants (10 each for 3 stores), {tags['count']} tags, "
        f"meta {meta['char_length']} chars, {len(pinterest['boards'])} boards, "
        f"{len(alts['alts'])} alt texts"
    )
    if warnings:
        summary += f", {len(warnings)} warning(s)"
    return {
        "pack_number": int(pack_number),
        "pack_folder": data.get("folder", ""),
        "sources": data.get("sources", []),
        "title": title,
        "main_keyword": main_kw,
        "keyword_set": keyword_set,
        "title_variants": titles,
        "all_title_variants": titles,
        "etsy_tags": tags,
        "meta_description": meta,
        "pinterest": pinterest,
        "alt_texts": alts,
        "readability": readability,
        "stuffing": stuffing,
        "warnings": warnings,
        "summary": summary,
    }


def build_for_pack(pack_number: int, products_dir: Optional[Any] = None) -> Dict[str, Any]:
    """Read a real pack folder and return its full SEO report."""
    content = read_pack_content(pack_number, products_dir)
    return build_seo(content, pack_number=pack_number)


def seo_n_command(listing_text: str, pack_number: int = 1) -> Dict[str, Any]:
    return build_seo({"combined": listing_text or "", "title": _extract_title(listing_text or "", f"{pack_number}-pack"),
                      "sections": _extract_sections(listing_text or ""), "sources": []}, pack_number=pack_number)


def build_seo_document(output: Dict[str, Any]) -> str:
    lines: List[str] = [
        f"=== SEO KIT PACK {output['pack_number']} ===",
        f"Title: {output['title']}",
        f"Main keyword: {output['main_keyword']}",
    ]
    if output.get("pack_folder"):
        lines.append(f"Folder: {output['pack_folder']}")
    if output.get("sources"):
        lines.append("Sources: " + ", ".join(output["sources"][:10]))
    lines.extend(["", f"--- Keywords ({output['keyword_set']['total']}, 40+ target) ---"])
    for item in output["keyword_set"]["head"]:
        lines.append(f"  HEAD: {item}")
    for item in output["keyword_set"]["long_tail"]:
        lines.append(f"  LONG-TAIL: {item}")
    lines.extend(["", "--- A/B Title Variants (owner chooses) ---"])
    for style in ("etsy", "gumroad", "payhip"):
        data = output["title_variants"][style]
        lines.append(f"  {_STORE_LABELS[style]} (max {data['max_chars']} chars):")
        for index, variant in enumerate(data["variants"], 1):
            lines.append(f"    {style.upper()}{index}: {variant} ({len(variant)} chars)")
    lines.extend(["", "--- 13 Shop Tags (max 20 chars, no brands) ---"])
    for index, tag in enumerate(output["etsy_tags"]["tags"], 1):
        lines.append(f"  TAG{index}: {tag} ({len(tag)} chars)")
    lines.extend(["", "--- Meta description (150-160 chars) ---",
                  f"  {output['meta_description']['description']}",
                  f"  ({output['meta_description']['char_length']} chars)"])
    lines.extend(["", "--- Pinterest boards ---"])
    for board in output["pinterest"]["boards"]:
        lines.append(f"  BOARD: {board}")
    lines.append("--- Pin keywords ---")
    for pin in output["pinterest"]["pin_keywords"]:
        lines.append(f"  PIN: {pin}")
    lines.extend(["", "--- Alt texts ---"])
    for index, alt in enumerate(output["alt_texts"]["alts"], 1):
        lines.append(f"  ALT{index}: {alt}")
    lines.extend(["", "--- Readability and stuffing checks ---"])
    if output["warnings"]:
        lines.extend(f"  WARN: {warning}" for warning in output["warnings"])
    else:
        lines.append("  All checks clear.")
    lines.extend(["", "--- Summary ---", output["summary"]])
    return "\n".join(lines)


def validate_output(output: Dict[str, Any]) -> List[str]:
    """Return a list of spec violations; empty means everything is clean."""
    problems: List[str] = []
    keywords = output["keyword_set"]
    if keywords["total"] < 40:
        problems.append("keyword total below 40")
    all_keywords = keywords["head"] + keywords["long_tail"]
    if len(all_keywords) != len({k.lower() for k in all_keywords}):
        problems.append("duplicate keywords")
    for style, data in output["title_variants"].items():
        if len(data["variants"]) != 10:
            problems.append(f"{style} title count is not 10")
        if len({v.lower() for v in data["variants"]}) != len(data["variants"]):
            problems.append(f"{style} titles repeat")
        for variant in data["variants"]:
            if len(variant) > data["max_chars"]:
                problems.append(f"{style} title over {data['max_chars']} chars")
            if _contains_banned(variant):
                problems.append(f"{style} title has blocked text")
    tags = output["etsy_tags"]["tags"]
    if len(tags) != 13:
        problems.append("tag count is not 13")
    if len({t.lower() for t in tags}) != len(tags):
        problems.append("tags repeat")
    for tag in tags:
        if len(tag) > 20 or _contains_banned(tag):
            problems.append("tag over 20 chars or blocked")
    meta_len = output["meta_description"]["char_length"]
    if not 150 <= meta_len <= 160:
        problems.append("meta length outside 150-160")
    if _contains_banned(output["meta_description"]["description"]):
        problems.append("meta has blocked text")
    for text in (output["meta_description"]["description"], *output["etsy_tags"]["tags"],
                 *(v for data in output["title_variants"].values() for v in data["variants"])):
        if _MONEY_PATTERN.search(text):
            problems.append("money text in output")
            break
    return problems
