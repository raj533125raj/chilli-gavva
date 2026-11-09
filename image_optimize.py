#!/usr/bin/env python3
"""image_optimize.py — Marketing image optimizer for ready marketing assets.

Optimizes PNG and JPG images found under exported_content/marketing/ (and never
inside private pack folders):
  - Preserves exact pixel dimensions (width x height).
  - Strips metadata (EXIF, comments, timestamps).
  - PNG: saves with optimize=True; converts to palette mode (P) when lossless-safe
    (<= 256 unique colors and exact pixel values preserved).
  - JPG: saves with quality=85, optimize=True (for photographic / JPG files).
  - Never makes a file bigger: if optimized candidate is not smaller than original,
    keeps the original file intact.
  - Max-size rules: pins < 1 MB, carousel < 1.5 MB, covers < 1 MB.
  - Dry-run mode: calculates savings and checks max-size rules without writing files.
  - Atomic replace: writes to a temporary file in the same directory and replaces atomically.
  - Savings table: formats per-file and total bytes/percentages saved.
  - Owner-facing summary: short Tenglish house style summary.
  - Zero money amounts or brand logos. Pillow is the only dependency.
"""

from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageChops
    HAS_PIL = True
except ImportError:
    Image = None
    ImageChops = None
    HAS_PIL = False

# Maximum size limits in bytes based on marketing asset type / folder
MAX_SIZE_RULES: Dict[str, int] = {
    "pins": 1_000_000,       # pins < 1 MB
    "carousel": 1_500_000,   # carousel < 1.5 MB
    "covers": 1_000_000,     # covers < 1 MB
    "reel": 2_000_000,       # reels < 2 MB
}

DEFAULT_MAX_SIZE = 2_000_000  # General fallback limit for marketing images (2 MB)


def get_max_size_limit(filepath: Path | str) -> Tuple[Optional[int], Optional[str]]:
    """Determine the max size rule (limit in bytes and category) based on file path."""
    p = Path(filepath)
    parts = [part.lower() for part in p.parts]
    for category, limit in MAX_SIZE_RULES.items():
        if category in parts:
            return limit, category
    return None, None


def check_max_size(filepath: Path | str, size_bytes: Optional[int] = None) -> Dict[str, Any]:
    """Check if the file obeys max-size rules.

    Returns dict with {category, limit, size, passed, message}.
    """
    p = Path(filepath)
    if size_bytes is None:
        size_bytes = p.stat().st_size if p.exists() else 0

    limit, category = get_max_size_limit(p)
    if limit is None:
        passed = True
        msg = "No specific category limit."
    else:
        passed = size_bytes < limit
        if passed:
            msg = f"{category} size {size_bytes:,} bytes is within limit (< {limit:,} bytes)."
        else:
            msg = f"{category} size {size_bytes:,} bytes exceeds or equals limit ({limit:,} bytes)."

    return {
        "category": category,
        "limit": limit,
        "size": size_bytes,
        "passed": passed,
        "message": msg,
    }


def optimize_image_bytes(original_bytes: bytes, filename: str = "image.png") -> Tuple[bytes, bool, Dict[str, Any]]:
    """Optimize image data in memory.

    Returns:
        (best_bytes, is_modified, info_dict)
    """
    if not HAS_PIL:
        raise RuntimeError("Pillow is required for image optimization")

    ext = Path(filename).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        return original_bytes, False, {"reason": "unsupported extension", "original_size": len(original_bytes)}

    original_size = len(original_bytes)
    try:
        with Image.open(io.BytesIO(original_bytes)) as im:
            orig_width, orig_height = im.size
            orig_mode = im.mode

            candidates: List[Tuple[bytes, str]] = []

            if ext == ".png":
                # Candidate 1: Standard optimized PNG (clearing metadata by fresh save)
                bio_opt = io.BytesIO()
                im.save(bio_opt, format="PNG", optimize=True)
                opt_data = bio_opt.getvalue()
                candidates.append((opt_data, "png_optimize"))

                # Candidate 2: Palette mode if lossless-safe
                # Check color count: if unique colors <= 256
                # getcolors returns list of (count, color) or None if count > maxcolors
                colors = im.getcolors(maxcolors=257)
                if colors is not None and len(colors) <= 256:
                    try:
                        p_im = im.convert("P", palette=Image.ADAPTIVE, colors=256)
                        # Verify losslessness by converting back and comparing difference
                        recon = p_im.convert(orig_mode)
                        diff = ImageChops.difference(im, recon)
                        if diff.getbbox() is None:
                            # Exact match! Safe to use palette
                            bio_palette = io.BytesIO()
                            p_im.save(bio_palette, format="PNG", optimize=True)
                            palette_data = bio_palette.getvalue()
                            candidates.append((palette_data, "png_palette_lossless"))
                    except Exception:
                        pass

            elif ext in (".jpg", ".jpeg"):
                # JPEG optimize: quality=85, optimize=True, strip metadata
                bio_jpg = io.BytesIO()
                # Ensure mode is RGB or L for JPEG saving
                save_im = im
                if im.mode not in ("RGB", "L"):
                    save_im = im.convert("RGB")
                save_im.save(bio_jpg, format="JPEG", quality=85, optimize=True)
                jpg_data = bio_jpg.getvalue()
                candidates.append((jpg_data, "jpg_quality_85"))

    except Exception as e:
        return original_bytes, False, {"error": str(e), "original_size": original_size}

    # Find the smallest candidate
    best_bytes = original_bytes
    best_strategy = "kept_original"
    best_size = original_size
    improved = False

    for candidate_bytes, strategy in candidates:
        cand_size = len(candidate_bytes)
        # Verify dimension preservation
        try:
            with Image.open(io.BytesIO(candidate_bytes)) as check_im:
                if check_im.size != (orig_width, orig_height):
                    continue
        except Exception:
            continue

        # Strictly smaller: never make file bigger
        if cand_size < best_size:
            best_size = cand_size
            best_bytes = candidate_bytes
            best_strategy = strategy
            improved = True

    info = {
        "original_size": original_size,
        "optimized_size": best_size,
        "saved_bytes": original_size - best_size if improved else 0,
        "saved_percent": round(((original_size - best_size) / original_size) * 100, 2) if improved and original_size > 0 else 0.0,
        "dimensions": (orig_width, orig_height),
        "strategy": best_strategy,
        "mode": orig_mode,
    }

    return best_bytes, improved, info


