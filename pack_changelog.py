#!/usr/bin/env python3
"""pack_changelog.py — Pack versions tracker and changelog generator.

BINNY-T4C ROBOT: PACK VERSIONS AND CHANGELOG.
For each ready pack:
- Compute file hashes (sha256) and sizes.
- Compare with the last snapshot stored in exported_content/products/versions/<pack>.json (outside the pack).
- Bump a version:
  - 1.0 initial version
  - 1.1, 1.2... for changed files (minor bump)
  - 2.0 when product.zip changes (major bump, resetting minor to 0)
- Write a short CHANGELOG entry (date, changed files) to:
  exported_content/products/versions/<pack>-CHANGELOG.md
- Outputs go OUTSIDE the private pack folders.
- Atomic writes via temp files, deterministic outputs.
- Owner-facing text in Tenglish house style (Telugu sentence words + English nouns).
- No money amounts anywhere (tests.test_r22_markdown_currency must pass).
- No brand logos/names in generated content.
- Nothing posts/publishes/emails automatically.
- CI installs ONLY Pillow: guard any other external imports.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

# Default locations
DEFAULT_PRODUCTS_DIR = Path("exported_content/products")
DEFAULT_VERSIONS_DIR = DEFAULT_PRODUCTS_DIR / "versions"

# Money regex check to ensure zero currency leak
MONEY_PATTERNS = [
    re.compile(r"(?:\$|₹|\b(?:USD|INR|Rs\.?|EUR|GBP)\b)\s*\d", re.IGNORECASE),
    re.compile(r"\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?|EUR|GBP)\b)", re.IGNORECASE),
]


def check_for_money(text: str) -> Optional[str]:
    """Return matched money string if found in text, else None."""
    for pat in MONEY_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def sanitize_no_money(text: str) -> str:
    """Sanitize any currency amount from text."""
    for pat in MONEY_PATTERNS:
        text = pat.sub("[REMOVED]", text)
    return text


def sha256_file(path: Path) -> str:
    """Compute sha256 hex digest of a file in 64KB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def discover_ready_packs(base_products_dir: Path | str = DEFAULT_PRODUCTS_DIR) -> List[Path]:
    """Find all ready upload packs under base_products_dir/upload or base_products_dir.

    A pack folder is considered ready if it is a directory and contains listing.txt
    (and usually product.zip).
    """
    base = Path(base_products_dir)
    upload_dir = base / "upload" if (base / "upload").is_dir() else base
    if not upload_dir.is_dir():
        return []

    ready: List[Path] = []
    try:
        entries = sorted(upload_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return []

    for item in entries:
        if not item.is_dir() or item.name.startswith("."):
            continue
        if (item / "listing.txt").is_file():
            ready.append(item)
    return ready


def snapshot_pack(pack_dir: Path | str) -> Dict[str, Any]:
    """Scan all files directly under pack_dir and compute relative paths, sha256, and sizes.

    Ignores hidden files (starting with '.') and subdirectories.
    Deterministic sorted output.
    """
    pdir = Path(pack_dir).resolve()
    if not pdir.is_dir():
        raise FileNotFoundError(f"Pack folder not found: {pdir}")

    files_info: Dict[str, Dict[str, Any]] = {}
    total_size = 0

    try:
        entries = sorted(pdir.iterdir(), key=lambda p: p.name)
    except OSError:
        entries = []

    for entry in entries:
        if not entry.is_file() or entry.name.startswith("."):
            continue
        size = entry.stat().st_size
        file_hash = sha256_file(entry)
        files_info[entry.name] = {
            "size": size,
            "sha256": file_hash,
        }
        total_size += size

    return {
        "pack_name": pdir.name,
        "files_count": len(files_info),
        "total_size": total_size,
        "files": files_info,
    }


def compare_snapshots(
    old_files: Dict[str, Dict[str, Any]],
    new_files: Dict[str, Dict[str, Any]],
) -> Dict[str, List[str]]:
    """Compare two snapshots' files dictionaries.

    Returns dict with keys:
    - added: list of added file names
    - removed: list of removed file names
    - modified: list of modified file names (hash or size changed)
    - unchanged: list of unchanged file names
    """
    old_names = set(old_files.keys())
    new_names = set(new_files.keys())

    added = sorted(list(new_names - old_names))
    removed = sorted(list(old_names - new_names))
    common = sorted(list(old_names & new_names))

    modified = []
    unchanged = []

    for name in common:
        o = old_files[name]
        n = new_files[name]
        if o.get("sha256") != n.get("sha256") or o.get("size") != n.get("size"):
            modified.append(name)
        else:
            unchanged.append(name)

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged": unchanged,
    }


