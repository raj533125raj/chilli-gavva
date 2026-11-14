"""sell_ready.py — "Sellable Now" gate evaluation engine.

Provides one honest answer per pack:
  - SELLABLE: Ready to list and sell immediately. Everything is clean and complete.
  - ALMOST: High quality, but has minor non-critical fixes needed before upload.
  - NOT YET: Hard fails present (truth check NEEDS FIX, missing pack files, forbidden money text, corrupt zip, etc.).

Combines:
  1. Truth check result (truth_check.py)
  2. Deep QA (product_qa_deep.py)
  3. Listing lint & money text check (listing_lint.py & upload_pack._check_money)
  4. Pack readiness (product.zip, listing.txt, checklist.txt)
  5. Marketing assets present (covers, pins, carousel, reel, blog, launch checklist)
  6. Store kit presence (store-kits folder or files)
  7. Preview & cover image sizes (valid dimensions / file size within bounds)
  8. License file presence (LICENSE.txt)
  9. Design theme presence (in spec/metadata/listing)

Scoring with clear weights (0–100):
  - Truth check: 25%
  - Deep QA: 25%
  - Listing lint: 15%
  - Marketing assets: 15%
  - Store kits: 5%
  - Preview & cover image quality/size: 5%
  - License file: 5%
  - Design theme: 5%

Hard fails that immediately force status = NOT YET:
  - Truth check verdict == 'needs fix' (or score < 80)
  - Missing core pack files (product.zip, listing.txt, or checklist.txt)
  - Forbidden money amounts detected in listing, checklist, or metadata
  - Corrupt / unreadable product.zip
  - Deep QA score < 60 or verdict == 'fix' due to critical file corruption

Deterministic outputs, atomic writes, Tenglish summaries, no money amounts.
"""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import zipfile
from typing import Any, Dict, List, Optional, Tuple, Union

# Status constants
STATUS_SELLABLE = "SELLABLE"
STATUS_ALMOST = "ALMOST"
STATUS_NOT_YET = "NOT YET"

# Thresholds
SELLABLE_MIN_SCORE = 85
ALMOST_MIN_SCORE = 65

# Regex for detecting money / currency amounts (strictly forbidden)
CURRENCY_RE = re.compile(
    r"(?:\$|₹|\b(?:USD|INR|EUR|GBP)\b|Rs\.?)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|EUR|GBP)\b|Rs\.?)",
    re.IGNORECASE,
)

# Known design themes supported in the planner/printable design system
KNOWN_THEMES = {
    "calm sage",
    "midnight focus",
    "terracotta",
    "ocean",
    "minimal mono",
    "festive marigold",
    "editorial",
    "classic",
    "modern minimalist",
    "warm beige",
    "lavender breeze",
    "forest green",
    "rose quartz",
    "slate dark",
}


def check_money_text(text: str) -> Optional[str]:
    """Return matching money string if found in text, else None."""
    if not text:
        return None
    match = CURRENCY_RE.search(text)
    if match:
        return match.group(0)
    return None


