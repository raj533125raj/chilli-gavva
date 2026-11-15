"""Read-only checks for whether a built product pack is ready for Gumroad."""
from __future__ import annotations

import re
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, List


THUMBNAIL_NAME = "01-thumbnail-600x600.jpg"
COVER_NAME = "02-cover-1280x720.jpg"


def _result(name: str, ok: bool, fix_te: str) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "fix_te": fix_te}


def _sections(text: str) -> Dict[str, str]:
    fields = {}
    for key in ("Title", "Description", "Tags"):
        match = re.search(
            rf"^{re.escape(key)}:[ \t]*(.*?)(?=^[A-Za-z][A-Za-z /_-]*:[ \t]*|\Z)",
            text, re.MULTILINE | re.DOTALL)
        fields[key] = match.group(1).strip() if match else ""
    return fields


def _buyer_files(pack: Path) -> List[Dict[str, Any]]:
    import store_kits

    return store_kits.gumroad_pack_files(pack)


def _image_dimensions(path: Path):
    """Return decoded dimensions, or None when Pillow is unavailable."""
    try:
        from PIL import Image
    except ImportError:
        return None

    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        return image.size


def check(pack_dir: Path | str) -> List[Dict[str, Any]]:
    """Return pass/fail checks for a pack without changing files or external state."""
    import gumroad_images
    import store_kits
    import truth_check

    pack = Path(pack_dir)
    checks: List[Dict[str, Any]] = []
    image_dir = pack / "gumroad-images"

    thumbnail = image_dir / THUMBNAIL_NAME
    cover = image_dir / COVER_NAME
    checks.append(_result("Thumbnail image exists", thumbnail.is_file(),
                          "Gumroad-images folder lo thumbnail image add cheyandi."))
    checks.append(_result("Cover image exists", cover.is_file(),
                          "Gumroad-images folder lo cover image add cheyandi."))
    if thumbnail.is_file():
        try:
            dimensions = _image_dimensions(thumbnail)
        except (OSError, ValueError):
            checks.append(_result("Thumbnail image is readable", False,
                                  "Thumbnail image ni malli export cheyandi."))
        else:
            if dimensions is not None:
                checks.append(_result("Thumbnail image size", dimensions == gumroad_images.THUMBNAIL_SIZE,
                                      "Thumbnail ni Gumroad size lo malli export cheyandi."))
    if cover.is_file():
        try:
            dimensions = _image_dimensions(cover)
        except (OSError, ValueError):
            checks.append(_result("Cover image is readable", False,
                                  "Cover image ni malli export cheyandi."))
        else:
            if dimensions is not None:
                checks.append(_result("Cover image size", dimensions == gumroad_images.COVER_SIZE,
                                      "Cover ni Gumroad size lo malli export cheyandi."))

    archive = pack / "product.zip"
    archive_ok = archive.is_file()
    archive_fix = "product.zip add cheyandi."
    if archive_ok:
        try:
            with zipfile.ZipFile(archive) as zipped:
                archive_ok = zipped.testzip() is None
        except (OSError, zipfile.BadZipFile, zlib.error):
            archive_ok = False
            archive_fix = "product.zip ni malli valid ZIP ga build cheyandi."
    checks.append(_result("Product ZIP opens", archive_ok, archive_fix))

    files = _buyer_files(pack)
    checks.append(_result("Buyer files exist", bool(files),
                          "Product ZIP lo leka pack lo buyer files add cheyandi."))
    checks.append(_result("Buyer files are not empty",
                          bool(files) and all(int(item.get("size", 0)) > 0 for item in files),
                          "Khali buyer files ni real content tho replace cheyandi."))

    listing_files = [pack / "listing.txt", pack / "listing.md"]
    listing_contents = [(path, path.read_text(encoding="utf-8-sig"))
                        for path in listing_files if path.is_file()]
    listing_text = "\n".join(text for _, text in listing_contents)
    money_hit = store_kits.check_money(listing_text)
    checks.append(_result("Listing has no money amount", money_hit is None,
                          "Listing nunchi money amount remove chesi owner decide ani pettandi."))
    field_text = next((text for path, text in listing_contents if path.name == "listing.txt"),
                      listing_contents[0][1] if listing_contents else "")
    fields = _sections(field_text)
    title = fields.get("Title", "").strip()
    title_limit = int(store_kits.STORE_LIMITS["gumroad"]["title_limit"])
    checks.append(_result("Listing title length", bool(title) and
                          store_kits.count_graphemes(title) <= title_limit,
                          "Title ni Gumroad limit lopala short cheyandi."))
    tags = [tag.strip() for tag in re.split(r"[,;\n]+", fields.get("Tags", "")) if tag.strip()]
    checks.append(_result("Listing has at most 13 tags", len(tags) <= 13,
                          "Tags ni 13 leka takkuva ki tagginchandi."))
    checks.append(_result("Listing description is not empty",
                          bool(fields.get("Description", "").strip()),
                          "Listing description add cheyandi."))
    license_path = pack / "LICENSE.txt"
    checks.append(_result("LICENSE is present", license_path.is_file() and
                          bool(license_path.read_text(encoding="utf-8-sig").strip())
                          if license_path.is_file() else False,
                          "Pack lo LICENSE.txt add chesi content rayandi."))

    truth_ready = False
    if archive_ok:
        truth_ready = truth_check.check(pack, with_images=False).get("verdict") == "ready"
    checks.append(_result("Truth check is ready", truth_ready,
                          "Truth check lo chupinchina problems fix chesi malli check cheyandi."))
    return checks