def parse_version(ver_str: str) -> Tuple[int, int]:
    """Parse string 'major.minor' into tuple of integers."""
    match = re.match(r"^(\d+)\.(\d+)$", ver_str.strip())
    if match:
        return int(match.group(1)), int(match.group(2))
    # Fallback if non-standard
    nums = re.findall(r"\d+", ver_str)
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    elif len(nums) == 1:
        return int(nums[0]), 0
    return 1, 0


def format_version(major: int, minor: int) -> str:
    """Format major.minor version string."""
    return f"{major}.{minor}"


def bump_version(current_ver: Optional[str], diff: Dict[str, List[str]]) -> Tuple[str, bool]:
    """Determine the next version based on current version and diff.

    Rules:
    - If no current version: initial version is "1.0", changed = True
    - If diff has no changes (added, removed, modified are all empty):
        keep current_ver, changed = False
    - If "product.zip" was modified or added or removed:
        bump major (e.g. 1.0 -> 2.0, 1.3 -> 2.0), minor reset to 0, changed = True
    - If any other files were changed (added, removed, or modified):
        bump minor (e.g. 1.0 -> 1.1, 2.0 -> 2.1), changed = True
    """
    if not current_ver:
        return "1.0", True

    all_changed_files = diff.get("added", []) + diff.get("removed", []) + diff.get("modified", [])
    if not all_changed_files:
        return current_ver, False

    major, minor = parse_version(current_ver)

    if "product.zip" in all_changed_files:
        return format_version(major + 1, 0), True
    else:
        return format_version(major, minor + 1), True


def atomic_write_text(dest_path: Path | str, content: str, encoding: str = "utf-8") -> None:
    """Write text atomically using a temporary file in the same directory."""
    dest = Path(dest_path).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile("w", dir=dest.parent, delete=False, encoding=encoding)
    try:
        temp_file.write(content)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()
        os.replace(temp_file.name, dest)
    except Exception:
        if os.path.exists(temp_file.name):
            try:
                os.remove(temp_file.name)
            except OSError:
                pass
        raise


def atomic_write_json(dest_path: Path | str, data: Any) -> None:
    """Write json data atomically with deterministic formatting."""
    content = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    atomic_write_text(dest_path, content)


def load_previous_snapshot(json_path: Path | str) -> Optional[Dict[str, Any]]:
    """Load previous snapshot json file if present, returning dict or None."""
    p = Path(json_path)
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def format_tenglish_summary(
    pack_name: str,
    version: str,
    prev_version: Optional[str],
    diff: Dict[str, List[str]],
    is_initial: bool,
) -> str:
    """Generate owner-facing Tenglish house style summary line."""
    if is_initial:
        return f"{pack_name} pack kosam initial snapshot v{version} register chesam. Anni files ready ga unnay."

    added = diff.get("added", [])
    removed = diff.get("removed", [])
    modified = diff.get("modified", [])
    total_changes = len(added) + len(removed) + len(modified)

    if total_changes == 0:
        return f"{pack_name} pack lo emi marledu, version v{version} stable ga undi."

    if "product.zip" in added or "product.zip" in removed or "product.zip" in modified:
        return (
            f"{pack_name} pack lo product.zip update ayindi, andukani major version bump v{prev_version} -> v{version} chesam."
        )
    return (
        f"{pack_name} pack lo {total_changes} file(s) update ayyayi, minor version bump v{prev_version} -> v{version} chesam."
    )