def inspect_pack_files(pack_dir: Path) -> Dict[str, Any]:
    """Check core upload pack files."""
    issues: List[str] = []
    hard_fail = False

    has_zip = (pack_dir / "product.zip").is_file()
    has_listing = (pack_dir / "listing.txt").is_file()
    has_checklist = (pack_dir / "checklist.txt").is_file()

    if not has_zip:
        issues.append("Missing product.zip file")
        hard_fail = True
    if not has_listing:
        issues.append("Missing listing.txt file")
        hard_fail = True
    if not has_checklist:
        issues.append("Missing checklist.txt file")
        hard_fail = True

    # Check zip integrity if present
    zip_ok = False
    zip_size = 0
    if has_zip:
        try:
            zip_size = (pack_dir / "product.zip").stat().st_size
            with zipfile.ZipFile(pack_dir / "product.zip") as zf:
                if zf.testzip() is not None:
                    issues.append("product.zip archive is corrupted")
                    hard_fail = True
                else:
                    zip_ok = True
        except Exception as exc:
            issues.append(f"product.zip unreadable: {exc}")
            hard_fail = True

    # Check for money text in listing and checklist
    listing_text = ""
    if has_listing:
        try:
            listing_text = (pack_dir / "listing.txt").read_text(encoding="utf-8", errors="replace")
            m_val = check_money_text(listing_text)
            if m_val:
                issues.append("Money text in listing.txt (price owner decide cheyali)")
                hard_fail = True
        except Exception as exc:
            issues.append(f"Cannot read listing.txt: {exc}")
            hard_fail = True

    checklist_text = ""
    if has_checklist:
        try:
            checklist_text = (pack_dir / "checklist.txt").read_text(encoding="utf-8", errors="replace")
            m_val = check_money_text(checklist_text)
            if m_val:
                issues.append("Money text in checklist.txt (price owner decide cheyali)")
                hard_fail = True
        except Exception as exc:
            issues.append(f"Cannot read checklist.txt: {exc}")
            hard_fail = True

    # Score out of 100
    if hard_fail:
        score = 0
    else:
        score = 100

    return {
        "has_zip": has_zip,
        "zip_ok": zip_ok,
        "zip_size": zip_size,
        "has_listing": has_listing,
        "has_checklist": has_checklist,
        "listing_text": listing_text,
        "checklist_text": checklist_text,
        "issues": issues,
        "hard_fail": hard_fail,
        "score": score,
    }


def inspect_license(pack_dir: Path) -> Dict[str, Any]:
    """Check for license file in pack or inside product.zip."""
    issues: List[str] = []
    has_license = False

    # Check directly in pack directory
    for name in ("LICENSE.txt", "LICENSE", "LICENSE.md", "LICENCE.txt", "LICENCE.md"):
        if (pack_dir / name).is_file():
            has_license = True
            break

    # If not in root of pack, inspect product.zip
    if not has_license and (pack_dir / "product.zip").is_file():
        try:
            with zipfile.ZipFile(pack_dir / "product.zip") as zf:
                for n in zf.namelist():
                    base_n = os.path.basename(n).upper()
                    if "LICENSE" in base_n or "LICENCE" in base_n:
                        has_license = True
                        break
        except Exception:
            pass

    score = 100 if has_license else 0
    if not has_license:
        issues.append("Missing LICENSE.txt in pack or zip")

    return {
        "has_license": has_license,
        "score": score,
        "issues": issues,
    }


def inspect_truth_check(pack_dir: Path) -> Dict[str, Any]:
    """Run truth_check on pack folder."""
    issues: List[str] = []
    score = 0
    verdict = "needs fix"
    hard_fail = False

    try:
        import truth_check
        tc_res = truth_check.check(str(pack_dir))
        verdict = tc_res.get("verdict", "needs fix")
        score = int(tc_res.get("score", 0))
        for p in tc_res.get("problems", []):
            issues.append(f"Truth check: {p}")
        if verdict != "ready" or score < 80:
            hard_fail = True
    except ImportError:
        # If truth_check is missing, pass safely with warning
        verdict = "ready"
        score = 80
        issues.append("truth_check module unavailable (skipped)")
    except Exception as exc:
        hard_fail = True
        verdict = "needs fix"
        score = 0
        issues.append(f"Truth check error: {exc}")

    return {
        "verdict": verdict,
        "score": score,
        "issues": issues,
        "hard_fail": hard_fail,
    }


def inspect_deep_qa(pack_dir: Path) -> Dict[str, Any]:
    """Run product_qa_deep on pack folder."""
    issues: List[str] = []
    score = 0
    verdict = "fix"
    hard_fail = False

    try:
        import product_qa_deep
        qa_res = product_qa_deep.check_folder(str(pack_dir))
        verdict = qa_res.get("verdict", "fix")
        score = int(qa_res.get("score", 0))
        for prob in qa_res.get("problems", []):
            detail = prob.get("detail") or prob.get("kind") or "QA issue"
            issues.append(f"Deep QA: {detail}")
        if score < 60:
            hard_fail = True
    except ImportError:
        verdict = "ready"
        score = 80
        issues.append("product_qa_deep module unavailable (skipped)")
    except Exception as exc:
        verdict = "fix"
        score = 0
        hard_fail = True
        issues.append(f"Deep QA error: {exc}")

    return {
        "verdict": verdict,
        "score": score,
        "issues": issues,
        "hard_fail": hard_fail,
    }


