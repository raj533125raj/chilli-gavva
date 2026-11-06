"""Prepare a local, price-free hand-upload pack. No accounts or network access."""
import argparse
import html
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

import truth_check


_NUMBER = r"[+-]?(?:\d{1,3}(?:[,\s]\d{2,3})+|\d+)(?:[.,]\d+)?"
_CURRENCY = (r"(?:USD|INR|EUR|GBP|AUD|CAD|NZD|SGD|HKD|JPY|CNY|RMB|AED|CHF|"
             r"ZAR|PKR|BDT|NGN|PHP|KRW|RUB|BRL|MXN|Rs\.?|rupees?|dollars?|"
             r"euros?|pounds?|yen|cents?|paise|paisa)")
_MONEY = re.compile(r"(?:\b" + _CURRENCY + r"\s*" + _NUMBER
                    + r"|(?<!\w)" + _NUMBER + r"\s*" + _CURRENCY + r"\b)", re.I)
_STATE = {".enc", ".db", ".sqlite", ".sqlite3", ".json"}
_TEXT = {".md", ".txt", ".csv", ".tsv", ".html", ".xml", ".yaml", ".yml", ".rst", ".svg"}
_READMES = {"README", "README.md", "README.txt"}


def _is_state(path):
    name = path.name.casefold()
    return (any(suffix in _STATE for suffix in Path(name).suffixes)
            or any(name.endswith(suffix + tail) for suffix in _STATE
                   for tail in ("-journal", "-wal", "-shm")))


def _plain(text):
    """Keep the words and punctuation, discard Markdown presentation."""
    text = html.unescape(text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]*>", "", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:#{1,6}\s+|>\s*|[-+*•]\s+|\d+[.)]\s+)", "", line)
        line = re.sub(r"[*`~]", "", line)
        line = re.sub(r"(?<!\w)_+|_+(?!\w)", "", line)
        if re.fullmatch(r"\s*[-=_]{3,}\s*", line):
            continue
        lines.append(line.strip())
    return "\n".join(lines).strip()


def _check_money(text, where):
    text = unicodedata.normalize("NFKC", html.unescape(text))
    text = re.sub(r"[*`_~]", "", text)
    symbols = "".join(re.escape(c) for c in set(text) if unicodedata.category(c) == "Sc")
    symbol_money = bool(symbols and re.search(
        r"(?:[" + symbols + r"]\s*" + _NUMBER + r"|" + _NUMBER + r"\s*[" + symbols + r"])", text))
    if symbol_money or _MONEY.search(text):
        # Do not echo the rejected text: it could be private buyer information.
        raise ValueError(f"Money amount found in {where}; the owner must set the price.")


def _clean_tags(raw_tags, title):
    valid_tags = []
    seen = set()
    for tag in raw_tags:
        cleaned = tag.strip()
        if not cleaned:
            continue
        if len(cleaned) > 20:
            continue
        if not re.fullmatch(r"[A-Za-z0-9 ]+", cleaned):
            continue
        norm = cleaned.casefold()
        if norm not in seen:
            seen.add(norm)
            valid_tags.append(cleaned)

    if len(valid_tags) < 13 and title:
        # Build relevant candidate tags from words in title
        words = re.findall(r"[A-Za-z0-9]+", title)
        candidates = []
        for word in words:
            if word not in candidates:
                candidates.append(word)
        for i in range(len(words) - 1):
            pair = f"{words[i]} {words[i+1]}"
            if pair not in candidates:
                candidates.append(pair)
        for cand in candidates:
            if len(valid_tags) >= 13:
                break
            cand_clean = cand.strip()
            if not cand_clean or len(cand_clean) > 20:
                continue
            if not re.fullmatch(r"[A-Za-z0-9 ]+", cand_clean):
                continue
            norm = cand_clean.casefold()
            if norm not in seen:
                seen.add(norm)
                valid_tags.append(cand_clean)

    return valid_tags[:13]


def _clean_description(description):
    # Replace spreadsheet app claims
    app_patterns = [
        r"Google Sheets",
        r"Numbers",
        r"LibreOffice",
        r"your preferred spreadsheet program",
        r"any spreadsheet app",
    ]
    cleaned = description
    for pat in app_patterns:
        cleaned = re.sub(re.escape(pat), "an Excel (.xlsx) file", cleaned, flags=re.IGNORECASE)

    # If the description does not contain "personal use", append notice
    if "personal use" not in cleaned.lower():
        notice = "For personal use only. Do not resell or share the files."
        cleaned = f"{cleaned}\n\n{notice}" if cleaned else notice

    return cleaned


