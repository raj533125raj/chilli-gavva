"""format_registry.py — Discovers and registers product format builders.

Discovers format builder modules matching `builder_<format>.py` exposing:
- `FORMAT`: string identifier for the format (e.g. 'budget_sheet', 'checklist')
- `KEYWORDS`: list of lowercase keywords/phrases to match in idea text
- `build(spec: dict, out_dir: str) -> list[str]`: function creating product files
  in out_dir and returning list of created relative/base file names.

Also maintains built-in formats ('sheets', 'planner', 'notion', 'resume', 'dated_planner')
so existing products are always classified and built properly.

Provides:
- `discover_builders(search_dir=None) -> dict[str, dict]`: scans for builder_*.py modules.
- `classify(idea_text: str) -> str | None`: classifies idea text into a format id.
- `can_build(idea_text_or_spec) -> bool`: checks if an idea text or spec can be built.
- `get_builder(format_id: str) -> callable | None`: returns builder build function.
- `list_registered_formats() -> list[str]`: sorted list of registered format ids.
- `get_format_info(format_id: str) -> dict | None`: metadata for a registered format.
"""

import importlib
import importlib.util
import os
import re
import sys
from pathlib import Path

# Built-in format definitions mapping to existing builders in product_builder
BUILTIN_FORMATS = {
    "sheets": {
        "keywords": [
            "sheet", "sheets", "spreadsheet", "spreadsheets", "excel", "google sheets",
            "xlsx", "csv", "calculator", "tracker sheet", "budget sheet", "expense tracker",
            "finance tracker", "habit tracker spreadsheet", "financial model", "income tracker",
            "freelancer tracker", "freelancer trackers", "invoice tracker", "small business sheet",
            "profit calculator"
        ],
        "description": "Spreadsheet workbook (.xlsx / CSV) with formulas and tabs",
    },
    "planner": {
        "keywords": [
            "planner", "printable planner", "daily planner", "weekly planner",
            "printable", "printables", "pdf planner", "worksheet", "worksheets",
            "workbook", "journal", "tracker", "checklist", "organizer", "adhd planner",
            "routine planner", "focus planner", "meal planner", "adhd planners"
        ],
        "description": "Printable PDF planner/workbook in A4 and Letter sizes",
    },
    "dated_planner": {
        "keywords": [
            "dated planner", "calendar", "calendar planner", "2027 planner",
            "2026 planner", "monthly calendar", "yearly planner", "annual planner",
            "dated calendar", "dated daily planner", "2027 planners"
        ],
        "description": "Dated calendar planner PDF with monthly/weekly layouts",
    },
    "notion": {
        "keywords": [
            "notion", "notion template", "notion dashboard", "notion system",
            "notion workspace", "notion planner", "notion database", "notion tracker",
            "notion templates"
        ],
        "description": "Notion template pack with markdown pages and CSV databases",
    },
    "resume": {
        "keywords": [
            "resume", "cv", "resume bundle", "ats resume", "cover letter",
            "resume template", "curriculum vitae", "job application pack",
            "job search bundle", "ats-friendly resume"
        ],
        "description": "ATS-friendly Word (.docx) resume and cover letter templates",
    },
}

_REGISTRY = {}
_DISCOVERED = False