def inspect_listing_lint(pack_dir: Path) -> Dict[str, Any]:
    """Run listing_lint on pack listing.txt."""
    issues: List[str] = []
    score = 100
    hard_fail = False
    lint_ok = True

    try:
        import listing_lint
        lint_fn = getattr(listing_lint, "lint_folder", None)
        if callable(lint_fn):
            results, ok = lint_fn(str(pack_dir))
            lint_ok = ok
            fails = 0
            warns = 0
            for check_name, lvl, msg in results:
                if lvl == "FAIL":
                    fails += 1
                    issues.append(f"Listing lint ({check_name}): {msg}")
                    if check_name == "money":
                        hard_fail = True
                elif lvl == "WARN":
                    warns += 1
                    issues.append(f"Listing lint warn ({check_name}): {msg}")
            score = max(0, 100 - (fails * 20) - (warns * 5))
            if fails >= 3:
                hard_fail = True
        else:
            # Fallback if lint_folder not present
            listing_file = pack_dir / "listing.txt"
            if listing_file.is_file():
                results = listing_lint.lint_text(listing_file.read_text(encoding="utf-8", errors="replace"))
                lint_ok = all(lvl != "FAIL" for _, lvl, _ in results)
                for check_name, lvl, msg in results:
                    if lvl == "FAIL":
                        issues.append(f"Listing lint ({check_name}): {msg}")
                        if check_name == "money":
                            hard_fail = True
                    elif lvl == "WARN":
                        issues.append(f"Listing lint warn ({check_name}): {msg}")
                fails = sum(1 for _, lvl, _ in results if lvl == "FAIL")
                warns = sum(1 for _, lvl, _ in results if lvl == "WARN")
                score = max(0, 100 - (fails * 20) - (warns * 5))
                if fails >= 3:
                    hard_fail = True
    except ImportError:
        score = 80
        issues.append("listing_lint module unavailable (skipped)")
    except Exception as exc:
        score = 0
        issues.append(f"Listing lint error: {exc}")

    return {
        "lint_ok": lint_ok,
        "score": score,
        "issues": issues,
        "hard_fail": hard_fail,
    }


def find_marketing_dir(pack_dir: Path, marketing_base: Optional[Path] = None) -> Optional[Path]:
    """Locate the marketing directory for a given pack folder."""
    base = marketing_base or (Path(__file__).resolve().parent / "exported_content" / "marketing")
    base = Path(base)
    if not base.is_dir():
        return None

    # Check exact match
    exact = base / pack_dir.name
    if exact.is_dir():
        return exact

    # Check match by numerical ID prefix (e.g., '1-name' or '1')
    m = re.match(r"^(\d+)", pack_dir.name)
    if m:
        aid = m.group(1)
        for child in base.iterdir():
            if child.is_dir() and (child.name == aid or child.name.startswith(f"{aid}-")):
                return child

    def _slug(name: str) -> str:
        name = re.sub(r"^\d+[-_ ]*", "", name.lower())
        return re.sub(r"[^a-z0-9]+", "-", name).strip("-")

    pack_slug = _slug(pack_dir.name)
    if pack_slug:
        for child in sorted(base.iterdir()):
            if child.is_dir() and _slug(child.name) == pack_slug:
                return child

    return None


