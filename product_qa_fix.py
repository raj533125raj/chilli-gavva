"""product_qa_fix.py — Auto-repair simple product problems found by deep QA.

BINNY-KI ROBOT: Auto-repairs simple problems reported by product_qa_deep.check_folder:
- Image too small (cover/preview short side < 720): upscale with Pillow LANCZOS
  to 1280x720 keeping aspect (padded with the image's own edge colour).
- Pin wrong size: fit into 1000x1500 (padded, no crop of text).
- ZIP with __MACOSX / .DS_Store / absolute paths: rebuild the zip without them.
- Placeholder words ("lorem ipsum", "TODO", "None", "null", "undefined", "[object"):
  only in listing/text files (.txt/.md) — remove the word; in PDF/XLSX/DOCX do NOT edit,
  mark not_fixed.
- Empty sheet named like Sheet1/Sheet2 with no content: remove that sheet with
  openpyxl (only if the workbook has other sheets).
- Frozen header missing on a data sheet: freeze row 1.

Every change writes a temp file then replaces (atomic).
Keep a backup of each changed file outside the pack folder (never inside the pack).
Unknown problems -> not_fixed.
Never touch the meaning/content of the product.
"""

from collections import Counter
import os
import re
import shutil
import tempfile
from typing import Any, Dict, List, Optional
import zipfile

PLACEHOLDER_WORDS = [
    r"lorem\s+ipsum",
    r"\bTODO\b",
    r"\bNone\b",
    r"\bnull\b",
    r"\bundefined\b",
    r"\[object"
]
PLACEHOLDER_RE = re.compile("|".join(PLACEHOLDER_WORDS), re.IGNORECASE)

EMPTY_SHEET_PATTERN = re.compile(r"^sheet\s*\d+$", re.IGNORECASE)


def _ensure_backup(filepath: str, backup_dir: str) -> str:
    """Copy filepath to backup_dir preserving filename, outside the pack folder."""
    os.makedirs(backup_dir, exist_ok=True)
    filename = os.path.basename(filepath)
    dest = os.path.join(backup_dir, filename)
    counter = 1
    base, ext = os.path.splitext(filename)
    while os.path.exists(dest):
        dest = os.path.join(backup_dir, f"{base}_{counter}{ext}")
        counter += 1
    shutil.copy2(filepath, dest)
    return dest


def _atomic_replace(temp_file: str, target_file: str):
    """Atomically replace target_file with temp_file."""
    os.replace(temp_file, target_file)


def _get_edge_color(im):
    """Sample the border pixels of an image to find the dominant edge color."""
    w, h = im.size
    pixels = []
    # Sample top & bottom borders
    for x in range(w):
        pixels.append(im.getpixel((x, 0)))
        pixels.append(im.getpixel((x, h - 1)))
    # Sample left & right borders
    for y in range(h):
        pixels.append(im.getpixel((0, y)))
        pixels.append(im.getpixel((w - 1, y)))

    if not pixels:
        return (255, 255, 255, 255) if im.mode in ("RGBA", "LA") else (255, 255, 255)

    most_common = Counter(pixels).most_common(1)[0][0]
    return most_common