def _tokenize(text: str) -> set[str]:
    """Tokenize text into lowercase words."""
    if not text:
        return set()
    return set(re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", text.lower()))


def register_format(format_id: str, keywords: list[str], build_fn=None,
                    description: str = "", module_name: str = None) -> bool:
    """Register a format into the registry.

    Returns True if registered successfully, False otherwise.
    """
    if not format_id or not isinstance(format_id, str):
        return False
    fid = format_id.strip().lower()
    if not fid:
        return False

    clean_kws = []
    if keywords:
        for kw in keywords:
            if isinstance(kw, str) and kw.strip():
                clean_kws.append(kw.strip().lower())

    _REGISTRY[fid] = {
        "format": fid,
        "keywords": clean_kws,
        "build": build_fn,
        "description": description or f"Format {fid}",
        "module": module_name,
        "builtin": build_fn is None,
    }
    return True


def unregister_format(format_id: str) -> bool:
    """Remove a format from the registry."""
    if not format_id:
        return False
    fid = format_id.strip().lower()
    if fid in _REGISTRY:
        del _REGISTRY[fid]
        return True
    return False


def reset_registry():
    """Reset registry to built-ins only."""
    global _DISCOVERED
    _REGISTRY.clear()
    for fid, meta in BUILTIN_FORMATS.items():
        register_format(fid, meta["keywords"], build_fn=None, description=meta["description"])
    _DISCOVERED = True


# Initialise with built-ins
reset_registry()


def discover_builders(search_dir=None, force_reload=False) -> dict[str, dict]:
    """Discover all builder_<format>.py modules in search_dir (or repo root).

    A builder module must define:
      - FORMAT: str (unique format ID)
      - KEYWORDS: list[str] (search terms for classification)
      - build: callable(spec: dict, out_dir: str) -> list[str]
    """
    global _DISCOVERED
    if _DISCOVERED and not force_reload and search_dir is None:
        return dict(_REGISTRY)

    if force_reload:
        reset_registry()

    root_dir = search_dir or os.path.dirname(os.path.abspath(__file__))
    root_path = Path(root_dir)

    if not root_path.exists():
        _DISCOVERED = True
        return dict(_REGISTRY)

    builder_files = sorted(root_path.glob("builder_*.py"))
    for file_path in builder_files:
        mod_name = file_path.stem
        if str(root_path) not in sys.path:
            sys.path.insert(0, str(root_path))

        try:
            spec = importlib.util.spec_from_file_location(mod_name, file_path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)

            fmt = getattr(module, "FORMAT", None)
            kws = getattr(module, "KEYWORDS", None)
            build_fn = getattr(module, "build", None)

            if not fmt or not isinstance(fmt, str):
                continue
            if not isinstance(kws, (list, tuple, set)):
                kws = []
            if not callable(build_fn):
                continue

            register_format(
                format_id=fmt,
                keywords=list(kws),
                build_fn=build_fn,
                description=getattr(module, "__doc__", "") or f"External builder {fmt}",
                module_name=mod_name,
            )
        except Exception as exc:
            sys.stderr.write(f"format_registry: error loading {file_path.name}: {exc}\n")

    _DISCOVERED = True
    return dict(_REGISTRY)


def list_registered_formats() -> list[str]:
    """Return sorted list of all registered format identifiers."""
    discover_builders()
    return sorted(_REGISTRY.keys())


def get_format_info(format_id: str) -> dict | None:
    """Return registration info dictionary for format_id, or None if not found."""
    discover_builders()
    if not format_id:
        return None
    return _REGISTRY.get(format_id.strip().lower())


def get_builder(format_id: str):
    """Return the build callable for format_id, or None if built-in or not found."""
    info = get_format_info(format_id)
    if info:
        return info.get("build")
    return None


def classify(idea_text: str) -> str | None:
    """Classify idea text into a format identifier using keyword matching.

    Returns format_id (str) if a match is found, or None if no builder can build it.
    """
    discover_builders()
    if not idea_text or not isinstance(idea_text, str):
        return None

    norm_text = idea_text.lower().strip()
    if not norm_text:
        return None

    # Check for direct format name mentions first (e.g. format: sheets or kind=planner)
    for fid in _REGISTRY:
        if re.search(rf"\b(format|kind)\s*[:=]\s*['\"]?{re.escape(fid)}['\"]?\b", norm_text):
            return fid

    scores = {}
    tokens = _tokenize(norm_text)

    for fid, meta in _REGISTRY.items():
        score = 0
        keywords = meta.get("keywords", [])

        sorted_kws = sorted(keywords, key=lambda k: len(k.split()), reverse=True)
        for kw in sorted_kws:
            kw_norm = kw.lower().strip()
            if not kw_norm:
                continue
            if " " in kw_norm or "-" in kw_norm:
                pattern = r"\b" + re.escape(kw_norm) + r"\b"
                matches = len(re.findall(pattern, norm_text))
                if matches > 0:
                    score += matches * (10 + len(kw_norm.split()) * 5)
            else:
                if kw_norm in tokens:
                    score += 5

        if score > 0:
            scores[fid] = score

    if not scores:
        return None

    def priority(fid):
        base_score = scores[fid]
        bonus = 0
        if not _REGISTRY[fid].get("builtin", False):
            bonus += 2
        if fid in ("notion", "dated_planner", "resume"):
            bonus += 1
        return (base_score, bonus)

    best_format = max(scores.keys(), key=priority)
    return best_format


def can_build(idea_text_or_spec) -> bool:
    """Feasibility check: returns True if an idea text or spec can be built, False otherwise."""
    discover_builders()
    if not idea_text_or_spec:
        return False

    if isinstance(idea_text_or_spec, dict):
        kind = idea_text_or_spec.get("kind") or idea_text_or_spec.get("format")
        if kind and isinstance(kind, str):
            fid = kind.strip().lower()
            if fid in _REGISTRY:
                return True
        combined_text = " ".join(str(idea_text_or_spec.get(k, "")) for k in ("name", "title", "description", "niche", "payload", "what_inside"))
        return classify(combined_text) is not None

    if isinstance(idea_text_or_spec, str):
        return classify(idea_text_or_spec) is not None

    return False


def get_tenglish_unbuildable_reason(idea_text: str = "") -> str:
    """Return a descriptive owner-facing reason in Tenglish house style explaining
    why an idea cannot be built.
    """
    return "Ee draft ki registered builder dorakaledu; owner review kavali (can we build it check failed)."
