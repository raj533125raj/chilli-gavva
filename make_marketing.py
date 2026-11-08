#!/usr/bin/env python3
"""One command for all marketing files of ready upload packs.

For every READY pack (product.zip + listing.txt present and the deep QA
verdict is "ready") this runs the two maker modules from the repo —
carousel_maker.make_pack_carousel and pin_variants.generate_pins_for_pack —
then records what was made in exported_content/marketing/<pack>/README.txt
(file names only) and prints a summary table. Missing modules or one failing
pack are reported while the rest continue; the pack folders are never
touched, so reruns are idempotent. No money amounts are written anywhere.
"""
import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_PACKS_DIR = REPO_ROOT / "exported_content" / "products" / "upload"
DEFAULT_MARKETING_DIR = REPO_ROOT / "exported_content" / "marketing"

MONEY_RE = re.compile(r"\$\s?\d[\d.,]*")

try:
    import product_qa_deep as _qa_deep
except Exception:
    _qa_deep = None


def qa_verdict(pack_dir):
    """Deep QA verdict for a pack folder, or "skipped" when unavailable."""
    if _qa_deep is None:
        return "skipped"
    try:
        return str(_qa_deep.check_folder(str(pack_dir)).get("verdict", "skipped"))
    except Exception:
        return "skipped"


def report_row(pack_dir, _marketing_dir=None):
    """Readiness dict for one pack folder (same rule as the O8 report)."""
    pack_dir = Path(pack_dir)
    files = {name: (pack_dir / name).is_file()
             for name in ("product.zip", "listing.txt")}
    verdict = qa_verdict(pack_dir)
    return {"id": pack_dir.name, "files": files, "qa": verdict,
            "ready": files["product.zip"] and files["listing.txt"]
            and verdict == "ready"}


def select_ready_packs(packs_dir, marketing_dir=None):
    """Ready pack folders under packs_dir, sorted by id."""
    root = Path(packs_dir)
    if not root.is_dir():
        return []
    return [pack for pack in sorted(p for p in root.iterdir() if p.is_dir())
            if report_row(pack)["ready"]]


def load_makers():
    """Import both maker modules; each stays None when unavailable."""
    carousel = pins = None
    try:
        import carousel_maker as carousel
    except Exception:
        carousel = None
    try:
        import pin_variants as pins
    except Exception:
        pins = None
    return carousel, pins


def made_files(pack_marketing_dir):
    """Relative file names written for this pack (inside marketing only)."""
    pack_marketing_dir = Path(pack_marketing_dir)
    if not pack_marketing_dir.is_dir():
        return []
    return sorted(path.relative_to(pack_marketing_dir).as_posix()
                  for path in pack_marketing_dir.rglob("*") if path.is_file()
                  and path.name != "README.txt")


def process_pack(pack_dir, marketing_dir, makers):
    """Run both makers for one pack; collects results without raising."""
    carousel_maker, pin_maker = makers
    marketing_dir = Path(marketing_dir)
    result = {"id": Path(pack_dir).name, "carousel": None, "pins": None, "errors": []}
    if carousel_maker is not None:
        try:
            result["carousel"] = carousel_maker.make_pack_carousel(
                Path(pack_dir), marketing_dir=marketing_dir)
        except Exception as error:
            result["errors"].append(f"carousel: {MONEY_RE.sub('[amount]', str(error))}")
    else:
        result["errors"].append("carousel: module missing")
    if pin_maker is not None:
        try:
            result["pins"] = pin_maker.generate_pins_for_pack(
                Path(pack_dir), out_dir=marketing_dir.parent)
        except Exception as error:
            result["errors"].append(f"pins: {MONEY_RE.sub('[amount]', str(error))}")
    else:
        result["errors"].append("pins: module missing")
    result["files"] = made_files(marketing_dir / result["id"])
    result["ok"] = not result["errors"]
    return result


def write_readme(marketing_dir, pack_id, result):
    """README.txt at exported_content/marketing/<pack>/ — file names only."""
    lines = [f"Marketing files made for {pack_id} (by make_marketing.py):", ""]
    for kind in ("carousel", "pins"):
        status = "ok" if result[kind] is not None else \
            next((error for error in result["errors"] if error.startswith(kind)),
                 "not made")
        lines.append(f"{kind}: {status}")
        lines.extend(f"  {name}" for name in result["files"]
                     if name.startswith(kind + "/"))
    if result["errors"]:
        lines.append("")
        lines.extend(result["errors"])
    lines.append("")
    lines.append("Pack folder untouched. The owner reviews these files and posts by hand.")
    out_dir = Path(marketing_dir) / pack_id
    out_dir.mkdir(parents=True, exist_ok=True)
    readme = out_dir / "README.txt"
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return readme


def run(pack_id=None, all_packs=False, packs_dir=None, marketing_dir=None,
        makers=None):
    """Drive the makers; returns (results, readmes, skipped notes)."""
    packs_dir = Path(packs_dir) if packs_dir else DEFAULT_PACKS_DIR
    marketing_dir = Path(marketing_dir) if marketing_dir else DEFAULT_MARKETING_DIR
    if makers is None:
        makers = load_makers()
    if pack_id:
        candidates = [packs_dir / pack_id]
    elif all_packs:
        candidates = sorted(p for p in packs_dir.iterdir() if p.is_dir()) \
            if packs_dir.is_dir() else []
    else:
        raise SystemExit("choose --pack ID or --all")
    results, readmes, skipped = [], [], []
    for pack in candidates:
        row = report_row(pack)
        if not pack.is_dir():
            skipped.append(f"{pack.name}: pack folder not found at {pack}")
            continue
        if not row["ready"]:
            missing = ", ".join(name for name, flag in row["files"].items() if not flag)
            skipped.append(f"{pack.name}: not ready ({missing}; qa {row['qa']})")
            continue
        result = process_pack(pack, marketing_dir, makers)
        readmes.append(write_readme(marketing_dir, result["id"], result))
        results.append(result)
    return results, readmes, skipped


def render_table(results, skipped):
    lines = []
    for result in results:
        lines.append(f"{result['id']}: carousel "
                     f"{'ok' if result['carousel'] else 'FAILED'} · pins "
                     f"{'ok' if result['pins'] else 'FAILED'} · "
                     f"{len(result['files'])} files · {result['id']}/README.txt")
    lines.extend("skip " + note for note in skipped)
    ok = sum(1 for result in results if result["ok"])
    errors = sum(len(result["errors"]) for result in results)
    lines.append(f"Marketing: {ok} of {len(results)} packs fully made, "
                 f"{len(skipped)} skipped, {errors} errors")
    return lines


def main(argv=None, makers=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pack", help="one pack id under exported_content/products/upload")
    parser.add_argument("--all", action="store_true", help="every ready pack")
    parser.add_argument("--packs-dir", default=str(DEFAULT_PACKS_DIR))
    parser.add_argument("--marketing-dir", default=str(DEFAULT_MARKETING_DIR))
    args = parser.parse_args(argv)
    results, _readmes, skipped = run(pack_id=args.pack, all_packs=args.all,
                                     packs_dir=args.packs_dir,
                                     marketing_dir=args.marketing_dir,
                                     makers=makers)
    for line in render_table(results, skipped):
        print(line)
    return 1 if (skipped or any(not result["ok"] for result in results)) else 0


if __name__ == "__main__":
    sys.exit(main())