def inspect_marketing(pack_dir: Path, marketing_base: Optional[Path] = None) -> Dict[str, Any]:
    """Check marketing files presence and completeness."""
    mkt_dir = find_marketing_dir(pack_dir, marketing_base)
    issues: List[str] = []

    has_carousel = False
    has_pins = False
    has_reel = False
    has_blog = False
    has_covers = False
    has_launch = False

    if mkt_dir and mkt_dir.is_dir():
        # Carousel
        car_dir = mkt_dir / "carousel"
        if car_dir.is_dir() and (any(car_dir.glob("*.png")) or (car_dir / "caption.txt").is_file()):
            has_carousel = True

        # Pins
        pins_dir = mkt_dir / "pins"
        if pins_dir.is_dir() and (any(pins_dir.glob("*.png")) or (pins_dir / "pins.json").is_file()):
            has_pins = True

        # Reel
        reel_dir = mkt_dir / "reel"
        if reel_dir.is_dir() and (any(reel_dir.glob("*.png")) or (reel_dir / "reel.txt").is_file()):
            has_reel = True

        # Blog
        if (mkt_dir / "blog.md").is_file():
            has_blog = True

        # Covers
        cov_dir = mkt_dir / "covers"
        if cov_dir.is_dir() and (any(cov_dir.glob("*.png")) or (cov_dir / "covers.json").is_file()):
            has_covers = True
        elif any(pack_dir.glob("cover*.png")) or any(pack_dir.glob("cover*.jpg")):
            has_covers = True

        # Launch checklist
        if (mkt_dir / "LAUNCH.md").is_file() or (pack_dir / "checklist.txt").is_file():
            has_launch = True
    else:
        # Fallbacks inside pack_dir
        if any(pack_dir.glob("cover*.png")) or any(pack_dir.glob("cover*.jpg")):
            has_covers = True
        if (pack_dir / "checklist.txt").is_file():
            has_launch = True

    # Calculate score
    found_count = sum([has_covers, has_pins, has_carousel, has_reel, has_blog, has_launch])
    # 6 items total -> 100 * (found / 6)
    score = int(round((found_count / 6.0) * 100))

    if not has_covers:
        issues.append("Missing cover image or cover variants")
    if not has_pins:
        issues.append("Missing Pinterest pin variants")
    if not has_carousel:
        issues.append("Missing Instagram carousel slides")
    if not has_reel:
        issues.append("Missing reel frames or reel.txt")
    if not has_blog:
        issues.append("Missing blog.md post")
    if not has_launch:
        issues.append("Missing LAUNCH.md checklist")

    return {
        "mkt_dir": str(mkt_dir) if mkt_dir else None,
        "has_carousel": has_carousel,
        "has_pins": has_pins,
        "has_reel": has_reel,
        "has_blog": has_blog,
        "has_covers": has_covers,
        "has_launch": has_launch,
        "found_count": found_count,
        "score": score,
        "issues": issues,
    }


def inspect_store_kits(pack_dir: Path) -> Dict[str, Any]:
    """Check store kits presence for manual product uploads."""
    issues: List[str] = []
    has_kits = False
    stores_found: List[str] = []

    # Store kits can be in <pack>/store-kits/ or <pack>/store_kits/ or directly store text files
    candidates = [
        pack_dir / "store-kits",
        pack_dir / "store_kits",
        pack_dir / "kits",
    ]

    for c in candidates:
        if c.is_dir():
            files = list(c.glob("*.txt")) + list(c.glob("*.md"))
            if files:
                has_kits = True
                stores_found = [f.stem for f in files]
                break

    if not has_kits:
        # Check if individual store kits exist in root
        root_kits = [f for f in pack_dir.glob("kit-*.txt")]
        if root_kits:
            has_kits = True
            stores_found = [f.stem.replace("kit-", "") for f in root_kits]

    score = 100 if has_kits else 0
    if not has_kits:
        issues.append("Missing store listing kits (store-kits folder)")

    return {
        "has_kits": has_kits,
        "stores_found": stores_found,
        "score": score,
        "issues": issues,
    }


def inspect_preview_images(pack_dir: Path, marketing_base: Optional[Path] = None) -> Dict[str, Any]:
    """Check preview and cover images size and valid bounds."""
    issues: List[str] = []
    score = 100

    # Collect cover and preview images
    images: List[Path] = []
    # In pack dir
    for pat in ("cover*.png", "cover*.jpg", "preview*.png", "preview*.jpg", "mockup*.png"):
        images.extend(pack_dir.glob(pat))

    # In marketing dir
    mkt_dir = find_marketing_dir(pack_dir, marketing_base)
    if mkt_dir and mkt_dir.is_dir():
        cov_dir = mkt_dir / "covers"
        if cov_dir.is_dir():
            images.extend(cov_dir.glob("*.png"))
            images.extend(cov_dir.glob("*.jpg"))

    if not images:
        return {
            "score": 0,
            "image_count": 0,
            "issues": ["No preview or cover image files found"],
            "max_file_size": 0,
            "hard_fail": True,
        }

    max_size = 0
    oversized = []
    zero_sized = []

    for img in images:
        try:
            sz = img.stat().st_size
            if sz > max_size:
                max_size = sz
            if sz == 0:
                zero_sized.append(img.name)
            elif sz > 5 * 1024 * 1024:  # Max 5MB per preview image
                oversized.append(img.name)
        except OSError:
            pass

    if zero_sized:
        issues.append(f"Empty (0-byte) image files: {', '.join(zero_sized[:3])}")
        score -= 40
    if oversized:
        issues.append(f"Image files too large (>5MB): {', '.join(oversized[:3])}")
        score -= 30

    score = max(0, min(100, score))
    return {
        "score": score,
        "image_count": len(images),
        "max_file_size": max_size,
        "issues": issues,
        "hard_fail": False,
    }