def is_inside_pack_folder(path: Path | str) -> bool:
    """Return True if path is inside a private pack folder (e.g. products/upload/...)."""
    p = Path(path).resolve()
    parts = p.parts
    # Protected locations: products/upload, packs, or any private folders
    for i in range(len(parts) - 1):
        if parts[i] == "products" and parts[i + 1] == "upload":
            return True
        if parts[i] == "products" and parts[i + 1] == "bundles":
            return True
    return False


def optimize_file(filepath: Path | str, dry_run: bool = False) -> Dict[str, Any]:
    """Optimize a single PNG or JPG image file in-place (atomically) or in dry-run mode."""
    p = Path(filepath)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")

    # Guard: Never modify inside private pack folders
    if is_inside_pack_folder(p):
        raise PermissionError(f"Modifying inside pack folder is strictly forbidden: {p}")

    ext = p.suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        return {
            "path": str(p),
            "status": "skipped",
            "reason": f"unsupported extension {ext}",
            "original_size": p.stat().st_size,
            "optimized_size": p.stat().st_size,
            "saved_bytes": 0,
            "saved_percent": 0.0,
        }

    original_bytes = p.read_bytes()
    orig_size = len(original_bytes)

    best_bytes, improved, info = optimize_image_bytes(original_bytes, p.name)
    opt_size = len(best_bytes)

    # Check max-size rules against the resulting size
    size_rule_check = check_max_size(p, opt_size)

    result = {
        "path": str(p),
        "filename": p.name,
        "original_size": orig_size,
        "optimized_size": opt_size,
        "saved_bytes": orig_size - opt_size if improved else 0,
        "saved_percent": round(((orig_size - opt_size) / orig_size) * 100, 2) if improved and orig_size > 0 else 0.0,
        "improved": improved,
        "dry_run": dry_run,
        "dimensions": info.get("dimensions"),
        "strategy": info.get("strategy"),
        "size_rule": size_rule_check,
    }

    if improved and not dry_run:
        # Atomic replace: write to temporary file in the same directory, then replace
        parent_dir = p.parent
        fd, tmp_path_str = tempfile.mkstemp(dir=parent_dir, prefix=f".{p.stem}_opt_", suffix=p.suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(best_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path_str, p)
            result["status"] = "optimized"
        except Exception as err:
            if os.path.exists(tmp_path_str):
                try:
                    os.unlink(tmp_path_str)
                except OSError:
                    pass
            raise err
    else:
        result["status"] = "dry_run" if dry_run else "no_gain_kept_original"

    return result


def find_marketing_images(marketing_dir: Path | str) -> List[Path]:
    """Find all PNG and JPG images in marketing directory (sorted deterministically)."""
    base = Path(marketing_dir)
    if not base.exists() or not base.is_dir():
        return []

    images: List[Path] = []
    for root, _, files in os.walk(base):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in (".png", ".jpg", ".jpeg"):
                full_path = Path(root) / file
                # Extra check: never collect images if somehow under a private pack folder
                if not is_inside_pack_folder(full_path):
                    images.append(full_path)

    images.sort(key=lambda x: str(x).lower())
    return images


def format_savings_table(results: List[Dict[str, Any]]) -> str:
    """Format a clean markdown/text table showing optimization results and savings."""
    if not results:
        return "No marketing images found to optimize."

    headers = ["File", "Original", "Optimized", "Saved", "Status", "Rule Check"]
    col_widths = [40, 12, 12, 12, 18, 16]

    def fmt_row(cols: List[str]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cols, col_widths)) + " |"

    def fmt_sep() -> str:
        return "|-" + "-|-".join("-" * w for w in col_widths) + "-|"

    lines = [
        fmt_row(headers),
        fmt_sep(),
    ]

    total_orig = 0
    total_opt = 0
    total_saved = 0

    for r in results:
        fname = r.get("filename") or Path(r["path"]).name
        if len(fname) > 38:
            fname = fname[:35] + "..."
        orig_s = r.get("original_size", 0)
        opt_s = r.get("optimized_size", 0)
        saved = r.get("saved_bytes", 0)
        pct = r.get("saved_percent", 0.0)
        status = r.get("status", "unknown")

        rule_check = r.get("size_rule", {})
        rule_passed = rule_check.get("passed", True)
        rule_cat = rule_check.get("category")
        rule_str = "OK" if rule_passed else f"EXCEED ({rule_cat})"
        if rule_cat is None:
            rule_str = "-"

        total_orig += orig_s
        total_opt += opt_s
        total_saved += saved

        saved_str = f"{saved:,} B ({pct}%)" if saved > 0 else "0 B (0%)"
        lines.append(fmt_row([
            fname,
            f"{orig_s:,} B",
            f"{opt_s:,} B",
            saved_str,
            status,
            rule_str,
        ]))

    lines.append(fmt_sep())
    tot_pct = round((total_saved / total_orig) * 100, 2) if total_orig > 0 else 0.0
    tot_saved_str = f"{total_saved:,} B ({tot_pct}%)"
    lines.append(fmt_row([
        "TOTAL",
        f"{total_orig:,} B",
        f"{total_opt:,} B",
        tot_saved_str,
        f"{len(results)} files",
        "",
    ]))

    return "\n".join(lines)