def _listing(body):
    sections, title, current = {}, "", None
    for line in body.splitlines():
        heading = re.match(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            label = _plain(heading[2])
            if len(heading[1]) == 1 and not title:
                title = label
                current = None
            else:
                current = re.sub(r"\s*\(\d+\)$", "", label).casefold().replace("’", "'")
                sections.setdefault(current, [])
        elif current:
            sections[current].append(line)
        elif not title and _plain(line).lower().startswith("title:"):
            title = _plain(line).split(":", 1)[1].strip()
    raw_description = _plain("\n".join(sections.get("description", [])))
    tags = []
    for line in sections.get("tags", []):
        tags.extend(t.strip() for t in _plain(line).split(",") if t.strip())
    get = next((sections[k] for k in ("what you get", "what's included", "what's inside", "included files")
                if sections.get(k)), [])
    items = [_plain(line) for line in get if _plain(line)]
    missing = [name for name, value in (("Title", title), ("Description", raw_description),
                                       ("Tags", tags), ("What you get", items)) if not value]
    if missing:
        raise ValueError("listing.md is missing: " + ", ".join(missing))

    cleaned_tags = _clean_tags(tags, title)
    cleaned_desc = _clean_description(raw_description)

    return (f"Title: {title}\n\nDescription:\n{cleaned_desc}\n\nTags: {', '.join(cleaned_tags)}"
            + "\n\nWhat you get:\n" + "\n".join(items) + "\n")


def _scan(path):
    """Read human text, including workbook strings and the builder's PDF pages."""
    _check_money(path.name, path.name)
    if path.suffix.lower() in _TEXT or path.name == "README":
        _check_money(path.read_text(encoding="utf-8-sig"), path.name)
    elif path.suffix.lower() == ".pdf":
        pages = truth_check._pdf_pages(str(path))
        if not pages or not any(p["items"] for p in pages):
            raise ValueError(f"Cannot inspect text in {path.name}; expected a robot-built PDF.")
        _check_money("\n".join(t for p in pages for _, _, _, _, t in p["items"]), path.name)
    elif path.suffix.lower() == ".xlsx":
        with zipfile.ZipFile(path) as workbook:
            for member in workbook.namelist():
                if member.endswith(".xml"):
                    tree = ET.fromstring(workbook.read(member))
                    # Formula references ($A$1) and number-format codes are not money amounts.
                    texts = [e.text or "" for e in tree.iter()
                             if e.tag.rsplit("}", 1)[-1] == "t"]
                    texts += [c.findtext("{*}v", "") for c in tree.iter()
                              if c.tag.rsplit("}", 1)[-1] == "c" and c.get("t") == "str"]
                    _check_money("\n".join(texts), f"{path.name}/{member}")
    elif path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return
        _check_money(text, path.name)


def _checklist(has_cover):
    cover = "Use cover.png for the cover." if has_cover else "Choose a cover image; this pack has no cover."
    return ("nothing was uploaded\n\n"
            "Price: set by owner\n\n"
            "Gumroad\n"
            "Create a digital product by hand in your own account.\n"
            "Fill the title, description and tags from listing.txt.\n"
            "Set the price yourself.\nUpload product.zip as the buyer download.\n"
            + cover + "\nReview the files and listing before deciding to publish.\n\n"
            "Topmate\n"
            "Create a digital product by hand in your own account.\n"
            "Fill the title, description and any available tags from listing.txt.\n"
            "Set the price yourself.\nUpload product.zip as the buyer download.\n"
            + cover + "\nReview the files and listing before deciding to publish.\n")


def make(product_folder, out_dir):
    """Write out_dir/<folder-name>/ and return its Path; refuse unsafe/unready input.

    Existing packs are never overwritten. Validation finishes before output is created.
    The buyer archive allowlist is PDF, XLSX, LICENSE.txt and README[.md/.txt].
    """
    source = Path(product_folder).expanduser()
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Product folder must be a real directory, not a symlink.")
    source = source.resolve()
    destination = Path(out_dir).expanduser().resolve() / source.name
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Output pack must not overlap the product folder.")
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Output pack already exists: {destination}")
    paths = sorted(source.iterdir())
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"Symlinks are not allowed in a product folder: {path.name}")
        if path.is_dir():
            raise ValueError(f"Expected a flat robot-built product folder; found directory: {path.name}")
    # The real checker is always called with its image checks enabled.
    result = truth_check.check(str(source))
    if result.get("verdict") != "ready":
        raise ValueError("Product is not ready (NEEDS FIX): "
                         + "; ".join(result.get("problems") or ["truth_check refused readiness"]))
    if not (source / "listing.md").is_file() or not (source / "LICENSE.txt").is_file():
        raise ValueError("Product folder requires listing.md and LICENSE.txt.")
    if not any((source / name).is_file() for name in _READMES):
        raise ValueError("Product folder requires a README.")
    # The robot's products are flat folders. Do not traverse runtime/state directories.
    for path in paths:
        if path.is_file() and not _is_state(path):
            _scan(path)
    for text in truth_check.image_texts(str(source)):
        _check_money(text, "listing images")
    listing = _listing((source / "listing.md").read_text(encoding="utf-8-sig"))
    buyers = [p for p in paths if p.is_file() and not _is_state(p)
              and (p.suffix.lower() in {".pdf", ".xlsx"}
                   or p.name in _READMES or p.name == "LICENSE.txt")]
    if not any(p.suffix.lower() in {".pdf", ".xlsx"} for p in buyers):
        raise ValueError("No buyer PDF or XLSX files found.")
    images = [p for p in paths if p.is_file() and not _is_state(p)
              and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
    cover = next((p for p in images if p.name == "cover.png"), images[0] if images else None)
    checklist = _checklist(cover is not None)
    _check_money(listing + checklist, "generated listing/checklist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".upload-pack-", dir=destination.parent) as temp:
        stage = Path(temp) / source.name
        stage.mkdir()
        with zipfile.ZipFile(stage / "product.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for path in buyers:
                archive.write(path, arcname=path.name)
        (stage / "listing.txt").write_text(listing, encoding="utf-8")
        (stage / "checklist.txt").write_text(checklist, encoding="utf-8")
        if cover:
            if cover.suffix.lower() == ".png":
                shutil.copyfile(cover, stage / "cover.png")
            else:
                from PIL import Image
                with Image.open(cover) as image:
                    image.convert("RGB").save(stage / "cover.png", "PNG")
        # Rename the complete directory only after every file has been written.
        os.rename(stage, destination)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product_folder")
    parser.add_argument("out_dir")
    args = parser.parse_args(argv)
    try:
        destination = make(args.product_folder, args.out_dir)
    except (ValueError, OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        parser.exit(1, f"Upload pack refused: {exc}\n")
    print(f"nothing was uploaded\nLocal pack: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