def _fix_image_too_small(filepath: str, backup_dir: str) -> bool:
    """Upscale cover/preview short side < 720 to 1280x720 keeping aspect ratio and padding with edge color."""
    try:
        from PIL import Image
    except ImportError:
        return False

    target_w, target_h = 1280, 720
    try:
        with Image.open(filepath) as im:
            orig_mode = im.mode
            orig_w, orig_h = im.size

            # Check if short side < 720
            if min(orig_w, orig_h) >= 720:
                return False

            edge_col = _get_edge_color(im)

            # Fit into 1280x720 keeping aspect ratio
            ratio = min(target_w / orig_w, target_h / orig_h)
            new_w = max(1, round(orig_w * ratio))
            new_h = max(1, round(orig_h * ratio))

            resample_filter = getattr(Image, "Resampling", Image).LANCZOS
            resized = im.resize((new_w, new_h), resample=resample_filter)

            # Create new canvas padded with edge colour
            canvas = Image.new(orig_mode, (target_w, target_h), edge_col)
            paste_x = (target_w - new_w) // 2
            paste_y = (target_h - new_h) // 2

            if orig_mode in ("RGBA", "LA"):
                canvas.paste(resized, (paste_x, paste_y), resized)
            else:
                canvas.paste(resized, (paste_x, paste_y))

            _ensure_backup(filepath, backup_dir)

            dirname = os.path.dirname(filepath)
            with tempfile.NamedTemporaryFile(dir=dirname, suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name

            canvas.save(tmp_path, format="PNG")
            _atomic_replace(tmp_path, filepath)
            return True
    except Exception:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


def _fix_pin_wrong_size(filepath: str, backup_dir: str) -> bool:
    """Fit pin into 1000x1500 (pad with edge color, no crop of text)."""
    try:
        from PIL import Image
    except ImportError:
        return False

    target_w, target_h = 1000, 1500
    try:
        with Image.open(filepath) as im:
            orig_mode = im.mode
            orig_w, orig_h = im.size
            if (orig_w, orig_h) == (target_w, target_h):
                return False

            edge_col = _get_edge_color(im)

            # Fit into 1000x1500 keeping aspect ratio
            ratio = min(target_w / orig_w, target_h / orig_h)
            new_w = max(1, round(orig_w * ratio))
            new_h = max(1, round(orig_h * ratio))

            resample_filter = getattr(Image, "Resampling", Image).LANCZOS
            resized = im.resize((new_w, new_h), resample=resample_filter)

            canvas = Image.new(orig_mode, (target_w, target_h), edge_col)
            paste_x = (target_w - new_w) // 2
            paste_y = (target_h - new_h) // 2

            if orig_mode in ("RGBA", "LA"):
                canvas.paste(resized, (paste_x, paste_y), resized)
            else:
                canvas.paste(resized, (paste_x, paste_y))

            _ensure_backup(filepath, backup_dir)

            dirname = os.path.dirname(filepath)
            with tempfile.NamedTemporaryFile(dir=dirname, suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name

            canvas.save(tmp_path, format="PNG")
            _atomic_replace(tmp_path, filepath)
            return True
    except Exception:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


def _fix_zip_contents(filepath: str, backup_dir: str) -> bool:
    """Rebuild zip without __MACOSX, .DS_Store, or absolute paths."""
    try:
        with zipfile.ZipFile(filepath, "r") as z:
            infos = z.infolist()
            if not infos:
                return False

            entries_to_keep = []
            modified = False

            for info in infos:
                arcname = info.filename
                # Check for __MACOSX or .DS_Store
                if "__MACOSX" in arcname or os.path.basename(arcname) == ".DS_Store":
                    modified = True
                    continue

                clean_name = arcname
                # Check for absolute path
                if clean_name.startswith("/") or clean_name.startswith("\\") or (len(clean_name) > 1 and clean_name[1] == ":"):
                    modified = True
                    clean_name = clean_name.lstrip("/\\")
                    if len(clean_name) > 1 and clean_name[1] == ":":
                        clean_name = clean_name[2:].lstrip("/\\")

                if clean_name != arcname:
                    modified = True

                if clean_name:
                    entries_to_keep.append((info, clean_name))

            if not modified:
                return False

            _ensure_backup(filepath, backup_dir)

            dirname = os.path.dirname(filepath)
            with tempfile.NamedTemporaryFile(dir=dirname, suffix=".zip", delete=False) as tmp:
                tmp_path = tmp.name

            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as new_z:
                for info, clean_name in entries_to_keep:
                    content = z.read(info)
                    new_info = zipfile.ZipInfo(clean_name, date_time=info.date_time)
                    new_info.compress_type = info.compress_type
                    new_info.external_attr = info.external_attr
                    new_z.writestr(new_info, content)

            _atomic_replace(tmp_path, filepath)
            return True
    except Exception:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


def _fix_text_placeholder(filepath: str, backup_dir: str) -> bool:
    """Remove placeholder words from .txt or .md files."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if not PLACEHOLDER_RE.search(content):
            return False

        # Remove the placeholder word matches
        new_content = PLACEHOLDER_RE.sub("", content)

        _ensure_backup(filepath, backup_dir)

        dirname = os.path.dirname(filepath)
        with tempfile.NamedTemporaryFile(dir=dirname, suffix=".txt", mode="w", encoding="utf-8", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(new_content)

        _atomic_replace(tmp_path, filepath)
        return True
    except Exception:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


def _fix_xlsx(filepath: str, problem_kind: str, page_name: Any, backup_dir: str) -> bool:
    """Remove empty sheet named Sheet1/Sheet2... or freeze row 1 on data sheet using openpyxl."""
    try:
        import openpyxl
    except ImportError:
        return False

    try:
        wb = openpyxl.load_workbook(filepath, data_only=False)
        modified = False

        if problem_kind == "empty_sheet":
            sheet_str = str(page_name or "").strip()
            # "empty sheet named like Sheet1/Sheet2 with no content: remove that sheet with openpyxl (only if workbook has other sheets)"
            if EMPTY_SHEET_PATTERN.match(sheet_str) and sheet_str in wb.sheetnames:
                if len(wb.sheetnames) > 1:
                    ws = wb[sheet_str]
                    wb.remove(ws)
                    modified = True

        elif problem_kind == "missing_frozen_header":
            sheet_str = str(page_name or "").strip()
            if sheet_str in wb.sheetnames:
                ws = wb[sheet_str]
                if "instruction" not in ws.title.lower() and ws.max_row > 1 and not ws.freeze_panes:
                    ws.freeze_panes = "A2"
                    modified = True
            elif not sheet_str:
                # If sheet name not specified, freeze row 1 on all data sheets
                for ws in wb.worksheets:
                    if "instruction" not in ws.title.lower() and ws.max_row > 1 and not ws.freeze_panes:
                        ws.freeze_panes = "A2"
                        modified = True

        if not modified:
            return False

        _ensure_backup(filepath, backup_dir)

        dirname = os.path.dirname(filepath)
        with tempfile.NamedTemporaryFile(dir=dirname, suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name

        wb.save(tmp_path)
        _atomic_replace(tmp_path, filepath)
        return True
    except Exception:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


def fix_folder(folder: str, problems: List[Dict[str, Any]], backup_dir: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Auto-repair simple product problems found by deep QA.

    Returns: {"fixed": [...], "not_fixed": [...]}
    Never touches the meaning/content of the product.
    Backups are kept outside the pack folder.
    """
    fixed: List[Dict[str, Any]] = []
    not_fixed: List[Dict[str, Any]] = []

    if not os.path.isdir(folder):
        return {"fixed": fixed, "not_fixed": list(problems)}

    # Ensure backup directory is strictly outside folder
    if not backup_dir:
        # Default backup directory: parent directory of folder or tempdir
        parent_dir = os.path.dirname(os.path.abspath(folder))
        folder_base = os.path.basename(os.path.abspath(folder))
        backup_dir = os.path.join(parent_dir, f".backups_{folder_base}")
    else:
        backup_dir = os.path.abspath(backup_dir)

    # Safety check: backup_dir must never be inside folder
    folder_abs = os.path.abspath(folder)
    if os.path.commonpath([backup_dir, folder_abs]) == folder_abs:
        backup_dir = os.path.join(tempfile.gettempdir(), f"product_backups_{os.path.basename(folder_abs)}")

    for prob in problems:
        kind = prob.get("kind")
        rel_file = prob.get("file")
        if not rel_file:
            not_fixed.append(prob)
            continue

        target_path = os.path.join(folder, rel_file)
        if not os.path.exists(target_path):
            not_fixed.append(prob)
            continue

        lower_name = os.path.basename(target_path).lower()
        _, ext = os.path.splitext(lower_name)

        success = False
        try:
            if kind == "image_resolution_too_low" and ("cover" in lower_name or "preview" in lower_name):
                success = _fix_image_too_small(target_path, backup_dir)
            elif kind == "invalid_pin_dimensions" and "pin" in lower_name:
                success = _fix_pin_wrong_size(target_path, backup_dir)
            elif kind in ("zip_macosx_metadata", "zip_ds_store", "zip_absolute_path") and ext == ".zip":
                success = _fix_zip_contents(target_path, backup_dir)
            elif kind == "placeholder_text":
                if ext in (".txt", ".md"):
                    success = _fix_text_placeholder(target_path, backup_dir)
                else:
                    # In PDF/XLSX/DOCX do NOT edit, mark not_fixed
                    success = False
            elif kind == "empty_sheet" and ext == ".xlsx":
                page_name = prob.get("page")
                success = _fix_xlsx(target_path, kind, page_name, backup_dir)
            elif kind == "missing_frozen_header" and ext == ".xlsx":
                page_name = prob.get("page")
                success = _fix_xlsx(target_path, kind, page_name, backup_dir)
            else:
                success = False
        except Exception:
            success = False

        if success:
            fixed.append(prob)
        else:
            not_fixed.append(prob)

    return {"fixed": fixed, "not_fixed": not_fixed}
