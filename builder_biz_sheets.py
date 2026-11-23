"""Small-business order and profit workbook builder."""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Union

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None
    HAS_PILLOW = False

from product_builder import Workbook, _col

FORMAT = "biz_sheets"
KEYWORDS = [
    "order tracker spreadsheet",
    "inventory spreadsheet",
    "small business spreadsheet",
    "profit calculator sheet",
    "etsy shop tracker",
    "bakery order spreadsheet",
]

DEFAULT_TITLE = "Small Business Order & Profit Workbook"


def normalize_spec(spec: Any) -> Dict[str, Any]:
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dictionary")
    title = " ".join(str(spec.get("title") or DEFAULT_TITLE).split())
    business = " ".join(str(spec.get("business") or "My Small Business").split())
    return {"title": title or DEFAULT_TITLE, "business": business or "My Small Business"}


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
    except TypeError:
        return ImageFont.load_default()


def _image(path: Path, norm: Dict[str, Any], preview: int) -> None:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for spreadsheet preview images")
    w, h = 1200, 800
    im = Image.new("RGB", (w, h), "#f7faf9")
    draw = ImageDraw.Draw(im)
    accent = "#287d78"
    draw.rounded_rectangle([24, 24, w - 24, h - 24], radius=18, fill="#ffffff", outline=accent, width=5)

    if preview == 0:
        draw.rectangle([24, 24, w - 24, 118], fill=accent)
        draw.text((55, 48), "CHILLI GAVVA · SMALL BUSINESS WORKBOOK", fill="#ffffff", font=_font(22, True))
        draw.text((55, 145), norm["title"][:48], fill="#23343d", font=_font(38, True))
        draw.text((55, 198), norm["business"][:40], fill=accent, font=_font(22))
        tabs = ["START HERE", "ORDERS", "INVENTORY", "CUSTOMERS", "EXPENSES", "MONTHLY"]
        for i, tab in enumerate(tabs):
            x = 55 + (i % 3) * 375
            y = 300 + (i // 3) * 170
            draw.rounded_rectangle([x, y, x + 330, y + 120], radius=12, fill="#edf6f5", outline="#b9cbd0", width=3)
            draw.text((x + 24, y + 42), tab, fill="#23343d", font=_font(18, True))
        draw.text((55, 720), "One Excel workbook · six working tabs", fill=accent, font=_font(16, True))
    else:
        titles = {
            1: ("ORDERS TAB · REAL WORKSHEET", ["Order Date", "Customer", "Item", "Qty", "Status", "Sales", "Cost"]),
            2: ("INVENTORY TAB · REAL WORKSHEET", ["Item", "Opening", "Added", "Used", "Stock Left"]),
            3: ("MONTHLY SUMMARY · REAL WORKSHEET", ["Month", "Orders", "Sales", "Costs", "Profit"]),
        }
        title, headers = titles[preview]
        draw.rectangle([24, 24, w - 24, 100], fill=accent)
        draw.text((55, 48), title, fill="#ffffff", font=_font(24, True))
        left, top = 55, 150
        widths = [1090 // len(headers)] * len(headers)
        widths[-1] += 1090 - sum(widths)
        x = left
        for i, header in enumerate(headers):
            draw.rectangle([x, top, x + widths[i], top + 58], fill="#edf6f5", outline="#b9cbd0")
            draw.text((x + 10, top + 18), header, fill="#23343d", font=_font(14, True))
            x += widths[i]
        for r in range(8):
            y = top + 58 + r * 58
            x = left
            for width in widths:
                draw.rectangle([x, y, x + width, y + 58], outline="#b9cbd0")
                x += width
    im.save(str(path), format="PNG", optimize=True)


def _tab(name: str, headers: List[str], widths: List[int]) -> Dict[str, Any]:
    return {"name": name, "headers": headers, "widths": widths}


def _put_headers(wb: Workbook, tab: Dict[str, Any]) -> Any:
    sheet = wb.add_sheet(tab["name"], widths=tab["widths"], freeze=True)
    for col, header in enumerate(tab["headers"]):
        Workbook.put(sheet, 1, col, header, 1)
    return sheet


def _add_csv(path: Path, headers: List[str], rows: List[List[str]], name: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)


def _readme(norm: Dict[str, Any]) -> str:
    return "\n".join([
        norm["title"],
        "=" * 52,
        "",
        "WHAT YOU GET",
        "- One Excel workbook",
        "- Start Here, Orders, Inventory, Customers, Expenses, and Monthly Summary tabs",
        "- A CSV copy of every tab",
        "- Formula-based stock and monthly profit calculations",
        "- Cover and three tab preview images",
        "",
        "HOW TO OPEN",
        "1. Open the .xlsx file in Microsoft Excel or Google Sheets.",
        "2. In Google Sheets, choose File, Import, and upload the workbook.",
        "3. Type dates as real spreadsheet dates in the white entry cells.",
        "4. Keep formula cells grey and let the workbook calculate.",
        "5. Use the CSV files if you need one tab at a time.",
        "",
        "LICENSE",
        "Personal use only. Do not resell or redistribute the files.",
    ]) + "\n"


def _license(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"LICENSE - {norm['title']}",
        "",
        "This spreadsheet template is for personal use only.",
        "You may use copies for your own business records.",
        "You may not resell, redistribute, share, or claim the files as your own.",
        "",
    ])


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for biz_sheets previews")
    norm = normalize_spec(spec)
    out_path = Path(out_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="biz_sheets_build_"))
    names: List[str] = []
    try:
        wb = Workbook()

        start = _tab("Start Here", ["Small Business Workbook"], [40])
        sheet = _put_headers(wb, start)
        Workbook.put(sheet, 2, 0, "1. Enter orders in the Orders tab.", 0)
        Workbook.put(sheet, 3, 0, "2. Update stock in the Inventory tab.", 0)
        Workbook.put(sheet, 4, 0, "3. Keep customer details in the Customers tab.", 0)
        Workbook.put(sheet, 5, 0, "4. Record costs in the Expenses tab.", 0)
        Workbook.put(sheet, 6, 0, "5. Review totals in Monthly Summary.", 0)
        Workbook.put(sheet, 8, 0, "Grey formula cells update automatically.", 0)
        _add_csv(temp_dir / "Start_Here.csv", ["Small Business Workbook"], [
            ["1. Enter orders in the Orders tab."],
            ["2. Update stock in the Inventory tab."],
            ["3. Keep customer details in the Customers tab."],
            ["4. Record costs in the Expenses tab."],
            ["5. Review totals in Monthly Summary."],
        ], "Start Here")

        orders_headers = ["Order Date", "Customer", "Item", "Quantity", "Status", "Sales", "Cost"]
        orders = _tab("Orders", orders_headers, [16, 24, 24, 12, 16, 14, 14])
        sheet = _put_headers(wb, orders)
        for row in range(2, 102):
            for col in range(7):
                Workbook.put(sheet, row, col, "")
        _add_csv(temp_dir / "Orders.csv", orders_headers, [[""] * 7 for _ in range(100)], "Orders")

        inventory_headers = ["Item", "Opening Stock", "Added", "Used", "Stock Left"]
        inventory = _tab("Inventory", inventory_headers, [24, 16, 14, 14, 16])
        sheet = _put_headers(wb, inventory)
        for row in range(2, 102):
            for col in range(4):
                Workbook.put(sheet, row, col, "")
            Workbook.put(sheet, row, 4, None, 0,
                         f'=IF(A{row}="","",N(B{row})+N(C{row})-N(D{row}))')
        _add_csv(temp_dir / "Inventory.csv", inventory_headers, [[""] * 5 for _ in range(100)], "Inventory")

        customer_headers = ["Customer", "Email", "Phone", "First Order", "Notes"]
        customers = _tab("Customers", customer_headers, [24, 28, 18, 16, 30])
        sheet = _put_headers(wb, customers)
        for row in range(2, 102):
            for col in range(5):
                Workbook.put(sheet, row, col, "")
        _add_csv(temp_dir / "Customers.csv", customer_headers, [[""] * 5 for _ in range(100)], "Customers")

        expense_headers = ["Date", "Category", "Description", "Amount"]
        expenses = _tab("Expenses", expense_headers, [16, 22, 36, 14])
        sheet = _put_headers(wb, expenses)
        for row in range(2, 102):
            for col in range(4):
                Workbook.put(sheet, row, col, "")
        _add_csv(temp_dir / "Expenses.csv", expense_headers, [[""] * 4 for _ in range(100)], "Expenses")

        summary_headers = ["Month", "Orders", "Sales", "Costs", "Profit"]
        summary = _tab("Monthly Summary", summary_headers, [18, 14, 14, 14, 14])
        sheet = _put_headers(wb, summary)
        month_names = ["January", "February", "March", "April", "May", "June",
                       "July", "August", "September", "October", "November", "December"]
        for month_index, month_name in enumerate(month_names, 1):
            row = month_index + 1
            Workbook.put(sheet, row, 0, month_name, 0)
            Workbook.put(sheet, row, 1, None, 0,
                         f'=COUNTIFS(Orders!A$2:A$101,">="&DATE(2027,{month_index},1),Orders!A$2:A$101,"<"&EDATE(DATE(2027,{month_index},1),1))')
            Workbook.put(sheet, row, 2, None, 0,
                         f'=SUMIFS(Orders!F$2:F$101,Orders!A$2:A$101,">="&DATE(2027,{month_index},1),Orders!A$2:A$101,"<"&EDATE(DATE(2027,{month_index},1),1))')
            Workbook.put(sheet, row, 3, None, 0,
                         f'=SUMIFS(Orders!G$2:G$101,Orders!A$2:A$101,">="&DATE(2027,{month_index},1),Orders!A$2:A$101,"<"&EDATE(DATE(2027,{month_index},1),1))+SUMIFS(Expenses!D$2:D$101,Expenses!A$2:A$101,">="&DATE(2027,{month_index},1),Expenses!A$2:A$101,"<"&EDATE(DATE(2027,{month_index},1),1))')
            Workbook.put(sheet, row, 4, None, 0, f'=C{row}-D{row}')
        _add_csv(temp_dir / "Monthly_Summary.csv", summary_headers,
                 [[month_name, "", "", "", ""] for month_name in month_names], "Monthly Summary")

        workbook_name = "biz_sheets.xlsx"
        wb.save(str(temp_dir / workbook_name))
        names.extend([
            workbook_name,
            "Start_Here.csv", "Orders.csv", "Inventory.csv", "Customers.csv",
            "Expenses.csv", "Monthly_Summary.csv",
            "cover.png", "preview_1.png", "preview_2.png", "preview_3.png",
            "README.txt", "LICENSE.txt",
        ])
        _image(temp_dir / "cover.png", norm, 0)
        for index in range(1, 4):
            _image(temp_dir / f"preview_{index}.png", norm, index)
        (temp_dir / "README.txt").write_text(_readme(norm), encoding="utf-8")
        (temp_dir / "LICENSE.txt").write_text(_license(norm), encoding="utf-8")
        manifest = {
            "format": FORMAT,
            "title": norm["title"],
            "business": norm["business"],
            "tabs": ["Start Here", "Orders", "Inventory", "Customers", "Expenses", "Monthly Summary"],
            "csv_tabs": [
                "Start_Here.csv", "Orders.csv", "Inventory.csv",
                "Customers.csv", "Expenses.csv", "Monthly_Summary.csv",
            ],
            "files": sorted(names + ["manifest.json"]),
        }
        (temp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        names.append("manifest.json")

        out_path.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(temp_dir / name, out_path / name)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return sorted(names)
