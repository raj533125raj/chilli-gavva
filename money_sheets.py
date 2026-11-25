"""Shared engine for the money-sheets family (CCA-1): debt payoff, couples budget, freelancer budget,
sinking funds. Each builder_<format>.py describes its tabs; this module writes the real files:

  <format>.xlsx  Start Here tab, input tabs, a Summary tab with real formulas (totals, progress %,
                 payoff order / months left) and a text progress bar (the stdlib xlsx writer has no charts)
  one CSV per tab, cover.png + 3 different preview PNGs (each shows a different real tab),
  README.txt (how to open in Excel / Google Sheets), LICENSE.txt, manifest.json

No amounts are typed into any cell: entry cells stay empty, every number comes from a formula.
Pillow is needed only for the images (guarded import; CI installs Pillow).
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:  # pragma: no cover - CI installs Pillow
    Image = ImageDraw = ImageFont = None
    HAS_PILLOW = False

from product_builder import Workbook, _col, S_HEADER, S_MONEY, S_DATE, S_WRAP, S_TITLE, S_BOLD, S_TOTAL_MONEY

TEXT, MONEY, DATE, NUMBER = "text", "money", "date", "number"
_STYLE = {TEXT: 0, MONEY: S_MONEY, DATE: S_DATE, NUMBER: 0}


def col(name: str, kind: str = TEXT, width: int = 16, formula=None, options=None) -> Dict[str, Any]:
    """One table column. formula(row) -> Excel formula text for a calculated (grey) column."""
    return {"name": name, "kind": kind, "width": width, "formula": formula, "options": list(options or [])}


def table_tab(name: str, columns: List[Dict[str, Any]], rows: int, note: str = "") -> Dict[str, Any]:
    """An entry tab: header row 1, empty white entry cells, grey formula columns, rows 2..rows+1."""
    return {"type": "table", "name": name, "columns": columns, "rows": rows, "note": note}


def form_tab(name: str, lines: List[tuple], widths=(34, 18, 18, 18, 18, 30)) -> Dict[str, Any]:
    """A label/value tab. lines: (label, value_kind, formula_or_None[, extra cells...]).

    value_kind "input" = empty white cell for the buyer, "head" = a bold section heading row,
    "row" = a table row: (label, "row", [(kind, formula), ...]).
    """
    return {"type": "form", "name": name, "lines": lines, "widths": list(widths)}


def last_row(tab: Dict[str, Any]) -> int:
    return tab["rows"] + 1


def rng(tab: Dict[str, Any], col_name: str, absolute: bool = True) -> str:
    """Whole data range of one column, e.g. Debts!$B$2:$B$21."""
    index = [c["name"] for c in tab["columns"]].index(col_name)
    letter = _col(index)
    sheet = tab["name"] if re.fullmatch(r"[A-Za-z0-9_]+", tab["name"]) else "'" + tab["name"] + "'"
    return f"{sheet}!${letter}$2:${letter}${last_row(tab)}"


def letter(tab: Dict[str, Any], col_name: str) -> str:
    return _col([c["name"] for c in tab["columns"]].index(col_name))


def progress_bar(cell: str, width: int = 20) -> str:
    """A text progress bar for a 0-100 percent cell (works in Excel and Google Sheets, no chart needed)."""
    filled = f"MAX(0,MIN({width},ROUND(N({cell})/100*{width},0)))"
    return f'=IF({cell}="","",REPT("█",{filled})&REPT("░",{width}-{filled}))'


def month_bounds(year: int, month: int) -> tuple:
    start = f"DATE({year},{month},1)"
    return start, f"EDATE({start},1)"


def sum_in_month(sum_range: str, date_range: str, year: int, month: int, extra: str = "") -> str:
    start, end = month_bounds(year, month)
    return f'SUMIFS({sum_range},{date_range},">="&{start},{date_range},"<"&{end}{extra})'


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


# ---- workbook --------------------------------------------------------------------------------
def _write_table(wb: Workbook, tab: Dict[str, Any]):
    sheet = wb.add_sheet(tab["name"], widths=[c["width"] for c in tab["columns"]], freeze=True)
    for i, c in enumerate(tab["columns"]):
        Workbook.put(sheet, 1, i, c["name"], S_HEADER)
    for r in range(2, last_row(tab) + 1):
        for i, c in enumerate(tab["columns"]):
            if c["formula"]:
                Workbook.put(sheet, r, i, None, _STYLE[c["kind"]], c["formula"](r))
    for i, c in enumerate(tab["columns"]):
        if c["options"] and not c["formula"]:
            sheet["validations"].append((f"{_col(i)}2:{_col(i)}{last_row(tab)}", c["options"]))
    rows = [[c["name"] for c in tab["columns"]]] + [["" for _ in tab["columns"]] for _ in range(tab["rows"])]
    return rows


def _write_form(wb: Workbook, tab: Dict[str, Any]):
    # Top row frozen on every tab (deep product QA: every data sheet keeps its header in view).
    sheet = wb.add_sheet(tab["name"], widths=tab["widths"], freeze=True)
    rows = []
    for r, line in enumerate(tab["lines"], 1):
        label, kind = line[0], line[1]
        csv_row = [label]
        if kind == "title":
            Workbook.put(sheet, r, 0, label, S_TITLE)
        elif kind == "head":
            for i, text in enumerate([label] + list(line[2] if len(line) > 2 else [])):
                Workbook.put(sheet, r, i, text, S_HEADER)
                if i:
                    csv_row.append(text)
        elif kind == "text":
            Workbook.put(sheet, r, 0, label, S_WRAP)
        elif kind == "input":
            Workbook.put(sheet, r, 0, label, S_BOLD)
            options = line[2] if len(line) > 2 else None
            if options:
                sheet["validations"].append((f"B{r}", options))
            csv_row.append("")
        elif kind == "row":
            Workbook.put(sheet, r, 0, label, S_BOLD if label else 0)
            for i, (vkind, formula) in enumerate(line[2], 1):
                if formula:
                    Workbook.put(sheet, r, i, None, S_TOTAL_MONEY if vkind == MONEY else _STYLE.get(vkind, 0),
                                 formula)
                csv_row.append("")
        elif kind != "blank":
            raise ValueError(f"unknown line kind {kind}")
        rows.append(csv_row)
    return rows


def _csv_name(tab_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", tab_name).strip("_") + ".csv"


# ---- images ----------------------------------------------------------------------------------
def _font(size: int, bold: bool = False):
    try:
        import mockups
        face = mockups.font("bold" if bold else "regular", size)
        if face is not None:
            return face
    except Exception:
        pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - old Pillow
        return ImageFont.load_default()


def _fit(draw, text: str, width: int, size: int, bold: bool = False):
    """Largest font (down to 11 px) that fits; a label is never cut."""
    while size > 11:
        face = _font(size, bold)
        if draw.textlength(text, font=face) <= width:
            return face
        size -= 1
    return _font(11, bold)


def _shorten(draw, text: str, width: int, face) -> str:
    if draw.textlength(text, font=face) <= width:
        return text
    words = text.split()
    while len(words) > 1 and draw.textlength(" ".join(words) + "…", font=face) > width:
        words.pop()
    return " ".join(words)


def _canvas(accent: str, band: str):
    w, h = 1200, 800
    im = Image.new("RGB", (w, h), "#f6f8f7")
    draw = ImageDraw.Draw(im)
    draw.rounded_rectangle([24, 24, w - 24, h - 24], radius=18, fill="#ffffff", outline=accent, width=5)
    draw.rectangle([24, 24, w - 24, 112], fill=accent)
    draw.text((56, 50), band, fill="#ffffff", font=_fit(draw, band, 1080, 26, True))
    return im, draw


def _cover(path: Path, design: Dict[str, Any]) -> None:
    accent = design["accent"]
    im, draw = _canvas(accent, design["band"])
    title_face = _fit(draw, design["title"], 1080, 44, True)
    draw.text((56, 150), design["title"], fill="#1f2d33", font=title_face)
    draw.text((56, 215), design["tagline"], fill=accent, font=_fit(draw, design["tagline"], 1080, 24))
    tabs = [t["name"] for t in design["tabs"]]
    per_row = 3
    box_w = (1088 - (per_row - 1) * 24) // per_row
    for i, name in enumerate(tabs[:9]):
        x = 56 + (i % per_row) * (box_w + 24)
        y = 290 + (i // per_row) * 130
        draw.rounded_rectangle([x, y, x + box_w, y + 104], radius=14, fill="#eef4f2", outline=accent, width=3)
        face = _fit(draw, name.upper(), box_w - 40, 22, True)
        draw.text((x + 20, y + 38), name.upper(), fill="#1f2d33", font=face)
    foot = f"Excel + Google Sheets · {len(tabs)} tabs · real formulas · undated, reuse every year"
    draw.text((56, 712), foot, fill=accent, font=_fit(draw, foot, 1080, 20, True))
    im.save(str(path), format="PNG", optimize=True)


def _table_preview(path: Path, design: Dict[str, Any], tab: Dict[str, Any], caption: str) -> None:
    accent = design["accent"]
    im, draw = _canvas(accent, f"{tab['name'].upper()} TAB · REAL WORKSHEET")
    cols = tab["columns"][:7]
    total = sum(c["width"] for c in cols)
    widths = [max(90, int(1088 * c["width"] / total)) for c in cols]
    widths[-1] += 1088 - sum(widths)
    x, top = 56, 150
    for c, w in zip(cols, widths):
        draw.rectangle([x, top, x + w, top + 64], fill="#e4efec", outline="#b7c9c6")
        face = _fit(draw, c["name"], w - 16, 17, True)
        label = _shorten(draw, c["name"], w - 16, face)
        draw.text((x + 8, top + 22), label, fill="#1f2d33", font=face)
        x += w
    for r in range(8):
        y = top + 64 + r * 54
        x = 56
        for c, w in zip(cols, widths):
            fill = "#eeeeee" if c["formula"] else "#ffffff"
            draw.rectangle([x, y, x + w, y + 54], fill=fill, outline="#c9d6d3")
            x += w
    draw.text((56, 640), caption, fill="#1f2d33", font=_fit(draw, caption, 1080, 20))
    key = "White cells: you type · Grey cells: formulas fill in by themselves"
    draw.text((56, 690), key, fill=accent, font=_fit(draw, key, 1080, 18, True))
    im.save(str(path), format="PNG", optimize=True)


def _form_preview(path: Path, design: Dict[str, Any], tab: Dict[str, Any], caption: str) -> None:
    accent = design["accent"]
    im, draw = _canvas(accent, f"{tab['name'].upper()} TAB · FORMULAS DO THE MATH")
    y = 140
    for line in tab["lines"]:
        label, kind = line[0], line[1]
        if kind in ("blank", "title", "text") or y > 600:
            continue
        if kind == "head":
            draw.rectangle([56, y, 1144, y + 40], fill=accent)
            draw.text((70, y + 9), label, fill="#ffffff", font=_fit(draw, label, 1040, 18, True))
            y += 48
            continue
        draw.rectangle([56, y, 1144, y + 40], fill="#ffffff", outline="#c9d6d3")
        face = _fit(draw, label, 560, 18, kind == "input")
        draw.text((70, y + 10), _shorten(draw, label, 560, face), fill="#1f2d33", font=face)
        is_bar = kind == "row" and any("REPT(" in (f or "") for _, f in line[2])
        if is_bar:
            draw.rounded_rectangle([650, y + 10, 1120, y + 30], radius=8, outline=accent, width=3)
        else:
            draw.rectangle([650, y + 4, 1130, y + 36], fill="#eeeeee" if kind == "row" else "#ffffff",
                           outline="#c9d6d3")
        y += 48
    draw.text((56, 690), caption, fill=accent, font=_fit(draw, caption, 1080, 20, True))
    im.save(str(path), format="PNG", optimize=True)


def _preview(path: Path, design: Dict[str, Any], spec: Dict[str, Any]) -> None:
    tab = next(t for t in design["tabs"] if t["name"] == spec["tab"])
    if tab["type"] == "table":
        _table_preview(path, design, tab, spec["caption"])
    else:
        _form_preview(path, design, tab, spec["caption"])


# ---- text files ------------------------------------------------------------------------------
def _readme(design: Dict[str, Any], csv_names: List[str]) -> str:
    tabs = [t["name"] for t in design["tabs"]]
    lines = [design["title"], "=" * min(72, max(20, len(design["title"]))), "", "WHAT YOU GET",
             f"- One Excel workbook ({design['format']}.xlsx) with {len(tabs)} tabs: " + ", ".join(tabs),
             f"- {design['format']}-csv-tabs.zip: a CSV copy of every tab (" + ", ".join(csv_names) + ")",
             "- Cover and three preview images", ""]
    lines += ["WHAT THE FORMULAS DO"] + [f"- {item}" for item in design["features"]] + [""]
    lines += ["HOW TO OPEN IN EXCEL",
              f"1. Double-click {design['format']}.xlsx (Excel 2019, Excel 2021 or Microsoft 365).",
              "2. Start on the Start Here tab and follow the steps in order.",
              "", "HOW TO OPEN IN GOOGLE SHEETS",
              "1. Go to Google Drive, choose New > File upload and upload the .xlsx file.",
              "2. Open it, then choose File > Save as Google Sheets so the formulas keep working.",
              "3. Unzip the csv-tabs ZIP for plain CSV copies of each tab if you only need one table.",
              "", "TIPS",
              "- Type in the white cells only. Grey cells hold formulas and fill in by themselves.",
              "- Dates: type them as real dates (for example 2027-01-15) so the monthly totals work.",
              "- Every amount cell starts empty: nothing is pre-filled, you add your own numbers.",
              "- This workbook organises your own numbers. It is not financial, tax or legal advice.",
              "", "LICENSE", "Personal use only. Do not resell, share or redistribute the files."]
    return "\n".join(lines) + "\n"


def _license(title: str) -> str:
    return "\n".join([f"LICENSE - {title}", "",
                      "This spreadsheet template is for personal use only.",
                      "You may make copies for your own records.",
                      "You may not resell, redistribute, share or claim the files as your own.", ""])


def normalize_title(spec: Any, default: str) -> str:
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dictionary")
    title = " ".join(str(spec.get("title") or spec.get("name") or default).split())
    return title[:90] or default


def build(design: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    """Write every file for one money-sheet design. Returns the sorted file names."""
    if not HAS_PILLOW:
        raise RuntimeError(f"Pillow is required for {design['format']} preview images")
    out_path = Path(out_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix=f"{design['format']}_build_"))
    try:
        wb = Workbook()
        csv_names = []
        for tab in design["tabs"]:
            rows = _write_table(wb, tab) if tab["type"] == "table" else _write_form(wb, tab)
            name = _csv_name(tab["name"])
            with (temp_dir / name).open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerows(rows)
            csv_names.append(name)
        workbook = f"{design['format']}.xlsx"
        wb.save(str(temp_dir / workbook))
        # The buyer download lists .xlsx/.zip files, so the CSV copies also travel as one listed ZIP.
        csv_zip = f"{design['format']}-csv-tabs.zip"
        with zipfile.ZipFile(temp_dir / csv_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in csv_names:
                archive.write(temp_dir / name, name)
        _cover(temp_dir / "cover.png", design)
        for i, spec in enumerate(design["previews"], 1):
            _preview(temp_dir / f"preview_{i}.png", design, spec)
        (temp_dir / "README.txt").write_text(_readme(design, csv_names), encoding="utf-8")
        (temp_dir / "LICENSE.txt").write_text(_license(design["title"]), encoding="utf-8")
        names = [workbook, csv_zip] + csv_names + ["cover.png", "preview_1.png", "preview_2.png", "preview_3.png",
                                          "README.txt", "LICENSE.txt"]
        manifest = {"format": design["format"], "title": design["title"],
                    "tabs": [t["name"] for t in design["tabs"]], "csv_tabs": csv_names,
                    "formulas": design["features"], "files": sorted(names + ["manifest.json"])}
        (temp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        names.append("manifest.json")
        out_path.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(temp_dir / name, out_path / name)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return sorted(names)


def start_here(title: str, steps: List[str], extra: Optional[List[str]] = None) -> Dict[str, Any]:
    lines = [(title, "title"), ("", "blank"), ("How to use this workbook", "head")]
    lines += [(f"{i}. {s}", "text") for i, s in enumerate(steps, 1)]
    lines += [("", "blank")] + [(e, "text") for e in (extra or [])]
    lines += [("", "blank"),
              ("White cells: you type. Grey cells: formulas, they fill in by themselves.", "text"),
              ("Nothing is pre-filled: every amount starts empty and is yours to add.", "text"),
              ("This workbook organises your own numbers. It is not financial, tax or legal advice.", "text")]
    return form_tab("Start Here", lines, widths=(100,))


def row_of(tab: Dict[str, Any], label: str) -> int:
    """Sheet row number of a form line (1-based), found by its label."""
    for r, line in enumerate(tab["lines"], 1):
        if line[0] == label:
            return r
    raise KeyError(label)


def form_ref(tab: Dict[str, Any], label: str, column: str = "B") -> str:
    sheet = tab["name"] if re.fullmatch(r"[A-Za-z0-9_]+", tab["name"]) else "'" + tab["name"] + "'"
    return f"{sheet}!${column}${row_of(tab, label)}"