def format_changelog_entry(
    version: str,
    date_str: str,
    diff: Dict[str, List[str]],
    files_snapshot: Dict[str, Dict[str, Any]],
    is_initial: bool,
) -> str:
    """Format a single Markdown changelog entry."""
    lines: List[str] = []
    lines.append(f"## [{version}] - {date_str}")
    lines.append("")

    if is_initial:
        lines.append("Initial pack release snapshot.")
        lines.append("")
        lines.append("### Files included")
        for fname in sorted(files_snapshot.keys()):
            sz = files_snapshot[fname].get("size", 0)
            lines.append(f"- `{fname}` ({sz} bytes)")
        lines.append("")
        lines.append("*Tenglish note: Pack initial snapshot record chesam, files anni set ayyayi.*")
        lines.append("")
        return "\n".join(lines)

    added = diff.get("added", [])
    removed = diff.get("removed", [])
    modified = diff.get("modified", [])

    if added:
        lines.append("### Added")
        for fname in added:
            sz = files_snapshot.get(fname, {}).get("size", 0)
            lines.append(f"- `{fname}` ({sz} bytes)")
        lines.append("")

    if modified:
        lines.append("### Changed")
        for fname in modified:
            sz = files_snapshot.get(fname, {}).get("size", 0)
            lines.append(f"- `{fname}` ({sz} bytes)")
        lines.append("")

    if removed:
        lines.append("### Removed")
        for fname in removed:
            lines.append(f"- `{fname}`")
        lines.append("")

    if "product.zip" in (added + removed + modified):
        lines.append("*Tenglish note: Main product.zip marindi, kothadi bundle chesam.*")
    else:
        lines.append("*Tenglish note: Files update ayyayi, details paina record chesam.*")
    lines.append("")
    return "\n".join(lines)


def update_changelog_markdown(
    changelog_path: Path,
    pack_name: str,
    new_entry: str,
) -> None:
    """Read existing CHANGELOG.md (or create new header) and prepend the new entry after header."""
    header = f"# Changelog - {pack_name}\n\nAll notable changes to this digital product pack will be documented here.\n\n"
    if changelog_path.is_file():
        try:
            existing = changelog_path.read_text(encoding="utf-8")
        except Exception:
            existing = ""
    else:
        existing = ""

    if not existing.strip():
        full_content = header + new_entry
    else:
        # Check if header exists
        if existing.startswith(f"# Changelog - {pack_name}"):
            # Split off header
            parts = existing.split("\n\n", 2)
            if len(parts) >= 3 and parts[0].startswith("# Changelog"):
                rest = parts[2]
                full_content = f"{parts[0]}\n\n{parts[1]}\n\n{new_entry}\n{rest}"
            else:
                full_content = header + new_entry + "\n" + existing
        else:
            full_content = header + new_entry + "\n" + existing

    atomic_write_text(changelog_path, full_content)


