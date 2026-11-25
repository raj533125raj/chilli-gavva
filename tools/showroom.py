"""tools/showroom.py — real pictures of every product the robot can make (CCD-1).

Builds ONE sample of every registered format (plus each ADHD planner edition) with the robot's real
builder path (product_builder.build_product) in a temporary products folder, then writes one contact
sheet PNG per sample: cover + 3 listing previews + the first 2 real product pages. An index.html shows
all sheets so the lead can show the owner real pictures.

Nothing is uploaded, posted or sold; no network. Output goes OUTSIDE the repo (default /private/tmp/showroom).

Usage:
  python3 tools/showroom.py                    # every format -> /private/tmp/showroom
  python3 tools/showroom.py --out DIR --only debt_payoff,adhd_planner
"""
from __future__ import annotations

import argparse
import copy
import html
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_OUT = Path("/private/tmp/showroom")
TILE_W, TILE_H, LABEL_H = 480, 340, 34
ADHD_EDITIONS = {"energy": "Energy-Based Daily Planner", "time_blindness": "Time-Blindness Routine Planner",
                 "focus": "Focus and Brain-Dump Sheets"}


def samples(only: Optional[List[str]] = None) -> List[Dict]:
    """(label, kind, spec) for every registered format; the ADHD planner once per edition."""
    from tools import sell_proof
    out = []
    for kind in sell_proof.FORMAT_ORDER:
        if only and kind not in only:
            continue
        if kind == "adhd_planner":
            for edition, title in ADHD_EDITIONS.items():
                spec = copy.deepcopy(sell_proof.spec_for(kind))
                spec.update(edition=edition, name=title, title=title, short_name=edition.replace("_", "-"))
                spec["listing"]["title"] = title + " Printable"
                out.append({"label": f"adhd_planner-{edition}", "kind": kind, "spec": spec})
        else:
            out.append({"label": kind, "kind": kind, "spec": sell_proof.spec_for(kind)})
    return out


def first_pages(folder: Path, count: int = 2):
    """The first real product pages as pictures (PDF pages, sheets, documents)."""
    import mockups
    import product_builder
    import truth_check
    info = mockups.product_info(str(folder))
    pages = info["pages"]
    if not pages:
        pages, _ = product_builder._real_picture_pages(str(folder), truth_check.image_facts(str(folder)))
    return [(p["title"], p["picture"](TILE_H * 2)) for p in pages[:count]]


def _tile(image, label):
    from PIL import Image, ImageDraw
    import mockups
    tile = Image.new("RGB", (TILE_W, TILE_H + LABEL_H), "#ffffff")
    pic = image.convert("RGB")
    pic.thumbnail((TILE_W - 16, TILE_H - 16))
    tile.paste(pic, ((TILE_W - pic.width) // 2, (TILE_H - pic.height) // 2))
    draw = ImageDraw.Draw(tile)
    draw.rectangle([0, TILE_H, TILE_W, TILE_H + LABEL_H], fill="#f0f3f2")
    draw.text((10, TILE_H + 8), label[:60], fill="#1f2a30", font=mockups.font("bold", 16))
    return tile


def contact_sheet(folder: Path, label: str, target: Path) -> Dict:
    """cover + 3 previews + first 2 pages in a 3 x 2 grid. Returns what went in."""
    from PIL import Image, ImageDraw
    import mockups
    tiles, names = [], []
    for name in ("cover.png", "preview-1.png", "preview-2.png", "preview-3.png"):
        path = folder / name
        if not path.is_file():
            alt = folder / name.replace("-", "_")
            path = alt if alt.is_file() else path
        if path.is_file():
            with Image.open(path) as im:
                tiles.append(_tile(im, name))
            names.append(name)
    for title, picture in first_pages(folder):
        tiles.append(_tile(picture, f"Page: {title}"))
        names.append(f"page: {title}")
    sheet = Image.new("RGB", (3 * TILE_W + 40, 70 + 2 * (TILE_H + LABEL_H) + 30), "#dde4e2")
    draw = ImageDraw.Draw(sheet)
    draw.text((20, 22), label, fill="#1f2a30", font=mockups.font("bold", 26))
    for i, tile in enumerate(tiles[:6]):
        sheet.paste(tile, (10 + (i % 3) * (TILE_W + 10), 70 + (i // 3) * (TILE_H + LABEL_H + 10)))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, "PNG", optimize=True)
    return {"label": label, "sheet": target.name, "tiles": names}


def build(out: Path = DEFAULT_OUT, only: Optional[List[str]] = None) -> List[Dict]:
    import product_builder as pb
    out = Path(out).resolve()
    if out == ROOT or ROOT in out.parents:
        raise ValueError("Showroom output must be outside the repository")
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(prefix="showroom_") as temp:
        products = Path(temp) / "products"
        for index, sample in enumerate(samples(only), 1):
            row = {"label": sample["label"], "kind": sample["kind"]}
            try:
                name = pb.build_product(sample["spec"], index, out_dir=str(products))
                row.update(contact_sheet(products / name, sample["label"], out / f"{sample['label']}.png"))
                pictures = sum(not t.startswith("page:") for t in row["tiles"])
                row["pages_shown"] = len(row["tiles"]) - pictures
                row["ok"] = pictures == 4 and row["pages_shown"] >= 1
            except Exception as error:   # one broken format must not hide the others
                row.update(ok=False, error=f"{type(error).__name__}: {error}")
            rows.append(row)
            print(f"{row['label']:32} {'ok' if row['ok'] else 'MISSING ' + str(row.get('error') or row.get('tiles'))}",
                  flush=True)
    (out / "index.html").write_text(index_html(rows), encoding="utf-8")
    (out / "showroom.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return rows


def index_html(rows: List[Dict]) -> str:
    parts = ["<!doctype html><html><head><meta charset='utf-8'><title>Chilli Gavva Showroom</title>",
             "<meta name='viewport' content='width=device-width, initial-scale=1'>",
             "<style>body{font-family:sans-serif;margin:16px;background:#f4f6f5;color:#1f2a30}"
             "img{max-width:100%;border:1px solid #c7d0d3}section{margin-bottom:28px}</style></head><body>",
             f"<h1>Showroom: {sum(r.get('ok', False) for r in rows)}/{len(rows)} products with real pictures</h1>"]
    for row in rows:
        parts.append(f"<section><h2>{html.escape(row['label'])}</h2>")
        if row.get("sheet"):
            parts.append(f"<img src='{html.escape(row['sheet'])}' alt='{html.escape(row['label'])}'>")
        if row.get("error"):
            parts.append(f"<p>Build problem: {html.escape(row['error'])}</p>")
        parts.append("</section>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Contact sheets of every product format (real builds).")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--only", help="comma-separated formats")
    args = parser.parse_args(argv)
    os.environ.setdefault("CHILLI_TEST", "1")
    rows = build(Path(args.out), [k.strip() for k in args.only.split(",")] if args.only else None)
    print(f"{sum(r.get('ok', False) for r in rows)}/{len(rows)} contact sheets -> {Path(args.out) / 'index.html'}")
    return 0 if all(r.get("ok") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