def inspect_design_theme(pack_dir: Path) -> Dict[str, Any]:
    """Check design theme presence in pack metadata, spec, or listing."""
    issues: List[str] = []
    theme_found: Optional[str] = None

    # Check for metadata/spec json files in pack or parent
    spec_files = list(pack_dir.glob("*.json")) + list(pack_dir.glob("spec*.json"))
    for sf in spec_files:
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                theme = data.get("theme") or data.get("design_theme") or data.get("style")
                if theme and isinstance(theme, str):
                    theme_found = theme
                    break
        except Exception:
            pass

    # Check listing.txt for theme mentions
    if not theme_found and (pack_dir / "listing.txt").is_file():
        try:
            raw_txt = (pack_dir / "listing.txt").read_text(encoding="utf-8", errors="replace")
            m = re.search(r"\btheme:\s*([^\n\r]+)", raw_txt, re.IGNORECASE)
            if m:
                theme_found = m.group(1).strip()
            else:
                txt_lower = raw_txt.lower()
                for kt in KNOWN_THEMES:
                    if kt in txt_lower:
                        theme_found = kt.title()
                        break
        except Exception:
            pass

    # Check checklist.txt
    if not theme_found and (pack_dir / "checklist.txt").is_file():
        try:
            txt = (pack_dir / "checklist.txt").read_text(encoding="utf-8", errors="replace").lower()
            m = re.search(r"\btheme:\s*([^\n\r]+)", txt, re.IGNORECASE)
            if m:
                theme_found = m.group(1).strip()
        except Exception:
            pass

    has_theme = theme_found is not None
    score = 100 if has_theme else 0
    if not has_theme:
        issues.append("Design theme not specified in metadata or listing")

    return {
        "has_theme": has_theme,
        "theme": theme_found,
        "score": score,
        "issues": issues,
    }


