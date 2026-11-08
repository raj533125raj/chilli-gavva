#!/usr/bin/env python3
"""Buyer FAQ builder: a 6-8 question Markdown FAQ built only from facts in the pack.

Facts come from the pack folder itself: file formats found there (and, for
product.zip, the names of entries inside it — a read-only peek, the pack is
never modified), page sizes named in those file names (A4 / US Letter), and
the first meaningful line of LICENSE.txt when present. Fixed answers cover
printing at 100% scale, which apps open which files, the digital-only
delivery, and "Questions? Contact the seller." No promises are added, no
listing titles are read, and no money amounts pass through (any dollar figure
in a licence line is replaced). CLI `--all` writes one
exported_content/products/faq/<pack>.md file per pack, outside the packs.
"""
import argparse
import os
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_PACKS_DIR = REPO_ROOT / "exported_content" / "products" / "upload"
DEFAULT_FAQ_DIR = REPO_ROOT / "exported_content" / "products" / "faq"

FORMAT_LABELS = ((".pdf", "PDF"), (".xlsx", "XLSX"), (".docx", "DOCX"), (".png", "PNG"))
SIZE_TOKENS = (("a4", "A4"), ("us-letter", "US Letter"), ("letter", "US Letter"), ("legal", "US Legal"))
MONEY_RE = re.compile(r"\$\s?\d[\d.,]*")


def _all_names(pack_dir):
    names = [path.name.lower() for path in pack_dir.iterdir() if path.is_file()]
    zip_path = pack_dir / "product.zip"
    if zip_path.is_file():
        try:
            with zipfile.ZipFile(zip_path) as archive:
                names.extend(os.path.basename(entry).lower() for entry in archive.namelist())
        except Exception:
            pass
    return names


def collect_facts(pack_dir):
    """Facts about the pack: formats, page sizes, licence line (or None)."""
    pack_dir = Path(pack_dir)
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"pack folder not found: {pack_dir}")
    names = _all_names(pack_dir)
    formats = [label for suffix, label in FORMAT_LABELS if any(n.endswith(suffix) for n in names)]
    sizes = []
    for token, label in SIZE_TOKENS:
        if any(token in name for name in names) and label not in sizes:
            sizes.append(label)
    licence = None
    licence_path = pack_dir / "LICENSE.txt"
    if licence_path.is_file():
        text = licence_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = MONEY_RE.sub("[price removed]", line.strip())
            line = re.sub(r"^(licen[cs]e)\s*[:\-]\s*", "", line, flags=re.I).strip()
            if line:
                licence = line
                break
    return {"formats": formats, "sizes": sizes, "licence": licence, "name": pack_dir.name}


def build_faq(pack_folder):
    """Markdown FAQ for one pack folder: exactly 6-8 buyer Q&A, facts only."""
    facts = collect_facts(pack_folder)
    qa = []

    if facts["formats"]:
        listed = ", ".join(facts["formats"])
        qa.append(("Which files do I get?", f"This download includes {listed} files."))
    else:
        qa.append(("Which files do I get?",
                   "This download includes the product files shown in the listing."))

    if facts["sizes"]:
        sizes = " and ".join(facts["sizes"])
        qa.append(("What page sizes are included?", f"Files use {sizes} page sizes."))

    if facts["licence"]:
        qa.append(("What can I do with these files?",
                   f"The licence line included with your download states: {facts['licence']}"))

    qa.append(("How should I print the planner pages?",
               "Print at 100% scale (Actual Size) and turn off Fit to page, so the "
               "page size stays exactly as designed."))

    openers = []
    if "PDF" in facts["formats"]:
        openers.append("PDF files open in any standard PDF reader")
    if "XLSX" in facts["formats"]:
        openers.append("the .xlsx spreadsheet opens in Microsoft Excel, Google Sheets, "
                       "or LibreOffice Calc")
    if "DOCX" in facts["formats"]:
        openers.append(".docx files open in Word, Google Docs, or LibreOffice Writer")
    if "PNG" in facts["formats"]:
        openers.append("PNG files open in any image viewer")
    openers = openers[:2]
    joined = "; ".join(openers) if openers else \
        "Everyday apps already on your computer or phone open these files."
    joined = joined[0].upper() + joined[1:]
    qa.append(("Which apps open these files?", joined))

    qa.append(("Will anything be shipped to me?",
               "No. These products are digital downloads, so nothing is mailed to "
               "a postal address."))

    qa.append(("Where do my downloads come from?",
               "From the shop where you bought them: open your account area there "
               "and use the download page for your order."))

    qa.append(("Questions? Contact the seller.",
               "Happy to help with the files in this download."))

    body = [f"# {facts['name']} — buyer FAQ", ""]
    for question, answer in qa:
        body.extend((f"## {question}", "", answer, ""))
    return "\n".join(body).rstrip() + "\n"


def write_all(packs_dir=None, faq_dir=None):
    """Write one faq/<pack>.md per pack; returns the written paths."""
    packs = Path(packs_dir) if packs_dir else DEFAULT_PACKS_DIR
    out = Path(faq_dir) if faq_dir else DEFAULT_FAQ_DIR
    written = []
    if packs.is_dir():
        for pack in sorted(p for p in packs.iterdir() if p.is_dir()):
            target = out / f"{pack.name}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(build_faq(pack), encoding="utf-8")
            written.append(target)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("packs_dir", nargs="?", default=str(DEFAULT_PACKS_DIR),
                        help="packs root (default exported_content/products/upload)")
    parser.add_argument("--all", action="store_true",
                        help="write exported_content/products/faq/<pack>.md (outside the packs)")
    parser.add_argument("--faq-dir", default=str(DEFAULT_FAQ_DIR),
                        help="where --all writes (default exported_content/products/faq)")
    args = parser.parse_args(argv)
    written = write_all(args.packs_dir, args.faq_dir)
    for path in written:
        print(path)
    print(f"FAQ: {len(written)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