def process_pack(
    pack_dir: Path | str,
    versions_dir: Path | str = DEFAULT_VERSIONS_DIR,
    snapshot_date: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Process a single ready pack.

    1. Snapshot current files in pack_dir (hashes, sizes).
    2. Read previous snapshot from <versions_dir>/<pack>.json.
    3. Compare snapshots, detect diff.
    4. Bump version:
       - 1.0 initial
       - unchanged -> keep version, do not append changelog
       - product.zip changed -> major bump (X.0)
       - other files changed -> minor bump (X.Y)
    5. Write updated JSON snapshot and update <pack>-CHANGELOG.md.
    6. Return result dict with summary and status.
    """
    pdir = Path(pack_dir).resolve()
    pack_name = pdir.name
    vdir = Path(versions_dir).resolve()

    date_str = snapshot_date or datetime.date.today().isoformat()

    current_snap = snapshot_pack(pdir)
    json_path = vdir / f"{pack_name}.json"
    changelog_path = vdir / f"{pack_name}-CHANGELOG.md"

    prev_data = load_previous_snapshot(json_path)

    if prev_data is None:
        is_initial = True
        prev_version = None
        diff = {
            "added": sorted(list(current_snap["files"].keys())),
            "removed": [],
            "modified": [],
            "unchanged": [],
        }
        new_version, changed = bump_version(None, diff)
    else:
        is_initial = False
        prev_version = prev_data.get("version", "1.0")
        prev_files = prev_data.get("files", {})
        diff = compare_snapshots(prev_files, current_snap["files"])
        new_version, changed = bump_version(prev_version, diff)

    tenglish_msg = format_tenglish_summary(
        pack_name=pack_name,
        version=new_version,
        prev_version=prev_version,
        diff=diff,
        is_initial=is_initial,
    )

    result: Dict[str, Any] = {
        "pack_name": pack_name,
        "is_initial": is_initial,
        "changed": changed,
        "previous_version": prev_version,
        "version": new_version,
        "date": date_str,
        "diff": diff,
        "tenglish_summary": tenglish_msg,
        "json_path": str(json_path),
        "changelog_path": str(changelog_path),
        "dry_run": dry_run,
    }

    if not dry_run:
        # Write JSON snapshot
        new_snap_data = {
            "pack_name": pack_name,
            "version": new_version,
            "last_updated": date_str,
            "files_count": current_snap["files_count"],
            "total_size": current_snap["total_size"],
            "files": current_snap["files"],
        }
        # If there were previous version histories, we can also record a history list
        history = list(prev_data.get("history", [])) if prev_data else []
        if changed:
            history.append({
                "version": new_version,
                "date": date_str,
                "changed_files": diff["added"] + diff["modified"] + diff["removed"],
            })
        new_snap_data["history"] = history

        atomic_write_json(json_path, new_snap_data)

        # If files changed, update CHANGELOG.md
        if changed:
            entry_md = format_changelog_entry(
                version=new_version,
                date_str=date_str,
                diff=diff,
                files_snapshot=current_snap["files"],
                is_initial=is_initial,
            )
            update_changelog_markdown(changelog_path, pack_name, entry_md)

    return result


def process_all_ready_packs(
    products_dir: Path | str = DEFAULT_PRODUCTS_DIR,
    versions_dir: Path | str = DEFAULT_VERSIONS_DIR,
    snapshot_date: Optional[str] = None,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Process all ready packs found under products_dir."""
    ready_packs = discover_ready_packs(products_dir)
    results: List[Dict[str, Any]] = []
    for pack in ready_packs:
        res = process_pack(
            pack_dir=pack,
            versions_dir=versions_dir,
            snapshot_date=snapshot_date,
            dry_run=dry_run,
        )
        results.append(res)
    return results


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Pack versions tracker and changelog generator outside pack folders."
    )
    parser.add_argument(
        "--pack",
        type=str,
        default=None,
        help="Path or name of a specific pack to process.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all ready upload packs.",
    )
    parser.add_argument(
        "--products-dir",
        type=str,
        default=str(DEFAULT_PRODUCTS_DIR),
        help="Products root directory (defaults to exported_content/products).",
    )
    parser.add_argument(
        "--versions-dir",
        type=str,
        default=str(DEFAULT_VERSIONS_DIR),
        help="Versions directory (defaults to exported_content/products/versions).",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Override snapshot date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output to stdout.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without writing files.",
    )

    args = parser.parse_args()

    results: List[Dict[str, Any]] = []

    if args.pack:
        pack_path = Path(args.pack)
        if not pack_path.is_dir():
            # Check under products_dir/upload or products_dir
            cand1 = Path(args.products_dir) / "upload" / args.pack
            cand2 = Path(args.products_dir) / args.pack
            if cand1.is_dir():
                pack_path = cand1
            elif cand2.is_dir():
                pack_path = cand2
            else:
                sys.stderr.write(f"Pack not found: {args.pack}\n")
                return 1
        res = process_pack(
            pack_dir=pack_path,
            versions_dir=args.versions_dir,
            snapshot_date=args.date,
            dry_run=args.dry_run,
        )
        results.append(res)
    elif args.all:
        results = process_all_ready_packs(
            products_dir=args.products_dir,
            versions_dir=args.versions_dir,
            snapshot_date=args.date,
            dry_run=args.dry_run,
        )
    else:
        # Default to all if neither specified
        results = process_all_ready_packs(
            products_dir=args.products_dir,
            versions_dir=args.versions_dir,
            snapshot_date=args.date,
            dry_run=args.dry_run,
        )

    if args.json:
        sys.stdout.write(json.dumps(results, indent=2) + "\n")
    else:
        for r in results:
            sys.stdout.write(f"[{r['pack_name']}] v{r['version']}: {r['tenglish_summary']}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