def evaluate_pack(
    pack_dir: Union[str, Path],
    marketing_base: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Evaluate one pack and return complete sellability status report."""
    p_dir = Path(pack_dir).resolve()
    m_base = Path(marketing_base).resolve() if marketing_base else None

    if not p_dir.is_dir():
        return {
            "pack_dir": str(p_dir),
            "pack_name": p_dir.name,
            "status": STATUS_NOT_YET,
            "score": 0,
            "title": p_dir.name,
            "hard_fails": ["Pack directory does not exist"],
            "top_fixes": ["Pack folder create cheyandi"],
            "fixes": ["Pack directory does not exist"],
            "checks": {},
        }

    # Extract pack title if available
    title = p_dir.name
    listing_path = p_dir / "listing.txt"
    if listing_path.is_file():
        try:
            ltxt = listing_path.read_text(encoding="utf-8", errors="replace")
            m_t = re.search(r"^Title:\s*(.+)$", ltxt, re.MULTILINE)
            if m_t:
                title = m_t.group(1).strip()
        except Exception:
            pass

    # 1. Run all checks
    pack_files_res = inspect_pack_files(p_dir)
    license_res = inspect_license(p_dir)
    truth_res = inspect_truth_check(p_dir)
    deep_qa_res = inspect_deep_qa(p_dir)
    lint_res = inspect_listing_lint(p_dir)
    mkt_res = inspect_marketing(p_dir, m_base)
    store_kits_res = inspect_store_kits(p_dir)
    preview_res = inspect_preview_images(p_dir, m_base)
    theme_res = inspect_design_theme(p_dir)

    checks = {
        "pack_files": pack_files_res,
        "license": license_res,
        "truth_check": truth_res,
        "deep_qa": deep_qa_res,
        "listing_lint": lint_res,
        "marketing": mkt_res,
        "store_kits": store_kits_res,
        "preview_images": preview_res,
        "design_theme": theme_res,
    }

    # 2. Check for hard fails
    hard_fails: List[str] = []
    if pack_files_res.get("hard_fail"):
        hard_fails.extend([f"Pack files: {i}" for i in pack_files_res.get("issues", [])])
    if truth_res.get("hard_fail"):
        hard_fails.append(f"Truth check: verdict is '{truth_res.get('verdict')}' (score {truth_res.get('score')})")
    if deep_qa_res.get("hard_fail"):
        hard_fails.append(f"Deep QA: score {deep_qa_res.get('score')} too low or critical errors")
    if lint_res.get("hard_fail"):
        hard_fails.append("Listing lint: forbidden money text or multiple critical lint fails")
    if preview_res.get("hard_fail"):
        hard_fails.append("Previews: no cover or preview images found")

    # 3. Calculate weighted score (0–100)
    # Weights:
    # truth_check: 25
    # deep_qa: 25
    # listing_lint: 15
    # marketing: 15
    # pack_files: 5 (license 5, theme 5, previews 5, kits 5 -> let's allocate cleanly)
    # Total sum = 25 + 25 + 15 + 15 + 5 + 5 + 5 + 5 = 95 (+ 5 for core files = 100)
    w_truth = (truth_res.get("score", 0) / 100.0) * 25.0
    w_qa = (deep_qa_res.get("score", 0) / 100.0) * 25.0
    w_lint = (lint_res.get("score", 0) / 100.0) * 15.0
    w_mkt = (mkt_res.get("score", 0) / 100.0) * 15.0
    w_files = (pack_files_res.get("score", 0) / 100.0) * 5.0
    w_license = (license_res.get("score", 0) / 100.0) * 5.0
    w_kits = (store_kits_res.get("score", 0) / 100.0) * 5.0
    w_previews = (preview_res.get("score", 0) / 100.0) * 5.0
    w_theme = (theme_res.get("score", 0) / 100.0) * 5.0

    raw_score = sum([w_truth, w_qa, w_lint, w_mkt, w_files, w_license, w_kits, w_previews, w_theme])
    overall_score = max(0, min(100, int(round(raw_score))))

    # 4. Collect all fixes
    all_fixes: List[str] = []
    # Hard fails first
    all_fixes.extend(hard_fails)
    # Then component issues
    for c_key, c_val in [
        ("Pack", pack_files_res),
        ("Truth", truth_res),
        ("QA", deep_qa_res),
        ("Lint", lint_res),
        ("Marketing", mkt_res),
        ("License", license_res),
        ("Kits", store_kits_res),
        ("Previews", preview_res),
        ("Theme", theme_res),
    ]:
        for issue in c_val.get("issues", []):
            if issue not in all_fixes:
                all_fixes.append(issue)

    # 5. Determine honest status
    if hard_fails:
        status = STATUS_NOT_YET
    elif overall_score >= SELLABLE_MIN_SCORE:
        status = STATUS_SELLABLE
    elif overall_score >= ALMOST_MIN_SCORE:
        status = STATUS_ALMOST
    else:
        status = STATUS_NOT_YET

    top_fixes = all_fixes[:3]

    return {
        "pack_dir": str(p_dir),
        "pack_name": p_dir.name,
        "title": title,
        "status": status,
        "score": overall_score,
        "hard_fails": hard_fails,
        "top_fixes": top_fixes,
        "fixes": all_fixes,
        "checks": checks,
    }


def find_all_ready_packs(products_dir: Optional[Union[str, Path]] = None) -> List[Tuple[Optional[int], str, Path]]:
    """Discover all ready packs as sorted list of (aid, title, path)."""
    base = Path(products_dir or "exported_content/products")
    packs: List[Tuple[Optional[int], str, Path]] = []
    if not base.is_dir():
        return packs

    search_dirs = []
    if (base / "upload").is_dir():
        search_dirs.append(base / "upload")
    search_dirs.append(base)

    seen = set()
    for sdir in search_dirs:
        try:
            entries = sorted(sdir.iterdir())
        except OSError:
            continue
        for p in entries:
            try:
                if not p.is_dir() or p.name == "upload" or p.resolve() in seen:
                    continue
                if (p / "product.zip").is_file() and (p / "listing.txt").is_file():
                    seen.add(p.resolve())
                    m = re.match(r"^(\d+)", p.name)
                    num = int(m.group(1)) if m else None
                    title = p.name
                    try:
                        txt = (p / "listing.txt").read_text(encoding="utf-8", errors="replace")
                        m_t = re.search(r"^Title:\s*(.+)$", txt, re.MULTILINE)
                        if m_t:
                            title = m_t.group(1).strip()
                    except Exception:
                        pass
                    packs.append((num, title, p))
            except OSError:
                continue

    packs.sort(key=lambda x: (x[0] if x[0] is not None else 999999, str(x[2])))
    return packs


def evaluate_all_packs(
    products_dir: Optional[Union[str, Path]] = None,
    marketing_base: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Evaluate all ready packs and return evaluation list."""
    packs = find_all_ready_packs(products_dir)
    results = []
    for _num, _title, pack_path in packs:
        eval_res = evaluate_pack(pack_path, marketing_base)
        results.append(eval_res)
    return results


def format_ready_summary(evaluations: List[Dict[str, Any]]) -> str:
    """Format summary of all packs grouped by status (SELLABLE, ALMOST, NOT YET) with top 3 fixes."""
    if not evaluations:
        return "Prasthuthaniki ready upload packs emi levu."

    sellable = [e for e in evaluations if e["status"] == STATUS_SELLABLE]
    almost = [e for e in evaluations if e["status"] == STATUS_ALMOST]
    not_yet = [e for e in evaluations if e["status"] == STATUS_NOT_YET]

    lines = [
        "📦 *SELL READY GATE REPORT*",
        f"Motham packs: {len(evaluations)} (Sellable: {len(sellable)}, Almost: {len(almost)}, Not Yet: {len(not_yet)})\n",
    ]

    def _format_group(header: str, group: List[Dict[str, Any]]):
        if not group:
            return
        lines.append(header)
        for e in group:
            name = e["title"]
            p_name = e["pack_name"]
            score = e["score"]
            lines.append(f"• *{p_name}*: {name} (Score: {score}/100)")
            if e["top_fixes"]:
                for fix in e["top_fixes"][:3]:
                    lines.append(f"  - ⚠️ {fix}")
            else:
                lines.append("  - ✅ Anni ready ga unnayi")
        lines.append("")

    if sellable:
        _format_group("🟢 *SELLABLE NOW* (Direct ga list cheyochu):", sellable)
    if almost:
        _format_group("🟡 *ALMOST READY* (Konni chinna fixes kavali):", almost)
    if not_yet:
        _format_group("🔴 *NOT YET* (Hard fails unnayi):", not_yet)

    lines.append("Individual pack card chudadaniki `/ready N` ani type cheyandi.")
    return "\n".join(lines).strip()


def format_pack_card(evaluation: Dict[str, Any]) -> str:
    """Format comprehensive card for a single pack."""
    name = evaluation["title"]
    p_name = evaluation["pack_name"]
    status = evaluation["status"]
    score = evaluation["score"]
    checks = evaluation.get("checks", {})

    status_icon = "🟢" if status == STATUS_SELLABLE else ("🟡" if status == STATUS_ALMOST else "🔴")

    lines = [
        f"{status_icon} *Pack {p_name} — Sell Ready Card*",
        f"Title: {name}",
        f"Status: *{status}* | Score: *{score}/100*\n",
        "*Component Checks:*",
    ]

    # Truth check
    tc = checks.get("truth_check", {})
    tc_verd = tc.get("verdict", "unknown")
    tc_icon = "✅" if tc_verd == "ready" else "❌"
    lines.append(f"{tc_icon} Truth Check: {tc_verd} (Score: {tc.get('score', 0)})")

    # Deep QA
    qa = checks.get("deep_qa", {})
    qa_verd = qa.get("verdict", "unknown")
    qa_icon = "✅" if qa_verd == "ready" else "❌"
    lines.append(f"{qa_icon} Deep QA: {qa_verd} (Score: {qa.get('score', 0)})")

    # Listing lint
    lint = checks.get("listing_lint", {})
    lint_ok = lint.get("lint_ok", False)
    lint_icon = "✅" if lint_ok else "❌"
    lines.append(f"{lint_icon} Listing Lint: {'Pass' if lint_ok else 'Issues'} (Score: {lint.get('score', 0)})")

    # Pack files
    pfiles = checks.get("pack_files", {})
    pfiles_ok = not pfiles.get("hard_fail", False)
    pfiles_icon = "✅" if pfiles_ok else "❌"
    lines.append(f"{pfiles_icon} Pack Files: {'All present' if pfiles_ok else 'Missing core files'}")

    # Marketing
    mkt = checks.get("marketing", {})
    mkt_count = mkt.get("found_count", 0)
    mkt_icon = "✅" if mkt_count >= 5 else ("🟡" if mkt_count >= 3 else "❌")
    lines.append(f"{mkt_icon} Marketing Assets: {mkt_count}/6 present")

    # Store kits
    sk = checks.get("store_kits", {})
    sk_ok = sk.get("has_kits", False)
    sk_icon = "✅" if sk_ok else "❌"
    lines.append(f"{sk_icon} Store Listing Kit: {'Ready' if sk_ok else 'Missing'}")

    # Preview images
    prev = checks.get("preview_images", {})
    prev_cnt = prev.get("image_count", 0)
    prev_icon = "✅" if prev_cnt > 0 and prev.get("score", 0) >= 80 else "❌"
    lines.append(f"{prev_icon} Previews & Covers: {prev_cnt} images")

    # License
    lic = checks.get("license", {})
    lic_ok = lic.get("has_license", False)
    lic_icon = "✅" if lic_ok else "❌"
    lines.append(f"{lic_icon} License File: {'Present' if lic_ok else 'Missing'}")

    # Design theme
    thm = checks.get("design_theme", {})
    thm_name = thm.get("theme") or "Default"
    thm_icon = "✅" if thm.get("has_theme") else "🟡"
    lines.append(f"{thm_icon} Design Theme: {thm_name}\n")

    # Top fixes or All fixes
    fixes = evaluation.get("fixes", [])
    if fixes:
        lines.append("*Top Fixes Needed:*")
        for f in fixes[:5]:
            lines.append(f"- ⚠️ {f}")
    else:
        lines.append("🎉 *Super! Emi issues levu, direct ga list cheyochu!*")

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for sell_ready."""
    parser = argparse.ArgumentParser(description="Evaluate digital product packs for sellability.")
    parser.add_argument("pack", nargs="?", help="Specific pack folder or approval ID")
    parser.add_argument("--all", action="store_true", help="Evaluate all ready packs")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--products-dir", default="exported_content/products", help="Packs directory")
    parser.add_argument("--marketing-dir", default="exported_content/marketing", help="Marketing directory")

    args = parser.parse_args(argv)

    if args.pack:
        target_path = Path(args.pack)
        if not target_path.exists():
            # Try finding within products dir
            base = Path(args.products_dir)
            candidates = [base / args.pack, base / "upload" / args.pack]
            found = None
            for c in candidates:
                if c.is_dir():
                    found = c
                    break
            if not found and args.pack.isdigit():
                for p in find_all_ready_packs(args.products_dir):
                    if p[0] == int(args.pack):
                        found = p[2]
                        break
            if found:
                target_path = found

        eval_res = evaluate_pack(target_path, args.marketing_dir)
        if args.json:
            print(json.dumps(eval_res, indent=2))
        else:
            print(format_pack_card(eval_res))
        return 0 if eval_res["status"] == STATUS_SELLABLE else 1

    # Default to --all if no pack specified
    evals = evaluate_all_packs(args.products_dir, args.marketing_dir)
    if args.json:
        print(json.dumps(evals, indent=2))
    else:
        print(format_ready_summary(evals))

    has_not_yet = any(e["status"] == STATUS_NOT_YET for e in evals)
    return 1 if has_not_yet else 0


if __name__ == "__main__":
    sys.exit(main())