def generate_tenglish_summary(results: List[Dict[str, Any]], dry_run: bool = False) -> str:
    """Generate owner-facing 1-line Tenglish summary.

    Examples:
      - Marketing images anni optimize ayyayi: 420 KB save chesamu (dry-run mode).
      - Marketing images anni optimize ayyayi: 350 KB save chesamu, pins mariyu covers rules pass ayyayi.
      - Marketing images lo konni size limits daatayi: 2 files exceed ayyayi.
    """
    if not results:
        return "Marketing folder lo images emi levu, optimize cheyadaniki emi ledu."

    total_orig = sum(r.get("original_size", 0) for r in results)
    total_saved = sum(r.get("saved_bytes", 0) for r in results)
    failed_rules = [r for r in results if not r.get("size_rule", {}).get("passed", True)]

    # Format human-readable saved amount
    if total_saved >= 1_000_000:
        saved_str = f"{round(total_saved / (1024 * 1024), 2)} MB"
    elif total_saved >= 1024:
        saved_str = f"{round(total_saved / 1024, 1)} KB"
    else:
        saved_str = f"{total_saved} B"

    mode_note = " (dry-run check)" if dry_run else ""

    if failed_rules:
        cats = sorted(set(r.get("size_rule", {}).get("category", "image") for r in failed_rules))
        return (
            f"Marketing images lo {len(failed_rules)} files size limits daatayi ({', '.join(cats)}). "
            f"Total {saved_str} save chesamu{mode_note}."
        )

    if total_saved > 0:
        return (
            f"Marketing images anni baaga optimize ayyayi: {saved_str} save chesamu, "
            f"max-size rules anni pass ayyayi{mode_note}."
        )

    return f"Marketing images already fully optimized ga unnayi, marpu ledu{mode_note}."


def optimize_marketing_folder(
    marketing_dir: Path | str = "exported_content/marketing",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Scan and optimize all PNG/JPG images in the marketing folder.

    Returns dict with summary, table, results, total_saved_bytes, and pass status.
    """
    m_dir = Path(marketing_dir)
    images = find_marketing_images(m_dir)

    results: List[Dict[str, Any]] = []
    for img_path in images:
        res = optimize_file(img_path, dry_run=dry_run)
        results.append(res)

    table = format_savings_table(results)
    summary = generate_tenglish_summary(results, dry_run=dry_run)
    all_rules_passed = all(r.get("size_rule", {}).get("passed", True) for r in results)
    total_saved = sum(r.get("saved_bytes", 0) for r in results)

    return {
        "marketing_dir": str(m_dir),
        "total_images": len(images),
        "dry_run": dry_run,
        "results": results,
        "total_saved_bytes": total_saved,
        "all_rules_passed": all_rules_passed,
        "table": table,
        "tenglish_summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Chilli Gavva Marketing Image Optimizer")
    parser.add_argument(
        "--dir",
        default="exported_content/marketing",
        help="Marketing directory path (default: exported_content/marketing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run mode: show savings table and rule checks without modifying files",
    )
    args = parser.parse_args()

    out = optimize_marketing_folder(marketing_dir=args.dir, dry_run=args.dry_run)
    print(out["table"])
    print()
    print("Summary:")
    print(out["tenglish_summary"])

    return 0 if out["all_rules_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
