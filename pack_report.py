#!/usr/bin/env python3
"""Pack readiness table for the robot's hand-upload queue.

One line per pack folder (upload-pack output by upload_pack.py, staged by
launch_pack/mockups): id, file flags (product.zip, cover.png, previews,
pinterest.png, listing.txt), product.zip size in MB (1 decimal), the deep QA
verdict from product_qa_deep.check_folder (never writes into the pack; word
"skipped" when that module cannot be imported here or refuses to run), the
marketing kit status from exported_content/marketing/<pack>/ (both carousel/
and pins/ present, each with at least one file), and READY/NOT READY.

A pack is READY only when product.zip and listing.txt exist and the deep QA
verdict is "ready"; when the checker cannot run here the verdict shows as
"skipped" and the pack is not claimed ready. The owner sets the price
and publishes by hand, so there are no money amounts and no listing titles
in this report. Last line is "Ready: N of M"; a missing products folder
prints "Ready: 0 of 0".
"""
import argparse
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_PRODUCTS_DIR = REPO_ROOT / "exported_content" / "products" / "upload"
DEFAULT_MARKETING_DIR = REPO_ROOT / "exported_content" / "marketing"

REQUIRED_FILES = ("product.zip", "listing.txt")
REPORTED_FILES = ("product.zip", "cover.png", "preview-1.png", "preview-2.png",
                  "preview-3.png", "pinterest.png", "listing.txt")
MAX_ZIP_MB = 250

try:
    import product_qa_deep as _qa_deep
except Exception:  # CI installs only Pillow; QA may be absent here
    _qa_deep = None


def deep_qa_verdict(pack_dir):
    """Deep QA verdict from product_qa_deep, or "skipped" when unavailable."""
    if _qa_deep is None:
        return "skipped"
    try:
        return str(_qa_deep.check_folder(str(pack_dir)).get("verdict", "skipped"))
    except Exception:
        return "skipped"


def marketing_ready(pack_dir, marketing_root):
    """✓ when exported_content/marketing/<pack>/ has both carousel/ and pins/ with files."""
    kit = Path(marketing_root) / pack_dir.name
    return all(
        path.is_dir() and any(child.is_file() for child in path.iterdir())
        for path in (kit / "carousel", kit / "pins"))


def report_row(pack_dir, marketing_root):
    """One readiness row dict for a pack folder."""
    files = {name: (pack_dir / name).is_file() for name in REPORTED_FILES}
    size_mb = round((pack_dir / "product.zip").stat().st_size / (1024 * 1024), 1) \
        if files["product.zip"] else None
    qa = deep_qa_verdict(pack_dir)
    marketing = marketing_ready(pack_dir, marketing_root)
    zip_too_big = size_mb is not None and size_mb > MAX_ZIP_MB
    if not zip_too_big and files["product.zip"]:
        try:
            with zipfile.ZipFile(pack_dir / "product.zip") as z:
                uncompressed_mb = round(
                    sum(info.file_size for info in z.infolist()) / (1024 * 1024), 1)
                if uncompressed_mb > MAX_ZIP_MB:
                    zip_too_big = True
        except Exception:
            pass
    ready = files["product.zip"] and files["listing.txt"] and qa == "ready" and not zip_too_big
    return {
        "id": pack_dir.name,
        "files": files,
        "zip_size_mb": size_mb,
        "qa": qa,
        "marketing": marketing,
        "ready": ready,
        "reason": "zip too big" if zip_too_big else "",
    }


def collect(products_dir=None, marketing_dir=None):
    """Rows for every pack folder under products_dir (missing folder -> [])."""
    root = Path(products_dir) if products_dir else DEFAULT_PRODUCTS_DIR
    marketing = Path(marketing_dir) if marketing_dir else DEFAULT_MARKETING_DIR
    if not root.is_dir():
        return []
    return [report_row(pack, marketing)
            for pack in sorted(p for p in root.iterdir() if p.is_dir())]


def _mark(flag):
    return "✓" if flag else "✗"


def render_line(row):
    files = row["files"]
    size = f"{row['zip_size_mb']:.1f}MB" if row["zip_size_mb"] is not None else "-"
    parts = [f"zip {_mark(files['product.zip'])} {size if files['product.zip'] else '-'}"]
    for name in REPORTED_FILES:
        if name == "product.zip":
            continue
        label = name.rsplit(".", 1)[0]
        parts.append(f"{label} {_mark(files[name])}")
    parts.append(f"qa {row['qa']}")
    parts.append(f"marketing {_mark(row['marketing'])}")
    status = "READY" if row["ready"] else "NOT READY"
    line = f"{row['id']}  {' · '.join(parts)}  {status}"
    if row["reason"]:
        line += f"  ({row['reason']})"
    return line


def summary(rows):
    ready = sum(1 for row in rows if row["ready"])
    return f"Ready: {ready} of {len(rows)}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("products_dir", nargs="?", default=str(DEFAULT_PRODUCTS_DIR),
                        help="packs root (default exported_content/products/upload)")
    parser.add_argument("--marketing-dir", default=str(DEFAULT_MARKETING_DIR),
                        help="marketing root (default exported_content/marketing)")
    args = parser.parse_args(argv)
    rows = collect(args.products_dir, args.marketing_dir)
    for row in rows:
        print(render_line(row))
    print(summary(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
